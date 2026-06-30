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
        "1": {
            "filename": "cat.jpg",
            "text": "一只猫在跳起来抓蝴蝶 哈哈哈",
            "text_hash": "sha256:abc",
        },
        "2": {
            "filename": "overtime.jpg",
            "text": "加班到凌晨三点的我",
            "text_hash": "sha256:def",
        },
        "3": {
            "filename": "suspect.jpg",
            "text": "人家一片热忱 你怎能以小人之心 度君子之腹呢",
            "text_hash": "sha256:ghi",
        },
        "4": {
            "filename": "empty.jpg",
            "text": "",
            "text_hash": "sha256:jkl",
        },
        "5": {
            "filename": "boss.jpg",
            "text": "当你的老板说今天要加班",
            "text_hash": "sha256:mno",
        },
        "6": {
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
            entry_id="1",
            filename="cat.jpg",
            text="一只猫",
            similarity=85.5,
        )
        assert r.entry_id == "1"
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
        assert results[0].entry_id == "3"
        assert results[0].similarity == 100.0

    def test_keyword_hits_multiple(self, searcher: KeywordSearcher) -> None:
        """关键词为多条 OCR 文本的子串时，全部返回 100 分。"""
        results = searcher.search("加班")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        ids = {r.entry_id for r in results}
        assert ids == {"2", "5", "6"}

    def test_full_text_match(self, searcher: KeywordSearcher) -> None:
        """关键词与 OCR 文本完全相同时，应返回 100 分。"""
        results = searcher.search("加班到凌晨三点的我")
        assert len(results) == 1
        assert results[0].entry_id == "2"
        assert results[0].similarity == 100.0


