"""关键词模糊搜索模块。

对 index.json 中的 OCR 文本使用 LCS（最长公共子序列）进行匹配。
"""

import logging
from dataclasses import dataclass
from typing import Protocol

import pylcs
import jieba.posseg as pseg

logger = logging.getLogger(__name__)

# jieba 词性标注中与助词相关的标签
# uj=助词(的/地/得), ul=语气词(了), uz=时态助词(着/了/过)
# us=结构助词(所/得以), y=语气词(吗/呢/吧), e=叹词(嗯/哦)
_PARTICLE_POS_TAGS: frozenset[str] = frozenset({"uj", "ul", "uz", "us", "y", "e"})


class IndexProvider(Protocol):
    """索引数据提供者协议。

    KeywordSearcher 依赖此协议获取索引条目，
    而非直接依赖具体的 IndexManager 实现。
    """

    def get_entries(self) -> dict[str, dict[str, str]]:
        """返回 index.json 中的 entries 字典。

        Returns:
            key 为索引 id（如 "1"），value 为包含 filename、text、text_hash 的字典。
        """
        ...


@dataclass
class SearchResult:
    """单条关键词搜索结果。

    Attributes:
        entry_id: 索引 id，如 "1"。
        filename: 表情包文件名，如 "cat_jump.jpg"。
        text: OCR 文本。
        similarity: 相似度分数，0-100。
    """

    entry_id: str
    filename: str
    text: str
    similarity: float


def _remove_particles(text: str) -> str:
    """使用 jieba.posseg 过滤助词，返回纯文本。

    对 text 做分词 + 词性标注，移除助词类标签（uj/ul/uz/us/e）的词位，
    保留非助词部分的原始字符和顺序。

    Args:
        text: 待处理的文本（搜索关键词）。

    Returns:
        移除助词后的纯文本；如果全部为助词则返回空字符串。
    """
    return "".join(
        word for word, flag in pseg.cut(text) if flag not in _PARTICLE_POS_TAGS
    )


class KeywordSearcher:
    """关键词模糊搜索引擎。

    使用 LCS（最长公共子序列）对 OCR 文本进行匹配：
    - 关键词是 OCR 文本的子串时，相似度为 100（精确命中）。
    - 否则按 LCS 长度与关键词长度的比值计算相似度。

    Attributes:
        threshold: 最低相似度阈值，默认 60。
        limit: 最大返回结果数，默认 10。
    """

    def __init__(
        self,
        index_provider: IndexProvider,
        threshold: float = 60.0,
        limit: int = 10,
    ) -> None:
        """初始化关键词搜索引擎。

        Args:
            index_provider: 索引数据提供者，需实现 get_entries() 方法。
            threshold: 最低相似度阈值，默认 60。
            limit: 最大返回结果数，默认 10。
        """
        self._index_provider = index_provider
        self._threshold = threshold
        self._limit = limit

    @staticmethod
    def _compute_similarity(keyword: str, text: str) -> float:
        """计算关键词与文本的相似度。

        优先精确子串匹配（返回 100），否则使用 LCS 算法。

        Args:
            keyword: 搜索关键词。
            text: OCR 文本。

        Returns:
            相似度分数，0-100。
        """
        if keyword in text:
            return 100.0
        lcs_len = pylcs.lcs_sequence_length(keyword, text)
        return (lcs_len / len(keyword)) * 100

    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        先对 keyword 做分词 + 助词过滤，再用过滤后的文本做 LCS 匹配。

        Args:
            keyword: 用户输入的搜索关键词。

        Returns:
            按相似度降序排列的搜索结果列表，最多返回 limit 条。
            无匹配时返回空列表。
        """
        keyword = keyword.strip()
        if not keyword:
            logger.debug("关键词为空，返回空结果")
            return []

        # 去助词后搜索
        cleaned = _remove_particles(keyword)
        cleaned = "".join(cleaned.split())  # 删除所有空白字符
        if not cleaned:
            logger.debug("关键词去助词后为空，返回空结果")
            return []

        entries = self._index_provider.get_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        results: list[SearchResult] = []

        for entry_id, entry in entries.items():
            text = entry.get("text", "").strip()
            if not text:
                continue

            score = self._compute_similarity(cleaned, text)
            if score >= self._threshold:
                results.append(
                    SearchResult(
                        entry_id=entry_id,
                        filename=entry.get("filename", ""),
                        text=text,
                        similarity=score,
                    )
                )

        results.sort(key=lambda r: r.similarity, reverse=True)

        # 如果存在分数为 100 的结果，只返回分数为 100 的结果
        perfect_results = [r for r in results if r.similarity == 100.0]
        if perfect_results:
            results = perfect_results

        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            min(len(results), self._limit),
        )

        return results[: self._limit]
