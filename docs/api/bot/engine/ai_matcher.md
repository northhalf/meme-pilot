# bot/engine/ai_matcher.py — AI 语义匹配 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

先用 embedding 做语义召回（ChromaDB），再可选调用精排 provider 选出最终表情包。

## Protocol

### `MetadataEntryProvider`

```python
class MetadataEntryProvider(Protocol):
    def get_entry(self, entry_id: int) -> MemeEntry | None: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_entry` | `entry_id: int` — 索引 id | `MemeEntry \| None` | 按 id 取条目；不存在返回 `None` |

`AIMatcher` 依赖此协议按 id 取 `MemeEntry` 构建候选，而非直接依赖具体的 `MetadataStore` 实现，便于测试用 mock 替换。与 `keyword_searcher.MetadataStoreProvider`（`get_all_entries`）接口不同，此协议只暴露 `AIMatcher` 实际使用的 `get_entry`。

---

### `VectorQueryProvider`

```python
class VectorQueryProvider(Protocol):
    def count(self) -> int: ...
    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `count` | 无 | `int` | 当前向量数 |
| `query` | `query_embedding: list[float]`；`n_results: int = 10` | `list[VectorHit]` | 召回 Top-N，按 `similarity` 降序 |

`AIMatcher` 依赖此协议做向量召回与空库判断，而非直接依赖具体的 `VectorStore` 实现，便于测试用 mock 替换。

---

### `RerankProvider`

```python
class RerankProvider(Protocol):
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `rerank` | `description` — 用户描述；`candidates` — embedding Top N 候选 | `int` | 返回 1-based 临时候选序号；返回 `0` 表示放弃精排 |

---

## 数据类

### `AIMatchCandidate`

```python
@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | `int` | 临时候选序号，1-based |
| `entry_id` | `int` | 索引 id |
| `image_path` | `str` | `memes/` 目录下相对路径 |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | 与用户描述 embedding 的余弦相似度 |

---

### `AIMatchResult`

```python
@dataclass(frozen=True)
class AIMatchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id |
| `image_path` | `str` | `memes/` 目录下相对路径 |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | embedding 余弦相似度 |
| `source` | `str` | 结果来源：`"embedding"` 或 `"rerank"` |

---

## `AIMatcher` 类

### `__init__(metadata_store, vector_store, embedding_provider, rerank_provider=None, limit=10) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `metadata_store` | `MetadataEntryProvider` | 必填 | 元数据提供者，按 id 取 `MemeEntry` 构建候选（如 `MetadataStore` 实例） |
| `vector_store` | `VectorQueryProvider` | 必填 | 向量提供者，`query` 召回 Top-N（如 `VectorStore` 实例） |
| `embedding_provider` | `EmbeddingProvider` | 必填 | 用户描述向量化服务 |
| `rerank_provider` | `RerankProvider \| None` | `None` | 可选候选精排服务 |
| `limit` | `int` | `10` | embedding 阶段最大候选数量 |

---

### `async match(description: str) -> AIMatchResult | None`

| | 类型 | 说明 |
|--|------|------|
| **参数** `description` | `str` | 用户自然语言描述 |
| **返回** | `AIMatchResult \| None` | 最终匹配结果；空描述、向量库为空或无有效候选时返回 `None` |
| **异常** | `ValueError` | 用户描述 embedding 为空、非数字或为零向量 |

流程：

1. 清洗用户描述（`strip`），空描述直接返回 `None`。
2. `VectorStore.count() == 0` 时直接返回 `None`，不调用 embedding provider。
3. 调用 `embedding_provider.embed(description)` 生成用户描述向量；provider 抛出的异常向外传播。
4. 用户描述向量为空列表、含非数字/非有限数字元素时抛 `ValueError`；为零向量时抛 `ValueError`。
5. `VectorStore.query(query_vector, n_results=limit)` 从 ChromaDB 召回 Top-N `VectorHit`（不设最低相似度阈值）。
6. 用 `MetadataStore.get_entry(hit.entry_id)` 将每个 hit 转为 `AIMatchCandidate`（带 1-based `rank`）；metadata 缺失的 hit 被跳过并记录 warning。无候选时返回 `None`。
7. 未配置 `rerank_provider` 时返回 embedding Top 1，`source="embedding"`。
8. 配置 `rerank_provider` 时使用精排结果，`source="rerank"`；精排抛异常、返回 `0`、返回非整数或越界时 fallback 到 embedding Top 1，`source="embedding"`。
