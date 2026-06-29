"""/search 命令插件 — 关键词搜索表情包。

授权用户在私聊中发送 /search <关键词>，Bot 通过 KeywordSearcher
对索引 OCR 文本做模糊匹配，返回搜索结果。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.plugins._search_utils import execute_search, handle_got_selection
from bot.session import (
    activate_chat,
    deactivate_chat,
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
    await handle_got_selection(bot, event, matcher, selection_msg, "/search")
