# bot/engine/protocols.py — 共享协议定义

> 本文档只记录模块对外接口。engine 包各模块共用的 Protocol 集中在此，避免重复定义。

## Protocol

### `EmbeddingProvider`

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `embed` | `text: str` — 待向量化的文本 | `list[float]` (1024 维) | 异步，生成文本向量，维度 1024 |

被以下模块使用：

- `ai_matcher.AIMatcher` — 用户描述向量化
- `index_manager.IndexManager` — 新增图片 OCR 文本向量化
- `openai_embedding.OpenAIEmbeddingService` — OpenAI 兼容 Embedding 实现
- `google_embedding.GoogleEmbeddingService` — Google Embedding 实现
