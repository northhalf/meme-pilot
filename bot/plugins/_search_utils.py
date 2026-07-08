"""搜索核心逻辑模块。

提供 execute_search、resolve_selection、present_candidates 和 dispatch_search_results，
供 meme_search、meme_rand、meme_sim 等插件复用。
以下划线开头避免 NoneBot2 自动加载为插件。
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
    MessageEvent,
)
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher

from bot.app_state import get_index_manager
from bot.config import MEMES_DIR
from bot.engine.types import SearchResult

from bot.plugins._help_text import HELP_TEXT

from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)


PAGE_SIZE: int = 10
"""每页展示的候选条数。"""

NEXT_PAGE_TRIGGER: str = "n"
"""用户回复该词触发"下一页"。"""


@dataclass(frozen=True)
class PresentOptions:
    """候选展示选项。

    控制列表行是否展示相似度、相似度量纲、是否支持翻页。

    Attributes:
        show_similarity: 是否在列表行末尾展示相似度百分比。
        similarity_scale: 相似度量纲；ratio=0–1，score=0–100。
        next_trigger: 下一页触发词；None 表示不支持翻页（如 /rand）。
        page_size: 每页条数，默认 PAGE_SIZE。
    """

    show_similarity: bool = False
    similarity_scale: Literal["ratio", "score"] = "score"
    next_trigger: str | None = None
    page_size: int = PAGE_SIZE


def _similarity_percent(similarity: float, scale: Literal["ratio", "score"]) -> int:
    """把相似度归一为 0–100 的整数百分比。

    Args:
        similarity: 相似度原值。
        scale: 量纲；ratio=0–1 乘 100，score=0–100 直接取整。

    Returns:
        clamp 到 [0, 100] 的整数百分比。
    """
    raw = similarity * 100 if scale == "ratio" else similarity
    return max(0, min(100, round(raw)))


def format_metadata_line(entry_id: int, speaker: str | None, tags: list[str]) -> str:
    """格式化表情包的元数据行。

    输出格式：id, speaker, tag1, tag2, ...
    speaker 缺失时显示为"无"；tags 为空时省略 tags 段。

    Args:
        entry_id: 索引 id。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。

    Returns:
        格式化后的元数据行字符串。
    """
    parts = [str(entry_id), speaker if speaker else "无"]
    parts.extend(tags)
    return ", ".join(parts)


def resolve_selection(
    matcher: Matcher,
    candidates: list[SearchResult],
    text: str,
) -> SearchResult | str:
    """解析用户选择编号。

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


