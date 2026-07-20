"""/collection 命令插件 - 管理表情包合集（create/delete/rename）。"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.engine.collection_manager import (
    CollectionNotFoundError,
    InvalidCollectionNameError,
)
from bot.index_manager import (
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionDeleteError,
    CollectionNotEmptyError,
    CollectionPathConflictError,
    CollectionRenameTargetExistsError,
    CreateCollectionResult,
    DeleteCollectionResult,
    IndexAddCancelledError,
    RefreshInProgressError,
    RenameCollectionResult,
)
from bot.log_context import generate_request_id, set_request_id
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)
_USAGE_CREATE = "用法：/collection create <名称>"
_USAGE_DELETE = "用法：/collection delete <编号|名称>"
_USAGE_RENAME = "用法：/collection rename <旧编号|名称> <新名称>"
_USAGE = _USAGE_CREATE  # 未知子命令兜底用 create 用法
_INVALID_NAME = "合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称"

collection_cmd = on_command("collection", rule=to_me(), priority=5, block=True)


def _format_create_success(result: CreateCollectionResult) -> str:
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


def _format_delete_success(result: DeleteCollectionResult) -> str:
    """格式化合集删除成功回复。

    Args:
        result: 已持久化的合集删除结果。

    Returns:
        用户可见的成功文本。
    """
    collection = result.collection
    lines = [
        "合集已删除 ✅",
        f"编号：{collection.id}",
        f"名称：{collection.name}",
    ]
    if result.reset_scope_count > 0:
        lines.append(
            f"已把 {result.reset_scope_count} 个聊天窗口的合集选择回退到全部合集"
        )
    return "\n".join(lines)


def _format_rename_success(result: RenameCollectionResult) -> str:
    """格式化合集重命名成功回复。

    Args:
        result: 已持久化的合集重命名结果。

    Returns:
        用户可见的成功文本。
    """
    collection = result.collection
    return "\n".join(
        [
            "合集已重命名 ✅",
            f"编号：{collection.id}",
            f"旧名称：{result.old_name}",
            f"新名称：{result.new_name}",
            f"更新条目：{result.entry_count}",
        ]
    )


@collection_cmd.handle()
async def handle_collection(
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """处理合集 create/delete/rename 子命令。

    Args:
        event: 私聊或群聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令后的完整参数消息。
    """
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
            await reply_utils.finish(event, matcher, "已有命令在处理中，请先 /cancel")
            return

        try:
            text = args.extract_plain_text().strip()
            parts = text.split()
            if not parts:
                await reply_utils.finish(event, matcher, _USAGE)
                return
            subcommand = parts[0]
            if subcommand == "create":
                await _handle_create(event, matcher, text)
            elif subcommand == "delete":
                await _handle_delete(event, matcher, text)
            elif subcommand == "rename":
                await _handle_rename(event, matcher, text)
            else:
                await reply_utils.finish(event, matcher, _USAGE)
        finally:
            session_manager.deactivate_chat(scope)


async def _handle_create(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """处理 create 子命令。

    Args:
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        text: 已 strip 的命令参数原文。
    """
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or parts[0] != "create":
        await reply_utils.finish(event, matcher, _USAGE_CREATE)
        return
    manager = get_index_manager()
    try:
        result = await manager.create_collection(parts[1])
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
        await reply_utils.finish(event, matcher, "无法创建合集：同名路径不是可用目录")
        return
    except RefreshInProgressError:
        await reply_utils.finish(event, matcher, "索引正在刷新，请稍后再试")
        return
    except IndexAddCancelledError:
        await reply_utils.finish(event, matcher, "服务正在关闭，请稍后再试")
        return
    except CollectionCreateError:
        logger.exception("合集创建和目录补偿失败")
        await reply_utils.finish(event, matcher, "合集创建失败，请检查日志后重试")
        return
    except Exception:
        logger.exception("合集创建失败")
        await reply_utils.finish(event, matcher, "合集创建失败，请检查日志后重试")
        return
    await reply_utils.finish(event, matcher, _format_create_success(result))


async def _handle_delete(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """处理 delete 子命令。

    Args:
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        text: 已 strip 的命令参数原文。
    """
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or parts[0] != "delete":
        await reply_utils.finish(event, matcher, _USAGE_DELETE)
        return
    manager = get_index_manager()
    target = parts[1]
    try:
        result = await manager.delete_collection(target)
    except CollectionNotFoundError:
        await reply_utils.finish(
            event,
            matcher,
            f"未找到表情包合集：{target}\n发送 /switch 查看可用合集",
        )
        return
    except CollectionNotEmptyError:
        await reply_utils.finish(
            event, matcher, "合集不为空，请先 /move 或 /del 清空后再删除"
        )
        return
    except CollectionPathConflictError:
        await reply_utils.finish(event, matcher, "无法删除合集：同名路径不是可用目录")
        return
    except RefreshInProgressError:
        await reply_utils.finish(event, matcher, "索引正在刷新，请稍后再试")
        return
    except IndexAddCancelledError:
        await reply_utils.finish(event, matcher, "服务正在关闭，请稍后再试")
        return
    except CollectionDeleteError:
        logger.exception("合集删除和目录补偿失败")
        await reply_utils.finish(event, matcher, "合集删除失败，请检查日志后重试")
        return
    except Exception:
        logger.exception("合集删除失败")
        await reply_utils.finish(event, matcher, "合集删除失败，请检查日志后重试")
        return
    await reply_utils.finish(event, matcher, _format_delete_success(result))


async def _handle_rename(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """处理 rename 子命令。

    Args:
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        text: 已 strip 的命令参数原文。
    """
    parts = text.split()
    if len(parts) != 3 or parts[0] != "rename":
        await reply_utils.finish(event, matcher, _USAGE_RENAME)
        return
    _, source_raw, new_name = parts
    manager = get_index_manager()
    try:
        result = await manager.rename_collection(source_raw, new_name)
    except InvalidCollectionNameError:
        await reply_utils.finish(event, matcher, _INVALID_NAME)
        return
    except CollectionNotFoundError:
        await reply_utils.finish(
            event,
            matcher,
            f"未找到表情包合集：{source_raw}\n发送 /switch 查看可用合集",
        )
        return
    except CollectionRenameTargetExistsError as exc:
        existing = exc.collection
        await reply_utils.finish(
            event,
            matcher,
            f"合集名称已存在：{existing.name}（{existing.id}）",
        )
        return
    except CollectionPathConflictError:
        await reply_utils.finish(
            event, matcher, "无法重命名：目标名称对应路径不是可用目录"
        )
        return
    except RefreshInProgressError:
        await reply_utils.finish(event, matcher, "索引正在刷新，请稍后再试")
        return
    except IndexAddCancelledError:
        await reply_utils.finish(event, matcher, "服务正在关闭，请稍后再试")
        return
    except CollectionCreateError:
        logger.exception("合集重命名和目录补偿失败")
        await reply_utils.finish(event, matcher, "合集重命名失败，请检查日志后重试")
        return
    except Exception:
        logger.exception("合集重命名失败")
        await reply_utils.finish(event, matcher, "合集重命名失败，请检查日志后重试")
        return
    await reply_utils.finish(event, matcher, _format_rename_success(result))
