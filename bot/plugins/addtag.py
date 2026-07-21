"""/addtag 命令插件 — 为指定表情包追加标签。

授权用户私聊中发送 /addtag <公开ID> <tag> [<tag>...]，
Bot 发送确认消息（包含 OCR 文本、当前标签和新增标签），
用户回复「确认」、「yes」或「y」后执行追加。
"""

import asyncio
import logging
import uuid

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
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

addtag_cmd = on_command("addtag", rule=to_me(), priority=5, block=True, aliases={"at"})


@addtag_cmd.handle()
async def handle_addtag(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """入口：授权校验 → 参数解析 → 发送确认消息。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入，含 entry_id 与标签）。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /addtag", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "addtag")
                await matcher.finish(None)
                return

            # 仅限私聊
            if event.message_type != "private":
                await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
                return

            # 会话检查
            if not session_manager.activate_chat(scope, "addtag", matcher):
                await reply_utils.finish(
                    event, matcher, "已有命令在处理中，请先 /cancel"
                )
                return

            # 解析参数
            text_part = args.extract_plain_text().strip()
            parts = text_part.split(maxsplit=1)
            if len(parts) < 2:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, "用法：/addtag <公开ID> <tag> [<tag>...]"
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
            tags = [tag.strip() for tag in parts[1].split() if tag.strip()]

            logger.debug(
                "/addtag 参数: entry_id=%s, public_id=%s, tags=%r",
                entry_id,
                public_id,
                tags,
            )

            # 确认消息（纯文本，不发送原图）
            current_tags_text = "，".join(entry.tags) if entry.tags else "(无)"
            current_tags_set = set(entry.tags)
            new_tags_text = "，".join(
                [tag for tag in tags if tag not in current_tags_set]
            )
            await reply_utils.send(
                event,
                matcher,
                f"当前 OCR 文本：{entry.text}\n"
                f"当前标签：{current_tags_text}\n"
                f"新增标签：{new_tags_text}\n"
                "回复「确认」、「yes」或「y」确认添加，回复其他内容取消",
            )

            # 存入 state
            matcher.state["entry_id"] = entry_id
            matcher.state["public_id"] = public_id
            matcher.state["tags"] = tags

            # 注册超时
            selection_id = str(uuid.uuid4())
            task = asyncio.create_task(
                timeout_session(
                    bot,
                    event,
                    scope,
                    selection_id,
                    "标签添加已取消（超时）",
                ),
            )
            session_manager.create_selection(scope, selection_id, task)
            session_manager.reset_current_task(scope)

        except asyncio.CancelledError:
            raise FinishedException


@addtag_cmd.got("confirm")
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
                    session_manager.remove_selection(scope)
                    entry_id = matcher.state["entry_id"]
                    public_id = matcher.state["public_id"]
                    tags = list(matcher.state["tags"])

                    try:
                        result = await asyncio.wait_for(
                            get_index_manager().add_tags(entry_id, tags),
                            timeout=get_index_manager().add_user_timeout,
                        )
                    except asyncio.TimeoutError:
                        await reply_utils.finish(
                            event, matcher, "添加处理超时，请稍后再试"
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
                            "/addtag 成功: entry_id=%s, tags=%r", entry_id, tags
                        )
                        added_text = (
                            "，".join(result.added_tags) if result.added_tags else "无"
                        )
                        all_text = (
                            "，".join(result.all_tags) if result.all_tags else "无"
                        )
                        await reply_utils.finish(
                            event,
                            matcher,
                            f"标签已添加 ✅\n"
                            f"ID：{public_id}\n"
                            f"本次新增：{added_text}\n"
                            f"全部标签：{all_text}",
                        )
                        return
                else:
                    session_manager.deactivate_chat(scope)
                    logger.info("用户 %s 取消 /addtag", user_id)
                    await reply_utils.finish(event, matcher, "已取消")

            except FinishedException:
                session_manager.deactivate_chat(scope)
                raise
            except RejectedException:
                raise
            except asyncio.CancelledError:
                session_manager.deactivate_chat(scope)
                raise FinishedException
            except Exception:
                logger.exception("用户 %s 的 /addtag 处理异常", user_id)
                session_manager.deactivate_chat(scope)
                raise
