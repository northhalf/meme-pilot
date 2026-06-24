# 代码审查修复设计

> 日期：2026-06-24
> 范围：#1 布尔锁、#3 假异步、#4 future annotations、#5 runtime_checkable

---

## 1. 布尔锁 → asyncio.Lock

### 问题

`index_manager.py` 用手写 `bool` 实现索引更新锁。虽然在单线程 asyncio 中 check-and-set 实际安全，但不符合 asyncio 惯例，且未来若在 acquire/release 之间插入 await 会立即产生竞态。

### 设计

**文件：** `bot/engine/index_manager.py`

- `self._locked: bool = False` → `self._lock = asyncio.Lock()`
- `acquire_lock()` 保持同步签名，内部用 `self._lock.acquire_nowait()`，捕获 `RuntimeError` 返回 `False`
- `release_lock()` 内部用 `self._lock.release()`，先检查 `self._lock.locked()` 避免释放未持有的锁
- `is_locked` 属性返回 `self._lock.locked()`
- 修正类文档字符串中 `_lock` 的描述

**调用方影响：** `meme_refresh.py` 零改动，API 契约不变（`acquire_lock() -> bool`）。

---

## 2. 假异步 → asyncio.to_thread

### 问题

`image_optimizer.py` 中四个 `_compress_*` 方法声明为 `async def` 但无任何 `await`。PIL 的 `Image.open()`、`img.save()` 等是 CPU 密集阻塞操作，直接在事件循环线程执行会阻塞整个 bot。

### 设计

**文件：** `bot/engine/image_optimizer.py`

- 四个 `_compress_*` 方法去掉 `async`，改为同步（内部逻辑不变）
- `optimize()` 中用 `await asyncio.to_thread(self._compress_xxx, path, original_size)` 包装每个调用
- 文件顶部新增 `import asyncio`
- 公共 API `async def optimize()` 签名不变

**调用方影响：** `index_manager.py` line 1100 零改动。测试文件零改动（`asyncio.run(optimizer.optimize(...))` 仍有效）。

---

## 3. 删除 `from __future__ import annotations`

**文件：** `bot/engine/image_optimizer.py` line 7

Python 3.12 原生支持 `str | Path`、`dict[str, Any]` 等语法，文件中无前向引用。删除该行。

---

## 4. 删除 `@runtime_checkable`

**文件：** `bot/engine/protocols.py` lines 6, 9

`@runtime_checkable` 的唯一作用是允许 `isinstance()` 检查 Protocol 实例。全项目无任何 `isinstance(..., EmbeddingProvider)` 调用。删除装饰器及 import 中的 `runtime_checkable`。

---

## 验证

```bash
uv run python -m compileall bot/engine/index_manager.py bot/engine/image_optimizer.py bot/engine/protocols.py
uv run pytest tests/unit/engine/test_image_optimizer.py tests/unit/engine/test_index_manager.py tests/unit/plugins/test_meme_refresh.py -v
uv run pytest
```
