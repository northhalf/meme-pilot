"""SessionManager 单元测试。"""

import pytest

from bot.session import ChatSession, session_manager


@pytest.fixture(autouse=True)
def _clear_sessions():
    """每个测试前后清空全局 SessionManager 的内部状态。"""
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()


def test_has_active_session_initially_false():
    """初始状态下不存在任何活跃会话。"""
    assert session_manager.has_active_session() is False


def test_has_active_session_true_after_activate():
    """激活一个会话后，has_active_session 应返回 True。"""
    chat = ChatSession(session_id="test-id")
    chat.active = True
    session_manager._chat_sessions["user_1"] = chat
    assert session_manager.has_active_session() is True


def test_has_active_session_false_after_deactivate():
    """去激活后，has_active_session 应恢复为 False。"""
    chat = ChatSession(session_id="test-id")
    chat.active = True
    session_manager._chat_sessions["user_1"] = chat

    session_manager.deactivate_chat("user_1")
    assert session_manager.has_active_session() is False
