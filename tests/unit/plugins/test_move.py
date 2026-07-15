"""/move 命令插件单元测试。"""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException, RejectedException

from bot.engine.collection_manager import (
    CollectionNotFoundError,
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.engine.index_manager import (
    DuplicateMemeInCollectionError,
    IndexAddCancelledError,
    MemeMoveError,
    MemeMoveSourceExpiredError,
    MovePreview,
    MoveSourceSnapshot,
    MoveResult,
    RefreshInProgressError,
)
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import CollectionSelection, MemePublicId
from bot.session import ChatScope
from tests.conftest import extract_message_text

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins import move
    from bot.plugins.move import got_confirm, handle_move


def _scope(user_id: str = "12345") -> ChatScope:
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))


def _event(
    user_id: str = "12345", *, message_type: str = "private", text: str = "/move"
) -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = message_type
    event.message_id = 88
    return event


def _message(text: str) -> MagicMock:
    message = MagicMock()
    message.extract_plain_text.return_value = text
    return message


def _matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.send = AsyncMock()
    matcher.finish = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _source() -> MemeEntry:
    return MemeEntry(
        id=42,
        image_path="新三国/a.webp",
        text="丞相何故发笑",
        collection_id=1,
        local_id=3,
        collection_name="新三国",
    )


def _target() -> CollectionSelection:
    return CollectionSelection(2, "甄嬛传")


def _preview() -> MovePreview:
    return MovePreview(
        entry_id=42,
        old_public_id=MemePublicId(1, 3),
        source_collection_name="新三国",
        target_collection_id=2,
        target_collection_name="甄嬛传",
        expected_public_id=MemePublicId(2, 5),
        source_snapshot=MoveSourceSnapshot(_source(), (11, 22)),
    )


def _result() -> MoveResult:
    return MoveResult(
        entry_id=42,
        old_public_id=MemePublicId(1, 3),
        new_public_id=MemePublicId(2, 6),
        target_collection_name="甄嬛传",
        old_image_path="新三国/a.webp",
        new_image_path="甄嬛传/a.webp",
    )


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.prepare_move = AsyncMock(return_value=_preview())
    manager.move = AsyncMock(return_value=_result())
    manager.add_user_timeout = 60.0
    return manager


def _session_manager() -> MagicMock:
    session = MagicMock()
    session.activate_chat.return_value = True

    @contextmanager
    def handler_context(scope: ChatScope, matcher: MagicMock):
        yield

    session.handler_context.side_effect = handler_context
    return session


@pytest.mark.asyncio
async def test_group_chat_rejected_before_activation_with_reply() -> None:
    """群聊必须在激活会话前拒绝，并使用 reply 文本。"""
    session = _session_manager()
    matcher = _matcher()
    event = _event(message_type="group")
    with (
        patch.object(move, "is_authorized", return_value=True),
        patch.object(move, "session_manager", session),
    ):
        await handle_move(_bot(), event, matcher, _message("1.3 甄嬛传"))

    session.activate_chat.assert_not_called()
    sent = matcher.finish.await_args.args[0]
    assert isinstance(sent, Message)
    assert sent[0].type == "reply"
    assert extract_message_text(sent) == "此命令仅限私聊使用"


