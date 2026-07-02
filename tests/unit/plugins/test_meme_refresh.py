"""/refresh 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.index_manager import RefreshInProgressError

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免需要 NoneBot2 完整初始化。
# 用 MagicMock 的 handle() 返回一个透传 decorator（原函数不变），
# 这样 handle_refresh 仍然是真实的 async 函数。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.on_message", return_value=MagicMock(handle=lambda fn: fn)),
):
    from bot.plugins import meme_refresh
    from bot.plugins.meme_refresh import handle_refresh


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
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
    *,
    entry_count: int = 5,
    sync_result: object = None,
    refresh_side_effect: Exception | None = None,
) -> MagicMock:
    """创建模拟的 IndexManager。"""
    from bot.engine.index_manager import SyncResult

    im = MagicMock()
    im.entry_count = entry_count
    result = (
        sync_result
        if sync_result is not None
        else SyncResult(added=2, deleted=0)
    )
    if refresh_side_effect is not None:
        im.refresh = AsyncMock(side_effect=refresh_side_effect)
    else:
        im.refresh = AsyncMock(return_value=result)
    return im



# ----------
# 测试：授权校验
# ---------------------------------------------------------------------------


class TestHandleRefreshAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_authorized_user_proceeds(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """授权用户应触发同步。"""
        matcher = _make_matcher()
        im = _make_index_manager()
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("111"), matcher)

        im.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=False)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_unauthorized_user_ignored(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        bot = _make_bot()

        await handle_refresh(bot, _make_event("999"), matcher)

        mock_get_im.assert_not_called()
        matcher.finish.assert_not_called()
        bot.send.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_group_chat_rejected(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """群聊中调用 /refresh 应回复仅限私聊提示。"""
        matcher = _make_matcher()
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.message_type = "group"

        await handle_refresh(_make_bot(), event, matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "仅限私聊" in call_args
        mock_get_im.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：索引锁
# ---------------------------------------------------------------------------


class TestHandleRefreshLock:
    """刷新冲突测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_refresh_in_progress_replies(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """已有刷新任务运行时应回复提示。"""
        matcher = _make_matcher()
        im = _make_index_manager(refresh_side_effect=RefreshInProgressError("刷新中"))
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        matcher.finish.assert_awaited_once_with("已有刷新任务在进行中，请稍后再试")
        im.refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# 测试：同步执行
# ---------------------------------------------------------------------------


class TestHandleRefreshSync:
    """同步执行测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_sends_progress_message(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """应先发送进度提示消息。"""
        matcher = _make_matcher()
        im = _make_index_manager()
        mock_get_im.return_value = im
        bot = _make_bot()

        await handle_refresh(bot, _make_event("12345"), matcher)

        bot.send.assert_awaited_once()
        call_args = bot.send.call_args[0]
        assert "正在刷新索引" in call_args[1]

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_sync_exception_replies_error(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """refresh() 异常时应回复错误提示。"""
        matcher = _make_matcher()
        im = _make_index_manager(refresh_side_effect=RuntimeError("网络错误"))
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "失败" in call_args


# ---------------------------------------------------------------------------
# 测试：结果回复
# ---------------------------------------------------------------------------


class TestHandleRefreshResult:
    """结果回复测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_empty_memes_replies_empty(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """memes/ 为空时应回复空目录提示。"""
        from bot.engine.index_manager import SyncResult

        matcher = _make_matcher()
        im = _make_index_manager(entry_count=0, sync_result=SyncResult())
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "表情包目录为空" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_normal_result_replies_summary(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """正常同步后应回复摘要。"""
        from bot.engine.index_manager import SyncResult

        matcher = _make_matcher()
        result = SyncResult(added=3, deleted=1, deduped=0, no_text_moved=0)
        im = _make_index_manager(entry_count=7, sync_result=result)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "索引刷新完成" in call_args
        assert "新增: 3" in call_args
        assert "删除: 1" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_failed_files_shown(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """有失败文件时应列出。"""
        from bot.engine.index_manager import SyncResult

        matcher = _make_matcher()
        result = SyncResult(added=1, failed=["bad.jpg", "corrupt.png"])
        im = _make_index_manager(entry_count=3, sync_result=result)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        call_args = matcher.finish.call_args[0][0]
        assert "失败: 2" in call_args
        assert "bad.jpg" in call_args
        assert "corrupt.png" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_failed_files_max_10(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """失败文件最多显示前 10 个。"""
        from bot.engine.index_manager import SyncResult

        matcher = _make_matcher()
        failed = [f"f{i}.jpg" for i in range(15)]
        result = SyncResult(added=0, failed=failed)
        im = _make_index_manager(entry_count=5, sync_result=result)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        call_args = matcher.finish.call_args[0][0]
        assert "f0.jpg" in call_args
        assert "f9.jpg" in call_args
        assert "f14.jpg" not in call_args


# ---------------------------------------------------------------------------
# 测试：初始化错误
# ---------------------------------------------------------------------------


class TestHandleRefreshInitError:
    """初始化错误测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_not_initialized_replies_error(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """IndexManager 未初始化时应回复错误提示。"""
        matcher = _make_matcher()
        mock_get_im.side_effect = RuntimeError("未初始化")

        await handle_refresh(_make_bot(), _make_event("12345"), matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "未就绪" in call_args
