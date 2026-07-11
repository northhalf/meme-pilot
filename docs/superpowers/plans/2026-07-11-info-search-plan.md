# /info 增强与 /search 删除实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `/info` 增加当前进程内存占用输出、支持 `/info [id]` 查看表情包详情，并删除显式 `/search` 命令（保留普通文本兜底搜索）。

**Architecture:** Engine 层新增带读锁的 `IndexManager.get_entry()`；Plugin 层在 `meme_info.py` 中解析可选 id、读取进程内存与文件大小并格式化；同时删除 `meme_search.py` 插件、对应测试及帮助文本条目，并同步文档。

**Tech Stack:** Python 3.12, NoneBot2, psutil, pytest, uv

---

## 文件结构

| 文件 | 操作 | 说明 |
|---|---|---|
| `bot/engine/index_manager.py` | 修改 | 新增 `get_entry()` 公共方法，持 `IndexRwLock` 读锁 |
| `tests/unit/engine/test_index_manager_info.py` | 修改 | 补充 `get_entry` 相关测试 |
| `bot/plugins/meme_info.py` | 修改 | 解析 `/info [id]`、新增进程内存与详情格式化 |
| `tests/unit/plugins/test_meme_info.py` | 修改 | 补充进程内存、id 详情、无效 id 回退、读锁超时等测试 |
| `bot/plugins/meme_search.py` | 删除 | `/search` 与 `/s` 命令插件 |
| `tests/unit/plugins/test_meme_search.py` | 删除 | `/search` 插件单元测试 |
| `bot/plugins/_help_text.py` | 修改 | 移除 `/search` 帮助行 |
| `docs/api/bot/plugins/meme_info.md` | 修改 | 更新命令说明与输出示例 |
| `docs/api/bot/plugins/meme_search.md` | 修改/删除 | 改为「已删除」说明或删除 |
| `docs/api/API.md` | 修改 | 更新目录/条目描述 |
| `docs/PRD.md` | 修改 | 更新 `/search`、`/info` 章节 |
| `CONTEXT.md` | 修改 | 更新 `/search`、`/info` 术语条目 |
| `README.md` | 修改 | 更新命令列表 |

---

## 前置：切出功能分支（避免在 main 上提交）

```bash
git checkout -b feat/info-search-changes
```

后续所有改动在该分支进行；最终提交由用户审核后执行。

---

## Task 1：Engine 新增 `IndexManager.get_entry()`

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager_info.py`

### Step 1: 实现 `get_entry`

在 `bot/engine/index_manager.py` 中，找到 `async def info(self) -> IndexInfo:` 方法结束位置，在其后新增：

```python
    async def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按 id 查询单条表情包元数据。

        持读锁调用 MetadataStore，保证与刷新期间的写入互斥，读取视图一致。

        Args:
            entry_id: 索引 id。

        Returns:
            对应 MemeEntry；id 不存在时返回 None。

        Raises:
            asyncio.TimeoutError: 等待读锁超时（刷新长时间占用写锁）。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return await run_sync_with_request_id(self._metadata_store.get_entry, entry_id)
```

`MemeEntry`、`asyncio`、`run_sync_with_request_id` 均已导入，无需新增 import。

### Step 2: 验证 engine 测试当前仍通过

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager_info.py -v
```

Expected: 现有 3 个测试全部 PASS。

### Step 3: 写 `get_entry` 失败测试

在 `tests/unit/engine/test_index_manager_info.py` 末尾新增：

```python
class TestGetEntry:
    """IndexManager.get_entry() 单元测试。"""

    @pytest.mark.anyio
    async def test_get_entry_existing(self, index_manager: IndexManager) -> None:
        """存在的 id 返回对应 MemeEntry。"""
        metadata_store = index_manager._metadata_store
        metadata_store.add("a.jpg", "加班心累", speaker="小明", tags=["吐槽"])

        entry = await index_manager.get_entry(1)

        assert entry is not None
        assert entry.id == 1
        assert entry.image_path == "a.jpg"
        assert entry.text == "加班心累"
        assert entry.speaker == "小明"
        assert entry.tags == ["吐槽"]

    @pytest.mark.anyio
    async def test_get_entry_not_found(self, index_manager: IndexManager) -> None:
        """不存在的 id 返回 None。"""
        entry = await index_manager.get_entry(999)
        assert entry is None
```

