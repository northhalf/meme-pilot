# /add 命令插件实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `/add [speaker <tags...>]` 命令，允许授权用户通过 QQ 私聊发送图片添加表情包到本地索引。

**Architecture:** 插件层（`meme_add.py`）负责 NoneBot2 交互、图片下载、文件名处理；引擎层（`IndexManager`）新增 `add_single_file()` 方法封装压缩→OCR→Embedding 管道；会话管理提取到 `bot/session.py` 供 `/add` 和 `/search` 共用。

**Tech Stack:** Python 3.12, NoneBot2, nonebot-adapter-onebot, httpx, pytest

**设计文档:** `docs/superpowers/specs/2026-06-24-meme-add-plugin-design.md`

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `bot/engine/index_manager.py` | 公共化 `resolve_unique_filename`；新增自定义异常、`_process_image_pipeline`、`add_single_file` |
| 修改 | `tests/unit/engine/test_index_manager.py` | 更新导入；新增管道和 `add_single_file` 测试 |
| 新建 | `bot/session.py` | `PendingSession` dataclass + 会话操作函数 |
| 新建 | `tests/unit/test_session.py` | 会话管理单元测试 |
| 新建 | `bot/plugins/meme_add.py` | `/add` 命令插件 |
| 修改 | `docs/process.md` | 记录 /add 插件实现 |
| 修改 | `docs/api/API.md` | 记录新增 API |

---

### Task 1: 公共化 resolve_unique_filename

**Files:**
- Modify: `bot/engine/index_manager.py:93-116`
- Modify: `tests/unit/engine/test_index_manager.py:10-20`

- [ ] **Step 1: 重命名函数为公共**

在 `bot/engine/index_manager.py` 中，将 `_resolve_unique_filename` 重命名为 `resolve_unique_filename`（去掉下划线前缀）：

```python
# bot/engine/index_manager.py 第 93 行
def resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。
    ...
    """
```

- [ ] **Step 2: 更新内部调用**

在同一文件中，更新 `_move_to_no_text` 方法（约第 743 行）的调用：

```python
# 第 743 行附近
dst = resolve_unique_filename(self._no_text_dir, filename)
```

- [ ] **Step 3: 更新测试导入**

在 `tests/unit/engine/test_index_manager.py` 中更新导入：

```python
from bot.engine.index_manager import (
    AddResult,
    IndexCorruptedError,
    IndexManager,
    SyncResult,
    resolve_unique_filename,  # 去掉下划线
    compute_text_hash,
    dedup_key,
    is_blank_text,
    normalize_text,
)
```

- [ ] **Step 4: 运行测试验证**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: 所有现有测试 PASS（函数名变更不影响行为）

- [ ] **Step 5: 编译检查**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

---

### Task 2: 新增自定义异常

**Files:**
- Modify: `bot/engine/index_manager.py:124-126`
- Modify: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 添加异常类**

在 `bot/engine/index_manager.py` 的 `IndexCorruptedError` 之后添加：

```python
class IndexCorruptedError(Exception):
    """index.json 结构损坏或缺少必要字段时抛出。"""


class CompressionError(RuntimeError):
    """图片压缩失败。"""


class OcrError(RuntimeError):
    """OCR 识别失败。"""


class EmbeddingError(RuntimeError):
    """Embedding 生成失败。"""
```

- [ ] **Step 2: 添加异常测试**

在 `tests/unit/engine/test_index_manager.py` 中添加测试类：

```python
class TestPipelineErrors:
    """管道异常类测试。"""

    def test_compression_error_is_runtime_error(self) -> None:
        """CompressionError 应为 RuntimeError 子类。"""
        with pytest.raises(CompressionError):
            raise CompressionError("test")

    def test_ocr_error_is_runtime_error(self) -> None:
        """OcrError 应为 RuntimeError 子类。"""
        with pytest.raises(OcrError):
            raise OcrError("test")

    def test_embedding_error_is_runtime_error(self) -> None:
        """EmbeddingError 应为 RuntimeError 子类。"""
        with pytest.raises(EmbeddingError):
            raise EmbeddingError("test")
```

