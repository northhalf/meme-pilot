"""/cancel 命令插件 — 取消当前活跃会话。

授权用户在任何状态（包括 got 等待中）发送 /cancel 时，
通过 execute_cancel 取消正在进行的命令会话。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.log_context import generate_request_id, set_request_id
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)

cancel_cmd = on_command("cancel", rule=to_me(), priority=5, block=True, aliases={"c"})


@cancel_cmd.handle()
async def handle_cancel(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/cancel 命令处理入口。

    execute_cancel 内部处理自取消（同频道）和跨 task 取消的逻辑。
    此 handler 只负责授权校验和结果转发。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /cancel", user_id)

        # 授权校验：非授权用户静默忽略（仅记录日志，不回复提示）
        if not is_authorized(user_id):
            log_unauthorized(user_id, "cancel")
            await matcher.finish(None)
            return

        succeed_cancel = await session_manager.execute_cancel(scope)
        if not succeed_cancel:
            await matcher.finish("当前没有活跃的会话")

        logger.info("用户 %s 的 /cancel 成功", user_id)
