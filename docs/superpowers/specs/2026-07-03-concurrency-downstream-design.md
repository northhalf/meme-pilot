# MemePilot 并发控制下沉设计

> 日期：2026-07-03
> 状态：设计已批准

---

## 1. 问题

**当前架构**：IndexManager 同时负责两层并发控制：

| 机制 | 用途 | 控制目标 |
|------|------|---------|
| `_add_concurrency` | 限制 Add Worker 数量 | 间接限制 API 并发 |
| `_sync_semaphore` | 限制 sync 中 pipeline 并发 | 限制 OCR+embed API 并发 |
| `_state_cond` | 协调 add worker 与 refresh | 线程安全排队 |

问题：
1. `ai_match` 和 `edit_text` 中的 `embed()` 调用在 IndexManager **锁外**，不受任何限流保护
2. `ai_match` 中的 `rerank()` 调用不受限流
3. OCR 和 Embedding 共享同一并发值（`SYNC_CONCURRENCY`），无法独立配置
4. 职责错位：IndexManager 是编排层，却管理了 API 层的并发细节

**目标**：将并发控制下沉到最接近 API 资源本身的 Service 层，IndexManager 回归编排角色。

---

## 2. 整体方案

### 原则

- 每个有外部 API 调用或阻塞资源的 Service 用 `asyncio.Semaphore` 自限流
- IndexManager 移除所有并发控制参数和机制
- 每个 Service 的并发值独立配置，默认 **5**

### 涉及 Service

| Service | 保护方法 | 环境变量 | 默认值 |
|---------|---------|---------|--------|
| `EmbeddingService` | `embed()` | `EMBEDDING_CONCURRENCY` | 5 |
| `DeepSeekOcrService` | `ocr()` | `OCR_CONCURRENCY` | 5 |
| `PaddleOcrClientService` | `ocr()` | `OCR_CONCURRENCY` | 5 |
| `RerankService` | `rerank()` | `RERANK_CONCURRENCY` | 5 |
| `ImageOptimizer` | `optimize()` | `COMPRESS_CONCURRENCY` | 5 |

---

## 3. Service 层改动

### 3.1 EmbeddingService

```python
class EmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        ...
        c = concurrency or int(os.environ.get("EMBEDDING_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    async def embed(self, text: str) -> list[float]:
        async with self._semaphore:
            # ... 现有 API 调用逻辑（通过 AsyncOpenAI）
```

限流覆盖所有 5 个调用点：

| 调用点 | 调用者 | 当前是否受保护 |
|-------|--------|-------------|
| `_process_image_pipeline` → embed | `add()` 和 `sync` 的 pipeline | 间接（`_add_concurrency`/`_sync_semaphore`） |
| `ai_match` → embed | `IndexManager.ai_match()`（锁外） | ❌ 无保护 |
| `edit_text` → embed | `IndexManager.edit_text()`（锁外） | ❌ 无保护 |
| `_sync_phase0_consistency` → embed | `_rebuild_all_from_sqlite` | 单条循环，天然无并发 |

### 3.2 DeepSeekOcrService / PaddleOcrClientService

```python
class DeepSeekOcrService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        ...
        c = concurrency or int(os.environ.get("OCR_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    async def ocr(self, image_path: str) -> str:
        async with self._semaphore:
            # ... 现有 API 调用逻辑
```

PaddleOcrClientService 同样加 `concurrency` 参数和 Semaphore。

### 3.3 RerankService

```python
class RerankService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        ...
        c = concurrency or int(os.environ.get("RERANK_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    async def rerank(self, description: str, candidates: list[AIMatchCandidate]) -> int:
        async with self._semaphore:
            # ... 现有 LLM 精排逻辑
```

### 3.4 ImageOptimizer

```python
class ImageOptimizer:
    def __init__(
        self,
        jpeg_quality: int = 95,
        webp_quality: int = 80,
        concurrency: int | None = None,
    ) -> None:
        ...
        c = concurrency or int(os.environ.get("COMPRESS_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    async def optimize(self, image_path: str | Path) -> OptimizeResult:
        async with self._semaphore:
            # ... 现有压缩逻辑（内含 asyncio.to_thread 调用 Pillow）
```

ImageOptimizer 的 Semaphore 保护了 `asyncio.to_thread` 背后的线程池，防止大量并发压缩耗尽线程。

---

## 4. IndexManager 简化

### 4.1 删除项

