"""SemanticSearcher 单元测试。"""


import pytest

from bot.engine.metadata_store import MemeEntry
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.vector_store import VectorHit


class MockMetadataStore:
    def __init__(self, entries: dict[int, MemeEntry]) -> None:
        self._entries = entries

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


class MockVectorStore:
    def __init__(self, hits: list[VectorHit]) -> None:
        self._hits = hits

    def count(self) -> int:
        return len(self._hits)

    async def query(
        self, query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]:
        if n_results is None:
            return list(self._hits)
        return self._hits[:n_results]


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="a.jpg", text="加班到崩溃", speaker="小明"),
        2: MemeEntry(id=2, image_path="b.jpg", text="猫抓蝴蝶"),
        3: MemeEntry(id=3, image_path="c.jpg", text="周末快乐"),
    }


@pytest.mark.asyncio
async def test_search_semantic_returns_search_results(sample_entries: dict[int, MemeEntry]) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 2
    assert results[0].entry_id == 1
    assert results[0].similarity == 0.95
    assert results[0].speaker == "小明"
    assert results[1].entry_id == 2


@pytest.mark.asyncio
async def test_search_semantic_skips_missing_metadata(
    sample_entries: dict[int, MemeEntry]
) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=999, similarity=0.90),  # 不存在的 entry
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 1
    assert results[0].entry_id == 1


@pytest.mark.asyncio
async def test_search_semantic_respects_limit(sample_entries: dict[int, MemeEntry]) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
        VectorHit(entry_id=3, similarity=0.75),
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=2)

    assert len(results) == 2
    assert results[0].entry_id == 1
    assert results[1].entry_id == 2


@pytest.mark.asyncio
async def test_search_semantic_limit_none_returns_all(
    sample_entries: dict[int, MemeEntry],
) -> None:
    """limit=None 时全库召回，不截断。"""
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
        VectorHit(entry_id=3, similarity=0.75),
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=None)

    assert len(results) == 3
    assert [r.entry_id for r in results] == [1, 2, 3]
