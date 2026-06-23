# bot/engine/embedding_service.py — Embedding 服务 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 类

### `EmbeddingService`

通用 Embedding 服务，通过 OpenAI 兼容 API 生成文本向量。

实现 `ai_matcher.EmbeddingProvider` 协议，可直接注入给 `AIMatcher` 使用。

支持任何兼容 OpenAI embeddings API 的服务商（如 SiliconFlow、OpenAI、DeepSeek 等），只需配置 `base_url` 和 `model` 即可。

```python
class EmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None

    async def embed(self, text: str) -> list[float]
```

---

## 构造函数

### `__init__(api_key=None, base_url=None, model=None) -> None`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API Key，默认从 `EMBEDDING_API_KEY` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `EMBEDDING_BASE_URL` 环境变量读取，回退为 `https://api.siliconflow.cn/v1` |
| `model` | `str \| None` | `None` | Embedding 模型名，默认从 `EMBEDDING_MODEL` 环境变量读取，回退为 `BAAI/bge-m3` |

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

通过 OpenAI 兼容的 embeddings API 将文本转换为浮点向量，显性指定 `dimensions=1024`。

---

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `EMBEDDING_API_KEY` | API Key | `""` |
| `EMBEDDING_BASE_URL` | API 地址 | `https://api.siliconflow.cn/v1` |
| `EMBEDDING_MODEL` | 模型名 | `BAAI/bge-m3` |
