"""CombinedSearcher 单元测试。"""

import pytest

from bot.engine.combined_searcher import CombinedSearcher
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry


class MockMetadataStore:
    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨", speaker="小明", tags=["吐槽", "加班"]),
        2: MemeEntry(id=2, image_path="b.jpg", text="老板又让加班", speaker="小红", tags=["加班"]),
        3: MemeEntry(id=3, image_path="c.jpg", text="周末加班通知", speaker="小明", tags=["通知", "加班"]),
        4: MemeEntry(id=4, image_path="d.jpg", text="猫在睡觉", speaker=None, tags=["萌宠"]),
        5: MemeEntry(id=5, image_path="e.jpg", text="Cat", speaker="Tom", tags=["animal"]),
    }


@pytest.fixture
def combined(sample_entries: dict[int, MemeEntry]) -> CombinedSearcher:
    md = MockMetadataStore(sample_entries)
    return CombinedSearcher(md, KeywordSearcher(md))


class TestSpeakerFilter:
    def test_single_speaker_exact(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], [])
        assert {r.entry_id for r in results} == {1, 3}
        assert all(r.speaker == "小明" for r in results)

    def test_multiple_speakers_or(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明", "小红"], [])
        assert {r.entry_id for r in results} == {1, 2, 3}

    def test_speaker_case_sensitive(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, ["tom"], []) == []  # "Tom" != "tom"

    def test_speaker_none_not_matched(self, combined: CombinedSearcher) -> None:
        """speaker=None 的条目不被 @ 命中。"""
        results = combined.search(None, ["小明"], [])
        assert 4 not in {r.entry_id for r in results}

    def test_speaker_no_match_returns_empty(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, ["不存在"], []) == []


class TestTagFilter:
    def test_single_tag(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班"])
        assert {r.entry_id for r in results} == {1, 2, 3}

    def test_multiple_tags_and(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班", "吐槽"])
        assert {r.entry_id for r in results} == {1}

    def test_tag_case_sensitive(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, [], ["Animal"]) == []  # "animal" != "Animal"

    def test_tag_not_exist_returns_empty(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, [], ["不存在的标签"]) == []

    def test_duplicate_tags_deduped(self, combined: CombinedSearcher) -> None:
        """重复 tag 不影响 AND 判定。"""
        results = combined.search(None, [], ["加班", "加班"])
        assert {r.entry_id for r in results} == {1, 2, 3}


class TestSpeakerAndTagCombined:
    def test_speaker_and_tag_intersection(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], ["通知"])
        assert {r.entry_id for r in results} == {3}


class TestWithKeyword:
    def test_keyword_on_subset(self, combined: CombinedSearcher) -> None:
        results = combined.search("凌晨", ["小明"], [])
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_keyword_subset_excludes_filtered_out(self, combined: CombinedSearcher) -> None:
        """关键词只在过滤子集上匹配，不召回被过滤掉的条目。"""
        results = combined.search("加班", ["小明"], [])
        assert {r.entry_id for r in results} == {1, 3}  # entry 2(小红) 被过滤

    def test_keyword_no_match_in_subset_returns_empty(
        self, combined: CombinedSearcher
    ) -> None:
        assert combined.search("火星文", ["小明"], []) == []

    def test_keyword_empty_string_treated_as_no_keyword(
        self, combined: CombinedSearcher
    ) -> None:
        """keyword='' 视为无关键词，走纯过滤分支，similarity=0.0。"""
        results = combined.search("", ["小明"], [])
        assert {r.entry_id for r in results} == {1, 3}
        assert all(r.similarity == 0.0 for r in results)


class TestNoKeywordBranch:
    def test_sorted_by_entry_id_ascending(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明", "小红"], [])
        assert [r.entry_id for r in results] == [1, 2, 3]

    def test_similarity_zero(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班"])
        assert all(r.similarity == 0.0 for r in results)

    def test_carries_metadata(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], [])
        r1 = next(r for r in results if r.entry_id == 1)
        assert r1.speaker == "小明"
        assert r1.tags == ["吐槽", "加班"]


class TestEmptyEntries:
    def test_empty_store_returns_empty(self) -> None:
        md = MockMetadataStore({})
        combined = CombinedSearcher(md, KeywordSearcher(md))
        assert combined.search("加班", ["小明"], ["加班"]) == []

    def test_filter_to_empty_returns_empty(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, ["小明"], ["萌宠"]) == []  # 小明无萌宠 tag


class TestPackageExport:
    def test_combined_searcher_exported_from_engine(self) -> None:
        """CombinedSearcher 应可从 bot.engine 顶层导入。"""
        from bot.engine import CombinedSearcher as Exported

        assert Exported is CombinedSearcher

    def test_app_state_has_get_combined_searcher(self) -> None:
        """app_state 应提供 get_combined_searcher。"""
        from bot.app_state import get_combined_searcher

        assert callable(get_combined_searcher)
