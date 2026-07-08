# bot/engine/retry_config.py — 网络请求重试配置

> 提供基于 tenacity 的通用网络请求重试装饰器，供各 engine Service 复用。

## 类型别名

### `ExceptionTuple`

```python
ExceptionTuple: TypeAlias = tuple[type[Exception], ...]
```

异常类元组类型。

---

## 装饰器工厂

### `api_retry(...)`

```python
def api_retry(
    *,
    max_attempts: int = 3,
    wait_min: float = 1,
    wait_max: float = 10,
    multiplier: float = 1,
    extra_exceptions: ExceptionTuple = (),
):
```

网络请求通用重试装饰器工厂。返回一个 tenacity `retry` 装饰器。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_attempts` | `int` | `3` | 最大尝试次数 |
| `wait_min` | `float` | `1` | 首次重试等待秒数 |
| `wait_max` | `float` | `10` | 最大等待秒数 |
| `multiplier` | `float` | `1` | 指数退避乘数 |
| `extra_exceptions` | `ExceptionTuple` | `()` | 调用方额外指定的可重试异常类型 |

### 默认重试的异常

- `httpx.NetworkError`
- `httpx.ConnectError`
- `httpx.TimeoutException`
- `httpx.RemoteProtocolError`（服务端中途断连，属 `ProtocolError` 分支，非 `NetworkError` 子类）
- `ConnectionError`
- `TimeoutError`
- `extra_exceptions` 中传入的异常（如 `openai.APIConnectionError`、`google.genai.errors.APIError` 等）

### 不会重试的异常

- `ValueError`
- `FileNotFoundError`
- 其他本地/业务异常

### 行为

- 使用 `wait_exponential` 指数退避，乘数为 `multiplier`，等待范围 `[wait_min, wait_max]`。
- 每次重试前通过 `before_sleep_log` 记录 WARNING 级别日志。
- 超过 `max_attempts` 仍未成功时，抛出最后一次异常（`reraise=True`）。

### 使用示例

```python
from bot.engine.retry_config import api_retry
import openai

@api_retry(
    extra_exceptions=(
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )
)
async def call_api() -> str:
    ...
```
