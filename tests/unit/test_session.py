"""bot.session 会话管理模块测试。"""

from __future__ import annotations

import asyncio
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.session import (
    ChatSession,
    SelectionSession,
    activate_chat,
    chat_sessions,
    create_selection,
    deactivate_chat,
    execute_cancel,
    get_or_create_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
    selection_sessions,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    """每个测试前清空会话字典。"""
    chat_sessions.clear()
    selection_sessions.clear()
    yield
    chat_sessions.clear()
    selection_sessions.clear()


class TestGetOrCreateChat:
    """get_or_create_chat 测试。"""

    def test_creates_new(self):
        """首次调用创建新 ChatSession。"""
        chat = get_or_create_chat("user1")
        assert isinstance(chat, ChatSession)
        assert chat.active is False

    def test_reuses_existing(self):
        """重复调用返回同一实例。"""
        chat1 = get_or_create_chat("user1")
        chat2 = get_or_create_chat("user1")
        assert chat1 is chat2
        assert chat1.session_id == chat2.session_id


class TestActivateChat:
    """activate_chat 测试。"""

    @pytest.mark.asyncio
    async def test_activate_success(self):
        """正常激活返回 True。"""
        matcher = MagicMock()
        result = activate_chat("user1", "search", matcher)
        assert result is True
        chat = get_or_create_chat("user1")
        assert chat.active is True
        assert chat.command_type == "search"
        assert chat.matcher is matcher
        assert chat.current_task is asyncio.current_task()

    @pytest.mark.asyncio
    async def test_activate_fails_when_active(self):
        """已有活跃会话时返回 False。"""
        matcher1 = MagicMock()
        matcher2 = MagicMock()
        activate_chat("user1", "add", matcher1)
        result = activate_chat("user1", "search", matcher2)
        assert result is False
        chat = get_or_create_chat("user1")
        assert chat.command_type == "add"  # 不应被覆盖
        assert chat.matcher is matcher1


class TestDeactivateChat:
    """deactivate_chat 测试。"""

    @pytest.mark.asyncio
    async def test_deactivate_active(self):
        """重置活跃会话为空闲。"""
        matcher = MagicMock()
        activate_chat("user1", "add", matcher)
        deactivate_chat("user1")
        chat = get_or_create_chat("user1")
        assert chat.active is False
        assert chat.command_type is None
        assert chat.matcher is None
        assert chat.current_task is None

    def test_deactivate_nonexistent(self):
        """对不存在的用户调用不报错。"""
        deactivate_chat("nonexistent")


class TestSelectionSession:
    """create_selection / remove_selection / get_selection 测试。"""

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        """创建选择会话后可查询。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        create_selection("user1", "sel_001", task)
        ss = get_selection("user1")
        assert ss is not None
        assert ss.selection_id == "sel_001"
        assert ss.timeout_task is task

    @pytest.mark.asyncio
    async def test_create_overwrites(self):
        """重复创建覆盖旧选择会话。"""
        loop = asyncio.get_running_loop()
        task1 = loop.create_task(asyncio.sleep(999))
        task2 = loop.create_task(asyncio.sleep(999))
        create_selection("user1", "sel_001", task1)
        create_selection("user1", "sel_002", task2)
        ss = get_selection("user1")
        assert ss.selection_id == "sel_002"

    @pytest.mark.asyncio
    async def test_remove_returns_old(self):
        """remove_selection 返回旧会话且从字典中移除。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        create_selection("user1", "sel_001", task)
        removed = remove_selection("user1")
        assert removed is not None
        assert removed.selection_id == "sel_001"
        assert get_selection("user1") is None

    def test_get_nonexistent(self):
        """不存在时返回 None。"""
        assert get_selection("no_user") is None


class TestExecuteCancel:
    """execute_cancel 测试。"""

    @pytest.mark.asyncio
    async def test_no_active_session(self):
        """无活跃会话时返回 False。"""
        result = await execute_cancel("user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_active_chat(self):
        """取消活跃会话返回 True。"""
        matcher = AsyncMock()
        activate_chat("user1", "add", matcher)
        result = await execute_cancel("user1")
        assert result is True
        chat = get_or_create_chat("user1")
        assert chat.active is False

    @pytest.mark.asyncio
    async def test_cancel_cleans_up_selection(self):
        """取消时清除选择会话。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        matcher = AsyncMock()
        activate_chat("user1", "search", matcher)
        create_selection("user1", "sel_001", task)
        await execute_cancel("user1")
        assert get_selection("user1") is None


class TestGotInterceptBypass:
    """got_intercept_bypass 测试。"""

    @pytest.mark.asyncio
    async def test_normal_text_returns_false(self):
        """普通文本返回 False。"""
        matcher = AsyncMock()
        result = await got_intercept_bypass(
            "user1", matcher, "hello", "帮助文本"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_help_returns_true(self):
        """/help 拦截后返回 True。"""
        matcher = AsyncMock()
        result = await got_intercept_bypass(
            "user1", matcher, "/help", "帮助文本"
        )
        assert result is True
        matcher.send.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_returns_true(self):
        """/cancel 拦截后返回 True。"""
        matcher = AsyncMock()
        activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass(
            "user1", matcher, "/cancel", "帮助文本"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_help_with_args_matches(self):
        """/help xxx（带参数）也匹配帮助。"""
        matcher = AsyncMock()
        result = await got_intercept_bypass(
            "user1", matcher, "/help 加班", "帮助文本"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_with_args_matches(self):
        """/cancel xxx（带参数）也匹配取消。"""
        matcher = AsyncMock()
        activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass(
            "user1", matcher, "/cancel something", "帮助文本"
        )
        assert result is True
