# bot/engine/semantic_searcher.py — 语义搜索器

> `SemanticSearcher` 基于 embedding 向量从 `VectorStore` 召回 Top-N 候选，不调用 LLM 精排。

## 本地依赖协议

`MetadataEntryProvider` 已归一到 `protocols.py`（详见 [protocols.md](protocols.md)），此处仅保留 `SemanticSearcher` 独有的 `VectorQueryProvider`。

```python
class VectorQueryProvider(Protocol):
    """向量查询协议。"""
    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]: ...
```

## 类

```python
class SemanticSearcher:
    def __init__(
        self,
        metadata_store: MetadataEntryProvider,
        vector_store: VectorQueryProvider,
    ) -> None

    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[SearchResult]:
        """根据 embedding 向量召回最相似的 N 个表情包。

        Args:
            query_vector: 查询文本的 embedding 向量。
            limit: 召回数量上限，默认 10。

        Returns:
            与向量最相似的 SearchResult 列表；
            metadata 缺失的命中会被跳过（日志 warning）。
        """
```

## 行为说明

- 基于 `VectorStore.query()` 余弦相似度召回
- `metadata_store.get_entry()` 取元数据构建 `SearchResult`
- 元数据缺失的命中静默跳过并记录 warning
- 非线程安全：外部 `IndexManager.semantic_search` 锁外 embed 后持读锁调用
- 与 `ai_matcher` 的区别：不调用 LLM 精排，直接返回向量搜索结果
