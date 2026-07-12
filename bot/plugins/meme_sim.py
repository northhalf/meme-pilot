"""/sim 命令插件 — 语义相似度 Top-10 选择。

授权用户发送 /sim <描述文本>，Bot 基于 embedding 语义搜索召回 Top 10 候选供选择。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._search_utils import (
    NEXT_PAGE_TRIGGER,
    PresentOptions,
    dispatch_search_results,
    handle_got_selection,
)
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)

SIM_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="ratio", next_trigger=NEXT_PAGE_TRIGGER
)

sim_cmd = on_command("sim", rule=to_me(), priority=5, block=True)


@sim_cmd.handle()
async def handle_sim(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/sim 命令入口。

    流程：授权校验 → 会话检查 → 提取描述文本 → 调用 IndexManager.semantic_search
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
        logger.info("用户 %s 调用 /sim", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "sim")
                await matcher.finish(None)
                return

            # 会话互斥：拒绝而非覆盖
            if not session_manager.activate_chat(scope, "sim", matcher):
                await matcher.finish("已有命令在处理中，请先 /cancel")
                return

            # 提取描述文本
            raw_text = event.get_plaintext().strip()
            description = raw_text.removeprefix("/sim").removeprefix("sim").strip()
            if not description:
                session_manager.deactivate_chat(scope)
                logger.info("用户 %s 的 /sim 缺少描述文本", user_id)
                await matcher.finish("/sim <描述文本>")
                return

            logger.debug("/sim 描述: %r", description)
            logger.info("用户 %s /sim 描述: %r", user_id, description)

            # 获取 IndexManager
            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                session_manager.deactivate_chat(scope)
                await matcher.finish("服务未就绪，请稍后再试")
                return

            # 执行语义搜索
            try:
                results = await index_manager.semantic_search(description, limit=None)
            except asyncio.TimeoutError:
                logger.info("用户 %s 的 /sim 等待读锁超时", user_id)
                session_manager.deactivate_chat(scope)
                await matcher.finish("索引更新较慢，请稍后再试")
                return
            except ValueError:
                logger.warning(
                    "用户 %s 的 /sim embedding 异常: description=%r",
                    user_id,
                    description,
                )
                session_manager.deactivate_chat(scope)
                await matcher.finish("AI 服务暂时不可用，稍后重试")
                return
            except Exception:
                logger.exception("语义搜索异常: description=%r", description)
                session_manager.deactivate_chat(scope)
                await matcher.finish("AI 服务暂时不可用，稍后重试")
                return

            # 空结果分支
            if not results:
                session_manager.deactivate_chat(scope)
                await matcher.finish("没有找到匹配的表情包 🙁")
                return

            logger.info("/sim 召回结果数: %d", len(results))
            await dispatch_search_results(
                bot, event, matcher, results, options=SIM_OPTIONS
            )
        except asyncio.CancelledError:
            session_manager.deactivate_chat(scope)
            raise FinishedException


@sim_cmd.got("selection")
async def got_sim_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """处理 /sim 的选择。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
    """
    request_id = generate_request_id()
    with set_request_id(request_id):
        await handle_got_selection(
            bot, event, matcher, selection_msg, "/sim", options=SIM_OPTIONS
        )
