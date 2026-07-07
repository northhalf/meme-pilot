# bot/engine/random_searcher.py — 随机取样搜索器

> `RandomSearcher` 从 `KeywordSearcher` 搜索结果或全库 `MetadataStore` 条目中随机取样返回。

## 类

```python
class RandomSearcher:
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        keyword_searcher: KeywordSearcher,
    ) -> None

    def search_random(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]
        """随机返回指定数量的表情包候选。
        
        Args:
            keyword: 可选关键词；None 或空串表示全库随机。
            limit: 返回数量上限，默认 10。
        
        Returns:
            随机取样后的 SearchResult 列表，候选不足时返回全部。
            有关键词但无匹配时返回空列表（不回退到全库随机）。
            所有结果的 similarity 字段固定为 0.0。
        """
```

## 依赖协议

- `keyword_searcher.MetadataStoreProvider` — 需要 `get_all_entries()` 获取全量条目（全库随机时使用）
- `KeywordSearcher.search()` — 关键词搜索（有关键词时使用）

## 行为说明

- 有关键词时：委托 `KeywordSearcher.search()` 结果中随机取样
- 无关键词时：从 `MetadataStore.get_all_entries()` 全量中随机取样，过滤 `text` 为空的条目
- 非线程安全：外部 `IndexManager.random_search` 持读锁调用
