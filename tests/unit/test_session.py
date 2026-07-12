"""bot.session 会话管理模块测试。"""
# pyright: reportUnusedFunction=false

import asyncio
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.session import (
    ChatScope,
    ChatSession,
    session_manager,
)


def _private_scope(user_id: int = 1001) -> ChatScope:
    """构造私聊作用域。"""
    return ChatScope(user_id=user_id, chat_type="private", chat_id=user_id)


def _group_scope(user_id: int = 1001, group_id: int = 2001) -> ChatScope:
    """构造群聊作用域。"""
    return ChatScope(user_id=user_id, chat_type="group", chat_id=group_id)


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:  # type: ignore[reportUnusedFunction]
    """每个测试前清空会话字典。"""
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()


class TestChatScope:
    """ChatScope 基础行为测试。"""

    def test_hashable_and_immutable(self):
        """frozen + slots 实例可作为字典键且不可变。"""
        scope1 = ChatScope(user_id=1, chat_type="private", chat_id=1)
        scope2 = ChatScope(user_id=1, chat_type="private", chat_id=1)
        mapping = {scope1: "value"}
        assert scope1 == scope2
        assert hash(scope1) == hash(scope2)
        assert mapping[scope2] == "value"
        with pytest.raises(AttributeError):
            scope1.user_id = 2  # type: ignore[misc]

    def test_str(self):
        """__str__ 输出包含类型与 ID 信息。"""
        private_scope = ChatScope(user_id=100, chat_type="private", chat_id=100)
        group_scope = ChatScope(user_id=100, chat_type="group", chat_id=200)
        assert str(private_scope) == "private:100:user:100"
        assert str(group_scope) == "group:200:user:100"

    def test_from_private_event(self):
        """从私聊事件构造 ChatScope。"""
        event = MagicMock()
        event.get_user_id.return_value = "1001"
        event.message_type = "private"
        scope = ChatScope.from_event(event)
        assert scope == ChatScope(user_id=1001, chat_type="private", chat_id=1001)

    def test_from_group_event(self):
        """从群聊事件构造 ChatScope。"""
        event = MagicMock()
        event.get_user_id.return_value = "1001"
        event.message_type = "group"
        event.group_id = "2001"
        scope = ChatScope.from_event(event)
        assert scope == ChatScope(user_id=1001, chat_type="group", chat_id=2001)


class TestGetOrCreateChat:
    """get_or_create_chat 测试。"""

    def test_creates_new(self):
        """首次调用创建新 ChatSession。"""
        scope = _private_scope()
        chat = session_manager.get_or_create_chat(scope)
        assert isinstance(chat, ChatSession)
        assert chat.active is False

    def test_reuses_existing(self):
        """重复调用返回同一实例。"""
        scope = _private_scope()
        chat1 = session_manager.get_or_create_chat(scope)
        chat2 = session_manager.get_or_create_chat(scope)
        assert chat1 is chat2
        assert chat1.session_id == chat2.session_id


class TestActivateChat:
    """activate_chat 测试。"""

    @pytest.mark.asyncio
    async def test_activate_success(self):
        """正常激活返回 True。"""
        scope = _private_scope()
        matcher = MagicMock()
        result = session_manager.activate_chat(scope, "search", matcher)
        assert result is True
        chat = session_manager.get_or_create_chat(scope)
        assert chat.active is True
        assert chat.command_type == "search"
        assert chat.matcher is matcher
        assert chat.current_task is asyncio.current_task()

    @pytest.mark.asyncio
    async def test_activate_fails_when_active(self):
        """已有活跃会话时返回 False。"""
        scope = _private_scope()
        matcher1 = MagicMock()
        matcher2 = MagicMock()
        session_manager.activate_chat(scope, "add", matcher1)
        result = session_manager.activate_chat(scope, "search", matcher2)
        assert result is False
        chat = session_manager.get_or_create_chat(scope)
        assert chat.command_type == "add"  # 不应被覆盖
        assert chat.matcher is matcher1


