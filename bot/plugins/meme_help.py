"""/help 命令插件 — 显示命令帮助摘要。

授权用户在私聊中发送 /help 时，Bot 返回当前可用命令和简单用法。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.plugins._help_text import HELP_TEXT

logger = logging.getLogger(__name__)

help_cmd = on_command("help", rule=to_me(), priority=5, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: PrivateMessageEvent) -> None:
    """/help 命令处理入口。

    流程：授权校验 → 回复帮助文本。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /help", user_id)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "help")
        return

    await help_cmd.finish(HELP_TEXT)
