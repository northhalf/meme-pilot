"""bot.session 会话管理模块测试。"""

from __future__ import annotations

import asyncio
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.session import (
    ChatSession,
    SelectionSession,
    session_manager,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    """每个测试前清空会话字典。"""
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()


class TestGetOrCreateChat:
    """get_or_create_chat 测试。"""

    def test_creates_new(self):
        """首次调用创建新 ChatSession。"""
        chat = session_manager.get_or_create_chat("user1")
        assert isinstance(chat, ChatSession)
        assert chat.active is False

    def test_reuses_existing(self):
        """重复调用返回同一实例。"""
        chat1 = session_manager.get_or_create_chat("user1")
        chat2 = session_manager.get_or_create_chat("user1")
        assert chat1 is chat2
        assert chat1.session_id == chat2.session_id


class TestActivateChat:
    """activate_chat 测试。"""

    @pytest.mark.asyncio
    async def test_activate_success(self):
        """正常激活返回 True。"""
        matcher = MagicMock()
        result = session_manager.activate_chat("user1", "search", matcher)
        assert result is True
        chat = session_manager.get_or_create_chat("user1")
        assert chat.active is True
        assert chat.command_type == "search"
        assert chat.matcher is matcher
        assert chat.current_task is asyncio.current_task()

    @pytest.mark.asyncio
    async def test_activate_fails_when_active(self):
        """已有活跃会话时返回 False。"""
        matcher1 = MagicMock()
        matcher2 = MagicMock()
        session_manager.activate_chat("user1", "add", matcher1)
        result = session_manager.activate_chat("user1", "search", matcher2)
        assert result is False
        chat = session_manager.get_or_create_chat("user1")
        assert chat.command_type == "add"  # 不应被覆盖
        assert chat.matcher is matcher1


class TestDeactivateChat:
    """deactivate_chat 测试。"""

    @pytest.mark.asyncio
    async def test_deactivate_active(self):
        """重置活跃会话为空闲。"""
        matcher = MagicMock()
        session_manager.activate_chat("user1", "add", matcher)
        session_manager.deactivate_chat("user1")
        chat = session_manager.get_or_create_chat("user1")
        assert chat.active is False
        assert chat.command_type is None
        assert chat.matcher is None
        assert chat.current_task is None

    def test_deactivate_nonexistent(self):
        """对不存在的用户调用不报错。"""
        session_manager.deactivate_chat("nonexistent")


class TestSelectionSession:
    """create_selection / remove_selection / get_selection 测试。"""

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        """创建选择会话后可查询。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        session_manager.create_selection("user1", "sel_001", task)
        ss = session_manager.get_selection("user1")
        assert ss is not None
        assert ss.selection_id == "sel_001"
        assert ss.timeout_task is task

    @pytest.mark.asyncio
    async def test_create_overwrites(self):
        """重复创建覆盖旧选择会话。"""
        loop = asyncio.get_running_loop()
        task1 = loop.create_task(asyncio.sleep(999))
        task2 = loop.create_task(asyncio.sleep(999))
        session_manager.create_selection("user1", "sel_001", task1)
        session_manager.create_selection("user1", "sel_002", task2)
        ss = session_manager.get_selection("user1")
        assert ss.selection_id == "sel_002"

    @pytest.mark.asyncio
    async def test_remove_returns_old(self):
        """remove_selection 返回旧会话且从字典中移除。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        session_manager.create_selection("user1", "sel_001", task)
        removed = session_manager.remove_selection("user1")
        assert removed is not None
        assert removed.selection_id == "sel_001"
        assert session_manager.get_selection("user1") is None

    def test_get_nonexistent(self):
        """不存在时返回 None。"""
        assert session_manager.get_selection("no_user") is None


class TestExecuteCancel:
    """execute_cancel 测试。"""

    @pytest.mark.asyncio
    async def test_no_active_session(self):
        """无活跃会话时返回 False。"""
        result = await session_manager.execute_cancel("user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_active_chat(self):
        """取消活跃会话返回 True。"""
        matcher = AsyncMock()
        session_manager.activate_chat("user1", "add", matcher)
        result = await session_manager.execute_cancel("user1")
        assert result is True
        chat = session_manager.get_or_create_chat("user1")
        assert chat.active is False

    @pytest.mark.asyncio
    async def test_cancel_cleans_up_selection(self):
        """取消时清除选择会话。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        matcher = AsyncMock()
        session_manager.activate_chat("user1", "search", matcher)
        session_manager.create_selection("user1", "sel_001", task)
        await session_manager.execute_cancel("user1")
        assert session_manager.get_selection("user1") is None
