"""组合搜索器 - 按关键词/说话人/标签组合检索。

先按 speakers(OR) + tags(AND) 过滤 entries 子集，再在子集上执行关键词搜索：
- 有关键词：委托 KeywordSearcher.search_in，返回带 similarity 的结果（降序）。
- 无关键词：包装 similarity=0.0，按 entry_id 升序返回。
"""

import logging
import random
from itertools import groupby

from bot.log_context import timed

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

    @timed(logger, "组合搜索")
    def search(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str] | None = None,
    ) -> list[SearchResult]:
        """按关键词/说话人/标签组合检索（全库兼容包装）。

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
        logger.debug(
            "组合检索入口: keyword=%r, speakers=%r, tags=%r",
            keyword,
            speakers,
            tags,
        )
        entries = self._metadata_store.get_all_entries()
        return self.search_in(entries, keyword, speakers, tags=tags)

    def search_in(
        self,
        entries: dict[int, MemeEntry],
        keyword: str | None,
        speakers: list[str],
        tags: list[str] | None = None,
    ) -> list[SearchResult]:
        """在指定 entries 子集上按关键词/说话人/标签组合检索。

        Args:
            entries: 索引条目子集，key=int(id)。
            keyword: 关键词；None 或空串表示纯过滤（不跑关键词匹配）。
            speakers: 说话人列表（OR，精确相等）；空列表表示不过滤。
            tags: 标签列表（AND，区分大小写）；None 或空列表表示不过滤。

        Note:
            调用方必须已持有读锁。

        Returns:
            有关键词时按相似度降序；无关键词时顺序随机。无匹配返回空列表。
        """
        tags = tags or []
        logger.debug(
            "组合子集检索入口: entries=%d, keyword=%r, speakers=%r, tags=%r",
            len(entries),
            keyword,
            speakers,
            tags,
        )
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        filtered = self._filter_entries(entries, speakers, tags)
        if not filtered:
            logger.info("组合检索过滤后子集为空: speakers=%r, tags=%r", speakers, tags)
            return []

        if keyword:
            results = self._keyword_searcher.search_in(filtered, keyword)
            results = _shuffle_within_similarity_groups(results)
            logger.info(
                "组合检索（含关键词）: keyword=%r, speakers=%r, tags=%r, 命中=%d",
                keyword,
                speakers,
                tags,
                len(results),
            )
            return results

        results = [SearchResult.from_entry(entry, 0.0) for entry in filtered.values()]
        random.shuffle(results)
        logger.info(
            "组合检索（纯过滤）: speakers=%r, tags=%r, 命中=%d",
            speakers,
            tags,
            len(results),
        )
        return results


def _shuffle_within_similarity_groups(
    results: list[SearchResult],
) -> list[SearchResult]:
    """对相似度相同的结果组内随机排序，组间保持相似度降序。

    依赖入参已按 similarity 降序排列（search_in 契约保证，同分相邻），
    因此 itertools.groupby 的「连续相同 key 合并」即可正确分组。

    Args:
        results: 已按 similarity 降序排列的搜索结果。

    Returns:
        组间顺序不变、组内随机打乱的新列表。
    """
    shuffled: list[SearchResult] = []
    for _sim, group in groupby(results, key=lambda r: r.similarity):
        group_list = list(group)
        random.shuffle(group_list)
        shuffled.extend(group_list)
    return shuffled
