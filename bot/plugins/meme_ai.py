"""/ai 命令插件 — AI 描述匹配表情包。

授权用户在私聊中发送 /ai <自然语言描述>，Bot 通过 Embedding 语义搜索
+ LLM 精排两阶段匹配表情包并发送结果图片。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._search_utils import format_metadata_line
from bot.session import session_manager

logger = logging.getLogger(__name__)

ai_cmd = on_command("ai", rule=to_me(), priority=5, block=True)


@ai_cmd.handle()
async def handle_ai(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/ai 命令处理入口。

    流程：授权校验 → 并发发送进度 + AI 匹配 → 发送结果。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /ai", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "ai")
                await matcher.finish(None)
                return

            # 群聊拦截：/ai 仅限私聊使用
            if event.message_type != "private":
                logger.info("用户 %s 在群聊中调用 /ai，已拒绝", user_id)
                await matcher.finish("此命令仅限私聊使用")
                return

            # 会话激活
            if not session_manager.activate_chat(user_id, "ai", matcher):
                await matcher.finish("已有命令在处理中，请先 /cancel")
                return

            # 获取 IndexManager
            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                session_manager.deactivate_chat(user_id)
                await matcher.finish("服务未就绪，请稍后再试")
                return

            # 提取描述
            raw_text = event.get_plaintext().strip()
            description = raw_text.removeprefix("/ai").removeprefix("ai").strip()
            if not description:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("/ai <自然语言描述>")
                return

            logger.debug("/ai 描述: %r", description)

            # 并发：发送进度提示 + 执行 AI 匹配
            try:
                _, match_result = await asyncio.gather(
                    matcher.send("正在根据你的描述搜索表情包，请稍候..."),
                    index_manager.ai_match(description),
                )
            except asyncio.TimeoutError:
                logger.info("用户 %s 的 /ai 等待读锁超时", user_id)
                session_manager.deactivate_chat(user_id)
                await matcher.finish("索引更新较慢，请稍后再试")
                return
            except ValueError:
                logger.warning("AI 匹配 embedding 异常: description=%r", description)
                session_manager.deactivate_chat(user_id)
                await matcher.finish("AI 服务暂时不可用，稍后重试")
                return
            except Exception:
                logger.exception("AI 匹配异常: description=%r", description)
                session_manager.deactivate_chat(user_id)
                await matcher.finish("AI 服务暂时不可用，稍后重试")
                return

            if match_result is None:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("没有找到匹配的表情包 🙁")
                return

            logger.info("/ai 命中 entry_id=%s", match_result.entry_id)

            # 发送匹配图片（本地文件使用 file:/// URI）
            image_path = MEMES_DIR / match_result.image_path
            session_manager.deactivate_chat(user_id)
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )
            await matcher.finish(
                format_metadata_line(
                    match_result.entry_id,
                    match_result.speaker,
                    match_result.tags,
                )
            )
        except asyncio.CancelledError:
            session_manager.deactivate_chat(user_id)
            raise FinishedException