async def got_intercept_bypass(
    user_id: str,
    matcher: Matcher,
    text: str,
    help_text: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。

    /cancel 分支委托给 session_manager.execute_cancel。
    /help 分支通过 reject(help_text) 发送帮助文本并继续等待。
    将 FinishedException 和 RejectedException 抛出

    Args:
        user_id: 用户 ID。
        matcher: 当前 got handler 的 matcher。
        text: 用户消息文本。
        help_text: 帮助文本常量。

    Returns:
        True 表示拦截到命令（调用方应 return），
        False 表示正常流程继续。
    """
    if text.startswith("/cancel ") or text == "/cancel":
        if not await session_manager.execute_cancel(user_id):
            await matcher.finish("当前没有活跃的会话")  # 抛 FinishedException
        return True

    if text.startswith("/help ") or text == "/help":
        await matcher.reject(help_text)  # 抛 RejectedException，以下不可达
        return True

    return False


async def present_candidates(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    candidates: list[SearchResult],
    *,
    options: PresentOptions = PresentOptions(),
    page_index: int = 0,
    total_pages: int = 1,
    prompt_suffix: str = "",
    use_reject: bool = False,
) -> None:
    """展示候选列表并创建选择会话（仅处理多结果）。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/reject）。
        candidates: 当前页候选结果切片。
        options: 展示选项（相似度与翻页）。
        page_index: 当前页索引（从 0 开始）。
        total_pages: 总页数。
        prompt_suffix: 附加在提示末尾的可选文本。
        use_reject: True 时用 matcher.reject 发送列表并重新等待下一次输入
            （用于 got handler 内的换一批/翻页；否则 handler 返回后 matcher
            结束，用户无法继续交互）；False 时用 send（用于首次展示）。
            reject 会中断当前流程，故选择会话与超时任务须在 reject 之前创建。
    """
    user_id = event.get_user_id()
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(candidates, 1):
        meta = format_metadata_line(r.entry_id, r.speaker, r.tags)
        if options.show_similarity:
            sim_pct = _similarity_percent(r.similarity, options.similarity_scale)
            lines.append(f"{i}. {r.text} -- {meta}, {sim_pct}%")
        else:
            lines.append(f"{i}. {r.text} -- {meta}")
    lines.append(f"回复编号即可 (1-{len(candidates)})")
    if options.next_trigger and page_index + 1 < total_pages:
        lines.append(f"回复 {options.next_trigger} 看下一页")
    if prompt_suffix:
        lines.append(prompt_suffix)

    cmd_matcher.state["candidates"] = candidates
    selection_id = str(uuid.uuid4())
    cmd_matcher.state["selection_id"] = selection_id

    # 先创建选择会话与超时任务，再发送列表；use_reject=True 时 reject 会
    # 中断当前 handler 并等待新事件重新执行，故会话必须在 reject 之前建好。
    task = asyncio.create_task(
        timeout_session(bot, event, user_id, selection_id, "选择已过期，请重新搜索")
    )
    session_manager.create_selection(user_id, selection_id, task)
    session_manager.reset_current_task(user_id)

    if use_reject:
        await cmd_matcher.reject("\n".join(lines))  # 抛 RejectedException，以下不可达
    else:
        await cmd_matcher.send("\n".join(lines))


async def dispatch_search_results(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    results: list[SearchResult],
    *,
    options: PresentOptions = PresentOptions(),
    prompt_suffix: str = "",
) -> None:
    """统一处理搜索结果：无结果、单结果、多结果分页。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        results: 搜索结果全量列表。
        options: 展示选项（相似度与翻页）。
        prompt_suffix: 多结果时传给 present_candidates 的附加提示。
    """
    user_id = event.get_user_id()

    if not results:
        session_manager.deactivate_chat(user_id)
        await cmd_matcher.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        session_manager.deactivate_chat(user_id)
        result = results[0]
        image_path = MEMES_DIR / result.image_path
        await cmd_matcher.send(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        await cmd_matcher.finish(
            format_metadata_line(result.entry_id, result.speaker, result.tags)
        )
        return

    page_size = options.page_size
    total_pages = max(1, (len(results) + page_size - 1) // page_size)
    cmd_matcher.state["all_results"] = results
    cmd_matcher.state["page_index"] = 0
    cmd_matcher.state["total_pages"] = total_pages
    first_page = results[0:page_size]
    await present_candidates(
        bot,
        event,
        cmd_matcher,
        first_page,
        options=options,
        page_index=0,
        total_pages=total_pages,
        prompt_suffix=prompt_suffix,
    )


async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
    """核心关键词搜索逻辑。

    流程：获取 IndexManager 并执行关键词搜索，
    再通过 dispatch_search_results 统一处理结果分支。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        keyword: 搜索关键词。
        options: 展示选项（相似度与翻页）。
    """
    user_id = event.get_user_id()

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    # 执行搜索
    try:
        results = await index_manager.search(keyword)
    except asyncio.TimeoutError:
        logger.info("用户 %s 的搜索等待读锁超时", user_id)
        await cmd_matcher.finish("索引更新较慢，请稍后再试")
        return
    except Exception:
        logger.exception("关键词搜索异常: keyword=%r", keyword)
        await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
        return

    await dispatch_search_results(bot, event, cmd_matcher, results, options=options)


async def handle_got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message,
    error_label: str = "搜索",
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
    """处理 got 选择编号的共享逻辑（含分页翻页）。

    供 meme_search.py、meme_sim.py、meme_plain_text.py 的 got("selection") 包装器调用。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
        error_label: 异常日志中的操作标签，用于区分调用方。
        options: 展示选项（相似度与翻页）。
    """
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):
        try:
            # /help 和 /cancel 旁路拦截
            text = event.get_plaintext().strip()
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            # 检查选择会话是否仍有效
            ss = session_manager.get_selection(user_id)
            if ss is None:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("选择已过期，请重新搜索")
                return

            selection_text = selection_msg.extract_plain_text().strip()

            # 下一页
            if options.next_trigger and selection_text == options.next_trigger:
                all_results: list[SearchResult] = matcher.state.get("all_results", [])
                page_index: int = matcher.state.get("page_index", 0)
                page_size = options.page_size
                if (page_index + 1) * page_size < len(all_results):
                    page_index += 1
                    matcher.state["page_index"] = page_index
                    start = page_index * page_size
                    current_page = all_results[start : start + page_size]
                    session_manager.remove_selection(user_id)
                    await present_candidates(
                        bot,
                        event,
                        matcher,
                        current_page,
                        options=options,
                        page_index=page_index,
                        total_pages=matcher.state.get("total_pages", 1),
                        use_reject=True,
                    )
                else:
                    await matcher.reject("没有更多结果了")
                return

            # 编号选择
            candidates = matcher.state.get("candidates", [])
            result = resolve_selection(matcher, candidates, selection_text)
            if isinstance(result, str):
                await matcher.reject(result)
                return

            # 有效选择：清除选择会话
            session_manager.remove_selection(user_id)
            image_path = MEMES_DIR / result.image_path
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )
            await matcher.finish(
                format_metadata_line(result.entry_id, result.speaker, result.tags)
            )

        except RejectedException:
            raise
        except asyncio.CancelledError:
            session_manager.deactivate_chat(user_id)
            raise FinishedException
        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except Exception:
            logger.exception("用户 %s 的 %s 处理异常", user_id, error_label)
            session_manager.deactivate_chat(user_id)
            raise
