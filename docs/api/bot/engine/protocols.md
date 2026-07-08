# bot/engine/protocols.py - 共享协议定义

> 本文档只记录模块对外接口。engine 包各模块共用的 Protocol 集中在此，避免重复定义。

## Protocol

### `EmbeddingProvider`

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `embed` | `text: str` - 待向量化的文本 | `list[float]` (1024 维) | 异步，生成文本向量，维度 1024 |

被以下模块使用：

- `ai_matcher.AIMatcher` - 用户描述向量化
- `index_manager.IndexManager` - 新增图片 OCR 文本向量化
- `openai_embedding.OpenAIEmbeddingService` - OpenAI 兼容 Embedding 实现
- `google_embedding.GoogleEmbeddingService` - Google Embedding 实现

---

### `MetadataEntryProvider`

```python
class MetadataEntryProvider(Protocol):
    def get_entry(self, entry_id: int) -> MemeEntry | None: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_entry` | `entry_id: int` - 索引 id | `MemeEntry \| None` | 按 id 取条目；不存在返回 `None` |

消费者通过此协议按 id 取 `MemeEntry` 构建候选，而非直接依赖具体的 `MetadataStore` 实现，便于测试用 mock 替换。与 `keyword_searcher.MetadataStoreProvider`（`get_all_entries`）接口不同，此协议只暴露 `get_entry`。

被以下模块使用：

- `ai_matcher.AIMatcher` - 按 id 取 `MemeEntry` 构建候选
- `semantic_searcher.SemanticSearcher` - 按 id 取 `MemeEntry` 构建 `SearchResult`
