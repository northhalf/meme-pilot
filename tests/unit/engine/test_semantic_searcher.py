"""SemanticSearcher 单元测试。"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.types import MemePublicId
from bot.engine.vector_store import VectorHit, VectorStore


def MockMetadataStore(entries: dict[int, MemeEntry]) -> MetadataStore:
    """构造模拟 MetadataStore，get_entry / get_all_entries 返回预定义 entries。"""
    mock = MagicMock()
    mock.get_all_entries.return_value = entries
    mock.get_entry.side_effect = lambda entry_id: entries.get(entry_id)
    return cast(MetadataStore, mock)


def MockVectorStore(hits: list[VectorHit]) -> VectorStore:
    """构造模拟 VectorStore，count 返回 hits 数量，query 按 n_results 切片返回。"""
    mock = MagicMock()

    def _query(
        query_embedding: list[float],
        n_results: int | None = 10,
        *,
        collection_id: int | None = None,
    ) -> list[VectorHit]:
        if n_results is None:
            return list(hits)
        return hits[:n_results]

    mock.count.return_value = len(hits)
    mock.query = AsyncMock(side_effect=_query)
    return cast(VectorStore, mock)


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="a.jpg", text="加班到崩溃", speaker="小明"),
        2: MemeEntry(id=2, image_path="b.jpg", text="猫抓蝴蝶"),
        3: MemeEntry(id=3, image_path="c.jpg", text="周末快乐"),
    }


@pytest.mark.asyncio
async def test_search_semantic_returns_search_results(
    sample_entries: dict[int, MemeEntry],
) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
    ]
    searcher = SemanticSearcher(
        MockMetadataStore(sample_entries), MockVectorStore(hits)
    )

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 2
    assert results[0].entry_id == 1
    assert results[0].similarity == 0.95
    assert results[0].speaker == "小明"
    assert results[1].entry_id == 2


@pytest.mark.asyncio
async def test_search_semantic_skips_missing_metadata(
    sample_entries: dict[int, MemeEntry],
) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=999, similarity=0.90),  # 不存在的 entry
    ]
    searcher = SemanticSearcher(
        MockMetadataStore(sample_entries), MockVectorStore(hits)
    )

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 1
    assert results[0].entry_id == 1


@pytest.mark.asyncio
async def test_search_semantic_respects_limit(
    sample_entries: dict[int, MemeEntry],
) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
        VectorHit(entry_id=3, similarity=0.75),
    ]
    searcher = SemanticSearcher(
        MockMetadataStore(sample_entries), MockVectorStore(hits)
    )

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
    searcher = SemanticSearcher(
        MockMetadataStore(sample_entries), MockVectorStore(hits)
    )

    results = await searcher.search_semantic([0.1] * 1024, limit=None)

    assert len(results) == 3
    assert [r.entry_id for r in results] == [1, 2, 3]


@pytest.mark.asyncio
async def test_search_semantic_passes_collection_filter() -> None:
    """collection_id 应正确传给 vector_store.query。"""
    vector_store = AsyncMock()
    vector_store.query.return_value = []
    metadata_store = Mock()
    metadata_store.get_all_entries.return_value = {}
    searcher = SemanticSearcher(metadata_store, vector_store)

    await searcher.search_semantic([1.0, 0.0], limit=10, collection_id=2)

    vector_store.query.assert_awaited_once_with(
        [1.0, 0.0], n_results=10, collection_id=2
    )


@pytest.mark.asyncio
async def test_search_semantic_carries_collection_identity() -> None:
    """SearchResult 应携带 MemeEntry 的合集身份。"""
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
    hits = [VectorHit(entry_id=1, similarity=0.95)]
    searcher = SemanticSearcher(MockMetadataStore(entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 1
    assert results[0].public_id == MemePublicId(1, 2)
    assert results[0].collection_name == "新三国"