- [ ] **Step 3: 更新测试导入**

```python
from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    EmbeddingError,
    IndexCorruptedError,
    IndexManager,
    OcrError,
    SyncResult,
    resolve_unique_filename,
    compute_text_hash,
    dedup_key,
    is_blank_text,
    normalize_text,
)
```

- [ ] **Step 4: 运行测试验证**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: 所有测试 PASS

---

### Task 3: 提取 _process_image_pipeline 并重构 _process_new_file

**Files:**
- Modify: `bot/engine/index_manager.py:1079-1108` (`_process_new_file` 方法)
- Modify: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 添加 _process_image_pipeline 方法**

在 `IndexManager` 类中，`_process_new_file` 方法之前添加新方法：

```python
async def _process_image_pipeline(
    self, filename: str
) -> tuple[str, list[float]]:
    """图片处理管道：压缩 → OCR → Embedding。

    Args:
        filename: memes/ 下的文件名。

    Returns:
        (ocr_text, embedding) 元组。

    Raises:
        CompressionError: 图片压缩失败。
        OcrError: OCR 服务未注入或调用失败。
        EmbeddingError: Embedding 服务未注入或调用失败。
    """
    image_path = self._memes_dir / filename

    # 压缩（可压缩格式）/ 跳过（.bmp）
    if self._optimizer is not None:
        try:
            await self._optimizer.optimize(str(image_path))
        except Exception as exc:
            raise CompressionError(f"图片压缩失败: {filename}") from exc

    # OCR
    if self._ocr_provider is None:
        raise OcrError("OCR 服务未注入")
    try:
        text = await self._ocr_provider.ocr(str(image_path))
    except Exception as exc:
        raise OcrError(f"OCR 调用失败: {filename}") from exc

    # Embedding
    if self._embedding_provider is None:
        raise EmbeddingError("Embedding 服务未注入")
    try:
        embedding = await self._embedding_provider.embed(text)
    except Exception as exc:
        raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc

    return text, embedding
```

- [ ] **Step 2: 重构 _process_new_file 调用管道**

将 `_process_new_file` 方法体替换为调用 `_process_image_pipeline`：

```python
async def _process_new_file(self, filename: str) -> tuple[str, str, list[float]]:
    """处理单张新增图片：压缩 → OCR → Embed。

    受 _sync_semaphore 约束，并发上限内执行。

    Args:
        filename: 表情包文件名。

    Returns:
        (filename, ocr_text, embedding) 三元组。

    Raises:
        CompressionError: 图片压缩失败。
        OcrError: OCR 服务未注入或调用失败。
        EmbeddingError: Embedding 服务未注入或调用失败。
    """
    async with self._sync_semaphore:
        text, embedding = await self._process_image_pipeline(filename)
    return filename, text, embedding
```

- [ ] **Step 3: 运行现有测试验证重构无破坏**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: 所有现有测试 PASS（行为不变）

- [ ] **Step 4: 编译检查**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

---

### Task 4: 新增 add_single_file 方法

**Files:**
- Modify: `bot/engine/index_manager.py` (在 `add_entry` 方法之后)
- Modify: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 添加 add_single_file 方法**

在 `IndexManager` 类的 `add_entry` 方法之后添加：

```python
async def add_single_file(self, filename: str) -> AddResult:
    """处理单张已保存的图片：管道处理 → add_entry。

    供 /add 插件调用。图片已保存在 memes/ 目录下，
    本方法执行压缩 → OCR → Embedding → 写入索引。

    Args:
        filename: memes/ 下的文件名。

    Returns:
        AddResult 描述添加结果。

    Raises:
        CompressionError: 图片压缩失败。
        OcrError: OCR 服务未注入或调用失败。
        EmbeddingError: Embedding 服务未注入或调用失败。
    """
    text, embedding = await self._process_image_pipeline(filename)
    return self.add_entry(filename, text, embedding)
```

