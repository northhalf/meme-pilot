"""关键词模糊搜索模块。

对 MetadataStore 中的 OCR 文本（已去除所有空白）使用 LCS（最长公共子序列）进行匹配。
"""

import logging

import jieba
import jieba.posseg as pseg
import pylcs

from bot.log_context import timed

from .metadata_store import MemeEntry
from .protocols import MetadataStoreProvider
from .types import SearchResult

logger = logging.getLogger(__name__)

# jieba 词性标注中与助词相关的标签
# uj=助词(的/地/得), ul=语气词(了), uz=时态助词(着/了/过)
# us=结构助词(所/得以), y=语气词(吗/呢/吧), e=叹词(嗯/哦)
_PARTICLE_POS_TAGS: frozenset[str] = frozenset({"uj", "ul", "uz", "us", "y", "e"})


def _remove_particles(text: str) -> str:
    """使用 jieba.posseg 过滤助词，返回纯文本。

    Args:
        text: 待处理的文本（搜索关键词）。

    Returns:
        移除助词后的纯文本；如果全部为助词则返回空字符串。
    """
    return "".join(
        word for word, flag in pseg.cut(text) if flag not in _PARTICLE_POS_TAGS
    )


def _strip_all_whitespace(text: str) -> str:
    """去除字符串中所有空白字符，保留其余字符（含助词）。

    Args:
        text: 待处理的文本。

    Returns:
        去除所有空白字符后的字符串。
    """
    return "".join(text.split())


class KeywordSearcher:
    """关键词模糊搜索引擎。

    使用 LCS 对 OCR 文本进行匹配：
    - 关键词是 OCR 文本的子串时，相似度为 100（精确命中）。
    - 否则按 LCS 长度与关键词长度的比值计算相似度。

    Attributes:
        threshold: 最低相似度阈值，默认 60。
        limit: 最大返回结果数；None 表示返回全部匹配，默认 None。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        threshold: float = 60.0,
        limit: int | None = None,
    ) -> None:
        """初始化关键词搜索引擎。

        Args:
            metadata_store: 元数据存储，需实现 get_all_entries() 方法。
            threshold: 最低相似度阈值，默认 60。
            limit: 最大返回结果数；None 表示返回全部匹配，默认 None。
        """
        self._metadata_store = metadata_store
        self._threshold = threshold
        self._limit = limit

    @timed(logger, "关键词搜索预热")
    def warm_up(self) -> None:
        """预加载 jieba 默认词典。

        在启动阶段加载 jieba 默认词典，避免首次模糊搜索产生初始化耗时。

        Raises:
            Exception: jieba 默认词典初始化失败时原样传播。
        """
        jieba.initialize()
        logger.info("关键词搜索预热完成")

    @staticmethod
    def _compute_similarity(keyword: str, text: str) -> float:
        """计算关键词与文本的相似度。

        使用 LCS 算法。

        Args:
            keyword: 搜索关键词（已去助词去空格）。
            text: OCR 文本（无空格）。

        Returns:
            相似度分数，0-100。
        """
        lcs_len = pylcs.lcs_sequence_length(keyword, text)
        return (lcs_len / len(keyword)) * 100

    def _search_exact_substring(
        self,
        entries: dict[int, MemeEntry],
        raw: str,
    ) -> list[SearchResult]:
        """第一层：精确子串匹配。

        用「原始输入去所有空白、保留助词」的关键词对 OCR 文本做子串判定，
        命中条目 similarity=100.0。

        Args:
            entries: 全部索引条目，key=int(id)。
            raw: 去所有空白后的关键词（保留助词）。

        Returns:
            命中结果列表（按 entries 读出顺序，未截断）；无命中返回空列表。
        """
        return [
            SearchResult.from_entry(entry, 100.0)
            for entry in entries.values()
            if entry.text and raw in entry.text
        ]

    def _search_fuzzy_lcs(
        self,
        entries: dict[int, MemeEntry],
        cleaned: str,
    ) -> list[SearchResult]:
        """第二层：LCS 模糊回退。

        用「去助词+去空白」的关键词走现有 LCS 模糊匹配，阈值过滤 + 降序排序
        +「存在 100 分只保留 100 分」规则。

        Args:
            entries: 全部索引条目，key=int(id)。
            cleaned: 去助词并去空白后的关键词。

        Returns:
            按相似度降序排列的结果列表（未截断）；无匹配返回空列表。
        """
        results: list[SearchResult] = []
        for entry in entries.values():
            text = entry.text
            if not text:
                continue
            score = self._compute_similarity(cleaned, text)
            if score >= self._threshold:
                results.append(SearchResult.from_entry(entry, score))

        results.sort(key=lambda r: r.similarity, reverse=True)
        perfect_results = [r for r in results if r.similarity == 100.0]
        if perfect_results:
            results = perfect_results
        return results

    @timed(logger, "关键词搜索")
    def search_in(
        self,
        entries: dict[int, MemeEntry],
        keyword: str,
    ) -> list[SearchResult]:
        """在给定 entries 子集上执行关键词搜索（两层匹配，逻辑同 search）。

        Args:
            entries: 索引条目子集，key=int(id)。
            keyword: 用户输入的搜索关键词。

        Note:
            调用方必须已持有读锁，保证读取期间子集快照不被并发写入修改。

        Returns:
            按相似度降序排列的搜索结果列表；limit=None 时返回全部匹配，否则最多返回 limit 条。
            无匹配时返回空列表。
        """
        keyword = keyword.strip()
        if not keyword:
            logger.debug("关键词为空，返回空结果")
            return []

        raw = _strip_all_whitespace(keyword)
        if not raw:
            logger.debug("关键词去空白后为空，返回空结果")
            return []

        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        exact_results = self._search_exact_substring(entries, raw)
        if exact_results:
            logger.info(
                "关键词精确子串命中：keyword=%r, 命中=%d, 返回=%d",
                keyword,
                len(exact_results),
                (
                    len(exact_results)
                    if self._limit is None
                    else min(len(exact_results), self._limit)
                ),
            )
            return exact_results[: self._limit]

        cleaned = _strip_all_whitespace(_remove_particles(keyword))
        if not cleaned:
            logger.debug("关键词去助词后为空，返回空结果")
            return []

        results = self._search_fuzzy_lcs(entries, cleaned)
        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            len(results) if self._limit is None else min(len(results), self._limit),
        )
        return results[: self._limit]

    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        在全部条目上执行两层匹配（逻辑同 search_in）：
        1. 精确子串层：用「原始输入去所有空白、保留助词」的关键词做子串匹配；
           命中则只返回包含该子串的条目（similarity=100.0）。
        2. LCS 模糊回退层：仅当第一层未命中时启用，用「去助词+去空白」的关键词
           走现有 LCS 模糊匹配（阈值 60，全量匹配）。

        Args:
            keyword: 用户输入的搜索关键词。

        Note:
            调用方必须已持有读锁，保证读取期间 MetadataStore 快照不被并发写入修改。
            IndexManager.search() 负责持锁。该方法委托 search_in，先获取全量条目快照再调用。

        Returns:
            按相似度降序排列的搜索结果列表；limit=None 时返回全部匹配，否则最多返回 limit 条。
            无匹配时返回空列表。
        """
        entries = self._metadata_store.get_all_entries()
        return self.search_in(entries, keyword)
