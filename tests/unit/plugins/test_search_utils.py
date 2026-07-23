"""_search_utils 模块单元测试。"""
# pyright: reportUnusedVariable=false

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import RejectedException

from bot.engine.types import MemePublicId, SearchResult
from bot.session import ChatScope


from tests.conftest import extract_message_text


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
    speaker: str | None = None,
    tags: list[str] | None = None,
    collection_id: int = 0,
    local_id: int | None = None,
    collection_name: str = "全局",
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
        speaker=speaker,
        tags=tuple(tags) if tags else (),
        collection_id=collection_id,
        local_id=entry_id if local_id is None else local_id,
        collection_name=collection_name,
    )


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_scope(user_id: int = 12345) -> ChatScope:
    return ChatScope(user_id=user_id, chat_type="private", chat_id=user_id)


def _make_event(
    user_id: str = "12345",
    message_id: int | None = 42,
    message_type: str = "private",
    group_id: int | None = None,
) -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_id = message_id
    event.message_type = message_type
    event.group_id = group_id
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


class TestResolveSelection:
    """resolve_selection 测试。"""

    def test_valid_choice_returns_result(self) -> None:
        """有效编号应返回对应 SearchResult。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [
            _make_search_result(entry_id=1, image_path="a.jpg", text="甲"),
            _make_search_result(entry_id=2, image_path="b.jpg", text="乙"),
        ]
        result = resolve_selection(candidates, "2")

        assert isinstance(result, SearchResult)
        assert result.entry_id == 2
        assert result.image_path == "b.jpg"

    def test_invalid_text_returns_error(self) -> None:
        """非数字输入应返回错误消息字符串。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result()]
        result = resolve_selection(candidates, "abc")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_low_returns_error(self) -> None:
        """编号小于 1 时应返回错误消息。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result(), _make_search_result()]
        result = resolve_selection(candidates, "0")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_high_returns_error(self) -> None:
        """编号超出范围时应返回错误消息。"""
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result()]
        result = resolve_selection(candidates, "5")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_empty_candidates_returns_error(self) -> None:
        """candidates 为空时应返回错误消息。"""
        from bot.plugins._search_utils import resolve_selection

        result = resolve_selection([], "1")

        assert isinstance(result, str)
        assert "搜索状态异常" in result


def _make_index_manager(
    *,
    results: list[SearchResult] | None = None,
    search_side_effect: Exception | None = None,
) -> MagicMock:
    im = MagicMock()
    if search_side_effect is not None:
        im.search_for_scope = AsyncMock(side_effect=search_side_effect)
    elif results is not None:
        im.search_for_scope = AsyncMock(return_value=results)
    else:
        im.search_for_scope = AsyncMock(return_value=[_make_search_result()])
    return im


class TestPresentCandidates:
    """present_candidates 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_creates_selection_and_formats_list(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
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
        sent_text = extract_message_text(cmd.send.call_args[0][0])
        assert "1. 甲 -- 0.1，全局，小明，吐槽" in sent_text
        assert "2. 乙 -- 0.2，全局，无，搞笑" in sent_text
        _mock_create_selection.assert_called_once()
        _mock_timeout.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_prompt_suffix_appended(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
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

        sent_text = extract_message_text(cmd.send.call_args[0][0])
        assert "回复 0 换一批" in sent_text

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_show_similarity_score_appends_percent(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """score 量纲下列表行末尾追加百分比。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲", similarity=82.0)]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(
            show_similarity=True, similarity_scale="score", next_trigger="n"
        )

        await present_candidates(
            _make_bot(), _make_event("111"), cmd, candidates, options=opts
        )

        sent_text = extract_message_text(cmd.send.call_args[0][0])
        assert "1. 甲 -- 0.1，全局，82%" in sent_text

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_show_similarity_ratio_appends_percent(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """ratio 量纲下 0.82 -> 82%。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲", similarity=0.82)]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(
            show_similarity=True, similarity_scale="ratio", next_trigger="n"
        )

        await present_candidates(
            _make_bot(), _make_event("111"), cmd, candidates, options=opts
        )

        assert "82%" in extract_message_text(cmd.send.call_args[0][0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_next_page_hint_shown_when_has_next(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """有下一页时追加"回复 n 看下一页"。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(next_trigger="n")

        await present_candidates(
            _make_bot(),
            _make_event("111"),
            cmd,
            candidates,
            options=opts,
            has_next_page=True,
        )

        assert "回复 n 看下一页" in extract_message_text(cmd.send.call_args[0][0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_next_page_hint_hidden_on_last_page(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """末页不追加"回复 n 看下一页"。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(next_trigger="n")

        await present_candidates(
            _make_bot(),
            _make_event("111"),
            cmd,
            candidates,
            options=opts,
            has_next_page=False,
        )

        assert "回复 n 看下一页" not in extract_message_text(cmd.send.call_args[0][0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_default_options_no_similarity_no_next_hint(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """/rand 默认 options：无相似度、无"回复 n"。"""
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲", similarity=0.0)]
        cmd = _make_matcher()
        cmd.state = {}

        await present_candidates(
            _make_bot(),
            _make_event("111"),
            cmd,
            candidates,
            prompt_suffix="回复 0 换一批",
        )

        sent_text = extract_message_text(cmd.send.call_args[0][0])
        assert "%" not in sent_text
        assert "回复 n 看下一页" not in sent_text
        assert "回复 0 换一批" in sent_text

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_use_reject_calls_reject_not_send(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """use_reject=True 时用 matcher.reject 重新等待，而非 send。

        守护 got handler 内换一批/翻页后 matcher 不结束、可继续交互。
        """
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}

        await present_candidates(
            _make_bot(),
            _make_event("111"),
            cmd,
            candidates,
            use_reject=True,
        )

        cmd.reject.assert_awaited_once()
        cmd.send.assert_not_awaited()
        # 选择会话在 reject 之前创建
        _mock_create_selection.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_private_sends_plain_text(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """私聊中 present_candidates 发送纯文本。"""
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}

        await present_candidates(
            _make_bot(), _make_event("111", message_id=42), cmd, candidates
        )

        sent = cmd.send.call_args[0][0]
        assert isinstance(sent, str)

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_group_with_reply_sends_message(
        self, _mock_timeout: AsyncMock, _mock_create_selection: MagicMock
    ) -> None:
        """群聊默认通过 reply 段发送 Message。"""
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}
        event = _make_event("111", message_id=42, message_type="group", group_id=67890)

        await present_candidates(_make_bot(), event, cmd, candidates)

        sent = cmd.send.call_args[0][0]
        assert isinstance(sent, Message)
        assert sent[0].type == "reply"
        assert sent[0].data["id"] == "42"
        assert "找到多个匹配的表情包" in extract_message_text(sent)


class TestDispatchSearchResults:
    """dispatch_search_results 测试。"""

    @pytest.mark.asyncio
    async def test_no_results_finishes_no_match(self) -> None:
        """无结果时应结束会话并提示没有匹配。"""
        from bot.plugins._search_utils import dispatch_search_results

        cmd = _make_matcher()
        event = _make_event("111")

        with patch(
            "bot.plugins._search_utils.session_manager.deactivate_chat"
        ) as mock_deactivate:
            await dispatch_search_results(_make_bot(), event, cmd, [])

            cmd.finish.assert_awaited_once()
            assert "没有匹配到" in extract_message_text(cmd.finish.call_args[0][0])
            mock_deactivate.assert_called_once_with(ChatScope.from_event(event))

    @pytest.mark.asyncio
    async def test_single_result_sends_image(self) -> None:
        """单结果时应发送图片并 finish 元数据。"""
        from bot.plugins._search_utils import dispatch_search_results

        result = _make_search_result(entry_id=7, image_path="a.jpg", speaker="小明")
        cmd = _make_matcher()
        event = _make_event("111")

        with (
            patch(
                "bot.plugins._search_utils.session_manager.deactivate_chat"
            ) as mock_deactivate,
            patch("bot.plugins._search_utils.MessageSegment") as mock_segment,
        ):
            await dispatch_search_results(_make_bot(), event, cmd, [result])

            cmd.send.assert_awaited_once()
            cmd.finish.assert_awaited_once()
            mock_deactivate.assert_called_once_with(ChatScope.from_event(event))
            mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.present_candidates", new_callable=AsyncMock)
    async def test_multiple_results_calls_present_candidates(
        self, mock_present: AsyncMock
    ) -> None:
        """多结果时应存分页状态、切第 1 页并传 options/prompt_suffix。"""
        from bot.plugins._search_utils import dispatch_search_results, PresentOptions

        results = [_make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 4)]
        cmd = _make_matcher()
        event = _make_event("111", message_id=42, message_type="group", group_id=67890)
        opts = PresentOptions(
            show_similarity=True, similarity_scale="score", next_trigger="n"
        )

        await dispatch_search_results(
            _make_bot(),
            event,
            cmd,
            results,
            options=opts,
            prompt_suffix="回复 0 换一批",
        )

        mock_present.assert_awaited_once()
        kwargs = mock_present.call_args.kwargs
        assert kwargs["options"] is opts
        assert kwargs["has_next_page"] is False
        assert kwargs["prompt_suffix"] == "回复 0 换一批"
        # 第 1 页切片
        assert mock_present.call_args.args[3] == results[0:10]
        # 分页状态
        assert cmd.state["all_results"] == results
        assert cmd.state["page_index"] == 0
        assert cmd.state["total_pages"] == 1

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.present_candidates", new_callable=AsyncMock)
    async def test_multiple_results_paginates_when_over_page_size(
        self, mock_present: AsyncMock
    ) -> None:
        """结果数 > page_size 时切第 1 页，并正确标记 has_next_page。"""
        from bot.plugins._search_utils import dispatch_search_results, PresentOptions

        results = [
            _make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 26)
        ]  # 25 条
        cmd = _make_matcher()
        event = _make_event("111")

        await dispatch_search_results(
            _make_bot(), event, cmd, results, options=PresentOptions(next_trigger="n")
        )

        kwargs = mock_present.call_args.kwargs
        assert kwargs["has_next_page"] is True
        assert len(mock_present.call_args.args[3]) == 10  # 第 1 页 10 条
        assert cmd.state["total_pages"] == 3


class TestExecuteSearch:
    """execute_search 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.dispatch_search_results", new_callable=AsyncMock)
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_uses_current_collection_once(
        self, mock_get_im: MagicMock, mock_dispatch: AsyncMock
    ) -> None:
        """每次搜索只读取一次当前合集并传入范围过滤。"""
        manager = _make_index_manager(results=[_make_search_result()])
        mock_get_im.return_value = manager
        event = _make_event("111")

        from bot.plugins._search_utils import execute_search

        await execute_search(_make_bot(), event, _make_matcher(), "关键词")

        manager.search_for_scope.assert_awaited_once_with(
            ChatScope.from_event(event), "关键词"
        )
        mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.deactivate_chat")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_selection_timeout_replies_without_search(
        self, mock_get_im: MagicMock, mock_deactivate: MagicMock
    ) -> None:
        """当前合集读取超时时统一提示、清会话且不执行搜索。"""
        manager = _make_index_manager()
        manager.search_for_scope.side_effect = asyncio.TimeoutError
        mock_get_im.return_value = manager
        event = _make_event("111")
        matcher = _make_matcher()

        from bot.plugins._search_utils import execute_search

        await execute_search(_make_bot(), event, matcher, "加班")

        manager.search_for_scope.assert_awaited_once()
        mock_deactivate.assert_called_once_with(ChatScope.from_event(event))
        assert "索引更新较慢" in extract_message_text(matcher.finish.await_args.args[0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_timeout_replies(self, mock_get_im: MagicMock) -> None:
        """等待读锁超时应回复提示。"""
        import asyncio
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(
            search_side_effect=asyncio.TimeoutError()
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()
        event = _make_event("111")

        await execute_search(_make_bot(), event, _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "索引更新较慢" in extract_message_text(_cmd.finish.call_args[0][0])

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
        event = _make_event("111")

        await execute_search(_make_bot(), event, _cmd, "xyz")

        _cmd.finish.assert_awaited_once()
        assert "没有匹配到" in extract_message_text(_cmd.finish.call_args[0][0])
        mock_deactivate.assert_called_once_with(ChatScope.from_event(event))

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
        event = _make_event("111")

        await execute_search(_make_bot(), event, _cmd, "加班")

        _cmd.send.assert_awaited_once()
        _cmd.finish.assert_awaited_once()
        finished_text = extract_message_text(_cmd.finish.call_args[0][0])
        assert "7" in finished_text
        assert "小明" in finished_text
        mock_deactivate.assert_called_once_with(ChatScope.from_event(event))

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.dispatch_search_results", new_callable=AsyncMock)
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_multiple_results_delegates_to_dispatch(
        self, mock_get_im: MagicMock, mock_dispatch: AsyncMock
    ) -> None:
        """多个结果时应委托给 dispatch_search_results 并透传 options。"""
        from bot.plugins._search_utils import execute_search, PresentOptions

        results = [
            _make_search_result(entry_id=1, text="甲"),
            _make_search_result(entry_id=2, text="乙"),
        ]
        mock_get_im.return_value = _make_index_manager(results=results)
        bot = _make_bot()
        event = _make_event("111")
        cmd = _make_matcher()
        opts = PresentOptions(
            show_similarity=True, similarity_scale="score", next_trigger="n"
        )

        await execute_search(bot, event, cmd, "加班", options=opts)

        mock_dispatch.assert_awaited_once_with(bot, event, cmd, results, options=opts)

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_search_exception_replies_error(self, mock_get_im: MagicMock) -> None:
        """search() 抛异常时应回复服务不可用。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(
            search_side_effect=RuntimeError("pylcs 错误")
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()
        event = _make_event("111")

        await execute_search(_make_bot(), event, _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "搜索服务暂时不可用" in extract_message_text(_cmd.finish.call_args[0][0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_timeout_deactivates_session(self, mock_get_im: MagicMock) -> None:
        """搜索超时时应 deactivate 会话，不泄漏。

        构造已激活会话，mock search 抛 asyncio.TimeoutError，
        execute_search 的错误分支应 deactivate_chat 使会话回归空闲。
        """
        import asyncio

        from bot.plugins._search_utils import execute_search
        from bot.session import session_manager

        user_id = 1001
        scope = _make_scope(user_id)
        # 确保干净起始状态后激活会话
        session_manager.deactivate_chat(scope)
        session_manager.activate_chat(scope, "search", _make_matcher())
        assert session_manager.get_or_create_chat(scope).active is True

        mock_get_im.return_value = _make_index_manager(
            search_side_effect=asyncio.TimeoutError()
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(str(user_id)), _cmd, "加班")

        # 错误分支应 deactivate，会话不再活跃
        assert session_manager.get_or_create_chat(scope).active is False

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_runtime_error_deactivates_session(
        self, mock_get_im: MagicMock
    ) -> None:
        """IndexManager 未初始化时应 deactivate 会话，不泄漏。"""
        from bot.plugins._search_utils import execute_search
        from bot.session import session_manager

        user_id = 1002
        scope = _make_scope(user_id)
        session_manager.deactivate_chat(scope)
        session_manager.activate_chat(scope, "search", _make_matcher())
        assert session_manager.get_or_create_chat(scope).active is True

        mock_get_im.side_effect = RuntimeError("not initialized")
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(str(user_id)), _cmd, "加班")

        assert session_manager.get_or_create_chat(scope).active is False

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_search_exception_deactivates_session(
        self, mock_get_im: MagicMock
    ) -> None:
        """search() 抛非预期异常时应 deactivate 会话，不泄漏。"""
        from bot.plugins._search_utils import execute_search
        from bot.session import session_manager

        user_id = 1003
        scope = _make_scope(user_id)
        session_manager.deactivate_chat(scope)
        session_manager.activate_chat(scope, "search", _make_matcher())
        assert session_manager.get_or_create_chat(scope).active is True

        mock_get_im.return_value = _make_index_manager(
            search_side_effect=RuntimeError("pylcs 错误")
        )
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(str(user_id)), _cmd, "加班")

        assert session_manager.get_or_create_chat(scope).active is False


class TestGotInterceptBypass:
    """got_intercept_bypass 测试。"""

    @pytest.mark.asyncio
    async def test_normal_text_returns_false(self) -> None:
        """普通文本返回 False。"""
        from bot.plugins._search_utils import got_intercept_bypass

        event = _make_event("12345")
        matcher = AsyncMock()
        result = await got_intercept_bypass(event, matcher, "hello", "帮助文本")
        assert result is False

    @pytest.mark.asyncio
    async def test_help_returns_true(self) -> None:
        """/help 拦截后抛出 RejectedException。"""
        from bot.plugins._search_utils import got_intercept_bypass

        event = _make_event("12345")
        matcher = AsyncMock()
        with pytest.raises(RejectedException):
            matcher.reject.side_effect = RejectedException("reject")
            await got_intercept_bypass(event, matcher, "/help", "帮助文本")
        matcher.reject.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_returns_true(self) -> None:
        """/cancel 拦截后返回 True。"""
        from bot.plugins._search_utils import got_intercept_bypass
        from bot.session import session_manager

        event = _make_event("12345")
        matcher = AsyncMock()
        scope = ChatScope.from_event(event)
        session_manager.activate_chat(scope, "add", matcher)
        result = await got_intercept_bypass(event, matcher, "/cancel", "帮助文本")
        assert result is True

    @pytest.mark.asyncio
    async def test_help_with_args_matches(self) -> None:
        """/help xxx（带参数）也匹配帮助，抛出 RejectedException。"""
        from bot.plugins._search_utils import got_intercept_bypass

        event = _make_event("12345")
        matcher = AsyncMock()
        with pytest.raises(RejectedException):
            matcher.reject.side_effect = RejectedException("reject")
            await got_intercept_bypass(event, matcher, "/help 加班", "帮助文本")
        matcher.reject.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_with_args_matches(self) -> None:
        """/cancel xxx（带参数）也匹配取消。"""
        from bot.plugins._search_utils import got_intercept_bypass
        from bot.session import session_manager

        event = _make_event("12345")
        matcher = AsyncMock()
        scope = ChatScope.from_event(event)
        session_manager.activate_chat(scope, "add", matcher)
        result = await got_intercept_bypass(
            event, matcher, "/cancel something", "帮助文本"
        )
        assert result is True


class TestFormatMetadataLine:
    """format_metadata_line 测试。"""

    def test_uses_public_id_and_collection(self) -> None:
        """普通合集元数据应展示公开 ID、合集、说话人和标签。"""
        from bot.plugins._search_utils import format_metadata_line

        assert (
            format_metadata_line(MemePublicId(1, 3), "新三国", "曹操", ["吐槽"])
            == "1.3，新三国，曹操，吐槽"
        )

    def test_global_entry_uses_global_name(self) -> None:
        """根目录条目应展示归属名称“全局”，而非搜索范围“全部合集”。"""
        from bot.plugins._search_utils import format_metadata_line

        # speaker 与 tags 同时为空时省略「无」占位，仅展示公开 ID 与合集
        assert (
            format_metadata_line(MemePublicId(0, 42), "全局", None, []) == "0.42，全局"
        )


class TestSimilarityPercent:
    """_similarity_percent 量纲归一测试。"""

    def test_ratio_to_percent(self) -> None:
        """ratio 量纲 0–1 乘 100 后取整。"""
        from bot.plugins._search_utils import _similarity_percent

        assert _similarity_percent(0.82, "ratio") == 82
        assert _similarity_percent(1.0, "ratio") == 100
        assert _similarity_percent(0.0, "ratio") == 0

    def test_score_to_percent(self) -> None:
        """score 量纲 0–100 直接取整。"""
        from bot.plugins._search_utils import _similarity_percent

        assert _similarity_percent(82.0, "score") == 82
        assert _similarity_percent(100.0, "score") == 100
        assert _similarity_percent(60.0, "score") == 60

    def test_clamp_out_of_range(self) -> None:
        """越界值 clamp 到 [0, 100]。"""
        from bot.plugins._search_utils import _similarity_percent

        assert _similarity_percent(1.05, "ratio") == 100  # 浮点越界 clamp
        assert _similarity_percent(-0.1, "ratio") == 0
        assert _similarity_percent(105.0, "score") == 100  # score 量纲越界 clamp


class TestPresentOptionsDefaults:
    """PresentOptions 默认值 = /rand 行为。"""

    def test_defaults(self) -> None:
        """PresentOptions 默认值匹配 /rand 零回归行为。"""
        from bot.plugins._search_utils import (
            PresentOptions,
            PAGE_SIZE,
            NEXT_PAGE_TRIGGER,
        )

        opts = PresentOptions()
        assert opts.show_similarity is False
        assert opts.similarity_scale == "score"
        assert opts.next_trigger is None
        assert opts.page_size == PAGE_SIZE == 10
        assert NEXT_PAGE_TRIGGER == "n"


class TestHandleGotSelectionPagination:
    """handle_got_selection 翻页测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.handler_context")
    @patch("bot.plugins._search_utils.present_candidates", new_callable=AsyncMock)
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", new_callable=AsyncMock)
    async def test_next_trigger_advances_page(
        self,
        mock_bypass: AsyncMock,
        mock_get_selection: MagicMock,
        mock_remove_selection: MagicMock,
        mock_present: AsyncMock,
        mock_ctx: MagicMock,
    ) -> None:
        """回复 n 且有下一页时，page_index +1 并重渲染。"""
        from contextlib import contextmanager

        from bot.plugins._search_utils import handle_got_selection, PresentOptions

        mock_bypass.return_value = False
        mock_get_selection.return_value = MagicMock()  # selection 有效
        all_results = [
            _make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 26)
        ]
        matcher = _make_matcher()
        matcher.state = {
            "all_results": all_results,
            "page_index": 0,
            "total_pages": 3,
            "candidates": all_results[0:10],
        }
        event = _make_event("111", message_id=42, message_type="group", group_id=67890)
        event.get_plaintext.return_value = "n"
        msg = MagicMock()
        msg.extract_plain_text.return_value = "n"
        opts = PresentOptions(
            show_similarity=True, similarity_scale="score", next_trigger="n"
        )

        @contextmanager
        def _ctx(s, m):
            yield

        mock_ctx.side_effect = _ctx

        await handle_got_selection(
            _make_bot(), event, matcher, msg, "搜索", options=opts
        )

        assert matcher.state["page_index"] == 1
        mock_present.assert_awaited_once()
        assert mock_present.call_args.kwargs["has_next_page"] is True
        assert len(mock_present.call_args.args[3]) == 10  # 第 2 页 10 条
        # 翻页在 got 内，必须用 reject 重新等待，否则 matcher 结束
        assert mock_present.call_args.kwargs["use_reject"] is True

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.handler_context")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", new_callable=AsyncMock)
    async def test_next_trigger_on_last_page_rejects(
        self,
        mock_bypass: AsyncMock,
        mock_get_selection: MagicMock,
        mock_ctx: MagicMock,
    ) -> None:
        """末页回复 n 时在群聊中以 reply 形式 reject，page_index 不变。"""
        from contextlib import contextmanager

        from bot.plugins._search_utils import handle_got_selection, PresentOptions

        mock_bypass.return_value = False
        mock_get_selection.return_value = MagicMock()
        all_results = [
            _make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 4)
        ]
        matcher = _make_matcher()
        matcher.state = {
            "all_results": all_results,
            "page_index": 0,
            "total_pages": 1,
            "candidates": all_results,
        }
        event = _make_event("111", message_id=42, message_type="group", group_id=67890)
        event.get_plaintext.return_value = "n"
        msg = MagicMock()
        msg.extract_plain_text.return_value = "n"
        opts = PresentOptions(next_trigger="n")

        @contextmanager
        def _ctx(s, m):
            yield

        mock_ctx.side_effect = _ctx

        await handle_got_selection(
            _make_bot(), event, matcher, msg, "搜索", options=opts
        )

        matcher.reject.assert_awaited_once()
        rejected = matcher.reject.call_args[0][0]
        assert isinstance(rejected, Message)
        assert rejected[0].type == "reply"
        assert rejected[0].data["id"] == "42"
        assert "没有更多结果了" in extract_message_text(rejected)
        assert matcher.state["page_index"] == 0

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", new_callable=AsyncMock)
    async def test_valid_selection_sends_image_without_similarity(
        self,
        mock_bypass: AsyncMock,
        mock_get_selection: MagicMock,
        mock_remove_selection: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """有效编号选中后发图 + 元数据行（不含相似度）。

        本测试不 patch handler_context，走真实 session_manager 实现
        （与现有 TestGotInterceptBypass 一致）。handler_context 仅存储
        matcher 引用，不会调用 matcher 方法，故与 MagicMock matcher 兼容。
        """
        from bot.plugins._search_utils import handle_got_selection, PresentOptions

        mock_bypass.return_value = False
        mock_get_selection.return_value = MagicMock()
        candidate = _make_search_result(
            entry_id=7, image_path="a.jpg", speaker="小明", similarity=82.0
        )
        matcher = _make_matcher()
        matcher.state = {
            "candidates": [candidate],
            "page_index": 0,
            "total_pages": 1,
            "all_results": [candidate],
        }
        event = _make_event("111")
        event.get_plaintext.return_value = "1"
        msg = MagicMock()
        msg.extract_plain_text.return_value = "1"
        opts = PresentOptions(
            show_similarity=True, similarity_scale="score", next_trigger="n"
        )

        await handle_got_selection(
            _make_bot(), event, matcher, msg, "搜索", options=opts
        )

        matcher.send.assert_awaited_once()
        matcher.finish.assert_awaited_once()
        finished_text = extract_message_text(matcher.finish.call_args[0][0])
        assert "0.7，全局，小明" in finished_text
        assert "%" not in finished_text


class TestExecuteCombinedSearch:
    """execute_combined_search 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    @patch("bot.plugins._search_utils.dispatch_search_results", new_callable=AsyncMock)
    @patch("bot.plugins._search_utils.session_manager")
    async def test_delegates_to_search_combined(
        self,
        mock_session: MagicMock,
        mock_dispatch: AsyncMock,
        mock_get_im: MagicMock,
    ) -> None:
        """应调用 index_manager.search_combined 并分发结果。"""
        from bot.plugins._search_utils import execute_combined_search

        mock_get_im.return_value.search_combined_for_scope = AsyncMock(
            return_value=[_make_search_result()]
        )
        event = _make_event("123")

        await execute_combined_search(
            MagicMock(),
            event,
            _make_matcher(),
            keyword="加班",
            speakers=["小明"],
            tags=["吐槽"],
        )

        mock_get_im.return_value.search_combined_for_scope.assert_awaited_once_with(
            ChatScope.from_event(event), "加班", ["小明"], ["吐槽"]
        )
        mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    @patch("bot.plugins._search_utils.session_manager")
    async def test_selection_timeout_replies_without_combined_search(
        self,
        mock_session: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """当前合集读取超时时清会话且不执行组合搜索。"""
        manager = mock_get_im.return_value
        manager.search_combined_for_scope = AsyncMock(side_effect=asyncio.TimeoutError)
        event = _make_event("123")
        matcher = _make_matcher()

        from bot.plugins._search_utils import execute_combined_search

        await execute_combined_search(
            MagicMock(), event, matcher, keyword="加班", speakers=[], tags=[]
        )

        manager.search_combined_for_scope.assert_awaited_once()
        mock_session.deactivate_chat.assert_called_once_with(
            ChatScope.from_event(event)
        )
        assert "索引更新较慢" in extract_message_text(matcher.finish.await_args.args[0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    @patch("bot.plugins._search_utils.session_manager")
    async def test_timeout_replies_slow(
        self,
        mock_session: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """读锁超时回复「索引更新较慢」。"""
        import asyncio
        from bot.plugins._search_utils import execute_combined_search

        mock_get_im.return_value.search_combined_for_scope = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        event = _make_event("123")
        matcher = _make_matcher()

        await execute_combined_search(
            MagicMock(),
            event,
            matcher,
            keyword="加班",
            speakers=[],
            tags=[],
        )

        matcher.finish.assert_awaited_once()
        assert "索引更新较慢" in extract_message_text(matcher.finish.call_args[0][0])

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager", side_effect=RuntimeError())
    @patch("bot.plugins._search_utils.session_manager")
    async def test_not_ready_replies(
        self,
        mock_session: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """IndexManager 未就绪回复「服务未就绪」。"""
        from bot.plugins._search_utils import execute_combined_search

        event = _make_event("123")
        matcher = _make_matcher()

        await execute_combined_search(
            MagicMock(),
            event,
            matcher,
            keyword="加班",
            speakers=[],
            tags=[],
        )

        matcher.finish.assert_awaited_once_with("服务未就绪，请稍后再试")
