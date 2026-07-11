"""兜底消息插件 — 处理普通文本和未知斜杠命令。

授权用户发送普通文本时，等同执行 /search。
授权用户发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
非授权用户静默忽略。
"""

import asyncio
import logging

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import (
    NEXT_PAGE_TRIGGER,
    PresentOptions,
    execute_search,
    handle_got_selection,
)
from bot.session import session_manager

logger = logging.getLogger(__name__)

SEARCH_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="score", next_trigger=NEXT_PAGE_TRIGGER
)

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
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("兜底处理用户 %s 消息: %r", user_id, text)

        try:
            if not is_authorized(user_id):
                log_unauthorized(user_id, "plain_text")
                await matcher.finish(None)
                return

            if text.startswith("/"):
                logger.info("用户 %s 发送未知命令: %r", user_id, text)
                await matcher.finish(f"未知命令\n\n{HELP_TEXT}")
                return

            # 普通文本当作 /search
            logger.info("用户 %s 的普通文本当作 /search: %r", user_id, text)
            # 会话检查：拒绝而非覆盖
            if not session_manager.activate_chat(user_id, "search", matcher):
                await matcher.finish("已有命令在处理中，请先 /cancel")
                return

            await execute_search(bot, event, matcher, text, options=SEARCH_OPTIONS)
        except asyncio.CancelledError:
            raise FinishedException
        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except Exception:
            logger.exception("用户 %s 的兜底搜索处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise


@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    request_id = generate_request_id()
    with set_request_id(request_id):
        await handle_got_selection(
            bot, event, matcher, selection_msg, "兜底搜索", options=SEARCH_OPTIONS
        )
