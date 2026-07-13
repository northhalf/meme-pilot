"""cancel 插件测试。"""

from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.session import ChatScope, session_manager
from tests.conftest import extract_message_text


# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins.cancel import handle_cancel


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()


def _make_event(user_id: str = "1001") -> MagicMock:
    """创建模拟的私聊消息事件。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = "private"
    return event


def _make_scope(user_id: str = "1001") -> ChatScope:
    """构造私聊 ChatScope。"""
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))


class TestCancelCommand:
    """/cancel 命令测试。"""

    @pytest.mark.asyncio
    async def test_cancel_with_active_session(self) -> None:
        """有活跃会话时取消成功。"""
        matcher = AsyncMock()
        scope = _make_scope("1001")
        session_manager.activate_chat(scope, "add", matcher)

        bot = AsyncMock()
        event = _make_event("1001")

        with patch("bot.plugins.cancel.is_authorized", return_value=True):
            await handle_cancel(bot, event, matcher)

        # 验证会话已取消
        chat = session_manager._chat_sessions.get(scope)
        assert chat is None or chat.active is False

    @pytest.mark.asyncio
    async def test_cancel_without_active_session(self) -> None:
        """无活跃会话时提示。"""
        matcher = AsyncMock()
        bot = AsyncMock()
        event = _make_event("1001")

        with patch("bot.plugins.cancel.is_authorized", return_value=True):
            await handle_cancel(bot, event, matcher)

        # 应调用 matcher.finish 且内容包含"没有活跃"
        msg = matcher.finish.await_args[0][0]
        assert "没有活跃" in extract_message_text(msg)

    @pytest.mark.asyncio
    async def test_cancel_without_active_session_in_group(self) -> None:
        """群聊中无活跃会话时提示应包含 reply segment。"""
        matcher = AsyncMock()
        bot = AsyncMock()
        event = _make_event("1001")
        event.message_type = "group"
        event.message_id = 123456

        with patch("bot.plugins.cancel.is_authorized", return_value=True):
            await handle_cancel(bot, event, matcher)

        msg = matcher.finish.await_args[0][0]
        assert "没有活跃" in extract_message_text(msg)
        if isinstance(msg, Message):
            assert msg[0].type == "reply"

    @pytest.mark.asyncio
    async def test_unauthorized(self) -> None:
        """未授权用户发送 /cancel 被静默忽略，不调用 execute_cancel。"""
        matcher = AsyncMock()
        bot = AsyncMock()
        event = _make_event("9999")

        with patch("bot.plugins.cancel.is_authorized", return_value=False), \
             patch.object(session_manager, "execute_cancel", new=AsyncMock()) as mock_exec:
            await handle_cancel(bot, event, matcher)

        # 非授权用户静默忽略：finish(None) 且不触发 execute_cancel
        matcher.finish.assert_awaited_once_with(None)
        mock_exec.assert_not_awaited()
