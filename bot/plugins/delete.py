"""/del 命令插件 — 按公开 ID 删除一个或多个表情包。

授权用户私聊中发送 /del <公开ID>...，
Bot 发送摘要确认消息，用户回复「确认」、「yes」或「y」后执行删除。
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
from bot.engine.index_manager import IndexAddCancelledError, RefreshInProgressError
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import MemePublicId
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._collection_utils import (
    public_id_error_message,
    resolve_entry_argument,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import ChatScope, session_manager, timeout_session

logger = logging.getLogger(__name__)

delete_cmd = on_command("del", rule=to_me(), priority=5, block=True, aliases={"d"})


def _truncate_text(text: str, limit: int = 30) -> str:
    """截断 OCR 文本，超出长度时附加总字数提示。

    Args:
        text: 原始 OCR 文本。
        limit: 保留的最大字符数。

    Returns:
        截断后的文本描述。
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...（共 {len(text)} 字）"


@delete_cmd.handle()
async def handle_delete(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """入口：授权校验 → 参数解析 → 发送摘要确认。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入），包含待删除的 id 列表。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /del", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "del")
                await matcher.finish(None)
                return

            # 仅限私聊
            if event.message_type != "private":
                await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
                return

            # 会话检查
            if not session_manager.activate_chat(scope, "del", matcher):
                await reply_utils.finish(
                    event, matcher, "已有命令在处理中，请先 /cancel"
                )
                return

            # 解析参数
            text_part = args.extract_plain_text().strip()
            tokens = text_part.split()
            if not tokens:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "用法：/del <id>...")
                return

            entries: list[MemeEntry] = []
            seen_entry_ids: set[int] = set()
            not_found_public_ids: list[str] = []
            seen_not_found_public_ids: set[str] = set()
            for token in tokens:
                try:
                    entry = await resolve_entry_argument(event, token)
                except asyncio.TimeoutError:
                    session_manager.deactivate_chat(scope)
                    await reply_utils.finish(event, matcher, "索引更新较慢，请稍后再试")
                    return
                except MemeNotFoundError as exc:
                    public_id = str(exc.args[0]) if exc.args else token
                    if public_id not in seen_not_found_public_ids:
                        seen_not_found_public_ids.add(public_id)
                        not_found_public_ids.append(public_id)
                    continue
                except (ShortIdUnavailableError, InvalidPublicIdError) as exc:
                    session_manager.deactivate_chat(scope)
                    await reply_utils.finish(
                        event, matcher, public_id_error_message(exc)
                    )
                    return
                if entry.id not in seen_entry_ids:
                    seen_entry_ids.add(entry.id)
                    entries.append(entry)

            logger.debug(
                "/del 目标 entry_ids=%s, 未找到 public_ids=%s",
                [entry.id for entry in entries],
                not_found_public_ids,
            )

            if not entries:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "未找到任何表情包")
                return

            lines = ["确认删除以下表情包？回复「确认」、「yes」或「y」执行删除，回复其他内容取消。"]
            for entry in entries:
                lines.append(f"{entry.public_id}, {_truncate_text(entry.text)}")
            if not_found_public_ids:
                lines.append("未找到 ID：" + "、".join(not_found_public_ids))

            await reply_utils.send(event, matcher, "\n".join(lines))

            matcher.state["entry_ids"] = [entry.id for entry in entries]
            matcher.state["public_ids"] = {
                entry.id: entry.public_id for entry in entries
            }
            matcher.state["not_found_public_ids"] = not_found_public_ids

            # 注册超时
            selection_id = str(uuid.uuid4())
            task = asyncio.create_task(
                timeout_session(bot, event, scope, selection_id, "删除已取消（超时）"),
            )
            session_manager.create_selection(scope, selection_id, task)
            session_manager.reset_current_task(scope)

        except asyncio.CancelledError:
            raise FinishedException


@delete_cmd.got("confirm")
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

                    entry_ids: list[int] = matcher.state["entry_ids"]
                    public_ids: dict[int, MemePublicId] = matcher.state["public_ids"]
                    parse_not_found_ids = list(
                        matcher.state.get("not_found_public_ids", [])
                    )
                    try:
                        result = await asyncio.wait_for(
                            get_index_manager().delete(entry_ids),
                            timeout=get_index_manager().add_user_timeout,
                        )
                    except asyncio.TimeoutError:
                        await reply_utils.finish(
                            event, matcher, "删除处理超时，请稍后再试"
                        )
                    except IndexAddCancelledError:
                        await reply_utils.finish(
                            event, matcher, "服务正在关闭，请稍后再试"
                        )
                    except RefreshInProgressError:
                        await reply_utils.finish(
                            event, matcher, "索引正在刷新，请稍后再试"
                        )
                    except Exception:
                        logger.exception("用户 %s 的 /del 删除异常", user_id)
                        await reply_utils.finish(
                            event, matcher, "删除过程中发生异常，请稍后重试"
                        )
                    else:
                        session_manager.deactivate_chat(scope)
                        logger.info(
                            "/del 完成: 成功=%d, 未找到=%d, 失败=%d",
                            len(result.deleted_ids),
                            len(result.not_found_ids),
                            len(result.failed_ids),
                        )
                        logger.debug(
                            "/del 详情: 成功=%r, 未找到=%r, 失败=%r",
                            result.deleted_ids,
                            result.not_found_ids,
                            result.failed_ids,
                        )
                        lines = ["删除结果如下:"]
                        if result.deleted_ids:
                            lines.append(
                                "成功："
                                + "、".join(
                                    str(public_ids[i]) for i in result.deleted_ids
                                )
                            )
                        not_found_ids = list(
                            dict.fromkeys(
                                [
                                    *parse_not_found_ids,
                                    *(str(public_ids[i]) for i in result.not_found_ids),
                                ]
                            )
                        )
                        if not_found_ids:
                            lines.append("未找到：" + "、".join(not_found_ids))
                        if result.failed_ids:
                            failed_parts = [
                                f"ID:{public_ids[eid]} 原因:『{reason}』"
                                for eid, reason in result.failed_ids
                            ]
                            lines.append("失败：" + "、".join(failed_parts))
                        await reply_utils.finish(event, matcher, "\n".join(lines))
                        return
                else:
                    session_manager.deactivate_chat(scope)
                    logger.info("用户 %s 取消 /del", user_id)
                    await reply_utils.finish(event, matcher, "已取消删除")

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
                logger.exception("用户 %s 的 /del 处理异常", user_id)
                session_manager.deactivate_chat(scope)
                raise
