"""兜底消息插件 — 处理普通文本和未知斜杠命令。

授权用户发送普通文本时，等同执行 /search。
授权用户发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
非授权用户静默忽略。
"""

import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import execute_search, handle_selection
from bot.session import cancel, check_and_cancel, is_cancelled

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 兜底：纯文本 → /search；未知斜杠命令 → 回复帮助摘要
# priority=99 在所有具体命令（priority=5）之后运行；
# block=False 不阻止其他 matcher 处理消息。
# ---------------------------------------------------------------------------

catch_all = on_message(rule=to_me(), priority=99, block=False)


@catch_all.handle()
async def handle_plain_text(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
) -> None:
    """兜底处理授权用户的普通文本和未知斜杠命令。

    授权用户私聊发送不以 / 开头的普通文本时，等同执行 /search。
    授权用户私聊发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
    非授权用户静默忽略。
    """
    user_id = event.get_user_id()
    text = event.get_plaintext().strip()
    logger.info("兜底处理用户 %s 消息: %r", user_id, text)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "plain_text")
        return

    if text.startswith("/"):
        logger.info("用户 %s 发送未知命令: %r", user_id, text)
        await catch_all.finish(f"未知命令\n\n{HELP_TEXT}")
        return

    # 普通文本当作 /search
    logger.info("用户 %s 的普通文本当作 /search: %r", user_id, text)
    hint = check_and_cancel(user_id, "search")
    if hint:
        await matcher.send(hint)
    await execute_search(bot, event, matcher, text)


@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    仅处理由本 matcher（catch_all）触发的搜索会话。
    """
    user_id = event.get_user_id()

    if is_cancelled(user_id):
        return

    candidates = matcher.state.get("candidates", [])

    text = selection_msg.extract_plain_text().strip()
    result = handle_selection(matcher, candidates, text)

    if isinstance(result, str):
        await catch_all.reject(result)
        return

    cancel(user_id)
    image_path = MEMES_DIR / result.filename
    await catch_all.finish(MessageSegment.image("file://" + str(image_path.resolve())))
