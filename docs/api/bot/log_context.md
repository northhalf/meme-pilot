# bot/log_context.py — 日志上下文工具 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 模块级函数

### `get_request_id() -> str | None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `str | None` | 当前上下文 request_id，未设置时返回 `None` |

获取当前 `ContextVar` 中的 request_id。

### `generate_request_id() -> str`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `str` | 8 位 UUID hex 短请求 ID |

生成短请求 ID，足够一次用户请求的全链路追踪。

### `set_request_id(request_id: str | None) -> Generator[None, None, None]`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `request_id` | `str | None` | — | 要设置的请求 ID；`None` 表示清空 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `Generator[None, None, None]` | 上下文管理器，退出时自动恢复原有 request_id |

使用 `with set_request_id(rid): ...` 在代码块内临时设置 request_id，离开代码块后自动恢复。

### `run_sync_with_request_id(fn, *args, **kwargs) -> _T`

| 参数 | 类型 | 说明 |
|------|------|------|
| `fn` | `Callable[..., _T]` | 要在线程中执行的同步函数 |
| `*args` | `Any` | 传给 fn 的位置参数 |
| `**kwargs` | `Any` | 传给 fn 的关键字参数 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `_T` | fn 的返回值 |

在线程池中执行同步函数，并在线程内恢复当前 request_id。适用于 `asyncio.to_thread` 调用点，保证跨线程日志仍带 `[req:xxx]` 前缀。

## 类

### `RequestIdFilter(logging.Filter)`

把当前 request_id 注入日志消息前的 `logging.Filter`。

| 方法 | 签名 | 说明 |
|------|------|------|
| `filter` | `filter(self, record: logging.LogRecord) -> bool` | 若当前上下文存在 request_id，则在 `record.msg` 前追加 `[req:xxx]` 前缀 |

注意：本 Filter 应只在顶层 `bot` logger 上注册一次，子 logger 通过继承获得。重复注册会导致 `[req:xxx]` 前缀被重复添加。

### `timed`

操作耗时统计工具。支持三种用法：

1. `async with timed(logger, "操作名"):`
2. `with timed(logger, "操作名"):`
3. `@timed(logger, "操作名")`

#### `timed.__init__(logger, operation, level=logging.DEBUG)`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `logger` | `logging.Logger` | — | 输出耗时日志的 logger |
| `operation` | `str` | — | 操作名称，会出现在日志消息中 |
| `level` | `int` | `logging.DEBUG` | 日志级别 |

行为说明：

- 进入上下文时记录起始时间（`time.perf_counter()`）。
- 退出时计算耗时并输出：`"<operation> 完成/失败，耗时 <毫秒> ms"`。
- 若退出时发生异常，`status` 显示为 `"失败"`，否则为 `"完成"`。
- 装饰器用法会自动识别协程函数并包装为异步上下文。

注意：同一个 `timed` 实例不宜被多个协程/线程并发使用，否则 `_start` 会被覆盖导致计时不准。上下文管理器用法应每次创建新实例；装饰器用法天然顺序进入，安全。
