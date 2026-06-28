"""/ai 命令插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
):
    from bot.plugins import meme_ai
    from bot.plugins.meme_ai import _do_match, handle_ai


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


def _make_index_manager(
    *, is_locked: bool = False, entry_count: int = 10
) -> MagicMock:
    """创建模拟的 IndexManager。"""
    im = MagicMock()
    im.is_locked = is_locked
    im.entry_count = entry_count
    return im


_UNSET = object()


def _make_ai_matcher(
    *, result: object = _UNSET, side_effect: Exception | None = None
) -> MagicMock:
    """创建模拟的 AIMatcher。"""
    from bot.engine.ai_matcher import AIMatchResult

    am = MagicMock()
    if side_effect is not None:
        am.match = AsyncMock(side_effect=side_effect)
    elif result is not _UNSET:
        am.match = AsyncMock(return_value=result)
    else:
        am.match = AsyncMock(
            return_value=AIMatchResult(
                entry_id="1",
                filename="加班心累.jpg",
                text="加班到心累",
                similarity=0.95,
                source="rerank",
            )
        )
    return am



# ----------
# 测试：授权校验
# ---------------------------------------------------------------------------


class TestHandleAiAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """授权用户应正常执行。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher()

        await handle_ai(_make_bot(), _make_event(), matcher)

        mock_get_ai.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        bot = _make_bot()

        await handle_ai(bot, _make_event("999"), matcher)

        mock_get_im.assert_not_called()
        mock_get_ai.assert_not_called()
        matcher.finish.assert_not_awaited()
        bot.send.assert_not_awaited()


    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_group_chat_rejected(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
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
        mock_get_ai.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：索引锁
# ---------------------------------------------------------------------------


class TestHandleAiLock:
    """索引锁测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_lock_contention_replies(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """索引锁占用时应回复提示。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager(is_locked=True)

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "索引正在更新" in matcher.finish.call_args[0][0]
        mock_get_ai.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：描述为空
# ---------------------------------------------------------------------------


class TestHandleAiEmptyDesc:
    """描述为空测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_empty_description_replies_usage(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
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
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_match_sends_image(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ai: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """匹配成功时应发送图片。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher()

        await handle_ai(_make_bot(), _make_event("12345", "/ai 加班心累"), matcher)

        matcher.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_ai, "MessageSegment")
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_image_path_correct(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ai: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """图片路径应为 file:/// URI 格式。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher()

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
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_none_result_replies_no_match(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """AIMatcher 返回 None 时应回复无匹配。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher(result=None)

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "没有找到" in matcher.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：服务异常
# ---------------------------------------------------------------------------


class TestHandleAiServiceError:
    """服务异常测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_value_error_replies_unavailable(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """ValueError（embedding 无效）时应回复服务不可用。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher(
            side_effect=ValueError("embedding 为空")
        )

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "AI 服务暂时不可用" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_generic_error_replies_unavailable(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """通用异常时应回复服务不可用。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher(
            side_effect=RuntimeError("API 超时")
        )

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "AI 服务暂时不可用" in matcher.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：空索引
# ---------------------------------------------------------------------------


class TestHandleAiEmptyIndex:
    """空索引测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """索引为空时应回复表情包目录为空。"""
        matcher = _make_matcher()
        mock_get_im.return_value = _make_index_manager(entry_count=0)

        await handle_ai(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        assert "表情包目录为空" in matcher.finish.call_args[0][0]
        mock_get_ai.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：_do_match 单元测试
# ---------------------------------------------------------------------------


class TestDoMatch:
    """_do_match 函数单元测试。"""

    @pytest.mark.asyncio
    async def test_success_returns_result(self) -> None:
        """匹配成功时返回 AIMatchResult。"""
        from bot.engine.ai_matcher import AIMatchResult

        expected = AIMatchResult(
            entry_id="1",
            filename="test.jpg",
            text="测试",
            similarity=0.9,
            source="embedding",
        )
        am = _make_ai_matcher(result=expected)

        result = await _do_match(am, "测试描述")

        assert result is expected
        am.match.assert_awaited_once_with("测试描述")

    @pytest.mark.asyncio
    async def test_none_returns_error_text(self) -> None:
        """无候选时返回错误提示文本。"""
        am = _make_ai_matcher(result=None)

        result = await _do_match(am, "找不到的描述")

        assert isinstance(result, str)
        assert "没有找到" in result

    @pytest.mark.asyncio
    async def test_value_error_returns_error_text(self) -> None:
        """ValueError 时返回服务不可用提示。"""
        am = _make_ai_matcher(side_effect=ValueError("embedding 失败"))

        result = await _do_match(am, "测试")

        assert isinstance(result, str)
        assert "AI 服务暂时不可用" in result

    @pytest.mark.asyncio
    async def test_generic_error_returns_error_text(self) -> None:
        """通用异常时返回服务不可用提示。"""
        am = _make_ai_matcher(side_effect=RuntimeError("网络错误"))

        result = await _do_match(am, "测试")

        assert isinstance(result, str)
        assert "AI 服务暂时不可用" in result
