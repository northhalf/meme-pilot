"""_search_utils 模块单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from nonebot.exception import RejectedException

from bot.engine.keyword_searcher import SearchResult


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id, image_path=image_path, text=text, similarity=similarity
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
            _make_search_result(entry_id=1, image_path="a.jpg", text="甲"),
            _make_search_result(entry_id=2, image_path="b.jpg", text="乙"),
        ]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "2")

        assert isinstance(result, SearchResult)
        assert result.entry_id == 2
        assert result.image_path == "b.jpg"

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
    @patch("bot.plugins._search_utils.session_manager.deactivate_chat")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_no_results_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock, mock_deactivate: MagicMock
    ) -> None:
        """无匹配结果时应回复提示并 deactivate_chat。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=[])
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "xyz")

        _cmd.finish.assert_awaited_once()
        assert "没有匹配到" in _cmd.finish.call_args[0][0]
        mock_deactivate.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.deactivate_chat")
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_single_result_sends_image(
        self,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_segment: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """唯一结果应直接发送图片并 deactivate_chat。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[_make_search_result(image_path="加班心累.jpg")]
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()
        mock_deactivate.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_multiple_results_registers_session(
        self,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_timeout: MagicMock,
        mock_create_selection: MagicMock,
    ) -> None:
        """多个结果时应创建选择会话并启动超时。"""
        from bot.plugins._search_utils import execute_search

        results = [
            _make_search_result(entry_id=1, text="甲"),
            _make_search_result(entry_id=2, text="乙"),
        ]
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=results)
        _cmd = MagicMock()
        _cmd.state = {}
        _cmd.send = AsyncMock()

        await execute_search(_make_bot(), _make_event("111"), _cmd, "加班")

        assert "candidates" in _cmd.state
        assert len(_cmd.state["candidates"]) == 2
        assert "selection_id" in _cmd.state
        mock_create_selection.assert_called_once()
        args = mock_create_selection.call_args[0]
        assert args[0] == "111"  # user_id
        assert args[1] == _cmd.state["selection_id"]  # selection_id matches
        mock_timeout.assert_called_once()
        timeout_args = mock_timeout.call_args[0]
        assert timeout_args[2] == "111"  # user_id
        assert timeout_args[3] == _cmd.state["selection_id"]  # selection_id
        assert "选择已过期" in timeout_args[4]  # message

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


class TestGotInterceptBypass:
    """got_intercept_bypass 测试。"""

    @pytest.mark.asyncio
    async def test_normal_text_returns_false(self):
        """普通文本返回 False。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        result = await got_intercept_bypass("user1", matcher, "hello", "帮助文本")
        assert result is False

    @pytest.mark.asyncio
    async def test_help_returns_true(self):
        """/help 拦截后抛出 RejectedException。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        with pytest.raises(RejectedException):
            matcher.reject.side_effect = RejectedException("reject")
            await got_intercept_bypass("user1", matcher, "/help", "帮助文本")
        matcher.reject.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_returns_true(self):
        """/cancel 拦截后返回 True。"""
        from bot.plugins._search_utils import got_intercept_bypass
        from bot.session import session_manager

        matcher = AsyncMock()
        session_manager.activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass("user1", matcher, "/cancel", "帮助文本")
        assert result is True

    @pytest.mark.asyncio
    async def test_help_with_args_matches(self):
        """/help xxx（带参数）也匹配帮助，抛出 RejectedException。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        with pytest.raises(RejectedException):
            matcher.reject.side_effect = RejectedException("reject")
            await got_intercept_bypass("user1", matcher, "/help 加班", "帮助文本")
        matcher.reject.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_with_args_matches(self):
        """/cancel xxx（带参数）也匹配取消。"""
        from bot.plugins._search_utils import got_intercept_bypass
        from bot.session import session_manager

        matcher = AsyncMock()
        session_manager.activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass("user1", matcher, "/cancel something", "帮助文本")
        assert result is True
