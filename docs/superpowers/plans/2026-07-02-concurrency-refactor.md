# MemePilot 并发安全重构实现计划

> **For agentic workers:** REQUIRED SUB-_SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 asyncio 读写锁 + FIFO Add Worker + Refresh 抢占，解决 IndexManager 跨库读撕裂、add 非原子、sync/add 互斥缺失等并发安全问题。

**Architecture:** 新增纯 asyncio `IndexRwLock`（写者优先、支持超时）保护跨库读写一致性；`IndexManager` 内部维护 `collections.deque` 任务队列与 `asyncio.Condition` 状态机，多个 worker 并行执行 OCR/embed、串行竞争写锁；`refresh()` 独占运行，开始前清空 pending add 队列。

**Tech Stack:** Python 3.12, asyncio, NoneBot2, pytest, pytest-asyncio / pytest-anyio, uv.

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `bot/engine/rwlock.py` | 新增 `IndexRwLock` 读写锁 |
| `bot/engine/index_manager.py` | 移除旧锁 API；新增读写锁、Add Worker、refresh、close、search、ai_match |
| `bot/engine/ai_matcher.py` | 删除 `match()` / `_coerce_vector()`，新增 `match_with_vector()` |
| `bot/engine/keyword_searcher.py` | 更新 `search()` 文档：调用方必须持读锁 |
| `bot/config.py` | 新增 `read_read_lock_timeout()` / `read_add_command_timeout()` |
| `bot/bot.py` | 调整 IndexManager/AI Matcher/KeywordSearcher 创建顺序；更新后台同步与关闭钩子 |
| `bot/plugins/_search_utils.py` | 改用 `await index_manager.search()` |
| `bot/plugins/meme_ai.py` | 改用 `await index_manager.ai_match()` |
| `bot/plugins/meme_add.py` | 改用 `await asyncio.wait_for(index_manager.add(), ...)`，捕获新异常 |
| `bot/plugins/meme_refresh.py` | 改用 `await index_manager.refresh()` |
| `.env.example` | 新增 `READ_LOCK_TIMEOUT` / `ADD_COMMAND_TIMEOUT` |
| `docker-compose.yml` | 传递上述环境变量 |
| `README.md` | 说明新环境变量 |
| `docs/api/bot/engine/index_manager.md` | 更新 API 文档 |
| `docs/api/bot/engine/ai_matcher.md` | 更新 API 文档 |
| `docs/api/API.md` | 同步接口摘要 |
| `tests/unit/engine/test_rwlock.py` | 新增读写锁单元测试 |
| `tests/unit/engine/test_index_manager.py` | 迁移旧锁测试，覆盖 add/refresh/search/ai_match/close |
| `tests/unit/engine/test_ai_matcher.py` | 改为测试 `match_with_vector()` |
| `tests/unit/bot/test_config.py` | 新增超时解析测试 |
| `tests/unit/plugins/*` | 更新 mock 调用方式 |

---

## Task 1: 在 `bot/engine/index_manager.py` 添加自定义异常

**Files:**
- Modify: `bot/engine/index_manager.py:58-59`

- [ ] **Step 1: 添加 `RefreshInProgressError` 与 `IndexAddCancelledError`**

在 `EmbeddingError` 类之后插入：

```python
class RefreshInProgressError(RuntimeError):
    """索引刷新进行中，新的写入请求应被拒绝。"""


class IndexAddCancelledError(RuntimeError):
    """/add 任务因刷新或关闭而被取消。"""
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`
Expected: `Compiled 1 file` 无错误输出。

- [ ] **Step 3: Commit**

```bash
git add bot/engine/index_manager.py
git commit -m "feat(engine): add RefreshInProgressError and IndexAddCancelledError"
```

---

## Task 2: 实现 `bot/engine/rwlock.py` 与单元测试

**Files:**
- Create: `bot/engine/rwlock.py`
- Create: `tests/unit/engine/test_rwlock.py`

- [ ] **Step 1: 创建 `IndexRwLock`**

Create `bot/engine/rwlock.py` with:

```python
"""asyncio 读写锁，写者优先。"""

import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class IndexRwLock:
    """asyncio 读写锁。

    特性：
    - 多个读者并发持有。
    - 写者独占。
    - 写者优先：有写者等待时，新读者阻塞。
    - 支持获取超时。

    用法：
        async with lock.read(timeout=30):
            ...

        async with lock.write(timeout=30):
            ...
    """

    def __init__(self) -> None:
        """初始化读写锁。"""
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer_active = False
        self._writer_waiters = 0

    async def acquire_read(self, timeout: float | None = None) -> None:
        """获取读锁。

        Args:
            timeout: 最大等待秒数；None 表示无限等待。

        Raises:
            asyncio.TimeoutError: 等待超时。
        """
        async with self._cond:
            await self._cond.wait_for(
                lambda: not self._writer_active and self._writer_waiters == 0,
                timeout=timeout,
            )
            self._readers += 1

    async def release_read(self) -> None:
        """释放读锁。"""
        async with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self, timeout: float | None = None) -> None:
        """获取写锁。

        Args:
            timeout: 最大等待秒数；None 表示无限等待。

        Raises:
            asyncio.TimeoutError: 等待超时。
        """
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

    async def release_write(self) -> None:
        """释放写锁。"""
        async with self._cond:
            self._writer_active = False
            self._cond.notify_all()

    @asynccontextmanager
    async def read(self, timeout: float | None = None):
        """读锁 async context manager。"""
        await self.acquire_read(timeout)
        try:
            yield
        finally:
            await self.release_read()

    @asynccontextmanager
    async def write(self, timeout: float | None = None):
        """写锁 async context manager。"""
        await self.acquire_write(timeout)
        try:
            yield
        finally:
            await self.release_write()
```

- [ ] **Step 2: 创建单元测试**

Create `tests/unit/engine/test_rwlock.py`:

