"""/rand 命令插件 — 随机表情包选择。

授权用户发送 /rand [关键词]，Bot 随机给出 10 个候选；
有关键词时先在关键词搜索结果中随机，无关键词时全库随机。
回复 0 可换一批。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.index_manager import CollectionSelectionExpiredError
from bot.engine.types import CollectionSelection
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import (
    dispatch_search_results,
    format_metadata_line,
    got_intercept_bypass,
    present_candidates,
    resolve_selection,
)
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)

rand_cmd = on_command("rand", rule=to_me(), priority=5, block=True)


@rand_cmd.handle()
async def handle_rand(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/rand 命令入口。

    流程：授权校验 → 会话检查 → 提取关键词 → 调用 IndexManager.random_search
    → 通过 dispatch_search_results 统一处理结果。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /rand", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "rand")
                await matcher.finish(None)
                return

            # 会话互斥：拒绝而非覆盖
            if not session_manager.activate_chat(scope, "rand", matcher):
                await reply_utils.finish(
                    event, matcher, "已有命令在处理中，请先 /cancel"
                )
                return

            # 提取关键词
            raw_text = event.get_plaintext().strip()
            keyword = raw_text.removeprefix("/rand").removeprefix("rand").strip()
            keyword = keyword or None

            logger.debug("/rand 关键词: %r", keyword)

            # 获取 IndexManager
            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "服务未就绪，请稍后再试")
                return

            # 读取当前合集快照并执行随机搜索
            try:
                selection, results = await index_manager.random_search_for_scope(
                    scope, keyword
                )
            except asyncio.TimeoutError:
                logger.info("用户 %s 的 /rand 等待读锁超时", user_id)
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "索引更新较慢，请稍后再试")
                return
            except Exception:
                logger.exception("随机搜索异常: keyword=%r", keyword)
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "搜索服务暂时不可用，稍后重试")
                return

            # 空结果分支
            if not results:
                session_manager.deactivate_chat(scope)
                if keyword:
                    await reply_utils.finish(event, matcher, "没有匹配到任何表情包 🙁")
                else:
                    await reply_utils.finish(
                        event, matcher, "表情包目录为空，请先添加图片并执行 /refresh"
                    )
                return

            # 保存关键词和完整合集选择快照，供换一批校验并复用
            matcher.state["keyword"] = keyword
            matcher.state["collection_selection"] = selection
            logger.info("/rand 发送结果数: %d", len(results))
            await dispatch_search_results(
                bot, event, matcher, results, prompt_suffix="回复 0 换一批"
            )
        except asyncio.CancelledError:
            session_manager.deactivate_chat(scope)
            raise FinishedException


@rand_cmd.got("selection")
async def got_rand_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """处理 /rand 的选择：支持回复 0 换一批。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        with session_manager.handler_context(scope, matcher):
            try:
                # /help 和 /cancel 旁路拦截
                text = event.get_plaintext().strip()
                if await got_intercept_bypass(event, matcher, text, HELP_TEXT):
                    return

                # 检查选择会话是否仍有效
                ss = session_manager.get_selection(scope)
                if ss is None:
                    session_manager.deactivate_chat(scope)
                    await reply_utils.finish(event, matcher, "选择已过期，请重新搜索")
                    return

                selection_text = selection_msg.extract_plain_text().strip()

                # 回复 0：换一批
                if selection_text == "0":
                    keyword = matcher.state.get("keyword")
                    expected_selection = matcher.state.get("collection_selection")
                    if not isinstance(expected_selection, CollectionSelection):
                        session_manager.remove_selection(scope)
                        session_manager.deactivate_chat(scope)
                        await reply_utils.finish(
                            event, matcher, "随机搜索状态异常，请重新发送 /rand"
                        )
                        return
                    try:
                        index_manager = get_index_manager()
                        new_results = (
                            await index_manager.random_search_for_scope_snapshot(
                                scope, keyword, expected_selection
                            )
                        )
                    except CollectionSelectionExpiredError:
                        session_manager.remove_selection(scope)
                        session_manager.deactivate_chat(scope)
                        await reply_utils.finish(
                            event, matcher, "当前合集已变化，请重新发送 /rand"
                        )
                        return
                    except asyncio.TimeoutError:
                        await reply_utils.reject(
                            event, matcher, "索引更新较慢，请稍后再试"
                        )
                        return
                    except Exception:
                        logger.exception("用户 %s 的 /rand 换一批异常", user_id)
                        await reply_utils.reject(
                            event, matcher, "搜索服务暂时不可用，稍后重试"
                        )
                        return

                    if not new_results:
                        session_manager.remove_selection(scope)
                        session_manager.deactivate_chat(scope)
                        await reply_utils.finish(
                            event, matcher, "出现错误，无法搜索到任何结果"
                        )
                        return

                    session_manager.remove_selection(scope)
                    await present_candidates(
                        bot,
                        event,
                        matcher,
                        new_results,
                        prompt_suffix="回复 0 换一批",
                        use_reject=True,
                    )
                    return

                # 非 0：解析编号并发送图片
                candidates = matcher.state.get("candidates", [])
                result = resolve_selection(candidates, selection_text)
                if isinstance(result, str):
                    await reply_utils.reject(event, matcher, result + "\n回复 0 换一批")
                    return

                session_manager.remove_selection(scope)
                image_path = MEMES_DIR / result.image_path
                await matcher.send(
                    MessageSegment.image("file://" + str(image_path.resolve()))
                )
                await reply_utils.finish(
                    event,
                    matcher,
                    format_metadata_line(
                        result.public_id,
                        result.collection_name,
                        result.speaker,
                        result.tags,
                    ),
                )
            except RejectedException:
                raise
            except asyncio.CancelledError:
                session_manager.deactivate_chat(scope)
                raise FinishedException
            except FinishedException:
                session_manager.deactivate_chat(scope)
                raise
            except Exception:
                logger.exception("用户 %s 的 /rand 处理异常", user_id)
                session_manager.deactivate_chat(scope)
                raise
