# MemePilot 并发安全重构设计

> 版本：v1.0
> 日期：2026-07-02
> 状态：待实现

---

## 1. 背景与问题

当前 `IndexManager` 使用 `asyncio.Lock` 作为全局索引更新锁，配合 `is_locked` 标志，但仍存在以下并发缺陷：

1. **跨库读撕裂**：`AIMatcher.match` 先 `VectorStore.query` 召回 id，再 `MetadataStore.get_entry` 取文本；写入顺序是"先 sqlite 后 chroma"。读操作可能落在两步之间，出现「新文本 + 旧向量」或「召回的 id 已不存在」。
2. **`add` 非原子**：`add_single_file` 的 `_add_sem` 只限流 OCR/embed，真正的写入 `_write_entry` 中"查重 → 写 sqlite → 写 chroma"可被另一个 `/add` 穿插，导致重复写入或错误回滚。
3. **sync 与 add 互斥缺失**：PRD 要求 `/add` 与 `/refresh` 共享全局索引更新锁，但 `add_single_file` 内部不拿 `IndexManager._lock`，插件层只读 `is_locked` 也存在检查-执行窗口。
4. **sync 自身快照失效**：`sync_phase2_add` 基于初始 `entries` 快照去重，写入期间无全局锁，其他 `add` 并发插入会让 `winner_keys` 和 `existing_paths` 过时。

---

## 2. 设计目标与约束

### 2.1 目标

- 任意时刻，**至多一个写操作**在执行（`/add` 或 `/refresh` 或启动 sync）。
- 无写操作时，**多个读操作并发**。
- 有写操作时，读操作**阻塞等待**（带 30 秒超时）。
- `/refresh` 是**批量独占任务**：运行期间新的 `/add`、新的 `/refresh` 立即拒绝。
- `/add` 之间 **FIFO 排队**；OCR/embed 阶段可并行，写阶段串行。
- `/refresh` 到达时可**抢占**：等当前 `/add` 完成后立即执行，并清空等待中的 `/add` 队列。

### 2.2 非目标

- 不支持跨进程并发（Bot 仍是单进程 asyncio）。
- 不引入第三方分布式锁或数据库级事务隔离。
- 不保证 `/add` 的完成顺序严格 FIFO（因 OCR/embed 耗时不同），只保证入队 FIFO。

---

## 3. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                         插件层                               │
│  /search → IndexManager.search()                            │
│  /ai     → IndexManager.ai_match()                          │
│  /add    → IndexManager.add()                               │
│  /refresh→ IndexManager.refresh()                           │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                      IndexManager                            │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │ IndexRwLock │  │ Add Worker   │  │ Refresh Scheduler   │ │
│  │ 读写锁      │  │ FIFO + 并行  │  │ 抢占/拒绝/清空      │ │
│  └──────┬──────┘  │ OCR/embed    │  └─────────────────────┘ │
│         │         └──────────────┘                           │
│         │                                                    │
│  ┌──────▼──────┐  ┌──────────────────┐                      │
│  │ MetadataStore│  │ VectorStore      │                      │
│  │ (sqlite)    │  │ (chroma)         │                      │
│  └─────────────┘  └──────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

- `IndexRwLock`：保护跨库读写一致性。
- `Add Worker`：多个持续运行的 worker，OCR/embed 并行，写锁串行。
- `Refresh Scheduler`：独占运行，开始前清空 add 队列。
- `MetadataStore` / `VectorStore`：保持各自的 `threading.Lock` 不变。

---

## 4. `IndexRwLock` 设计

### 4.1 职责

纯 asyncio 读写锁：

- 多个读者并发。
- 写者独占。
- **写者优先**：有写者等待时，新读者阻塞，防止写者饿死。
- 支持 `timeout`。

### 4.2 接口

```python
class IndexRwLock:
    async def acquire_read(self, timeout: float | None = None) -> None: ...
    def release_read(self) -> None: ...
    async def acquire_write(self, timeout: float | None = None) -> None: ...
    def release_write(self) -> None: ...

    async def read(self, timeout: float | None = None): ...     # async context manager
    async def write(self, timeout: float | None = None): ...    # async context manager
```

### 4.3 内部状态

```python
self._cond: asyncio.Condition
self._readers: int = 0          # 当前读锁持有者数
self._writer_active: bool = False
self._writer_waiters: int = 0   # 等待写锁的协程数（用于写者优先）
```

### 4.4 关键逻辑

**获取读锁**

