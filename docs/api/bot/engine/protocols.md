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

消费者通过此协议按 id 取 `MemeEntry` 构建候选，而非直接依赖具体的 `MetadataStore` 实现，便于测试用 mock 替换。与 `MetadataStoreProvider`（`get_all_entries`）接口不同，此协议只暴露 `get_entry`。

被以下模块使用：

- `ai_matcher.AIMatcher` - 按 id 取 `MemeEntry` 构建候选

---

### `MetadataStoreProvider`

```python
class MetadataStoreProvider(Protocol):
    def get_all_entries(self) -> dict[int, MemeEntry]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_all_entries` | 无 | `dict[int, MemeEntry]` | key 为 `int(id)`，value 为 `MemeEntry`（含 `image_path`、`text` 等） |

需全量元数据快照的消费者通过此协议取条目，而非直接依赖具体的 `MetadataStore` 实现，便于测试用 mock 替换。

被以下模块使用：

- `keyword_searcher.KeywordSearcher` - 全量条目做 LCS 关键词匹配
- `random_searcher.RandomSearcher` - 全库随机时取全量条目
- `semantic_searcher.SemanticSearcher` - 批量映射 metadata 构建 `SearchResult`

---

### `VectorQueryProvider`

```python
class VectorQueryProvider(Protocol):
    def count(self) -> int: ...
    async def query(
        self, query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `count` | 无 | `int` | 当前向量数 |
| `query` | `query_embedding: list[float]`；`n_results: int \| None = 10` | `list[VectorHit]` | 召回 Top-N（`None` 全库），按 `similarity` 降序 |

向量召回消费者通过此协议做向量查询与空库判断，而非直接依赖具体的 `VectorStore` 实现，便于测试用 mock 替换。此协议为 `ai_matcher` 与 `semantic_searcher` 共用的统一接口（含 `count` + `query`），原先两个模块各自定义的 `VectorQueryProvider` 已归并到此。

被以下模块使用：

- `ai_matcher.AIMatcher` - `count` 判空库 + `query` 召回 Top-N 候选
- `semantic_searcher.SemanticSearcher` - `query` 召回 Top-N 候选