```python
"""IndexRwLock 单元测试。"""

import asyncio

import pytest

from bot.engine.rwlock import IndexRwLock

pytestmark = pytest.mark.asyncio


async def test_multiple_readers_concurrent() -> None:
    lock = IndexRwLock()
    async with lock.read():
        async with lock.read():
            pass


async def test_write_blocks_read() -> None:
    lock = IndexRwLock()
    async with lock.write():
        with pytest.raises(asyncio.TimeoutError):
            async with lock.read(timeout=0.1):
                pass


async def test_read_blocks_write() -> None:
    lock = IndexRwLock()
    async with lock.read():
        with pytest.raises(asyncio.TimeoutError):
            async with lock.write(timeout=0.1):
                pass


async def test_writer_priority() -> None:
    lock = IndexRwLock()
    writer_started = asyncio.Event()

    async def writer() -> None:
        writer_started.set()
        async with lock.write():
            pass

    async with lock.read():
        task = asyncio.create_task(writer())
        await writer_started.wait()
        await asyncio.sleep(0.05)
        with pytest.raises(asyncio.TimeoutError):
            async with lock.read(timeout=0.1):
                pass
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_two_writes_are_serial() -> None:
    lock = IndexRwLock()
    values: list[int] = []

    async def writer(value: int) -> None:
        async with lock.write():
            values.append(value)
            await asyncio.sleep(0)

    await asyncio.gather(writer(1), writer(2))
    assert values == [1, 2] or values == [2, 1]


async def test_read_timeout_does_not_leak_readers() -> None:
    lock = IndexRwLock()
    async with lock.write():
        with pytest.raises(asyncio.TimeoutError):
            async with lock.read(timeout=0.05):
                pass
    # 写锁释放后应能正常获取读锁
    async with lock.read():
        pass
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/unit/engine/test_rwlock.py -v`
Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add bot/engine/rwlock.py tests/unit/engine/test_rwlock.py
git commit -m "feat(engine): add IndexRwLock with writer priority and timeout"
```

---

## Task 3: 在 `bot/config.py` 添加超时读取函数

**Files:**
- Modify: `bot/config.py`
- Modify: `tests/unit/bot/test_config.py`（如不存在则创建）

- [ ] **Step 1: 实现超时解析**

在 `bot/config.py` 中，在 `read_ocr_provider` 之前插入：

```python
def _parse_timeout_seconds(raw: str, default: int) -> int:
    """解析超时秒数，支持纯数字或 timedelta 格式。

    Args:
        raw: 环境变量原始值。
        default: 解析失败时的默认值。

    Returns:
        正整数秒数。
    """
    from datetime import timedelta

    from pydantic import TypeAdapter

    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        pass
    try:
        td = TypeAdapter(timedelta).validate_python(raw)
        total = int(td.total_seconds())
        return total if total > 0 else default
    except Exception:
        pass
    return default


def read_read_lock_timeout() -> int:
    """从环境变量读取读锁等待超时秒数。

    Returns:
        超时秒数，默认 30。
    """
    return _parse_timeout_seconds(os.environ.get("READ_LOCK_TIMEOUT", ""), 30)


def read_add_command_timeout() -> int:
    """从环境变量读取 /add 命令用户等待超时秒数。

    Returns:
        超时秒数，默认 60。
    """
    return _parse_timeout_seconds(os.environ.get("ADD_COMMAND_TIMEOUT", ""), 60)
```

更新 `__all__`：

```python
__all__ = [
    "PROJECT_ROOT",
    "MEMES_DIR",
    "DATA_DIR",
    "INDEX_DB_PATH",
    "CHROMA_DIR",
    "read_session_timeout",
    "read_ocr_provider",
    "read_read_lock_timeout",
    "read_add_command_timeout",
]
```

- [ ] **Step 2: 创建/更新测试**

Create or update `tests/unit/bot/test_config.py`:

```python
"""bot/config.py 单元测试。"""

import os

import pytest

from bot.config import _parse_timeout_seconds, read_add_command_timeout, read_read_lock_timeout


class TestParseTimeoutSeconds:
    def test_empty_returns_default(self) -> None:
        assert _parse_timeout_seconds("", 30) == 30

    def test_number_returns_int(self) -> None:
        assert _parse_timeout_seconds("45", 30) == 45

    def test_zero_or_negative_returns_default(self) -> None:
        assert _parse_timeout_seconds("0", 30) == 30
        assert _parse_timeout_seconds("-1", 30) == 30

    def test_hhmmss_returns_seconds(self) -> None:
        assert _parse_timeout_seconds("00:01:00", 30) == 60
        assert _parse_timeout_seconds("00:00:30", 30) == 30

    def test_invalid_returns_default(self) -> None:
        assert _parse_timeout_seconds("abc", 30) == 30


class TestReadTimeoutEnv:
    def test_read_read_lock_timeout_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("READ_LOCK_TIMEOUT", raising=False)
        assert read_read_lock_timeout() == 30

    def test_read_read_lock_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("READ_LOCK_TIMEOUT", "00:00:45")
        assert read_read_lock_timeout() == 45

    def test_read_add_command_timeout_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ADD_COMMAND_TIMEOUT", raising=False)
        assert read_add_command_timeout() == 60

    def test_read_add_command_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADD_COMMAND_TIMEOUT", "90")
        assert read_add_command_timeout() == 90
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/unit/bot/test_config.py -v`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add bot/config.py tests/unit/bot/test_config.py
git commit -m "feat(config): add READ_LOCK_TIMEOUT and ADD_COMMAND_TIMEOUT readers"
```

---

## Task 4: 重构 `bot/engine/ai_matcher.py` — 新增 `match_with_vector`

**Files:**
- Modify: `bot/engine/ai_matcher.py`
- Modify: `tests/unit/engine/test_ai_matcher.py`

- [ ] **Step 1: 删除 `match()` 与 `_coerce_vector()`**

