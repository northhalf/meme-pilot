"""/search 命令插件 — 关键词搜索表情包。

授权用户在私聊中发送 /search <关键词>，Bot 通过 KeywordSearcher
对索引 OCR 文本做模糊匹配，返回搜索结果。
"""

import logging

from nonebot.exception import FinishedException, RejectedException
from nonebot import on_command
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
from bot.plugins._search_utils import execute_search, handle_selection
from bot.session import cancel, check_and_cancel, is_cancelled

logger = logging.getLogger(__name__)

search_cmd = on_command("search", rule=to_me(), priority=5, block=True)


@search_cmd.handle()
async def handle_search(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None:
    """/search 命令入口。

    流程：授权校验 → 会话覆盖 → 提取关键词 → 调用 execute_search。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /search", user_id)

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "search")
        return

    # 会话覆盖检查
    hint = check_and_cancel(user_id, "search")
    if hint:
        await matcher.send(hint)

    # 提取关键词
    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/search").removeprefix("search").strip()
    if not keyword:
        logger.info("用户 %s 的 /search 缺少关键词", user_id)
        await search_cmd.finish("/search <关键词>")
        return

    logger.info("用户 %s 搜索关键词: %r", user_id, keyword)
    await execute_search(bot, event, matcher, keyword)


@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
    """
    user_id = event.get_user_id()

    try:
        if is_cancelled(user_id):
            return

        candidates = matcher.state.get("candidates", [])
        text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, text)
        if isinstance(result, str):
            await matcher.reject(result)
            return

        cancel(user_id)
        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )

    except (FinishedException, RejectedException):
        raise
    except Exception:
        logger.exception("用户 %s 的 /search 处理异常", user_id)
        cancel(user_id)
        raise