- [ ] **Step 2: 运行编译检查**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

- [ ] **Step 3: 运行现有测试**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: 所有测试 PASS

---

### Task 5: 创建 bot/session.py 会话管理模块

**Files:**
- Create: `bot/session.py`
- Create: `tests/unit/test_session.py`

- [ ] **Step 1: 编写会话管理测试**

```python
# tests/unit/test_session.py
"""bot.session 会话管理模块测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.session import (
    PendingSession,
    cancel,
    check_and_cancel,
    is_cancelled,
    register,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    """每个测试前清空会话字典。"""
    from bot.session import pending_sessions

    pending_sessions.clear()
    yield
    pending_sessions.clear()


class TestPendingSession:
    """PendingSession 数据类测试。"""

    def test_create_defaults(self) -> None:
        """默认值：cancelled=False, type='add'。"""
        matcher = MagicMock()
        s = PendingSession(matcher=matcher)
        assert s.cancelled is False
        assert s.type == "add"

    def test_create_custom(self) -> None:
        """自定义字段值。"""
        matcher = MagicMock()
        s = PendingSession(matcher=matcher, cancelled=True, type="search")
        assert s.cancelled is True
        assert s.type == "search"


class TestCheckAndCancel:
    """check_and_cancel 函数测试。"""

    def test_no_existing_session(self) -> None:
        """无旧会话时返回 None。"""
        result = check_and_cancel("user1", "add")
        assert result is None

    def test_cancels_existing_session(self) -> None:
        """有旧会话时标记取消并返回提示。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        result = check_and_cancel("user1", "add")
        assert result is not None
        assert "已取消" in result
        assert is_cancelled("user1") is True

    def test_cross_command_cancel(self) -> None:
        """不同类型命令也能取消旧会话。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        result = check_and_cancel("user1", "search")
        assert result is not None


class TestRegister:
    """register 函数测试。"""

    def test_registers_session(self) -> None:
        """注册后会话存在。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        from bot.session import pending_sessions

        assert "user1" in pending_sessions
        assert pending_sessions["user1"].type == "add"

    def test_overwrites_existing(self) -> None:
        """重复注册覆盖旧会话。"""
        old_matcher = MagicMock()
        new_matcher = MagicMock()
        register("user1", old_matcher, "add")
        register("user1", new_matcher, "search")
        from bot.session import pending_sessions

        assert pending_sessions["user1"].matcher is new_matcher
        assert pending_sessions["user1"].type == "search"


class TestCancel:
    """cancel 函数测试。"""

    def test_removes_session(self) -> None:
        """cancel 后会话被移除。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        cancel("user1")
        from bot.session import pending_sessions

        assert "user1" not in pending_sessions

    def test_cancel_nonexistent(self) -> None:
        """取消不存在的会话不报错。"""
        cancel("nonexistent")  # 不应抛异常


class TestIsCancelled:
    """is_cancelled 函数测试。"""

    def test_no_session(self) -> None:
        """无会话时返回 False。"""
        assert is_cancelled("user1") is False

    def test_active_session(self) -> None:
        """活跃会话返回 False。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        assert is_cancelled("user1") is False

    def test_cancelled_session(self) -> None:
        """已取消会话返回 True。"""
        matcher = MagicMock()
        register("user1", matcher, "add")
        check_and_cancel("user1", "search")
        assert is_cancelled("user1") is True
```

- [ ] **Step 2: 运行测试验证失败**