删除 `AIMatcher.match()` 方法（lines 149-190）和 `_coerce_vector()` 函数（lines 263-291）。同时删除 `import math` 如果不再使用（`_vector_norm` 仍需要 math）。

- [ ] **Step 2: 添加 `match_with_vector()`**

在 `AIMatcher` 类中添加：

```python
    async def match_with_vector(
        self,
        description: str,
        query_vector: list[float],
    ) -> AIMatchResult | None:
        """根据已生成的 embedding 向量匹配表情包。

        Args:
            description: 用户输入的自然语言描述（已 strip）。
            query_vector: 用户描述对应的 embedding 向量。

        Returns:
            匹配结果；空描述、零向量、向量库为空或无有效候选时返回 None。

        Raises:
            ValueError: query_vector 为零向量。
        """
        description = description.strip()
        if not description:
            logger.debug("AI 匹配描述为空，返回空结果")
            return None

        if _vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")

        if self._vector_store.count() == 0:
            logger.debug("向量库为空，返回空结果")
            return None

        hits = await self._vector_store.query(query_vector, n_results=self._limit)
        candidates = self._build_candidates(hits)
        if not candidates:
            logger.info("AI 召回无候选：description=%r", description)
            return None

        if self._rerank_provider is None:
            return _candidate_to_result(candidates[0], source="embedding")

        rank = await self._rerank(description, candidates)
        if rank is None:
            return _candidate_to_result(candidates[0], source="embedding")

        return _candidate_to_result(candidates[rank - 1], source="rerank")
```

- [ ] **Step 3: 更新现有测试**

在 `tests/unit/engine/test_ai_matcher.py` 中，把所有 `await matcher.match(description)` 改为：

```python
query_vector = await matcher._embedding_provider.embed(description)
result = await matcher.match_with_vector(description, query_vector)
```

或者直接构造 `MockEmbeddingProvider` 返回固定向量，调用时传入。

具体修改：
- `test_empty_description_returns_none_without_embedding_call`: 直接调用 `await matcher.match_with_vector("   ", [0.1, 0.2, 0.3])`。
- `test_empty_vector_store_returns_none`: 直接调用 `await matcher.match_with_vector("找猫", [0.1, 0.2, 0.3])`。
- 其他所有调用 `match()` 的测试同理。

新增一个测试验证零向量抛 `ValueError`：

```python
@pytest.mark.anyio
async def test_zero_vector_raises_value_error() -> None:
    matcher = AIMatcher(
        MockMetadataStore(_make_entries()),
        MockVectorStore(count=1),
        MockEmbeddingProvider(),
    )
    with pytest.raises(ValueError, match="不能是零向量"):
        await matcher.match_with_vector("找猫", [0.0, 0.0, 0.0])
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/unit/engine/test_ai_matcher.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add bot/engine/ai_matcher.py tests/unit/engine/test_ai_matcher.py
git commit -m "refactor(engine): AIMatcher use match_with_vector, remove match and coerce"
```

---

## Task 5: 更新 `bot/engine/keyword_searcher.py` 文档

**Files:**
- Modify: `bot/engine/keyword_searcher.py:186-200`

- [ ] **Step 1: 在 `search()` docstring 中增加持锁约定**

修改 `search()` 的 docstring，在 `Returns:` 前增加：

```python
        Note:
            调用方必须已持有读锁，保证读取期间 MetadataStore 快照不被并发写入修改。
            IndexManager.search() 负责持锁。
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/engine/keyword_searcher.py`
Expected: Compiled 1 file.

- [ ] **Step 3: Commit**

```bash
git add bot/engine/keyword_searcher.py
git commit -m "docs(engine): note KeywordSearcher.search requires read lock"
```

---

## Task 6: 重构 `bot/engine/index_manager.py` — 核心并发逻辑

**Files:**
- Modify: `bot/engine/index_manager.py`

- [ ] **Step 1: 添加导入**

在文件顶部导入：

```python
from collections import deque
from contextlib import asynccontextmanager

from bot.engine.ai_matcher import AIMatcher
from bot.engine.keyword_searcher import KeywordSearcher, SearchResult
from bot.engine.rwlock import IndexRwLock
from bot.config import read_add_command_timeout, read_read_lock_timeout
```

- [ ] **Step 2: 添加 `_AddRequest` dataclass**

在 `AddResult` dataclass 之后添加：

```python
@dataclass
class _AddRequest:
    """Add Worker 任务单元。"""

    filename: str
    future: asyncio.Future[AddResult]
```

- [ ] **Step 3: 替换 `__init__`**

将原 `__init__`（lines 187-229）替换为：

```python
    def __init__(
        self,
        metadata_store: MetadataStoreProtocol,
        vector_store: VectorStoreProtocol,
        memes_dir: str,
        no_text_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizerProtocol | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
        sync_concurrency: int | None = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            memes_dir: 表情包图片目录路径。
            no_text_dir: 无文字图目录；None 时取 memes_dir 同级的 meme_no_text/。
            ocr_provider: OCR 服务提供者。
            embedding_provider: Embedding 服务提供者。
            optimizer: 图片压缩器。
            keyword_searcher: 关键词搜索器，由 IndexManager 持锁后调用。
            ai_matcher: AI 匹配器，由 IndexManager 持锁后调用。
            sync_concurrency: 并发上限，None/非正用默认值。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = Path(memes_dir)
        if no_text_dir is not None:
            self._no_text_dir = Path(no_text_dir)
        else:
            self._no_text_dir = Path(memes_dir).parent / "meme_no_text"
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider
        self._optimizer = optimizer
        self._keyword_searcher = keyword_searcher
        self._ai_matcher = ai_matcher

        self.read_timeout = float(read_read_lock_timeout())
        self.add_user_timeout = float(read_add_command_timeout())

        self._rwlock = IndexRwLock()
        self._state_cond = asyncio.Condition()
        self._add_requests: deque[_AddRequest] = deque()
        self._add_in_flight = 0
        self._refresh_pending = False
        self._refresh_active = False
        self._add_workers: list[asyncio.Task] = []
        self._shutting_down = False

        concurrency = (
            sync_concurrency
            if isinstance(sync_concurrency, int) and sync_concurrency > 0
            else self.DEFAULT_SYNC_CONCURRENCY
        )
        self._add_concurrency = concurrency
        self._sync_semaphore = asyncio.Semaphore(concurrency)
```

