"""/collection 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.engine.collection_manager import (
    CollectionNotFoundError,
    InvalidCollectionNameError,
)
from bot.engine.types import MemeCollection
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
from bot.session import ChatScope
from tests.conftest import extract_message_text

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_on_command = MagicMock(return_value=_mock_cmd)
_to_me_rule = MagicMock(name="collection_to_me_rule")

with (
    patch("nonebot.on_command", _on_command),
    patch("nonebot.rule.to_me", return_value=_to_me_rule),
):
    from bot.plugins import collection
    from bot.plugins.collection import handle_collection


def _event(user_id: str = "12345", *, message_type: str = "private") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = message_type
    event.message_id = 88
    event.group_id = 98765 if message_type == "group" else None
    return event


def _args(text: str) -> MagicMock:
    message = MagicMock()
    message.extract_plain_text.return_value = text
    return message


def _matcher() -> MagicMock:
    matcher = MagicMock()
    matcher.finish = AsyncMock()
    return matcher


def _scope(user_id: str = "12345") -> ChatScope:
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))


def _result(*, existing_directory: bool = False) -> CreateCollectionResult:
    return CreateCollectionResult(
        collection=MemeCollection(3, "新三国"),
        registered_existing_directory=existing_directory,
    )


def test_collection_matcher_registration_has_no_aliases() -> None:
    args, kwargs = _on_command.call_args
    assert args == ("collection",)
    assert kwargs["rule"] is _to_me_rule
    assert kwargs["priority"] == 5
    assert kwargs["block"] is True
    assert kwargs["force_whitespace"] is True
    assert "aliases" not in kwargs


@pytest.mark.asyncio
async def test_create_success_keeps_current_selection() -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(return_value=_result())
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("create 新三国"))

    manager.create_collection.assert_awaited_once_with("新三国")
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert text == "合集创建完成 ✅\n编号：3\n名称：新三国"
    deactivate.assert_called_once_with(_scope())
    assert (
        not hasattr(manager, "switch_collection")
        or not manager.switch_collection.called
    )


@pytest.mark.asyncio
async def test_existing_directory_success_adds_refresh_hint() -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(return_value=_result(existing_directory=True))
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("create 新三国"))

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "已登记现有目录" in text
    assert "/refresh" in text
    deactivate.assert_called_once_with(_scope())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "用法：/collection create <名称>"),
        ("create", "用法：/collection create <名称>"),
        ("delete", "用法：/collection delete <编号|名称>"),
        ("rename", "用法：/collection rename <旧编号|名称> <新名称>"),
        ("rename 新三国", "用法：/collection rename <旧编号|名称> <新名称>"),
        ("bogus 新三国", "用法：/collection create <名称>"),
    ],
)
async def test_invalid_subcommand_replies_usage(raw: str, expected: str) -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
    ):
        await handle_collection(_event(), matcher, _args(raw))

    assert extract_message_text(matcher.finish.await_args.args[0]) == expected
    deactivate.assert_called_once_with(_scope())


@pytest.mark.asyncio
async def test_unauthorized_user_is_silently_ignored() -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=False),
        patch.object(collection, "get_index_manager") as get_manager,
    ):
        await handle_collection(_event("999"), matcher, _args("create 新三国"))

    matcher.finish.assert_awaited_once_with(None)
    get_manager.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_group_user_is_silently_ignored() -> None:
    matcher = _matcher()
    event = _event("999", message_type="group")

    with (
        patch.object(collection, "is_authorized", return_value=False),
        patch.object(collection.session_manager, "activate_chat") as activate,
        patch.object(collection, "get_index_manager") as get_manager,
    ):
        await handle_collection(event, matcher, _args("create 新三国"))

    assert event.group_id == 98765
    assert event.message_id == 88
    matcher.finish.assert_awaited_once_with(None)
    assert matcher.finish.await_args.args[0] is None
    activate.assert_not_called()
    get_manager.assert_not_called()


@pytest.mark.asyncio
async def test_group_chat_rejected_before_activation() -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat") as activate,
    ):
        await handle_collection(
            _event(message_type="group"), matcher, _args("create 新三国")
        )

    activate.assert_not_called()
    reply = matcher.finish.await_args.args[0]
    assert isinstance(reply, Message)
    assert extract_message_text(reply) == "此命令仅限私聊使用"


@pytest.mark.asyncio
async def test_active_session_rejects_new_command() -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=False),
        patch.object(collection, "get_index_manager") as get_manager,
    ):
        await handle_collection(_event(), matcher, _args("create 新三国"))

    assert "已有命令在处理中" in extract_message_text(matcher.finish.await_args.args[0])
    get_manager.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            InvalidCollectionNameError("坏 名称"),
            "合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称",
        ),
        (
            CollectionAlreadyExistsError(MemeCollection(2, "新三国")),
            "表情包合集已存在：新三国（2）",
        ),
        (
            CollectionPathConflictError("新三国"),
            "无法创建合集：同名路径不是可用目录",
        ),
        (
            RefreshInProgressError("raw"),
            "索引正在刷新，请稍后再试",
        ),
        (
            IndexAddCancelledError("Bot 正在关闭"),
            "服务正在关闭，请稍后再试",
        ),
        (
            CollectionCreateError("internal path"),
            "合集创建失败，请检查日志后重试",
        ),
    ],
)
async def test_domain_errors_have_fixed_messages_and_cleanup(
    error: Exception, expected: str
) -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(side_effect=error)
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("create 新三国"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == expected
    assert "internal path" not in expected
    deactivate.assert_called_once_with(_scope())


@pytest.mark.asyncio
async def test_unexpected_error_does_not_leak_details_and_cleanup() -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(
        side_effect=ValueError("sqlite failed at /srv/memes/secret")
    )
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("create 新三国"))

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert text == "合集创建失败，请检查日志后重试"
    assert "sqlite" not in text
    assert "/srv/memes" not in text
    deactivate.assert_called_once_with(_scope())


@pytest.mark.asyncio
async def test_cancelled_create_propagates_and_cleanup() -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(side_effect=asyncio.CancelledError)
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
        pytest.raises(asyncio.CancelledError),
    ):
        await handle_collection(_event(), matcher, _args("create 新三国"))

    deactivate.assert_called_once_with(_scope())


def _delete_result(*, reset_scopes: int = 0) -> DeleteCollectionResult:
    return DeleteCollectionResult(
        collection=MemeCollection(3, "新三国"),
        reset_scope_count=reset_scopes,
    )


@pytest.mark.asyncio
async def test_delete_success_replies_summary() -> None:
    manager = MagicMock()
    manager.delete_collection = AsyncMock(return_value=_delete_result(reset_scopes=2))
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("delete 新三国"))

    manager.delete_collection.assert_awaited_once_with("新三国")
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert text == (
        "合集已删除 ✅\n编号：3\n名称：新三国\n"
        "已把 2 个聊天窗口的合集选择回退到全部合集"
    )
    deactivate.assert_called_once_with(_scope())


@pytest.mark.asyncio
async def test_delete_success_without_scope_reset_omits_line() -> None:
    manager = MagicMock()
    manager.delete_collection = AsyncMock(return_value=_delete_result(reset_scopes=0))
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat"),
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("delete 3"))

    manager.delete_collection.assert_awaited_once_with("3")
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert text == "合集已删除 ✅\n编号：3\n名称：新三国"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "target", "expected"),
    [
        (
            CollectionNotFoundError("新三国"),
            "新三国",
            "未找到表情包合集：新三国\n发送 /switch 查看可用合集",
        ),
        (
            CollectionNotEmptyError("新三国"),
            "新三国",
            "合集不为空，请先 /move 或 /del 清空后再删除",
        ),
        (
            CollectionPathConflictError("新三国"),
            "新三国",
            "无法删除合集：同名路径不是可用目录",
        ),
        (RefreshInProgressError("raw"), "新三国", "索引正在刷新，请稍后再试"),
        (IndexAddCancelledError("Bot 正在关闭"), "新三国", "服务正在关闭，请稍后再试"),
        (CollectionDeleteError("internal"), "新三国", "合集删除失败，请检查日志后重试"),
    ],
)
async def test_delete_errors_have_fixed_messages(
    error: Exception, target: str, expected: str
) -> None:
    manager = MagicMock()
    manager.delete_collection = AsyncMock(side_effect=error)
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat"),
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args(f"delete {target}"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == expected
    assert "internal" not in expected


def _rename_result() -> RenameCollectionResult:
    return RenameCollectionResult(
        collection=MemeCollection(3, "旧三国"),
        old_name="新三国",
        new_name="旧三国",
        entry_count=12,
    )


@pytest.mark.asyncio
async def test_rename_success_replies_summary() -> None:
    manager = MagicMock()
    manager.rename_collection = AsyncMock(return_value=_rename_result())
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("rename 新三国 旧三国"))

    manager.rename_collection.assert_awaited_once_with("新三国", "旧三国")
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert text == (
        "合集已重命名 ✅\n编号：3\n旧名称：新三国\n新名称：旧三国\n更新条目：12"
    )
    deactivate.assert_called_once_with(_scope())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            InvalidCollectionNameError("坏 名称"),
            "合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称",
        ),
        (
            CollectionNotFoundError("新三国"),
            "未找到表情包合集：新三国\n发送 /switch 查看可用合集",
        ),
        (
            CollectionRenameTargetExistsError(MemeCollection(2, "甄嬛传")),
            "合集名称已存在：甄嬛传（2）",
        ),
        (
            CollectionPathConflictError("旧三国"),
            "无法重命名：目标名称对应路径不是可用目录",
        ),
        (RefreshInProgressError("raw"), "索引正在刷新，请稍后再试"),
        (IndexAddCancelledError("Bot 正在关闭"), "服务正在关闭，请稍后再试"),
        (CollectionCreateError("internal"), "合集重命名失败，请检查日志后重试"),
    ],
)
async def test_rename_errors_have_fixed_messages(
    error: Exception, expected: str
) -> None:
    manager = MagicMock()
    manager.rename_collection = AsyncMock(side_effect=error)
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat", return_value=True),
        patch.object(collection.session_manager, "deactivate_chat"),
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(_event(), matcher, _args("rename 新三国 旧三国"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == expected
    assert "internal" not in expected
