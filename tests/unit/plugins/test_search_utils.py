"""_search_utils 模块单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.exception import RejectedException

from bot.engine.keyword_searcher import SearchResult


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
    speaker: str | None = None,
    tags: list[str] | None = None,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
        speaker=speaker,
        tags=tags or [],
    )


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


class TestResolveSelection:
    """resolve_selection 测试。"""

    def test_valid_choice_returns_result(self) -> None:
        """有效编号应返回对应 SearchResult。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [
            _make_search_result(entry_id=1, image_path="a.jpg", text="甲"),
            _make_search_result(entry_id=2, image_path="b.jpg", text="乙"),
        ]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "2")

        assert isinstance(result, SearchResult)
        assert result.entry_id == 2
        assert result.image_path == "b.jpg"

    def test_invalid_text_returns_error(self) -> None:
        """非数字输入应返回错误消息字符串。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "abc")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_low_returns_error(self) -> None:
        """编号小于 1 时应返回错误消息。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result(), _make_search_result()]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "0")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_high_returns_error(self) -> None:
        """编号超出范围时应返回错误消息。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "5")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_empty_candidates_returns_error(self) -> None:
        """candidates 为空时应返回错误消息。"""
        from bot.plugins._search_utils import resolve_selection

        matcher = _make_matcher()

        result = resolve_selection(matcher, [], "1")

        assert isinstance(result, str)
        assert "搜索状态异常" in result


def _make_index_manager(
    *, results: list | None = None, search_side_effect: Exception | None = None
) -> MagicMock:
    im = MagicMock()
    if search_side_effect is not None:
        im.search = AsyncMock(side_effect=search_side_effect)
    elif results is not None:
        im.search = AsyncMock(return_value=results)
    else:
        im.search = AsyncMock(return_value=[_make_search_result()])
    return im


def _make_event(user_id: str = "12345") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


class TestPresentCandidates:
    """present_candidates 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_creates_selection_and_formats_list(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """应格式化列表、存储候选并创建选择会话。"""
        from bot.plugins._search_utils import present_candidates

        candidates = [
            _make_search_result(entry_id=1, text="甲", speaker="小明", tags=["吐槽"]),
            _make_search_result(entry_id=2, text="乙", tags=["搞笑"]),
        ]
        cmd = _make_matcher()
        cmd.state = {}

        await present_candidates(_make_bot(), _make_event("111"), cmd, candidates)

        assert "candidates" in cmd.state
        assert "selection_id" in cmd.state
        sent_text = cmd.send.call_args[0][0]
        assert "1. 甲 -- 1, 小明, 吐槽" in sent_text
        assert "2. 乙 -- 2, 无, 搞笑" in sent_text
        mock_create_selection.assert_called_once()
        mock_timeout.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_prompt_suffix_appended(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """prompt_suffix 应追加到提示文本末尾。"""
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}

        await present_candidates(
            _make_bot(),
            _make_event("111"),
            cmd,
            candidates,
            prompt_suffix="回复 0 换一批",
        )

        sent_text = cmd.send.call_args[0][0]
        assert "回复 0 换一批" in sent_text


class TestDispatchSearchResults:
    """dispatch_search_results 测试。"""

    @pytest.mark.asyncio
    async def test_no_results_finishes_no_match(self) -> None:
        """无结果时应结束会话并提示没有匹配。"""
        from bot.plugins._search_utils import dispatch_search_results

        cmd = _make_matcher()

        with patch(
            "bot.plugins._search_utils.session_manager.deactivate_chat"
        ) as mock_deactivate:
            await dispatch_search_results(_make_bot(), _make_event("111"), cmd, [])

            cmd.finish.assert_awaited_once()
            assert "没有匹配到" in cmd.finish.call_args[0][0]
            mock_deactivate.assert_called_once_with("111")

    @pytest.mark.asyncio
    async def test_single_result_sends_image(self) -> None:
        """单结果时应发送图片并 finish 元数据。"""
        from bot.plugins._search_utils import dispatch_search_results

        result = _make_search_result(entry_id=7, image_path="a.jpg", speaker="小明")
        cmd = _make_matcher()

        with patch(
            "bot.plugins._search_utils.session_manager.deactivate_chat"
        ) as mock_deactivate, patch(
            "bot.plugins._search_utils.MessageSegment"
        ) as mock_segment:
            await dispatch_search_results(_make_bot(), _make_event("111"), cmd, [result])

            cmd.send.assert_awaited_once()
            cmd.finish.assert_awaited_once()
            mock_deactivate.assert_called_once_with("111")
            mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "bot.plugins._search_utils.present_candidates", new_callable=AsyncMock
    )
    async def test_multiple_results_calls_present_candidates(
        self, mock_present: AsyncMock
    ) -> None:
        """多结果时应调用 present_candidates 并传递 prompt_suffix。"""
        from bot.plugins._search_utils import dispatch_search_results

        results = [
            _make_search_result(entry_id=1, text="甲"),
            _make_search_result(entry_id=2, text="乙"),
        ]
        cmd = _make_matcher()

        await dispatch_search_results(
            _make_bot(),
            _make_event("111"),
            cmd,
            results,
            prompt_suffix="回复 0 换一批",
        )

        mock_present.assert_awaited_once()
        args = mock_present.call_args
        assert args.kwargs.get("prompt_suffix") == "回复 0 换一批"


