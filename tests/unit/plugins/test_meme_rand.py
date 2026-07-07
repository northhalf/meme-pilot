"""/rand 命令插件单元测试。"""

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

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_rand
    from bot.plugins.meme_rand import got_rand_selection, handle_rand


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "/rand 加班") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.message_type = "private"
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(state: dict | None = None) -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 0.0,
) -> SearchResult:
    """创建模拟的 SearchResult。"""
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
    )


def _make_message(text: str = "1") -> MagicMock:
    """创建模拟的 Message 对象（Arg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


# ===========================================================================
# handle_rand 测试
# ===========================================================================


class TestHandleRandAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand, "is_authorized", return_value=True)
    @patch.object(meme_rand, "dispatch_search_results", new_callable=AsyncMock)
    async def test_authorized_user_proceeds(
        self, mock_dispatch: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """授权用户应正常调用 dispatch_search_results。"""
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=[_make_search_result()])
            await handle_rand(_make_bot(), _make_event(), _make_matcher())
            mock_dispatch.assert_awaited_once()


class TestHandleRandDelegation:
    """参数委托测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_keyword_passed_to_random_search(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """有关键词时应传给 random_search。"""
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_random = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.random_search = mock_random

            await handle_rand(_make_bot(), _make_event(text="/rand 加班"), _make_matcher())

            mock_random.assert_awaited_once_with("加班")

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_empty_keyword_passed_as_none(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """无关键词时应以 None 调用 random_search。"""
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_random = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.random_search = mock_random

            await handle_rand(_make_bot(), _make_event(text="/rand"), _make_matcher())

            mock_random.assert_awaited_once_with(None)


class TestHandleRandEmptyResults:
    """空结果处理测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand.session_manager, "deactivate_chat")
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_keyword_no_match_replies_no_results(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """关键词无匹配时应回复没有匹配到。"""
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_rand(_make_bot(), _make_event(text="/rand 火星文"), matcher)

            matcher.finish.assert_awaited_once()
            assert "没有匹配到" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand.session_manager, "deactivate_chat")
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty_dir(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """全库随机但目录为空时应提示目录为空。"""
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_rand(_make_bot(), _make_event(text="/rand"), matcher)

            matcher.finish.assert_awaited_once()
            assert "表情包目录为空" in matcher.finish.call_args[0][0]


# ===========================================================================
# got_rand_selection 测试
# ===========================================================================


class TestGotRandSelection:
    """got_rand_selection 处理函数测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_rand.present_candidates")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_zero_triggers_refresh(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_remove_sel: MagicMock,
        mock_present: AsyncMock,
    ) -> None:
        """回复 0 应换一批并重新展示候选。"""
        mock_get_sel.return_value = MagicMock()
        new_results = [_make_search_result(entry_id=2, text="乙")]
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=new_results)
            matcher = _make_matcher(state={"candidates": [_make_search_result()], "keyword": None})

            await got_rand_selection(
                _make_bot(), _make_event(text="0"), matcher, _make_message("0")
            )

            mock_remove_sel.assert_called_once_with("12345")
            mock_present.assert_awaited_once()
            args = mock_present.call_args
            assert args.kwargs.get("prompt_suffix") == "回复 0 换一批"

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_non_zero_uses_resolve_selection(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_remove_sel: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """回复非 0 编号时应使用 resolve_selection 处理。"""
        result = _make_search_result(entry_id=1, image_path="a.jpg")
        mock_resolve.return_value = result
        mock_get_sel.return_value = MagicMock()
        candidates = [result]
        matcher = _make_matcher(state={"candidates": candidates})

        with patch("bot.plugins.meme_rand.MessageSegment") as mock_segment:
            await got_rand_selection(
                _make_bot(), _make_event(text="1"), matcher, _make_message("1")
            )

            mock_remove_sel.assert_called_once_with("12345")
            matcher.send.assert_awaited_once()
            matcher.finish.assert_awaited_once()