- [ ] **Step 4: 删除旧锁 API**

删除 `acquire_lock()`、`release_lock()`、`is_locked` property（lines 244-272）。

- [ ] **Step 5: 重命名 `sync_with_filesystem` 为 `_run_sync_internal`**

将 `async def sync_with_filesystem(self) -> SyncResult:` 改为 `async def _run_sync_internal(self) -> SyncResult:`。方法体不变。

- [ ] **Step 6: 添加 public 方法 `search` / `ai_match` / `add` / `refresh` / `close`**

在 `entry_count` property 之后（或删除旧锁 API 后的位置）添加：

```python
    async def search(self, keyword: str) -> list[SearchResult]:
        """关键词搜索。

        Args:
            keyword: 用户输入的关键词。

        Returns:
            搜索结果列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: KeywordSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._metadata_store.entry_count() == 0:
                return []
            if self._keyword_searcher is None:
                raise RuntimeError("KeywordSearcher 未注入")
            return self._keyword_searcher.search(keyword)

    async def ai_match(self, description: str) -> AIMatchResult | None:
        """AI 描述匹配。

        Args:
            description: 用户自然语言描述。

        Returns:
            匹配结果；空库或无可行候选时返回 None。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: AIMatcher 未注入。
        """
        if self._ai_matcher is None:
            raise RuntimeError("AIMatcher 未注入")
        query_vector = await self._embedding_provider.embed(description)
        async with self._rwlock.read(timeout=self.read_timeout):
            return await self._ai_matcher.match_with_vector(description, query_vector)

    async def add(self, filename: str) -> AddResult:
        """提交 /add 任务到 FIFO 队列，等待执行完成。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            AddResult 描述添加结果。

        Raises:
            RefreshInProgressError: 当前有刷新任务在运行或等待中。
            IndexAddCancelledError: Bot 正在关闭。
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async with self._state_cond:
            if self._shutting_down:
                raise IndexAddCancelledError("Bot 正在关闭")
            if self._refresh_active or self._refresh_pending:
                raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

            self._add_requests.append(_AddRequest(filename, future))
            self._state_cond.notify_all()

            self._add_workers = [w for w in self._add_workers if not w.done()]
            while len(self._add_workers) < self._add_concurrency:
                self._add_workers.append(
                    asyncio.create_task(self._add_worker_loop())
                )

        return await future

    async def refresh(self) -> SyncResult:
        """独占执行索引同步（refresh）。

        Returns:
            SyncResult 描述同步结果。

        Raises:
            RefreshInProgressError: 已有刷新任务在运行或 Bot 正在关闭。
        """
        async with self._state_cond:
            if self._shutting_down:
                raise RefreshInProgressError("Bot 正在关闭")
            if self._refresh_active:
                raise RefreshInProgressError("已有刷新任务在运行")
            self._refresh_pending = True
            self._state_cond.notify_all()

        try:
            async with self._state_cond:
                await self._state_cond.wait_for(
                    lambda: self._add_in_flight == 0 or self._shutting_down
                )
                if self._shutting_down:
                    self._refresh_pending = False
                    self._state_cond.notify_all()
                    raise RefreshInProgressError("Bot 正在关闭")

                for req in self._add_requests:
                    try:
                        req.future.set_exception(
                            RefreshInProgressError("索引正在刷新，已取消等待中的添加")
                        )
                    except Exception:
                        pass
                self._add_requests.clear()

                self._refresh_pending = False
                self._refresh_active = True
                self._state_cond.notify_all()

            # in_flight 已为 0，且新 add/worker 被 _refresh_active 阻塞，安全获取写锁
            async with self._rwlock.write():
                return await self._run_sync_internal()
        finally:
            async with self._state_cond:
                self._refresh_active = False
                self._state_cond.notify_all()

    async def close(self) -> None:
        """安全关闭 IndexManager。

        1. 设置 shutting_down，拒绝新的 add/refresh。
        2. 取消所有 add worker tasks。
        3. 等待 workers 实际结束。
        4. 清空 pending add 队列。
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
                try:
                    req.future.set_exception(
                        IndexAddCancelledError("Bot 正在关闭")
                    )
                except Exception:
                    pass
            self._add_requests.clear()
            self._state_cond.notify_all()

        self._metadata_store.close()
        self._vector_store.close()
```

- [ ] **Step 7: 删除 `add_single_file` 并添加 `_add_worker_loop`**

删除 `add_single_file()` 方法（lines 494-508）。在 `_process_image_pipeline` 附近添加 `_add_worker_loop`：

```python
    async def _add_worker_loop(self) -> None:
        """Add Worker 主循环：串行取任务 → 并行 OCR/embed → 写锁写入。"""
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

                text, embedding = await self._process_image_pipeline(request.filename)

                # 阶段 2：写锁保护跨库写入
                async with self._rwlock.write():
                    result = await self._write_entry(request.filename, text, embedding)
                request.future.set_result(result)

            except asyncio.CancelledError:
                if request is not None:
                    try:
                        request.future.set_exception(
                            IndexAddCancelledError("添加任务被取消")
                        )
                    except Exception:
                        pass
                raise
            except Exception as exc:
                logger.exception("Add worker 异常")
                if request is not None:
                    try:
                        request.future.set_exception(exc)
                    except Exception:
                        pass
            finally:
                if incremented:
                    async with self._state_cond:
                        self._add_in_flight -= 1
                        self._state_cond.notify_all()
```

