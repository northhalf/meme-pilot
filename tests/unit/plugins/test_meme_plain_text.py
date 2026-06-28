"""兜底消息插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.keyword_searcher import SearchResult

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_message，
# 避免需要 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_message = MagicMock()
_mock_message.handle.return_value = lambda fn: fn
_mock_message.got.return_value = lambda fn: fn

with patch("nonebot.on_message", return_value=_mock_message):
    from bot.plugins import meme_plain_text
    from bot.plugins.meme_plain_text import handle_plain_text


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "") -> MagicMock:
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


def _reset_mocks() -> None:
    """重置 mock matcher 的 finish 为新的 AsyncMock。"""
    _mock_message.finish = AsyncMock()
    _mock_message.send = AsyncMock()


# ---------------------------------------------------------------------------
# 未知斜杠命令测试
# ---------------------------------------------------------------------------


class TestHandleUnknownSlashCommand:
    """未知斜杠命令测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_plain_text, "is_authorized", return_value=True)
    async def test_unknown_slash_command_replies_unknown(
        self, mock_auth: MagicMock
    ) -> None:
        """授权用户发送未知斜杠命令应回复"未知命令"。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_plain_text(
            _make_bot(), _make_event("111", "/foo"), matcher
        )

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "未知命令" in call_args
        assert "/help" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_plain_text, "is_authorized", return_value=False)
    async def test_unauthorized_slash_command_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户发送未知斜杠命令应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_plain_text(
            bot, _make_event("999", "/foo"), _make_matcher()
        )

        _mock_message.finish.assert_not_called()
        bot.send.assert_not_called()


# ---------------------------------------------------------------------------
# 普通文本走搜索测试
# ---------------------------------------------------------------------------


class TestHandlePlainTextAsSearch:
    """普通文本当作 /search 测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_plain_text, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_plain_text, "activate_chat", return_value=True)
    @patch.object(meme_plain_text, "is_authorized", return_value=True)
    async def test_plain_text_calls_execute_search(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_exec: MagicMock,
    ) -> None:
        """普通文本应调用 execute_search。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_plain_text(
            _make_bot(), _make_event("111", "加班"), matcher
        )

        mock_exec.assert_awaited_once()
        call_args = mock_exec.call_args
        assert call_args[0][3] == "加班"  # keyword 参数

    @pytest.mark.asyncio
    @patch.object(meme_plain_text, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_plain_text, "activate_chat", return_value=False)
    @patch.object(meme_plain_text, "is_authorized", return_value=True)
    async def test_plain_text_with_session_busy(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_exec: MagicMock,
    ) -> None:
        """有活跃会话时应拒绝并提示。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_plain_text(
            _make_bot(), _make_event("111", "加班"), matcher
        )

        matcher.finish.assert_awaited_once()
        assert "已有命令在处理中" in matcher.finish.call_args[0][0]
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(meme_plain_text, "is_authorized", return_value=False)
    async def test_unauthorized_plain_text_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户发送纯文本应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_plain_text(
            bot, _make_event("999", "你好"), _make_matcher()
        )

        _mock_message.finish.assert_not_called()
        bot.send.assert_not_called()
