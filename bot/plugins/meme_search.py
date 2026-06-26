"""/search 命令插件 — 关键词搜索表情包。

授权用户在私聊中发送 /search <关键词>，Bot 通过 KeywordSearcher
对索引 OCR 文本做模糊匹配，返回搜索结果。
"""

import asyncio
import logging

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

from bot.app_state import get_index_manager, get_keyword_searcher
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.session import (
    cancel,
    check_and_cancel,
    is_cancelled,
    register,
    timeout_session,
)

logger = logging.getLogger(__name__)

search_cmd = on_command("search", rule=to_me(), priority=5, block=True)


@search_cmd.handle()
async def handle_search(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None:
    """/search 命令入口。

    流程：授权校验 → 会话覆盖 → 锁检查 → 搜索 → 结果分支。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "search")
        return

    # 会话覆盖检查
    hint = check_and_cancel(user_id, "search")
    if hint:
        await matcher.send(hint)

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await search_cmd.finish("服务未就绪，请稍后再试")
        return

    # 检查索引锁（只读检查，不持有锁）
    if index_manager.is_locked:
        logger.info("用户 %s 的 /search 被拒绝：索引正在更新", user_id)
        await search_cmd.finish("索引正在更新，请稍后再试")
        return

    # 提取关键词
    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/search").removeprefix("search").strip()
    if not keyword:
        await search_cmd.finish("/search <关键词>")
        return

    # 检查索引是否为空
    if index_manager.entry_count == 0:
        await search_cmd.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 获取 KeywordSearcher
    try:
        keyword_searcher = get_keyword_searcher()
    except RuntimeError:
        logger.error("KeywordSearcher 尚未初始化")
        await search_cmd.finish("服务未就绪，请稍后再试")
        return

    # 执行搜索
    try:
        results = keyword_searcher.search(keyword)
    except Exception:
        logger.exception("关键词搜索异常: keyword=%r", keyword)
        await search_cmd.finish("搜索服务暂时不可用，稍后重试")
        return

    if not results:
        await search_cmd.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        # 唯一结果直接发送图片
        image_path = MEMES_DIR / results[0].filename
        await search_cmd.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        return

    # 多个结果：格式化选择列表
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.text}")
    lines.append(f"回复编号即可 (1-{len(results)})")

    # 存储候选并注册会话
    matcher.state["candidates"] = results
    register(user_id, matcher, "search")

    await matcher.send("\n".join(lines))

    # 启动超时任务
    asyncio.create_task(
        timeout_session(bot, event, user_id, "选择已过期，请重新 /search")
    )


@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    会话超时时清理 session 状态。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
    """
    user_id = event.get_user_id()

    try:
        # 会话有效性检查
        if is_cancelled(user_id):
            return

        candidates = matcher.state.get("candidates", [])
        if not candidates:
            cancel(user_id)
            await matcher.finish("搜索状态异常，请重新搜索")
            return
        text = selection_msg.extract_plain_text().strip()

        # 解析编号
        try:
            choice = int(text)
        except ValueError:
            await matcher.reject(f"无效编号，请回复 1-{len(candidates)} 之间的数字")
            return

        if choice < 1 or choice > len(candidates):
            await matcher.reject(f"无效编号，请回复 1-{len(candidates)} 之间的数字")
            return

        # 发送图片
        selected = candidates[choice - 1]
        cancel(user_id)
        image_path = MEMES_DIR / selected.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )

    except Exception:
        # 未预期异常：清理 session 状态
        logger.exception("用户 %s 的 /search 处理异常", user_id)
        cancel(user_id)
        raise
