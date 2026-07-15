"""随机取样搜索器。"""

import logging
import random

from bot.log_context import timed

from .keyword_searcher import KeywordSearcher
from .metadata_store import MemeEntry, MetadataStore
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
        metadata_store: MetadataStore,
        keyword_searcher: KeywordSearcher,
    ) -> None:
        self.metadata_store = metadata_store
        self.keyword_searcher = keyword_searcher

    @timed(logger, "随机搜索")
    def search_random(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """随机返回指定数量的表情包候选（全库兼容包装）。

        Args:
            keyword: 可选关键词；None 或空串表示全库随机。
            limit: 返回数量上限，默认 10。

        Returns:
            随机取样后的 SearchResult 列表，候选不足时返回全部。
            有关键词但无匹配时返回空列表（不回退到全库随机）。
            所有结果的 similarity 字段固定为 0.0。
        """
        logger.debug("随机搜索入口: keyword=%r, limit=%d", keyword, limit)
        entries = self.metadata_store.get_all_entries()
        return self.search_random_in(entries, keyword=keyword, limit=limit)

    def search_random_in(
        self,
        entries: dict[int, MemeEntry],
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """在指定 entries 子集上随机取样。

        Args:
            entries: 索引条目子集，key=int(id)。
            keyword: 可选关键词；None 或空串表示直接对 entries 随机取样。
            limit: 返回数量上限，默认 10。

        Returns:
            随机取样后的 SearchResult 列表，候选不足时返回全部。
            有关键词但无匹配时返回空列表，不会回退到全库随机。
            所有结果的 similarity 字段固定为 0.0。
        """
        logger.debug(
            "随机子集搜索入口: entries=%d, keyword=%r, limit=%d",
            len(entries),
            keyword,
            limit,
        )
        if keyword:
            keyword_results = self.keyword_searcher.search_in(entries, keyword)
            candidates = [
                SearchResult.from_entry(entry, 0.0)
                for result in keyword_results
                if (entry := entries.get(result.entry_id)) is not None
            ]
        else:
            candidates = [
                SearchResult.from_entry(entry, 0.0)
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
