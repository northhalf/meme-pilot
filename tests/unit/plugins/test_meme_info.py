"""/info 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.index_manager import IndexInfo

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_info
    from bot.plugins.meme_info import handle_info


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", message_type: str = "private") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.message_type = message_type
    event.get_user_id.return_value = user_id
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
    return matcher


# ===========================================================================
# handle_info 测试
# ===========================================================================


class TestHandleInfoAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_info, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        bot = _make_bot()

        await handle_info(bot, _make_event("999"), matcher)

        matcher.finish.assert_awaited_once_with(None)
        bot.send.assert_not_awaited()


class TestHandleInfoGroupChat:
    """群聊场景测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_group_chat_allowed(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
    ) -> None:
        """/info 在群聊 @bot 中应正常返回。"""
        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=128,
                speaker_ranking=[("小明", 45), (None, 38), ("老板", 21)],
                status="空闲",
            )
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 512 * 1024 * 1024
        mem_mock.total = 2048 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(message_type="group"), matcher)

        mock_index_manager.info.assert_awaited_once()
        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        assert "表情包数量：128" in reply
        assert "当前机器人状态：空闲" in reply


class TestHandleInfoNormalReply:
    """正常回复内容测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_normal_reply_content(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
    ) -> None:
        """正常 /info 调用应返回完整统计信息。"""
        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=256,
                speaker_ranking=[("Alice", 100), ("Bob", 50)],
                status="正在处理命令",
            )
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 1024 * 1024 * 1024
        mem_mock.total = 4096 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher)

        mock_virtual_memory.assert_called_once()
        mock_cpu_percent.assert_called_once_with(interval=0.1)
        matcher.finish.assert_awaited_once()

        reply = matcher.finish.call_args[0][0]
        assert "表情包数量：256" in reply
        assert "1. Alice 100" in reply
        assert "2. Bob 50" in reply
        assert "排行（前 10）：" in reply
        assert "当前机器人状态：正在处理命令" in reply
        assert "内存占用：1024 MB / 4096 MB (25.0%)" in reply
        assert "CPU占用：12.5%" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_ranking_renders_top_ten(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
    ) -> None:
        """speaker 排行 10 项时全部渲染。"""
        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=100,
                speaker_ranking=[(f"s{i}", 10 - i) for i in range(10)],
                status="空闲",
            )
        )
        mock_get_index_manager.return_value = mock_index_manager
        mem_mock = MagicMock()
        mem_mock.used = 512 * 1024 * 1024
        mem_mock.total = 2048 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher)

        reply = matcher.finish.call_args[0][0]
        assert "排行（前 10）：" in reply
        assert "10. s9 1" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.cpu_percent", side_effect=RuntimeError("cpu fail"))
    @patch(
        "bot.plugins.meme_info.psutil.virtual_memory",
        side_effect=RuntimeError("mem fail"),
    )
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_hardware_info_failure(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
    ) -> None:
        """psutil 读取失败时应在回复中显示获取失败。"""
        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=10,
                speaker_ranking=[],
                status="空闲",
            )
        )
        mock_get_index_manager.return_value = mock_index_manager

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        assert "内存占用：获取失败" in reply
        assert "CPU占用：获取失败" in reply
        assert "表情包数量：10" in reply
