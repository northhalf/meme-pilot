"""/rand 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.types import CollectionSelection, SearchResult
from bot.session import ChatScope
from tests.conftest import _assert_has_reply, _assert_no_reply, extract_message_text


# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# got() 返回透传 decorator。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import rand
    from bot.plugins.rand import got_rand_selection, handle_rand


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(
    user_id: str = "12345", text: str = "/rand 加班", message_type: str = "private"
) -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.message_type = message_type
    event.message_id = 123456 if message_type == "group" else 1
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    return event


def _make_scope(user_id: str = "12345") -> ChatScope:
    """创建模拟的私聊 ChatScope。"""
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))


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
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand, "is_authorized", return_value=True)
    @patch.object(rand, "dispatch_search_results", new_callable=AsyncMock)
    async def test_authorized_user_proceeds(
        self, mock_dispatch: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """授权用户应正常调用 dispatch_search_results。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(
                    CollectionSelection(0, "全部合集"),
                    [_make_search_result()],
                )
            )
            await handle_rand(_make_bot(), _make_event(), _make_matcher())
            mock_dispatch.assert_awaited_once()


class TestHandleRandDelegation:
    """参数委托测试。"""

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_current_collection_is_snapshotted(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """首次搜索读取一次合集并把过滤条件保存为会话快照。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            manager = mock_get_im.return_value
            manager.random_search_for_scope = AsyncMock(
                return_value=(
                    CollectionSelection(1, "新三国"),
                    [_make_search_result()],
                )
            )
            matcher = _make_matcher()
            event = _make_event(text="/rand 加班")

            await handle_rand(_make_bot(), event, matcher)

            manager.random_search_for_scope.assert_awaited_once_with(
                ChatScope.from_event(event), "加班"
            )
            assert matcher.state["collection_selection"] == CollectionSelection(
                1, "新三国"
            )

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_keyword_passed_to_random_search(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """有关键词时应传给 random_search。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_random = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(
                    CollectionSelection(0, "全部合集"),
                    mock_random.return_value,
                )
            )

            await handle_rand(
                _make_bot(), _make_event(text="/rand 加班"), _make_matcher()
            )

            mock_get_im.return_value.random_search_for_scope.assert_awaited_once_with(
                ChatScope.from_event(_make_event()), "加班"
            )

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_empty_keyword_passed_as_none(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        """无关键词时应以 None 调用 random_search。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_random = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(
                    CollectionSelection(0, "全部合集"),
                    mock_random.return_value,
                )
            )

            await handle_rand(_make_bot(), _make_event(text="/rand"), _make_matcher())

            mock_get_im.return_value.random_search_for_scope.assert_awaited_once_with(
                ChatScope.from_event(_make_event(text="/rand")), None
            )


class TestHandleRandSelectionTimeout:
    """当前合集读取超时测试。"""

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand.session_manager, "deactivate_chat")
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_timeout_clears_session_without_random_search(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """合集读取超时时统一提示、清会话且不调用随机搜索。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            manager = mock_get_im.return_value
            manager.random_search_for_scope = AsyncMock(side_effect=TimeoutError)
            event = _make_event()
            matcher = _make_matcher()

            await handle_rand(_make_bot(), event, matcher)

            manager.random_search_for_scope.assert_awaited_once()
            mock_deactivate.assert_called_once_with(ChatScope.from_event(event))
            assert "索引更新较慢" in extract_message_text(
                matcher.finish.await_args.args[0]
            )


class TestHandleRandEmptyResults:
    """空结果处理测试。"""

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand.session_manager, "deactivate_chat")
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_keyword_no_match_replies_no_results(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """关键词无匹配时应回复没有匹配到。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(CollectionSelection(0, "全部合集"), [])
            )
            matcher = _make_matcher()

            await handle_rand(_make_bot(), _make_event(text="/rand 火星文"), matcher)

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.call_args[0][0]
            assert "没有匹配到" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand.session_manager, "deactivate_chat")
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_keyword_no_match_replies_no_results_group_reply(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """群聊中关键词无匹配时应带 reply。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(CollectionSelection(0, "全部合集"), [])
            )
            matcher = _make_matcher()

            await handle_rand(
                _make_bot(),
                _make_event(text="/rand 火星文", message_type="group"),
                matcher,
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            _assert_has_reply(reply)
            assert "没有匹配到" in extract_message_text(reply)

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand.session_manager, "deactivate_chat")
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty_dir(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """全库随机但目录为空时应提示目录为空。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(CollectionSelection(0, "全部合集"), [])
            )
            matcher = _make_matcher()

            await handle_rand(_make_bot(), _make_event(text="/rand"), matcher)

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.call_args[0][0]
            assert "表情包目录为空" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand.session_manager, "deactivate_chat")
    @patch.object(rand, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty_dir_group_reply(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """群聊中全库随机但目录为空时应带 reply。"""
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(CollectionSelection(0, "全部合集"), [])
            )
            matcher = _make_matcher()

            await handle_rand(
                _make_bot(), _make_event(text="/rand", message_type="group"), matcher
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            _assert_has_reply(reply)
            assert "表情包目录为空" in extract_message_text(reply)


# ===========================================================================
# got_rand_selection 测试
# ===========================================================================


class TestGotRandSelection:
    """got_rand_selection 处理函数测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.rand.present_candidates")
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
        with patch.object(rand, "get_index_manager") as mock_get_im:
            selection = CollectionSelection(1, "新三国")
            mock_get_im.return_value.random_search_for_scope_snapshot = AsyncMock(
                return_value=new_results
            )
            matcher = _make_matcher(
                state={
                    "candidates": [_make_search_result()],
                    "keyword": None,
                    "collection_selection": selection,
                }
            )

            await got_rand_selection(
                _make_bot(), _make_event(text="0"), matcher, _make_message("0")
            )

            mock_get_im.return_value.random_search_for_scope_snapshot.assert_awaited_once_with(
                _make_scope("12345"), None, selection
            )
            mock_remove_sel.assert_called_once_with(_make_scope("12345"))
            mock_present.assert_awaited_once()
            args = mock_present.call_args
            assert args.kwargs.get("prompt_suffix") == "回复 0 换一批"
            assert args.kwargs.get("has_next_page", False) is False
            # 换一批在 got 内，必须用 reject 重新等待，否则 matcher 结束
            assert args.kwargs.get("use_reject") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message_type", ["private", "group"])
    @patch.object(rand.session_manager, "deactivate_chat")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_zero_rejects_expired_collection_snapshot(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_remove_sel: MagicMock,
        mock_deactivate: MagicMock,
        message_type: str,
    ) -> None:
        """刷新回退 scope 后换批应结束会话并提示重新执行 /rand。"""
        mock_get_sel.return_value = MagicMock()
        selection = CollectionSelection(1, "旧合集")
        event = _make_event(text="0", message_type=message_type)
        matcher = _make_matcher(
            state={
                "candidates": [_make_search_result()],
                "keyword": None,
                "collection_selection": selection,
            }
        )
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope_snapshot = AsyncMock(
                side_effect=rand.CollectionSelectionExpiredError()
            )

            await got_rand_selection(_make_bot(), event, matcher, _make_message("0"))

        mock_remove_sel.assert_called_once_with(ChatScope.from_event(event))
        mock_deactivate.assert_called_once_with(ChatScope.from_event(event))
        matcher.finish.assert_awaited_once()
        reply = matcher.finish.await_args.args[0]
        assert "当前合集已变化，请重新发送 /rand" in extract_message_text(reply)
        if message_type == "group":
            _assert_has_reply(reply)
        else:
            _assert_no_reply(reply)

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

        with patch("bot.plugins.rand.MessageSegment") as _:
            await got_rand_selection(
                _make_bot(), _make_event(text="1"), matcher, _make_message("1")
            )

            mock_remove_sel.assert_called_once_with(_make_scope("12345"))
            matcher.send.assert_awaited_once()
            matcher.finish.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.resolve_selection")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.activate_chat")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", return_value=False)
    async def test_selection_expired_group_reply(
        self,
        mock_bypass: MagicMock,
        mock_get_sel: MagicMock,
        mock_activate: MagicMock,
        mock_remove_sel: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """群聊中选择过期时应带 reply 提示。"""
        mock_get_sel.return_value = None
        matcher = _make_matcher(state={"candidates": []})

        await got_rand_selection(
            _make_bot(),
            _make_event(text="1", message_type="group"),
            matcher,
            _make_message("1"),
        )

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        _assert_has_reply(reply)
        assert "选择已过期" in extract_message_text(reply)


# ===========================================================================
# /rand 传参 options 测试
# ===========================================================================


class TestHandleRandOptions:
    """/rand 传参 options 测试。"""

    @pytest.mark.asyncio
    @patch.object(rand.session_manager, "activate_chat", return_value=True)
    @patch.object(rand, "is_authorized", return_value=True)
    @patch.object(rand, "dispatch_search_results", new_callable=AsyncMock)
    async def test_rand_uses_default_options(
        self,
        mock_dispatch: AsyncMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """/rand 不显式传 options（用默认 PresentOptions，不展示相似度/不翻页）。

        mock 无法捕获函数默认参数，故断言不显式传 options；
        默认 PresentOptions（show_similarity=False、next_trigger=None）
        的字段值由 _search_utils 单元测试保证。

        Args:
            mock_dispatch: 替换 dispatch_search_results 的 AsyncMock。
            mock_auth: is_authorized 的 mock。
            mock_activate: activate_chat 的 mock。
        """
        with patch.object(rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search_for_scope = AsyncMock(
                return_value=(
                    CollectionSelection(0, "全部合集"),
                    [_make_search_result()],
                )
            )
            await handle_rand(_make_bot(), _make_event(), _make_matcher())

            mock_dispatch.assert_awaited_once()
            kwargs = mock_dispatch.call_args.kwargs
            # /rand 不显式传 options，使用 dispatch_search_results 的默认 PresentOptions()
            assert "options" not in kwargs
            assert kwargs["prompt_suffix"] == "回复 0 换一批"