### Step 4: 运行测试确认通过

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager_info.py -v
```

Expected: `test_get_entry_existing` 与 `test_get_entry_not_found` PASS。

### Step 5: 提交（可选，由用户审核后执行）

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager_info.py
git commit -m "feat(engine): 新增带读锁的 IndexManager.get_entry()"
```

---

## Task 2：改造 `/info` 插件

**Files:**
- Modify: `bot/plugins/meme_info.py`
- Test: `tests/unit/plugins/test_meme_info.py`

### Step 1: 修改 `meme_info.py`

完整替换 `bot/plugins/meme_info.py` 为以下内容：

```python
"""/info 命令插件 — 显示机器人统计与状态信息，支持查看单条表情包详情。

授权用户在私聊或群聊 @bot 中发送 /info [id]，Bot 返回索引统计、当前状态、
本机内存/CPU/进程内存占用；带 id 时返回该表情包的详细信息。
"""

import asyncio
import logging
from pathlib import Path

import psutil
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.metadata_store import MemeEntry
from bot.session import session_manager
from bot.log_context import generate_request_id, set_request_id

logger = logging.getLogger(__name__)

info_cmd = on_command("info", rule=to_me(), priority=5, block=True)


def _format_process_memory() -> str:
    """读取当前进程 RSS 并格式化为 MB。

    Returns:
        形如 "123 MB" 的字符串；读取失败返回 "获取失败"。
    """
    try:
        process = psutil.Process()
        rss_mb = process.memory_info().rss // (1024 * 1024)
        return f"{rss_mb} MB"
    except Exception:
        logger.warning("获取进程内存失败", exc_info=True)
        return "获取失败"


def _format_file_size(size_bytes: int) -> str:
    """把字节数格式化为易读字符串。

    Args:
        size_bytes: 文件字节数。

    Returns:
        自动切换 B / KB / MB 的字符串（KB/MB 保留两位小数）。
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def _build_detail_reply(entry: MemeEntry) -> str:
    """组装 /info <id> 的详情回复。

    Args:
        entry: 表情包元数据条目。

    Returns:
        包含 id、文本、文件名、大小、说话人、标签的多行文本。
    """
    image_path = Path(MEMES_DIR) / entry.image_path
    try:
        size_bytes = image_path.stat().st_size
        size_text = _format_file_size(size_bytes)
    except FileNotFoundError:
        size_text = "文件不存在"
    except Exception:
        logger.warning("获取文件大小失败: %s", image_path, exc_info=True)
        size_text = "获取失败"

    speaker_text = entry.speaker if entry.speaker else "无"
    tags_text = ", ".join(entry.tags) if entry.tags else "无"

    return (
        f"id: {entry.id}\n"
        f"文本：{entry.text}\n"
        f"文件名：{entry.image_path}\n"
        f"大小：{size_text}\n"
        f"说话人：{speaker_text}\n"
        f"标签：{tags_text}"
    )


@info_cmd.handle()
async def handle_info(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """/info 命令处理入口。

    流程：授权校验 → 解析可选 id → 有效 id 则查询详情；否则返回总体统计。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件（私聊或群聊 @bot）。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入）。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /info", user_id)

        try:
            # 授权校验：/info 允许私聊和群聊 @bot
            if not is_authorized(user_id):
                log_unauthorized(user_id, "info")
                await matcher.finish(None)
                return

            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                await matcher.finish("服务未就绪，请稍后再试")
                return

            # 解析可选 id
            raw = args.extract_plain_text().strip()
            entry_id: int | None = None
            if raw:
                try:
                    entry_id = int(raw.split()[0])
                except ValueError:
                    entry_id = None

            # 有效 id 分支：持读锁查询；超时或异常均按设计处理
            if entry_id is not None:
                try:
                    entry = await index_manager.get_entry(entry_id)
                except asyncio.TimeoutError:
                    logger.info("用户 %s 的 /info %s 等待读锁超时", user_id, entry_id)
                    await matcher.finish("索引更新较慢，请稍后再试")
                    return
                except Exception:
                    logger.exception("获取条目详情失败: entry_id=%s", entry_id)
                    entry = None
                else:
                    if entry is not None:
                        await matcher.finish(_build_detail_reply(entry))
                        return
                # id 无效或不存在时回退到总体信息分支

            # 总体信息分支
            try:
                info = await index_manager.info()
            except Exception:
                logger.exception("获取索引信息失败")
                await matcher.finish("索引信息获取失败，请稍后再试")
                return

            # engine 只感知刷新态；"正在处理命令"属应用层语义，由插件层覆写
            if info.status == "空闲" and session_manager.has_active_session():
                info.status = "正在处理命令"

            # 读取硬件信息
            try:
                mem = psutil.virtual_memory()
                mem_text = (
                    f"{mem.used // (1024 * 1024)} MB / "
                    f"{mem.total // (1024 * 1024)} MB ({mem.percent}%)"
                )
            except Exception:
                logger.warning("获取内存信息失败", exc_info=True)
                mem_text = "获取失败"

            try:
                cpu_percent = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
                cpu_text = f"{cpu_percent}%"
            except Exception:
                logger.warning("获取 CPU 信息失败", exc_info=True)
                cpu_text = "获取失败"

            # 组装说话人排行（前 10）
            ranking_lines: list[str] = []
            for idx, (speaker, count) in enumerate(info.speaker_ranking, start=1):
                speaker_name = speaker if speaker is not None else "无"
                ranking_lines.append(f"  {idx}. {speaker_name} {count}")

            if not ranking_lines:
                ranking_lines.append("  暂无数据")

            process_mem_text = _format_process_memory()

            lines = [
                f"表情包数量：{info.entry_count}",
                "排行（前 10）：",
                *ranking_lines,
                f"当前机器人状态：{info.status}",
                f"内存占用：{mem_text}",
                f"进程内存：{process_mem_text}",
                f"CPU占用：{cpu_text}",
            ]

            await matcher.finish("\n".join(lines))
        except asyncio.CancelledError:
            raise FinishedException
```

