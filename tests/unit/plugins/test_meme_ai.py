"""/ai 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.ai_matcher import AIMatchResult

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
):
    from bot.plugins import meme_ai
    from bot.plugins.meme_ai import handle_ai


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "/ai 加班心累") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher() -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    return matcher


_UNSET = object()


def _make_index_manager(
    *, ai_result: object = _UNSET, ai_side_effect: Exception | None = None
) -> MagicMock:
    """创建模拟的 IndexManager。"""
    im = MagicMock()
    if ai_side_effect is not None:
        im.ai_match = AsyncMock(side_effect=ai_side_effect)
    elif ai_result is not _UNSET:
        im.ai_match = AsyncMock(return_value=ai_result)
    else:
        im.ai_match = AsyncMock(
            return_value=AIMatchResult(
                entry_id=1,
                image_path="加班心累.jpg",
                text="加班到心累",
                similarity=0.95,
                source="rerank",
                speaker="小明",
                tags=["吐槽"],
            )
        )
    return im


# ---------------------------------------------------------------------------
# 测试：授权校验
# ---------------------------------------------------------------------------


class TestHandleAiAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """授权用户应正常执行。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()

        await handle_ai(_make_bot(), _make_event(), matcher)

        mock_get_im.return_value.ai_match.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        bot = _make_bot()

        await handle_ai(bot, _make_event("999"), matcher)

        mock_get_im.assert_not_called()
        matcher.finish.assert_not_awaited()
        bot.send.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_group_chat_rejected(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """群聊中调用 /ai 应回复仅限私聊提示。"""
        matcher = _make_matcher()
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.get_plaintext.return_value = "/ai 加班心累"
        event.message_type = "group"

        await handle_ai(_make_bot(), event, matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "仅限私聊" in call_args
        mock_get_im.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：读锁超时
# ---------------------------------------------------------------------------


class TestHandleAiTimeout:
    """读锁超时测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_timeout_replies_slow_index(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """等待读锁超时应回复提示。"""
        matcher = _make_matcher()
        import asyncio

        mock_get_im.return_value = _make_index_manager(
            ai_side_effect=asyncio.TimeoutError()
        )

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "索引更新较慢" in matcher.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：描述为空
# ---------------------------------------------------------------------------


class TestHandleAiEmptyDesc:
    """描述为空测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_empty_description_replies_usage(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """/ai 无参数时应回复用法提示。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()

        await handle_ai(_make_bot(), _make_event(text="/ai"), matcher)

        matcher.finish.assert_awaited_once()
        assert "/ai" in matcher.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：匹配成功
# ---------------------------------------------------------------------------


class TestHandleAiSuccess:
    """匹配成功测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "MessageSegment")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_match_sends_image_then_metadata(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """匹配成功时应先发送图片，再 finish 元数据行。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()

        await handle_ai(_make_bot(), _make_event("12345", "/ai 加班心累"), matcher)

        assert matcher.send.await_count == 2
        matcher.finish.assert_awaited_once()
        finished_text = matcher.finish.call_args[0][0]
        assert "1" in finished_text
        assert "小明" in finished_text
        assert "吐槽" in finished_text

    @pytest.mark.asyncio
    @patch.object(meme_ai, "MessageSegment")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_image_path_correct(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """图片路径应为 file:/// URI 格式。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()

        await handle_ai(_make_bot(), _make_event("12345", "/ai 加班心累"), matcher)

        call_args = mock_segment.image.call_args[0][0]
        assert "memes" in str(call_args)
        assert str(call_args).startswith("file:///")


# ---------------------------------------------------------------------------
# 测试：无候选
# ---------------------------------------------------------------------------


class TestHandleAiNoMatch:
    """无候选测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_none_result_replies_no_match(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """ai_match 返回 None 时应回复无匹配。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager(ai_result=None)

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "没有找到" in matcher.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：服务异常
# ---------------------------------------------------------------------------


class TestHandleAiServiceError:
    """服务异常测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_value_error_replies_unavailable(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """ValueError（embedding 无效）时应回复服务不可用。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager(
            ai_side_effect=ValueError("embedding 为空")
        )

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "AI 服务暂时不可用" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_generic_error_replies_unavailable(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """通用异常时应回复服务不可用。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager(
            ai_side_effect=RuntimeError("API 超时")
        )

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "AI 服务暂时不可用" in matcher.finish.call_args[0][0]
