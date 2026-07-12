"""/del 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化
_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.meme_delete import got_confirm, handle_delete


def _make_event(user_id: str = "12345", text: str = "/del") -> MagicMock:
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


def _make_entry(entry_id: int, text: str) -> MagicMock:
    """创建模拟的 MemeEntry。"""
    entry = MagicMock()
    entry.id = entry_id
    entry.text = text
    return entry


def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 Message 对象（CommandArg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


# ---------------------------------------------------------------------------
# handle_delete 测试
# ---------------------------------------------------------------------------


class TestHandleDelete:
    """handle_delete 入口函数测试。"""

    def test_unauthorized(self) -> None:
        """非授权用户应调用 finish(None) 结束匹配。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=False),
            patch("bot.plugins.meme_delete.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with patch("bot.plugins.meme_delete.is_authorized", return_value=True):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("此命令仅限私聊使用")

    def test_missing_args(self) -> None:
        """无参数 → 回复用法提示。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/del")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in msg

    def test_invalid_id(self) -> None:
        """非数字 id → 回复 id 必须为数字。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/del abc")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher, args=_make_message("abc")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("id 必须为数字")

    def test_all_ids_not_found(self) -> None:
        """所有 id 都不存在 → 回复未找到任何表情包。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_delete.get_metadata_store") as mock_get_store,
            patch(
                "bot.plugins.meme_delete.session_manager.deactivate_chat"
            ) as mock_deactivate,
        ):
            store = MagicMock()
            store.get_entry.return_value = None
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/del 999 998")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher, args=_make_message("999 998")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("未找到任何表情包")
            mock_deactivate.assert_called_once()

    def test_summary_sent_correctly(self) -> None:
        """找到部分 id → 发送正确的摘要确认消息。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_delete.get_metadata_store") as mock_get_store,
            patch(
                "bot.plugins.meme_delete.session_manager.create_selection"
            ) as mock_create_selection,
            patch(
                "bot.plugins.meme_delete.session_manager.reset_current_task"
            ) as mock_reset,
        ):
            store = MagicMock()

            def _get_entry(eid: int):
                if eid == 42:
                    return _make_entry(42, "加班心累时的表情包")
                if eid == 43:
                    return _make_entry(43, "当你的老板说今天要加班")
                return None

            store.get_entry.side_effect = _get_entry
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/del 42 43 44")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher, args=_make_message("42 43 44")))  # type: ignore[arg-type]

            assert matcher.send.await_count == 1
            msg = matcher.send.await_args[0][0]
            assert "确认删除以下表情包" in msg
            assert "42, 加班心累时的表情包" in msg
            assert "43, 当你的老板说今天要加班" in msg
            assert "未找到 id：44" in msg
            assert matcher.state["entry_ids"] == [42, 43]
            assert matcher.state["not_found_ids"] == [44]
            mock_create_selection.assert_called_once()
            mock_reset.assert_called_once()


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestGotConfirm:
    """got_confirm 确认处理测试。"""

    def test_confirm_yes(self) -> None:
        """用户回复确认 → 调用 delete，回复成功摘要。"""
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(
                return_value=MagicMock(
                    deleted_ids=[42, 43],
                    not_found_ids=[44],
                    failed_ids=[],
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(
                state={"entry_ids": [42, 43], "not_found_ids": [44]}
            )

            asyncio.run(got_confirm(bot, event, matcher, _make_message(event.get_plaintext())))  # type: ignore[arg-type]

            im.delete.assert_awaited_once_with([42, 43])
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "删除结果如下" in msg
            assert "成功：42、43" in msg
            assert "未找到：44" in msg

    def test_confirm_yes_english(self) -> None:
        """用户回复 yes → 调用 delete。"""
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(
                return_value=MagicMock(
                    deleted_ids=[42],
                    not_found_ids=[],
                    failed_ids=[],
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="yes")
            matcher = _make_matcher(state={"entry_ids": [42]})

            asyncio.run(got_confirm(bot, event, matcher, _make_message(event.get_plaintext())))  # type: ignore[arg-type]

            im.delete.assert_awaited_once_with([42])

    def test_confirm_failed_ids(self) -> None:
        """删除结果包含失败 id → 摘要中显示失败原因。"""
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(
                return_value=MagicMock(
                    deleted_ids=[42],
                    not_found_ids=[],
                    failed_ids=[(45, "文件移动失败")],
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(state={"entry_ids": [42, 45]})

            asyncio.run(got_confirm(bot, event, matcher, _make_message(event.get_plaintext())))  # type: ignore[arg-type]

            msg = matcher.finish.await_args[0][0]
            assert "失败：id:45 原因:『文件移动失败』" in msg

    def test_cancel(self) -> None:
        """用户回复其他内容 → 回复已取消删除。"""
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(
                state={"entry_ids": [42, 43], "not_found_ids": [44]}
            )

            asyncio.run(got_confirm(bot, event, matcher, _make_message(event.get_plaintext())))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("已取消删除")

    def test_cancel_intercept(self) -> None:
        """等待确认时 /cancel → 旁路取消。"""
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
            patch(
                "bot.plugins.meme_delete.got_intercept_bypass",
                new_callable=AsyncMock,
            ) as mock_bypass,
        ):
            mock_bypass.return_value = True

            bot = _make_bot()
            event = _make_event(text="/cancel")
            matcher = _make_matcher()

            asyncio.run(got_confirm(bot, event, matcher, _make_message(event.get_plaintext())))  # type: ignore[arg-type]

            mock_bypass.assert_awaited_once()

    def test_refresh_in_progress(self) -> None:
        """删除时触发 RefreshInProgressError → 回复索引正在刷新。"""
        from bot.engine.index_manager import RefreshInProgressError

        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(side_effect=RefreshInProgressError())
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(state={"entry_ids": [42]})

            asyncio.run(got_confirm(bot, event, matcher, _make_message(event.get_plaintext())))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("索引正在刷新，请稍后再试")


# ---------------------------------------------------------------------------
# 短命令 /d 测试
# ---------------------------------------------------------------------------


class TestShortCommandDelete:
    """短命令 /d 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_ids(self) -> None:
        """短命令 /d 的参数经 CommandArg 提取后应与 /del 一致。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_delete.get_metadata_store") as mock_store,
            patch(
                "bot.plugins.meme_delete.session_manager.create_selection"
            ),
            patch(
                "bot.plugins.meme_delete.session_manager.reset_current_task"
            ),
            patch("bot.plugins.meme_delete.timeout_session", new_callable=MagicMock),
            patch("bot.plugins.meme_delete.asyncio.create_task"),
        ):
            entry = MagicMock()
            entry.text = "文本"
            store = MagicMock()
            store.get_entry.return_value = entry
            mock_store.return_value = store

            matcher = _make_matcher()
            asyncio.run(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/d 12 42"),
                    matcher,
                    args=_make_message("12 42"),
                )  # type: ignore[arg-type]
            )
            assert matcher.state["entry_ids"] == [12, 42]
