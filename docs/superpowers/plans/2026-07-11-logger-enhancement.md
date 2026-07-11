# Logger 增强 Implementation Plan

> **实现演进说明**：本计划中的 `RequestIdFilter` 方案已被替换为 `RequestIdFormatter`
>（作用于 Handler 的 `logging.Formatter`），`RequestIdFilter` 类已从代码库中删除。
> 具体实现请以 `bot/log_context.py` 和 `bot/logging_config.py` 当前源码为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 engine 与 plugins 增加统一的 request_id 追踪、关键操作耗时统计和多级别日志，同时保持现有日志配置不变。

**Architecture：** 新增 `bot/log_context.py` 提供 `contextvars` 隐式 request_id 传播、`RequestIdFilter` 注入前缀、`timed` 耗时统计；在 `bot/logging_config.py` 注册 filter；在 engine 核心模块补充 INFO/DEBUG 日志；在插件命令入口用 `set_request_id()` 包装整个处理流程。

**Tech Stack：** Python 3.12、标准库 `logging`/`contextvars`/`time`/`uuid`、pytest

---

## File Map

| 文件 | 动作 | 职责 |
|---|---|---|
| `bot/log_context.py` | 新建 | request_id 上下文、filter、耗时统计工具 |
| `tests/unit/test_log_context.py` | 新建 | `log_context.py` 单元测试 |
| `bot/logging_config.py` | 修改 | 注册 `RequestIdFilter` |
| `bot/bot.py` | 修改 | 启动/关闭 INFO 日志补充 |
| `bot/engine/index_manager.py` | 修改 | load/refresh/add/update/delete 等日志 |
| `bot/engine/metadata_store.py` | 修改 | CRUD/搜索日志 |
| `bot/engine/vector_store.py` | 修改 | upsert/query/delete 日志 |
| `bot/engine/ai_matcher.py` | 修改 | match 流程日志 |
| `bot/engine/keyword_searcher.py` | 修改 | 搜索耗时与 request_id |
| `bot/engine/combined_searcher.py` | 修改 | 组合搜索日志 |
| `bot/engine/semantic_searcher.py` | 修改 | 语义搜索日志 |
| `bot/engine/random_searcher.py` | 修改 | 随机搜索日志 |
| `bot/engine/rerank_service.py` | 修改 | rerank 调用与耗时日志 |
| `bot/engine/image_optimizer.py` | 修改 | 压缩/转换耗时日志 |
| `bot/plugins/*.py` | 修改 | 命令入口 request_id 包装 |

---

### Task 1: 创建 `bot/log_context.py` 与单元测试

**Files:**
- Create: `bot/log_context.py`
- Create: `tests/unit/test_log_context.py`

- [ ] **Step 1: 写 failing 测试**

在 `tests/unit/test_log_context.py` 写入：