```bash
uv run pytest tests/unit/test_session.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 bot/session.py**

```python
# bot/session.py
"""共享会话管理模块。

管理 /add、/search 等命令的待处理会话，
支持跨命令的会话覆盖（新命令取消旧命令）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from nonebot.matcher import Matcher

logger = logging.getLogger(__name__)


@dataclass
class PendingSession:
    """待处理会话。

    Attributes:
        matcher: NoneBot2 Matcher 实例。
        cancelled: 是否已被新命令取消。
        type: 命令类型，如 "add" 或 "search"。
    """

    matcher: Matcher
    cancelled: bool = False
    type: str = "add"


# 模块级会话字典：user_id → PendingSession
pending_sessions: dict[str, PendingSession] = {}


def check_and_cancel(user_id: str, new_type: str) -> str | None:
    """检查旧会话并标记取消。

    Args:
        user_id: 用户 ID。
        new_type: 新命令类型。

    Returns:
        取消提示文本，无旧会话返回 None。
    """
    if user_id not in pending_sessions:
        return None

    old = pending_sessions[user_id]
    old.cancelled = True
    logger.info(
        "取消用户 %s 的旧会话: type=%s, 新命令=%s",
        user_id,
        old.type,
        new_type,
    )
    return f"已取消上一条未完成的操作，开始新的 /{new_type}"


def register(user_id: str, matcher: Matcher, type: str) -> None:
    """注册新会话。

    Args:
        user_id: 用户 ID。
        matcher: NoneBot2 Matcher 实例。
        type: 命令类型。
    """
    pending_sessions[user_id] = PendingSession(matcher=matcher, type=type)
    logger.debug("注册会话: user=%s, type=%s", user_id, type)


def cancel(user_id: str) -> None:
    """移除会话。

    Args:
        user_id: 用户 ID。
    """
    if user_id in pending_sessions:
        del pending_sessions[user_id]
        logger.debug("移除会话: user=%s", user_id)


def is_cancelled(user_id: str) -> bool:
    """检查会话是否已被取消。

    Args:
        user_id: 用户 ID。

    Returns:
        True 表示已取消或无会话。
    """
    session = pending_sessions.get(user_id)
    if session is None:
        return False
    return session.cancelled
```

- [ ] **Step 4: 运行测试验证通过**

```bash
uv run pytest tests/unit/test_session.py -v
```

Expected: 所有测试 PASS

- [ ] **Step 5: 编译检查**

```bash
uv run python -m compileall bot/session.py
```

---

### Task 6: 创建 /add 命令插件

**Files:**
- Create: `bot/plugins/meme_add.py`

- [ ] **Step 1: 实现 meme_add.py**

```python
"""/add 命令插件 — 通过聊天添加表情包。

授权用户在私聊中发送 /add [speaker <tags...>]，Bot 等待图片后
下载、压缩、OCR、Embedding 并写入索引。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.adapters.onebot.v11.helpers import extract_image_urls
from nonebot.matcher import Matcher
from nonebot.params import ArgPlainText
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.engine.index_manager import (
    CompressionError,
    EmbeddingError,
    IndexManager,
    OcrError,
    resolve_unique_filename,
)
from bot.session import cancel, check_and_cancel, is_cancelled, register

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 30  # 图片下载超时（秒）
MEMES_DIR = Path("memes")
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# 文件名安全化：替换非法字符
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")

add_cmd = on_command("add", rule=to_me(), priority=5, block=True)


