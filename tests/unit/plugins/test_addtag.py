"""/addtag 命令插件单元测试。"""

import asyncio
from collections.abc import Awaitable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nonebot.adapters.onebot.v11 import Message

from bot.engine.collection_manager import InvalidPublicIdError, MemeNotFoundError
from bot.engine.index_manager import AddTagResult
from bot.engine.types import MemePublicId
from tests.conftest import extract_message_text


# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化
_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.addtag import (
        got_confirm,
        handle_addtag,
    )


async def _await_handler(result: Any | Awaitable[Any]) -> Any:
    """等待 NoneBot Handler 的宽泛 Awaitable 返回类型。"""
    return await result


def _run_handler(result: Any | Awaitable[Any]) -> Any:
    """在独立事件循环中运行 NoneBot Handler。"""
    return asyncio.run(_await_handler(result))


def _make_event(user_id: str = "12345", text: str = "/addtag") -> MagicMock:
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


def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 Message 对象（CommandArg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


def _make_entry(
    image_path: str = "test.jpg",
    text: str = "测试文本",
    tags: list[str] | None = None,
) -> MagicMock:
    """创建模拟的 MemeEntry。"""
    entry = MagicMock()
    entry.id = 3
    entry.image_path = image_path
    entry.text = text
    entry.speaker = None
    entry.tags = tags if tags is not None else []
    entry.public_id = MemePublicId(1, 3)
    return entry


# ---------------------------------------------------------------------------
# handle_addtag 测试
# ---------------------------------------------------------------------------


