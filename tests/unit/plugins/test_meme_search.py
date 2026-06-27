"""/search 命令插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.keyword_searcher import SearchResult

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# got() 返回透传 decorator。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (patch("nonebot.on_command", return_value=_mock_cmd),):
    from bot.plugins import meme_search
    from bot.plugins.meme_search import got_selection, handle_search


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "/search 加班") -> MagicMock:
    """创建模拟的 PrivateMessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
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


def _make_search_result(
    entry_id: str = "1",
    filename: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
) -> SearchResult:
    """创建模拟的 SearchResult。"""

    return SearchResult(
        entry_id=entry_id,
        filename=filename,
        text=text,
        similarity=similarity,
    )


def _reset_cmd() -> None:
    """重置 mock_cmd 的 finish/send 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()
    _mock_cmd.send = AsyncMock()


def _make_message(text: str = "1") -> MagicMock:
    """创建模拟的 Message 对象（Arg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


# ===========================================================================
# handle_search 测试
# ===========================================================================


# ---------------------------------------------------------------------------
# 授权校验
# ---------------------------------------------------------------------------


class TestHandleSearchAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_exec: AsyncMock
    ) -> None:
        """授权用户应正常调用 execute_search。"""
        await handle_search(_make_bot(), _make_event(), _make_matcher())

        mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock, mock_exec: AsyncMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_cmd()
        bot = _make_bot()

        await handle_search(bot, _make_event("999"), _make_matcher())

        mock_exec.assert_not_awaited()
        _mock_cmd.finish.assert_not_awaited()
        bot.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# 会话覆盖
# ---------------------------------------------------------------------------


class TestHandleSearchSessionOverride:
    """会话覆盖测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(
        meme_search, "check_and_cancel", return_value="已取消上一条未完成的操作"
    )
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_existing_session_cancelled(
        self,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_exec: AsyncMock,
    ) -> None:
        """旧会话存在时应取消并提示。"""
        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.send.assert_awaited_once()
        assert "已取消上一条未完成的操作" in matcher.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search, "check_and_cancel", return_value=None)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_no_existing_session_skips_hint(
        self,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_exec: AsyncMock,
    ) -> None:
        """无旧会话时不应发送提示。"""
        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# 空关键词
# ---------------------------------------------------------------------------


class TestHandleSearchEmptyKeyword:
    """空关键词测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_empty_keyword_replies_usage(
        self, mock_auth: MagicMock
    ) -> None:
        """/search 无参数时应回复用法提示。"""
        _reset_cmd()

        await handle_search(_make_bot(), _make_event(text="/search"), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        assert "/search" in _mock_cmd.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 委托 execute_search
# ---------------------------------------------------------------------------


class TestHandleSearchDelegation:
    """测试 handle_search 正确委托 execute_search。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_execute_search_called_with_correct_args(
        self, mock_auth: MagicMock, mock_exec: AsyncMock
    ) -> None:
        """应将 bot、event、matcher、keyword 传给 execute_search。"""
        bot = _make_bot()
        event = _make_event(text="/search 测试关键词")
        matcher = _make_matcher()

        await handle_search(bot, event, matcher)

        mock_exec.assert_awaited_once_with(bot, event, matcher, "测试关键词")


# ===========================================================================
# got_selection 测试
# ===========================================================================


class TestGotSelection:
    """got_selection 处理函数测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=True)
    async def test_cancelled_session_exits(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """已取消的会话应静默退出。"""
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})
        await got_selection(_make_bot(), _make_event(), matcher, _make_message())

        mock_cancel.assert_not_called()
        matcher.finish.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_choice_sends_image(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """有效编号应发送对应图片。"""
        result = _make_search_result(filename="a.jpg")
        mock_handle.return_value = result
        candidates = [result]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="1"), matcher, _make_message("1"))

        matcher.finish.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_choice_cancels_session(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """有效编号应清理会话。"""
        result = _make_search_result(filename="a.jpg")
        mock_handle.return_value = result
        matcher = _make_matcher(state={"candidates": [result]})

        await got_selection(_make_bot(), _make_event("12345", "1"), matcher, _make_message("1"))

        mock_cancel.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_choice_sends_correct_image(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """选择第 2 个结果应发送对应图片路径。"""
        result = _make_search_result(entry_id="2", filename="b.jpg", text="乙")
        mock_handle.return_value = result
        candidates = [
            _make_search_result(entry_id="1", filename="a.jpg", text="甲"),
            result,
        ]
        matcher = _make_matcher(state={"candidates": candidates})

        with patch.object(meme_search, "MessageSegment") as mock_segment:
            await got_selection(_make_bot(), _make_event(text="2"), matcher, _make_message("2"))

            call_args = mock_segment.image.call_args[0][0]
            assert "b.jpg" in str(call_args)

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_invalid_text_rejects(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """非数字输入应 reject 提示重输。"""
        mock_handle.return_value = "无效编号，请回复 1-1 之间的数字"
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="abc"), matcher, _make_message("abc"))

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_out_of_range_low_rejects(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """编号小于 1 时应 reject。"""
        mock_handle.return_value = "无效编号，请回复 1-2 之间的数字"
        candidates = [_make_search_result(), _make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="0"), matcher, _make_message("0"))

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_out_of_range_high_rejects(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """编号超出范围时应 reject。"""
        mock_handle.return_value = "无效编号，请回复 1-1 之间的数字"
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="5"), matcher, _make_message("5"))

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "handle_selection")
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_empty_candidates_rejects_with_error(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """candidates 为空时 handle_selection 返回错误消息，应 reject。"""
        mock_handle.return_value = "搜索状态异常，请重新搜索"
        matcher = _make_matcher(state={})

        await got_selection(_make_bot(), _make_event(text="1"), matcher, _make_message("1"))

        mock_handle.assert_called_once()
        matcher.reject.assert_awaited_once()
        assert "搜索状态异常" in matcher.reject.call_args[0][0]
