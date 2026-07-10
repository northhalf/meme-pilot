"""组合搜索器 - 按关键词/说话人/标签组合检索。

先按 speakers(OR) + tags(AND) 过滤 entries 子集，再在子集上执行关键词搜索：
- 有关键词：委托 KeywordSearcher.search_in，返回带 similarity 的结果（降序）。
- 无关键词：包装 similarity=0.0，按 entry_id 升序返回。
"""

import logging

from .keyword_searcher import KeywordSearcher
from .metadata_store import MemeEntry
from .protocols import MetadataStoreProvider
from .types import SearchResult

logger = logging.getLogger(__name__)


class CombinedSearcher:
    """组合搜索引擎。

    Attributes:
        _metadata_store: 元数据存储提供者，需实现 get_all_entries()。
        _keyword_searcher: 关键词搜索器，用于子集上的关键词匹配。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        keyword_searcher: KeywordSearcher,
    ) -> None:
        """初始化组合搜索引擎。

        Args:
            metadata_store: 元数据存储提供者。
            keyword_searcher: 关键词搜索器。
        """
        self._metadata_store = metadata_store
        self._keyword_searcher = keyword_searcher

    @staticmethod
    def _filter_entries(
        entries: dict[int, MemeEntry],
        speakers: list[str],
        tags: list[str],
    ) -> dict[int, MemeEntry]:
        """按 speakers(OR) + tags(AND) 过滤 entries 子集。

        Args:
            entries: 全部索引条目。
            speakers: 说话人列表（OR，精确相等）；空列表表示不过滤。
            tags: 标签列表（AND，区分大小写）；空列表表示不过滤。

        Returns:
            过滤后的 entries 子集。
        """
        speaker_set = set(speakers) if speakers else None
        tag_set = set(tags) if tags else None
        result: dict[int, MemeEntry] = {}
        for eid, entry in entries.items():
            if speaker_set is not None and entry.speaker not in speaker_set:
                continue
            if tag_set is not None and not tag_set.issubset(set(entry.tags)):
                continue
            result[eid] = entry
        return result

    def search(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str] | None = None,
    ) -> list[SearchResult]:
        """按关键词/说话人/标签组合检索。

        Args:
            keyword: 关键词；None 或空串表示纯过滤（不跑关键词匹配）。
            speakers: 说话人列表（OR，精确相等）；空列表表示不过滤。
            tags: 标签列表（AND，区分大小写）；None 或空列表表示不过滤。

        Note:
            调用方必须已持有读锁。IndexManager.search_combined() 负责持锁。

        Returns:
            有关键词时按相似度降序；无关键词时按 entry_id 升序。无匹配返回空列表。
        """
        tags = tags or []
        entries = self._metadata_store.get_all_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        filtered = self._filter_entries(entries, speakers, tags)
        if not filtered:
            logger.info("组合检索过滤后子集为空: speakers=%r, tags=%r", speakers, tags)
            return []

        if keyword:
            results = self._keyword_searcher.search_in(filtered, keyword)
            logger.info(
                "组合检索（含关键词）: keyword=%r, speakers=%r, tags=%r, 命中=%d",
                keyword,
                speakers,
                tags,
                len(results),
            )
            return results

        results = [
            SearchResult(
                entry_id=entry.id,
                image_path=entry.image_path,
                text=entry.text,
                similarity=0.0,
                speaker=entry.speaker,
                tags=entry.tags,
            )
            for entry in filtered.values()
        ]
        results.sort(key=lambda r: r.entry_id)
        logger.info(
            "组合检索（纯过滤）: speakers=%r, tags=%r, 命中=%d",
            speakers,
            tags,
            len(results),
        )
        return results
