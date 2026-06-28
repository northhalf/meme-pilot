"""/ai 命令插件 — AI 描述匹配表情包。

授权用户在私聊中发送 /ai <自然语言描述>，Bot 通过 Embedding 语义搜索
+ LLM 精排两阶段匹配表情包并发送结果图片。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.app_state import get_ai_matcher, get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.ai_matcher import AIMatcher, AIMatchResult
from bot.session import activate_chat, deactivate_chat

logger = logging.getLogger(__name__)

ai_cmd = on_command("ai", rule=to_me(), priority=5, block=True)


async def _do_match(
    ai_matcher: AIMatcher,
    description: str,
) -> AIMatchResult | str:
    """执行 AI 匹配，返回结果或错误提示。

    Args:
        ai_matcher: AI 匹配器实例。
        description: 用户自然语言描述。

    Returns:
        AIMatchResult 表示匹配成功；str 表示错误提示文本。
    """
    try:
        result = await ai_matcher.match(description)
    except ValueError:
        logger.warning("AI 匹配 embedding 异常: description=%r", description)
        return "AI 服务暂时不可用，稍后重试"
    except Exception:
        logger.exception("AI 匹配异常: description=%r", description)
        return "AI 服务暂时不可用，稍后重试"

    if result is None:
        return "没有找到匹配的表情包 🙁"
    return result


@ai_cmd.handle()
async def handle_ai(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/ai 命令处理入口。

    流程：授权校验 → 锁检查 → 空索引检查 → 并发发送进度 + AI 匹配 → 发送结果。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /ai", user_id)

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "ai")
        return

    # 群聊拦截：/ai 仅限私聊使用
    if event.message_type != "private":
        logger.info("用户 %s 在群聊中调用 /ai，已拒绝", user_id)
        await matcher.finish("此命令仅限私聊使用")
        return

    # 会话激活
    if not activate_chat(user_id, "ai", matcher):
        await matcher.finish("已有命令在处理中，请先 /cancel")
        return

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        deactivate_chat(user_id)
        await matcher.finish("服务未就绪，请稍后再试")
        return

    # 检查索引锁（只读检查，不持有锁）
    if index_manager.is_locked:
        logger.info("用户 %s 的 /ai 被拒绝：索引正在更新", user_id)
        deactivate_chat(user_id)
        await matcher.finish("索引正在更新，请稍后再试")
        return

    # 提取描述
    raw_text = event.get_plaintext().strip()
    description = raw_text.removeprefix("/ai").removeprefix("ai").strip()
    if not description:
        deactivate_chat(user_id)
        await matcher.finish("/ai <自然语言描述>")
        return

    # 检查索引是否为空
    if index_manager.entry_count == 0:
        deactivate_chat(user_id)
        await matcher.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 获取 AIMatcher
    try:
        ai_matcher = get_ai_matcher()
    except RuntimeError:
        logger.error("AIMatcher 尚未初始化")
        deactivate_chat(user_id)
        await matcher.finish("服务未就绪，请稍后再试")
        return

    # 并发：发送进度提示 + 执行 AI 匹配
    _, match_result = await asyncio.gather(
        matcher.send("正在根据你的描述搜索表情包，请稍候..."),
        _do_match(ai_matcher, description),
    )

    # 错误提示
    if isinstance(match_result, str):
        deactivate_chat(user_id)
        await matcher.finish(match_result)
        return

    # 发送匹配图片（本地文件使用 file:/// URI）
    image_path = MEMES_DIR / match_result.filename
    deactivate_chat(user_id)
    await matcher.finish(MessageSegment.image("file://" + str(image_path.resolve())))
