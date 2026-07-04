# MemePilot 并发控制下沉 — Implementation Plan

> 日期：2026-07-03
> 状态：设计已批准（参考 `docs/superpowers/specs/2026-07-03-concurrency-downstream-design.md`）

---

## 阶段总览与依赖关系

```
阶段 1 (Service Semaphore) ──┬──> 阶段 3 (bot.py)
                              │
阶段 2 (IndexManager 精简) ──┘──> 阶段 3
                              │
阶段 4 (.env.example) ───────┘──> 阶段 3 (环境变量先行)
                              │
                              ├──> 阶段 5 (API.md)
                              └──> 阶段 6 (单元测试)
```

| 阶段 | 内容 | 依赖 | 预估文件数 |
|------|------|------|-----------|
| 1 | Service 层 Semaphore 改造 | 无 | 5 |
| 2 | IndexManager 精简 | 无 | 1 |
| 3 | bot.py 注入 concurrency | 阶段 1, 2 | 1 |
| 4 | .env.example 更新 | 无 | 1 |
| 5 | API.md 接口文档更新 | 阶段 1, 2 | 6 |
| 6 | 单元测试编写 | 阶段 1, 2 | 7 |

**阶段 1 和阶段 2 完全独立，可以并行实施。** 阶段 4 在任何时候都可独立完成。

---

## 阶段 1：Service 层 Semaphore 改造

### 通用模式（5 个 Service 一致）

每个 Service 的改动模式如下：

1. 在 `__init__` 签名末尾增加 `concurrency: int | None = None` 参数
2. 读取环境变量作为默认值，回退值为 `5`
3. 创建 `self._semaphore = asyncio.Semaphore(c)`
4. 在受保护方法的 I/O 部分外包 `async with self._semaphore:`
5. 前置校验（参数校验、路径检查等）保持在外，避免空耗信号量

---

### 1a. `bot/engine/embedding_service.py`

**import 层**（第 14 行附近）：添加 `import asyncio`

**`__init__` 方法**（第 33-57 行）：
- 签名末尾增加 `concurrency: int | None = None`
- 第 57 行（`self._client = AsyncOpenAI(...)` 之后）添加：
  ```python
  c = concurrency or int(os.environ.get("EMBEDDING_CONCURRENCY", 5))
  self._semaphore = asyncio.Semaphore(c)
  ```

**`embed` 方法**（第 59-99 行）：
- 第 75-77 行（空文本检查）保持在外
- 第 79-98 行从 `logger.debug(...)` 到 `return embedding` 整体缩进一级，外包 `async with self._semaphore:`

最终结构：
```python
async def embed(self, text: str) -> list[float]:
    text = text.strip()
    if not text:
        raise ValueError(...)
    async with self._semaphore:
        logger.debug(...)
        try:
            response = await self._client.embeddings.create(...)
        ...
```

---

### 1b. `bot/engine/deepseek_ocr.py`

**import 层**（第 9 行附近）：添加 `import asyncio`

**`__init__` 方法**（第 66-91 行）：
- 签名末尾增加 `concurrency: int | None = None`
- 第 91 行（`self._client = AsyncOpenAI(...)` 之后）添加：
  ```python
  c = concurrency or int(os.environ.get("OCR_CONCURRENCY", 5))
  self._semaphore = asyncio.Semaphore(c)
  ```

**`ocr` 方法**（第 94-152 行）：
- 第 111-123 行（路径/格式验证）保持在外
- 第 125-151 行从 `logger.debug("调用 DeepSeek-OCR: %s", path.name)` 到 `return text` 外包 `async with self._semaphore:`

**风险点**：`close()` 方法（第 154-157 行）不涉及 Semaphore，无需修改。

---

### 1c. `bot/engine/paddle_ocr.py`

**import 层**（第 9 行附近）：添加 `import asyncio`

**`__init__` 方法**（第 110-148 行）：
- 签名末尾增加 `concurrency: int | None = None`
- 第 148 行（`self._client = AsyncPaddleOCRClient(...)` 之后）添加：
  ```python
  c = concurrency or int(os.environ.get("OCR_CONCURRENCY", 5))
  self._semaphore = asyncio.Semaphore(c)
  ```

**`ocr` 方法**（第 150-190 行）：
- 第 165-189 行从 `logger.debug("调用 PaddleOCR API: %s", image_path)` 到 `return full_text` 外包 `async with self._semaphore:`

