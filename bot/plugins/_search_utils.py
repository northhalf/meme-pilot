"""搜索核心逻辑模块。

提供 execute_search 和 handle_selection 供 meme_search 和 meme_help 复用。
以下划线开头避免 NoneBot2 自动加载为插件。
"""

import asyncio
import logging

from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher

from bot.app_state import get_index_manager, get_keyword_searcher
from bot.config import MEMES_DIR
from bot.engine.keyword_searcher import SearchResult
from bot.session import cancel, register, timeout_session

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
    event: PrivateMessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
    """核心搜索逻辑。

    流程：锁检查 → 索引空检查 → 执行搜索 → 结果分支。
    多结果时注册 session 并启动超时任务。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
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
        await cmd_matcher.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
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

    # 存储候选并注册会话
    cmd_matcher.state["candidates"] = results
    register(user_id, cmd_matcher, "search")

    await cmd_matcher.send("\n".join(lines))

    # 启动超时任务
    asyncio.create_task(timeout_session(bot, event, user_id, "选择已过期，请重新搜索"))
