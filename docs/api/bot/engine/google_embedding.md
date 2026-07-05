# bot/engine/google_embedding.py — Google Embedding API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 类

### `GoogleEmbeddingService`

Google Embedding 服务，通过 Google GenAI SDK 生成文本向量。

实现 `protocols.EmbeddingProvider` 协议，可直接注入给 `AIMatcher` 使用。

```python
class GoogleEmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None

    async def embed(self, text: str) -> list[float]
    async def close(self) -> None
```

---

## 构造函数

### `__init__(api_key=None, base_url=None, model=None, concurrency=None) -> None`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | Google API Key，默认从 `GOOGLE_API_KEY` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `GOOGLE_BASE_URL` 环境变量读取；未设置时使用 Google 官方地址 |
| `model` | `str \| None` | `None` | Embedding 模型名，默认从 `GOOGLE_EMBEDDING_MODEL` 环境变量读取，示例默认值为 `gemini-embedding-001` |
| `concurrency` | `int \| None` | `None` | Embedding API 并发上限，默认从 `EMBEDDING_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 `embed()` 调用数。 |

参数优先级：构造参数 > 环境变量 > 默认值。

---

## 方法

### `embed(text: str) -> list[float]`

生成文本 embedding 向量。

| 参数 | 类型 | 说明 |
|------|------|------|
| `text` | `str` | 待向量化的文本 |

| 返回 | 说明 |
|------|------|
| `list[float]` (1024 维) | embedding 向量，维度固定为 1024 |

| 异常 | 说明 |
|------|------|
| `ValueError` | 文本为空 |
| `RuntimeError` | API 调用失败或返回为空 |

Google GenAI SDK 当前为同步 API，通过 `asyncio.to_thread` 在线程池中调用；固定请求 `output_dimensionality=1024`，与现有 ChromaDB 索引维度保持一致。

方法装饰有 `@api_retry(...)`，对 Google GenAI SDK 的 `APIError` 及 httpx 网络异常进行最多 3 次指数退避重试。

---

### `close() -> None`

Google GenAI SDK 同步 `Client` 持有 HTTP 连接池，调用 `close()` 可释放底层网络资源。由于 `close()` 是同步方法，通过 `asyncio.to_thread` 在线程池中执行，避免阻塞事件循环。

---

## 工厂函数

### `create_google_embedding_service() -> GoogleEmbeddingService`

从环境变量创建 `GoogleEmbeddingService` 实例。

| | 说明 |
|--|------|
| 并发数 | 通过 `bot.config.read_int_env("EMBEDDING_CONCURRENCY")` 读取，无效时 Service 内部回退为 5 |

通常由 `bot/engine/__init__.py` 注册为 `"google"` Embedding provider。

---

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `GOOGLE_API_KEY` | Google API Key | `""` |
| `GOOGLE_BASE_URL` | API 代理地址 | — |
| `GOOGLE_EMBEDDING_MODEL` | 模型名 | `gemini-embedding-001` |
| `EMBEDDING_CONCURRENCY` | Embedding API 并发上限 | `5` |
