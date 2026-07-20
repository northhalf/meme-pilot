"""RandomSearcher 单元测试。"""

import pytest

from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry
from bot.engine.random_searcher import RandomSearcher
from bot.engine.types import MemePublicId


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
        for i in range(1, 21)
    }


@pytest.fixture
def random_searcher() -> RandomSearcher:
    return RandomSearcher(KeywordSearcher())


class TestSearchRandomIn:
    """search_random_in：在显式传入的 entries 子集上随机取样。"""

    def test_full_random_returns_limit(
        self,
        random_searcher: RandomSearcher,
        sample_entries: dict[int, MemeEntry],
    ) -> None:
        results = random_searcher.search_random_in(sample_entries, limit=10)
        assert len(results) == 10
        assert len({r.entry_id for r in results}) == 10
        assert all(r.similarity == 0.0 for r in results)

    def test_full_random_returns_all_when_entries_less_than_limit(
        self,
        random_searcher: RandomSearcher,
    ) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="甲"),
            2: MemeEntry(id=2, image_path="b.jpg", text="乙"),
        }
        results = random_searcher.search_random_in(entries, limit=10)
        assert len(results) == 2
        assert {r.entry_id for r in results} == {1, 2}

    def test_keyword_random_returns_from_search_results(
        self,
        random_searcher: RandomSearcher,
        sample_entries: dict[int, MemeEntry],
    ) -> None:
        results = random_searcher.search_random_in(
            sample_entries, keyword="加班", limit=10
        )
        assert len(results) == 10
        assert all("加班" in r.text for r in results)

    def test_keyword_no_match_returns_empty(
        self,
        random_searcher: RandomSearcher,
        sample_entries: dict[int, MemeEntry],
    ) -> None:
        results = random_searcher.search_random_in(
            sample_entries, keyword="火星文xyz", limit=10
        )
        assert results == []

    def test_empty_entries_returns_empty(self, random_searcher: RandomSearcher) -> None:
        assert random_searcher.search_random_in({}, limit=10) == []

    def test_random_seed_not_fixed(
        self,
        random_searcher: RandomSearcher,
        sample_entries: dict[int, MemeEntry],
    ) -> None:
        """两次独立调用应可能产生不同结果（非确定性）。"""
        results1 = random_searcher.search_random_in(sample_entries, limit=10)
        results2 = random_searcher.search_random_in(sample_entries, limit=10)
        ids1 = [r.entry_id for r in results1]
        ids2 = [r.entry_id for r in results2]
        # 20 个里随机取 10 个，两次完全相同的概率极低；允许偶尔相同
        assert len(ids1) == 10 and len(ids2) == 10

    def test_search_random_in_only_uses_supplied_entries(
        self, random_searcher: RandomSearcher
    ) -> None:
        entries = {
            2: MemeEntry(
                id=2,
                image_path="新三国/a.webp",
                text="文本",
                collection_id=1,
                local_id=1,
                collection_name="新三国",
            )
        }
        results = random_searcher.search_random_in(entries, limit=10)

        assert [result.entry_id for result in results] == [2]

    def test_search_random_in_with_keyword_filters_subset(
        self, random_searcher: RandomSearcher
    ) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班"),
            2: MemeEntry(
                id=2,
                image_path="新三国/a.webp",
                text="丞相发笑",
                collection_id=1,
                local_id=1,
                collection_name="新三国",
            ),
        }
        results = random_searcher.search_random_in(entries, keyword="丞相", limit=10)

        assert [result.entry_id for result in results] == [2]
        assert results[0].public_id == MemePublicId(1, 1)


class TestSearchResultCarriesMetadata:
    def test_speaker_and_tags_preserved(self, random_searcher: RandomSearcher) -> None:
        entries = {
            1: MemeEntry(
                id=1,
                image_path="a.jpg",
                text="加班",
                speaker="小明",
                tags=["吐槽"],
            ),
        }
        results = random_searcher.search_random_in(entries, limit=10)
        assert len(results) == 1
        assert results[0].speaker == "小明"
        assert results[0].tags == ("吐槽",)

    def test_collection_identity_preserved(
        self, random_searcher: RandomSearcher
    ) -> None:
        entries = {
            1: MemeEntry(
                id=1,
                image_path="新三国/a.webp",
                text="丞相发笑",
                collection_id=1,
                local_id=2,
                collection_name="新三国",
            ),
        }
        results = random_searcher.search_random_in(entries, limit=10)
        assert len(results) == 1
        assert results[0].public_id == MemePublicId(1, 2)
        assert results[0].collection_name == "新三国"
