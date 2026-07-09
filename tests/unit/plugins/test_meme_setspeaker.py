"""/setspeaker 命令插件单元测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化
_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.meme_setspeaker import (
        got_confirm,
        handle_setspeaker,
    )

from bot.engine.index_manager import SetSpeakerResult


def _make_event(user_id: str = "12345", text: str = "/setspeaker") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
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

    def test_unauthorized(self) -> None:
        """非授权用户应调用 finish(None) 结束匹配。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=False),
            patch("bot.plugins.meme_setspeaker.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("此命令仅限私聊使用")

    def test_active_session_conflict(self) -> None:
        """已有活跃会话 → 提示 /cancel。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=False,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker 3")
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("3")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("已有命令在处理中，请先 /cancel")

    def test_missing_entry_id(self) -> None:
        """无参数 → 回复用法提示。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker")
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in msg

    def test_invalid_entry_id(self) -> None:
        """非数字 entry_id → 回复 entry_id 必须为数字。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/setspeaker abc 张三")
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("abc 张三")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("entry_id 必须为数字")

    def test_entry_not_found(self) -> None:
        """entry_id 不存在 → 回复未找到。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_setspeaker.get_metadata_store") as mock_get_store,
        ):
            store = MagicMock()
            store.get_entry.return_value = None
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/setspeaker 999 张三")
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("999 张三")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "未找到" in msg

    def test_with_speaker(self, tmp_path: Path) -> None:
        """带说话人参数 → 正确解析并发送确认消息。"""
        img_file = tmp_path / "test.jpg"
        img_file.touch()

        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_setspeaker.get_metadata_store") as mock_get_store,
            patch("bot.plugins.meme_setspeaker.get_index_manager"),
            patch("bot.plugins.meme_setspeaker.MEMES_DIR", new=tmp_path),
            patch("bot.config.MEMES_DIR", new=tmp_path),  # 修复 import
        ):
            store = MagicMock()
            store.get_entry.return_value = _make_entry(speaker=None)
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/setspeaker 3 张三")
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("3 张三")))  # type: ignore[arg-type]

            # 应发送图片 + 确认消息
            assert matcher.send.await_count == 2
            # 验证 state
            assert matcher.state["entry_id"] == 3
            assert matcher.state["speaker"] == "张三"
            assert matcher.state["old_speaker"] is None

    def test_without_speaker_clear(self, tmp_path: Path) -> None:
        """无说话人参数 → speaker=None（清空）。"""
        img_file = tmp_path / "test.jpg"
        img_file.touch()

        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_setspeaker.get_metadata_store") as mock_get_store,
            patch("bot.plugins.meme_setspeaker.get_index_manager"),
            patch("bot.plugins.meme_setspeaker.MEMES_DIR", new=tmp_path),
            patch("bot.config.MEMES_DIR", new=tmp_path),
        ):
            store = MagicMock()
            store.get_entry.return_value = _make_entry(speaker="李四")
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/setspeaker 3")
            matcher = _make_matcher()

            asyncio.run(handle_setspeaker(bot, event, matcher, args=_make_message("3")))  # type: ignore[arg-type]

            assert matcher.state["entry_id"] == 3
            assert matcher.state["speaker"] is None


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestGotConfirm:
    """got_confirm 确认处理测试。"""

    def test_confirm_yes(self) -> None:
        """用户回复确认 → 调用 set_speaker，回复成功。"""
        with (
            patch("bot.plugins.meme_setspeaker.session_manager.handler_context"),
            patch("bot.plugins.meme_setspeaker.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_setspeaker.get_index_manager") as mock_get_im,
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
                state={"entry_id": 3, "speaker": "张三", "old_speaker": None}
            )

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            im.set_speaker.assert_awaited_once_with(3, "张三")
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "已设置" in msg

    def test_confirm_yes_english(self) -> None:
        """用户回复 yes → 调用 set_speaker。"""
        with (
            patch("bot.plugins.meme_setspeaker.session_manager.handler_context"),
            patch("bot.plugins.meme_setspeaker.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_setspeaker.get_index_manager") as mock_get_im,
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
                state={"entry_id": 3, "speaker": "张三", "old_speaker": None}
            )

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            im.set_speaker.assert_awaited_once_with(3, "张三")

    def test_cancel(self) -> None:
        """用户回复其他内容 → 回复已取消。"""
        with (
            patch("bot.plugins.meme_setspeaker.session_manager.handler_context"),
            patch("bot.plugins.meme_setspeaker.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(
                state={"entry_id": 3, "speaker": "张三", "old_speaker": None}
            )

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("已取消")

    def test_clear_speaker_confirmation(self) -> None:
        """清空 speaker 场景的确认。"""
        with (
            patch("bot.plugins.meme_setspeaker.session_manager.handler_context"),
            patch("bot.plugins.meme_setspeaker.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_setspeaker.get_index_manager") as mock_get_im,
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
                state={"entry_id": 3, "speaker": None, "old_speaker": "李四"}
            )

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            im.set_speaker.assert_awaited_once_with(3, None)
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "已设置" in msg

    def test_cancel_intercept(self) -> None:
        """等待确认时 /cancel → 旁路取消。"""
        with (
            patch("bot.plugins.meme_setspeaker.session_manager.handler_context"),
            patch("bot.plugins.meme_setspeaker.session_manager.deactivate_chat"),
            patch(
                "bot.plugins.meme_setspeaker.got_intercept_bypass",
                new_callable=AsyncMock,
            ) as mock_bypass,
        ):
            mock_bypass.return_value = True

            bot = _make_bot()
            event = _make_event(text="/cancel")
            matcher = _make_matcher()

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            mock_bypass.assert_awaited_once()


# ---------------------------------------------------------------------------
# 短命令 /sp 测试
# ---------------------------------------------------------------------------


class TestShortCommandSetspeaker:
    """短命令 /sp 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_id_and_speaker(self) -> None:
        """短命令 /sp 的参数经 CommandArg 提取后应与 /setspeaker 一致。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_setspeaker.get_metadata_store") as mock_store,
            patch("bot.plugins.meme_setspeaker.session_manager.create_selection"),
            patch("bot.plugins.meme_setspeaker.session_manager.reset_current_task"),
            patch("bot.plugins.meme_setspeaker.timeout_session", new_callable=MagicMock),
            patch("bot.plugins.meme_setspeaker.asyncio.create_task"),
        ):
            entry = MagicMock()
            entry.image_path = "test.jpg"
            entry.speaker = None
            entry.text = "旧文本"
            store = MagicMock()
            store.get_entry.return_value = entry
            mock_store.return_value = store

            matcher = _make_matcher()
            asyncio.run(
                handle_setspeaker(
                    _make_bot(),
                    _make_event(text="/sp 42 小明"),
                    matcher,
                    args=_make_message("42 小明"),
                )  # type: ignore[arg-type]
            )
            assert matcher.state["entry_id"] == 42
            assert matcher.state["speaker"] == "小明"
