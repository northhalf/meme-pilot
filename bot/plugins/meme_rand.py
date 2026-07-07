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

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.plugins._search_utils import (
    dispatch_search_results,
    format_metadata_line,
    got_intercept_bypass,
    present_candidates,
    resolve_selection,
)
from bot.plugins._help_text import HELP_TEXT
from bot.session import session_manager

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
    logger.info("用户 %s 调用 /rand", user_id)

    try:
        # 授权校验
        if not is_authorized(user_id):
            log_unauthorized(user_id, "rand")
            await matcher.finish(None)
            return

        # 会话互斥：拒绝而非覆盖
        if not session_manager.activate_chat(user_id, "rand", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        # 提取关键词
        raw_text = event.get_plaintext().strip()
        keyword = raw_text.removeprefix("/rand").removeprefix("rand").strip()
        keyword = keyword or None

        # 获取 IndexManager
        try:
            index_manager = get_index_manager()
        except RuntimeError:
            logger.error("IndexManager 尚未初始化")
            session_manager.deactivate_chat(user_id)
            await matcher.finish("服务未就绪，请稍后再试")
            return

        # 执行随机搜索
        try:
            results = await index_manager.random_search(keyword)
        except asyncio.TimeoutError:
            logger.info("用户 %s 的 /rand 等待读锁超时", user_id)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("索引更新较慢，请稍后再试")
            return
        except Exception:
            logger.exception("随机搜索异常: keyword=%r", keyword)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("搜索服务暂时不可用，稍后重试")
            return

        # 空结果分支
        if not results:
            session_manager.deactivate_chat(user_id)
            if keyword:
                await matcher.finish("没有匹配到任何表情包 🙁")
            else:
                await matcher.finish("表情包目录为空，请先添加图片并执行 /refresh")
            return

        # 保存关键词，供换一批复用
        matcher.state["keyword"] = keyword
        await dispatch_search_results(
            bot, event, matcher, results, prompt_suffix="回复 0 换一批"
        )
    except asyncio.CancelledError:
        session_manager.deactivate_chat(user_id)
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

    with session_manager.handler_context(user_id, matcher):
        try:
            # /help 和 /cancel 旁路拦截
            text = event.get_plaintext().strip()
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            # 检查选择会话是否仍有效
            ss = session_manager.get_selection(user_id)
            if ss is None:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("选择已过期，请重新搜索")
                return

            selection_text = selection_msg.extract_plain_text().strip()

            # 回复 0：换一批
            if selection_text == "0":
                keyword = matcher.state.get("keyword")
                try:
                    index_manager = get_index_manager()
                    new_results = await index_manager.random_search(keyword)
                except asyncio.TimeoutError:
                    await matcher.reject("索引更新较慢，请稍后再试")
                    return
                except Exception:
                    logger.exception("用户 %s 的 /rand 换一批异常", user_id)
                    await matcher.reject("搜索服务暂时不可用，稍后重试")
                    return

                if not new_results:
                    session_manager.remove_selection(user_id)
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish("出现错误，无法搜索到任何结果")
                    return

                session_manager.remove_selection(user_id)
                await present_candidates(
                    bot, event, matcher, new_results, prompt_suffix="回复 0 换一批"
                )
                return

            # 非 0：解析编号并发送图片
            candidates = matcher.state.get("candidates", [])
            result = resolve_selection(matcher, candidates, selection_text)
            if isinstance(result, str):
                await matcher.reject(result + "\n回复 0 换一批")
                return

            session_manager.remove_selection(user_id)
            image_path = MEMES_DIR / result.image_path
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )
            await matcher.finish(
                format_metadata_line(result.entry_id, result.speaker, result.tags)
            )
        except RejectedException:
            raise
        except asyncio.CancelledError:
            session_manager.deactivate_chat(user_id)
            raise FinishedException
        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except Exception:
            logger.exception("用户 %s 的 /rand 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
