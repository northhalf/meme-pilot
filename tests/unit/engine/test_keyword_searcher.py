"""KeywordSearcher 单元测试。"""

import pytest

from bot.engine.keyword_searcher import KeywordSearcher, SearchResult
from bot.engine.metadata_store import MemeEntry


class MockMetadataStore:
    """模拟 MetadataStore，返回预定义的 entries 字典。"""

    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    """标准测试用的表情包索引数据（text 已无空格）。"""
    return {
        1: MemeEntry(id=1, image_path="cat.jpg", text="一只猫在跳起来抓蝴蝶哈哈哈"),
        2: MemeEntry(id=2, image_path="overtime.jpg", text="加班到凌晨三点的我"),
        3: MemeEntry(
            id=3, image_path="suspect.jpg", text="人家一片热忱你怎能以小人之心度君子之腹呢"
        ),
        4: MemeEntry(id=4, image_path="empty.jpg", text=""),
        5: MemeEntry(id=5, image_path="boss.jpg", text="当你的老板说今天要加班"),
        6: MemeEntry(id=6, image_path="sunday.jpg", text="周日晚上的加班通知"),
    }


@pytest.fixture
def searcher(sample_entries: dict[int, MemeEntry]) -> KeywordSearcher:
    return KeywordSearcher(MockMetadataStore(sample_entries))


class TestSearchResult:
    def test_create(self) -> None:
        r = SearchResult(
            entry_id=1, image_path="cat.jpg", text="一只猫", similarity=85.5
        )
        assert r.entry_id == 1
        assert r.image_path == "cat.jpg"
        assert r.text == "一只猫"
        assert r.similarity == 85.5


class TestInit:
    def test_default_threshold(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._threshold == 60.0

    def test_custom_threshold(self) -> None:
        assert KeywordSearcher(MockMetadataStore(), threshold=80.0)._threshold == 80.0

    def test_default_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._limit == 10

    def test_custom_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore(), limit=5)._limit == 5


class TestSearchExactSubstring:
    def test_short_keyword_in_long_text(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("小人之心")
        assert len(results) == 1
        assert results[0].entry_id == 3
        assert results[0].image_path == "suspect.jpg"
        assert results[0].similarity == 100.0

    def test_keyword_hits_multiple(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        assert {r.entry_id for r in results} == {2, 5, 6}

    def test_full_text_match(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班到凌晨三点的我")
        assert len(results) == 1
        assert results[0].entry_id == 2
        assert results[0].similarity == 100.0


class TestSearchFuzzy:
    def test_partial_overlap(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_non_contiguous_match(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班凌晨通知")
        assert len(results) >= 1
        assert all(60.0 <= r.similarity < 100.0 for r in results)


class TestSearchWithParticleRemoval:
    def test_drops_particles(self, sample_entries: dict[int, MemeEntry]) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        results = s.search("了加班吗")
        assert {r.entry_id for r in results} == {2, 5, 6}
        assert all(r.similarity == 100.0 for r in results)

    def test_all_particles_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search("的呢吗") == []

    def test_content_word_with_embedded_particle_char(self) -> None:
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="了解详情请咨询")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("了解")
        assert len(results) == 1
        assert results[0].similarity == 100.0


class TestSearchEdgeCases:
    def test_empty_keyword(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("") == []

    def test_whitespace_keyword(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("   ") == []

    def test_no_match(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("火星文xyz") == []

    def test_empty_entries(self) -> None:
        assert KeywordSearcher(MockMetadataStore({})).search("加班") == []

    def test_all_empty_text(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text=""),
            2: MemeEntry(id=2, image_path="b.jpg", text=""),
        }
        assert KeywordSearcher(MockMetadataStore(entries)).search("加班") == []

    def test_below_threshold_filtered(self) -> None:
        entries = {1: MemeEntry(id=1, image_path="x.jpg", text="今天天气真好")}
        s = KeywordSearcher(MockMetadataStore(entries), threshold=90.0)
        assert s.search("加班") == []


class TestSearchResultOrder:
    def test_limit_truncation(self) -> None:
        entries = {
            i: MemeEntry(id=i, image_path=f"meme_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        assert len(s.search("加班")) == 5

    def test_perfect_score_filters_others(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班"),
            2: MemeEntry(id=2, image_path="b.jpg", text="加班到凌晨"),
            3: MemeEntry(id=3, image_path="c.jpg", text="加斑"),  # LCS=1, score=50
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班")
        assert {r.entry_id for r in results} == {1, 2}
        assert all(r.similarity == 100.0 for r in results)


class TestParticleRemovalFn:
    def test_removes_structural_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("的加班") == "加班"

    def test_removes_modal_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("加班了吗") == "加班"

    def test_all_particles_returns_empty(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert "".join(_remove_particles("的呢吗").split()) == ""