```python
"""bot/log_context.py 单元测试。"""

import logging

import pytest

from bot.log_context import (
    REQUEST_ID,
    RequestIdFilter,
    generate_request_id,
    get_request_id,
    set_request_id,
    timed,
)


def test_generate_request_id_format():
    """generate_request_id 应返回 16 位 hex 字符串。"""
    rid = generate_request_id()
    assert isinstance(rid, str)
    assert len(rid) == 8
    assert all(c in "0123456789abcdef" for c in rid)


def test_set_request_id_sets_and_resets():
    """set_request_id 应设置并恢复 request_id。"""
    assert get_request_id() is None
    with set_request_id("abc123"):
        assert get_request_id() == "abc123"
    assert get_request_id() is None


def test_set_request_id_nested():
    """嵌套 set_request_id 不应串号。"""
    with set_request_id("outer"):
        assert get_request_id() == "outer"
        with set_request_id("inner"):
            assert get_request_id() == "inner"
        assert get_request_id() == "outer"
    assert get_request_id() is None


def test_request_id_filter_injects_prefix(caplog):
    """RequestIdFilter 应在日志消息前注入 [req:xxx]。"""
    logger = logging.getLogger("test_request_id_filter")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    handler.addFilter(RequestIdFilter())
    logger.addHandler(handler)

    with set_request_id("rid123"):
        logger.info("测试消息")

    record = caplog.records[0]
    assert "[req:rid123] 测试消息" in record.msg


def test_request_id_filter_no_prefix_without_id(caplog):
    """无 request_id 时不应注入前缀。"""
    logger = logging.getLogger("test_request_id_filter_no_id")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    handler.addFilter(RequestIdFilter())
    logger.addHandler(handler)

    logger.info("无 id 消息")

    record = caplog.records[0]
    assert record.msg == "无 id 消息"


@pytest.mark.asyncio
async def test_timed_async_context_manager(caplog):
    """timed 异步上下文管理器应记录耗时。"""
    logger = logging.getLogger("test_timed_async")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    async with timed(logger, "异步操作"):
        pass

    assert any("异步操作 完成，耗时" in r.msg for r in caplog.records)


@pytest.mark.asyncio
async def test_timed_async_decorator(caplog):
    """timed 异步装饰器应记录耗时。"""
    logger = logging.getLogger("test_timed_async_deco")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    @timed(logger, "装饰操作")
    async def do_something():
        return 42

    result = await do_something()
    assert result == 42
    assert any("装饰操作 完成，耗时" in r.msg for r in caplog.records)


def test_timed_sync_context_manager(caplog):
    """timed 同步上下文管理器应记录耗时。"""
    logger = logging.getLogger("test_timed_sync")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    with timed(logger, "同步操作"):
        pass

    assert any("同步操作 完成，耗时" in r.msg for r in caplog.records)


def test_timed_sync_decorator(caplog):
    """timed 同步装饰器应记录耗时。"""
    logger = logging.getLogger("test_timed_sync_deco")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    @timed(logger, "同步装饰操作")
    def do_something():
        return 42

    result = do_something()
    assert result == 42
    assert any("同步装饰操作 完成，耗时" in r.msg for r in caplog.records)


@pytest.mark.asyncio
async def test_timed_records_failure_on_exception(caplog):
    """timed 在异常发生时应记录失败。"""
    logger = logging.getLogger("test_timed_fail")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    with pytest.raises(ValueError):
        async with timed(logger, "失败操作"):
            raise ValueError("boom")

    assert any("失败操作 失败，耗时" in r.msg for r in caplog.records)
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```bash
uv run pytest tests/unit/test_log_context.py -v
```

Expected: 多个 `ModuleNotFoundError: No module named 'bot.log_context'`

- [ ] **Step 3: 实现 `bot/log_context.py`**

创建 `bot/log_context.py`：

```python
"""日志上下文工具。

提供 request_id 的隐式传播、请求 ID 注入 filter 和操作耗时统计。
"""

import contextvars
import functools
import inspect
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

F = TypeVar("F", bound=Callable)

REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    """获取当前上下文的 request_id。"""
    return REQUEST_ID.get()


def generate_request_id() -> str:
    """生成短请求 ID。

    Returns:
        uuid hex 前 8 位，足够一次用户请求的全链路追踪。
    """
    return uuid.uuid4().hex[:8]


@contextmanager
def set_request_id(request_id: str | None) -> Iterator[None]:
    """设置当前上下文的 request_id，退出时自动恢复。"""
    token = REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        REQUEST_ID.reset(token)


