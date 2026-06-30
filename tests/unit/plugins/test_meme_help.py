"""/help 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，
# 避免需要 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_help
    from bot.plugins.meme_help import handle_help


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = "private"
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _reset_mocks() -> None:
    """重置 mock matcher 的 finish 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()


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
