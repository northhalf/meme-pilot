"""/del 命令插件单元测试。"""

import asyncio
from collections.abc import Awaitable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nonebot.adapters.onebot.v11 import Message

from bot.engine.collection_manager import InvalidPublicIdError, MemeNotFoundError
from bot.engine.metadata_store import MemeEntry
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
    from bot.plugins.delete import got_confirm, handle_delete


async def _await_handler(result: Any | Awaitable[Any]) -> Any:
    """等待 NoneBot Handler 的宽泛 Awaitable 返回类型。"""
    return await result


def _run_handler(result: Any | Awaitable[Any]) -> Any:
    """在独立事件循环中运行 NoneBot Handler。"""
    return asyncio.run(_await_handler(result))


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
    entry.public_id = MemePublicId(0, entry_id)
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
            patch("bot.plugins.delete.is_authorized", return_value=False),
            patch("bot.plugins.delete.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            _run_handler(handle_delete(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        """群聊中 @bot → 回复仅限私聊。"""
        with patch("bot.plugins.delete.is_authorized", return_value=True):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            event.message_id = 123456
            matcher = _make_matcher()

            _run_handler(handle_delete(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "此命令仅限私聊使用"
            if isinstance(msg, Message):
                assert msg[0].type == "reply"

    def test_missing_args(self) -> None:
        """无参数 → 回复用法提示。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/del")
            matcher = _make_matcher()

            _run_handler(handle_delete(bot, event, matcher, args=_make_message("")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in extract_message_text(msg)

    def test_invalid_id(self) -> None:
        """非法公开 ID 应回复领域格式提示。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(side_effect=InvalidPublicIdError("abc")),
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/del abc")
            matcher = _make_matcher()

            _run_handler(handle_delete(bot, event, matcher, args=_make_message("abc")))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "表情包 ID 格式错误" in extract_message_text(msg)

    def test_resolve_timeout_deactivates_without_starting_confirmation(self) -> None:
        """解析公开 ID 等待读锁超时应清理会话且不启动确认超时。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(side_effect=asyncio.TimeoutError),
            ),
            patch("bot.plugins.delete.session_manager.deactivate_chat") as deactivate,
            patch(
                "bot.plugins.delete.timeout_session", new_callable=AsyncMock
            ) as timeout,
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/del 1.3"),
                    matcher,
                    args=_make_message("1.3"),
                )
            )  # type: ignore[arg-type]

        assert extract_message_text(matcher.finish.await_args[0][0]) == (
            "索引更新较慢，请稍后再试"
        )
        deactivate.assert_called_once()
        timeout.assert_not_called()

    def test_all_ids_not_found(self) -> None:
        """所有 id 都不存在 → 回复未找到任何表情包。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(side_effect=MemeNotFoundError("0.999")),
            ),
            patch(
                "bot.plugins.delete.session_manager.deactivate_chat"
            ) as mock_deactivate,
        ):
            bot = _make_bot()
            event = _make_event(text="/del 999 998")
            matcher = _make_matcher()

            _run_handler(
                handle_delete(bot, event, matcher, args=_make_message("999 998"))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "未找到任何表情包"
            mock_deactivate.assert_called_once()

    def test_summary_sent_correctly(self) -> None:
        """找到部分 id → 发送正确的摘要确认消息。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(
                    side_effect=[
                        _make_entry(42, "加班心累时的表情包"),
                        _make_entry(43, "当你的老板说今天要加班"),
                    ]
                ),
            ),
            patch(
                "bot.plugins.delete.session_manager.create_selection"
            ) as mock_create_selection,
            patch(
                "bot.plugins.delete.session_manager.reset_current_task"
            ) as mock_reset,
        ):
            bot = _make_bot()
            event = _make_event(text="/del 42 43")
            matcher = _make_matcher()

            _run_handler(
                handle_delete(bot, event, matcher, args=_make_message("42 43"))
            )  # type: ignore[arg-type]

            assert matcher.send.await_count == 1
            msg = matcher.send.await_args[0][0]
            assert "确认删除以下表情包" in extract_message_text(msg)
            assert "0.42，加班心累时的表情包" in extract_message_text(msg)
            assert "0.43，当你的老板说今天要加班" in extract_message_text(msg)
            assert matcher.state["entry_ids"] == [42, 43]
            assert matcher.state["public_ids"] == {
                42: MemePublicId(0, 42),
                43: MemePublicId(0, 43),
            }
            mock_create_selection.assert_called_once()
            mock_reset.assert_called_once()


