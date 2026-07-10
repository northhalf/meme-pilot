"""/query 命令插件 - 组合检索表情包。

按关键词/说话人/标签组合检索：
- #tag 标记标签（可多个，AND）
- @speaker 标记说话人（可多个，OR）
- 其余 token 为关键词
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.plugins._search_utils import (
    NEXT_PAGE_TRIGGER,
    PresentOptions,
    execute_combined_search,
    handle_got_selection,
)
from bot.session import session_manager

logger = logging.getLogger(__name__)

QUERY_KW_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="score", next_trigger=NEXT_PAGE_TRIGGER
)
"""有关键词时：展示关键词相似度（score 0-100）+ 翻页。"""

QUERY_FILTER_OPTIONS = PresentOptions(
    show_similarity=False, next_trigger=NEXT_PAGE_TRIGGER
)
"""无关键词纯过滤时：不展示相似度 + 翻页。"""

QUERY_USAGE = "/query <关键词> [@说话人] [#标签...]"

query_cmd = on_command("query", rule=to_me(), priority=5, block=True, aliases={"q"})


def _parse_args(text: str) -> tuple[str, list[str], list[str]]:
    """解析 /query 参数：#tag / @speaker / 关键词。

    Args:
        text: 命令参数纯文本。

    Returns:
        (keyword, speakers, tags) 三元组：
        keyword 为剩余 token 空格拼接（可能为空串）；
        speakers 为 @ 前缀 token 去前缀列表（OR）；
        tags 为 # 前缀 token 去前缀列表（AND）。
        # / @ 单独成 token（前缀后为空）忽略。
    """
    speakers: list[str] = []
    tags: list[str] = []
    kw_tokens: list[str] = []
    for tok in text.split():
        if tok.startswith("#"):
            if len(tok) > 1:
                tags.append(tok[1:])
            # lone # 忽略
        elif tok.startswith("@"):
            if len(tok) > 1:
                speakers.append(tok[1:])
            # lone @ 忽略
        else:
            kw_tokens.append(tok)
    return " ".join(kw_tokens), speakers, tags


@query_cmd.handle()
async def handle_query(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """/query 命令入口。

    流程：授权校验 -> 会话检查 -> 解析参数 -> 调用 execute_combined_search。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入）。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /query", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "query")
            await matcher.finish(None)
            return

        if not session_manager.activate_chat(user_id, "query", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        text = args.extract_plain_text().strip()
        keyword, speakers, tags = _parse_args(text)

        if not keyword and not speakers and not tags:
            session_manager.deactivate_chat(user_id)
            logger.info("用户 %s 的 /query 缺少参数", user_id)
            await matcher.finish(QUERY_USAGE)
            return

        logger.info(
            "用户 %s 组合检索: keyword=%r, speakers=%r, tags=%r",
            user_id,
            keyword,
            speakers,
            tags,
        )
        options = QUERY_KW_OPTIONS if keyword else QUERY_FILTER_OPTIONS
        matcher.state["query_options"] = options
        await execute_combined_search(
            bot, event, matcher, keyword, speakers, tags, options=options
        )
    except asyncio.CancelledError:
        session_manager.deactivate_chat(user_id)
        raise FinishedException
    except FinishedException:
        session_manager.deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的 /query 处理异常", user_id)
        session_manager.deactivate_chat(user_id)
        raise


@query_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    options: PresentOptions = matcher.state.get("query_options", QUERY_FILTER_OPTIONS)
    await handle_got_selection(
        bot, event, matcher, selection_msg, "/query", options=options
    )
