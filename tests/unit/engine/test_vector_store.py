"""VectorStore 单元测试。"""

from pathlib import Path

import pytest

from bot.engine.vector_store import VectorHit, VectorStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_chroma_dir: Path) -> VectorStore:
    """已 load 的 VectorStore。"""
    s = VectorStore(str(tmp_chroma_dir))
    s.load()
    return s


class TestLoadAndCount:
    async def test_load_creates_collection(self, tmp_chroma_dir: Path) -> None:
        s = VectorStore(str(tmp_chroma_dir))
        s.load()
        assert s.count() == 0
        assert tmp_chroma_dir.exists()

    async def test_reload_preserves_data(self, tmp_chroma_dir: Path) -> None:
        s1 = VectorStore(str(tmp_chroma_dir))
        s1.load()
        await s1.upsert(1, [1.0, 0.0])
        s1.close()

        s2 = VectorStore(str(tmp_chroma_dir))
        s2.load()
        assert s2.count() == 1


class TestUpsertAndQuery:
    async def test_upsert_then_query_returns_hit(self, store: VectorStore) -> None:
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        hits = await store.query([1.0, 0.0], n_results=2)
        assert len(hits) == 2
        assert all(isinstance(h, VectorHit) for h in hits)
        assert hits[0].entry_id == 1
        assert abs(hits[0].similarity - 1.0) < 1e-5

    async def test_upsert_overwrites(self, store: VectorStore) -> None:
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(1, [0.0, 1.0])
        assert store.count() == 1
        hits = await store.query([0.0, 1.0], n_results=1)
        assert hits[0].entry_id == 1
        assert abs(hits[0].similarity - 1.0) < 1e-5

    async def test_query_returns_int_entry_id(self, store: VectorStore) -> None:
        await store.upsert(42, [1.0, 0.0])
        hits = await store.query([1.0, 0.0], n_results=1)
        assert hits[0].entry_id == 42
        assert isinstance(hits[0].entry_id, int)

    async def test_query_empty_collection_returns_empty(self, store: VectorStore) -> None:
        assert await store.query([1.0, 0.0], n_results=10) == []

    async def test_query_n_results_limits(self, store: VectorStore) -> None:
        for i in range(5):
            await store.upsert(i, [float(i), 0.0])
        hits = await store.query([0.0, 0.0], n_results=3)
        assert len(hits) == 3

    async def test_query_none_returns_all(self, store: VectorStore) -> None:
        """n_results=None 时返回全库所有向量。"""
        for i in range(5):
            await store.upsert(i, [float(i), 0.0])
        hits = await store.query([0.0, 0.0], n_results=None)
        assert len(hits) == 5


class TestRemove:
    async def test_remove_existing(self, store: VectorStore) -> None:
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        await store.remove(1)
        assert store.count() == 1
        hits = await store.query([1.0, 0.0], n_results=2)
        assert [h.entry_id for h in hits] == [2]

    async def test_remove_nonexistent_silent(self, store: VectorStore) -> None:
        await store.remove(999)

    async def test_remove_many(self, store: VectorStore) -> None:
        for i in range(4):
            await store.upsert(i, [float(i), 0.0])
        await store.remove_many([0, 1])
        assert store.count() == 2

    async def test_remove_many_empty_noop(self, store: VectorStore) -> None:
        await store.remove_many([])


class TestRebuildAll:
    async def test_rebuild_all_replaces(self, store: VectorStore) -> None:
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        await store.rebuild_all([(10, [1.0, 1.0]), (20, [0.5, 0.5])])
        assert store.count() == 2
        hits = await store.query([1.0, 1.0], n_results=2)
        ids = {h.entry_id for h in hits}
        assert ids == {10, 20}

    async def test_rebuild_all_empty(self, store: VectorStore) -> None:
        await store.upsert(1, [1.0, 0.0])
        await store.rebuild_all([])
        assert store.count() == 0


class TestGetAllIds:
    async def test_get_all_ids_empty(self, store: VectorStore) -> None:
        """空 collection 时返回空集。"""
        assert await store.get_all_ids() == set()

    async def test_get_all_ids_returns_all(self, store: VectorStore) -> None:
        """upsert 多条后返回全部 entry_id。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        await store.upsert(42, [1.0, 1.0])
        assert await store.get_all_ids() == {1, 2, 42}

    async def test_get_all_ids_after_remove(self, store: VectorStore) -> None:
        """remove 后被删 id 不再出现在集合中。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        await store.upsert(42, [1.0, 1.0])
        await store.remove(2)
        assert await store.get_all_ids() == {1, 42}
