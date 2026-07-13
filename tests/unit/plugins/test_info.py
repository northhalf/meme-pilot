"""/info 命令插件单元测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.index_manager import IndexInfo
from bot.engine.metadata_store import MemeEntry
from bot.session import ChatScope
from tests.conftest import _assert_has_reply, _assert_no_reply, extract_message_text

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from nonebot.adapters.onebot.v11 import Message
    from bot.plugins import info
    from bot.plugins.info import handle_info


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------



def _make_test_scope(user_id: str = "1001") -> ChatScope:
    """构造测试用私聊 ChatScope。"""
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))

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


def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 CommandArg Message 对象。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


def _make_index_manager(
    entry: MemeEntry | None = None,
    info: IndexInfo | None = None,
    get_entry_side_effect=None,
) -> MagicMock:
    """创建带 mock 的 IndexManager。"""
    mock_index_manager = MagicMock()
    mock_index_manager.info = AsyncMock(return_value=info)
    mock_index_manager.get_entry = AsyncMock(
        return_value=entry, side_effect=get_entry_side_effect
    )
    return mock_index_manager


# ===========================================================================
# 授权校验
# ===========================================================================


class TestHandleInfoAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(info, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        bot = _make_bot()

        await handle_info(bot, _make_event("999"), matcher, args=_make_message(""))

        matcher.finish.assert_awaited_once_with(None)
        bot.send.assert_not_awaited()


# ===========================================================================
# 总体信息
# ===========================================================================


class TestHandleInfoOverall:
    """无参数 /info 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_overall_includes_process_memory(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """总体信息应包含进程内存行。"""
        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=123 * 1024 * 1024)
        mock_process.return_value = process_mock

        mock_index_manager = _make_index_manager(
            info=IndexInfo(
                entry_count=10,
                speaker_ranking=[("小明", 5)],
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
        await handle_info(_make_bot(), _make_event(), matcher, args=_make_message(""))

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        text = extract_message_text(reply)
        assert "进程内存：123 MiB" in text
        _assert_no_reply(reply)

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_process_memory_failure_shows_fallback(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """进程内存读取失败时显示获取失败。"""
        mock_process.side_effect = RuntimeError("psutil fail")

        mock_index_manager = _make_index_manager(
            info=IndexInfo(entry_count=1, speaker_ranking=[], status="空闲")
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 512 * 1024 * 1024
        mem_mock.total = 2048 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher, args=_make_message(""))

        reply = matcher.finish.call_args[0][0]
        text = extract_message_text(reply)
        assert "进程内存：获取失败" in text
        _assert_no_reply(reply)


# ===========================================================================
# id 详情
# ===========================================================================


class TestHandleInfoDetail:
    """`/info <id>` 详情测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_valid_id_shows_detail(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        tmp_path: Path,
    ) -> None:
        """有效 id 返回详情，包含大小、说话人、标签。"""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"x" * 1536)  # 1.50 KB

        with patch("bot.plugins.info.MEMES_DIR", tmp_path):
            entry = MemeEntry(
                id=42,
                image_path="test.jpg",
                text="加班心累",
                speaker="小明",
                tags=["吐槽", "加班"],
            )
            mock_index_manager = _make_index_manager(entry=entry)
            mock_get_index_manager.return_value = mock_index_manager

            matcher = _make_matcher()
            await handle_info(
                _make_bot(), _make_event(), matcher, args=_make_message("42")
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            assert isinstance(reply, Message)
            assert reply[0].type == "image"
            assert "file://" in reply[0].data["file"]
            assert reply[1].type == "text"
            _assert_no_reply(reply)
            text = reply[1].data["text"]
            assert "id: 42" in text
            assert "文本：加班心累" in text
            assert "文件名：test.jpg" in text
            assert "大小：1.50 KiB" in text
            assert "说话人：小明" in text
            assert "标签：吐槽, 加班" in text

    @pytest.mark.asyncio
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_valid_id_missing_file_shows_not_found(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        tmp_path: Path,
    ) -> None:
        """entry 存在但文件不存在时大小显示「文件不存在」。"""
        with patch("bot.plugins.info.MEMES_DIR", tmp_path):
            entry = MemeEntry(
                id=7,
                image_path="missing.webp",
                text="无",
                speaker=None,
                tags=[],
            )
            mock_index_manager = _make_index_manager(entry=entry)
            mock_get_index_manager.return_value = mock_index_manager

            matcher = _make_matcher()
            await handle_info(
                _make_bot(), _make_event(), matcher, args=_make_message("7")
            )

            reply = matcher.finish.call_args[0][0]
            text = extract_message_text(reply)
            assert "大小：文件不存在" in text
            assert "说话人：无" in text
            assert "标签：无" in text
            _assert_no_reply(reply)

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=0.0)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_invalid_id_falls_back_to_overall(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """id 非数字时回退到总体信息。"""
        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=0)
        mock_process.return_value = process_mock

        mock_index_manager = _make_index_manager(
            info=IndexInfo(entry_count=5, speaker_ranking=[], status="空闲")
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 0
        mem_mock.total = 1024 * 1024 * 1024
        mem_mock.percent = 0.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(
            _make_bot(), _make_event(), matcher, args=_make_message("abc")
        )

        reply = matcher.finish.call_args[0][0]
        text = extract_message_text(reply)
        assert "表情包数量：5" in text
        assert "进程内存：0 Bytes" in text
        _assert_no_reply(reply)

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=0.0)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_nonexistent_id_falls_back_to_overall(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """id 存在但 entry 为 None 时回退到总体信息。"""
        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=0)
        mock_process.return_value = process_mock

        mock_index_manager = _make_index_manager(
            entry=None,
            info=IndexInfo(entry_count=3, speaker_ranking=[], status="空闲"),
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 0
        mem_mock.total = 1024 * 1024 * 1024
        mem_mock.percent = 0.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(
            _make_bot(), _make_event(), matcher, args=_make_message("999")
        )

        reply = matcher.finish.call_args[0][0]
        text = extract_message_text(reply)
        assert "表情包数量：3" in text
        _assert_no_reply(reply)

    @pytest.mark.asyncio
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_detail_lock_timeout(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
    ) -> None:
        """读锁超时时返回索引更新提示。"""
        import asyncio

        mock_index_manager = _make_index_manager(
            get_entry_side_effect=asyncio.TimeoutError
        )
        mock_get_index_manager.return_value = mock_index_manager

        matcher = _make_matcher()
        await handle_info(
            _make_bot(), _make_event(), matcher, args=_make_message("1")
        )

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        text = extract_message_text(reply)
        assert "索引更新较慢" in text
        _assert_no_reply(reply)


# ===========================================================================
# 原有总体信息/群聊/失败/状态覆写测试（兼容新增进程内存行）
# ===========================================================================


class TestHandleInfoGroupChat:
    """群聊场景测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_group_chat_allowed(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """/info 在群聊 @bot 中应正常返回。"""
        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=64 * 1024 * 1024)
        mock_process.return_value = process_mock

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
        event = _make_event(message_type="group")
        event.message_id = 123456
        await handle_info(_make_bot(), event, matcher, args=_make_message(""))

        mock_index_manager.info.assert_awaited_once()
        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        _assert_has_reply(reply)
        text = extract_message_text(reply)
        assert "表情包数量：128" in text
        assert "当前机器人状态：空闲" in text

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=0.0)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_group_chat_without_message_id_fallback_to_plain_text(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """群聊 event 未设置 message_id 时，总体信息应退化为纯字符串。"""
        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=0)
        mock_process.return_value = process_mock

        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(entry_count=5, speaker_ranking=[], status="空闲")
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 0
        mem_mock.total = 1024 * 1024 * 1024
        mem_mock.percent = 0.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        event = _make_event(message_type="group")
        event.message_id = None
        # 故意不设置有效 message_id，验证退化行为
        await handle_info(_make_bot(), event, matcher, args=_make_message(""))

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        assert isinstance(reply, str)
        assert "表情包数量：5" in reply


class TestHandleInfoIndexFailure:
    """索引信息获取失败测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_info_failure_returns_error_message(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
    ) -> None:
        """index_manager.info() 抛异常时应回复失败提示且不向上抛出。"""
        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(side_effect=RuntimeError("db locked"))
        mock_get_index_manager.return_value = mock_index_manager

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher, args=_make_message(""))

        mock_index_manager.info.assert_awaited_once()
        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        text = extract_message_text(reply)
        assert "索引信息获取失败" in text
        _assert_no_reply(reply)


class TestHandleInfoStatusOverride:
    """状态覆写测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_info_overrides_status_when_session_active(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """engine 返回"空闲"且有活跃会话时，插件层应覆写为"正在处理命令"。"""
        from bot.session import session_manager

        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=64 * 1024 * 1024)
        mock_process.return_value = process_mock

        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(entry_count=0, speaker_ranking=[], status="空闲")
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 512 * 1024 * 1024
        mem_mock.total = 2048 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        scope = _make_test_scope("1001")
        session_manager.deactivate_chat(scope)
        try:
            assert session_manager.activate_chat(scope, "search", MagicMock()) is True
            assert session_manager.has_active_session() is True

            matcher = _make_matcher()
            await handle_info(_make_bot(), _make_event("1001"), matcher, args=_make_message(""))

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            text = extract_message_text(reply)
            assert "当前机器人状态：正在处理命令" in text
            _assert_no_reply(reply)
        finally:
            session_manager.deactivate_chat(scope)

    @pytest.mark.asyncio
    @patch("bot.plugins.info.psutil.Process")
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_info_keeps_idle_when_no_session(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """engine 返回"空闲"且无活跃会话时，状态保持"空闲"。"""
        from bot.session import session_manager

        process_mock = MagicMock()
        process_mock.memory_info.return_value = MagicMock(rss=64 * 1024 * 1024)
        mock_process.return_value = process_mock

        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(entry_count=0, speaker_ranking=[], status="空闲")
        )
        mock_get_index_manager.return_value = mock_index_manager

        mem_mock = MagicMock()
        mem_mock.used = 512 * 1024 * 1024
        mem_mock.total = 2048 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        scope = _make_test_scope("1002")
        session_manager.deactivate_chat(scope)
        try:
            assert session_manager.has_active_session() is False

            matcher = _make_matcher()
            await handle_info(_make_bot(), _make_event("1001"), matcher, args=_make_message(""))

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            text = extract_message_text(reply)
            assert "当前机器人状态：空闲" in text
            _assert_no_reply(reply)
        finally:
            session_manager.deactivate_chat(scope)