### Step 2: 运行现有 `/info` 插件测试，确认基线

Run:

```bash
uv run pytest tests/unit/plugins/test_meme_info.py -v
```

Expected: 现有测试 PASS（输出可能因新增 `进程内存` 行而失败，因此先修改测试断言）。

### Step 3: 更新 `/info` 测试断言并新增用例

完整替换 `tests/unit/plugins/test_meme_info.py` 为以下内容：

```python
"""/info 命令插件单元测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.index_manager import IndexInfo
from bot.engine.metadata_store import MemeEntry

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


# ===========================================================================
# 总体信息
# ===========================================================================


class TestHandleInfoOverall:
    """无参数 /info 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.Process")
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
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
        await handle_info(_make_bot(), _make_event(), matcher)

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        assert "进程内存：123 MB" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.Process")
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
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
        await handle_info(_make_bot(), _make_event(), matcher)

        reply = matcher.finish.call_args[0][0]
        assert "进程内存：获取失败" in reply


# ===========================================================================
# id 详情
# ===========================================================================


class TestHandleInfoDetail:
    """`/info <id>` 详情测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_valid_id_shows_detail(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        tmp_path,
    ) -> None:
        """有效 id 返回详情，包含大小、说话人、标签。"""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"x" * 1536)  # 1.50 KB

        with patch("bot.plugins.meme_info.MEMES_DIR", tmp_path):
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
            assert "id: 42" in reply
            assert "文本：加班心累" in reply
            assert "文件名：test.jpg" in reply
            assert "大小：1.50 KB" in reply
            assert "说话人：小明" in reply
            assert "标签：吐槽, 加班" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_valid_id_missing_file_shows_not_found(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        tmp_path,
    ) -> None:
        """entry 存在但文件不存在时大小显示「文件不存在」。"""
        with patch("bot.plugins.meme_info.MEMES_DIR", tmp_path):
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
            assert "大小：文件不存在" in reply
            assert "说话人：无" in reply
            assert "标签：无" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.Process")
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=0.0)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
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
        assert "表情包数量：5" in reply
        assert "进程内存：0 MB" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.Process")
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=0.0)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
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
        assert "表情包数量：3" in reply

    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
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

        matcher.finish.assert_awaited_once_with("索引更新较慢，请稍后再试")
```