class RequestIdFilter(logging.Filter):
    """把当前 request_id 注入日志消息前的 Filter。

    注意：本 Filter 应只在顶层 ``bot`` logger 上注册一次，子 logger 通过继承获得。
    重复注册会导致 ``[req:xxx]`` 前缀被重复添加。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rid = get_request_id()
        if rid is not None:
            record.msg = f"[req:{rid}] {record.msg}"
        return True


class timed:
    """操作耗时统计。

    支持三种用法：
    1. async with timed(logger, "操作名"):
           ...
    2. with timed(logger, "操作名"):
           ...
    3. @timed(logger, "操作名")
       def func(...): ...

    注意：同一个 ``timed`` 实例不宜被多个协程/线程并发使用，否则 ``_start`` 会被覆盖，
    导致计时不准。上下文管理器用法应每次创建新实例；装饰器用法天然顺序进入，安全。
    """

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        level: int = logging.DEBUG,
    ) -> None:
        self._logger = logger
        self._operation = operation
        self._level = level
        self._start: float = 0.0

    def __enter__(self) -> "timed":
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object | None,
    ) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        status = "失败" if exc_type is not None else "完成"
        self._logger.log(
            self._level,
            "%s %s，耗时 %.2f ms",
            self._operation,
            status,
            elapsed_ms,
        )

    async def __aenter__(self) -> "timed":
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object | None,
    ) -> None:
        self.__exit__(exc_type, _exc_val, _exc_tb)

    def __call__(self, func: F) -> F:
        """支持装饰器用法。"""
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: object, **kwargs: object) -> object:
                async with self:
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: object, **kwargs: object) -> object:
            with self:
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```bash
uv run pytest tests/unit/test_log_context.py -v
```

Expected: 所有测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/log_context.py tests/unit/test_log_context.py
git commit -m "feat(log): 新增 log_context 工具（request_id、RequestIdFilter、timed）"
```

---

### Task 2: 在 `bot/logging_config.py` 注册 `RequestIdFilter`

**Files:**
- Modify: `bot/logging_config.py`
- Test: `tests/unit/test_logging_config.py`（回归）

- [ ] **Step 1: 修改 `bot/logging_config.py`**

在文件顶部新增 import：

```python
from bot.log_context import RequestIdFilter
```

在 `setup_logging()` 函数末尾、`bot_logger.propagate = False` 之前添加：

```python
    # 注入 request_id 前缀 filter（只在顶层 bot logger 注册一次）
    bot_logger.addFilter(RequestIdFilter())
```

完整函数关键部分应如下：

```python
def setup_logging(log_dir: str = "log") -> None:
    # ... 原有 handler 配置 ...
    bot_logger = logging.getLogger("bot")
    bot_logger.setLevel(logging.DEBUG)
    bot_logger.addHandler(file_handler)
    bot_logger.addHandler(stream_handler)
    bot_logger.addFilter(RequestIdFilter())
    bot_logger.propagate = False
```

- [ ] **Step 2: 运行现有日志配置测试**

Run:
```bash
uv run pytest tests/unit/test_logging_config.py -v
```

Expected: 所有测试 PASS（我们只加了 filter，formatter/handler 未变）

- [ ] **Step 3: 提交**

```bash
git add bot/logging_config.py
git commit -m "feat(log): 在 bot logger 注册 RequestIdFilter"
```

---

### Task 3: 补充 `bot/bot.py` 启动/关闭日志

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: 在 `_on_startup` 各阶段补充 INFO 日志**

在 `bot/bot.py` 的 `_on_startup` 中：

1. 在 `ocr_service = create_ocr_provider(...)` 之后添加：

```python
    logger.info("Rerank 服务已初始化")
    logger.info(
        "图片优化器已初始化: convert_to_webp=%s",
        read_convert_to_webp(),
    )
```

2. 在 `await index_manager.load()` 之后添加：

```python
    logger.info("IndexManager 索引加载完成")
```

3. 在 `init_app(...)` 调用之后保持原有日志不变。

- [ ] **Step 2: 在 `_on_shutdown` 补充日志**

已有 `IndexManager 已关闭`、`OCR 服务 HTTP 会话已关闭`、`Embedding 服务 HTTP 会话已关闭` 日志，保持不变。

- [ ] **Step 3: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/bot.py
```

Expected: Compiled 1 files.

- [ ] **Step 4: 提交**

```bash
git add bot/bot.py
git commit -m "feat(log): 补充启动阶段服务初始化日志"
```

---

### Task 4: 补充 `bot/engine/index_manager.py` 日志

**Files:**
- Modify: `bot/engine/index_manager.py`

- [ ] **Step 1: 导入 `timed`**

在文件顶部已有 `import logging` 后添加：

```python
from bot.log_context import timed
```

- [ ] **Step 2: 为 `load()` 增加日志**

在 `load()` 方法入口添加：

```python
        logger.info("开始加载索引...")
```

在 `load()` 方法成功返回前添加：

```python
        logger.info("索引加载完成")
```

- [ ] **Step 3: 为 `refresh()` 增加日志与耗时**

将 `refresh()` 方法体用 `timed` 包装：

```python
    async def refresh(self) -> RefreshResult:
        """刷新索引..."""
        async with timed(logger, "索引刷新"):
            logger.info("开始刷新索引...")
            # ... 原有实现 ...
            logger.info(
                "索引刷新完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
                result.added,
                result.deleted,
                result.deduped,
                result.no_text_moved,
                len(result.failed),
            )
            return result
```

- [ ] **Step 4: 为 `add()` / `update()` / `delete()` 增加日志**

在 `add()` 方法入口添加：

```python
        logger.info("添加图片: %s", image_path)
```

在成功返回前添加：

```python
        logger.info("图片添加完成: %s", image_path)
```

`update()` 和 `delete()` 同理：

```python
        logger.info("更新图片: %s", image_path)
        # ...
        logger.info("图片更新完成: %s", image_path)
```

```python
        logger.info("删除图片: %s", filename)
        # ...
        logger.info("图片删除完成: %s", filename)
```

- [ ] **Step 5: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/engine/index_manager.py
```

Expected: Compiled 1 files.

- [ ] **Step 6: 提交**

```bash
git add bot/engine/index_manager.py
git commit -m "feat(log): 补充 IndexManager 生命周期与耗时日志"
```

---

### Task 5: 补充 `bot/engine/metadata_store.py` 日志

**Files:**
- Modify: `bot/engine/metadata_store.py`

- [ ] **Step 1: 为 CRUD 方法增加 DEBUG 日志**

在 `add_or_update()` 成功处理一批数据后添加：

```python
        logger.debug("添加/更新 %d 条元数据", len(records))
```

在 `get()` 方法中添加（未找到时）：

```python
        if row is None:
            logger.debug("未找到元数据: %s", filename)
            return None
```

在 `search()` 方法返回前添加：

```python
        logger.debug("关键词搜索返回 %d 条结果", len(results))
```

在 `delete()` 方法中添加：

```python
        logger.debug("删除元数据: %s", filename)
```

- [ ] **Step 2: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/engine/metadata_store.py
```

Expected: Compiled 1 files.

- [ ] **Step 3: 提交**

```bash
git add bot/engine/metadata_store.py
git commit -m "feat(log): 补充 MetadataStore 操作日志"
```

---

### Task 6: 补充 `bot/engine/vector_store.py` 日志

**Files:**
- Modify: `bot/engine/vector_store.py`

- [ ] **Step 1: 为向量操作增加 DEBUG 日志**

在 `upsert()` 中添加：

```python
        logger.debug("upsert %d 条向量，维度=%d", len(ids), len(embedding))
```

在 `query()` 返回前添加：

```python
        logger.debug("向量查询返回 %d 个候选", len(results))
```

在 `delete()` 中添加：

```python
        logger.debug("删除向量: %s", ids)
```

- [ ] **Step 2: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/engine/vector_store.py
```

Expected: Compiled 1 files.

- [ ] **Step 3: 提交**

```bash
git add bot/engine/vector_store.py
git commit -m "feat(log): 补充 VectorStore 操作日志"
```

---

### Task 7: 补充 `bot/engine/ai_matcher.py` 与 searcher 日志

**Files:**
- Modify: `bot/engine/ai_matcher.py`
- Modify: `bot/engine/keyword_searcher.py`
- Modify: `bot/engine/combined_searcher.py`
- Modify: `bot/engine/semantic_searcher.py`
- Modify: `bot/engine/random_searcher.py`

- [ ] **Step 1: 修改 `ai_matcher.py`**

导入 `timed`：

```python
from bot.log_context import timed
```

在 `match()` 方法中用 `timed` 包装并补充日志：

```python
    async def match(self, description: str, top_k: int = 5) -> list[MatchResult]:
        """..."""
        async with timed(logger, "AI 语义匹配"):
            logger.info("AI 匹配描述: %r, top_k=%d", description, top_k)
            embedding = await self._embedding_provider.embed(description)
            candidates = await self._vector_store.query(embedding, top_k=top_k * 3)
            logger.info("向量召回 %d 个候选", len(candidates))
            if not candidates:
                return []
            reranked = await self._rerank_provider.rerank(
                description, candidates, top_k
            )
            logger.info("rerank 后返回 %d 个结果", len(reranked))
            return reranked
```

- [ ] **Step 2: 修改 `keyword_searcher.py`**

导入 `timed`：

```python
from bot.log_context import timed
```

在 `search()` 主路径用 `timed` 包装：

```python
    async def search(self, keyword: str, top_k: int = 5) -> list[SearchResult]:
        """..."""
        async with timed(logger, "关键词搜索"):
            # ... 原有实现 ...
            logger.info("关键词搜索: %r, 返回 %d 个结果", keyword, len(results))
            return results
```

- [ ] **Step 3: 修改 `combined_searcher.py`**

在 `search()` 入口和出口添加：

```python
        logger.debug("组合搜索: keyword=%r, description=%r", keyword, description)
        # ... 原有实现 ...
        logger.info("组合搜索返回 %d 个结果", len(results))
```

- [ ] **Step 4: 修改 `semantic_searcher.py`**

在 `search()` 入口和出口添加：

```python
        logger.debug("语义搜索: description=%r, top_k=%d", description, top_k)
        # ... 原有实现 ...
        logger.info("语义搜索返回 %d 个结果", len(results))
```

- [ ] **Step 5: 修改 `random_searcher.py`**

在 `search()` 入口和出口添加：

```python
        logger.debug("随机搜索: top_k=%d", top_k)
        # ... 原有实现 ...
        logger.info("随机搜索返回 %d 个结果", len(results))
```

- [ ] **Step 6: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/engine/ai_matcher.py bot/engine/keyword_searcher.py bot/engine/combined_searcher.py bot/engine/semantic_searcher.py bot/engine/random_searcher.py
```

Expected: Compiled 5 files.

- [ ] **Step 7: 提交**

```bash
git add bot/engine/ai_matcher.py bot/engine/keyword_searcher.py bot/engine/combined_searcher.py bot/engine/semantic_searcher.py bot/engine/random_searcher.py
git commit -m "feat(log): 补充 AI 匹配与各类搜索日志及耗时"
```

---

### Task 8: 补充 `bot/engine/rerank_service.py` 与 `image_optimizer.py` 日志

**Files:**
- Modify: `bot/engine/rerank_service.py`
- Modify: `bot/engine/image_optimizer.py`

- [ ] **Step 1: 修改 `rerank_service.py`**

导入 `timed`：

```python
from bot.log_context import timed
```

在 `rerank()` 方法中用 `timed` 包装并补充日志：

```python
    async def rerank(
        self,
        query: str,
        candidates: list[MatchResult],
        top_k: int,
    ) -> list[MatchResult]:
        """..."""
        async with timed(logger, "Rerank"):
            logger.info("Rerank: 候选=%d, top_k=%d", len(candidates), top_k)
            # ... 原有实现 ...
            logger.info("Rerank 完成，返回 %d 个结果", len(results))
            return results
```

- [ ] **Step 2: 修改 `image_optimizer.py`**

导入 `timed`：

```python
from bot.log_context import timed
```

在 `optimize()` 或主要压缩方法中用 `timed` 包装：

```python
    async def optimize(self, path: Path) -> Path:
        """..."""
        async with timed(logger, "图片优化"):
            logger.debug("优化图片: %s", path.name)
            # ... 原有实现 ...
            logger.info("图片优化完成: %s -> %s", path.name, result.name)
            return result
```

- [ ] **Step 3: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/engine/rerank_service.py bot/engine/image_optimizer.py
```

Expected: Compiled 2 files.

- [ ] **Step 4: 提交**

```bash
git add bot/engine/rerank_service.py bot/engine/image_optimizer.py
git commit -m "feat(log): 补充 Rerank 与图片优化日志及耗时"
```

---

### Task 9: 在插件命令入口包装 `set_request_id`

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `bot/plugins/meme_query.py`
- Modify: `bot/plugins/meme_ai.py`
- Modify: `bot/plugins/meme_add.py`
- Modify: `bot/plugins/meme_edittext.py`
- Modify: `bot/plugins/meme_setspeaker.py`
- Modify: `bot/plugins/meme_refresh.py`
- Modify: `bot/plugins/meme_delete.py`
- Modify: `bot/plugins/meme_info.py`
- Modify: `bot/plugins/meme_sim.py`

- [ ] **Step 1: 通用修改模式**

对每个插件文件执行以下修改：

1. 在 import 区域添加：

```python
from bot.log_context import generate_request_id, set_request_id
```

2. 找到 `@matcher.handle()` 装饰的 `handle_xxx` 函数，在函数开头、获取 `user_id` 之后，用 `with set_request_id(...)` 包裹后续逻辑。

修改前示例：

```python
@matcher.handle()
async def handle_search(bot: Bot, event: Event, args: Message = CommandArg()):
    user_id = str(event.user_id)
    logger.info("用户 %s 调用 /search", user_id)
    # ...
```

修改后示例：

```python
@matcher.handle()
async def handle_search(bot: Bot, event: Event, args: Message = CommandArg()):
    user_id = str(event.user_id)
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /search", user_id)
        # ... 后续所有代码保持缩进一致 ...
```

3. 确保 `handle_xxx` 函数体所有代码都在 `with set_request_id(request_id):` 块内，包括 try/except 和返回值。

- [ ] **Step 2: 逐个应用模式**

按上述模式修改以下 10 个文件中的命令处理函数：

- `bot/plugins/meme_search.py` → `handle_search`
- `bot/plugins/meme_query.py` → `handle_query`
- `bot/plugins/meme_ai.py` → `handle_ai`
- `bot/plugins/meme_add.py` → `handle_add`
- `bot/plugins/meme_edittext.py` → `handle_edittext`
- `bot/plugins/meme_setspeaker.py` → `handle_setspeaker`
- `bot/plugins/meme_refresh.py` → `handle_refresh`
- `bot/plugins/meme_delete.py` → `handle_delete`
- `bot/plugins/meme_info.py` → `handle_info`
- `bot/plugins/meme_sim.py` → `handle_sim`

- [ ] **Step 3: 运行语法检查**

Run:
```bash
uv run python -m compileall bot/plugins/
```

Expected: Compiled 10 files.

- [ ] **Step 4: 提交**

```bash
git add bot/plugins/
git commit -m "feat(log): 插件命令入口统一包装 request_id"
```

---

### Task 10: 全量测试与回归验证

**Files:**
- Test: `tests/`

- [ ] **Step 1: 运行全量单元测试**

Run:
```bash
uv run pytest tests/unit -v
```

Expected: 所有测试 PASS（包括 `test_log_context.py` 和 `test_logging_config.py`）

- [ ] **Step 2: 运行语法检查**

Run:
```bash
uv run python -m compileall bot tests
```

Expected: 无编译错误

- [ ] **Step 3: 运行类型检查（如项目已配置）**

Run:
```bash
uv run pyright bot/log_context.py
```

Expected: 无类型错误（如果项目配置了 pyright）

- [ ] **Step 4: 可选：手动查看日志输出**

启动 bot 并触发一条 `/search` 命令，检查 `log/bot.log` 中是否出现类似：

```
2026-07-11 10:23:45 - bot.plugins.meme_search - INFO - [req:a1b2c3d4] 用户 123456 调用 /search
2026-07-11 10:23:45 - bot.engine.keyword_searcher - INFO - [req:a1b2c3d4] 关键词搜索: 'doge', 返回 5 个结果
2026-07-11 10:23:45 - bot.engine.keyword_searcher - DEBUG - [req:a1b2c3d4] 关键词搜索 完成，耗时 12.34 ms
```

- [ ] **Step 5: 更新文档**

如果 `docs/api/API.md` 记录了新增模块的对外接口，补充 `bot/log_context.py` 的说明。根据 CLAUDE.md 要求，每实现一个模块后更新 `docs/api/API.md`。

- [ ] **Step 6: 最终提交（如所有检查通过）**

```bash
git add docs/api/API.md
git commit -m "docs(api): 更新 log_context 接口说明"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** 每个 spec 章节都有对应任务：
  - `log_context.py` API → Task 1
  - `RequestIdFilter` 注册 → Task 2
  - `bot.py` 启动日志 → Task 3
  - engine 各模块日志 → Task 4-8
  - plugins request_id → Task 9
  - 测试与回归 → Task 10
- [ ] **Placeholder scan:** 无 TBD/TODO/"implement later"/"similar to Task N"
- [ ] **Type consistency:** `timed`、`RequestIdFilter`、`set_request_id`、`generate_request_id` 名称与 spec 一致
- [ ] **Path consistency:** 文件路径使用项目实际路径
- [ ] **Test coverage:** 新增 `test_log_context.py` 覆盖核心行为

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-11-logger-enhancement.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints

**Which approach?**