@pytest.mark.asyncio
async def test_confirmation_contains_expected_id_without_image_and_saves_snapshots() -> (
    None
):
    """确认只发文本，并保存内部目标与公开快照。"""
    manager = _manager()
    session = _session_manager()
    matcher = _matcher()
    with (
        patch.object(move, "is_authorized", return_value=True),
        patch.object(move, "session_manager", session),
        patch.object(move, "get_index_manager", return_value=manager),
    ):
        await handle_move(_bot(), _event(), matcher, _message("1.3 合集 名称"))

    manager.prepare_move.assert_awaited_once_with(_scope(), "1.3", "合集 名称")
    text = extract_message_text(matcher.send.await_args.args[0])
    assert "源合集：新三国（1）" in text
    assert "目标合集：甄嬛传（2）" in text
    assert "预计新编号：2.5" in text
    assert matcher.state["move_preview"] == _preview()
    session.create_selection.assert_called_once()
    session.reset_current_task.assert_called_once_with(_scope())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidPublicIdError("bad"), "表情包 ID 格式错误"),
        (ShortIdUnavailableError("3"), "全部合集模式下请使用完整 ID"),
        (MemeNotFoundError("1.9"), "未找到 ID 为 1.9 的表情包"),
        (CollectionNotFoundError("坏目标"), "未找到表情包合集：坏目标"),
        (MemeMoveSourceExpiredError("raw"), "原表情包已变化，请重新执行 /move"),
        (ValueError("底层原始错误"), "表情包已属于目标合集"),
        (asyncio.TimeoutError(), "索引更新较慢，请稍后再试"),
    ],
)
async def test_pre_confirmation_errors_have_fixed_messages_and_deactivate(
    exc: Exception, expected: str
) -> None:
    """确认前错误必须固定映射、清理会话且不泄漏原始异常。"""
    manager = _manager()
    manager.prepare_move.side_effect = exc
    session = _session_manager()
    matcher = _matcher()

    with (
        patch.object(move, "is_authorized", return_value=True),
        patch.object(move, "session_manager", session),
        patch.object(move, "get_index_manager", return_value=manager),
    ):
        await handle_move(_bot(), _event(), matcher, _message("1.3 坏目标"))

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert expected in text
    assert "底层原始错误" not in text
    session.deactivate_chat.assert_called_once_with(_scope())
    session.create_selection.assert_not_called()


