# Logger 增强设计文档

> **实现演进说明**：本文档为初始设计稿。实际代码中 `RequestIdFilter` 已被删除，
> 改为在 `RequestIdFormatter`（作用于 Handler 的 `logging.Formatter`）中注入
> `[req:xxx]` 前缀。具体实现请以 `bot/log_context.py` 和 `bot/logging_config.py`
> 的当前源码为准。

## 1. 背景与目标

当前项目使用 Python 标准库 `logging`，通过 `bot/logging_config.py` 配置顶层 `bot` logger，输出到：

- `stdout`（`INFO+`）
- `log/bot.log`（`DEBUG+`，1 MB 滚动，保留 1 个备份）

插件层日志相对完善，但 engine 核心模块（`index_manager`、`metadata_store`、`vector_store`、`ai_matcher`、`rerank_service`、`image_optimizer` 等）存在日志稀疏、缺少关键路径信息的问题。

**目标**：在不引入新依赖、保持纯文本格式的前提下，为 engine + plugins 增加多级别日志，并统一记录：

- 关键操作耗时
- 请求/任务 ID（request_id）
- 更完整的异常上下文

## 2. 设计原则

- **最小改动现有日志配置**：保留 `bot/logging_config.py` 的 formatter、handler、滚动策略和现有测试；仅新增 `RequestIdFilter` 到顶层 `bot` logger。
- **无新依赖**：仅使用标准库 `logging`、`contextvars`、`functools`、`inspect`、`time`。
- **最小侵入**：通过 `contextvars` 隐式传播 request_id，避免逐层函数参数透传。
- **统一格式**：request_id 以 `[req:xxx]` 前缀注入日志消息；耗时日志统一为 `{操作} 完成/失败，耗时 x.xx ms`。
- **级别分明**：
  - `INFO`：关键生命周期与结果摘要
  - `DEBUG`：详细诊断与中间状态
  - `WARNING`：可恢复异常或需要注意的状态
  - `ERROR/EXCEPTION`：不可恢复错误

## 3. 新增模块：`bot/log_context.py`

### 3.1 核心 API

| 组件 | 类型 | 说明 |
|---|---|---|
| `REQUEST_ID` | `ContextVar[str \| None]` | 保存当前请求 ID |
| `get_request_id()` | 函数 | 读取当前 request_id |
| `generate_request_id()` | 函数 | 生成短 request_id |
| `set_request_id(request_id)` | 上下文管理器 | 设置/恢复当前 request_id |
| `RequestIdFilter` | `logging.Filter` | 将 `[req:<id>]` 注入日志消息前 |
| `timed` | 类 | 支持 `with` / `async with` / 装饰器的耗时统计 |

### 3.2 实现

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

### 3.3 与现有 logging 集成

在 `bot/logging_config.py` 的 `setup_logging()` 中，给顶层 `bot` logger 添加 `RequestIdFilter`：

```python
from bot.log_context import RequestIdFilter

# ... 原有 handler 配置不变 ...
bot_logger = logging.getLogger("bot")
bot_logger.addFilter(RequestIdFilter())
```

所有子 logger（`bot.plugins.*`、`bot.engine.*`）通过继承自动获得该 filter。`RequestIdFilter` 在无 request_id 时不做任何修改。

## 4. Request ID 传播

### 4.1 插件命令入口

每个插件命令处理函数入口生成短 ID（`uuid.uuid4().hex[:8]`），并用 `set_request_id` 包裹整个处理流程：

```python
from bot.log_context import generate_request_id, set_request_id

@matcher.handle()
async def handle_search(bot: Bot, event: Event, args: Message = CommandArg()):
    user_id = str(event.user_id)
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /search", user_id)
        # ... 后续 engine 调用自动带上 request_id
```

### 4.2 Engine 层

Engine 层无需修改函数签名，直接使用 logger：

```python
from bot.log_context import timed

async def match(self, description: str, top_k: int = 5) -> list[MatchResult]:
    async with timed(logger, "语义匹配"):
        logger.debug("匹配描述: %r, top_k=%d", description, top_k)
        embedding = await self._embedding_provider.embed(description)
        candidates = await self._vector_store.query(embedding, top_k=top_k * 3)
        logger.info("向量召回 %d 个候选", len(candidates))
        ranked = await self._rerank_provider.rerank(description, candidates, top_k)
        return ranked
```

### 4.3 线程池边界

`contextvars` 在 `asyncio.to_thread` 中不会自动带到线程里。对于线程内仍需打印日志的场景，在线程函数内显式恢复：

```python
from bot.log_context import get_request_id, set_request_id

async def embed(self, text: str) -> list[float]:
    rid = get_request_id()
    async with timed(logger, "Google Embedding"):
        def _call():
            with set_request_id(rid):
                return self._client.models.embed_content(...)
        response = await asyncio.to_thread(_call)
```