| 删除项 | 原因 |
|-------|------|
| `sync_concurrency` 构造参数 | Service 自限流，不再需要 |
| `_add_concurrency` | 不再限制 Add Worker 数量 |
| `_sync_semaphore` + `_process_new_file` | 已下沉到 Service |
| `DEFAULT_SYNC_CONCURRENCY` 类属性 | 不再需要 |
| `_add_requests: deque[_AddRequest]` | 不再排队 |
| `_add_worker_loop()` + `_add_workers: list[Task]` | 不再有多 Worker 池 |
| `_add_in_flight: int` | 不再跟踪 |
| `_state_cond: asyncio.Condition` | 不再需要协调 |
| `_refresh_pending: bool` | 合并到 `_refresh_active` |

### 4.2 新增项

```python
_refresh_active: bool = False           # 刷新进行中标志
_write_drained: asyncio.Event           # 写队列排空信号（初始已 set）
```

### 4.3 add() 方法

从「入队 → Worker 取 → 管道」变为直接调用：

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

### 4.4 refresh() 方法 — 保留 drain-waiting

```python
async def refresh(self) -> SyncResult:
    if self._shutting_down:
        raise RefreshInProgressError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("已有刷新任务在运行")

    self._refresh_active = True              # ① 阻止新写入
    try:
        if not self._write_queue.empty():     # ② 检查积压
            self._write_drained.clear()       # ③ 清信号
            await self._write_drained.wait()  # ④ 等排空

        async with self._rwlock.write():      # ⑤ 写锁
            return await self._run_sync_internal()
    finally:
        self._refresh_active = False          # ⑥ 释放
```

### 4.5 Write Worker 末尾信号

```python
# _write_worker_loop() 每处理完一条请求的末尾（finally 块内）：
if self._write_queue.empty():
    self._write_drained.set()
```

### 4.6 _sync_phase2_add()

去掉 `_process_new_file` 包装，直接 gather pipeline：

```python
# 原来：
raw = await asyncio.gather(
    *(self._process_new_file(fn) for fn in new_files),
    return_exceptions=True,
)

# 改为：
raw = await asyncio.gather(
    *(self._process_image_pipeline(fn) for fn in new_files),
    return_exceptions=True,
)
```

### 4.7 保留项

| 保留项 | 原因 |
|-------|------|
| `_rwlock: IndexRwLock` | 读写排他（search 读锁、write 写锁） |
| `_shutting_down: bool` | 优雅关闭标志 |
| `_write_queue: asyncio.Queue` | 写入串行化 |
| `_write_worker_task: Task` | Write Worker |
| `_ensure_write_worker()` | 延迟启动 Write Worker |

---

## 5. 并发效果对比

### Sync 第二阶段（100 张新图）

```
当前 (sync_semaphore=5)：
  asyncio.gather 创建 100 个 coroutine
  Semaphore(5) 限制整条 pipeline 并行数
  图1: compress→OCR→embed（占一个 slot）
  图2: compress→OCR→embed（占一个 slot）
  最多 5 张同时处于任一阶段

新方案 (compress=5, OCR=5, embed=5)：
  asyncio.gather 创建 100 个 coroutine
  图1: compress 完 → 释放 slot → 图6 compress
  图1: OCR 开始（独立限流）
  各阶段流水线化，实际效果 ≈ min(各 Semaphore)
```

### 多用户 /ai 并发

```
当前：
  ai_match embed → 无限制（可能打爆 Embedding API）
  ai_match rerank → 无限制（可能打爆 DeepSeek LLM）

新方案：
  ai_match embed → EmbeddingService Semaphore 自动限流
  ai_match rerank → RerankService Semaphore 自动限流
```

---

## 6. 环境变量

```env
# 新增（.env.example）
EMBEDDING_CONCURRENCY=5     # Embedding API 并发上限
OCR_CONCURRENCY=5           # OCR API 并发上限
RERANK_CONCURRENCY=5        # LLM 精排并发上限
COMPRESS_CONCURRENCY=5      # 图片压缩并发上限
```

`SYNC_CONCURRENCY` 不再需要（原默认值 5 已分散到各服务）。

---

## 8. 边界情况

| 场景 | 行为 |
|------|------|
| refresh 开始前已有积压 write | Event drain 等待排空后才获取写锁 |
| add 在 pipeline 期间 refresh 开始 | 完成 pipeline 后检查 `_refresh_active` → 抛 `RefreshInProgressError`（API 已调用但被 Semaphore 限流，浪费可控） |
| 多个 `/ai` 同时并发 | embed 和 rerank 分别受各自 Semaphore 限制 |
| 并发 `/edittext` 和 `/refresh` | edit_text 入口检测 `_refresh_active` → 拒绝 |
| write_queue 为空时 refresh | Event 已 set → 直接跳过 wait |
| refresh 期间 write_queue 有新入队 | 不可能，`_refresh_active` 阻止新写入提交 |

---

## 9. 测试计划

### 9.1 单元测试（`tests/unit/engine/`）

