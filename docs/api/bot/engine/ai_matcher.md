# bot/engine/ai_matcher.py — AI 语义匹配 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## Protocol

### `AIIndexProvider`

```python
class AIIndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]: ...
    def get_embeddings(self) -> dict[str, dict[str, object]]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_entries` | 无 | `dict[str, dict[str, str]]` | key 为索引 ID，value 为包含 `filename`、`text`、`text_hash` 的字典 |
| `get_embeddings` | 无 | `dict[str, dict[str, object]]` | key 为索引 ID，value 为包含 `text_hash`、`embedding` 的字典 |

---

### `EmbeddingProvider`

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `embed` | `text: str` — 待向量化文本 | `list[float]` (1024 维) | 异步生成文本 embedding 向量，维度 1024 |

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
    entry_id: str
    filename: str
    text: str
    similarity: float
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | `int` | 临时候选序号，1-based |
| `entry_id` | `str` | 索引 ID |
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | 与用户描述 embedding 的余弦相似度 |

---

### `AIMatchResult`

```python
@dataclass(frozen=True)
class AIMatchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float
    source: str
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 索引 ID |
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | embedding 余弦相似度 |
| `source` | `str` | 结果来源：`"rerank"` 或 `"embedding"` |

---

## `AIMatcher` 类

### `__init__(index_provider, embedding_provider, rerank_provider=None, limit=10) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `index_provider` | `AIIndexProvider` | 必填 | 索引与向量数据来源，如 `IndexManager` 实例 |
| `embedding_provider` | `EmbeddingProvider` | 必填 | 用户描述向量化服务 |
| `rerank_provider` | `RerankProvider \| None` | `None` | 可选候选精排服务 |
| `limit` | `int` | `10` | embedding 阶段最大候选数量 |

---

### `async match(description: str) -> AIMatchResult | None`

| | 类型 | 说明 |
|--|------|------|
| **参数** `description` | `str` | 用户自然语言描述 |
| **返回** | `AIMatchResult \| None` | 最终匹配结果；空描述、索引为空或无有效候选时返回 `None` |
| **异常** | `ValueError` | 用户描述 embedding 为空、非数字或为零向量 |

流程：

1. 清洗用户描述，空描述直接返回 `None`。
2. 读取索引 `entries` 与 `embeddings`；任一为空时直接返回 `None`，不调用 embedding provider。
3. 调用 `embedding_provider.embed(description)` 生成用户描述向量；provider 抛出的异常向外传播。
4. 用户描述向量为空列表、含非数字/非有限数字元素时抛 `ValueError`；为零向量时抛 `ValueError`。
5. 与 `get_embeddings()` 中的向量计算余弦相似度，不设最低阈值，取 Top `limit`。
6. 未配置 `rerank_provider` 时返回 embedding Top 1，`source="embedding"`。
7. 配置 `rerank_provider` 时使用精排结果，`source="rerank"`；精排抛异常、返回 `0`、返回非整数或越界时 fallback 到 embedding Top 1，`source="embedding"`。

单条索引 embedding 异常、维度不一致或为零向量时会被跳过，并记录 warning。相似度相同时优先按可转整数的 `entry_id` 数值升序、否则按字符串排序，保证结果稳定。
