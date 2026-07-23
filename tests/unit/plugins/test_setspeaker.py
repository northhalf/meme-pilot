"""/setspeaker 命令插件单元测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.collection_manager import InvalidPublicIdError, MemeNotFoundError
from bot.index_manager import SetSpeakerResult
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import MemePublicId
from bot.session import ChatScope
from tests.conftest import _assert_has_reply, _assert_no_reply, extract_message_text


# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化
_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn
_mock_on_command = MagicMock(return_value=_mock_cmd)

with (
    patch("nonebot.on_command", _mock_on_command),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.setspeaker import (
        got_confirm,
        handle_setspeaker,
        timeout_session,
    )


class TestSetspeakerCommandRegistration:
    """测试 /setspeaker 命令注册边界。"""

    def test_requires_whitespace_boundary(self) -> None:
        """命令带参数时必须以空白分隔，避免误匹配前缀相近的文本。"""
        registration = _mock_on_command.call_args

        assert registration is not None
        assert registration.args[0] == "setspeaker"
        assert registration.kwargs["aliases"] == {"sp"}
        assert registration.kwargs.get("force_whitespace") is True


def _make_event(
    user_id: str = "12345", text: str = "/setspeaker", message_type: str = "private"
) -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = message_type
    if message_type == "group":
        event.message_id = 123456
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_entry(image_path: str = "test.jpg", speaker: str | None = None) -> MagicMock:
    """创建模拟的 MemeEntry。"""
    entry = MagicMock()
    entry.id = 3
    entry.public_id = MemePublicId(1, 3)
    entry.image_path = image_path
    entry.speaker = speaker
    entry.text = "一些文字"
    return entry


def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 Message 对象（CommandArg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


# ---------------------------------------------------------------------------
# handle_setspeaker 测试
# ---------------------------------------------------------------------------


class TestHandleSetspeaker:
    """handle_setspeaker 入口函数测试。"""

    @pytest.mark.asyncio
    async def test_unauthorized(self) -> None:
        """非授权用户应调用 finish(None) 结束匹配。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=False),
            patch("bot.plugins.setspeaker.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message(""))

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            event.message_id = 123456
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message(""))

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.await_args[0][0]
            _assert_has_reply(reply)
            assert "仅限私聊" in extract_message_text(reply)

    @pytest.mark.asyncio
    async def test_active_session_conflict(self) -> None:
        """已有活跃会话 → 提示 /cancel。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=False,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker 3")
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message("3"))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已有命令在处理中，请先 /cancel"
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_missing_entry_id(self) -> None:
        """无参数 → 回复用法提示。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker")
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message(""))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_invalid_entry_id(self) -> None:
        """非数字 entry_id → 回复 entry_id 必须为数字。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(side_effect=InvalidPublicIdError("abc")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker abc 张三")
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message("abc 张三"))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "表情包 ID 格式错误" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_resolve_timeout_deactivates_without_starting_confirmation(
        self,
    ) -> None:
        """解析公开 ID 等待读锁超时应清理会话且不启动确认超时。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(side_effect=asyncio.TimeoutError),
            ),
            patch(
                "bot.plugins.setspeaker.session_manager.deactivate_chat"
            ) as deactivate,
            patch(
                "bot.plugins.setspeaker.timeout_session", new_callable=AsyncMock
            ) as timeout,
        ):
            matcher = _make_matcher()
            await handle_setspeaker(
                _make_bot(),
                _make_event(text="/setspeaker 1.3 曹操"),
                matcher,
                args=_make_message("1.3 曹操"),
            )

        assert extract_message_text(matcher.finish.await_args[0][0]) == (
            "索引更新较慢，请稍后再试"
        )
        deactivate.assert_called_once()
        timeout.assert_not_called()

    @pytest.mark.asyncio
    async def test_entry_not_found(self) -> None:
        """entry_id 不存在 → 回复未找到。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(side_effect=MemeNotFoundError("1.999")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker 999 张三")
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message("999 张三"))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "未找到" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_with_speaker(self, tmp_path: Path) -> None:
        """带说话人参数 → 正确解析并发送确认消息。"""
        img_file = tmp_path / "test.jpg"
        img_file.touch()

        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(return_value=_make_entry(speaker=None)),
            ),
            patch("bot.plugins.setspeaker.MEMES_DIR", new=tmp_path),
            patch("bot.config.MEMES_DIR", new=tmp_path),  # 修复 import
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker 3 张三")
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message("3 张三"))

            # 应发送图片 + 确认消息
            assert matcher.send.await_count == 2
            confirm_msg = matcher.send.await_args[0][0]
            assert "当前说话人" in extract_message_text(confirm_msg)
            _assert_no_reply(confirm_msg)
            # 验证 state
            assert matcher.state["entry_id"] == 3
            assert matcher.state["speaker"] == "张三"
            assert matcher.state["old_speaker"] is None

    @pytest.mark.asyncio
    async def test_without_speaker_clear(self, tmp_path: Path) -> None:
        """无说话人参数 → speaker=None（清空）。"""
        img_file = tmp_path / "test.jpg"
        img_file.touch()

        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(return_value=_make_entry(speaker="李四")),
            ),
            patch("bot.plugins.setspeaker.MEMES_DIR", new=tmp_path),
            patch("bot.config.MEMES_DIR", new=tmp_path),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker 3")
            matcher = _make_matcher()

            await handle_setspeaker(bot, event, matcher, args=_make_message("3"))

            assert matcher.state["entry_id"] == 3
            assert matcher.state["speaker"] is None


class TestPublicIdSetspeaker:
    """`/setspeaker` 公开 ID 迁移测试。"""

    @pytest.mark.asyncio
    async def test_long_leading_zero_id_is_passed_raw(self) -> None:
        """超长前导零完整 ID 应原样交给共享解析器。"""
        raw_id = f"{'0' * 5000}1.{'0' * 5000}3"
        entry = MemeEntry(
            id=85,
            image_path="新三国/a.webp",
            text="测试",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(return_value=entry),
            ) as mock_resolve,
            patch("bot.plugins.setspeaker.session_manager.create_selection"),
            patch("bot.plugins.setspeaker.session_manager.reset_current_task"),
            patch("bot.plugins.setspeaker.timeout_session", new_callable=AsyncMock),
        ):
            matcher = _make_matcher()
            event = _make_event(text=f"/setspeaker {raw_id} 曹操")
            await handle_setspeaker(
                _make_bot(), event, matcher, args=_make_message(f"{raw_id} 曹操")
            )

        mock_resolve.assert_awaited_once_with(event, raw_id)
        assert matcher.state["entry_id"] == 85
        assert matcher.state["public_id"] == MemePublicId(1, 3)


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestGotConfirm:
    """got_confirm 确认处理测试。"""

    @pytest.mark.asyncio
    async def test_confirm_yes(self) -> None:
        """用户回复确认 → 调用 set_speaker，回复成功。"""
        with (
            patch("bot.plugins.setspeaker.session_manager.handler_context"),
            patch("bot.plugins.setspeaker.session_manager.deactivate_chat"),
            patch("bot.plugins.setspeaker.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.set_speaker = AsyncMock(
                return_value=SetSpeakerResult(
                    entry_id=3, old_speaker=None, new_speaker="张三"
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(
                state={
                    "entry_id": 3,
                    "public_id": MemePublicId(1, 3),
                    "speaker": "张三",
                    "old_speaker": None,
                }
            )

            await got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))

            im.set_speaker.assert_awaited_once_with(3, "张三")
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "已设置" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_confirm_yes_english(self) -> None:
        """用户回复 yes → 调用 set_speaker。"""
        with (
            patch("bot.plugins.setspeaker.session_manager.handler_context"),
            patch("bot.plugins.setspeaker.session_manager.deactivate_chat"),
            patch("bot.plugins.setspeaker.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.set_speaker = AsyncMock(
                return_value=SetSpeakerResult(
                    entry_id=3, old_speaker=None, new_speaker="张三"
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="yes")
            matcher = _make_matcher(
                state={
                    "entry_id": 3,
                    "public_id": MemePublicId(1, 3),
                    "speaker": "张三",
                    "old_speaker": None,
                }
            )

            await got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))

            im.set_speaker.assert_awaited_once_with(3, "张三")

    @pytest.mark.asyncio
    async def test_cancel(self) -> None:
        """用户回复其他内容 → 回复已取消。"""
        with (
            patch("bot.plugins.setspeaker.session_manager.handler_context"),
            patch("bot.plugins.setspeaker.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(
                state={
                    "entry_id": 3,
                    "public_id": MemePublicId(1, 3),
                    "speaker": "张三",
                    "old_speaker": None,
                }
            )

            await got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已取消"
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_clear_speaker_confirmation(self) -> None:
        """清空 speaker 场景的确认。"""
        with (
            patch("bot.plugins.setspeaker.session_manager.handler_context"),
            patch("bot.plugins.setspeaker.session_manager.deactivate_chat"),
            patch("bot.plugins.setspeaker.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.set_speaker = AsyncMock(
                return_value=SetSpeakerResult(
                    entry_id=3, old_speaker="李四", new_speaker=None
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(
                state={
                    "entry_id": 3,
                    "public_id": MemePublicId(1, 3),
                    "speaker": None,
                    "old_speaker": "李四",
                }
            )

            await got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))

            im.set_speaker.assert_awaited_once_with(3, None)
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "已设置" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    async def test_cancel_intercept(self) -> None:
        """等待确认时 /cancel → 旁路取消。"""
        with (
            patch("bot.plugins.setspeaker.session_manager.handler_context"),
            patch("bot.plugins.setspeaker.session_manager.deactivate_chat"),
            patch(
                "bot.plugins.setspeaker.got_intercept_bypass",
                new_callable=AsyncMock,
            ) as mock_bypass,
        ):
            mock_bypass.return_value = True

            bot = _make_bot()
            event = _make_event(text="/cancel")
            matcher = _make_matcher()

            await got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))

            mock_bypass.assert_awaited_once()


# ---------------------------------------------------------------------------
# 短命令 /sp 测试
# ---------------------------------------------------------------------------


class TestShortCommandSetspeaker:
    """短命令 /sp 通过 CommandArg 提取参数测试。"""

    @pytest.mark.asyncio
    async def test_short_command_extracts_id_and_speaker(self) -> None:
        """短命令 /sp 的参数经 CommandArg 提取后应与 /setspeaker 一致。"""
        with (
            patch("bot.plugins.setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.setspeaker.resolve_entry_argument",
                new=AsyncMock(return_value=_make_entry()),
            ),
            patch("bot.plugins.setspeaker.session_manager.create_selection"),
            patch("bot.plugins.setspeaker.session_manager.reset_current_task"),
            patch("bot.plugins.setspeaker.timeout_session", new_callable=MagicMock),
            patch("bot.plugins.setspeaker.asyncio.create_task"),
        ):
            matcher = _make_matcher()
            await handle_setspeaker(
                _make_bot(),
                _make_event(text="/sp 42 小明"),
                matcher,
                args=_make_message("42 小明"),
            )
            assert matcher.state["entry_id"] == 3
            assert matcher.state["public_id"] == MemePublicId(1, 3)
            assert matcher.state["speaker"] == "小明"


# ---------------------------------------------------------------------------
# 超时测试
# ---------------------------------------------------------------------------


class TestTimeoutSession:
    """timeout_session 超时提示测试。"""

    @pytest.mark.asyncio
    async def test_timeout_group_reply(self) -> None:
        """群聊中超时提示应通过 bot.send 发送带 reply 的消息。"""
        bot = _make_bot()
        event = _make_event(message_type="group")
        scope = ChatScope(
            user_id=int(event.get_user_id()),
            chat_type="group",
            chat_id=654321,
        )
        selection_id = "test-timeout-id"

        selection = MagicMock()
        selection.selection_id = selection_id

        with (
            patch(
                "bot.plugins.setspeaker.session_manager.get_selection",
                return_value=selection,
            ) as mock_get_selection,
            patch(
                "bot.plugins.setspeaker.session_manager.remove_selection"
            ) as mock_remove_selection,
            patch(
                "bot.plugins.setspeaker.session_manager.deactivate_chat"
            ) as mock_deactivate_chat,
        ):
            await timeout_session(
                bot,
                event,
                scope,
                selection_id,
                "说话人设置已取消（超时）",
                timeout=0,
            )

            mock_get_selection.assert_called_once_with(scope)
            mock_remove_selection.assert_called_once_with(scope)
            mock_deactivate_chat.assert_called_once_with(scope)
            bot.send.assert_awaited_once()
            call_args = bot.send.call_args[0]
            sent_event = call_args[0]
            assert sent_event is event
            sent_msg = call_args[1]
            _assert_has_reply(sent_msg, message_id=123456)
            assert "说话人设置已取消（超时）" in extract_message_text(sent_msg)
