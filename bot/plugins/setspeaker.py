"""/setspeaker 命令插件 — 设置或清空表情包的说话人（speaker）字段。

授权用户私聊中发送 /setspeaker <公开ID> [说话人]，
Bot 发送图片和确认消息，用户回复「确认」、「yes」或「y」后执行修改。
无 [说话人] 参数时清空 speaker。
"""

import asyncio
import logging
import uuid

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.collection_manager import (
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.index_manager import (
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._collection_utils import (
    public_id_error_message,
    resolve_entry_argument,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import ChatScope, session_manager, timeout_session

logger = logging.getLogger(__name__)

setspeaker_cmd = on_command(
    "setspeaker",
    rule=to_me(),
    priority=5,
    block=True,
    aliases={"sp"},
    force_whitespace=True,
)


@setspeaker_cmd.handle()
async def handle_setspeaker(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """入口：授权校验 → 参数解析 → 发图确认。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入），含 entry_id 与可选说话人。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /setspeaker", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "setspeaker")
                await matcher.finish(None)
                return

            # 仅限私聊
            if event.message_type != "private":
                await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
                return

            # 会话检查
            if not session_manager.activate_chat(scope, "setspeaker", matcher):
                await reply_utils.finish(
                    event, matcher, "已有命令在处理中，请先 /cancel"
                )
                return

            # 解析参数
            text_part = args.extract_plain_text().strip()
            parts = text_part.split(maxsplit=1)
            if len(parts) < 1:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, "用法：/setspeaker <公开ID> [说话人]"
                )
                return

            raw_id = parts[0]
            try:
                entry = await resolve_entry_argument(event, raw_id)
            except asyncio.TimeoutError:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "索引更新较慢，请稍后再试")
                return
            except (
                ShortIdUnavailableError,
                InvalidPublicIdError,
                MemeNotFoundError,
            ) as exc:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, public_id_error_message(exc))
                return

            entry_id = entry.id
            public_id = entry.public_id
            speaker: str | None = parts[1].strip() if len(parts) > 1 else None
            if speaker is not None and not speaker:
                speaker = None

            logger.debug("/setspeaker 参数: entry_id=%s, speaker=%r", entry_id, speaker)

            # 发送图片
            image_path = MEMES_DIR / entry.image_path
            if image_path.exists():
                await matcher.send(
                    MessageSegment.image("file://" + str(image_path.resolve()))
                )

            # 确认消息
            old_speaker_text = entry.speaker if entry.speaker else "(无)"
            new_speaker_text = speaker if speaker else "(无)"
            await reply_utils.send(
                event,
                matcher,
                f"当前说话人：{old_speaker_text}\n"
                f"新说话人：{new_speaker_text}\n"
                "回复「确认」、「yes」或「y」确认修改，回复其他内容取消",
            )

            # 存入 state
            matcher.state["entry_id"] = entry_id
            matcher.state["public_id"] = public_id
            matcher.state["speaker"] = speaker
            matcher.state["old_speaker"] = entry.speaker

            # 注册超时
            selection_id = str(uuid.uuid4())
            task = asyncio.create_task(
                timeout_session(
                    bot,
                    event,
                    scope,
                    selection_id,
                    "说话人设置已取消（超时）",
                ),
            )
            session_manager.create_selection(scope, selection_id, task)
            session_manager.reset_current_task(scope)

        except asyncio.CancelledError:
            raise FinishedException


@setspeaker_cmd.got("confirm")
async def got_confirm(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    confirm_msg: Message = Arg("confirm"),
) -> None:
    """处理用户确认/取消。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        confirm_msg: got("confirm") 接收到的消息。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        with session_manager.handler_context(scope, matcher):
            try:
                text = confirm_msg.extract_plain_text().strip()

                # 旁路拦截 /help 和 /cancel
                if await got_intercept_bypass(event, matcher, text, HELP_TEXT):
                    return

                if text.strip().lower() in ("确认", "yes", "y"):
                    entry_id = matcher.state["entry_id"]
                    public_id = matcher.state["public_id"]
                    speaker = matcher.state.get("speaker")

                    try:
                        result = await asyncio.wait_for(
                            get_index_manager().set_speaker(entry_id, speaker),
                            timeout=get_index_manager().add_user_timeout,
                        )
                    except asyncio.TimeoutError:
                        await reply_utils.finish(
                            event, matcher, "修改处理超时，请稍后再试"
                        )
                    except IndexAddCancelledError:
                        await reply_utils.finish(
                            event, matcher, "服务正在关闭，请稍后再试"
                        )
                    except RefreshInProgressError:
                        await reply_utils.finish(
                            event, matcher, "索引正在刷新，请稍后再试"
                        )
                    except ValueError:
                        await reply_utils.finish(
                            event, matcher, f"未找到 ID 为 {public_id} 的表情包"
                        )
                    else:
                        session_manager.deactivate_chat(scope)
                        logger.info(
                            "/setspeaker 成功: entry_id=%s, speaker=%r",
                            entry_id,
                            speaker,
                        )
                        old_text = result.old_speaker if result.old_speaker else "无"
                        new_text = result.new_speaker if result.new_speaker else "无"
                        await reply_utils.finish(
                            event,
                            matcher,
                            f"说话人已设置 ✅\n"
                            f"ID：{public_id}\n"
                            f"旧：{old_text}\n新：{new_text}",
                        )
                        return
                else:
                    session_manager.deactivate_chat(scope)
                    logger.info("用户 %s 取消 /setspeaker", user_id)
                    await reply_utils.finish(event, matcher, "已取消")

                # 异常统一清理
                session_manager.deactivate_chat(scope)

            except FinishedException:
                session_manager.deactivate_chat(scope)
                raise
            except RejectedException:
                raise
            except asyncio.CancelledError:
                session_manager.deactivate_chat(scope)
                raise FinishedException
            except Exception:
                logger.exception("用户 %s 的 /setspeaker 处理异常", user_id)
                session_manager.deactivate_chat(scope)
                raise