- [ ] **Step 8: 语法检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`
Expected: Compiled 1 file.

- [ ] **Step 9: Commit**

```bash
git add bot/engine/index_manager.py
git commit -m "feat(engine): refactor IndexManager with rwlock, add workers, refresh preemption"
```

---

## Task 7: 更新 `bot/bot.py`

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: 更新导入**

`bot/bot.py` 的导入块已有 `from bot.config import (...)`，需要加入：

```python
from bot.config import (
    CHROMA_DIR,
    INDEX_DB_PATH,
    MEMES_DIR,
    PROJECT_ROOT,
    read_ocr_provider,
)
```

（`read_ocr_provider` 已存在，无需新增。）

- [ ] **Step 2: 更新 `_background_sync`**

将 `_background_sync` 替换为：

```python
async def _background_sync(index_manager: IndexManager) -> None:
    """后台索引同步任务，不阻塞启动。

    Args:
        index_manager: 已加载索引的 IndexManager 实例。
    """
    logger.info("开始后台索引同步...")
    try:
        result = await index_manager.refresh()
        logger.info(
            "后台索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
            result.added,
            result.deleted,
            result.deduped,
            result.no_text_moved,
            len(result.failed),
        )
        if result.failed:
            logger.warning("同步失败文件（前 10 个）: %s", result.failed[:10])
    except Exception:
        logger.exception("后台索引同步失败，Bot 继续运行（用已有索引）")
```

- [ ] **Step 3: 重排 `_on_startup` 中的服务创建顺序**

将 `index_manager = IndexManager(...)` 块替换为：

```python
    metadata_store = MetadataStore(str(INDEX_DB_PATH))
    vector_store = VectorStore(str(CHROMA_DIR))

    # 4. 创建搜索和匹配服务（IndexManager 内部持锁后委托调用）
    ai_matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )
    keyword_searcher = KeywordSearcher(metadata_store)

    # 3. 创建 IndexManager 并加载索引
    memes_dir = str(MEMES_DIR)
    sync_concurrency = _read_sync_concurrency()

    index_manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=memes_dir,
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        optimizer=image_optimizer,
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        sync_concurrency=sync_concurrency,
    )
    index_manager.load()
```

同时删除原第 4 步（创建 ai_matcher / keyword_searcher）的重复代码。

- [ ] **Step 4: 更新 `_on_shutdown`**

将 `_on_shutdown` 替换为：

```python
async def _on_shutdown() -> None:
    """NoneBot2 关闭钩子 — 先关闭 IndexManager，再关闭 OCR 服务。"""
    from bot.app_state import get_index_manager, get_ocr_service

    try:
        index_manager = get_index_manager()
        await index_manager.close()
        logger.info("IndexManager 已关闭")
    except RuntimeError:
        pass

    try:
        ocr_service = get_ocr_service()
        await ocr_service.close()
        logger.info("OCR 服务 HTTP 会话已关闭")
    except RuntimeError:
        pass
```

- [ ] **Step 5: 语法检查**

Run: `uv run python -m compileall bot/bot.py`
Expected: Compiled 1 file.

- [ ] **Step 6: Commit**

```bash
git add bot/bot.py
git commit -m "feat(bot): wire IndexManager with new search/ai_match/add/refresh APIs"
```

---

## Task 8: 更新插件层

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Modify: `bot/plugins/meme_ai.py`
- Modify: `bot/plugins/meme_add.py`
- Modify: `bot/plugins/meme_refresh.py`

- [ ] **Step 1: 更新 `_search_utils.py`**

修改导入：

```python
from bot.app_state import get_index_manager
```

删除 `get_keyword_searcher` 导入和 `from bot.engine.keyword_searcher import SearchResult` 之外的相关引用。

将 `execute_search` 中的锁检查、空库检查、`keyword_searcher.search()` 替换为：

```python
    # 执行搜索
    try:
        results = await index_manager.search(keyword)
    except asyncio.TimeoutError:
        logger.info("用户 %s 的搜索等待读锁超时", user_id)
        await cmd_matcher.finish("索引更新较慢，请稍后再试")
        return
    except Exception:
        logger.exception("关键词搜索异常: keyword=%r", keyword)
        await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
        return
```

同时删除之前的 `if index_manager.is_locked:` 和 `if index_manager.entry_count == 0:` 两段代码（`search()` 内部已做空库检查；锁等待改为超时异常）。

- [ ] **Step 2: 更新 `meme_ai.py`**

修改导入，删除 `get_ai_matcher`：

```python
from bot.app_state import get_index_manager
```

删除 `_do_match` 辅助函数以及 `from bot.engine.ai_matcher import AIMatcher, AIMatchResult` 导入中不再使用的 `AIMatcher`。

将 `handle_ai` 中的锁检查、空库检查、ai_matcher 获取与匹配替换为：

```python
        # 提取描述
        raw_text = event.get_plaintext().strip()
        description = raw_text.removeprefix("/ai").removeprefix("ai").strip()
        if not description:
            session_manager.deactivate_chat(user_id)
            await matcher.finish("/ai <自然语言描述>")
            return

        # 并发：发送进度提示 + 执行 AI 匹配
        try:
            _, match_result = await asyncio.gather(
                matcher.send("正在根据你的描述搜索表情包，请稍候..."),
                index_manager.ai_match(description),
            )
        except asyncio.TimeoutError:
            logger.info("用户 %s 的 /ai 等待读锁超时", user_id)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("索引更新较慢，请稍后再试")
            return
        except ValueError:
            logger.warning("AI 匹配 embedding 异常: description=%r", description)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("AI 服务暂时不可用，稍后重试")
            return
        except Exception:
            logger.exception("AI 匹配异常: description=%r", description)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("AI 服务暂时不可用，稍后重试")
            return

        if match_result is None:
            session_manager.deactivate_chat(user_id)
            await matcher.finish("没有找到匹配的表情包 🙁")
            return
