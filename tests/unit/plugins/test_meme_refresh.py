"""/refresh 命令插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    """创建模拟的 PrivateMessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_index_manager(
    *,
    acquire_result: bool = True,
    entry_count: int = 5,
    sync_result: object = None,
) -> MagicMock:
    """创建模拟的 IndexManager。"""
    from bot.engine.index_manager import SyncResult

    im = MagicMock()
    im.acquire_lock.return_value = acquire_result
    im.entry_count = entry_count
    im.sync_with_filesystem = AsyncMock(
        return_value=(
            sync_result if sync_result is not None else SyncResult(added=2, deleted=0)
        )
    )
    return im


def _reset_cmd() -> None:
    """重置 mock_cmd 的 finish 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()
    _mock_cmd.send = AsyncMock()


# ---------------------------------------------------------------------------
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
        _reset_cmd()
        im = _make_index_manager()
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("111"))

        im.acquire_lock.assert_called_once()
        im.sync_with_filesystem.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=False)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_unauthorized_user_ignored(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_cmd()
        bot = _make_bot()

        await handle_refresh(bot, _make_event("999"))

        mock_get_im.assert_not_called()
        _mock_cmd.finish.assert_not_called()
        bot.send.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：索引锁
# ---------------------------------------------------------------------------


class TestHandleRefreshLock:
    """索引锁测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_lock_contention_replies(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """锁占用时应回复提示。"""
        _reset_cmd()
        im = _make_index_manager(acquire_result=False)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        _mock_cmd.finish.assert_awaited_once_with("索引正在更新，请稍后再试")
        im.sync_with_filesystem.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_lock_released_after_sync(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """同步完成后应释放锁。"""
        _reset_cmd()
        im = _make_index_manager()
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        im.release_lock.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_lock_released_on_sync_exception(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """sync_with_filesystem 异常时也应释放锁。"""
        _reset_cmd()
        im = _make_index_manager()
        im.sync_with_filesystem = AsyncMock(side_effect=RuntimeError("API 失败"))
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        im.release_lock.assert_called_once()


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
        _reset_cmd()
        im = _make_index_manager()
        mock_get_im.return_value = im
        bot = _make_bot()

        await handle_refresh(bot, _make_event("12345"))

        bot.send.assert_awaited_once()
        call_args = bot.send.call_args[0]
        assert "正在刷新索引" in call_args[1]

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_sync_exception_replies_error(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """sync_with_filesystem 异常时应回复错误提示。"""
        _reset_cmd()
        im = _make_index_manager()
        im.sync_with_filesystem = AsyncMock(side_effect=RuntimeError("网络错误"))
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
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

        _reset_cmd()
        im = _make_index_manager(entry_count=0, sync_result=SyncResult())
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "表情包目录为空" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_normal_result_replies_summary(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """正常同步后应回复摘要。"""
        from bot.engine.index_manager import SyncResult

        _reset_cmd()
        result = SyncResult(added=3, deleted=1, deduped=0, no_text_moved=0)
        im = _make_index_manager(entry_count=7, sync_result=result)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
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

        _reset_cmd()
        result = SyncResult(added=1, failed=["bad.jpg", "corrupt.png"])
        im = _make_index_manager(entry_count=3, sync_result=result)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        call_args = _mock_cmd.finish.call_args[0][0]
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

        _reset_cmd()
        failed = [f"f{i}.jpg" for i in range(15)]
        result = SyncResult(added=0, failed=failed)
        im = _make_index_manager(entry_count=5, sync_result=result)
        mock_get_im.return_value = im

        await handle_refresh(_make_bot(), _make_event("12345"))

        call_args = _mock_cmd.finish.call_args[0][0]
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
        _reset_cmd()
        mock_get_im.side_effect = RuntimeError("未初始化")

        await handle_refresh(_make_bot(), _make_event("12345"))

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "未就绪" in call_args
