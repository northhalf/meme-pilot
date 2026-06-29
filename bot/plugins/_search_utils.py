"""搜索核心逻辑模块。

提供 execute_search 和 handle_selection 供 meme_search 和 meme_help 复用。
以下划线开头避免 NoneBot2 自动加载为插件。
"""

import asyncio
import logging
import uuid

from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
    MessageEvent,
)
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher

from bot.app_state import get_index_manager, get_keyword_searcher
from bot.config import MEMES_DIR
from bot.engine.keyword_searcher import SearchResult

from bot.plugins._help_text import HELP_TEXT

from bot.session import (
    activate_chat,
    create_selection,
    deactivate_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
    timeout_session,
)

logger = logging.getLogger(__name__)


def handle_selection(
    matcher: Matcher,
    candidates: list[SearchResult],
    text: str,
) -> SearchResult | str:
    """处理用户选择编号。

    Args:
        matcher: NoneBot2 Matcher 实例。
        candidates: 搜索结果候选列表。
        text: 用户输入的编号文本。

    Returns:
        SearchResult: 选择成功时返回对应结果。
        str: 错误消息（无效编号、candidates 为空等）。
    """
    if not candidates:
        return "搜索状态异常，请重新搜索"

    try:
        choice = int(text)
    except ValueError:
        return f"无效编号，请回复 1-{len(candidates)} 之间的数字"

    if choice < 1 or choice > len(candidates):
        return f"无效编号，请回复 1-{len(candidates)} 之间的数字"

    return candidates[choice - 1]


async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
    """核心搜索逻辑。

    流程：锁检查 → 索引空检查 → 执行搜索 → 结果分支。
    多结果时创建选择会话（selection_id + create_selection）并启动超时任务。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        keyword: 搜索关键词。
    """
    user_id = event.get_user_id()

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    # 锁检查
    if index_manager.is_locked:
        logger.info("用户 %s 的搜索被拒绝：索引正在更新", user_id)
        await cmd_matcher.finish("索引正在更新，请稍后再试")
        return

    # 索引空检查
    if index_manager.entry_count == 0:
        await cmd_matcher.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 获取 KeywordSearcher
    try:
        keyword_searcher = get_keyword_searcher()
    except RuntimeError:
        logger.error("KeywordSearcher 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    # 执行搜索
    try:
        results = keyword_searcher.search(keyword)
    except Exception:
        logger.exception("关键词搜索异常: keyword=%r", keyword)
        await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
        return

    if not results:
        deactivate_chat(user_id)
        await cmd_matcher.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        deactivate_chat(user_id)
        image_path = MEMES_DIR / results[0].filename
        await cmd_matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        return

    # 多个结果：格式化选择列表
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.text}")
    lines.append(f"回复编号即可 (1-{len(results)})")

    # 存储候选、创建选择会话
    cmd_matcher.state["candidates"] = results
    selection_id = str(uuid.uuid4())
    cmd_matcher.state["selection_id"] = selection_id

    await cmd_matcher.send("\n".join(lines))

    # 启动超时任务（使用 selection_id 双重校验）
    task = asyncio.create_task(
        timeout_session(bot, event, user_id, selection_id, "选择已过期，请重新搜索")
    )
    create_selection(user_id, selection_id, task)


async def handle_got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message,
    error_label: str = "搜索",
) -> None:
    """处理 got 选择编号的共享逻辑。

    供 meme_search.py 和 meme_plain_text.py 的 got("selection") 包装器调用，
    消除两个插件间的重复代码。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
        error_label: 异常日志中的操作标签，用于区分调用方。
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
        logger.exception("用户 %s 的 %s 处理异常", user_id, error_label)
        deactivate_chat(user_id)
        raise