```python
async with self._cond:
    await self._cond.wait_for(
        lambda: not self._writer_active and self._writer_waiters == 0,
        timeout=timeout,
    )
    self._readers += 1
```

**释放读锁**

```python
async with self._cond:
    self._readers -= 1
    if self._readers == 0:
        self._cond.notify_all()
```

**获取写锁**

```python
async with self._cond:
    self._writer_waiters += 1
    try:
        await self._cond.wait_for(
            lambda: self._readers == 0 and not self._writer_active,
            timeout=timeout,
        )
        self._writer_active = True
    finally:
        self._writer_waiters -= 1
```

**释放写锁**

```python
async with self._cond:
    self._writer_active = False
    self._cond.notify_all()
```

### 4.5 超时

`acquire_read/acquire_write` 将 `timeout` 透传给 `asyncio.Condition.wait_for()`；超时抛 `asyncio.TimeoutError`。

### 4.6 位置

新增文件 `bot/engine/rwlock.py`。

---

## 5. Add Worker 调度与 Refresh 抢占

### 5.1 核心变更

- 放弃 `asyncio.Queue`，改用 `collections.deque[_AddRequest]` 作为任务队列，全部受 `_state_cond` 保护，消除原子化竞态。
- `refresh` 开始前清空等待队列，拒绝所有 pending add。
- 多个 add worker 持续运行；OCR/embed 阶段并行，写阶段串行竞争 `IndexRwLock.write()`。
- worker 异常自恢复；shutdown 时统一取消。

### 5.2 数据结构

```python
from collections import deque

@dataclass
class _AddRequest:
    filename: str
    future: asyncio.Future[AddResult]

self._state_cond: asyncio.Condition
self._add_requests: deque[_AddRequest] = deque()
self._add_in_flight: int = 0
self._refresh_pending: bool = False
self._refresh_active: bool = False
self._add_workers: list[asyncio.Task] = []
self._add_concurrency: int  # 默认等于 SYNC_CONCURRENCY，至少为 1
self._shutting_down: bool = False
```

`_add_concurrency` 取 `sync_concurrency` 参数的值（默认 5，初始化时 `max(1, sync_concurrency)`，确保至少 1），表示可同时执行 OCR/embed 的 worker 数量。`_shutting_down` 用于关闭时拒绝新请求。

### 5.3 Worker 循环

```python
async def _add_worker_loop(self) -> None:
    while True:
        request: _AddRequest | None = None
        incremented = False
        try:
            async with self._state_cond:
                await self._state_cond.wait_for(
                    lambda: (
                        not self._shutting_down
                        and not self._refresh_pending
                        and not self._refresh_active
                        and self._add_requests
                    )
                )
                request = self._add_requests.popleft()
                self._add_in_flight += 1
                incremented = True

            # 阶段 1：耗时任务，多个 worker 并行
            text, embedding = await self._process_image_pipeline(request.filename)

            # 阶段 2：写锁保护跨库写入
            async with self._rwlock.write():
                result = await self._write_entry(request.filename, text, embedding)
            request.future.set_result(result)

        except asyncio.CancelledError:
            if request is not None:
                request.future.set_exception(
                    IndexAddCancelledError("添加任务被取消")
                )
            raise
        except Exception as exc:
            logger.exception("Add worker 异常")
            if request is not None:
                request.future.set_exception(exc)
        finally:
            if incremented:
                async with self._state_cond:
                    self._add_in_flight -= 1
                    self._state_cond.notify_all()
```

### 5.4 `/add` 入口

```python
async def add(self, filename: str) -> AddResult:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    async with self._state_cond:
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active or self._refresh_pending:
            raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

        self._add_requests.append(_AddRequest(filename, future))
        self._state_cond.notify_all()

        # 清理已完成的 worker，补充到目标并发数
        self._add_workers = [w for w in self._add_workers if not w.done()]
        while len(self._add_workers) < self._add_concurrency:
            self._add_workers.append(
                asyncio.create_task(self._add_worker_loop())
            )

    return await future
```

### 5.5 `/refresh` 抢占路径