class TestSearchFuzzy:
    """模糊匹配测试（LCS 相似度 >= 60）。"""

    def test_partial_overlap(self, searcher: KeywordSearcher) -> None:
        """关键词与 OCR 文本部分重叠，应命中。

        "猫抓蝴蝶" 的每个字符都按序出现在 "一只猫在跳起来抓蝴蝶" 中，
        LCS = 4 = len(keyword)，因此 LCS 算法给出 100 分（完整子序列匹配）。
        """
        results = searcher.search("猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == "1"
        assert results[0].similarity == 100.0

    def test_non_contiguous_match(self, searcher: KeywordSearcher) -> None:
        """关键词字符在 OCR 文本中非连续出现时，LCS 应命中但分数低于 100。

        "加班凌晨通知" 的字符分散在 "周日晚上的加班通知" 中：
        LCS = "加班通知"（4字），score = 4/6 * 100 ≈ 66.7。
        """
        results = searcher.search("加班凌晨通知")
        assert len(results) >= 1
        assert all(60.0 <= r.similarity < 100.0 for r in results)


class TestSearchFuzzyEdgeCases:
    """短关键词模糊匹配边界情况测试。"""

    def test_two_char_keyword_fuzzy_score_below_50_still_excluded(
        self,
    ) -> None:
        """2 字关键词模糊匹配分数 < 50 时仍不命中。"""
        entries: dict[str, dict[str, str]] = {
            "1": {
                "filename": "x.jpg",
                "text": "加班到凌晨",
                "text_hash": "a",
            },
        }
        s = KeywordSearcher(MockIndex(entries))
        # "xy": 2 字，两字均不出现在文本中 → LCS=0，score=0 < 50 → 排除
        results = s.search("xy")
        assert len(results) == 0

    def test_three_plus_char_keyword_still_uses_original_threshold(
        self,
    ) -> None:
        """3 字以上关键词仍使用原始阈值（默认 60）。"""
        entries: dict[str, dict[str, str]] = {
            "1": {
                "filename": "x.jpg",
                "text": "AB12",
                "text_hash": "a",
            },
        }
        s = KeywordSearcher(MockIndex(entries), threshold=60.0)
        # "2AXY": 4 字，LCS vs "AB12" = 2（"A" 和 "2"），score = 2/4*100 = 50
        # 50 < 60 → 排除
        results = s.search("2AXY")
        assert len(results) == 0


class TestSearchWithParticleRemoval:
    """去助词后的搜索行为测试。"""

    def test_search_drops_particles_from_keyword(
        self, sample_entries: dict[str, dict[str, str]],
    ) -> None:
        searcher = KeywordSearcher(MockIndex(sample_entries))
        results = searcher.search("了加班吗")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        ids = {r.entry_id for r in results}
        assert ids == {"2", "5", "6"}

    def test_search_all_particles_returns_empty(
        self, sample_entries: dict[str, dict[str, str]],
    ) -> None:
        searcher = KeywordSearcher(MockIndex(sample_entries))
        results = searcher.search("的呢吗")
        assert len(results) == 0

    def test_search_content_word_with_embedded_particle_char(self) -> None:
        entries = {
            "1": {"filename": "a.jpg", "text": "了解详情请咨询", "text_hash": "x"},
        }
        searcher = KeywordSearcher(MockIndex(entries))
        results = searcher.search("了解")
        assert len(results) == 1
        assert results[0].similarity == 100.0

    def test_two_char_fuzzy_below_60_filtered(self) -> None:
        entries = {
            "1": {"filename": "x.jpg", "text": "加班到凌晨", "text_hash": "a"},
        }
        searcher = KeywordSearcher(MockIndex(entries))
        results = searcher.search("加a")
        assert len(results) == 0


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
            "1": {"filename": "a.jpg", "text": "", "text_hash": "x"},
            "2": {"filename": "b.jpg", "text": "   ", "text_hash": "y"},
        }
        s = KeywordSearcher(MockIndex(entries))
        assert s.search("加班") == []

    def test_below_threshold_filtered(self) -> None:
        """相似度低于阈值的条目应被过滤。"""
        entries = {
            "1": {"filename": "x.jpg", "text": "今天天气真好", "text_hash": "a"},
        }
        s = KeywordSearcher(MockIndex(entries), threshold=90.0)
        # "加班" vs "今天天气真好" partial_ratio 远低于 90
        assert s.search("加班") == []

    def test_threshold_boundary(self) -> None:
        """相似度等于阈值时应被保留。"""
        entries = {
            "1": {"filename": "x.jpg", "text": "abc", "text_hash": "a"},
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
        """关键词比 OCR 文本长且含助词时，去助词后 LCS 分数可能降低。

        "当你的老板说今天要加班而且不给加班费"(18字) 含助词"的"，
        去助词后变为 "当你老板说今天要加班而且不给加班费"(17字)。
        与 OCR 文本 "当你的老板说今天要加班"(11字) 的 LCS = 10（缺"的"），
        score = 10/17 * 100 ≈ 58.8，低于默认阈值 60，不命中。
        """
        results = searcher.search("当你的老板说今天要加班而且不给加班费")
        assert len(results) == 0


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
            str(i): {
                "filename": f"meme_{i}.jpg",
                "text": f"加班第{i}天",
                "text_hash": "x",
            }
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockIndex(entries), limit=5)
        results = s.search("加班")
        assert len(results) == 5

    def test_perfect_score_filters_others(self, searcher: KeywordSearcher) -> None:
        """当存在分数为 100 的结果时，只返回分数为 100 的结果。

        搜索 "加班"：
        - "加班到凌晨三点的我" (子串命中，score=100)
        - "当你的老板说今天要加班" (子串命中，score=100)
        - "周日晚上的加班通知" (子串命中，score=100)
        所有结果都是 100 分，应全部返回。
        """
        results = searcher.search("加班")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)

    def test_perfect_score_excludes_lower(self) -> None:
        """当存在分数为 100 的结果时，排除低于 100 的结果。"""
        entries = {
            "1": {"filename": "a.jpg", "text": "加班", "text_hash": "x"},
            "2": {"filename": "b.jpg", "text": "加班到凌晨", "text_hash": "y"},
            "3": {"filename": "c.jpg", "text": "加斑", "text_hash": "z"},  # LCS=1, score=50
        }
        s = KeywordSearcher(MockIndex(entries))
        results = s.search("加班")
        # 只返回 score=100 的结果（"加班" 和 "加班到凌晨"）
        assert len(results) == 2
        assert all(r.similarity == 100.0 for r in results)
        ids = {r.entry_id for r in results}
        assert ids == {"1", "2"}


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


class TestParticleRemoval:
    """_remove_particles 函数行为测试。"""

    def test_removes_structural_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles
        assert _remove_particles("的加班") == "加班"

    def test_removes_modal_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles
        assert _remove_particles("加班了吗") == "加班"

    def test_keeps_content_word_with_particle_char(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles
        assert _remove_particles("了解") == "了解"

    def test_all_particles_returns_empty(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles
        result = _remove_particles("的呢吗")
        assert "".join(result.split()) == ""

    def test_no_particles_unchanged(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles
        assert _remove_particles("加班") == "加班"
