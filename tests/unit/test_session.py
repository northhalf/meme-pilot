"""bot.session 会话管理模块测试。"""

from __future__ import annotations

from typing import Any, Generator
from unittest.mock import MagicMock

import pytest

from bot.session import (
    PendingSession,
    cancel,
    check_and_cancel,
    is_cancelled,
    register,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    """每个测试前清空会话字典。"""
    from bot.session import pending_sessions

    pending_sessions.clear()
    yield
    pending_sessions.clear()


class TestPendingSession:
    """PendingSession 数据类测试。"""

    def test_create_defaults(self) -> None:
        """默认值：cancelled=False, type='add'。"""
        matcher = MagicMock()
        s = PendingSession(matcher=matcher)
        assert s.cancelled is False
        assert s.type == "add"

    def test_create_custom(self) -> None:
        """自定义字段值。"""
        matcher = MagicMock()
        s = PendingSession(matcher=matcher, cancelled=True, type="search")
        assert s.cancelled is True
        assert s.type == "search"


class TestCheckAndCancel:
    """check_and_cancel 函数测试。"""

    def test_no_existing_session(self) -> None:
        """无旧会话时返回 None。"""
        result = check_and_cancel("user1", "add")
        assert result is None

    def test_cancels_existing_session(self) -> None:
        """有旧会话时标记取消并返回提示。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        result = check_and_cancel("user1", "add")
        assert result is not None
        assert "已取消" in result
        assert is_cancelled("user1") is True

    def test_cross_command_cancel(self) -> None:
        """不同类型命令也能取消旧会话。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        result = check_and_cancel("user1", "search")
        assert result is not None


class TestRegister:
    """register 函数测试。"""

    def test_registers_session(self) -> None:
        """注册后会话存在。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        from bot.session import pending_sessions

        assert "user1" in pending_sessions
        assert pending_sessions["user1"].type == "add"

    def test_overwrites_existing(self) -> None:
        """重复注册覆盖旧会话。"""
        old_matcher = MagicMock()
        new_matcher = MagicMock()
        register("user1", old_matcher, "add")
        register("user1", new_matcher, "search")
        from bot.session import pending_sessions

        assert pending_sessions["user1"].matcher is new_matcher
        assert pending_sessions["user1"].type == "search"


class TestCancel:
    """cancel 函数测试。"""

    def test_removes_session(self) -> None:
        """cancel 后会话被移除。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        cancel("user1")
        from bot.session import pending_sessions

        assert "user1" not in pending_sessions

    def test_cancel_nonexistent(self) -> None:
        """取消不存在的会话不报错。"""
        cancel("nonexistent")


class TestIsCancelled:
    """is_cancelled 函数测试。"""

    def test_no_session(self) -> None:
        """无会话时返回 False。"""
        assert is_cancelled("user1") is False

    def test_active_session(self) -> None:
        """活跃会话返回 False。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        assert is_cancelled("user1") is False

    def test_cancelled_session(self) -> None:
        """已取消会话返回 True。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        check_and_cancel("user1", "search")
        assert is_cancelled("user1") is True
