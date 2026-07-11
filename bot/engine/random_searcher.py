"""随机取样搜索器。"""

import logging
import random

from bot.log_context import timed
from .keyword_searcher import KeywordSearcher
from .protocols import MetadataStoreProvider
from .types import SearchResult

logger = logging.getLogger(__name__)


class RandomSearcher:
    """随机取样搜索器。

    基于 KeywordSearcher 的结果或全库条目进行随机取样。

    Attributes:
        metadata_store: 元数据存储提供者，需实现 get_all_entries()。
        keyword_searcher: 关键词搜索器，用于有关键词时的候选召回。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        keyword_searcher: KeywordSearcher,
    ) -> None:
        self.metadata_store = metadata_store
        self.keyword_searcher = keyword_searcher

    def search_random(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """随机返回指定数量的表情包候选。

        Args:
            keyword: 可选关键词；None 或空串表示全库随机。
            limit: 返回数量上限，默认 10。

        Returns:
            随机取样后的 SearchResult 列表，候选不足时返回全部。
            关键词无匹配时返回空列表，不会回退到全库随机。
            所有结果的 similarity 字段固定为 0.0。
        """
        with timed(logger, "随机搜索"):
            logger.debug("随机搜索入口: keyword=%r, limit=%d", keyword, limit)
            if keyword:
                candidates = self.keyword_searcher.search(keyword)
                candidates = [
                    SearchResult(
                        entry_id=r.entry_id,
                        image_path=r.image_path,
                        text=r.text,
                        similarity=0.0,
                        speaker=r.speaker,
                        tags=r.tags,
                    )
                    for r in candidates
                ]
            else:
                entries = self.metadata_store.get_all_entries()
                candidates = [
                    SearchResult(
                        entry_id=entry.id,
                        image_path=entry.image_path,
                        text=entry.text,
                        similarity=0.0,
                        speaker=entry.speaker,
                        tags=entry.tags,
                    )
                    for entry in entries.values()
                    if entry.text
                ]

            if not candidates:
                logger.info("随机搜索返回 0 个结果")
                return []

            if len(candidates) <= limit:
                logger.info("随机搜索返回 %d 个结果", len(candidates))
                return candidates

            results = random.sample(candidates, limit)
            logger.info("随机搜索返回 %d 个结果", len(results))
            return results
