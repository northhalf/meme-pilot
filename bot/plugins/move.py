"""/move 命令插件 — 跨合集移动表情包。"""

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
from bot.app_state import get_index_manager, get_metadata_store
from bot.auth import is_authorized, log_unauthorized
from bot.engine.collection_manager import (
    CollectionNotFoundError,
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.index_manager import (
    DuplicateMemeInCollectionError,
    IndexAddCancelledError,
    MemeMoveError,
    MemeMoveSourceExpiredError,
    MovePreview,
    RefreshInProgressError,
)
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._collection_utils import public_id_error_message
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import ChatScope, session_manager, timeout_session

logger = logging.getLogger(__name__)

move_cmd = on_command("move", rule=to_me(), priority=5, block=True, aliases={"mv"})


@move_cmd.handle()
async def handle_move(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """解析移动参数并发送纯文本确认。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 源公开 ID 与目标合集参数。
    """
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)
    with set_request_id(generate_request_id()):
        logger.info("用户 %s 调用 /move", user_id)
        try:
            if not is_authorized(user_id):
                log_unauthorized(user_id, "move")
                await matcher.finish(None)
                return
            if event.message_type != "private":
                await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
                return
            if not session_manager.activate_chat(scope, "move", matcher):
                await reply_utils.finish(
                    event, matcher, "已有命令在处理中，请先 /cancel"
                )
                return

            text = args.extract_plain_text().strip()
            parts = text.split(maxsplit=1)
            if len(parts) != 2:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, "用法：/move <id> <目标合集编号|名称>"
                )
                return
            source_raw, target_raw = parts
            try:
                manager = get_index_manager()
            except RuntimeError:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "服务未就绪，请稍后再试")
                return
            try:
                preview = await manager.prepare_move(scope, source_raw, target_raw)
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
            except CollectionNotFoundError:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, f"未找到表情包合集：{target_raw}"
                )
                return
            except MemeMoveSourceExpiredError:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, "原表情包已变化，请重新执行 /move"
                )
                return
            except ValueError:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "表情包已属于目标合集")
                return

            matcher.state["move_preview"] = preview
            await reply_utils.send(
                event,
                matcher,
                "确认移动表情包：\n"
                f"源合集：{preview.source_collection_name}"
                f"（{preview.old_public_id.collection_id}）\n"
                f"目标合集：{preview.target_collection_name}"
                f"（{preview.target_collection_id}）\n"
                f"当前编号：{preview.old_public_id}\n"
                f"预计新编号：{preview.expected_public_id}\n\n"
                "回复「确认」、「yes」或「y」确认移动，回复其他内容取消",
            )
            selection_id = str(uuid.uuid4())
            task = asyncio.create_task(
                timeout_session(
                    bot,
                    event,
                    scope,
                    selection_id,
                    "移动已取消（超时）",
                )
            )
            session_manager.create_selection(scope, selection_id, task)
            session_manager.reset_current_task(scope)
        except asyncio.CancelledError:
            raise FinishedException


@move_cmd.got("confirm")
async def got_confirm(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    confirm_msg: Message = Arg("confirm"),
) -> None:
    """执行或取消已预览的移动。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        confirm_msg: 用户确认消息。
    """
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)
    with set_request_id(generate_request_id()):
        with session_manager.handler_context(scope, matcher):
            keep_session_active = False
            try:
                text = confirm_msg.extract_plain_text().strip()
                if await got_intercept_bypass(event, matcher, text, HELP_TEXT):
                    return
                if text.lower() not in {"确认", "yes", "y"}:
                    await reply_utils.finish(event, matcher, "已取消移动")
                    return

                session_manager.remove_selection(scope)
                preview = matcher.state["move_preview"]
                if not isinstance(preview, MovePreview):
                    raise ValueError("移动预览快照无效")
                if preview.source_snapshot is None:
                    raise ValueError("移动预览缺少源身份快照")
                manager = get_index_manager()
                try:
                    result = await asyncio.wait_for(
                        manager.move(
                            preview.entry_id,
                            preview.target_collection_id,
                            expected_source=preview.source_snapshot,
                            expected_target_name=preview.target_collection_name,
                        ),
                        timeout=manager.add_user_timeout,
                    )
                except DuplicateMemeInCollectionError as exc:
                    conflict = get_metadata_store().get_entry(exc.conflicting_entry_id)
                    conflict_id = (
                        str(conflict.public_id) if conflict is not None else "未知"
                    )
                    await reply_utils.finish(
                        event,
                        matcher,
                        f"目标合集已存在相同内容的表情包：{conflict_id}",
                    )
                except MemeMoveSourceExpiredError:
                    await reply_utils.finish(
                        event, matcher, "原表情包已变化，请重新执行 /move"
                    )
                except MemeMoveError:
                    await reply_utils.finish(
                        event,
                        matcher,
                        "移动失败，索引将在下次刷新时检查一致性",
                    )
                except RefreshInProgressError:
                    await reply_utils.finish(event, matcher, "索引正在刷新，请稍后再试")
                except IndexAddCancelledError:
                    await reply_utils.finish(event, matcher, "服务正在关闭，请稍后再试")
                except asyncio.TimeoutError:
                    await reply_utils.finish(event, matcher, "移动处理超时，请稍后再试")
                except CollectionNotFoundError:
                    await reply_utils.finish(
                        event, matcher, "目标合集已失效，请重新 /move"
                    )
                except ValueError:
                    await reply_utils.finish(
                        event, matcher, "表情包状态已变化，请重新 /move"
                    )
                else:
                    logger.info(
                        "/move 成功: entry_id=%s, old=%s, new=%s",
                        result.entry_id,
                        result.old_public_id,
                        result.new_public_id,
                    )
                    await reply_utils.finish(
                        event,
                        matcher,
                        "移动完成 ✅\n"
                        f"原编号：{result.old_public_id}\n"
                        f"新编号：{result.new_public_id}\n"
                        f"目标合集：{result.target_collection_name}",
                    )
            except FinishedException:
                raise
            except RejectedException:
                keep_session_active = True
                raise
            except asyncio.CancelledError:
                raise FinishedException
            except Exception:
                logger.exception("用户 %s 的 /move 处理异常", user_id)
                raise
            finally:
                if not keep_session_active:
                    session_manager.deactivate_chat(scope)