**风险点**：`close()` 方法（第 192-195 行）不涉及 Semaphore，无需修改。

---

### 1d. `bot/engine/rerank_service.py`

**import 层**（第 9 行附近）：添加 `import asyncio`

**`__init__` 方法**（第 99-124 行）：
- 签名末尾增加 `concurrency: int | None = None`
- 第 124 行（`self._client = AsyncOpenAI(...)` 之后）添加：
  ```python
  c = concurrency or int(os.environ.get("RERANK_CONCURRENCY", 5))
  self._semaphore = asyncio.Semaphore(c)
  ```

**`rerank` 方法**（第 126-182 行）：
- 第 147-154 行（候选列表校验 + prompt 构建）保持在外
- 第 156-180 行从 `logger.debug("调用 DeepSeek 精排: ...")` 到 `return rank` 外包 `async with self._semaphore:`

---

### 1e. `bot/engine/image_optimizer.py`

**import 层**：`asyncio` 已导入（第 7 行），无需添加

**`__init__` 方法**（第 58-70 行）：
- 签名末尾增加 `concurrency: int | None = None`
- 第 70 行（`self._webp_quality = webp_quality` 之后）添加：
  ```python
  c = concurrency or int(os.environ.get("COMPRESS_CONCURRENCY", 5))
  self._semaphore = asyncio.Semaphore(c)
  ```

**`optimize` 方法**（第 72-151 行）：
- 第 86-104 行（路径/格式验证 + BMP 跳过 + 不支持的格式检查）保持在外
- 第 106-126 行从 `if suffix in (".jpg", ".jpeg"):` 到 `raise` 分支外包 `async with self._semaphore:`

**说明**：Semaphore 保护的是 `asyncio.to_thread` 调用背后的线程池资源，防止大量并发压缩耗尽系统线程。

---

## 阶段 2：IndexManager 精简

**涉及文件**：`bot/engine/index_manager.py`

### 2.1 删除项清单

| 删除内容 | 原因 |
|---------|------|
| `from collections import deque` | `_add_requests` 已删除 |
| `class _AddRequest` 整个 dataclass | 不再需要 Add Worker |
| `DEFAULT_SYNC_CONCURRENCY: int = 5` | 并发值已下沉到 Service |
| `sync_concurrency` 参数 | IndexManager 不再管理并发 |
| `self._state_cond`、`self._add_requests`、`self._add_in_flight`、`self._refresh_pending` | 不再需要 |
| `self._add_workers: list[asyncio.Task]` | 不再有多 Worker 池 |
| `concurrency = ...` 到 `self._sync_semaphore = ...` 的计算 | 并发控制已下沉 |
| add() 中的入队/通知/创建 Worker 逻辑 | 改为直接 pipeline |
| `edit_text()`, `set_speaker()` 中的 `_refresh_pending` 检查 | 不再有 pending 状态 |
| `refresh()` 中的 `_state_cond` 等待/通知逻辑 | 改为 Event drain |
| 整个 `_add_worker_loop()` 方法 | 不再有多 Worker |
| 整个 `_process_new_file()` 方法 | `_sync_semaphore` 已删除 |
| `_sync_phase2_add()` 中 `_process_new_file` 调用 | 改为 `_process_image_pipeline` |
| `close()` 中 `_state_cond` / `_add_workers` / `_add_requests` 清理 | 不再需要 |

### 2.2 新增项

**`__init__` 中**：
```python
self._write_drained = asyncio.Event()
self._write_drained.set()  # 初始已 set：无内容需要 drain
```

**`_refresh_active` 替换 `_refresh_pending`**：
```python
self._refresh_active: bool = False  # 刷新进行中标志
```

**`add()` 方法** — 直接 pipeline + 写队列：
```python
async def add(self, filename: str) -> AddResult:
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

    text, embedding = await self._process_image_pipeline(filename)

    # TOCTOU 防护
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

    self._ensure_write_worker()
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await self._write_queue.put(
        _WriteRequest(
            op=WriteOp.ADD, future=future,
            filename=filename, text=text, embedding=embedding,
        )
    )
    return await future
```