```python
async def refresh(self) -> SyncResult:
    async with self._state_cond:
        if self._shutting_down:
            raise RefreshInProgressError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("已有刷新任务在运行")
        self._refresh_pending = True
        self._state_cond.notify_all()

    try:
        async with self._state_cond:
            # 等所有正在处理的 add 完成（或 Bot 关闭）
            await self._state_cond.wait_for(
                lambda: self._add_in_flight == 0 or self._shutting_down
            )
            if self._shutting_down:
                self._refresh_pending = False
                self._state_cond.notify_all()
                raise RefreshInProgressError("Bot 正在关闭")

            # 清空等待队列，拒绝所有 pending add
            for req in self._add_requests:
                try:
                    req.future.set_exception(
                        RefreshInProgressError("索引正在刷新，已取消等待中的添加")
                    )
                except Exception:
                    pass
            self._add_requests.clear()

            # 正式进入 refresh active
            self._refresh_pending = False
            self._refresh_active = True
            self._state_cond.notify_all()

        # 此时 in_flight 已为 0，且新 add 被 add()/worker wait_for 拒绝，
        # 直接获取写锁执行 sync 即可。
        async with self._rwlock.write():
            return await self._run_sync_internal()
    finally:
        async with self._state_cond:
            self._refresh_active = False
            self._state_cond.notify_all()
```

### 5.6 竞态消除说明

- `add()` 和 worker 取任务都持 `_state_cond`，refresh 标志检查和任务取出是原子的。
- refresh 设置 `pending=True` 后，新 add 立即被拒；已有 worker 在 `wait_for(refresh not active)` 处阻塞。
- refresh 在 `wait_for(in_flight == 0)` 后清空 pending 队列。由于 `in_flight` 只在 worker 完成 write lock 内写入并退出 `finally` 后才减 1，因此此时**不存在任何处于 pipeline 与 write_entry 之间的 worker**。
- 设置 `_refresh_active=True` 后、获取 write lock 前的窗口内，没有 in-flight worker 能与 refresh 竞争写锁；新 add 与 worker 的 `wait_for` 也已因 `_refresh_active=True` 而阻塞。

---

## 6. 读路径与插件层变更

### 6.1 读锁范围

所有跨库/跨条目读操作在 `IndexRwLock.read()` 保护下进行：

- `IndexManager.search()`：持读锁 → 检查空库 → 调用 `KeywordSearcher.search()`。
- `IndexManager.ai_match()`：锁外 embed → 持读锁 → 调用 `AIMatcher.match_with_vector()`。

`MetadataStore` / `VectorStore` 内部的 `threading.Lock` 保持不变，作为第二层保护。

### 6.2 IndexManager 新 API

```python
class IndexManager:
    read_timeout: float      # 从 READ_LOCK_TIMEOUT 读取，默认 30 秒
    add_user_timeout: float  # 从 ADD_COMMAND_TIMEOUT 读取，默认 60 秒

    def __init__(
        self,
        metadata_store: MetadataStoreProtocol,
        vector_store: VectorStoreProtocol,
        ocr_provider: OcrProvider,
        embedding_provider: EmbeddingProvider,
        optimizer: ImageOptimizerProtocol,
        keyword_searcher: KeywordSearcher,
        ai_matcher: AIMatcher,
        sync_concurrency: int = 5,
    ) -> None: ...

    async def search(self, keyword: str) -> list[SearchResult]: ...
    async def ai_match(self, description: str) -> AIMatchResult | None: ...
    async def add(self, filename: str) -> AddResult: ...
    async def refresh(self) -> SyncResult: ...
    async def close(self) -> None: ...

    # 以下旧 API 直接删除
    # async def acquire_lock(self) -> bool: ...
    # def release_lock(self) -> None: ...
    # @property def is_locked(self) -> bool: ...
```

`IndexManager` 接收已构造好的 `KeywordSearcher` 与 `AIMatcher` 实例，内部只负责持读锁并委托调用。`bot.py` 的创建顺序保持现有方式：先构造 `MetadataStore`/`VectorStore`/`OCR`/`Embed`/`Rerank`/`Optimizer`，再构造 `KeywordSearcher` 与 `AIMatcher`，最后把它们和并发配置一起传入 `IndexManager`。

`read_timeout` 与 `add_user_timeout` 通过 `bot/config.py` 新增函数读取：

```python
def read_read_lock_timeout() -> int: ...
def read_add_command_timeout() -> int: ...
```

两个环境变量均支持纯数字（秒）或 `HH:MM:SS` 格式。

### 6.3 `IndexManager.search` 实现

```python
async def search(self, keyword: str) -> list[SearchResult]:
    async with self._rwlock.read(timeout=self.read_timeout):
        if self._metadata_store.entry_count() == 0:
            return []
        return self._keyword_searcher.search(keyword)
```

### 6.4 `IndexManager.ai_match` 实现

```python
async def ai_match(self, description: str) -> AIMatchResult | None:
    query_vector = await self._embedding_provider.embed(description)
    async with self._rwlock.read(timeout=self.read_timeout):
        return await self._ai_matcher.match_with_vector(description, query_vector)
```

