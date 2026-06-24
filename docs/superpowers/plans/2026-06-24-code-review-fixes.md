# 代码审查修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复四项代码质量问题：布尔锁→asyncio.Lock、假异步→to_thread、删除多余 future import、删除未使用的 runtime_checkable

**Architecture:** 四项改动独立，按文件分组。`index_manager.py` 的锁改造保持同步 API 不变；`image_optimizer.py` 的异步改造在 `optimize()` 层用 `to_thread` 包装同步的压缩方法；两处单行删除无依赖。

**Tech Stack:** Python 3.12, asyncio, PIL/Pillow

---

## 文件变更总览

| 文件 | 操作 | 说明 |
|------|------|------|
| `bot/engine/index_manager.py` | 修改 | `_locked: bool` → `_lock = asyncio.Lock()`，重写锁方法 |
| `bot/engine/image_optimizer.py` | 修改 | `_compress_*` 改同步，`optimize()` 用 `to_thread` 包装，删除 `from __future__` |
| `bot/engine/protocols.py` | 修改 | 删除 `@runtime_checkable` 及其 import |
| `tests/unit/engine/test_index_manager.py` | 修改 | 新增锁行为单元测试 |

---

## Task 1：布尔锁 → asyncio.Lock

**Files:**
- Modify: `bot/engine/index_manager.py:269,758-778,1138-1141`
- Modify: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1：新增锁行为单元测试**

在 `tests/unit/engine/test_index_manager.py` 末尾新增锁测试类：

```python
class TestIndexManagerLock:
    """索引更新锁行为测试。"""

    def test_acquire_lock_returns_true_when_free(self, tmp_path: Path) -> None:
        """空闲时 acquire_lock 返回 True。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        assert mgr.acquire_lock() is True

    def test_acquire_lock_returns_false_when_held(self, tmp_path: Path) -> None:
        """已持有时 acquire_lock 返回 False。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        mgr.acquire_lock()
        assert mgr.acquire_lock() is False

    def test_release_lock_allows_reacquire(self, tmp_path: Path) -> None:
        """释放后可重新获取。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        mgr.acquire_lock()
        mgr.release_lock()
        assert mgr.acquire_lock() is True

    def test_is_locked_reflects_state(self, tmp_path: Path) -> None:
        """is_locked 属性反映当前锁状态。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        assert mgr.is_locked is False
        mgr.acquire_lock()
        assert mgr.is_locked is True
        mgr.release_lock()
        assert mgr.is_locked is False

    def test_release_lock_when_not_held_is_noop(self, tmp_path: Path) -> None:
        """未持有时 release_lock 不抛异常。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        mgr.release_lock()  # 不应抛出
        assert mgr.is_locked is False
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestIndexManagerLock -v
```

预期：PASS（当前布尔锁实现恰好能通过这些测试——这是重构，不是新功能）

- [ ] **Step 3：修改 `index_manager.py` — `__init__` 中的锁字段**

`bot/engine/index_manager.py:269`：

```python
# 旧
self._locked: bool = False

# 新
self._lock = asyncio.Lock()
```

- [ ] **Step 4：修改 `index_manager.py` — `acquire_lock()`**

`bot/engine/index_manager.py:758-772`：

```python
# 旧
def acquire_lock(self) -> bool:
    """非阻塞尝试获取索引更新锁。

    同一时间只允许一个索引写入任务运行。
    如果锁已被占用，返回 False；
    调用方应回复"索引正在更新，请稍后再试"。

    Returns:
        True 表示成功获取锁，False 表示锁已被占用。
    """
    if self._locked:
        return False
    self._locked = True
    logger.debug("索引更新锁已获取")
    return True

# 新
def acquire_lock(self) -> bool:
    """非阻塞尝试获取索引更新锁。

    同一时间只允许一个索引写入任务运行。
    如果锁已被占用，返回 False；
    调用方应回复"索引正在更新，请稍后再试"。

    Returns:
        True 表示成功获取锁，False 表示锁已被占用。
    """
    try:
        self._lock.acquire_nowait()
    except RuntimeError:
        return False
    logger.debug("索引更新锁已获取")
    return True
```

- [ ] **Step 5：修改 `index_manager.py` — `release_lock()`**

`bot/engine/index_manager.py:774-778`：

```python
# 旧
def release_lock(self) -> None:
    """释放索引更新锁。"""
    if self._locked:
        self._locked = False
        logger.debug("索引更新锁已释放")

# 新
def release_lock(self) -> None:
    """释放索引更新锁。"""
    if self._lock.locked():
        self._lock.release()
        logger.debug("索引更新锁已释放")
```

- [ ] **Step 6：修改 `index_manager.py` — `is_locked` 属性**

`bot/engine/index_manager.py:1138-1141`：

