"""_search_utils ChatScope 与回复行为测试。"""
# pyright: reportUnusedVariable=false

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.engine.types import SearchResult
from bot.plugins._search_utils import present_candidates
from bot.session import ChatScope


from tests.conftest import extract_message_text


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=90.0,
        speaker=None,
        tags=[],
    )


def _make_event(
    user_id: str = "12345",
    message_id: int | None = 42,
    message_type: str = "private",
    group_id: int | None = None,
) -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_id = message_id
    event.message_type = message_type
    event.group_id = group_id
    return event


def _make_matcher() -> MagicMock:
    matcher = MagicMock()
    matcher.state = {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


class TestPresentCandidatesWithScope:
    """present_candidates 结合 ChatScope 的展示行为测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_private_scope_sends_plain_text(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """私聊作用域发送纯文本列表。"""
        event = _make_event(message_id=42, message_type="private")
        matcher = _make_matcher()

        await present_candidates(
            _make_bot(), event, matcher, [_make_search_result()]
        )

        matcher.send.assert_awaited_once()
        sent = matcher.send.call_args[0][0]
        assert isinstance(sent, str)
        assert "找到多个匹配的表情包" in extract_message_text(sent)

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_group_scope_with_reply_sends_message(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """群聊作用域默认发送带 reply 的 Message。"""
        event = _make_event(
            message_id=42, message_type="group", group_id=67890
        )
        matcher = _make_matcher()

        await present_candidates(
            _make_bot(),
            event,
            matcher,
            [_make_search_result()],
        )

        matcher.send.assert_awaited_once()
        sent = matcher.send.call_args[0][0]
        assert isinstance(sent, Message)
        assert sent[0].type == "reply"
        assert sent[0].data["id"] == "42"
        assert "找到多个匹配的表情包" in extract_message_text(sent)

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_group_scope_without_reply_sends_plain_text(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """群聊作用域但 message_id 为 None 时退化为纯文本。"""
        event = _make_event(
            message_id=None, message_type="group", group_id=67890
        )
        matcher = _make_matcher()

        await present_candidates(
            _make_bot(), event, matcher, [_make_search_result()]
        )

        matcher.send.assert_awaited_once()
        sent = matcher.send.call_args[0][0]
        assert isinstance(sent, str)

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_use_reject_in_group_wraps_message(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """群聊 use_reject=True 时同样用 Message 包裹并调用 reject。"""
        event = _make_event(
            message_id=42, message_type="group", group_id=67890
        )
        matcher = _make_matcher()

        await present_candidates(
            _make_bot(),
            event,
            matcher,
            [_make_search_result()],
            use_reject=True,
        )

        matcher.reject.assert_awaited_once()
        matcher.send.assert_not_awaited()
        sent = matcher.reject.call_args[0][0]
        assert isinstance(sent, Message)
        assert sent[0].type == "reply"
        assert sent[0].data["id"] == "42"
        assert sent[1].type == "text"
        assert "找到多个匹配的表情包" in extract_message_text(sent)


class TestChatScopeFromEvent:
    """ChatScope.from_event 构造测试。"""

    def test_private_event(self) -> None:
        """私聊事件构造 private ChatScope。"""
        event = _make_event("12345", message_type="private")
        scope = ChatScope.from_event(event)
        assert scope.chat_type == "private"
        assert scope.user_id == 12345
        assert scope.chat_id == 12345

    def test_group_event(self) -> None:
        """群聊事件构造 group ChatScope。"""
        event = _make_event(
            "12345", message_type="group", group_id=67890
        )
        scope = ChatScope.from_event(event)
        assert scope.chat_type == "group"
        assert scope.user_id == 12345
        assert scope.chat_id == 67890

    def test_group_event_missing_group_id_raises(self) -> None:
        """群聊事件缺少 group_id 时抛出 ValueError。"""
        event = _make_event("12345", message_type="group")
        event.group_id = None
        with pytest.raises(ValueError):
            ChatScope.from_event(event)
