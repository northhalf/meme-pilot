"""/switch 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException

from bot.engine.collection_manager import CollectionNotFoundError
from bot.engine.types import CollectionSelection, CollectionSummary
from bot.session import ChatScope
from tests.conftest import _assert_has_reply, _assert_no_reply, extract_message_text

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda function: function
_mock_rule = MagicMock()

with (
    patch("nonebot.on_command", return_value=_mock_cmd) as _on_command,
    patch("nonebot.rule.to_me", return_value=_mock_rule),
):
    from bot.plugins import switch
    from bot.plugins.switch import handle_switch


def _make_event(user_id: str = "12345", message_type: str = "private") -> MagicMock:
    """创建模拟的消息事件。"""
    event = MagicMock()
    event.message_type = message_type
    event.get_user_id.return_value = user_id
    if message_type == "group":
        event.group_id = 98765
        event.message_id = 123456
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    return MagicMock()


def _make_matcher(*, finish_side_effect: Exception | None = None) -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.finish = AsyncMock(side_effect=finish_side_effect)
    return matcher


def _make_args(text: str = "") -> Message:
    """创建命令参数消息。"""
    return Message(text)


def _make_index_manager() -> MagicMock:
    """创建模拟的 IndexManager。"""
    manager = MagicMock()
    manager.list_collections = AsyncMock()
    manager.switch_collection = AsyncMock()
    return manager


def _scope(event: MagicMock) -> ChatScope:
    """返回事件对应的聊天作用域。"""
    return ChatScope.from_event(event)


def test_switch_matcher_registration() -> None:
    """命令注册应使用统一优先级、阻断、to_me 规则与空白边界。"""
    _on_command.assert_called_once_with(
        "switch", rule=_mock_rule, priority=5, block=True, force_whitespace=True
    )


class TestSwitchList:
    """无参数合集列表测试。"""

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_lists_counts_and_current_collection(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """列表应区分全库总数、普通数量和当前合集。"""
        event = _make_event()
        matcher = _make_matcher()
        manager = _make_index_manager()
        manager.list_collections.return_value = [
            CollectionSummary(0, "全部合集", 10, True),
            CollectionSummary(1, "新三国", 4, False),
            CollectionSummary(2, "空合集", 0, False),
        ]
        mock_get_manager.return_value = manager

        await handle_switch(_make_bot(), event, matcher, _make_args())

        manager.list_collections.assert_awaited_once_with(_scope(event))
        text = extract_message_text(matcher.finish.await_args.args[0])
        assert text == (
            "表情包合集：\n"
            "* 0. 全部合集（共 10 张）\n"
            "  1. 新三国（4 张）\n"
            "  2. 空合集（0 张）\n\n"
            "当前合集：全部合集\n"
            "使用 /switch <编号|名称> 切换"
        )
        _assert_no_reply(matcher.finish.await_args.args[0])
        mock_deactivate.assert_called_once_with(_scope(event))

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_marks_current_regular_collection(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """当前普通合集行应带星号并显示为当前合集。"""
        manager = _make_index_manager()
        manager.list_collections.return_value = [
            CollectionSummary(0, "全部合集", 10, False),
            CollectionSummary(1, "新三国", 4, True),
        ]
        mock_get_manager.return_value = manager
        matcher = _make_matcher()

        await handle_switch(_make_bot(), _make_event(), matcher, _make_args("  "))

        text = extract_message_text(matcher.finish.await_args.args[0])
        assert "  0. 全部合集（共 10 张）" in text
        assert "* 1. 新三国（4 张）" in text
        assert "当前合集：新三国" in text

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_empty_summary_list_is_defensive(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """引擎异常返回空列表时不应崩溃。"""
        manager = _make_index_manager()
        manager.list_collections.return_value = []
        mock_get_manager.return_value = manager
        matcher = _make_matcher()

        await handle_switch(_make_bot(), _make_event(), matcher, _make_args())

        assert extract_message_text(matcher.finish.await_args.args[0]) == (
            "表情包合集：\n暂无可用合集\n\n"
            "当前合集：全部合集\n"
            "使用 /switch <编号|名称> 切换"
        )


class TestSwitchTarget:
    """带参数切换测试。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["001", "合集 名称"])
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_passes_complete_target_to_index_manager(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
        target: str,
    ) -> None:
        """编号和含空格名称均应完整交给 IndexManager 解析。"""
        event = _make_event()
        manager = _make_index_manager()
        manager.switch_collection.return_value = CollectionSelection(1, "新三国")
        mock_get_manager.return_value = manager

        await handle_switch(_make_bot(), event, _make_matcher(), _make_args(target))

        manager.switch_collection.assert_awaited_once_with(_scope(event), target)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("selection", "expected"),
        [
            (CollectionSelection(0, "全部合集"), "已切换到：全部合集（0）"),
            (CollectionSelection(2, "甄嬛传"), "已切换到合集：甄嬛传（2）"),
        ],
    )
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_reports_successful_selection(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
        selection: CollectionSelection,
        expected: str,
    ) -> None:
        """切换全部合集与普通合集应使用各自文案。"""
        manager = _make_index_manager()
        manager.switch_collection.return_value = selection
        mock_get_manager.return_value = manager
        matcher = _make_matcher()

        await handle_switch(_make_bot(), _make_event(), matcher, _make_args("2"))

        assert extract_message_text(matcher.finish.await_args.args[0]) == expected

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_group_chat_uses_group_scope_and_reply(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """群聊应使用群作用域并引用原消息。"""
        event = _make_event(message_type="group")
        manager = _make_index_manager()
        manager.switch_collection.return_value = CollectionSelection(1, "新三国")
        mock_get_manager.return_value = manager
        matcher = _make_matcher()

        await handle_switch(_make_bot(), event, matcher, _make_args("新三国"))

        expected_scope = ChatScope(12345, "group", 98765)
        mock_activate.assert_called_once_with(expected_scope, "switch", matcher)
        manager.switch_collection.assert_awaited_once_with(expected_scope, "新三国")
        _assert_has_reply(matcher.finish.await_args.args[0], 123456)


class TestSwitchGuardsAndErrors:
    """授权、会话互斥和错误处理测试。"""

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "activate_chat")
    @patch.object(switch, "log_unauthorized")
    @patch.object(switch, "is_authorized", return_value=False)
    async def test_unauthorized_is_silent_and_does_not_activate(
        self,
        mock_auth: MagicMock,
        mock_log: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """未授权用户应静默结束且不激活会话。"""
        matcher = _make_matcher()

        await handle_switch(_make_bot(), _make_event("999"), matcher, _make_args("1"))

        matcher.finish.assert_awaited_once_with(None)
        mock_log.assert_called_once_with("999", "switch")
        mock_activate.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=False)
    @patch.object(switch, "is_authorized", return_value=True)
    async def test_rejects_active_session_without_deactivating_it(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """已有命令时应提示先取消，且不能清理原会话。"""
        matcher = _make_matcher()

        await handle_switch(_make_bot(), _make_event(), matcher, _make_args("1"))

        assert "请先 /cancel" in extract_message_text(matcher.finish.await_args.args[0])
        mock_deactivate.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    async def test_argument_extraction_error_still_deactivates(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """会话激活后即使参数解析异常也必须清理会话。"""
        event = _make_event()
        args = MagicMock()
        args.extract_plain_text.side_effect = RuntimeError("解析失败")

        with pytest.raises(RuntimeError, match="解析失败"):
            await handle_switch(_make_bot(), event, _make_matcher(), args)

        mock_deactivate.assert_called_once_with(_scope(event))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("side_effect", "expected"),
        [
            (
                CollectionNotFoundError("不存在"),
                "未找到表情包合集：不存在\n发送 /switch 查看可用合集",
            ),
            (asyncio.TimeoutError(), "索引更新较慢，请稍后再试"),
        ],
    )
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_expected_errors_reply_and_deactivate(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
        side_effect: Exception,
        expected: str,
    ) -> None:
        """合集不存在和读锁超时应统一转换为用户提示并清理会话。"""
        event = _make_event()
        manager = _make_index_manager()
        manager.switch_collection.side_effect = side_effect
        mock_get_manager.return_value = manager
        matcher = _make_matcher()

        await handle_switch(_make_bot(), event, matcher, _make_args("不存在"))

        assert extract_message_text(matcher.finish.await_args.args[0]) == expected
        mock_deactivate.assert_called_once_with(_scope(event))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "manager_side_effect",
        [None, CollectionNotFoundError("不存在"), asyncio.TimeoutError()],
    )
    @patch.object(switch.session_manager, "deactivate_chat")
    @patch.object(switch.session_manager, "activate_chat", return_value=True)
    @patch.object(switch, "is_authorized", return_value=True)
    @patch.object(switch, "get_index_manager")
    async def test_finished_exception_still_deactivates(
        self,
        mock_get_manager: MagicMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
        manager_side_effect: Exception | None,
    ) -> None:
        """无论成功或错误回复，Matcher.finish 抛出时都必须清理会话。"""
        event = _make_event()
        manager = _make_index_manager()
        manager.switch_collection.return_value = CollectionSelection(1, "新三国")
        manager.switch_collection.side_effect = manager_side_effect
        mock_get_manager.return_value = manager
        matcher = _make_matcher(finish_side_effect=FinishedException())

        with pytest.raises(FinishedException):
            await handle_switch(_make_bot(), event, matcher, _make_args("不存在"))

        mock_deactivate.assert_called_once_with(_scope(event))