```python
# 旧
@property
def is_locked(self) -> bool:
    """索引是否处于锁定状态。"""
    return self._locked

# 新
@property
def is_locked(self) -> bool:
    """索引是否处于锁定状态。"""
    return self._lock.locked()
```

- [ ] **Step 7：修正文档字符串**

`bot/engine/index_manager.py:214`：

```
# 旧
_lock: 写操作异步锁。

# 新
_lock: 索引更新 asyncio.Lock。
```

- [ ] **Step 8：运行锁测试**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestIndexManagerLock -v
```

预期：5 个测试全部 PASS

- [ ] **Step 9：运行刷新插件测试**

```bash
uv run pytest tests/unit/plugins/test_meme_refresh.py -v
```

预期：全部 PASS（`meme_refresh.py` 未改动，API 契约不变）

---

## Task 2：假异步 → asyncio.to_thread

**Files:**
- Modify: `bot/engine/image_optimizer.py:7,9,108-115,146,175,195,221`

- [ ] **Step 1：运行现有测试确认基线**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py -v
```

预期：全部 PASS

- [ ] **Step 2：删除 `from __future__ import annotations`**

`bot/engine/image_optimizer.py:7`：

```python
# 旧
from __future__ import annotations

# 新（删除该行）
```

- [ ] **Step 3：新增 `import asyncio`**

`bot/engine/image_optimizer.py:9`（原 `import logging` 之前）：

```python
import asyncio
import logging
import os
```

- [ ] **Step 4：`_compress_jpeg` 改为同步**

`bot/engine/image_optimizer.py:146`：

```python
# 旧
async def _compress_jpeg(self, path: Path, original_size: int) -> int:

# 新
def _compress_jpeg(self, path: Path, original_size: int) -> int:
```

- [ ] **Step 5：`_compress_png` 改为同步**

`bot/engine/image_optimizer.py:175`：

```python
# 旧
async def _compress_png(self, path: Path, original_size: int) -> int:

# 新
def _compress_png(self, path: Path, original_size: int) -> int:
```

- [ ] **Step 6：`_compress_webp` 改为同步**

`bot/engine/image_optimizer.py:195`：

```python
# 旧
async def _compress_webp(self, path: Path, original_size: int) -> int:

# 新
def _compress_webp(self, path: Path, original_size: int) -> int:
```

- [ ] **Step 7：`_compress_gif` 改为同步**

`bot/engine/image_optimizer.py:221`：

```python
# 旧
async def _compress_gif(self, path: Path, original_size: int) -> int:

# 新
def _compress_gif(self, path: Path, original_size: int) -> int:
```

- [ ] **Step 8：`optimize()` 中用 `asyncio.to_thread` 包装调用**

`bot/engine/image_optimizer.py:108-115`：

```python
# 旧
if suffix in (".jpg", ".jpeg"):
    optimized_size = await self._compress_jpeg(path, original_size)
elif suffix == ".png":
    optimized_size = await self._compress_png(path, original_size)
elif suffix == ".webp":
    optimized_size = await self._compress_webp(path, original_size)
else:
    optimized_size = await self._compress_gif(path, original_size)

# 新
if suffix in (".jpg", ".jpeg"):
    optimized_size = await asyncio.to_thread(self._compress_jpeg, path, original_size)
elif suffix == ".png":
    optimized_size = await asyncio.to_thread(self._compress_png, path, original_size)
elif suffix == ".webp":
    optimized_size = await asyncio.to_thread(self._compress_webp, path, original_size)
else:
    optimized_size = await asyncio.to_thread(self._compress_gif, path, original_size)
```

- [ ] **Step 9：运行 image_optimizer 测试**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py -v
```

预期：全部 PASS（`asyncio.run()` 包装的 async 调用仍然有效）

- [ ] **Step 10：语法检查**

```bash
uv run python -m compileall bot/engine/image_optimizer.py
```

预期：OK

---

## Task 3：删除 `@runtime_checkable`

**Files:**
- Modify: `bot/engine/protocols.py:6,9`

- [ ] **Step 1：修改 `protocols.py`**

`bot/engine/protocols.py`：

```python
# 旧
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):

# 新
from typing import Protocol


class EmbeddingProvider(Protocol):
```

- [ ] **Step 2：语法检查**

```bash
uv run python -m compileall bot/engine/protocols.py
```

预期：OK

---

## Task 4：全量验证

- [ ] **Step 1：语法检查三个修改文件**

```bash
uv run python -m compileall bot/engine/index_manager.py bot/engine/image_optimizer.py bot/engine/protocols.py
```

预期：全部 OK

- [ ] **Step 2：运行相关单元测试**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py tests/unit/engine/test_index_manager.py tests/unit/plugins/test_meme_refresh.py -v
```

预期：全部 PASS

- [ ] **Step 3：运行全量测试**

```bash
uv run pytest
```

预期：全部 PASS