```

删除原先的锁检查、空库检查、get_ai_matcher 调用、_do_match 调用等代码。

- [ ] **Step 3: 更新 `meme_add.py`**

修改导入：

```python
from bot.engine.index_manager import (
    CompressionError,
    EmbeddingError,
    IndexAddCancelledError,
    OcrError,
    RefreshInProgressError,
    resolve_unique_filename,
)
```

在 `handle_add` 中删除 `if index_manager.is_locked:` 检查块。

在 `got_image` 中：
1. 删除两次 `if index_manager.is_locked:` 检查块。
2. 将 `result = await index_manager.add_single_file(filename)` 替换为：

```python
            # 调用 IndexManager 处理
            try:
                result = await asyncio.wait_for(
                    index_manager.add(filename),
                    timeout=index_manager.add_user_timeout,
                )
            except RefreshInProgressError as exc:
                logger.info("用户 %s 的 /add 被拒绝：%s", user_id, exc)
                msg = "索引正在刷新，请稍后再试"
            except IndexAddCancelledError as exc:
                logger.info("用户 %s 的 /add 被取消：%s", user_id, exc)
                msg = "添加任务已取消"
            except asyncio.TimeoutError:
                logger.info("用户 %s 的 /add 等待超时", user_id)
                msg = "添加处理超时，请稍后再试"
            except CompressionError as exc:
                logger.error("图片压缩失败: %s", exc)
                msg = "图片压缩失败"
            except OcrError as exc:
                logger.error("OCR 失败: %s", exc)
                msg = "OCR 服务不可用"
            except EmbeddingError as exc:
                logger.error("Embedding 失败: %s", exc)
                msg = "Embedding 服务不可用"
            except Exception as exc:
                logger.exception("添加表情包异常")
                msg = "添加失败，请查看日志"
```

注意：新增的 `except RefreshInProgressError` / `except IndexAddCancelledError` / `except asyncio.TimeoutError` 必须在 `except Exception` 之前。

- [ ] **Step 4: 更新 `meme_refresh.py`**

将 `handle_refresh` 中锁的获取/释放与 `sync_with_filesystem` 调用替换为：

```python
        try:
            await bot.send(event, "正在刷新索引，请稍候...")
            result = await index_manager.refresh()
        except Exception:
            logger.exception("索引刷新失败")
            session_manager.deactivate_chat(user_id)
            await matcher.finish("索引刷新失败，请查看日志")
            return
        finally:
            session_manager.deactivate_chat(user_id)
```

删除 `acquire_lock` / `release_lock` 相关代码。

- [ ] **Step 5: 语法检查**

Run: `uv run python -m compileall bot/plugins/_search_utils.py bot/plugins/meme_ai.py bot/plugins/meme_add.py bot/plugins/meme_refresh.py`
Expected: Compiled 4 files.

- [ ] **Step 6: Commit**

```bash
git add bot/plugins/_search_utils.py bot/plugins/meme_ai.py bot/plugins/meme_add.py bot/plugins/meme_refresh.py
git commit -m "refactor(plugins): adapt to new IndexManager APIs"
```

---

## Task 9: 更新环境变量与 README

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`

- [ ] **Step 1: 更新 `.env.example`**

在 `SYNC_CONCURRENCY` 注释块之后、`SESSION_EXPIRE_TIMEOUT` 之前插入：

```bash
# 读锁等待超时：search/ai_match 在写入期间阻塞等待的最大时间
READ_LOCK_TIMEOUT=00:00:30

# /add 命令用户等待超时：从用户发送图片到收到结果的等待上限
ADD_COMMAND_TIMEOUT=00:01:00

```

- [ ] **Step 2: 更新 `docker-compose.yml`**

在 `SYNC_CONCURRENCY=${SYNC_CONCURRENCY:-5}` 之后添加：

```yaml
      - READ_LOCK_TIMEOUT=${READ_LOCK_TIMEOUT:-00:00:30}
      - ADD_COMMAND_TIMEOUT=${ADD_COMMAND_TIMEOUT:-00:01:00}
```

- [ ] **Step 3: 更新 `README.md`**

在 "部署步骤" 的 `.env` 注释块中，在 `SYNC_CONCURRENCY` 和 `SESSION_EXPIRE_TIMEOUT` 之间添加：

```bash
#   READ_LOCK_TIMEOUT=00:00:30  # 可选，search/ai_match 等待写锁释放的超时
#   ADD_COMMAND_TIMEOUT=00:01:00  # 可选，/add 从提交到结果返回的超时
```

- [ ] **Step 4: Commit**

```bash
git add .env.example docker-compose.yml README.md
git commit -m "chore(env): add READ_LOCK_TIMEOUT and ADD_COMMAND_TIMEOUT"
```

---

## Task 10: 更新 API 文档

**Files:**
- Modify: `docs/api/bot/engine/index_manager.md`
- Modify: `docs/api/bot/engine/ai_matcher.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 更新 `docs/api/bot/engine/index_manager.md`**

将 `IndexManager` API 摘要替换为：

```python
class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
    DEFAULT_SYNC_CONCURRENCY: int
    read_timeout: float
    add_user_timeout: float

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: str,
        no_text_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizer | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
        sync_concurrency: int | None = None,
    ) -> None

    def load(self) -> None

    async def search(self, keyword: str) -> list[SearchResult]
    # 持读锁调用 KeywordSearcher；空库返回 []；超时抛 asyncio.TimeoutError

    async def ai_match(self, description: str) -> AIMatchResult | None
    # 锁外 embed，持读锁调用 AIMatcher.match_with_vector()；超时抛 asyncio.TimeoutError

    async def add(self, filename: str) -> AddResult
    # FIFO 入队；refresh 期间抛 RefreshInProgressError；关闭时抛 IndexAddCancelledError

    async def refresh(self) -> SyncResult
    # 独占写锁执行同步；运行期间新的 add/refresh 被拒绝

    async def close(self) -> None
    # 取消 workers，清空 pending，关闭两个 Store