class TestHandleAddtag:
    """handle_addtag 入口函数测试。"""

    def test_unauthorized(self) -> None:
        """非授权用户应调用 finish(None) 结束匹配。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=False),
            patch("bot.plugins.addtag.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            _run_handler(handle_addtag(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            event.message_id = 123456
            matcher = _make_matcher()

            _run_handler(handle_addtag(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "此命令仅限私聊使用"
            if isinstance(msg, Message):
                assert msg[0].type == "reply"

    def test_active_session_conflict(self) -> None:
        """已有活跃会话 → 提示 /cancel。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=False,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag 3 标签")
            matcher = _make_matcher()

            _run_handler(
                handle_addtag(bot, event, matcher, args=_make_message("3 标签"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已有命令在处理中，请先 /cancel"

    def test_missing_args(self) -> None:
        """无参数 → 回复用法提示。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag")
            matcher = _make_matcher()

            _run_handler(handle_addtag(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in extract_message_text(msg)

    def test_missing_tags(self) -> None:
        """只有 entry_id 无标签 → 回复用法提示。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag 3")
            matcher = _make_matcher()

            _run_handler(handle_addtag(bot, event, matcher, args=_make_message("3")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in extract_message_text(msg)

    def test_invalid_entry_id(self) -> None:
        """非法公开 ID 应回复领域格式提示。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.addtag.resolve_entry_argument",
                new=AsyncMock(side_effect=InvalidPublicIdError("abc")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag abc 标签")
            matcher = _make_matcher()

            _run_handler(
                handle_addtag(bot, event, matcher, args=_make_message("abc 标签"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "表情包 ID 格式错误" in extract_message_text(msg)

    def test_resolve_timeout_deactivates_without_starting_confirmation(self) -> None:
        """解析公开 ID 等待读锁超时应清理会话且不启动确认超时。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.addtag.resolve_entry_argument",
                new=AsyncMock(side_effect=asyncio.TimeoutError),
            ),
            patch("bot.plugins.addtag.session_manager.deactivate_chat") as deactivate,
            patch(
                "bot.plugins.addtag.timeout_session", new_callable=AsyncMock
            ) as timeout,
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_addtag(
                    _make_bot(),
                    _make_event(text="/addtag 1.3 标签"),
                    matcher,
                    args=_make_message("1.3 标签"),
                )
            )  # type: ignore[arg-type]

        assert extract_message_text(matcher.finish.await_args[0][0]) == (
            "索引更新较慢，请稍后再试"
        )
        deactivate.assert_called_once()
        timeout.assert_not_called()

    def test_entry_not_found(self) -> None:
        """entry_id 不存在 → 回复未找到。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.addtag.resolve_entry_argument",
                new=AsyncMock(side_effect=MemeNotFoundError("9.99")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag 999 标签")
            matcher = _make_matcher()

            _run_handler(
                handle_addtag(bot, event, matcher, args=_make_message("999 标签"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "未找到" in extract_message_text(msg)

    def test_confirmation_message(self) -> None:
        """参数合法 → 发送确认消息并保存 state。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.addtag.resolve_entry_argument",
                new=AsyncMock(return_value=_make_entry(tags=["已有"])),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag 3 新增1 新增2")
            matcher = _make_matcher()

            _run_handler(
                handle_addtag(bot, event, matcher, args=_make_message("3 新增1 新增2"))
            )  # type: ignore[arg-type]

            # 应只发送一条确认消息（不发送图片）
            matcher.send.assert_awaited_once()
            msg = matcher.send.await_args[0][0]
            assert "当前 OCR 文本" in extract_message_text(msg)
            assert "当前标签" in extract_message_text(msg)
            assert "新增标签" in extract_message_text(msg)
            assert "新增1" in extract_message_text(msg)
            assert "新增2" in extract_message_text(msg)
            assert "已有" in extract_message_text(msg)

            assert matcher.state["entry_id"] == 3
            assert matcher.state["tags"] == ["新增1", "新增2"]


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestGotConfirm:
    """got_confirm 确认处理测试。"""

    def test_confirm_yes(self) -> None:
        """用户回复确认 → 调用 add_tags，回复成功。"""
        with (
            patch("bot.plugins.addtag.session_manager.handler_context"),
            patch("bot.plugins.addtag.session_manager.deactivate_chat"),
            patch("bot.plugins.addtag.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.add_tags = AsyncMock(
                return_value=AddTagResult(
                    entry_id=3,
                    added_tags=["新增1", "新增2"],
                    all_tags=["已有", "新增1", "新增2"],
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
                    "tags": ["新增1", "新增2"],
                }
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            im.add_tags.assert_awaited_once_with(3, ["新增1", "新增2"])
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "已添加" in extract_message_text(msg)
            assert "新增1" in extract_message_text(msg)
            assert "已有" in extract_message_text(msg)

    def test_confirm_yes_english(self) -> None:
        """用户回复 yes → 调用 add_tags。"""
        with (
            patch("bot.plugins.addtag.session_manager.handler_context"),
            patch("bot.plugins.addtag.session_manager.deactivate_chat"),
            patch("bot.plugins.addtag.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.add_tags = AsyncMock(
                return_value=AddTagResult(
                    entry_id=3,
                    added_tags=["新增"],
                    all_tags=["新增"],
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
                    "tags": ["新增"],
                }
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            im.add_tags.assert_awaited_once_with(3, ["新增"])

    def test_cancel(self) -> None:
        """用户回复其他内容 → 回复已取消。"""
        with (
            patch("bot.plugins.addtag.session_manager.handler_context"),
            patch("bot.plugins.addtag.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(
                state={
                    "entry_id": 3,
                    "public_id": MemePublicId(1, 3),
                    "tags": ["新增1", "新增2"],
                }
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已取消"

    def test_cancel_intercept(self) -> None:
        """等待确认时 /cancel → 旁路取消。"""
        with (
            patch("bot.plugins.addtag.session_manager.handler_context"),
            patch("bot.plugins.addtag.session_manager.deactivate_chat"),
            patch(
                "bot.plugins.addtag.got_intercept_bypass",
                new_callable=AsyncMock,
            ) as mock_bypass,
        ):
            mock_bypass.return_value = True

            bot = _make_bot()
            event = _make_event(text="/cancel")
            matcher = _make_matcher()

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            mock_bypass.assert_awaited_once()


# ---------------------------------------------------------------------------
# 短命令 /at 测试
# ---------------------------------------------------------------------------


class TestShortCommandAddtag:
    """短命令 /at 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_id_and_tags(self) -> None:
        """短命令 /at 的参数经 CommandArg 提取后应与 /addtag 一致。"""
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.addtag.resolve_entry_argument",
                new=AsyncMock(return_value=_make_entry(text="旧文本")),
            ),
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_addtag(
                    _make_bot(),
                    _make_event(text="/at 42 心累 深夜"),
                    matcher,
                    args=_make_message("42 心累 深夜"),
                )  # type: ignore[arg-type]
            )
            assert matcher.state["entry_id"] == 3
            assert matcher.state["public_id"] == MemePublicId(1, 3)
            assert matcher.state["tags"] == ["心累", "深夜"]


class TestPublicIdAddtag:
    """`/addtag` 公开 ID 解析与展示测试。"""

    def test_leading_zero_public_id_is_resolved_without_int_conversion(self) -> None:
        """完整 ID 前导零应原样传给共享解析器并保存双 ID state。"""
        from bot.engine.metadata_store import MemeEntry
        from bot.engine.types import MemePublicId

        entry = MemeEntry(
            id=42,
            image_path="新三国/a.webp",
            text="旧文本",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        with (
            patch("bot.plugins.addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.addtag.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.addtag.resolve_entry_argument",
                new=AsyncMock(return_value=entry),
            ) as mock_resolve,
            patch("bot.plugins.addtag.session_manager.create_selection"),
            patch("bot.plugins.addtag.session_manager.reset_current_task"),
            patch("bot.plugins.addtag.timeout_session", new_callable=AsyncMock),
        ):
            event = _make_event(text="/addtag 01.003 吐槽")
            matcher = _make_matcher()
            _run_handler(
                handle_addtag(
                    _make_bot(), event, matcher, args=_make_message("01.003 吐槽")
                )
            )  # type: ignore[arg-type]

        mock_resolve.assert_awaited_once_with(event, "01.003")
        assert matcher.state["entry_id"] == 42
        assert matcher.state["public_id"] == MemePublicId(1, 3)

    def test_success_message_uses_public_id_snapshot(self) -> None:
        """执行成功消息应使用 state 中的公开 ID 快照。"""
        from bot.engine.types import MemePublicId

        with (
            patch("bot.plugins.addtag.session_manager.handler_context"),
            patch("bot.plugins.addtag.session_manager.deactivate_chat"),
            patch("bot.plugins.addtag.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.add_tags = AsyncMock(
                return_value=AddTagResult(
                    entry_id=42, added_tags=["吐槽"], all_tags=["吐槽"]
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im
            matcher = _make_matcher(
                state={
                    "entry_id": 42,
                    "public_id": MemePublicId(1, 3),
                    "tags": ["吐槽"],
                }
            )
            event = _make_event(text="确认")

            _run_handler(
                got_confirm(_make_bot(), event, matcher, _make_message("确认"))
            )  # type: ignore[arg-type]

        assert "ID：1.3" in extract_message_text(matcher.finish.await_args[0][0])
        assert "42" not in extract_message_text(matcher.finish.await_args[0][0])