### 6.5 插件层变更

| 文件 | 变更前 | 变更后 |
|---|---|---|
| `_search_utils.execute_search` | 检查 `is_locked`、`entry_count`，调用 `keyword_searcher.search` | `await index_manager.search(keyword)` |
| `meme_ai._do_match` | 检查 `is_locked`，调用 `ai_matcher.match` | `await index_manager.ai_match(description)` |
| `meme_add.got_image` | 检查 `is_locked`，调用 `add_single_file` | `await asyncio.wait_for(index_manager.add(filename), timeout=index_manager.add_user_timeout)`，捕获 `RefreshInProgressError` / `IndexAddCancelledError` / `TimeoutError` |
| `meme_refresh.handle_refresh` | `acquire_lock/sync/release_lock` | `await index_manager.refresh()` |
| `bot.py _background_sync` | `acquire_lock/sync/release_lock` | `await index_manager.refresh()` |
| `bot.py _on_shutdown` | 关闭 MetadataStore / VectorStore / OCR | 先 `await index_manager.close()`，再关闭 OCR |

### 6.6 `IndexManager.close()` 关闭流程

```python
async def close(self) -> None:
    """安全关闭 IndexManager。

    1. 设置 shutting_down，拒绝新的 add/refresh。
    2. 取消所有 add worker tasks。
    3. 等待 workers 实际结束。
    4. 清空 pending add 队列，为每个 future 设置 IndexAddCancelledError。
    5. 关闭 MetadataStore 和 VectorStore。
    """
    async with self._state_cond:
        self._shutting_down = True
        self._state_cond.notify_all()

    for worker in self._add_workers:
        if not worker.done():
            worker.cancel()

    if self._add_workers:
        await asyncio.gather(*self._add_workers, return_exceptions=True)

    async with self._state_cond:
        for req in self._add_requests:
            req.future.set_exception(
                IndexAddCancelledError("Bot 正在关闭")
            )
        self._add_requests.clear()
        self._state_cond.notify_all()

    self._metadata_store.close()
    self._vector_store.close()
```

---

## 7. `AIMatcher` / `KeywordSearcher` 调整

### 7.1 `AIMatcher`

删除旧 `match()`，保留：

```python
class AIMatcher:
    async def match_with_vector(
        self,
        description: str,
        query_vector: list[float],
    ) -> AIMatchResult | None:
        if _vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")
        if self._vector_store.count() == 0:
            return None
        hits = await self._vector_store.query(query_vector, n_results=self._limit)
        candidates = self._build_candidates(hits)
        ...
```

`_coerce_vector` 删除（`EmbeddingService` 已保证返回 `list[float]`）。

### 7.2 `KeywordSearcher`

`KeywordSearcher.search(keyword)` 保持同步，文档约定：**调用方必须已持有读锁**。`IndexManager.search()` 负责持锁。

---

## 8. 错误处理

### 8.0 自定义异常

```python
class RefreshInProgressError(RuntimeError):
    """索引刷新进行中，新的写入请求应被拒绝。"""

class IndexAddCancelledError(RuntimeError):
    """/add 任务因刷新或关闭而被取消。"""
```

### 8.1 读超时

- `IndexRwLock.read(timeout=30)` 超时抛 `asyncio.TimeoutError`。
- `IndexManager.search/ai_match` 捕获后向用户回复：「索引更新较慢，请稍后再试」。

### 8.2 `/refresh` 拒绝等待中的 `/add`

- 清空 `_add_requests`。
- 为每个 pending add 的 future 设置 `RefreshInProgressError`。
- 插件回复：「索引正在刷新，已取消等待中的添加」。

### 8.3 `/refresh` 期间新 `/add` / 新 `/refresh`

- `add()` / `refresh()` 入口检查 `_refresh_active` 或 `_refresh_pending`，抛 `RefreshInProgressError`。
- 插件回复：「索引正在刷新，请稍后再试」或「已有刷新任务在运行」。

### 8.4 Add Worker 异常

- 外层 `except Exception` 捕获，为对应 future 设置异常。
- Worker `continue` 自恢复。
- 异常日志记录。

### 8.5 Add Worker 取消

- `except asyncio.CancelledError`：若已取出任务，为 future 设置 `IndexAddCancelledError`；重新抛出，worker 正常退出。
- `IndexManager.close()` 取消所有 workers，并清空 pending 队列。

### 8.6 `in_flight` 计数安全