### Step 4: 运行 `/info` 插件测试

Run:

```bash
uv run pytest tests/unit/plugins/test_meme_info.py -v
```

Expected: 所有测试 PASS。

### Step 5: 提交（可选）

```bash
git add bot/plugins/meme_info.py tests/unit/plugins/test_meme_info.py
git commit -m "feat(plugins): /info 增加进程内存与 /info [id] 详情查询"
```

---

## Task 3：删除 `/search` 命令

**Files:**
- Delete: `bot/plugins/meme_search.py`
- Delete: `tests/unit/plugins/test_meme_search.py`
- Modify: `bot/plugins/_help_text.py`

### Step 1: 删除插件与测试文件

```bash
rm bot/plugins/meme_search.py
rm tests/unit/plugins/test_meme_search.py
```

### Step 2: 移除帮助文本中的 `/search` 行

编辑 `bot/plugins/_help_text.py`，将：

```
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
```

整行删除。最终 `HELP_TEXT` 以 `/help (/h)` 开头。

### Step 3: 检查是否有其他文件引用 `meme_search`

```bash
rg "meme_search|/search" --type py
```

Expected: 仅可能在测试或文档中出现。若 `test_meme_plain_text.py` 等未引用 `meme_search` 模块，无需改动；若文档中出现，在 Task 4 中处理。

### Step 4: 运行剩余插件测试确认未破坏兜底搜索

```bash
uv run pytest tests/unit/plugins/test_meme_plain_text.py tests/unit/plugins/test_search_utils.py -v
```

Expected: PASS。

### Step 5: 提交（可选）

```bash
git add bot/plugins/_help_text.py
git rm bot/plugins/meme_search.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(plugins): 删除 /search 命令，保留普通文本兜底搜索"
```

---

## Task 4：同步文档

**Files:**
- Modify: `docs/api/bot/plugins/meme_info.md`
- Modify/Delete: `docs/api/bot/plugins/meme_search.md`
- Modify: `docs/api/API.md`
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `README.md`

### Step 1: 更新 `docs/api/bot/plugins/meme_info.md`

完整替换为：

```markdown
# /info 命令 — API 参考

## 依赖

- `app_state.get_index_manager()`
- `bot.auth.is_authorized()`
- `psutil`
- `bot.config.MEMES_DIR`

## 命令

`on_command("info", rule=to_me(), priority=5, block=True)`

支持两种调用方式：

- `/info`：返回机器人总体统计与状态。
- `/info <id>`：返回指定表情包的详细信息。

### handle_info

入口处理器。授权校验 → 获取 `IndexManager` → 解析可选 id → 返回总体信息或条目详情。

#### 总体信息回复

- 表情包数量
- speaker 使用频率排行（前 10）
- 当前机器人状态（空闲/正在刷新索引/正在处理命令）
- 内存占用（系统内存）
- 进程内存（当前 Bot 进程 RSS）
- CPU 占用

#### 详情回复

```
id: 42
文本：加班心累
文件名：meme_xxx.webp
大小：123.45 KB
说话人：小明
标签：吐槽, 加班
```

- `大小` 为文件在 `memes/` 目录下的实际大小，自动使用 B/KB/MB 格式化。
- `说话人`/`标签` 为空时显示 `无`。
- 文件不存在时 `大小` 显示 `文件不存在`。

#### 特殊行为

- id 非数字或 id 不存在时，回退到总体信息输出。
- `/info <id>` 持 `IndexRwLock` 读锁查询；刷新期间等待读锁超时时回复 `索引更新较慢，请稍后再试`。

## 错误处理

| 场景 | 用户消息 |
|------|----------|
| `IndexManager` 尚未初始化 | `服务未就绪，请稍后再试` |
| `IndexManager.info()` 失败 | `索引信息获取失败，请稍后再试` |
| `/info <id>` 等待读锁超时 | `索引更新较慢，请稍后再试` |
| 硬件/进程内存/文件大小读取失败 | 对应字段显示「获取失败」/「文件不存在」 |

## 群聊

授权用户群聊 @bot 调用时同样返回状态信息或详情。
```

### Step 2: 处理 `docs/api/bot/plugins/meme_search.md`

方案 A（推荐，保留历史说明）：完整替换为：

