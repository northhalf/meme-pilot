"""/edittext 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化
_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.meme_edit import (
        got_confirm,
        handle_edit,
    )

from bot.engine.index_manager import (
    EditTextResult,
)


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
    entry.image_path = image_path
    entry.text = text
    return entry


# ---------------------------------------------------------------------------
# handle_edit 测试
# ---------------------------------------------------------------------------


class TestHandleEdit:
    """handle_edit 入口函数测试。"""

    def test_unauthorized(self) -> None:
        """非授权用户 → 静默忽略。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=False),
            patch("bot.plugins.meme_edit.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 0
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_edit.session_manager.activate_chat", return_value=True
            ),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("此命令仅限私聊使用")

    def test_invalid_args_no_text(self) -> None:
        """参数不足 → 用法提示。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_edit.session_manager.activate_chat", return_value=True
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext")
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            args, _ = matcher.finish.await_args
            assert "用法" in args[0]

    def test_invalid_args_not_number(self) -> None:
        """entry_id 非数字 → 用法提示。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_edit.session_manager.activate_chat", return_value=True
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext abc 新文本")
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("entry_id 必须为数字")

    def test_entry_not_found(self) -> None:
        """entry_id 不存在 → 错误消息。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_edit.session_manager.activate_chat", return_value=True
            ),
            patch("bot.plugins.meme_edit.get_metadata_store") as mock_store,
        ):
            store = MagicMock()
            store.get_entry.return_value = None
            mock_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/edittext 5 新文本")
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            args, _ = matcher.finish.await_args
            assert "未找到" in args[0]

    def test_active_session_conflict(self) -> None:
        """已有活跃会话 → 提示 /cancel。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_edit.session_manager.activate_chat",
                return_value=False,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/edittext 5 新文本")
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("已有命令在处理中，请先 /cancel")


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestGotConfirm:
    """got_confirm 处理器测试。"""

    def _setup_matcher(
        self, entry_id: int = 5, new_text: str = "加班到崩溃", old_text: str = "旧文本"
    ) -> MagicMock:
        return _make_matcher(
            state={
                "entry_id": entry_id,
                "new_text": new_text,
                "old_text": old_text,
            }
        )

    @pytest.mark.anyio
    async def test_confirm_flow(self) -> None:
        """用户回复「确认」→ edit_text 被调用。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch("bot.plugins.meme_edit.session_manager") as mock_sm,
            patch("bot.plugins.meme_edit.get_index_manager") as mock_im,
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

            await got_confirm(bot, event, matcher, "确认")

            im.edit_text.assert_awaited_once_with(5, "加班到崩溃")
            matcher.finish.assert_awaited_once()
            args, _ = matcher.finish.await_args
            assert "OCR 文本已修改" in args[0]

    @pytest.mark.anyio
    async def test_cancel_flow(self) -> None:
        """用户回复其他内容 → 回复已取消。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch("bot.plugins.meme_edit.session_manager") as mock_sm,
        ):
            mock_sm.handler_context.return_value.__enter__ = MagicMock()
            mock_sm.handler_context.return_value.__exit__ = MagicMock()

            bot = _make_bot()
            event = _make_event(text="不要")
            matcher = self._setup_matcher()

            await got_confirm(bot, event, matcher, "不要")

            matcher.finish.assert_awaited_once_with("已取消修改")

    @pytest.mark.anyio
    async def test_help_bypass(self) -> None:
        """等待确认时 /help → 旁路，不取消。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch("bot.plugins.meme_edit.got_intercept_bypass") as mock_bypass,
        ):
            # Simulate bypass returning False (handled by bypass, so finish not called)
            async def bypass_side_effect(uid, matcher, text, help_text):
                if text == "/help":
                    return True  # handled by bypass
                return False

            mock_bypass.side_effect = bypass_side_effect

            bot = _make_bot()
            event = _make_event(text="/help")
            matcher = _make_matcher(
                state={
                    "entry_id": 5,
                    "new_text": "加班到崩溃",
                    "old_text": "旧文本",
                }
            )

            await got_confirm(bot, event, matcher, "/help")

            # bypass intercepted /help, so finish should not be called
            assert matcher.finish.call_count == 0

    @pytest.mark.anyio
    async def test_cancel_bypass(self) -> None:
        """等待确认时 /cancel → 取消，不执行修改。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch("bot.plugins.meme_edit.got_intercept_bypass") as mock_bypass,
        ):

            async def bypass_side_effect(uid, matcher, text, help_text):
                if text == "/cancel":
                    return True  # handled by bypass
                return False

            mock_bypass.side_effect = bypass_side_effect

            bot = _make_bot()
            event = _make_event(text="/cancel")
            matcher = _make_matcher(
                state={
                    "entry_id": 5,
                    "new_text": "加班到崩溃",
                    "old_text": "旧文本",
                }
            )

            await got_confirm(bot, event, matcher, "/cancel")

            assert matcher.finish.call_count == 0

    @pytest.mark.anyio
    async def test_timeout_handling(self) -> None:
        """edit_text 超时 → 回复超时消息。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch("bot.plugins.meme_edit.session_manager") as mock_sm,
            patch("bot.plugins.meme_edit.get_index_manager") as mock_im,
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
                    "new_text": "加班到崩溃",
                    "old_text": "旧文本",
                }
            )

            await got_confirm(bot, event, matcher, "确认")

            matcher.finish.assert_awaited_once_with("修改处理超时，请稍后再试")
