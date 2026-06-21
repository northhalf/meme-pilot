"""关键词模糊搜索模块。

对 index.json 中的 OCR 文本使用 partial_ratio 进行子串模糊匹配。
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


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


class KeywordSearcher:
    """关键词模糊搜索引擎。

    使用 partial_ratio 对 OCR 文本进行子串模糊匹配：
    - 关键词是 OCR 文本的连续子串时，相似度为 100（精确命中）。
    - 关键词与 OCR 文本部分匹配时，按最长公共子串比例计算相似度。

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

    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        对每条 OCR 文本计算 partial_ratio 相似度，
        过滤低于阈值的结果后按分数降序排列。

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

        entries = self._index_provider.get_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        results: list[SearchResult] = []

        for entry_id, entry in entries.items():
            text = entry.get("text", "").strip()
            if not text:
                continue

            score = fuzz.partial_ratio(keyword, text)
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

        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            min(len(results), self._limit),
        )

        return results[: self._limit]
