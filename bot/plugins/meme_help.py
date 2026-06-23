"""/help 命令插件 — 显示命令帮助摘要。

授权用户在私聊中发送 /help 或不以 / 开头的普通文本时，
Bot 返回当前可用命令和简单用法。
授权用户发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
"""

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized

_HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [目标命名]：通过聊天添加一张表情包
/refresh：扫描 memes/ 并增量更新索引"""

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

    if not is_authorized(user_id):
        log_unauthorized(user_id, "help")
        return

    await help_cmd.finish(_HELP_TEXT)


# ---------------------------------------------------------------------------
# 兜底：纯文本 / 未知斜杠命令 → 回复帮助摘要
# priority=99 在所有具体命令（priority=5）之后运行；
# block=False 不阻止其他 matcher 处理消息。
# ---------------------------------------------------------------------------

catch_all = on_message(rule=to_me(), priority=99, block=False)


@catch_all.handle()
async def handle_plain_text(bot: Bot, event: PrivateMessageEvent) -> None:
    """兜底处理授权用户的普通文本和未知斜杠命令。

    授权用户私聊发送不以 / 开头的普通文本时，回复帮助摘要。
    授权用户私聊发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
    非授权用户静默忽略。
    """
    user_id = event.get_user_id()

    if not is_authorized(user_id):
        log_unauthorized(user_id, "plain_text")
        return

    text = event.get_plaintext().strip()

    if text.startswith("/"):
        await catch_all.finish(f"未知命令\n\n{_HELP_TEXT}")
        return

    await catch_all.finish(_HELP_TEXT)