class TestExecuteSearch:
    """execute_search 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_timeout_replies(
        self, mock_get_im: MagicMock
    ) -> None:
        """等待读锁超时应回复提示。"""
        import asyncio
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(
            search_side_effect=asyncio.TimeoutError()
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "索引更新较慢" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.deactivate_chat")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_no_results_replies(
        self, mock_get_im: MagicMock, mock_deactivate: MagicMock
    ) -> None:
        """无匹配结果时应回复提示并 deactivate_chat。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(results=[])
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "xyz")

        _cmd.finish.assert_awaited_once()
        assert "没有匹配到" in _cmd.finish.call_args[0][0]
        mock_deactivate.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.deactivate_chat")
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_single_result_sends_image_then_metadata(
        self,
        mock_get_im: MagicMock,
        mock_segment: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """唯一结果应先发送图片，再 finish 元数据行。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(
            results=[
                _make_search_result(
                    entry_id=7, image_path="加班心累.jpg", speaker="小明"
                )
            ]
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()
        _cmd.send = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.send.assert_awaited_once()
        _cmd.finish.assert_awaited_once()
        finished_text = _cmd.finish.call_args[0][0]
        assert "7" in finished_text
        assert "小明" in finished_text
        mock_deactivate.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch(
        "bot.plugins._search_utils.present_candidates", new_callable=AsyncMock
    )
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_multiple_results_delegates_to_present_candidates(
        self, mock_get_im: MagicMock, mock_present: AsyncMock
    ) -> None:
        """多个结果时应委托给 present_candidates。"""
        from bot.plugins._search_utils import execute_search

        results = [
            _make_search_result(entry_id=1, text="甲"),
            _make_search_result(entry_id=2, text="乙"),
        ]
        mock_get_im.return_value = _make_index_manager(results=results)
        bot = _make_bot()
        event = _make_event("111")
        cmd = _make_matcher()

        await execute_search(bot, event, cmd, "加班")

        mock_present.assert_awaited_once_with(
            bot, event, cmd, results, prompt_suffix=""
        )

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_search_exception_replies_error(
        self, mock_get_im: MagicMock
    ) -> None:
        """search() 抛异常时应回复服务不可用。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(
            search_side_effect=RuntimeError("pylcs 错误")
        )
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
        result = await got_intercept_bypass(
            "user1", matcher, "/cancel something", "帮助文本"
        )
        assert result is True


class TestFormatMetadataLine:
    """format_metadata_line 测试。"""

    def test_with_speaker_and_tags(self) -> None:
        """同时存在 speaker 和 tags 时格式化正确。"""
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(3, "小明", ["吐槽", "加班"]) == "3, 小明, 吐槽, 加班"

    def test_missing_speaker(self) -> None:
        """speaker 缺失时显示为"无"。"""
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(7, None, ["吐槽"]) == "7, 无, 吐槽"

    def test_empty_tags_omitted(self) -> None:
        """tags 为空时省略 tags 段。"""
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(7, "小明", []) == "7, 小明"

    def test_both_empty(self) -> None:
        """speaker 和 tags 都为空时只显示 id 和"无"。"""
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(12, None, []) == "12, 无"
