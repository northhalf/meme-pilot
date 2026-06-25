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


def _make_index_manager(*, is_locked: bool = False, entry_count: int = 10) -> MagicMock:
    """创建模拟的 IndexManager。"""
    im = MagicMock()
    im.is_locked = is_locked
    im.entry_count = entry_count
    return im


def _make_keyword_searcher(*, results: list | None = None) -> MagicMock:
    """创建模拟的 KeywordSearcher。"""
    from bot.engine.keyword_searcher import SearchResult

    ks = MagicMock()
    if results is not None:
        ks.search.return_value = results
    else:
        ks.search.return_value = [
            SearchResult(
                entry_id="1",
                filename="加班心累.jpg",
                text="加班到心累",
                similarity=95.0,
            )
        ]
    return ks


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
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """授权用户应正常执行。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher()

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        mock_get_ks.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_cmd()
        bot = _make_bot()

        await handle_search(bot, _make_event("999"), _make_matcher())

        mock_get_im.assert_not_called()
        mock_get_ks.assert_not_called()
        _mock_cmd.finish.assert_not_awaited()
        bot.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# 索引锁
# ---------------------------------------------------------------------------


class TestHandleSearchLock:
    """索引锁测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_lock_contention_replies(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引锁占用时应回复提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager(is_locked=True)

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        assert "索引正在更新" in _mock_cmd.finish.call_args[0][0]
        mock_get_ks.assert_not_called()


# ---------------------------------------------------------------------------
# 会话覆盖
# ---------------------------------------------------------------------------


class TestHandleSearchSessionOverride:
    """会话覆盖测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(
        meme_search, "check_and_cancel", return_value="已取消上一条未完成的操作"
    )
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_existing_session_cancelled(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """旧会话存在时应取消并提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher()

        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.send.assert_awaited_once()
        assert "已取消上一条未完成的操作" in matcher.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(meme_search, "check_and_cancel", return_value=None)
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_no_existing_session_skips_hint(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """无旧会话时不应发送提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher()

        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# 空关键词
# ---------------------------------------------------------------------------


class TestHandleSearchEmptyKeyword:
    """空关键词测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_empty_keyword_replies_usage(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """/search 无参数时应回复用法提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()

        await handle_search(_make_bot(), _make_event(text="/search"), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        assert "/search" in _mock_cmd.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 空索引
# ---------------------------------------------------------------------------


class TestHandleSearchEmptyIndex:
    """空索引测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引为空时应回复表情包目录为空。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager(entry_count=0)

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        assert "表情包目录为空" in _mock_cmd.finish.call_args[0][0]
        mock_get_ks.assert_not_called()


# ---------------------------------------------------------------------------
# 无匹配结果
# ---------------------------------------------------------------------------


class TestHandleSearchNoMatch:
    """无匹配结果测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "MessageSegment")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_no_results_replies_no_match(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """无匹配结果时应回复提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=[])

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        assert "没有匹配到" in _mock_cmd.finish.call_args[0][0]
        mock_segment.image.assert_not_called()


# ---------------------------------------------------------------------------
# 唯一结果直接发送图片
# ---------------------------------------------------------------------------


class TestHandleSearchSingleResult:
    """唯一结果测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "MessageSegment")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_single_result_sends_image(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """唯一结果应直接发送图片。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[_make_search_result(filename="加班心累.jpg")]
        )

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "MessageSegment")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_single_result_image_path_correct(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """图片路径应为 file:/// URI 格式。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[_make_search_result(filename="test.jpg")]
        )

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        call_args = mock_segment.image.call_args[0][0]
        assert "memes" in str(call_args)
        assert str(call_args).startswith("file:///")


# ---------------------------------------------------------------------------
# 多结果显示选择列表
# ---------------------------------------------------------------------------


class TestHandleSearchMultipleResults:
    """多结果测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(meme_search, "check_and_cancel", return_value=None)
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_multiple_results_sends_list(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """多个结果时应发送选择列表。"""
        _reset_cmd()
        results = [
            _make_search_result(entry_id="1", text="加班到心累", similarity=95.0),
            _make_search_result(entry_id="2", text="加班使我快乐", similarity=80.0),
        ]
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=results)

        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.send.assert_awaited_once()
        sent_text = matcher.send.call_args[0][0]
        assert "找到多个匹配的表情包" in sent_text
        assert "1. 加班到心累" in sent_text
        assert "2. 加班使我快乐" in sent_text
        assert "回复编号即可" in sent_text

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(meme_search, "check_and_cancel", return_value=None)
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_multiple_results_stores_candidates(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """多个结果时应将候选存储到 matcher.state。"""
        _reset_cmd()
        results = [
            _make_search_result(entry_id="1", text="加班到心累"),
            _make_search_result(entry_id="2", text="加班使我快乐"),
        ]
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=results)

        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        assert "candidates" in matcher.state
        assert len(matcher.state["candidates"]) == 2

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(meme_search, "check_and_cancel", return_value=None)
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_multiple_results_registers_session(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """多个结果时应注册会话。"""
        _reset_cmd()
        results = [
            _make_search_result(entry_id="1", text="加班到心累"),
            _make_search_result(entry_id="2", text="加班使我快乐"),
        ]
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=results)

        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event("111"), matcher)

        mock_register.assert_called_once_with("111", matcher, "search")


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
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_choice_sends_image(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """有效编号应发送对应图片。"""
        candidates = [
            _make_search_result(entry_id="1", filename="a.jpg", text="甲"),
            _make_search_result(entry_id="2", filename="b.jpg", text="乙"),
        ]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="1"), matcher, _make_message("1"))

        matcher.finish.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_choice_cancels_session(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """有效编号应清理会话。"""
        candidates = [
            _make_search_result(entry_id="1", filename="a.jpg", text="甲"),
        ]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event("12345", "1"), matcher, _make_message("1"))

        mock_cancel.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_choice_sends_correct_image(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """选择第 2 个结果应发送对应图片路径。"""
        from unittest.mock import call

        candidates = [
            _make_search_result(entry_id="1", filename="a.jpg", text="甲"),
            _make_search_result(entry_id="2", filename="b.jpg", text="乙"),
        ]
        matcher = _make_matcher(state={"candidates": candidates})

        with patch.object(meme_search, "MessageSegment") as mock_segment:
            await got_selection(_make_bot(), _make_event(text="2"), matcher, _make_message("2"))

            call_args = mock_segment.image.call_args[0][0]
            assert "b.jpg" in str(call_args)

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_invalid_text_rejects(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """非数字输入应 reject 提示重输。"""
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="abc"), matcher, _make_message("abc"))

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_out_of_range_low_rejects(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """编号小于 1 时应 reject。"""
        candidates = [_make_search_result(), _make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="0"), matcher, _make_message("0"))

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_out_of_range_high_rejects(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """编号超出范围时应 reject。"""
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(_make_bot(), _make_event(text="5"), matcher, _make_message("5"))

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_empty_candidates_finishes_with_error(
        self,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """candidates 为空时应 finish 并提示搜索状态异常。"""
        matcher = _make_matcher(state={})

        await got_selection(_make_bot(), _make_event(text="1"), matcher, _make_message("1"))

        mock_cancel.assert_called_once_with("12345")
        matcher.finish.assert_awaited_once()
        assert "搜索状态异常" in matcher.finish.call_args[0][0]


# ===========================================================================
# search() 异常测试
# ===========================================================================


class TestHandleSearchResults:
    """search() 异常测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_search_exception_replies_error(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
    ) -> None:
        """search() 抛异常时应回复服务不可用。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        ks = _make_keyword_searcher()
        ks.search.side_effect = RuntimeError("rapidfuzz 错误")
        mock_get_ks.return_value = ks

        await handle_search(_make_bot(), _make_event(), _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        assert "搜索服务暂时不可用" in _mock_cmd.finish.call_args[0][0]
