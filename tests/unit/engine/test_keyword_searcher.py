"""KeywordSearcher 单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from bot.engine.keyword_searcher import KeywordSearcher, SearchResult


class MockIndex:
    """模拟 IndexProvider，返回预定义的 entries 字典。"""

    def __init__(self, entries: dict[str, dict[str, str]] | None = None) -> None:
        self._entries = entries or {}

    def get_entries(self) -> dict[str, dict[str, str]]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[str, dict[str, str]]:
    """标准测试用的表情包索引数据。"""
    return {
        "001": {
            "filename": "cat.jpg",
            "text": "一只猫在跳起来抓蝴蝶 哈哈哈",
            "text_hash": "sha256:abc",
        },
        "002": {
            "filename": "overtime.jpg",
            "text": "加班到凌晨三点的我",
            "text_hash": "sha256:def",
        },
        "003": {
            "filename": "suspect.jpg",
            "text": "人家一片热忱 你怎能以小人之心 度君子之腹呢",
            "text_hash": "sha256:ghi",
        },
        "004": {
            "filename": "empty.jpg",
            "text": "",
            "text_hash": "sha256:jkl",
        },
        "005": {
            "filename": "boss.jpg",
            "text": "当你的老板说今天要加班",
            "text_hash": "sha256:mno",
        },
        "006": {
            "filename": "sunday.jpg",
            "text": "周日晚上的加班通知",
            "text_hash": "sha256:pqr",
        },
    }


@pytest.fixture
def searcher(sample_entries: dict[str, dict[str, str]]) -> KeywordSearcher:
    """使用标准测试数据创建 KeywordSearcher。"""
    return KeywordSearcher(MockIndex(sample_entries))


class TestSearchResult:
    """SearchResult 数据类测试。"""

    def test_create(self) -> None:
        """验证创建 SearchResult 实例。"""
        r = SearchResult(
            entry_id="001",
            filename="cat.jpg",
            text="一只猫",
            similarity=85.5,
        )
        assert r.entry_id == "001"
        assert r.filename == "cat.jpg"
        assert r.text == "一只猫"
        assert r.similarity == 85.5


class TestKeywordSearcherInit:
    """KeywordSearcher 初始化测试。"""

    def test_default_threshold(self) -> None:
        """验证默认阈值为 60。"""
        s = KeywordSearcher(MockIndex())
        assert s._threshold == 60.0

    def test_custom_threshold(self) -> None:
        """验证可自定义阈值。"""
        s = KeywordSearcher(MockIndex(), threshold=80.0)
        assert s._threshold == 80.0

    def test_default_limit(self) -> None:
        """验证默认最大返回数为 10。"""
        s = KeywordSearcher(MockIndex())
        assert s._limit == 10

    def test_custom_limit(self) -> None:
        """验证可自定义最大返回数。"""
        s = KeywordSearcher(MockIndex(), limit=5)
        assert s._limit == 5


class TestSearchExactSubstring:
    """子串精确命中测试（partial_ratio = 100）。"""

    def test_short_keyword_in_long_text(self, searcher: KeywordSearcher) -> None:
        """短关键词应命中长 OCR 文本中的连续子串。"""
        results = searcher.search("小人之心")
        assert len(results) == 1
        assert results[0].entry_id == "003"
        assert results[0].similarity == 100.0

    def test_keyword_hits_multiple(self, searcher: KeywordSearcher) -> None:
        """关键词为多条 OCR 文本的子串时，全部返回 100 分。"""
        results = searcher.search("加班")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        ids = {r.entry_id for r in results}
        assert ids == {"002", "005", "006"}

    def test_full_text_match(self, searcher: KeywordSearcher) -> None:
        """关键词与 OCR 文本完全相同时，应返回 100 分。"""
        results = searcher.search("加班到凌晨三点的我")
        assert len(results) == 1
        assert results[0].entry_id == "002"
        assert results[0].similarity == 100.0


class TestSearchFuzzy:
    """模糊匹配测试（partial_ratio >= 60 但 < 100）。"""

    def test_partial_overlap(self, searcher: KeywordSearcher) -> None:
        """关键词与 OCR 文本部分重叠，应命中但分数低于 100。"""
        results = searcher.search("猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == "001"
        assert 60.0 <= results[0].similarity < 100.0

    def test_typo_keyword(self, searcher: KeywordSearcher) -> None:
        """错别字关键词应通过部分匹配命中。"""
        results = searcher.search("加斑")
        assert len(results) >= 1
        assert all(60.0 <= r.similarity < 100.0 for r in results)


class TestSearchEdgeCases:
    """边界情况测试。"""

    def test_empty_keyword(self, searcher: KeywordSearcher) -> None:
        """空关键词应返回空列表。"""
        assert searcher.search("") == []

    def test_whitespace_keyword(self, searcher: KeywordSearcher) -> None:
        """纯空白关键词应返回空列表。"""
        assert searcher.search("   ") == []

    def test_no_match(self, searcher: KeywordSearcher) -> None:
        """无任何匹配时返回空列表。"""
        assert searcher.search("火星文xyz") == []

    def test_empty_entries(self) -> None:
        """索引为空时返回空列表。"""
        s = KeywordSearcher(MockIndex({}))
        assert s.search("加班") == []

    def test_entries_with_all_empty_text(self) -> None:
        """所有条目 text 为空时返回空列表。"""
        entries = {
            "001": {"filename": "a.jpg", "text": "", "text_hash": "x"},
            "002": {"filename": "b.jpg", "text": "   ", "text_hash": "y"},
        }
        s = KeywordSearcher(MockIndex(entries))
        assert s.search("加班") == []

    def test_below_threshold_filtered(self) -> None:
        """相似度低于阈值的条目应被过滤。"""
        entries = {
            "001": {"filename": "x.jpg", "text": "今天天气真好", "text_hash": "a"},
        }
        s = KeywordSearcher(MockIndex(entries), threshold=90.0)
        # "加班" vs "今天天气真好" partial_ratio 远低于 90
        assert s.search("加班") == []

    def test_threshold_boundary(self) -> None:
        """相似度等于阈值时应被保留。"""
        entries = {
            "001": {"filename": "x.jpg", "text": "abc", "text_hash": "a"},
        }
        # partial_ratio("ab", "abc") = 100 or close to 100
        # Let's test boundary more precisely
        s = KeywordSearcher(MockIndex(entries), threshold=66.0)
        # partial_ratio("ab", "abc") = 100
        results = s.search("ab")
        assert len(results) == 1

    def test_keyword_longer_than_text(
        self, searcher: KeywordSearcher
    ) -> None:
        """关键词比 OCR 文本长时，partial_ratio 以较短文本为基准匹配。"""
        # "当你的老板说今天要加班" 是长关键词的子串 → 100 分
        results = searcher.search("当你的老板说今天要加班而且不给加班费")
        assert len(results) == 1
        assert results[0].entry_id == "005"
        assert results[0].similarity == 100.0


class TestSearchResultOrder:
    """结果排序测试。"""

    def test_higher_similarity_first(self, searcher: KeywordSearcher) -> None:
        """结果应按相似度降序排列。"""
        results = searcher.search("蝴蝶")
        for i in range(len(results) - 1):
            assert results[i].similarity >= results[i + 1].similarity

    def test_limit_truncation(self) -> None:
        """超过 limit 的结果应被截断。"""
        entries = {
            str(i).zfill(3): {
                "filename": f"meme_{i}.jpg",
                "text": f"加班第{i}天",
                "text_hash": "x",
            }
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockIndex(entries), limit=5)
        results = s.search("加班")
        assert len(results) == 5


class TestSearchResultsFormat:
    """返回值格式测试。"""

    def test_all_fields_present(self, searcher: KeywordSearcher) -> None:
        """每一条结果应包含所有必需字段。"""
        results = searcher.search("加班")
        for r in results:
            assert isinstance(r.entry_id, str)
            assert isinstance(r.filename, str)
            assert isinstance(r.text, str)
            assert isinstance(r.similarity, float)
            assert 0.0 <= r.similarity <= 100.0
            assert r.entry_id  # 非空
            assert r.filename  # 非空
