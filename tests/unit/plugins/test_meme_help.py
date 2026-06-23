"""/help 命令插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command / nonebot.on_message，
# 避免需要 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

_mock_message = MagicMock()
_mock_message.handle.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.on_message", return_value=_mock_message),
):
    from bot.plugins import meme_help
    from bot.plugins.meme_help import handle_help, handle_plain_text


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "") -> MagicMock:
    """创建模拟的 PrivateMessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _reset_mocks() -> None:
    """重置 mock matcher 的 finish 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()
    _mock_message.finish = AsyncMock()


# ---------------------------------------------------------------------------
# /help 命令测试
# ---------------------------------------------------------------------------


class TestHandleHelp:
    """/help 命令测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_authorized_user_receives_help(
        self, mock_auth: MagicMock
    ) -> None:
        """授权用户应收到帮助文本。"""
        _reset_mocks()

        await handle_help(_make_bot(), _make_event("111"))

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "/help" in call_args
        assert "/search" in call_args
        assert "/ai" in call_args
        assert "/add" in call_args
        assert "/refresh" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_help(bot, _make_event("999"))

        _mock_cmd.finish.assert_not_called()
        bot.send.assert_not_called()


# ---------------------------------------------------------------------------
# 兜底处理测试
# ---------------------------------------------------------------------------


class TestHandlePlainText:
    """兜底消息处理测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_plain_text_replies_help(
        self, mock_auth: MagicMock
    ) -> None:
        """授权用户发送纯文本应回复帮助摘要。"""
        _reset_mocks()

        await handle_plain_text(_make_bot(), _make_event("111", "你好"))

        _mock_message.finish.assert_awaited_once()
        call_args = _mock_message.finish.call_args[0][0]
        assert "/help" in call_args
        assert "未知命令" not in call_args

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_unknown_slash_command_replies_unknown(
        self, mock_auth: MagicMock
    ) -> None:
        """授权用户发送未知斜杠命令应回复"未知命令"。"""
        _reset_mocks()

        await handle_plain_text(_make_bot(), _make_event("111", "/foo"))

        _mock_message.finish.assert_awaited_once()
        call_args = _mock_message.finish.call_args[0][0]
        assert "未知命令" in call_args
        assert "/help" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=False)
    async def test_unauthorized_plain_text_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户发送纯文本应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_plain_text(bot, _make_event("999", "你好"))

        _mock_message.finish.assert_not_called()
        bot.send.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=False)
    async def test_unauthorized_slash_command_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户发送未知斜杠命令应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_plain_text(bot, _make_event("999", "/foo"))

        _mock_message.finish.assert_not_called()
        bot.send.assert_not_called()
