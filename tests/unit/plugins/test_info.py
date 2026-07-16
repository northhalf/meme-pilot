"""/info 命令插件单元测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.collection_manager import InvalidPublicIdError, MemeNotFoundError
from bot.engine.index_manager import IndexInfo
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import CollectionSelection
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
    resolve_entry_side_effect=None,
) -> MagicMock:
    """创建带 mock 的 IndexManager。"""
    mock_index_manager = MagicMock()
    mock_index_manager.info = AsyncMock(return_value=info)
    mock_index_manager.get_selected_collection = AsyncMock(
        return_value=CollectionSelection(0, "全部合集")
    )
    mock_index_manager.resolve_entry = AsyncMock(
        return_value=entry, side_effect=resolve_entry_side_effect
    )
    return mock_index_manager


# ===========================================================================
# 授权校验
# ===========================================================================


class TestHandleInfoAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(info, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(self, mock_auth: MagicMock) -> None:
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
    @patch("bot.plugins.info.psutil.cpu_percent", return_value=0.0)
    @patch("bot.plugins.info.psutil.virtual_memory")
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_displays_total_and_current_collection_counts(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
        mock_process: MagicMock,
    ) -> None:
        """总体信息展示总数、当前合集数量、普通合集数和范围排行。"""
        manager = _make_index_manager(
            info=IndexInfo(
                entry_count=100,
                current_entry_count=30,
                collection_count=3,
                speaker_ranking=(("曹操", 10),),
                status="空闲",
            )
        )
        manager.get_selected_collection.return_value = CollectionSelection(1, "新三国")
        mock_get_index_manager.return_value = manager
        mock_virtual_memory.return_value = MagicMock(used=0, total=1, percent=0.0)
        mock_process.return_value.memory_info.return_value = MagicMock(rss=0)
        event = _make_event()
        matcher = _make_matcher()

        await handle_info(_make_bot(), event, matcher, args=_make_message(""))

        manager.get_selected_collection.assert_awaited_once_with(
            ChatScope.from_event(event)
        )
        manager.info.assert_awaited_once_with(collection_id=1)
        text = extract_message_text(matcher.finish.await_args.args[0])
        assert "表情包总数：100" in text
        assert "当前合集：新三国（30 张）" in text
        assert "普通合集数：3" in text
        assert "当前范围说话人排行（前 10）：" in text

    @pytest.mark.asyncio
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_selection_timeout_skips_info(
        self, mock_auth: MagicMock, mock_get_index_manager: MagicMock
    ) -> None:
        """合集读取超时时统一提示且不调用统计接口。"""
        manager = _make_index_manager()
        manager.get_selected_collection.side_effect = TimeoutError
        mock_get_index_manager.return_value = manager
        matcher = _make_matcher()

        await handle_info(_make_bot(), _make_event(), matcher, args=_make_message(""))

        manager.info.assert_not_awaited()
        assert "索引更新较慢" in extract_message_text(matcher.finish.await_args.args[0])

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
                current_entry_count=10,
                collection_count=0,
                speaker_ranking=(("小明", 5),),
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
        assert "进程内存：123.00 MiB" in text
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
            info=IndexInfo(
                entry_count=1,
                current_entry_count=1,
                collection_count=0,
                speaker_ranking=(),
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
    @patch("bot.plugins.info.resolve_entry_argument", new_callable=AsyncMock)
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_leading_zero_public_id_uses_scope_resolution(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_resolve_entry_argument: AsyncMock,
    ) -> None:
        """单图详情应把前导零公开 ID 原样交给共享解析器。"""
        entry = MemeEntry(
            id=42,
            image_path="新三国/a.webp",
            text="测试",
            collection_id=1,
            local_id=3,
            collection_name="新三国",
        )
        manager = MagicMock()
        mock_get_index_manager.return_value = manager
        mock_resolve_entry_argument.return_value = entry
        event = _make_event()
        matcher = _make_matcher()

        await handle_info(_make_bot(), event, matcher, args=_make_message("01.003"))

        mock_resolve_entry_argument.assert_awaited_once_with(event, "01.003")
        assert "ID：1.3" in extract_message_text(matcher.finish.await_args[0][0])

    @pytest.mark.asyncio
    @patch("bot.plugins.info.resolve_entry_argument", new_callable=AsyncMock)
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_valid_id_shows_detail(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_resolve_entry_argument: AsyncMock,
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
                collection_id=1,
                local_id=3,
                collection_name="新三国",
            )
            mock_index_manager = _make_index_manager(entry=entry)
            mock_get_index_manager.return_value = mock_index_manager
            mock_resolve_entry_argument.return_value = entry

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
            assert "ID：1.3" in text
            assert "合集：新三国" in text
            assert "id: 42" not in text
            assert "文本：加班心累" in text
            assert "文件名：test.jpg" in text
            assert "大小：1.50 KiB" in text
            assert "说话人：小明" in text
            assert "标签：吐槽, 加班" in text

    @pytest.mark.asyncio
    @patch("bot.plugins.info.resolve_entry_argument", new_callable=AsyncMock)
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_valid_id_missing_file_shows_not_found(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_resolve_entry_argument: AsyncMock,
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
            mock_resolve_entry_argument.return_value = entry

            matcher = _make_matcher()
            await handle_info(
                _make_bot(), _make_event(), matcher, args=_make_message("7")
            )

            reply = matcher.finish.call_args[0][0]
            text = extract_message_text(reply)
            assert "大小：文件不存在" in text
            # speaker 与 tags 同时为空时省略「说话人」「标签」两行
            assert "说话人：" not in text
            assert "标签：" not in text
            _assert_no_reply(reply)

    @pytest.mark.asyncio
    @patch("bot.plugins.info.resolve_entry_argument", new_callable=AsyncMock)
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_invalid_id_replies_domain_error(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_resolve_entry_argument: AsyncMock,
    ) -> None:
        """带参数的非法公开 ID 应提示格式错误，不回退总体统计。"""
        mock_get_index_manager.return_value = _make_index_manager()
        mock_resolve_entry_argument.side_effect = InvalidPublicIdError("abc")
        matcher = _make_matcher()

        await handle_info(
            _make_bot(), _make_event(), matcher, args=_make_message("abc")
        )

        text = extract_message_text(matcher.finish.await_args[0][0])
        assert "表情包 ID 格式错误" in text
        mock_get_index_manager.return_value.info.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("bot.plugins.info.resolve_entry_argument", new_callable=AsyncMock)
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_nonexistent_id_replies_domain_error(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_resolve_entry_argument: AsyncMock,
    ) -> None:
        """不存在的公开 ID 应提示未找到，不回退总体统计。"""
        mock_get_index_manager.return_value = _make_index_manager()
        mock_resolve_entry_argument.side_effect = MemeNotFoundError("9.99")
        matcher = _make_matcher()

        await handle_info(
            _make_bot(), _make_event(), matcher, args=_make_message("9.99")
        )

        text = extract_message_text(matcher.finish.await_args[0][0])
        assert text == "未找到 ID 为 9.99 的表情包"
        mock_get_index_manager.return_value.info.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("bot.plugins.info.resolve_entry_argument", new_callable=AsyncMock)
    @patch("bot.plugins.info.get_index_manager")
    @patch.object(info, "is_authorized", return_value=True)
    async def test_detail_lock_timeout(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_resolve_entry_argument: AsyncMock,
    ) -> None:
        """读锁超时时返回索引更新提示。"""
        import asyncio

        mock_index_manager = _make_index_manager()
        mock_get_index_manager.return_value = mock_index_manager
        mock_resolve_entry_argument.side_effect = asyncio.TimeoutError

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher, args=_make_message("1"))

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
        mock_index_manager.get_selected_collection = AsyncMock(
            return_value=CollectionSelection(0, "全部合集")
        )
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=128,
                current_entry_count=128,
                collection_count=0,
                speaker_ranking=(("小明", 45), (None, 38), ("老板", 21)),
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
        assert "表情包总数：128" in text
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
        mock_index_manager.get_selected_collection = AsyncMock(
            return_value=CollectionSelection(0, "全部合集")
        )
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=5,
                current_entry_count=5,
                collection_count=0,
                speaker_ranking=(),
                status="空闲",
            )
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
        assert "表情包总数：5" in reply


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
        mock_index_manager.get_selected_collection = AsyncMock(
            return_value=CollectionSelection(0, "全部合集")
        )
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
        mock_index_manager.get_selected_collection = AsyncMock(
            return_value=CollectionSelection(0, "全部合集")
        )
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=0,
                current_entry_count=0,
                collection_count=0,
                speaker_ranking=(),
                status="空闲",
            )
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
            await handle_info(
                _make_bot(), _make_event("1001"), matcher, args=_make_message("")
            )

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
        mock_index_manager.get_selected_collection = AsyncMock(
            return_value=CollectionSelection(0, "全部合集")
        )
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=0,
                current_entry_count=0,
                collection_count=0,
                speaker_ranking=(),
                status="空闲",
            )
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
            await handle_info(
                _make_bot(), _make_event("1001"), matcher, args=_make_message("")
            )

            matcher.finish.assert_awaited_once()
            reply = matcher.finish.call_args[0][0]
            text = extract_message_text(reply)
            assert "当前机器人状态：空闲" in text
            _assert_no_reply(reply)
        finally:
            session_manager.deactivate_chat(scope)