@add_cmd.handle()
async def handle_add(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/add 命令入口。

    流程：授权校验 → 会话覆盖 → 锁检查 → 注册会话 → 等待图片。
    """
    user_id = event.get_user_id()

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "add")
        return

    # 会话覆盖检查
    hint = check_and_cancel(user_id, "add")
    if hint:
        await matcher.send(hint)

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await add_cmd.finish("服务未就绪，请稍后再试")
        return

    # 检查索引锁
    if not await index_manager.acquire_lock():
        logger.info("用户 %s 的 /add 被拒绝：索引正在更新", user_id)
        await add_cmd.finish("索引正在更新，请稍后再试")
        return

    # 注册会话
    register(user_id, matcher, "add")

    # 等待图片（got 内部调用 receive，SESSION_EXPIRE_TIMEOUT 控制超时）
    await matcher.got("image", prompt="请发送图片，60 秒内有效")


@add_cmd.got("image")
async def got_image(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    image_text: str = ArgPlainText(),
) -> None:
    """接收图片并处理。

    非图片消息时 reject 重新等待；图片消息执行完整添加流程。
    """
    user_id = event.get_user_id()

    # 会话有效性检查
    if is_cancelled(user_id):
        return

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        return

    # 提取图片 URL
    urls = extract_image_urls(event.message)
    if not urls:
        await matcher.reject("请发送一张图片")
        return

    image_url = urls[0]
    target_name = image_text.strip() if image_text else ""

    # 下载图片
    try:
        image_data, response = await _download_image(image_url)
    except Exception as exc:
        logger.error("图片下载失败: %s", exc)
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish("图片下载失败")
        return

    # 确定扩展名
    ext = _get_extension(image_url, response)
    if ext is None or ext.lower() not in SUPPORTED_EXTENSIONS:
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish(f"不支持的图片格式: {ext or '未知'}")
        return

    # 文件名处理
    filename = _build_filename(target_name, image_data, ext)

    # 检查文件名冲突
    filepath = resolve_unique_filename(MEMES_DIR, filename)
    filename = filepath.name

    # 保存图片
    MEMES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        filepath.write_bytes(image_data)
    except OSError as exc:
        logger.error("保存图片失败: %s", exc)
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish("图片保存失败")
        return

    # 调用 IndexManager 处理
    try:
        result = await index_manager.add_single_file(filename)
    except CompressionError as exc:
        logger.error("图片压缩失败: %s", exc)
        filepath.unlink(missing_ok=True)
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish("图片压缩失败")
        return
    except OcrError as exc:
        logger.error("OCR 失败: %s", exc)
        filepath.unlink(missing_ok=True)
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish("OCR 服务不可用")
        return
    except EmbeddingError as exc:
        logger.error("Embedding 失败: %s", exc)
        filepath.unlink(missing_ok=True)
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish("Embedding 服务不可用")
        return
    except Exception as exc:
        logger.exception("添加表情包异常")
        filepath.unlink(missing_ok=True)
        _release_lock_safe(index_manager)
        cancel(user_id)
        await matcher.finish("添加失败，请查看日志")
        return
    finally:
        _release_lock_safe(index_manager)
        cancel(user_id)

    # 回复结果
    if result.reason == "no_text":
        await matcher.finish("未识别到文字，已移至 meme_no_text/")
    elif result.reason == "replaced":
        await matcher.finish("已成功添加（替换旧图）✅")
    else:
        await matcher.finish("已成功添加表情包 ✅")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


async def _download_image(url: str) -> tuple[bytes, httpx.Response]:
    """下载图片。

    Args:
        url: 图片 URL。

    Returns:
        (图片数据, HTTP 响应) 元组。

    Raises:
        httpx.HTTPError: 下载失败。
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        return response.content, response


def _get_extension(url: str, response: httpx.Response) -> str | None:
    """确定图片扩展名。

    优先从 URL 路径提取，其次从 Content-Type 推断。

    Args:
        url: 图片 URL。
        response: HTTP 响应。

    Returns:
        扩展名（含点号），无法推断返回 None。
    """
    # 从 URL 路径提取
    path = Path(url.split("?")[0])
    if path.suffix:
        return path.suffix.lower()

    # 从 Content-Type 推断
    content_type = response.headers.get("content-type", "")
    mime_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    for mime, ext in mime_map.items():
        if mime in content_type:
            return ext

    return None


def _sanitize_filename(name: str) -> str:
    """安全化文件名基名。

    规则：去除首尾空白 → 替换非法字符 → 合并空白为 _ → 截断 80 字符。

    Args:
        name: 原始文件名基名。

    Returns:
        安全化后的基名（无扩展名），可能为空字符串。
    """
    name = name.strip()
    name = _UNSAFE_CHARS.sub("_", name)
    name = _WHITESPACE.sub("_", name)
    name = name[:80].strip("_")
    return name


def _auto_filename(image_data: bytes) -> str:
    """自动生成文件名。

    格式：meme_<YYYYMMDDHHMMSS>_<hash8>

    Args:
        image_data: 图片内容。

    Returns:
        自动生成的文件名基名。
    """
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    hash8 = hashlib.sha256(image_data).hexdigest()[:8]
    return f"meme_{now}_{hash8}"


def _build_filename(target_name: str, image_data: bytes, ext: str) -> str:
    """构建最终文件名。

    Args:
        target_name: 用户指定的目标命名（可能为空）。
        image_data: 图片内容。
        ext: 扩展名（含点号）。

    Returns:
        包含扩展名的完整文件名。
    """
    if target_name:
        base = _sanitize_filename(target_name)
    else:
        base = ""

    if not base:
        base = _auto_filename(image_data)

    return f"{base}{ext}"


def _release_lock_safe(index_manager: IndexManager) -> None:
    """安全释放索引锁。

    Args:
        index_manager: 索引管理器实例。
    """
    try:
        index_manager.release_lock()
    except Exception:
        logger.debug("释放锁时异常（可能已释放）", exc_info=True)
```

- [ ] **Step 2: 编译检查**

```bash
uv run python -m compileall bot/plugins/meme_add.py
```

Expected: 无语法错误

- [ ] **Step 3: 运行全量编译检查**

```bash
uv run python -m compileall bot tests
```

Expected: 所有文件编译通过

---

### Task 7: 更新文档

**Files:**
- Modify: `docs/process.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 更新 process.md**

在 `docs/process.md` 中添加 /add 插件记录（在 /refresh 插件记录之后）：

```markdown
### /add 命令插件（meme_add.py）

实现 PRD 3.3 节「聊天添加表情包」功能。

- 新建 `bot/plugins/meme_add.py`：NoneBot2 命令插件
- 新建 `bot/session.py`：共享会话管理模块（/add 和 /search 共用）
- 修改 `bot/engine/index_manager.py`：
  - `_resolve_unique_filename()` 公共化为 `resolve_unique_filename()`
  - 新增 `CompressionError`、`OcrError`、`EmbeddingError` 自定义异常
  - 新增 `_process_image_pipeline()` 压缩→OCR→Embedding 管道
  - 新增 `add_single_file()` 单张图片添加方法
  - 重构 `_process_new_file()` 调用 `_process_image_pipeline()`
```

- [ ] **Step 2: 更新 API.md**

在 `docs/api/API.md` 的 `bot/engine/index_manager.md` 部分添加新 API：

在 `AddResult` 类之后添加：

```markdown
**新增异常：**

```python
class CompressionError(RuntimeError): ...
class OcrError(RuntimeError): ...
class EmbeddingError(RuntimeError): ...
```

**新增/修改方法：**

```python
def resolve_unique_filename(target_dir: Path, filename: str) -> Path
    # 原 _resolve_unique_filename，已公共化

async def _process_image_pipeline(self, filename: str) -> tuple[str, list[float]]
    # 压缩 → OCR → Embedding 管道
    # Raises: CompressionError, OcrError, EmbeddingError

async def add_single_file(self, filename: str) -> AddResult
    # 单张图片添加：管道处理 → add_entry
    # Raises: CompressionError, OcrError, EmbeddingError
```
```

在 API.md 目录结构中添加：

```markdown
├── bot
│   ├── ...
│   ├── session.md          # 新增
│   └── plugins
│       ├── ...
│       └── meme_add.md     # 新增
```

在 API.md 文件索引中添加 `bot/session.md` 和 `bot/plugins/meme_add.md` 的条目。

- [ ] **Step 3: 运行语法检查确认无破坏**

```bash
uv run python -m compileall bot tests
```

---

### Task 8: 最终验证

- [ ] **Step 1: 全量测试**

```bash
uv run pytest tests/ -v
```

Expected: 所有测试 PASS

- [ ] **Step 2: 全量编译检查**

```bash
uv run python -m compileall bot tests
```

Expected: 所有文件编译通过

- [ ] **Step 3: 确认 git 状态**

```bash
git status
git diff --stat
```

确认变更文件列表与计划一致。