**`refresh()` 方法** — Event drain：
```python
async def refresh(self) -> SyncResult:
    if self._shutting_down:
        raise RefreshInProgressError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("已有刷新任务在运行")

    self._refresh_active = True
    try:
        if not self._write_queue.empty():
            self._write_drained.clear()
            await self._write_drained.wait()

        async with self._rwlock.write():
            return await self._run_sync_internal()
    finally:
        self._refresh_active = False
```

**`_write_worker_loop()` finally 块**：
```python
finally:
    if self._write_queue.empty():
        self._write_drained.set()
```

### 2.3 保留项确认

| 属性/方法 | 状态 |
|----------|------|
| `_rwlock: IndexRwLock` | 保留 |
| `_shutting_down: bool` | 保留 |
| `_ensure_write_worker()` | 保留 |
| `_write_worker_loop()` | 保留（仅 finally 块改写） |
| `_execute_edit_text()` | 保留 |
| `_execute_set_speaker()` | 保留 |
| `_write_entry()` | 保留 |
| `_process_image_pipeline()` | 保留 |

---

## 阶段 3：bot.py 注入 concurrency 参数

**涉及文件**：`bot/bot.py`

### 3.1 删除 `_read_sync_concurrency()` 整个函数
### 3.2 删除 IndexManager 的 `sync_concurrency` 参数
### 3.3 为 Service 注入 `concurrency` 参数

```python
ocr_service = PaddleOcrClientService(concurrency=_read_int_env("OCR_CONCURRENCY", 5))
# 或
ocr_service = DeepSeekOcrService(concurrency=_read_int_env("OCR_CONCURRENCY", 5))
embedding_service = EmbeddingService(concurrency=_read_int_env("EMBEDDING_CONCURRENCY", 5))
rerank_service = RerankService(concurrency=_read_int_env("RERANK_CONCURRENCY", 5))
image_optimizer = ImageOptimizer(concurrency=_read_int_env("COMPRESS_CONCURRENCY", 5))
```

### 3.4 添加辅助函数 `_read_int_env(key: str, default: int) -> int | None`

---

## 阶段 4：.env.example 更新

**涉及文件**：`.env.example`
- 删除 `SYNC_CONCURRENCY` 块
- 新增 `EMBEDDING_CONCURRENCY`、`OCR_CONCURRENCY`、`RERANK_CONCURRENCY`、`COMPRESS_CONCURRENCY`

---

## 阶段 5：API.md 接口文档更新

**涉及文件**：`docs/api/API.md`
- 各 Service 的 `__init__` 签名增加 `concurrency` 参数
- IndexManager 删除 `DEFAULT_SYNC_CONCURRENCY` 和 `sync_concurrency` 参数
- `add()`, `refresh()` 行为描述更新

---

## 阶段 6：单元测试编写

### 6.1 测试替身

```python
class FakeSemaphore:
    """asyncio.Semaphore 测试替身。"""
    def __init__(self, concurrency: int = 0) -> None:
        self.concurrency = concurrency
        self.acquire_count = 0
        self.release_count = 0

    async def __aenter__(self) -> "FakeSemaphore":
        self.acquire_count += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        self.release_count += 1
```

### 6.2 Service Semaphore 测试

每个 Service 的测试文件追加测试类，验证：
- 默认 concurrency 值
- 自定义 concurrency
- 环境变量覆盖
- 并发阻塞行为

### 6.3 IndexManager 测试更新

- 删除 `sync_concurrency=1` fixture 参数
- 删除 3 个旧测试（fifo_order、refresh_pending、refresh_pending 相关）
- 新增 7 个测试：add_direct_pipeline、add_refresh_active、refresh_drains_write_queue、write_queue_empty_no_wait、write_worker_drain_signal、no_concurrency_params、deleted_attrs_not_present

---

## 关键风险点

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `_write_worker_loop` finally 未改为 drain 信号 | refresh 永久等待 drain，死锁 | 修改后立即测试 drain 通路 |
| FIFO 顺序不再保证 | 并发 add 的 entry_id 分配顺序不确定 | 更新测试，在文档中说明 |
| `_refresh_pending` 在所有文件中的引用未清理干净 | AttributeError | 全局 grep `_refresh_pending` 确保全部移除 |
| `_process_new_file` 在单元测试中仍被引用 | ImportError | 确保所有测试移除引用 |
| `close()` 中 add pipeline 仍在运行 | 竞态条件 | `_shutting_down` 检查已覆盖 TOCTOU |
