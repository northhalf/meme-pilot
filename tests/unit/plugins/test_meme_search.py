"""/search 命令插件单元测试。"""

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


def _make_matcher(*, state: dict | None = None) -> MagicMock:
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
    similarity: float = 90.0,
) -> SearchResult:
    """创建模拟的 SearchResult。"""

    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
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
    @patch.object(meme_search.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_exec: AsyncMock, mock_activate: MagicMock
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
# 会话拒绝
# ---------------------------------------------------------------------------


class TestHandleSearchSessionRejection:
    """会话拒绝测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search.session_manager, "activate_chat", return_value=False)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_active_session_rejected(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_exec: AsyncMock,
    ) -> None:
        """活跃会话存在时应拒绝新命令。"""
        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once_with("已有命令在处理中，请先 /cancel")
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_inactive_session_proceeds(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_exec: AsyncMock,
    ) -> None:
        """无活跃会话时应正常执行搜索。"""
        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(), matcher)

        matcher.send.assert_not_awaited()
        mock_exec.assert_awaited_once()


# ---------------------------------------------------------------------------
# 空关键词
# ---------------------------------------------------------------------------


class TestHandleSearchEmptyKeyword:
    """空关键词测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_empty_keyword_replies_usage(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """/search 无参数时应回复用法提示。"""
        matcher = _make_matcher()
        await handle_search(_make_bot(), _make_event(text="/search"), matcher)

        matcher.finish.assert_awaited_once()
        assert "/search" in matcher.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 委托 execute_search
# ---------------------------------------------------------------------------


class TestHandleSearchDelegation:
    """测试 handle_search 正确委托 execute_search。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_execute_search_called_with_correct_args(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_exec: AsyncMock,
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

    # -----------------------------------------------------------------------
    # 旁路拦截
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=True)
    async def test_cancel_intercepted(
        self,
        mock_bypass: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """/cancel 应被 intercept。"""
        matcher = _make_matcher()
        await got_selection(
            _make_bot(), _make_event(text="/cancel"), matcher, _make_message("")
        )

        mock_bypass.assert_called_once()
        matcher.reject.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=True)
    async def test_help_intercepted(
        self,
        mock_bypass: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """/help 应被 intercept（不调用 deactivate_chat）。"""
        matcher = _make_matcher()
        await got_selection(
            _make_bot(), _make_event(text="/help"), matcher, _make_message("")
        )

        matcher.finish.assert_not_awaited()
        matcher.reject.assert_not_awaited()

    # -----------------------------------------------------------------------
    # 选择会话过期
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.deactivate_chat")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection", return_value=None)
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_selection_expired(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_deactivate: MagicMock,
    ) -> None:
        """选择会话过期时应提示重新搜索。"""
        matcher = _make_matcher()
        await got_selection(_make_bot(), _make_event(), matcher, _make_message("1"))

        mock_deactivate.assert_called_once_with("12345")
        matcher.finish.assert_awaited_once_with("选择已过期，请重新搜索")

    # -----------------------------------------------------------------------
    # 有效选择
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_valid_choice_sends_image(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_remove_sel: MagicMock,
        mock_handle: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """有效编号选择应发送对应图片并清理选择会话。"""
        result = _make_search_result(image_path="a.jpg")
        mock_handle.return_value = result
        mock_get_sel.return_value = MagicMock()
        candidates = [result]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(
            _make_bot(), _make_event(text="1"), matcher, _make_message("1")
        )

        mock_remove_sel.assert_called_once_with("12345")
        matcher.send.assert_awaited_once()
        matcher.finish.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_valid_choice_sends_correct_image(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """选择第 2 个结果应发送对应图片路径。"""
        result = _make_search_result(entry_id=2, image_path="b.jpg", text="乙")
        mock_handle.return_value = result
        mock_get_sel.return_value = MagicMock()
        candidates = [
            _make_search_result(entry_id=1, image_path="a.jpg", text="甲"),
            result,
        ]
        matcher = _make_matcher(state={"candidates": candidates})

        with patch("bot.plugins._search_utils.MessageSegment") as mock_segment:
            await got_selection(
                _make_bot(), _make_event(text="2"), matcher, _make_message("2")
            )

            call_args = mock_segment.image.call_args[0][0]
            assert "b.jpg" in str(call_args)

    # -----------------------------------------------------------------------
    # 无效选择
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_invalid_text_rejects(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """非数字输入应 reject 提示重输。"""
        mock_handle.return_value = "无效编号，请回复 1-1 之间的数字"
        mock_get_sel.return_value = MagicMock()
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(
            _make_bot(), _make_event(text="abc"), matcher, _make_message("abc")
        )

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_out_of_range_low_rejects(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """编号小于 1 时应 reject。"""
        mock_handle.return_value = "无效编号，请回复 1-2 之间的数字"
        mock_get_sel.return_value = MagicMock()
        candidates = [_make_search_result(), _make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(
            _make_bot(), _make_event(text="0"), matcher, _make_message("0")
        )

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_out_of_range_high_rejects(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """编号超出范围时应 reject。"""
        mock_handle.return_value = "无效编号，请回复 1-1 之间的数字"
        mock_get_sel.return_value = MagicMock()
        candidates = [_make_search_result()]
        matcher = _make_matcher(state={"candidates": candidates})

        await got_selection(
            _make_bot(), _make_event(text="5"), matcher, _make_message("5")
        )

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_empty_candidates_rejects_with_error(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_handle: MagicMock,
    ) -> None:
        """candidates 为空时 resolve_selection 返回错误消息，应 reject。"""
        mock_handle.return_value = "搜索状态异常，请重新搜索"
        mock_get_sel.return_value = MagicMock()
        matcher = _make_matcher(state={})

        await got_selection(
            _make_bot(), _make_event(text="1"), matcher, _make_message("1")
        )

        mock_handle.assert_called_once()
        matcher.reject.assert_awaited_once()
        assert "搜索状态异常" in matcher.reject.call_args[0][0]
