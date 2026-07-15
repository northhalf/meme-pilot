"""语义搜索器。"""

import logging

from bot.log_context import timed

from .metadata_store import MetadataStore
from .types import SearchResult
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class SemanticSearcher:
    """语义搜索器。

    基于 embedding 向量从 VectorStore 召回 Top-N 候选。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store

    @timed(logger, "语义搜索")
    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int | None = 10,
        *,
        collection_id: int | None = None,
    ) -> list[SearchResult]:
        """根据 embedding 向量召回最相似的 N 个表情包。

        Args:
            query_vector: 查询文本的 embedding 向量。
            limit: 召回数量上限；None 表示全库召回，默认 10。
            collection_id: 只召回该合集的向量；None 表示全库召回。

        Returns:
            与向量最相似的 SearchResult 列表；metadata 缺失的命中会被跳过。
        """
        logger.debug("语义搜索入口: limit=%s, collection_id=%s", limit, collection_id)
        hits = await self.vector_store.query(
            query_vector,
            n_results=limit,
            collection_id=collection_id,
        )
        entries = self.metadata_store.get_all_entries()
        results: list[SearchResult] = []
        for hit in hits:
            entry = entries.get(hit.entry_id)
            if entry is None:
                logger.warning(
                    "召回 hit 的 metadata 缺失，跳过：entry_id=%s", hit.entry_id
                )
                continue
            results.append(SearchResult.from_entry(entry, hit.similarity))
        logger.info("语义搜索返回 %d 个结果", len(results))
        return results
