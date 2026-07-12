"""/sim 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.types import SearchResult
from tests.conftest import _assert_has_reply, _assert_no_reply, extract_message_text

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_sim
    from bot.plugins.meme_sim import handle_sim


def _make_event(
    user_id: str = "12345",
    text: str = "/sim 心累的加班",
    message_type: str = "private",
) -> MagicMock:
    event = MagicMock()
    event.message_type = message_type
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    if message_type == "group":
        event.message_id = 123456
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(state: dict | None = None) -> MagicMock:
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
    similarity: float = 0.9,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
    )


def _make_message(text: str = "1") -> MagicMock:
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


class TestHandleSimAuth:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim, "is_authorized", return_value=True)
    @patch.object(meme_sim, "dispatch_search_results", new_callable=AsyncMock)
    async def test_authorized_user_proceeds(
        self, mock_dispatch: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(return_value=[_make_search_result()])
            await handle_sim(_make_bot(), _make_event(), _make_matcher())
            mock_dispatch.assert_awaited_once()


class TestHandleSimDelegation:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_description_passed_to_semantic_search(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_semantic = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.semantic_search = mock_semantic

            await handle_sim(_make_bot(), _make_event(text="/sim 心累的加班"), _make_matcher())

            mock_semantic.assert_awaited_once_with("心累的加班", limit=None)

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_empty_description_replies_usage(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        matcher = _make_matcher()
        await handle_sim(_make_bot(), _make_event(text="/sim"), matcher)

        matcher.finish.assert_awaited_once()
        msg = matcher.finish.call_args[0][0]
        assert extract_message_text(msg) == "/sim <描述文本>"
        _assert_no_reply(msg)

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_empty_description_replies_usage_group_reply(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """群聊中缺少描述时应带 reply 返回用法。"""
        matcher = _make_matcher()
        await handle_sim(
            _make_bot(), _make_event(text="/sim", message_type="group"), matcher
        )

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        _assert_has_reply(reply)
        assert extract_message_text(reply) == "/sim <描述文本>"


class TestHandleSimEmptyResults:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_no_results_replies_not_found(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_sim(_make_bot(), _make_event(), matcher)

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.call_args[0][0]
            assert "没有找到匹配" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_no_results_replies_not_found_group_reply(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """群聊中无结果时应带 reply 提示。"""
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_sim(
                _make_bot(), _make_event(message_type="group"), matcher
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            _assert_has_reply(reply)
            assert "没有找到匹配" in extract_message_text(reply)


class TestHandleSimErrors:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_timeout_replies_slow(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        import asyncio

        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(side_effect=asyncio.TimeoutError())
            matcher = _make_matcher()

            await handle_sim(_make_bot(), _make_event(), matcher)

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.call_args[0][0]
            assert "索引更新较慢" in extract_message_text(msg)
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_timeout_replies_slow_group_reply(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """群聊中超时时应带 reply 提示。"""
        import asyncio

        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            matcher = _make_matcher()

            await handle_sim(
                _make_bot(), _make_event(message_type="group"), matcher
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            _assert_has_reply(reply)
            assert "索引更新较慢" in extract_message_text(reply)

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_embedding_error_replies_unavailable(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(side_effect=ValueError("零向量"))
            matcher = _make_matcher()

            await handle_sim(_make_bot(), _make_event(), matcher)

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.call_args[0][0]
            assert extract_message_text(msg) == "AI 服务暂时不可用，稍后重试"
            _assert_no_reply(msg)

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_embedding_error_replies_unavailable_group_reply(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """群聊中 AI 异常时应带 reply 提示。"""
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(
                side_effect=ValueError("零向量")
            )
            matcher = _make_matcher()

            await handle_sim(
                _make_bot(), _make_event(message_type="group"), matcher
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            _assert_has_reply(reply)
            assert extract_message_text(reply) == "AI 服务暂时不可用，稍后重试"


class TestHandleSimOptions:
    """/sim 传参 options 测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim, "is_authorized", return_value=True)
    @patch.object(meme_sim, "dispatch_search_results", new_callable=AsyncMock)
    async def test_sim_passes_ratio_options(
        self,
        mock_dispatch: AsyncMock,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        """/sim 应传 show_similarity=True、scale=ratio、next_trigger=n。

        Args:
            mock_dispatch: 替换 dispatch_search_results 的 AsyncMock。
            mock_auth: is_authorized 的 mock。
            mock_activate: activate_chat 的 mock。
        """
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(
                return_value=[_make_search_result()]
            )

            await handle_sim(_make_bot(), _make_event(), _make_matcher())

            mock_dispatch.assert_awaited_once()
            opts = mock_dispatch.call_args.kwargs["options"]
            assert opts.show_similarity is True
            assert opts.similarity_scale == "ratio"
            assert opts.next_trigger == "n"