#### Service Semaphore 测试（每类 Service 一套）

| 测试 | 验证点 |
|------|--------|
| `test_<service>_semaphore_default` | 不传 `concurrency` 时使用环境变量默认值（5），Semaphore 正确初始化 |
| `test_<service>_semaphore_custom` | 传 `concurrency=2` 时 Semaphore 值为 2 |
| `test_<service>_semaphore_blocks_concurrent` | 超过并发上限后第 N+1 个调用阻塞，直至前一个释放 |
| `test_<service>_semaphore_env_override` | 设置对应环境变量（如 `EMBEDDING_CONCURRENCY=3`），不传参时读取正确 |

对应 Service：`test_embedding_service.py`、`test_deepseek_ocr.py`、`test_paddle_ocr.py`、`test_rerank_service.py`、`test_image_optimizer.py`

测试手法：`asyncio.Semaphore` 注入 mock `asyncio.Semaphore` 或通过 `concurrency=1` 验证连续两个 `embed()` 不并行（第二个 `wait()` 超时）。

#### IndexManager 精简测试（`test_index_manager.py`）

| 测试 | 验证点 |
|------|--------|
| `test_add_direct_pipeline` | `add()` 直接调用 `_process_image_pipeline`，不再经 add worker 队列 |
| `test_add_refresh_rejected` | refresh 期间 `add()` 抛 `RefreshInProgressError` |
| `test_refresh_add_rejected` | add pipeline 期间 refresh 开始 + TOCTOU 检查生效 |
| `test_refresh_drains_write_queue` | refresh 等待 `_write_drained` Event 排空后才获取写锁 |
| `test_write_queue_empty_no_wait` | write_queue 为空时 refresh 不等待 drain，直接获取写锁 |
| `test_write_worker_drain_signal` | Write Worker 处理完最后一条后 `_write_drained.set()` |
| `test_edit_text_refresh_rejected` | refresh 期间 `edit_text` 抛 `RefreshInProgressError` |
| `test_no_concurrency_params_in_init` | `IndexManager.__init__` 不再接受 `sync_concurrency` 等并发参数 |
| `test_deleted_attrs_not_present` | 验证不再有 `_add_concurrency`、`_sync_semaphore`、`_add_requests`、`_add_worker_loop`、`_state_cond` 等已删除属性 |

### 9.2 集成测试（`tests/integration/`）

| 测试 | 验证点 |
|------|--------|
| `test_semaphore_real_api_backpressure` | 向真实 API 发送大量并发请求，确认 Semaphore 生效且无 `RateLimitError` |
| `test_refresh_with_pending_adds` | 触发 refresh 前先提交若干 add，验证 drain 等待后写入一致 |
| `test_ai_match_embed_concurrency` | 并发调用 `/ai` 验证 embed 调用数受 Semaphore 限制 |

### 9.3 测试替身

| 替身 | 用途 |
|------|------|
| `FakeSemaphore`（`concurrency=0`=无限制） | 单测中绕过并发限制，直接测试业务逻辑 |
| `SlowService`（每个操作延迟 0.1s） | 验证并发阻塞行为，不依赖真实 API |

---

## 10. 涉及文件清单（完整）

| 文件 | 改动类型 |
|------|---------|
| `bot/engine/embedding_service.py` | 加 `concurrency` 参数 + Semaphore |
| `bot/engine/deepseek_ocr.py` | 加 `concurrency` 参数 + Semaphore |
| `bot/engine/paddle_ocr.py` | 加 `concurrency` 参数 + Semaphore |
| `bot/engine/rerank_service.py` | 加 `concurrency` 参数 + Semaphore |
| `bot/engine/image_optimizer.py` | 加 `concurrency` 参数 + Semaphore |
| `bot/engine/index_manager.py` | 删除并发控制，改用 Event drain |
| `bot/bot.py` | 创建 Service 时传入 `concurrency` |
| `.env.example` | 新增 4 个环境变量，移除 `SYNC_CONCURRENCY` 注释 |
| `docs/api/API.md` | 更新 IndexManager 和各 Service 接口 |
| `tests/unit/engine/test_embedding_service.py` | 新增 |
| `tests/unit/engine/test_deepseek_ocr.py` | 新增 |
| `tests/unit/engine/test_paddle_ocr.py` | 新增 |
| `tests/unit/engine/test_rerank_service.py` | 新增 |
| `tests/unit/engine/test_image_optimizer.py` | 新增 |
| `tests/unit/engine/test_index_manager.py` | 更新 |
| `tests/integration/test_concurrency.py` | 新增 |
| `bot/engine/protocols.py` | 不变（Semaphore 是内部行为） |
| `bot/config.py` | 不变 |
| `app_state.py` | 不变 |

