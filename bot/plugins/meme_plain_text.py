"""兜底消息插件 — 处理普通文本和未知斜杠命令。

授权用户发送普通文本时，等同执行 /search。
授权用户发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
非授权用户静默忽略。
"""

import logging

from nonebot import on_message
from nonebot.exception import FinishedException, RejectedException
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.plugins._search_utils import execute_search, handle_selection
from bot.plugins._help_text import HELP_TEXT
from bot.session import (
    activate_chat,
    deactivate_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 兜底：纯文本 → /search；未知斜杠命令 → 回复帮助摘要
# priority=99 在所有具体命令（priority=5）之后运行；
# block=False 不阻止其他 matcher 处理消息。
# ---------------------------------------------------------------------------

catch_all = on_message(rule=to_me(), priority=99, block=False)


@catch_all.handle()
async def handle_plain_text(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
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
        await matcher.finish(f"未知命令\n\n{HELP_TEXT}")
        return

    # 普通文本当作 /search
    logger.info("用户 %s 的普通文本当作 /search: %r", user_id, text)
    # 会话检查：拒绝而非覆盖
    if not activate_chat(user_id, "search", matcher):
        await matcher.finish("已有命令在处理中，请先 /cancel")
        return

    await execute_search(bot, event, matcher, text)


@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    got 入口重新激活 chat session（不同 asyncio task），
    然后拦截 /help 和 /cancel，有效选择时发送图片。
    """
    user_id = event.get_user_id()

    # got 入口重新激活 chat session
    activate_chat(user_id, "search", matcher)

    try:
        # /help 和 /cancel 旁路拦截
        text = event.get_plaintext().strip()
        if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
            return

        # 检查选择会话是否仍有效
        ss = get_selection(user_id)
        if ss is None:
            deactivate_chat(user_id)
            await matcher.finish("选择已过期，请重新搜索")
            return

        candidates = matcher.state.get("candidates", [])
        selection_text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, selection_text)
        if isinstance(result, str):
            await matcher.reject(result)
            return

        # 有效选择：清除选择会话
        remove_selection(user_id)
        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        deactivate_chat(user_id)

    except (FinishedException, RejectedException):
        deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的兜底搜索处理异常", user_id)
        deactivate_chat(user_id)
        raise
