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

search_cmd = on_command("search", rule=to_me(), priority=5, block=True)


@search_cmd.handle()
async def handle_search(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/search 命令入口。

    流程：授权校验 → 会话检查 → 提取关键词 → 调用 execute_search。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /search", user_id)

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "search")
        return

    # 拒绝而非覆盖
    if not activate_chat(user_id, "search", matcher):
        await matcher.finish("已有命令在处理中，请先 /cancel")
        return

    # 提取关键词
    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/search").removeprefix("search").strip()
    if not keyword:
        deactivate_chat(user_id)
        logger.info("用户 %s 的 /search 缺少关键词", user_id)
        await matcher.finish("/search <关键词>")
        return

    logger.info("用户 %s 搜索关键词: %r", user_id, keyword)
    await execute_search(bot, event, matcher, keyword)


@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    got 入口重新激活 chat session（不同 asyncio task），
    然后拦截 /help 和 /cancel，有效选择时发送图片。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
    """
    user_id = event.get_user_id()

    # got 入口重新激活 chat session（不同 asyncio task）
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
        logger.exception("用户 %s 的 /search 处理异常", user_id)
        deactivate_chat(user_id)
        raise
