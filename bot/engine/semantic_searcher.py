"""语义搜索器。"""

import logging

from bot.log_context import timed

from .protocols import MetadataStoreProvider, VectorQueryProvider
from .types import SearchResult

logger = logging.getLogger(__name__)


class SemanticSearcher:
    """语义搜索器。

    基于 embedding 向量从 VectorStore 召回 Top-N 候选。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        vector_store: VectorQueryProvider,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store

    @timed(logger, "语义搜索")
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
            与向量最相似的 SearchResult 列表；metadata 缺失的命中会被跳过。
        """
        logger.debug("语义搜索入口: limit=%s", limit)
        hits = await self.vector_store.query(query_vector, n_results=limit)
        entries = self.metadata_store.get_all_entries()
        results: list[SearchResult] = []
        for hit in hits:
            entry = entries.get(hit.entry_id)
            if entry is None:
                logger.warning(
                    "召回 hit 的 metadata 缺失，跳过：entry_id=%s", hit.entry_id
                )
                continue
            results.append(
                SearchResult(
                    entry_id=entry.id,
                    image_path=entry.image_path,
                    text=entry.text,
                    similarity=hit.similarity,
                    speaker=entry.speaker,
                    tags=entry.tags,
                )
            )
        logger.info("语义搜索返回 %d 个结果", len(results))
        return results
