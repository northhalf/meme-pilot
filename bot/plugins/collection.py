"""/collection 命令插件 — 创建表情包合集。"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.engine import (
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    CreateCollectionResult,
)
from bot.engine.collection_manager import InvalidCollectionNameError
from bot.engine.index_manager import IndexAddCancelledError, RefreshInProgressError
from bot.log_context import generate_request_id, set_request_id
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)
_USAGE = "用法：/collection create <名称>"
_INVALID_NAME = (
    "合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称"
)

collection_cmd = on_command("collection", rule=to_me(), priority=5, block=True)


def _format_success(result: CreateCollectionResult) -> str:
    """格式化合集创建成功回复。

    Args:
        result: 已持久化的合集创建结果。

    Returns:
        用户可见的成功文本。
    """
    collection = result.collection
    lines = [
        "合集创建完成 ✅",
        f"编号：{collection.id}",
        f"名称：{collection.name}",
    ]
    if result.registered_existing_directory:
        lines.append("已登记现有目录；目录中的图片请执行 /refresh 建立索引")
    return "\n".join(lines)


@collection_cmd.handle()
async def handle_collection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """处理合集创建命令。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊或群聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令后的完整参数消息。
    """
    _ = bot
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)
    request_id = generate_request_id()
    with set_request_id(request_id):
        if not is_authorized(user_id):
            log_unauthorized(user_id, "collection")
            await matcher.finish(None)
            return

        if event.message_type != "private":
            await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
            return

        if not session_manager.activate_chat(scope, "collection", matcher):
            await reply_utils.finish(
                event, matcher, "已有命令在处理中，请先 /cancel"
            )
            return

        try:
            text = args.extract_plain_text().strip()
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or parts[0] != "create":
                await reply_utils.finish(event, matcher, _USAGE)
                return

            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                await reply_utils.finish(event, matcher, "服务未就绪，请稍后再试")
                return

            try:
                result = await index_manager.create_collection(parts[1])
            except InvalidCollectionNameError:
                await reply_utils.finish(event, matcher, _INVALID_NAME)
                return
            except CollectionAlreadyExistsError as exc:
                existing = exc.collection
                await reply_utils.finish(
                    event,
                    matcher,
                    f"表情包合集已存在：{existing.name}（{existing.id}）",
                )
                return
            except CollectionPathConflictError:
                await reply_utils.finish(
                    event, matcher, "无法创建合集：同名路径不是可用目录"
                )
                return
            except RefreshInProgressError:
                await reply_utils.finish(event, matcher, "索引正在刷新，请稍后再试")
                return
            except IndexAddCancelledError:
                await reply_utils.finish(event, matcher, "服务正在关闭，请稍后再试")
                return
            except CollectionCreateError:
                logger.exception("合集创建和目录补偿失败")
                await reply_utils.finish(
                    event, matcher, "合集创建失败，请检查日志后重试"
                )
                return
            except Exception:
                logger.exception("合集创建失败")
                await reply_utils.finish(
                    event, matcher, "合集创建失败，请检查日志后重试"
                )
                return

            await reply_utils.finish(event, matcher, _format_success(result))
        finally:
            session_manager.deactivate_chat(scope)
