"""CombinedSearcher 单元测试。"""

from typing import cast
from unittest.mock import MagicMock

import pytest

from bot.engine.combined_searcher import CombinedSearcher
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.types import MemePublicId, SearchResult


def MockMetadataStore(entries: dict[int, MemeEntry] | None = None) -> MetadataStore:
    """构造模拟 MetadataStore，get_all_entries 返回预定义的 entries 字典。"""
    mock = MagicMock()
    mock.get_all_entries.return_value = entries or {}
    return cast(MetadataStore, mock)


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(
            id=1,
            image_path="a.jpg",
            text="加班到凌晨",
            speaker="小明",
            tags=["吐槽", "加班"],
        ),
        2: MemeEntry(
            id=2, image_path="b.jpg", text="老板又让加班", speaker="小红", tags=["加班"]
        ),
        3: MemeEntry(
            id=3,
            image_path="c.jpg",
            text="周末加班通知",
            speaker="小明",
            tags=["通知", "加班"],
        ),
        4: MemeEntry(
            id=4, image_path="d.jpg", text="猫在睡觉", speaker=None, tags=["萌宠"]
        ),
        5: MemeEntry(
            id=5, image_path="e.jpg", text="Cat", speaker="Tom", tags=["animal"]
        ),
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

    def test_keyword_subset_excludes_filtered_out(
        self, combined: CombinedSearcher
    ) -> None:
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

    def test_keyword_shuffles_within_same_similarity(
        self, combined: CombinedSearcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有关键词：同相似度组内随机（精确子串全 100.0 单组）。"""
        monkeypatch.setattr(
            "bot.engine.combined_searcher.random.shuffle",
            lambda seq: seq.reverse(),
        )
        results = combined.search("加班", ["小明"], [])
        # speaker 小明 -> {1,3}；精确子串「加班」命中二者，全 100.0 单组，反转后 [3,1]
        assert [r.entry_id for r in results] == [3, 1]
        assert all(r.similarity == 100.0 for r in results)


class TestNoKeywordBranch:
    def test_no_keyword_returns_all_entries(self, combined: CombinedSearcher) -> None:
        """无关键词：返回全部过滤命中条目（顺序随机，仅校验集合与数量）。"""
        results = combined.search(None, ["小明", "小红"], [])
        assert {r.entry_id for r in results} == {1, 2, 3}
        assert len(results) == 3

    def test_no_keyword_shuffles_via_random(
        self, combined: CombinedSearcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无关键词：确实调用 random.shuffle 打乱（monkeypatch 反转验证）。"""
        monkeypatch.setattr(
            "bot.engine.combined_searcher.random.shuffle",
            lambda seq: seq.reverse(),
        )
        results = combined.search(None, ["小明", "小红"], [])
        # filtered.values() 迭代序为 [1,2,3]，反转后 [3,2,1]
        assert [r.entry_id for r in results] == [3, 2, 1]
        assert all(r.similarity == 0.0 for r in results)

    def test_similarity_zero(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班"])
        assert all(r.similarity == 0.0 for r in results)

    def test_carries_metadata(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], [])
        r1 = next(r for r in results if r.entry_id == 1)
        assert r1.speaker == "小明"
        assert r1.tags == ("吐槽", "加班")


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


class TestSearchIn:
    """search_in：在显式传入的 entries 子集上组合检索。"""

    def test_search_in_only_uses_supplied_entries(self) -> None:
        entries = {
            2: MemeEntry(
                id=2,
                image_path="新三国/a.webp",
                text="文本",
                tags=["三国"],
                collection_id=1,
                local_id=1,
                collection_name="新三国",
            )
        }
        metadata_store = MockMetadataStore({})
        combined = CombinedSearcher(metadata_store, KeywordSearcher(metadata_store))

        results = combined.search_in(entries, None, [], ["三国"])

        assert [result.public_id for result in results] == [MemePublicId(1, 1)]

    def test_search_in_with_keyword_filters_subset(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨"),
            2: MemeEntry(
                id=2,
                image_path="新三国/a.webp",
                text="丞相发笑",
                collection_id=1,
                local_id=1,
                collection_name="新三国",
            ),
        }
        metadata_store = MockMetadataStore(entries)
        combined = CombinedSearcher(metadata_store, KeywordSearcher(metadata_store))

        results = combined.search_in(entries, "丞相", [], [])

        assert [result.entry_id for result in results] == [2]
        assert results[0].public_id == MemePublicId(1, 1)

    def test_search_delegates_to_search_in(self, combined: CombinedSearcher) -> None:
        """search() 作为全库兼容包装委托给 search_in()。"""
        results = combined.search("凌晨", ["小明"], [])
        assert len(results) == 1
        assert results[0].entry_id == 1


class TestCollectionIdentity:
    """搜索结果应携带合集身份字段。"""

    def test_collection_identity_preserved(self) -> None:
        entries = {
            1: MemeEntry(
                id=1,
                image_path="新三国/a.webp",
                text="丞相发笑",
                tags=["三国"],
                collection_id=1,
                local_id=3,
                collection_name="新三国",
            ),
        }
        metadata_store = MockMetadataStore(entries)
        combined = CombinedSearcher(metadata_store, KeywordSearcher(metadata_store))

        results = combined.search_in(entries, None, [], ["三国"])

        assert len(results) == 1
        assert results[0].public_id == MemePublicId(1, 3)
        assert results[0].collection_name == "新三国"


class TestShuffleWithinSimilarityGroups:
    """_shuffle_within_similarity_groups 白盒测试。"""

    def test_preserves_group_order_and_membership(self) -> None:
        from bot.engine.combined_searcher import _shuffle_within_similarity_groups

        results = [
            SearchResult(entry_id=1, image_path="a", text="t1", similarity=100.0),
            SearchResult(entry_id=2, image_path="b", text="t2", similarity=100.0),
            SearchResult(entry_id=3, image_path="c", text="t3", similarity=80.0),
            SearchResult(entry_id=4, image_path="d", text="t4", similarity=80.0),
            SearchResult(entry_id=5, image_path="e", text="t5", similarity=60.0),
        ]
        out = _shuffle_within_similarity_groups(results)
        # 组间仍按 similarity 降序
        assert [r.similarity for r in out] == [100.0, 100.0, 80.0, 80.0, 60.0]
        # 每组成员集合不变
        assert {r.entry_id for r in out[:2]} == {1, 2}
        assert {r.entry_id for r in out[2:4]} == {3, 4}
        assert out[4].entry_id == 5

    def test_randomizes_within_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bot.engine.combined_searcher import _shuffle_within_similarity_groups

        monkeypatch.setattr(
            "bot.engine.combined_searcher.random.shuffle",
            lambda seq: seq.reverse(),
        )
        results = [
            SearchResult(entry_id=1, image_path="a", text="t1", similarity=100.0),
            SearchResult(entry_id=2, image_path="b", text="t2", similarity=100.0),
            SearchResult(entry_id=3, image_path="c", text="t3", similarity=80.0),
            SearchResult(entry_id=4, image_path="d", text="t4", similarity=80.0),
        ]
        out = _shuffle_within_similarity_groups(results)
        # 每组反转：[2,1] + [4,3]
        assert [r.entry_id for r in out] == [2, 1, 4, 3]
