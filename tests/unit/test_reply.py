"""bot/reply.py 单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.reply import bot_send, build_reply_text, finish, reject, send


def _make_event(
    *,
    message_type: str = "private",
    message_id: int | None = 42,
) -> MagicMock:
    event = MagicMock()
    event.message_type = message_type
    event.message_id = message_id
    return event


class TestBuildReplyText:
    """build_reply_text 行为测试。"""

    def test_private_chat_returns_plain_text(self) -> None:
        """私聊返回原字符串。"""
        event = _make_event(message_type="private", message_id=42)
        result = build_reply_text(event, "hello")
        assert result == "hello"
        assert isinstance(result, str)

    def test_group_chat_with_message_id_returns_message(self) -> None:
        """群聊且 message_id 存在时返回带 reply 的 Message。"""
        event = _make_event(message_type="group", message_id=123)
        result = build_reply_text(event, "hello")
        assert isinstance(result, Message)
        assert len(result) == 2
        assert result[0].type == "reply"
        assert result[0].data["id"] == "123"
        assert result[1].type == "text"
        assert result[1].data["text"] == "hello"

    def test_group_chat_without_message_id_returns_plain_text(self) -> None:
        """群聊但 message_id 缺失时退化为纯文本。"""
        event = _make_event(message_type="group", message_id=None)
        result = build_reply_text(event, "hello")
        assert result == "hello"
        assert isinstance(result, str)

    def test_message_type_missing_returns_plain_text(self) -> None:
        """message_type 属性缺失时退化为纯文本。"""
        event = MagicMock(spec=[])
        result = build_reply_text(event, "hello")
        assert result == "hello"


class TestReplySendHelpers:
    """finish/send/reject/bot_send 包装函数测试。"""

    @pytest.mark.asyncio
    async def test_finish_sends_replied_text_in_group(self) -> None:
        """群聊下 finish 应发送带 reply 的 Message。"""
        event = _make_event(message_type="group", message_id=123)
        matcher = MagicMock()
        matcher.finish = AsyncMock()
        await finish(event, matcher, "hello")
        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.await_args
        assert call_args is not None
        message = call_args.args[0]
        assert isinstance(message, Message)
        assert message[0].type == "reply"
        assert message[1].type == "text"
        assert message[1].data["text"] == "hello"

    @pytest.mark.asyncio
    async def test_send_passes_plain_text_in_private(self) -> None:
        """私聊下 send 应直接传递字符串。"""
        event = _make_event(message_type="private", message_id=42)
        matcher = MagicMock()
        matcher.send = AsyncMock()
        await send(event, matcher, "hello")
        matcher.send.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_reject_passes_plain_text_in_private(self) -> None:
        """私聊下 reject 应直接传递字符串。"""
        event = _make_event(message_type="private", message_id=42)
        matcher = MagicMock()
        matcher.reject = AsyncMock()
        await reject(event, matcher, "hello")
        matcher.reject.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_bot_send_sends_replied_text_in_group(self) -> None:
        """群聊下 bot_send 应发送带 reply 的 Message。"""
        event = _make_event(message_type="group", message_id=123)
        bot = MagicMock()
        bot.send = AsyncMock()
        await bot_send(event, bot, "hello")
        bot.send.assert_awaited_once()
        call_args = bot.send.await_args
        assert call_args is not None
        message = call_args.args[1]
        assert isinstance(message, Message)
        assert message[0].type == "reply"
        assert message[1].type == "text"