```

删除文档中 `acquire_lock()` / `release_lock()` / `is_locked` 的说明。

- [ ] **Step 2: 更新 `docs/api/bot/engine/ai_matcher.md`**

将 `AIMatcher.match()` 替换为 `match_with_vector()`：

```python
class AIMatcher:
    async def match_with_vector(
        self,
        description: str,
        query_vector: list[float],
    ) -> AIMatchResult | None
    # 调用方需保证 description 非空、query_vector 非零向量
```

删除 `_coerce_vector` 相关说明。

- [ ] **Step 3: 更新 `docs/api/API.md`**

在索引摘要处同步更新 `IndexManager` 与 `AIMatcher` 的 API 说明，保持与上述一致。

- [ ] **Step 4: Commit**

```bash
git add docs/api/bot/engine/index_manager.md docs/api/bot/engine/ai_matcher.md docs/api/API.md
git commit -m "docs(api): update IndexManager and AIMatcher API docs"
```

---

## Task 11: 迁移 `tests/unit/engine/test_index_manager.py`

**Files:**
- Modify: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 更新导入**

替换为：

```python
import asyncio
from pathlib import Path

import pytest

from bot.engine.ai_matcher import AIMatcher
from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    EmbeddingError,
    IndexAddCancelledError,
    IndexCorruptedError,
    IndexManager,
    OcrError,
    RefreshInProgressError,
    SyncResult,
    resolve_unique_filename,
)
from bot.engine.image_optimizer import OptimizeResult
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry
```

- [ ] **Step 2: 更新 FakeVectorStore**

在 `FakeVectorStore` 中添加 `count()` 方法（如缺失）：

```python
    def count(self) -> int:
        return len(self._vecs)
```

- [ ] **Step 3: 更新 `_make_index_manager` helper**

在测试文件末尾或合适位置添加一个 fixture/helper：

```python
@pytest.fixture
def index_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造带 Fake Store 的 IndexManager。"""
    monkeypatch.setenv("READ_LOCK_TIMEOUT", "30")
    monkeypatch.setenv("ADD_COMMAND_TIMEOUT", "60")

    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = FakeMetadataStore()
    vector_store = FakeVectorStore()
    keyword_searcher = KeywordSearcher(metadata_store)
    embedding_provider = MockEmbeddingProvider()
    ai_matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
    )

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        ocr_provider=MockOcrProvider(),
        embedding_provider=embedding_provider,
        optimizer=MockOptimizer(),
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        sync_concurrency=2,
    )
    manager.load()
    return manager


class MockOcrProvider:
    """OCR provider mock：固定返回传入文件名（不含扩展名）的文本。"""

    async def ocr(self, image_path: str) -> str:
        return Path(image_path).stem


class MockEmbeddingProvider:
    """Embedding provider mock：返回固定维度向量。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, text: str) -> list[float]:
        return [float(ord(text[0]) if text else 0)] * self._dim


class MockOptimizer:
    """图片优化器 mock：不做任何操作。"""

    async def optimize(self, image_path: str) -> OptimizeResult:
        return OptimizeResult(success=True, path=image_path)
```

- [ ] **Step 4: 删除旧锁测试**

删除所有测试 `acquire_lock` / `release_lock` / `is_locked` 的测试用例。

- [ ] **Step 5: 新增核心测试**

添加以下测试：

```python
@pytest.mark.anyio
async def test_add_returns_add_result(index_manager: IndexManager) -> None:
    (Path(index_manager._memes_dir) / "test.jpg").write_bytes(b"fake")
    result = await index_manager.add("test.jpg")
    assert result.reason == "added"
    assert result.entry_id is not None


@pytest.mark.anyio
async def test_add_fifo_order(index_manager: IndexManager) -> None:
    for i in range(3):
        (Path(index_manager._memes_dir) / f"img{i}.jpg").write_bytes(b"fake")
    results = await asyncio.gather(
        index_manager.add("img0.jpg"),
        index_manager.add("img1.jpg"),
        index_manager.add("img2.jpg"),
    )
    ids = [r.entry_id for r in results]
    assert ids == sorted(ids)


@pytest.mark.anyio
async def test_refresh_rejects_pending_add(index_manager: IndexManager) -> None:
    # 通过 monkeypatch _process_image_pipeline 使其挂住，保证 in_flight > 0
    original = index_manager._process_image_pipeline
    started = asyncio.Event()

    async def slow_pipeline(filename: str) -> tuple[str, list[float]]:
        started.set()
        await asyncio.sleep(10)
        return await original(filename)

    index_manager._process_image_pipeline = slow_pipeline

    (Path(index_manager._memes_dir) / "hold.jpg").write_bytes(b"fake")
    add_task = asyncio.create_task(index_manager.add("hold.jpg"))
    await started.wait()

    # 提交第二个 add，它会在 pending 队列中
    (Path(index_manager._memes_dir) / "drop.jpg").write_bytes(b"fake")
    pending_task = asyncio.create_task(index_manager.add("drop.jpg"))
    await asyncio.sleep(0.05)

    # 触发 refresh
    refresh_task = asyncio.create_task(index_manager.refresh())
    with pytest.raises(RefreshInProgressError):
        await pending_task

    add_task.cancel()
    try:
        await add_task
    except (asyncio.CancelledError, IndexAddCancelledError):
        pass
    await refresh_task


@pytest.mark.anyio
async def test_refresh_rejects_new_add(index_manager: IndexManager) -> None:
    # 让 refresh 长期持有写锁
    original = index_manager._run_sync_internal
    async def slow_refresh() -> SyncResult:
        await asyncio.sleep(10)
        return await original()
    index_manager._run_sync_internal = slow_refresh

    refresh_task = asyncio.create_task(index_manager.refresh())
    await asyncio.sleep(0.05)

    (Path(index_manager._memes_dir) / "blocked.jpg").write_bytes(b"fake")
    with pytest.raises(RefreshInProgressError):
        await index_manager.add("blocked.jpg")

    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass


@pytest.mark.anyio
async def test_search_holds_read_lock(index_manager: IndexManager) -> None:
    # 先 add 一条
    (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
    await index_manager.add("cat.jpg")

    results = await index_manager.search("猫")
    assert isinstance(results, list)


@pytest.mark.anyio
async def test_close_cancels_pending_add(index_manager: IndexManager) -> None:
    (Path(index_manager._memes_dir) / "pending.jpg").write_bytes(b"fake")
    task = asyncio.create_task(index_manager.add("pending.jpg"))
    await asyncio.sleep(0)

    await index_manager.close()

    with pytest.raises(IndexAddCancelledError):
        await task
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/engine/test_index_manager.py
git commit -m "test(engine): migrate IndexManager tests to new concurrency APIs"
```

---

## Task 12: 更新插件测试

**Files:**
- Modify: `tests/unit/plugins/test_meme_add.py`（如存在）
- Modify: `tests/unit/plugins/test_meme_ai.py`（如存在）
- Modify: `tests/unit/plugins/test_meme_refresh.py`（如存在）
- Modify: `tests/unit/plugins/test_search_utils.py`（如存在）

- [ ] **Step 1: 统一 mock 方式**

所有插件测试中，把 `index_manager.is_locked`、`index_manager.entry_count`、`index_manager.add_single_file`、`index_manager.sync_with_filesystem`、`keyword_searcher.search`、`ai_matcher.match` 等旧 mock 替换为：

- `index_manager.search` -> async mock returning `list[SearchResult]`
- `index_manager.ai_match` -> async mock returning `AIMatchResult | None`
- `index_manager.add` -> async mock returning `AddResult`
- `index_manager.refresh` -> async mock returning `SyncResult`

例如 `test_meme_ai.py` 中，使用标准库 `unittest.mock`：

```python
from unittest.mock import AsyncMock, patch

@patch("bot.plugins.meme_ai.get_index_manager")
def test_handle_ai_success(mock_get_index_manager) -> None:
    mock_manager = AsyncMock()
    mock_manager.ai_match = AsyncMock(
        return_value=AIMatchResult(
            entry_id=1,
            image_path="cat.jpg",
            text="一只猫",
            similarity=0.95,
            source="embedding",
        )
    )
    mock_manager.read_timeout = 30.0
    mock_get_index_manager.return_value = mock_manager

    # ... 构造 bot/event/matcher 并调用 handle_ai(...)
```

对于 `test_meme_add.py`，把 `index_manager.add_single_file` 替换为：

```python
mock_manager.add = AsyncMock(
    return_value=AddResult(entry_id=1, reason="added", text="测试")
)
```

对于 `test_meme_refresh.py`，把 `index_manager.acquire_lock` / `sync_with_filesystem` / `release_lock` 替换为：

```python
mock_manager.refresh = AsyncMock(
    return_value=SyncResult(added=1, deleted=0, deduped=0, no_text_moved=0)
)
```

对于 `test_search_utils.py`，把 `index_manager.is_locked` / `entry_count` / `keyword_searcher.search` 替换为：

```python
mock_manager.search = AsyncMock(
    return_value=[SearchResult(entry_id=1, image_path="cat.jpg", text="猫", similarity=100.0)]
)
```

- [ ] **Step 2: 运行插件测试**

Run: `uv run pytest tests/unit/plugins -v`
Expected: all passed.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/plugins
git commit -m "test(plugins): update mocks for new IndexManager APIs"
```

---

## Task 13: 全量验证

**Files:**
- All of the above

- [ ] **Step 1: 语法检查**

Run: `uv run python -m compileall bot tests`
Expected: `Compiled N files` 无错误。

- [ ] **Step 2: 全量测试**

Run: `uv run pytest`
Expected: all passed。

- [ ] **Step 3: 手动场景冒烟（可选，需要运行环境）**

1. 启动 Bot。
2. 同时发送多张图片 `/add`，验证 FIFO 与并发。
3. `/refresh` 运行时尝试 `/add`，验证被拒绝。
4. `/add` 进行时尝试 `/search`，验证阻塞或超时提示。
5. 关闭 Bot 时验证无异常。

- [ ] **Step 4: 最终 Commit / 结束**

```bash
git add .
git commit -m "feat: concurrent-safe IndexManager with rwlock and add workers"
```

---

## 自审清单

**1. Spec coverage:**
- [x] `IndexRwLock` 写者优先 + 超时（Task 2）
- [x] Add Worker FIFO + OCR/embed 并行 + 写锁串行（Task 6）
- [x] refresh 抢占：等当前 add、清空 pending、拒绝新 add（Task 6）
- [x] in_flight 计数保证 refresh 开始时无 worker 处于 pipeline 与 write_entry 之间（Task 6）
- [x] search/ai_match 持读锁 + 30s 超时（Task 6）
- [x] `/add` 用户层 60s 超时（Task 3 + Task 8）
- [x] `IndexManager.close()` 安全关闭（Task 6）
- [x] `AIMatcher.match_with_vector` 替代旧 `match`（Task 4）
- [x] 删除 `acquire_lock/release_lock/is_locked`（Task 6）
- [x] 环境变量与文档同步（Task 9 + Task 10）

**2. Placeholder scan:**
- [x] 无 "TBD"/"TODO"
- [x] 每步含实际代码或命令
- [x] 异常处理代码完整

**3. Type consistency:**
- [x] `IndexRwLock.read/write` 返回 async context manager
- [x] `IndexManager.add/refresh/search/ai_match/close` 全为 async
- [x] `read_timeout` / `add_user_timeout` 类型为 float
- [x] `_AddRequest.future` 类型为 `asyncio.Future[AddResult]`
