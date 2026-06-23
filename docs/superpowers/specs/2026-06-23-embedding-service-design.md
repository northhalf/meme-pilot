# Embedding Service 设计文档

> 日期：2026-06-23
> 状态：已实现

---

## 1. 概述

实现 `bot/engine/embedding_service.py`，通用 OpenAI 兼容 Embedding API 封装，为 AI 语义匹配提供文本向量化能力。

支持任何兼容 OpenAI embeddings API 的服务商（如 SiliconFlow、OpenAI、DeepSeek 等），只需配置 base_url 和 model 即可。

## 2. 协议

实现 `ai_matcher.EmbeddingProvider` 协议：

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]
```

## 3. 类设计

### EmbeddingService

| 属性 | 类型 | 说明 |
|------|------|------|
| `_client` | `AsyncOpenAI` | OpenAI 兼容客户端 |
| `_model` | `str` | Embedding 模型名 |

### 构造函数

```python
def __init__(
    self,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> None
```

参数优先级：构造参数 > 环境变量 > 默认值

| 参数 | 环境变量 | 默认值 |
|------|----------|--------|
| `api_key` | `EMBEDDING_API_KEY` | `""` |
| `base_url` | `EMBEDDING_BASE_URL` | `https://api.siliconflow.cn/v1` |
| `model` | `EMBEDDING_MODEL` | `BAAI/bge-m3` |

### embed 方法

```python
async def embed(self, text: str) -> list[float]
```

- 输入：待向量化文本
- 输出：1024 维浮点数列表
- 异常：空文本 → `ValueError`；API 失败 → `RuntimeError`

## 4. API 调用

使用 OpenAI 兼容的 embeddings API，显性指定向量维度为 1024：

```python
response = await self._client.embeddings.create(
    model=self._model,
    input=text,
    dimensions=1024,
)
return response.data[0].embedding
```

## 5. 错误处理

| 场景 | 处理 |
|------|------|
| 文本为空 | `raise ValueError("待向量化文本不能为空")` |
| API 调用异常 | `raise RuntimeError(f"Embedding API 调用失败: {exc}")` |
| 返回为空 | `raise RuntimeError("Embedding API 返回为空")` |

## 6. 日志

- DEBUG：调用前记录 model 和 text_len
- DEBUG：完成后记录 embedding 维度

## 7. 依赖

- `openai`（已在 `pyproject.toml` 中）

## 8. 代码风格

与 `ocr_service.py` 保持一致：
- Google 风格 docstring（中文）
- 类型标注
- 环境变量回退模式
