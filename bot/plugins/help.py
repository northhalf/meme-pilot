"""/help 命令插件 — 显示命令帮助摘要。

授权用户在私聊或群聊 @bot 中发送 /help 时，Bot 返回当前可用命令和简单用法。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.auth import is_authorized, log_unauthorized
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._help_text import help_text_for

logger = logging.getLogger(__name__)

help_cmd = on_command("help", rule=to_me(), priority=5, block=True, aliases={"h"})


@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/help 命令处理入口。

    流程：授权校验 → 回复帮助文本。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /help", user_id)

        if not is_authorized(user_id):
            log_unauthorized(user_id, "help")
            return

        await reply_utils.finish(event, matcher, help_text_for(event.message_type))
