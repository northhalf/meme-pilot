"""/edittext 命令插件单元测试。"""

import asyncio
from collections.abc import Awaitable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException

from bot.engine.collection_manager import (
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.index_manager import EditTextResult
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import MemePublicId
from tests.conftest import extract_message_text

# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化
_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.edit import (
        got_confirm,
        handle_edit,
    )


async def _await_handler(result: Any | Awaitable[Any]) -> Any:
    """等待 NoneBot Handler 的宽泛 Awaitable 返回类型。"""
    return await result


def _run_handler(result: Any | Awaitable[Any]) -> Any:
    """在独立事件循环中运行 NoneBot Handler。"""
    return asyncio.run(_await_handler(result))


def _make_event(user_id: str = "12345", text: str = "/edittext") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_entry(image_path: str = "test.jpg", text: str = "旧文本") -> MagicMock:
    """创建模拟的 MemeEntry。"""
    entry = MagicMock()
    entry.id = 5
    entry.public_id = MemePublicId(1, 5)
    entry.image_path = image_path
    entry.text = text
    return entry


def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 Message 对象（CommandArg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


# ---------------------------------------------------------------------------
# handle_edit 测试
# ---------------------------------------------------------------------------


class TestHandleEdit:
    """handle_edit 入口函数测试。"""

    def test_unauthorized(self) -> None:
        """非授权用户应调用 finish(None) 结束匹配。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=False),
            patch("bot.plugins.edit.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            _run_handler(handle_edit(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            event.message_id = 123456
            matcher = _make_matcher()

            _run_handler(handle_edit(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "此命令仅限私聊使用"
            if isinstance(msg, Message):
                assert msg[0].type == "reply"

    def test_invalid_args_no_text(self) -> None:
        """参数不足 → 用法提示。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext")
            matcher = _make_matcher()

            _run_handler(handle_edit(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            args, _ = matcher.finish.await_args
            assert "用法" in extract_message_text(args[0])

    def test_invalid_args_not_number(self) -> None:
        """entry_id 非数字 → 用法提示。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(side_effect=InvalidPublicIdError("abc")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext abc 新文本")
            matcher = _make_matcher()

            _run_handler(
                handle_edit(bot, event, matcher, args=_make_message("abc 新文本"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "表情包 ID 格式错误" in extract_message_text(msg)

    def test_resolve_timeout_deactivates_without_starting_confirmation(self) -> None:
        """解析公开 ID 等待读锁超时应清理会话且不启动确认超时。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(side_effect=asyncio.TimeoutError),
            ),
            patch("bot.plugins.edit.session_manager.deactivate_chat") as deactivate,
            patch(
                "bot.plugins.edit.timeout_session", new_callable=AsyncMock
            ) as timeout,
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_edit(
                    _make_bot(),
                    _make_event(text="/edittext 1.3 新文本"),
                    matcher,
                    args=_make_message("1.3 新文本"),
                )
            )  # type: ignore[arg-type]

        assert extract_message_text(matcher.finish.await_args[0][0]) == (
            "索引更新较慢，请稍后再试"
        )
        deactivate.assert_called_once()
        timeout.assert_not_called()

    def test_entry_not_found(self) -> None:
        """entry_id 不存在 → 错误消息。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(side_effect=MemeNotFoundError("1.5")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext 5 新文本")
            matcher = _make_matcher()

            _run_handler(
                handle_edit(bot, event, matcher, args=_make_message("5 新文本"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            args, _ = matcher.finish.await_args
            assert "未找到" in extract_message_text(args[0])

    def test_active_session_conflict(self) -> None:
        """已有活跃会话 → 提示 /cancel。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.edit.session_manager.activate_chat",
                return_value=False,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext 5 新文本")
            matcher = _make_matcher()

            _run_handler(
                handle_edit(bot, event, matcher, args=_make_message("5 新文本"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已有命令在处理中，请先 /cancel"

    def test_short_command_extracts_id_and_text(self) -> None:
        """短命令 /e 的参数经 CommandArg 提取后应与 /edittext 一致。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(return_value=_make_entry(text="旧文本")),
            ),
            patch("bot.plugins.edit.session_manager.create_selection"),
            patch("bot.plugins.edit.session_manager.reset_current_task"),
            patch("bot.plugins.edit.timeout_session", new_callable=MagicMock),
            patch("bot.plugins.edit.asyncio.create_task"),
        ):
            bot = _make_bot()
            event = _make_event(text="/e 5 新文本")
            matcher = _make_matcher()

            _run_handler(
                handle_edit(bot, event, matcher, args=_make_message("5 新文本"))
            )  # type: ignore[arg-type]

            assert matcher.state["entry_id"] == 5
            assert matcher.state["new_text"] == "新文本"


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestPublicIdEdit:
    """`/edittext` 公开 ID 迁移测试。"""

    @pytest.mark.asyncio
    async def test_short_id_is_resolved_and_state_keeps_both_ids(self) -> None:
        """普通合集短号应交给共享解析器并保存内部/公开 ID。"""
        entry = MemeEntry(
            id=85,
            image_path="新三国/a.webp",
            text="旧文本",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(return_value=entry),
            ) as mock_resolve,
            patch("bot.plugins.edit.session_manager.create_selection"),
            patch("bot.plugins.edit.session_manager.reset_current_task"),
            patch("bot.plugins.edit.timeout_session", new_callable=AsyncMock),
        ):
            event = _make_event(text="/edittext 003 新文本")
            matcher = _make_matcher()
            await handle_edit(
                _make_bot(), event, matcher, args=_make_message("003 新文本")
            )  # type: ignore[arg-type]

        mock_resolve.assert_awaited_once_with(event, "003")
        assert matcher.state["entry_id"] == 85
        assert matcher.state["public_id"] == MemePublicId(1, 3)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raw_id", "exc", "expected"),
        [
            ("３", InvalidPublicIdError("３"), "表情包 ID 格式错误"),
            ("3", ShortIdUnavailableError("3"), "全部合集模式下"),
        ],
    )
    async def test_domain_error_deactivates_session(
        self, raw_id: str, exc: ValueError, expected: str
    ) -> None:
        """公开 ID 领域错误应提示并立即清理会话。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(side_effect=exc),
            ),
            patch("bot.plugins.edit.session_manager.deactivate_chat") as deactivate,
        ):
            matcher = _make_matcher()
            await handle_edit(
                _make_bot(),
                _make_event(text=f"/edittext {raw_id} 新文本"),
                matcher,
                args=_make_message(f"{raw_id} 新文本"),
            )  # type: ignore[arg-type]

        assert expected in extract_message_text(matcher.finish.await_args[0][0])
        deactivate.assert_called_once()

    @pytest.mark.asyncio
    async def test_domain_error_stays_deactivated_when_finish_stops_handler(
        self,
    ) -> None:
        """测试模式下 finish 抛 FinishedException 时也应先清理会话。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager.activate_chat", return_value=True),
            patch(
                "bot.plugins.edit.resolve_entry_argument",
                new=AsyncMock(side_effect=InvalidPublicIdError("abc")),
            ),
            patch("bot.plugins.edit.session_manager.deactivate_chat") as deactivate,
        ):
            matcher = _make_matcher()
            matcher.finish = AsyncMock(side_effect=FinishedException)

            with pytest.raises(FinishedException):
                await handle_edit(
                    _make_bot(),
                    _make_event(text="/edittext abc 新文本"),
                    matcher,
                    args=_make_message("abc 新文本"),
                )  # type: ignore[arg-type]

        deactivate.assert_called_once()


class TestGotConfirm:
    """got_confirm 处理器测试。"""

    def _setup_matcher(
        self, entry_id: int = 5, new_text: str = "加班到崩溃", old_text: str = "旧文本"
    ) -> MagicMock:
        return _make_matcher(
            state={
                "entry_id": entry_id,
                "public_id": MemePublicId(1, entry_id),
                "new_text": new_text,
                "old_text": old_text,
            }
        )

    @pytest.mark.asyncio
    async def test_confirm_flow(self) -> None:
        """用户回复「确认」→ edit_text 被调用。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager") as mock_sm,
            patch("bot.plugins.edit.get_index_manager") as mock_im,
        ):
            mock_sm.handler_context.return_value.__enter__ = MagicMock()
            mock_sm.handler_context.return_value.__exit__ = MagicMock()

            im = MagicMock()
            im.edit_text = AsyncMock(
                return_value=EditTextResult(
                    entry_id=5, old_text="旧文本", new_text="加班到崩溃"
                ),
            )
            im.add_user_timeout = 60.0
            mock_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = self._setup_matcher()

            await got_confirm(bot, event, matcher, _make_message("确认"))

            im.edit_text.assert_awaited_once_with(5, "加班到崩溃")
            matcher.finish.assert_awaited_once()
            args, _ = matcher.finish.await_args
            assert "OCR 文本已修改" in extract_message_text(args[0])

    @pytest.mark.asyncio
    async def test_cancel_flow(self) -> None:
        """用户回复其他内容 → 回复已取消。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager") as mock_sm,
        ):
            mock_sm.handler_context.return_value.__enter__ = MagicMock()
            mock_sm.handler_context.return_value.__exit__ = MagicMock()

            bot = _make_bot()
            event = _make_event(text="不要")
            matcher = self._setup_matcher()

            await got_confirm(bot, event, matcher, _make_message("不要"))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已取消修改"

    @pytest.mark.asyncio
    async def test_help_bypass(self) -> None:
        """等待确认时 /help → 旁路，不取消。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.got_intercept_bypass") as mock_bypass,
        ):
            # Simulate bypass returning False (handled by bypass, so finish not called)
            async def bypass_side_effect(event, matcher, text, help_text):
                if text == "/help":
                    return True  # handled by bypass
                return False

            mock_bypass.side_effect = bypass_side_effect

            bot = _make_bot()
            event = _make_event(text="/help")
            matcher = _make_matcher(
                state={
                    "entry_id": 5,
                    "public_id": MemePublicId(1, 5),
                    "new_text": "加班到崩溃",
                    "old_text": "旧文本",
                }
            )

            await got_confirm(bot, event, matcher, _make_message("/help"))

            # bypass intercepted /help, so finish should not be called
            assert matcher.finish.call_count == 0

    @pytest.mark.asyncio
    async def test_cancel_bypass(self) -> None:
        """等待确认时 /cancel → 取消，不执行修改。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.got_intercept_bypass") as mock_bypass,
        ):

            async def bypass_side_effect(event, matcher, text, help_text):
                if text == "/cancel":
                    return True  # handled by bypass
                return False

            mock_bypass.side_effect = bypass_side_effect

            bot = _make_bot()
            event = _make_event(text="/cancel")
            matcher = _make_matcher(
                state={
                    "entry_id": 5,
                    "public_id": MemePublicId(1, 5),
                    "new_text": "加班到崩溃",
                    "old_text": "旧文本",
                }
            )

            await got_confirm(bot, event, matcher, _make_message("/cancel"))

            assert matcher.finish.call_count == 0

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """edit_text 超时 → 回复超时消息。"""
        with (
            patch("bot.plugins.edit.is_authorized", return_value=True),
            patch("bot.plugins.edit.session_manager") as mock_sm,
            patch("bot.plugins.edit.get_index_manager") as mock_im,
        ):
            mock_sm.handler_context.return_value.__enter__ = MagicMock()
            mock_sm.handler_context.return_value.__exit__ = MagicMock()
            mock_sm.deactivate_chat = MagicMock()

            im = MagicMock()
            im.edit_text = AsyncMock(side_effect=asyncio.TimeoutError())
            im.add_user_timeout = 60.0
            mock_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(
                state={
                    "entry_id": 5,
                    "public_id": MemePublicId(1, 5),
                    "new_text": "加班到崩溃",
                    "old_text": "旧文本",
                }
            )

            await got_confirm(bot, event, matcher, _make_message("确认"))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "修改处理超时，请稍后再试"