class TestDeactivateChat:
    """deactivate_chat 测试。"""

    @pytest.mark.asyncio
    async def test_deactivate_active(self):
        """重置活跃会话为空闲。"""
        scope = _private_scope()
        matcher = MagicMock()
        session_manager.activate_chat(scope, "add", matcher)
        session_manager.deactivate_chat(scope)
        chat = session_manager.get_or_create_chat(scope)
        assert chat.active is False
        assert chat.command_type is None
        assert chat.matcher is None
        assert chat.current_task is None

    def test_deactivate_nonexistent(self):
        """对不存在的作用域调用不报错。"""
        session_manager.deactivate_chat(_private_scope(9999))


class TestSelectionSession:
    """create_selection / remove_selection / get_selection 测试。"""

    @pytest.mark.asyncio
    async def test_create_and_get(self):
        """创建选择会话后可查询。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        scope = _private_scope()
        session_manager.create_selection(scope, "sel_001", task)
        ss = session_manager.get_selection(scope)
        assert ss is not None
        assert ss.selection_id == "sel_001"
        assert ss.timeout_task is task

    @pytest.mark.asyncio
    async def test_create_overwrites(self):
        """重复创建覆盖旧选择会话。"""
        loop = asyncio.get_running_loop()
        task1 = loop.create_task(asyncio.sleep(999))
        task2 = loop.create_task(asyncio.sleep(999))
        scope = _private_scope()
        session_manager.create_selection(scope, "sel_001", task1)
        session_manager.create_selection(scope, "sel_002", task2)
        ss = session_manager.get_selection(scope)
        assert ss is not None
        assert ss.selection_id == "sel_002"

    @pytest.mark.asyncio
    async def test_remove_returns_old(self):
        """remove_selection 返回旧会话且从字典中移除。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        scope = _private_scope()
        session_manager.create_selection(scope, "sel_001", task)
        removed = session_manager.remove_selection(scope)
        assert removed is not None
        assert removed.selection_id == "sel_001"
        assert session_manager.get_selection(scope) is None

    def test_get_nonexistent(self):
        """不存在时返回 None。"""
        assert session_manager.get_selection(_private_scope(9999)) is None


