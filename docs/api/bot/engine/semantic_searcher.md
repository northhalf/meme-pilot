# bot/engine/semantic_searcher.py - 语义搜索器

> `SemanticSearcher` 基于 embedding 向量从 `VectorStore` 召回候选（`limit=None` 全库召回），不调用 LLM 精排。

## 依赖类型

`SemanticSearcher` 依赖的协议与数据类型均从 engine 共享模块导入，本模块不再本地定义：

- `MetadataStoreProvider`（`get_all_entries`）- 定义于 `protocols.py`，详见 [protocols.md](protocols.md)；用于批量映射 metadata
- `VectorQueryProvider`（`count` + `async query`）- 定义于 `protocols.py`，详见 [protocols.md](protocols.md)；本模块仅使用 `query` 召回 Top-N（`count` 为协议统一接口的一部分，由 `ai_matcher` 使用）
- `SearchResult` - 定义于 `types.py`，详见 [types.md](types.md)

## 类

```python
class SemanticSearcher:
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        vector_store: VectorQueryProvider,
    ) -> None

    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int | None = 10,
    ) -> list[SearchResult]:
        """根据 embedding 向量召回最相似的 N 个表情包。

        Args:
            query_vector: 查询文本的 embedding 向量。
            limit: 召回数量上限；None 表示全库召回，默认 10。

        Returns:
            与向量最相似的 SearchResult 列表；
            metadata 缺失的命中会被跳过（日志 warning）。
        """
```

## 行为说明

- 基于 `VectorStore.query()` 余弦相似度召回（`limit=None` 时全库召回）
- `metadata_store.get_all_entries()` 一次取全库 dict 按 `hit.entry_id` 批量映射构建 `SearchResult`，避免逐条 `get_entry`
- 元数据缺失的命中静默跳过并记录 warning
- 非线程安全：外部 `IndexManager.semantic_search` 锁外 embed 后持读锁调用
- 与 `ai_matcher` 的区别：不调用 LLM 精排，直接返回向量搜索结果