通过 `incremented` 标志确保：

- 成功 `+=1` 后，`-=1` 一定执行。
- 未成功 `+=1` 时，`-=1` 不执行。
- 避免负值或溢出。

---

## 9. 测试策略

### 9.1 `IndexRwLock` 单元测试

- 多个 coroutine 并发读，全部成功。
- 两个 coroutine 竞争写锁，必须串行。
- 读锁持有时写锁阻塞；写锁持有时读锁阻塞。
- 写者优先：写者等待时新读者阻塞。
- 超时：`timeout=0.1` 不满足时抛 `asyncio.TimeoutError`。

### 9.2 `IndexManager` 调度单元测试（mock Store / OCR / Embed）

- **add FIFO**：按顺序提交 3 个 add，验证 `_write_entry` 调用顺序。
- **add OCR 并行**：3 个 add 同时提交，验证 `_process_image_pipeline` 并发调用数等于 `_add_concurrency`。
- **refresh 抢占**：一个 add 正在处理时触发 refresh，验证 refresh 等当前 add 完成后开始，并清空 pending add 队列。
- **refresh 期间拒绝 add**：refresh active 时调用 add，验证抛 `RefreshInProgressError`。
- **in_flight 计数正确性**：各种场景下验证 `_add_in_flight` 最终归零。
- **worker 异常自恢复**：mock worker 内部抛异常，验证 task 不退出、后续 add 仍可处理。

### 9.3 集成测试

- **并发 add + search**：add 进行时调用 search，验证 search 阻塞等待或超时。
- **refresh + add**：refresh 运行时 add 被拒绝。
- **无读撕裂**：add/replace 过程中高频调用 `ai_match`，验证不会拿到不匹配的 text/vector。

### 9.4 现有测试迁移

- `test_index_manager.py`：移除 `acquire_lock/release_lock/is_locked` 测试，改为验证 `refresh()` / `add()` 行为。
- `test_ai_matcher.py`：从测试 `match()` 改为测试 `match_with_vector()`。
- 插件测试：mock `index_manager.search/ai_match/add/refresh`。

---

## 10. 迁移与废弃

### 10.1 废弃 API

以下 `IndexManager` 方法标记为删除：

- `acquire_lock()`
- `release_lock()`
- `is_locked`（property）

### 10.2 文件变更清单

- 新增：`bot/engine/rwlock.py`
- 修改：`bot/engine/index_manager.py`
- 修改：`bot/engine/ai_matcher.py`
- 修改：`bot/engine/keyword_searcher.py`（文档/注释）
- 修改：`bot/plugins/_search_utils.py`
- 修改：`bot/plugins/meme_ai.py`
- 修改：`bot/plugins/meme_add.py`
- 修改：`bot/plugins/meme_refresh.py`
- 修改：`bot/bot.py`
- 修改：`bot/config.py`
- 修改：`.env.example`
- 修改：`docker-compose.yml`
- 修改：`README.md`（环境变量说明、超时说明）
- 修改：`docs/api/API.md`
- 修改：`docs/api/bot/engine/index_manager.md`
- 修改：`docs/api/bot/engine/ai_matcher.md`
- 新增测试：`tests/unit/engine/test_rwlock.py`
- 更新测试：`tests/unit/engine/test_index_manager.py`
- 更新测试：`tests/unit/engine/test_ai_matcher.py`
- 更新测试：`tests/unit/plugins/*`

---

## 11. 附录：状态机

### 11.1 Refresh 状态转换

```text
Idle --(refresh 请求)--> Pending --(in_flight == 0)--> Active --(sync 完成)--> Idle
 ^                          |                              |
 |                          |--(异常)--> Idle              |--(异常)--> Idle
 |_________________________________________________________|
```

### 11.2 Add 调度状态

```text
add 请求 ──[refresh not active]──> _add_requests deque
                                    │
                                    ▼
                            Add Worker (N并发)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              OCR/embed       Write Lock          Result Future
              (并行)          (串行)              (通知用户)
```

---

## 12. 环境变量

新增以下环境变量（加入 `.env.example` 和 `docker-compose.yml`）：

```bash
# 读锁等待超时（search/ai_match 在写入期间阻塞等待的最大时间）
READ_LOCK_TIMEOUT=00:00:30

# /add 命令用户等待超时（从用户发送图片到收到结果的等待上限）
ADD_COMMAND_TIMEOUT=00:01:00
```

两个变量均支持纯数字秒数或 `HH:MM:SS` 格式，由 `bot/config.py` 新增函数解析。