class TestPublicIdDelete:
    """`/del` 混合公开 ID 与快照测试。"""

    def test_valid_and_missing_full_ids_continue_to_confirmation(self) -> None:
        """完整 ID 混合不存在项时应保留有效项并在摘要提示未找到。"""
        entry = MemeEntry(
            id=85,
            image_path="新三国/a.webp",
            text="甲",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(side_effect=[entry, MemeNotFoundError("2.999")]),
            ),
            patch("bot.plugins.delete.session_manager.create_selection"),
            patch("bot.plugins.delete.session_manager.reset_current_task"),
            patch("bot.plugins.delete.timeout_session", new_callable=AsyncMock),
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/del 1.3 2.999"),
                    matcher,
                    args=_make_message("1.3 2.999"),
                )
            )  # type: ignore[arg-type]

        summary = extract_message_text(matcher.send.await_args[0][0])
        assert "1.3，甲" in summary
        assert "未找到 ID：2.999" in summary
        assert matcher.state["entry_ids"] == [85]
        assert matcher.state["not_found_public_ids"] == ["2.999"]

    def test_short_missing_ids_are_normalized_and_deduplicated(self) -> None:
        """短号不存在项应按完整公开 ID 展示并保持首次出现顺序去重。"""
        entry = MemeEntry(
            id=85,
            image_path="新三国/a.webp",
            text="甲",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(
                    side_effect=[
                        entry,
                        MemeNotFoundError("1.999"),
                        MemeNotFoundError("1.999"),
                        MemeNotFoundError("1.1000"),
                    ]
                ),
            ),
            patch("bot.plugins.delete.session_manager.create_selection"),
            patch("bot.plugins.delete.session_manager.reset_current_task"),
            patch("bot.plugins.delete.timeout_session", new_callable=AsyncMock),
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/del 003 0999 999 1000"),
                    matcher,
                    args=_make_message("003 0999 999 1000"),
                )
            )  # type: ignore[arg-type]

        summary = extract_message_text(matcher.send.await_args[0][0])
        assert "未找到 ID：1.999、1.1000" in summary
        assert matcher.state["not_found_public_ids"] == ["1.999", "1.1000"]

    def test_all_missing_ids_finish_without_confirmation(self) -> None:
        """全部公开 ID 不存在时应统一结束且不启动确认会话。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(
                    side_effect=[
                        MemeNotFoundError("1.999"),
                        MemeNotFoundError("2.999"),
                    ]
                ),
            ),
            patch("bot.plugins.delete.session_manager.deactivate_chat") as deactivate,
            patch(
                "bot.plugins.delete.timeout_session", new_callable=AsyncMock
            ) as timeout,
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/del 1.999 2.999"),
                    matcher,
                    args=_make_message("1.999 2.999"),
                )
            )  # type: ignore[arg-type]

        assert extract_message_text(matcher.finish.await_args[0][0]) == (
            "未找到任何表情包"
        )
        matcher.send.assert_not_awaited()
        deactivate.assert_called_once()
        timeout.assert_not_called()

    def test_mixed_ids_deduplicate_by_internal_id_preserving_order(self) -> None:
        """完整 ID 与短号指向同条目时只处理一次并保存公开 ID 快照。"""
        first = MemeEntry(
            id=85,
            image_path="新三国/a.webp",
            text="甲",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        second = MemeEntry(
            id=86,
            image_path="甄嬛传/b.webp",
            text="乙",
            collection_id=2,
            local_id=1,
            collection_name="甄嬛传",
        )
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat", return_value=True
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(side_effect=[first, first, second]),
            ) as mock_resolve,
            patch("bot.plugins.delete.session_manager.create_selection"),
            patch("bot.plugins.delete.session_manager.reset_current_task"),
            patch("bot.plugins.delete.timeout_session", new_callable=AsyncMock),
        ):
            event = _make_event(text="/del 1.3 003 2.1")
            matcher = _make_matcher()
            _run_handler(
                handle_delete(
                    _make_bot(), event, matcher, args=_make_message("1.3 003 2.1")
                )
            )  # type: ignore[arg-type]

        assert [call.args[1] for call in mock_resolve.await_args_list] == [
            "1.3",
            "003",
            "2.1",
        ]
        assert matcher.state["entry_ids"] == [85, 86]
        assert matcher.state["public_ids"] == {
            85: MemePublicId(1, 3),
            86: MemePublicId(2, 1),
        }
        summary = extract_message_text(matcher.send.await_args[0][0])
        assert "1.3，甲" in summary
        assert summary.count("1.3，甲") == 1
        assert "2.1，乙" in summary

    def test_result_merges_parse_and_racing_not_found_in_order(self) -> None:
        """最终结果应先展示解析期未找到，再展示执行竞态未找到。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch("bot.plugins.delete.get_index_manager") as mock_get_im,
        ):
            manager = MagicMock()
            manager.delete = AsyncMock(
                return_value=MagicMock(
                    deleted_ids=[85],
                    not_found_ids=[86],
                    failed_ids=[],
                )
            )
            manager.add_user_timeout = 60
            mock_get_im.return_value = manager
            matcher = _make_matcher(
                state={
                    "entry_ids": [85, 86],
                    "public_ids": {
                        85: MemePublicId(1, 3),
                        86: MemePublicId(2, 1),
                    },
                    "not_found_public_ids": ["3.7", "2.1", "1.999"],
                }
            )
            _run_handler(
                got_confirm(
                    _make_bot(),
                    _make_event(text="确认"),
                    matcher,
                    _make_message("确认"),
                )
            )  # type: ignore[arg-type]

        text = extract_message_text(matcher.finish.await_args[0][0])
        assert "成功：1.3" in text
        assert "未找到：3.7、2.1、1.999" in text
        assert text.count("2.1") == 1

    def test_result_uses_public_id_snapshot_after_racing_delete(self) -> None:
        """执行期未找到与失败结果都应使用删除前公开 ID 快照。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch("bot.plugins.delete.get_index_manager") as mock_get_im,
        ):
            manager = MagicMock()
            manager.delete = AsyncMock(
                return_value=MagicMock(
                    deleted_ids=[85],
                    not_found_ids=[86],
                    failed_ids=[(87, "文件移动失败")],
                )
            )
            manager.add_user_timeout = 60
            mock_get_im.return_value = manager
            matcher = _make_matcher(
                state={
                    "entry_ids": [85, 86, 87],
                    "public_ids": {
                        85: MemePublicId(1, 3),
                        86: MemePublicId(2, 1),
                        87: MemePublicId(0, 42),
                    },
                }
            )
            event = _make_event(text="确认")
            _run_handler(
                got_confirm(_make_bot(), event, matcher, _make_message("确认"))
            )  # type: ignore[arg-type]

        text = extract_message_text(matcher.finish.await_args[0][0])
        assert "成功：1.3" in text
        assert "未找到：2.1" in text
        assert "ID:0.42 原因:『文件移动失败』" in text
        assert "85" not in text
        assert "86" not in text
        assert "87" not in text


# ---------------------------------------------------------------------------
# got_confirm 测试
# ---------------------------------------------------------------------------


class TestGotConfirm:
    """got_confirm 确认处理测试。"""

    def test_confirm_yes(self) -> None:
        """用户回复确认 → 调用 delete，回复成功摘要。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch("bot.plugins.delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(
                return_value=MagicMock(
                    deleted_ids=[42],
                    not_found_ids=[43],
                    failed_ids=[],
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(
                state={
                    "entry_ids": [42, 43],
                    "public_ids": {42: MemePublicId(0, 42), 43: MemePublicId(0, 43)},
                }
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            im.delete.assert_awaited_once_with([42, 43])
            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "删除结果如下" in extract_message_text(msg)
            assert "成功：0.42" in extract_message_text(msg)
            assert "未找到：0.43" in extract_message_text(msg)

    def test_confirm_yes_english(self) -> None:
        """用户回复 yes → 调用 delete。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch("bot.plugins.delete.get_index_manager") as mock_get_im,
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
            matcher = _make_matcher(
                state={"entry_ids": [42], "public_ids": {42: MemePublicId(0, 42)}}
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            im.delete.assert_awaited_once_with([42])

    def test_confirm_failed_ids(self) -> None:
        """删除结果包含失败 id → 摘要中显示失败原因。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch("bot.plugins.delete.get_index_manager") as mock_get_im,
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
            matcher = _make_matcher(
                state={
                    "entry_ids": [42, 45],
                    "public_ids": {42: MemePublicId(0, 42), 45: MemePublicId(0, 45)},
                }
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            msg = matcher.finish.await_args[0][0]
            assert "失败：ID:0.45 原因:『文件移动失败』" in extract_message_text(msg)

    def test_cancel(self) -> None:
        """用户回复其他内容 → 回复已取消删除。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(
                state={
                    "entry_ids": [42, 43],
                    "public_ids": {42: MemePublicId(0, 42), 43: MemePublicId(0, 43)},
                }
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "已取消删除"

    def test_cancel_intercept(self) -> None:
        """等待确认时 /cancel → 旁路取消。"""
        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch(
                "bot.plugins.delete.got_intercept_bypass",
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

    def test_refresh_in_progress(self) -> None:
        """删除时触发 RefreshInProgressError → 回复索引正在刷新。"""
        from bot.index_manager import RefreshInProgressError

        with (
            patch("bot.plugins.delete.session_manager.handler_context"),
            patch("bot.plugins.delete.session_manager.deactivate_chat"),
            patch("bot.plugins.delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(side_effect=RefreshInProgressError())
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(
                state={"entry_ids": [42], "public_ids": {42: MemePublicId(0, 42)}}
            )

            _run_handler(
                got_confirm(bot, event, matcher, _make_message(event.get_plaintext()))
            )  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert extract_message_text(msg) == "索引正在刷新，请稍后再试"


# ---------------------------------------------------------------------------
# 短命令 /d 测试
# ---------------------------------------------------------------------------


class TestShortCommandDelete:
    """短命令 /d 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_ids(self) -> None:
        """短命令 /d 的参数经 CommandArg 提取后应与 /del 一致。"""
        with (
            patch("bot.plugins.delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch(
                "bot.plugins.delete.resolve_entry_argument",
                new=AsyncMock(
                    side_effect=[_make_entry(12, "文本"), _make_entry(42, "文本")]
                ),
            ),
            patch("bot.plugins.delete.session_manager.create_selection"),
            patch("bot.plugins.delete.session_manager.reset_current_task"),
            patch("bot.plugins.delete.timeout_session", new_callable=MagicMock),
            patch("bot.plugins.delete.asyncio.create_task"),
        ):
            matcher = _make_matcher()
            _run_handler(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/d 12 42"),
                    matcher,
                    args=_make_message("12 42"),
                )  # type: ignore[arg-type]
            )
            assert matcher.state["entry_ids"] == [12, 42]