```markdown
# /search 命令 — 已删除

`/search <关键词>`（短命令 `/s`）已从 Bot 命令列表中移除。

普通文本兜底搜索仍然保留：授权用户私聊发送不以 `/` 开头的文字时，仍会按关键词搜索表情包并返回结果。

关键词搜索的核心逻辑仍由 `_search_utils.py` 中的 `execute_search` 提供，供 `/query`、`/rand` 及兜底普通文本搜索复用。
```

方案 B：直接删除 `docs/api/bot/plugins/meme_search.md`，并在 `docs/api/API.md` 中移除对应链接。

### Step 3: 更新 `docs/api/API.md`

- 将 `/info` 条目描述更新为 `/info [id]`，补充进程内存与详情查询说明。
- 将 `/search` 条目描述更新为「已删除，普通文本兜底搜索保留」。

### Step 4: 更新 `docs/PRD.md`

- §3.1「关键词搜索」：在标题或开头标注「已删除」，正文说明普通文本与 `/query` 继续提供关键词搜索能力。
- §3.13「状态信息」：
  - 触发方式改为 `/info [id]`。
  - 输出示例增加 `进程内存` 行。
  - 增加 `/info <id>` 详情输出示例与字段说明。
  - 增加 id 无效/不存在回退总体信息的行为说明。
- §5 边界情况：移除或调整 `/search` 相关行；补充 `/info <id>` 读锁超时行。

### Step 5: 更新 `CONTEXT.md`

- `/search` 条目：说明已删除，兜底普通文本搜索保留。
- `/info` 条目：说明支持 `/info [id]`、进程内存、详情字段。

### Step 6: 更新 `README.md`

- 命令列表中移除 `/search`，更新 `/info` 描述。

### Step 7: 提交（可选）

```bash
git add docs/
git commit -m "docs: 同步 /info 增强与 /search 删除的 API/PRD/CONTEXT/README 文档"
```

---

## Task 5：回归验证

### Step 1: 运行 engine 相关测试

```bash
uv run pytest tests/unit/engine/test_index_manager_info.py -v
```

Expected: PASS。

### Step 2: 运行 plugins 相关测试

```bash
uv run pytest tests/unit/plugins/test_meme_info.py tests/unit/plugins/test_meme_plain_text.py tests/unit/plugins/test_search_utils.py -v
```

Expected: PASS。

### Step 3: 运行全量单元测试

```bash
uv run pytest tests/unit -q
```

Expected: 无新增失败。

### Step 4: 类型检查（如项目使用 pyright）

```bash
uv run pyright bot/plugins/meme_info.py bot/engine/index_manager.py
```

Expected: 无新增类型错误。

### Step 5: 提交最终变更（由用户审核后执行）

```bash
git add -A
git status
# 用户确认后执行
git commit -m "feat(info,search): /info 增加进程内存与 /info [id] 详情，删除 /search 命令"
```

---

## Self-Review

### Spec coverage

- `/info` 进程内存输出 → Task 2。
- `/info [id]` 详情（id/文本/文件名/大小/说话人/标签）→ Task 2。
- id 无效/不存在回退总体信息 → Task 2 测试与实现。
- `/info <id>` 持读锁 → Task 1 实现、Task 2 超时测试。
- 删除 `/search`（含 `/s`）但保留兜底搜索 → Task 3。
- 文档同步 → Task 4。

### Placeholder scan

- 无 TBD/TODO。
- 所有代码步骤均给出完整代码或明确命令。
- 文档修改给出可直接替换的文本。

### Type consistency

- `IndexManager.get_entry` 返回 `MemeEntry | None`。
- `meme_info.py` 导入 `MemeEntry` 用于 `_build_detail_reply` 参数类型。
- 测试使用 `MemeEntry` 构造 mock 数据，字段与生产代码一致。

### 注意事项

- `CLAUDE.md` 禁止在 `main` 分支自行 `git add`/`git commit`。本计划先切出 `feat/info-search-changes` 分支，所有提交步骤均由用户审核后执行。
- 若用户希望不建分支直接在 `main` 上保留未提交改动，可跳过 `git checkout -b` 与 `git commit` 步骤，改为在实现完成后由用户自行 `git add`/`git commit`。