@pytest.mark.asyncio
async def test_uninitialized_manager_deactivates_with_fixed_message() -> None:
    """会话激活后 IndexManager 未初始化时应固定回复并清理。"""
    session = _session_manager()
    matcher = _matcher()
    with (
        patch.object(move, "is_authorized", return_value=True),
        patch.object(move, "session_manager", session),
        patch.object(
            move,
            "get_index_manager",
            side_effect=RuntimeError("内部初始化细节"),
        ),
    ):
        await handle_move(_bot(), _event(), matcher, _message("1.3 目标"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == (
        "服务未就绪，请稍后再试"
    )
    session.deactivate_chat.assert_called_once_with(_scope())


@pytest.mark.asyncio
async def test_invalid_arguments_deactivate_and_show_usage() -> None:
    session = _session_manager()
    matcher = _matcher()
    with (
        patch.object(move, "is_authorized", return_value=True),
        patch.object(move, "session_manager", session),
    ):
        await handle_move(_bot(), _event(), matcher, _message("1.3"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == (
        "用法：/move <id> <目标合集编号|名称>"
    )
    session.deactivate_chat.assert_called_once_with(_scope())


@pytest.mark.asyncio
async def test_success_reports_actual_id_and_validates_target_name() -> None:
    """确认执行应传目标名称快照并报告实际编号。"""
    manager = _manager()
    session = _session_manager()
    matcher = _matcher(state={"move_preview": _preview()})
    with (
        patch.object(move, "session_manager", session),
        patch.object(move, "get_index_manager", return_value=manager),
        patch.object(move, "got_intercept_bypass", new=AsyncMock(return_value=False)),
    ):
        await got_confirm(_bot(), _event(), matcher, _message("确认"))

    manager.move.assert_awaited_once_with(
        42,
        2,
        expected_source=_preview().source_snapshot,
        expected_target_name="甄嬛传",
    )
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "移动完成 ✅" in text
    assert "原编号：1.3" in text
    assert "新编号：2.6" in text
    assert "目标合集：甄嬛传" in text
    session.deactivate_chat.assert_called_with(_scope())


@pytest.mark.asyncio
async def test_help_bypass_keeps_move_session_active() -> None:
    """确认期间 /help reject 后必须继续等待，不清理移动会话。"""
    session = _session_manager()
    matcher = _matcher(state={})
    bypass = AsyncMock(side_effect=RejectedException())
    with (
        patch.object(move, "session_manager", session),
        patch.object(move, "got_intercept_bypass", new=bypass),
    ):
        with pytest.raises(RejectedException):
            await got_confirm(_bot(), _event(), matcher, _message("/help"))

    session.deactivate_chat.assert_not_called()


@pytest.mark.asyncio
async def test_non_confirmation_cancels_and_cleans_session() -> None:
    manager = _manager()
    session = _session_manager()
    matcher = _matcher(state={})
    with (
        patch.object(move, "session_manager", session),
        patch.object(move, "get_index_manager", return_value=manager),
        patch.object(move, "got_intercept_bypass", new=AsyncMock(return_value=False)),
    ):
        await got_confirm(_bot(), _event(), matcher, _message("不"))

    manager.move.assert_not_awaited()
    assert extract_message_text(matcher.finish.await_args.args[0]) == "已取消移动"
    session.deactivate_chat.assert_called_with(_scope())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (MemeMoveError("raw"), "移动失败，索引将在下次刷新时检查一致性"),
        (RefreshInProgressError("raw"), "索引正在刷新，请稍后再试"),
        (IndexAddCancelledError("raw"), "服务正在关闭，请稍后再试"),
        (asyncio.TimeoutError(), "移动处理超时，请稍后再试"),
        (CollectionNotFoundError("raw"), "目标合集已失效，请重新 /move"),
        (
            MemeMoveSourceExpiredError("raw"),
            "原表情包已变化，请重新执行 /move",
        ),
        (ValueError("raw"), "表情包状态已变化，请重新 /move"),
    ],
)
async def test_confirmation_errors_are_fixed_and_always_cleanup(
    exc: Exception, expected: str
) -> None:
    manager = _manager()
    manager.move.side_effect = exc
    session = _session_manager()
    matcher = _matcher(state={"move_preview": _preview()})
    with (
        patch.object(move, "session_manager", session),
        patch.object(move, "get_index_manager", return_value=manager),
        patch.object(move, "got_intercept_bypass", new=AsyncMock(return_value=False)),
    ):
        await got_confirm(_bot(), _event(), matcher, _message("yes"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == expected
    session.deactivate_chat.assert_called_with(_scope())


@pytest.mark.asyncio
async def test_duplicate_conflict_missing_lookup_reports_unknown() -> None:
    manager = _manager()
    manager.move.side_effect = DuplicateMemeInCollectionError(99)
    metadata = MagicMock()
    metadata.get_entry.return_value = None
    session = _session_manager()
    matcher = _matcher(state={"move_preview": _preview()})
    with (
        patch.object(move, "session_manager", session),
        patch.object(move, "get_index_manager", return_value=manager),
        patch.object(move, "get_metadata_store", return_value=metadata),
        patch.object(move, "got_intercept_bypass", new=AsyncMock(return_value=False)),
    ):
        await got_confirm(_bot(), _event(), matcher, _message("y"))

    assert extract_message_text(matcher.finish.await_args.args[0]) == (
        "目标合集已存在相同内容的表情包：未知"
    )
    session.deactivate_chat.assert_called_with(_scope())


@pytest.mark.asyncio
async def test_finished_exception_and_cancelled_error_do_not_leak_session() -> None:
    """finish 控制流与重复取消均必须清理会话。"""
    for side_effect in (FinishedException(), asyncio.CancelledError()):
        manager = _manager()
        manager.move.side_effect = side_effect
        session = _session_manager()
        matcher = _matcher(state={"move_preview": _preview()})
        with (
            patch.object(move, "session_manager", session),
            patch.object(move, "get_index_manager", return_value=manager),
            patch.object(
                move, "got_intercept_bypass", new=AsyncMock(return_value=False)
            ),
        ):
            with pytest.raises(FinishedException):
                await got_confirm(_bot(), _event(), matcher, _message("确认"))
        session.deactivate_chat.assert_called_with(_scope())