### 4.4 后台任务

后台任务（如 `_background_sync`）无用户请求，使用固定 ID：

```python
async def _background_sync(index_manager: IndexManager) -> None:
    with set_request_id("background"):
        logger.info("开始后台索引同步...")
```

## 5. 耗时记录范围

使用 `timed` 装饰或上下文管理记录以下操作：

- OCR 单图识别
- Embedding 单条/批量
- Rerank 重排序
- 关键词搜索 / 语义搜索 / 组合搜索 / 随机搜索
- 索引刷新整体及子阶段（扫描、OCR、Embedding、入库）
- 图片压缩 / WebP 转换
- MetadataStore / VectorStore 的批量写入

## 6. 各模块补充要点

### 6.1 `bot/plugins/*`

- 命令入口用 `set_request_id` 包装。
- 保留现有 `INFO` 日志。
- 在关键分支补充 `DEBUG`/`WARNING`：缺少参数、权限校验、锁超时、无结果、取消操作等。
- 异常处使用 `logger.exception(...)` 并自动携带 request_id。

### 6.2 `bot/engine/index_manager.py`

- `load()` / `refresh()`：记录开始、完成、结果摘要、总耗时。
- `add()` / `update()` / `delete()`：记录操作、文件名、成功/失败。
- 批处理 OCR / Embedding 进度：`DEBUG` 记录已处理数量。

### 6.3 `bot/engine/metadata_store.py`

- CRUD 操作记录 `DEBUG`，包括影响行数或结果数。
- 搜索/去重操作记录 `INFO` 摘要。

### 6.4 `bot/engine/vector_store.py`

- `upsert()` / `query()` / `delete()` 记录 `DEBUG`，包括 ids 数量、维度、距离类型。
- 批量操作记录总耗时。

### 6.5 `bot/engine/ai_matcher.py`

- `match()`：`INFO` 记录描述、候选数、最终排名数。
- `DEBUG` 记录 embedding 调用、rerank 调用、中间结果变化。

### 6.6 `bot/engine/keyword_searcher.py`

- 保留现有 `INFO`/`DEBUG`。
- 补充耗时和 request_id。

### 6.7 `bot/engine/combined_searcher.py` / `semantic_searcher.py` / `random_searcher.py`

- 补充入口/出口 `DEBUG`。
- 结果统计 `INFO`。

### 6.8 `bot/engine/rerank_service.py` / `image_optimizer.py`

- 调用 `INFO`。
- 耗时 `DEBUG`。
- 可恢复异常 `WARNING`。

### 6.9 `bot/bot.py`

- 启动/关闭增加更多服务初始化 `INFO`。
- request_id 不适用，不注入。

## 7. 配置变更

- `bot/logging_config.py`：在 `setup_logging()` 中给顶层 `bot` logger 添加 `RequestIdFilter`，不修改 formatter、handler 或滚动策略。
- 若未来需要按模块调整级别，可通过标准库 `logging.getLogger("bot.engine.xxx").setLevel(...)` 实现，不纳入本次改动。

## 8. 测试策略

### 8.1 单元测试

新增 `tests/unit/test_log_context.py`：

- `set_request_id` 设置/恢复正确。
- 嵌套 `set_request_id` 不串号。
- `RequestIdFilter` 在有/无 request_id 时行为正确。
- `timed` 作为上下文管理器和装饰器时正确记录耗时。
- `timed` 在异常发生时记录“失败”。

### 8.2 集成测试

- 选取 1-2 个插件命令，验证日志输出包含 `[req:...]`。
- 选取 1-2 个 engine 方法，验证 `timed` 输出包含 `完成，耗时 x.xx ms`。

### 8.3 回归测试

- 运行 `uv run pytest` 全量测试，确保现有日志相关测试（`test_logging_config.py`）不受影响。

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| 修改 `record.msg` 破坏 lazy formatting | 只拼接 `record.msg`，保留 `record.args`，Formatter 正常处理 |
| 无 request_id 时误加前缀 | `RequestIdFilter` 只在 `rid is not None` 时修改 |
| 线程内丢失 request_id | 在 `asyncio.to_thread` 前捕获并在线程函数内恢复 |
| 嵌套 `set_request_id` 覆盖 | `ContextVar.set/reset` 通过 token 恢复，嵌套安全 |
| 日志量过大 | `DEBUG` 默认只写入文件不输出 stdout；`INFO` 控制密度 |

## 10. 不引入 loguru 的原因

虽然 `loguru` 功能丰富，但：

- 项目已稳定使用标准库 `logging`。
- 现有测试依赖标准库 handler 类型。
- 标准库已能满足 request_id、耗时、多级别需求。
- 引入新依赖需要重新评估配置、Docker 镜像和文档。

因此本次保持标准库 `logging`。
