"""_search_utils 模块单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.engine.keyword_searcher import SearchResult


def _make_search_result(
    entry_id: str = "1",
    filename: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id, filename=filename, text=text, similarity=similarity
    )


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


class TestHandleSelection:
    """handle_selection 测试。"""

    def test_valid_choice_returns_result(self) -> None:
        """有效编号应返回对应 SearchResult。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [
            _make_search_result(entry_id="1", filename="a.jpg", text="甲"),
            _make_search_result(entry_id="2", filename="b.jpg", text="乙"),
        ]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "2")

        assert isinstance(result, SearchResult)
        assert result.entry_id == "2"
        assert result.filename == "b.jpg"

    def test_invalid_text_returns_error(self) -> None:
        """非数字输入应返回错误消息字符串。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "abc")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_low_returns_error(self) -> None:
        """编号小于 1 时应返回错误消息。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [_make_search_result(), _make_search_result()]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "0")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_high_returns_error(self) -> None:
        """编号超出范围时应返回错误消息。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "5")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_empty_candidates_returns_error(self) -> None:
        """candidates 为空时应返回错误消息。"""
        from bot.plugins._search_utils import handle_selection

        matcher = _make_matcher()

        result = handle_selection(matcher, [], "1")

        assert isinstance(result, str)
        assert "搜索状态异常" in result


from unittest.mock import patch


def _make_index_manager(*, is_locked: bool = False, entry_count: int = 10) -> MagicMock:
    im = MagicMock()
    im.is_locked = is_locked
    im.entry_count = entry_count
    return im


def _make_keyword_searcher(*, results: list | None = None) -> MagicMock:
    ks = MagicMock()
    if results is not None:
        ks.search.return_value = results
    else:
        ks.search.return_value = [_make_search_result()]
    return ks


def _make_event(user_id: str = "12345") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


class TestExecuteSearch:
    """execute_search 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_lock_contention_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引锁占用时应回复提示。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(is_locked=True)
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "索引正在更新" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_empty_index_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引为空时应回复提示。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(entry_count=0)
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "表情包目录为空" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_no_results_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """无匹配结果时应回复提示。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=[])
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "xyz")

        _cmd.finish.assert_awaited_once()
        assert "没有匹配到" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_single_result_sends_image(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock, mock_segment: MagicMock
    ) -> None:
        """唯一结果应直接发送图片。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[_make_search_result(filename="加班心累.jpg")]
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.timeout_session")
    @patch("bot.plugins._search_utils.register")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_multiple_results_registers_session(
        self,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_register: MagicMock,
        mock_timeout: MagicMock,
    ) -> None:
        """多个结果时应注册会话并启动超时。"""
        from bot.plugins._search_utils import execute_search

        results = [
            _make_search_result(entry_id="1", text="甲"),
            _make_search_result(entry_id="2", text="乙"),
        ]
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=results)
        _cmd = MagicMock()
        _cmd.state = {}
        _cmd.send = AsyncMock()

        await execute_search(_make_bot(), _make_event("111"), _cmd, "加班")

        mock_register.assert_called_once_with("111", _cmd, "search")
        assert "candidates" in _cmd.state
        assert len(_cmd.state["candidates"]) == 2

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_search_exception_replies_error(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """search() 抛异常时应回复服务不可用。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        ks = _make_keyword_searcher()
        ks.search.side_effect = RuntimeError("pylcs 错误")
        mock_get_ks.return_value = ks
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "搜索服务暂时不可用" in _cmd.finish.call_args[0][0]
