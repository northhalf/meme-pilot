"""/query 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_query
    from bot.plugins.meme_query import _parse_args, handle_query


def _make_event(user_id: str = "12345", text: str = "/query 加班 @小明 #吐槽") -> MagicMock:
    event = MagicMock()
    event.message_type = "private"
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
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


def _make_args(text: str) -> MagicMock:
    args = MagicMock()
    args.extract_plain_text.return_value = text
    return args


class TestParseArgs:
    def test_keyword_speaker_tags(self) -> None:
        kw, sp, tg = _parse_args("加班心累 @小明 #吐槽 #加班")
        assert kw == "加班心累"
        assert sp == ["小明"]
        assert tg == ["吐槽", "加班"]

    def test_multiple_speakers_or(self) -> None:
        kw, sp, tg = _parse_args("@小明 @小红")
        assert kw == ""
        assert sp == ["小明", "小红"]
        assert tg == []

    def test_multiple_tags_and(self) -> None:
        kw, sp, tg = _parse_args("#吐槽 #深夜")
        assert kw == ""
        assert sp == []
        assert tg == ["吐槽", "深夜"]

    def test_keyword_only(self) -> None:
        kw, sp, tg = _parse_args("加班")
        assert kw == "加班"
        assert sp == []
        assert tg == []

    def test_lone_prefix_ignored(self) -> None:
        kw, sp, tg = _parse_args("加班 # @")
        assert kw == "加班"
        assert sp == []
        assert tg == []

    def test_keyword_with_spaces(self) -> None:
        kw, sp, tg = _parse_args("加班 心累 @小明")
        assert kw == "加班 心累"
        assert sp == ["小明"]


class TestHandleQueryAuth:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query, "is_authorized", return_value=True)
    @patch.object(meme_query, "execute_combined_search", new_callable=AsyncMock)
    async def test_authorized_proceeds(
        self, mock_exec: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        await handle_query(
            _make_bot(), _make_event(), _make_matcher(), _make_args("加班 @小明 #吐槽")
        )
        mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_query, "log_unauthorized")
    @patch.object(meme_query, "is_authorized", return_value=False)
    async def test_unauthorized_silent(
        self, mock_auth: MagicMock, mock_log: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(), matcher, _make_args("加班"))
        matcher.finish.assert_awaited_once_with(None)


class TestHandleQueryEmptyArgs:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query.session_manager, "deactivate_chat")
    @patch.object(meme_query, "is_authorized", return_value=True)
    async def test_all_empty_replies_usage(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(text="/query"), matcher, _make_args(""))
        matcher.finish.assert_awaited_once_with(
            "/query <关键词> [@说话人] [#标签...]"
        )


class TestHandleQueryOptions:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query, "is_authorized", return_value=True)
    @patch.object(meme_query, "execute_combined_search", new_callable=AsyncMock)
    async def test_keyword_uses_kw_options(
        self, mock_exec: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(), matcher, _make_args("加班"))
        opts = mock_exec.call_args.kwargs["options"]
        assert opts.show_similarity is True
        assert opts.similarity_scale == "score"
        assert matcher.state["query_options"].show_similarity is True

    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query, "is_authorized", return_value=True)
    @patch.object(meme_query, "execute_combined_search", new_callable=AsyncMock)
    async def test_no_keyword_uses_filter_options(
        self, mock_exec: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(
            _make_bot(), _make_event(text="/query @小明"), matcher, _make_args("@小明")
        )
        opts = mock_exec.call_args.kwargs["options"]
        assert opts.show_similarity is False
        assert matcher.state["query_options"].show_similarity is False


class TestHandleQuerySession:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=False)
    @patch.object(meme_query, "is_authorized", return_value=True)
    async def test_busy_replies_cancel(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(), matcher, _make_args("加班"))
        matcher.finish.assert_awaited_once_with("已有命令在处理中，请先 /cancel")


class TestHelpTextContainsQuery:
    def test_help_text_includes_query(self) -> None:
        from bot.plugins._help_text import HELP_TEXT

        assert "/query" in HELP_TEXT
        assert "@说话人" in HELP_TEXT
        assert "#标签" in HELP_TEXT