class TestExecuteCancel:
    """execute_cancel 测试。"""

    @pytest.mark.asyncio
    async def test_no_active_session(self):
        """无活跃会话时返回 False。"""
        result = await session_manager.execute_cancel(_private_scope())
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_active_chat(self):
        """取消活跃会话返回 True。"""
        scope = _private_scope()
        matcher = AsyncMock()
        session_manager.activate_chat(scope, "add", matcher)
        result = await session_manager.execute_cancel(scope)
        assert result is True
        chat = session_manager.get_or_create_chat(scope)
        assert chat.active is False

    @pytest.mark.asyncio
    async def test_cancel_cleans_up_selection(self):
        """取消时清除选择会话。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        scope = _private_scope()
        matcher = AsyncMock()
        session_manager.activate_chat(scope, "search", matcher)
        session_manager.create_selection(scope, "sel_001", task)
        await session_manager.execute_cancel(scope)
        assert session_manager.get_selection(scope) is None

    @pytest.mark.asyncio
    async def test_cancel_cancels_other_task(self):
        """execute_cancel 会取消当前正在运行的 current_task。"""
        scope = _private_scope()
        matcher = AsyncMock()

        async def long_running() -> None:
            await asyncio.sleep(999)

        other_task = asyncio.create_task(long_running())
        session_manager.activate_chat(scope, "add", matcher)
        session_manager.set_current_task(scope, other_task)

        result = await session_manager.execute_cancel(scope)
        assert result is True
        await asyncio.sleep(0)  # 让取消传播
        assert other_task.cancelled()

        chat = session_manager.get_or_create_chat(scope)
        assert chat.active is False


class TestHandlerContext:
    """handler_context 行为测试。"""

    @pytest.mark.asyncio
    async def test_handler_context_sets_and_resets_task(self):
        """handler_context 进入时更新 current_task/matcher，退出时清理。"""
        scope = _private_scope()
        matcher = MagicMock()
        chat = session_manager.get_or_create_chat(scope)

        with session_manager.handler_context(scope, matcher):
            assert chat.current_task is asyncio.current_task()
            assert chat.matcher is matcher

        assert chat.current_task is None
        # matcher 由 handler_context 交由调用方 / deactivate_chat 清理，此处不做断言


class TestChatScopeIsolation:
    """ChatScope 隔离性测试。"""

    @pytest.mark.asyncio
    async def test_same_user_different_groups(self):
        """同一用户在不同群聊中拥有独立会话。"""
        matcher = MagicMock()
        group_a = _group_scope(group_id=2001)
        group_b = _group_scope(group_id=2002)
        assert session_manager.activate_chat(group_a, "search", matcher) is True
        assert session_manager.activate_chat(group_b, "search", matcher) is True
        chat_a = session_manager.get_or_create_chat(group_a)
        chat_b = session_manager.get_or_create_chat(group_b)
        assert chat_a is not chat_b
        assert chat_a.session_id != chat_b.session_id

    @pytest.mark.asyncio
    async def test_same_user_private_vs_group(self):
        """同一用户的私聊与会话与群聊会话互相隔离。"""
        matcher = MagicMock()
        private_scope = _private_scope()
        group_scope = _group_scope()
        assert session_manager.activate_chat(private_scope, "search", matcher) is True
        assert session_manager.activate_chat(group_scope, "search", matcher) is True
        chat_private = session_manager.get_or_create_chat(private_scope)
        chat_group = session_manager.get_or_create_chat(group_scope)
        assert chat_private is not chat_group
        assert chat_private.session_id != chat_group.session_id

    @pytest.mark.asyncio
    async def test_same_scope_reuses_session(self):
        """同一作用域重复激活视为冲突。"""
        scope = _private_scope()
        matcher = MagicMock()
        assert session_manager.activate_chat(scope, "add", matcher) is True
        assert session_manager.activate_chat(scope, "search", matcher) is False
        chat = session_manager.get_or_create_chat(scope)
        assert chat.command_type == "add"

    @pytest.mark.asyncio
    async def test_selection_isolation_by_scope(self):
        """选择会话按作用域隔离。"""
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.sleep(999))
        private_scope = _private_scope()
        group_scope = _group_scope()
        session_manager.create_selection(private_scope, "sel_private", task)
        session_manager.create_selection(group_scope, "sel_group", task)
        private_selection = session_manager.get_selection(private_scope)
        group_selection = session_manager.get_selection(group_scope)
        assert private_selection is not None
        assert group_selection is not None
        assert private_selection.selection_id == "sel_private"
        assert group_selection.selection_id == "sel_group"


class TestTimeoutSession:
    """timeout_session 工具函数测试。"""

    def test_timeout_session_signature_uses_scope(self):
        """timeout_session 参数为 scope 而不是 user_id。"""
        import inspect

        import bot.session as session_module

        sig = inspect.signature(session_module.timeout_session)
        assert "scope" in sig.parameters
        assert "user_id" not in sig.parameters

    @pytest.mark.asyncio
    async def test_timeout_session_cleans_up_on_match(self):
        """超时匹配后清理选择会话与聊天会话。"""
        import bot.session as session_module

        bot = AsyncMock()
        event = MagicMock()
        scope = _private_scope()

        loop = asyncio.get_running_loop()
        dummy_task = loop.create_task(asyncio.sleep(999))
        matcher = MagicMock()
        session_manager.activate_chat(scope, "search", matcher)
        session_manager.create_selection(scope, "sel_timeout", dummy_task)

        await session_module.timeout_session(
            bot, event, scope, "sel_timeout", "超时", timeout=0
        )

        assert session_manager.get_selection(scope) is None
        chat = session_manager.get_or_create_chat(scope)
        assert chat.active is False
        bot.send.assert_awaited_once_with(event, "超时")
