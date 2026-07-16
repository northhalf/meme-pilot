"""VectorStore 单元测试。"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from bot.engine.vector_store import (
    VectorHit,
    VectorMetadata,
    VectorRecord,
    VectorStore,
)

pytestmark = pytest.mark.asyncio


class _QuerySpyCollection:
    """记录 query 调用参数的最小 Chroma Collection 替身。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def count(self) -> int:
        """返回固定记录数。"""
        return 1

    def query(self, **kwargs: Any) -> dict[str, Any]:
        """记录参数并返回一条合法查询结果。"""
        self.calls.append(kwargs)
        return {"ids": [["1"]], "distances": [[0.0]]}


class _GetResultCollection:
    """返回指定 get 结果的最小 Chroma Collection 替身。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def get(self, **_: Any) -> dict[str, Any]:
        """返回预设结果。"""
        return self.result


class _QueryResultCollection:
    """返回指定 query 结果的最小 Chroma Collection 替身。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def count(self) -> int:
        """返回固定记录数。"""
        return 2

    def query(self, **_: Any) -> dict[str, Any]:
        """返回预设结果。"""
        return self.result


@pytest.fixture
def store(tmp_chroma_dir: Path) -> Iterator[VectorStore]:
    """创建并在测试后关闭已加载的 VectorStore。"""
    vector_store = VectorStore(str(tmp_chroma_dir))
    vector_store.load()
    try:
        yield vector_store
    finally:
        vector_store.close()


class TestLoadAndCount:
    """测试加载、关闭与计数。"""

    async def test_load_creates_collection(self, tmp_chroma_dir: Path) -> None:
        """load 应创建空 collection。"""
        vector_store = VectorStore(str(tmp_chroma_dir))
        vector_store.load()
        try:
            assert vector_store.count() == 0
            assert tmp_chroma_dir.exists()
        finally:
            vector_store.close()

    async def test_reload_preserves_data(self, tmp_chroma_dir: Path) -> None:
        """关闭后重新加载应保留记录。"""
        first = VectorStore(str(tmp_chroma_dir))
        first.load()
        await first.upsert(1, [1.0, 0.0])
        first.close()

        second = VectorStore(str(tmp_chroma_dir))
        second.load()
        try:
            assert second.count() == 1
        finally:
            second.close()


class TestUpsertAndQuery:
    """测试写入、过滤查询与查询结果校验。"""

    async def test_upsert_then_query_returns_hit(self, store: VectorStore) -> None:
        """写入后应按相似度返回 VectorHit。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])

        hits = await store.query([1.0, 0.0], n_results=2)

        assert len(hits) == 2
        assert all(isinstance(hit, VectorHit) for hit in hits)
        assert hits[0].entry_id == 1
        assert hits[0].similarity == pytest.approx(1.0)

    async def test_upsert_overwrites_embedding_and_metadata(
        self, store: VectorStore
    ) -> None:
        """相同 ID 再次 upsert 应覆盖向量与合集 metadata。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)
        await store.upsert(1, [0.0, 1.0], collection_id=2)

        assert store.count() == 1
        assert await store.get_collection_ids() == {1: 2}
        hits = await store.query([0.0, 1.0], n_results=1, collection_id=2)
        assert hits[0].entry_id == 1
        assert hits[0].similarity == pytest.approx(1.0)

    async def test_query_returns_int_entry_id(self, store: VectorStore) -> None:
        """Chroma 字符串 ID 应转换为 int。"""
        await store.upsert(42, [1.0, 0.0])

        hits = await store.query([1.0, 0.0], n_results=1)

        assert hits[0].entry_id == 42
        assert isinstance(hits[0].entry_id, int)

    async def test_query_empty_collection_returns_empty(
        self, store: VectorStore
    ) -> None:
        """空 collection 查询应返回空列表。"""
        assert await store.query([1.0, 0.0], n_results=10) == []

    async def test_query_n_results_limits(self, store: VectorStore) -> None:
        """n_results 应限制返回数量。"""
        for entry_id in range(1, 6):
            await store.upsert(entry_id, [float(entry_id), 0.0])

        hits = await store.query([0.0, 0.0], n_results=3)

        assert len(hits) == 3

    async def test_query_none_returns_all_collections(self, store: VectorStore) -> None:
        """collection_id=None 与 n_results=None 应召回全库。"""
        await store.upsert(1, [1.0, 0.0], collection_id=0)
        await store.upsert(2, [1.0, 0.0], collection_id=1)
        await store.upsert(3, [1.0, 0.0], collection_id=2)

        hits = await store.query([1.0, 0.0], n_results=None, collection_id=None)

        assert {hit.entry_id for hit in hits} == {1, 2, 3}

    async def test_query_filters_by_collection(self, store: VectorStore) -> None:
        """指定普通合集时只应返回该合集记录。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)
        await store.upsert(2, [1.0, 0.0], collection_id=2)

        hits = await store.query([1.0, 0.0], n_results=10, collection_id=2)

        assert [hit.entry_id for hit in hits] == [2]

    async def test_query_zero_filters_root_collection(self, store: VectorStore) -> None:
        """collection_id=0 应只查询根目录 metadata。"""
        await store.upsert(1, [1.0, 0.0], collection_id=0)
        await store.upsert(2, [1.0, 0.0], collection_id=1)

        hits = await store.query([1.0, 0.0], n_results=10, collection_id=0)

        assert [hit.entry_id for hit in hits] == [1]

    async def test_query_omits_where_for_all_collections(
        self, store: VectorStore
    ) -> None:
        """全库查询不得向 Chroma 传 where。"""
        spy = _QuerySpyCollection()
        store._collection = cast(Any, spy)

        await store.query([1.0, 0.0], collection_id=None)

        assert "where" not in spy.calls[0]

    async def test_query_passes_where_for_specific_collection(
        self, store: VectorStore
    ) -> None:
        """合集查询应向 Chroma 传精确 metadata 过滤条件。"""
        spy = _QuerySpyCollection()
        store._collection = cast(Any, spy)

        await store.query([1.0, 0.0], collection_id=0)

        assert spy.calls[0]["where"] == {"collection_id": 0}

    @pytest.mark.parametrize("invalid_collection_id", [True, 1.0, -1])
    async def test_upsert_rejects_invalid_collection_id(
        self,
        store: VectorStore,
        invalid_collection_id: bool | float | int,
    ) -> None:
        """写入应拒绝 bool、float 和负合集编号。"""
        with pytest.raises(ValueError):
            await store.upsert(
                1,
                [1.0, 0.0],
                collection_id=cast(int, invalid_collection_id),
            )

    @pytest.mark.parametrize("invalid_collection_id", [True, 1.0, -1])
    async def test_query_rejects_invalid_collection_id(
        self,
        store: VectorStore,
        invalid_collection_id: bool | float | int,
    ) -> None:
        """查询应拒绝 bool、float 和负合集编号。"""
        with pytest.raises(ValueError):
            await store.query(
                [1.0, 0.0],
                collection_id=cast(int, invalid_collection_id),
            )

    @pytest.mark.parametrize("invalid_entry_id", [True, 0, -1, 1.0])
    async def test_upsert_rejects_invalid_entry_id(
        self,
        store: VectorStore,
        invalid_entry_id: bool | float | int,
    ) -> None:
        """写入应拒绝 bool、零、负数和非整数 ID。"""
        with pytest.raises(ValueError, match="entry_id 必须是正整数"):
            await store.upsert(cast(int, invalid_entry_id), [1.0, 0.0])

    @pytest.mark.parametrize(
        "invalid_embedding",
        [
            [],
            [True],
            ["1.0"],
            [float("nan")],
            [float("inf")],
        ],
    )
    async def test_upsert_rejects_invalid_embedding(
        self,
        store: VectorStore,
        invalid_embedding: list[object],
    ) -> None:
        """写入应拒绝空向量及非数值、bool、非有限向量。"""
        with pytest.raises(ValueError, match="embedding 必须"):
            await store.upsert(1, cast(list[float], invalid_embedding))

    @pytest.mark.parametrize("invalid_n_results", [True, 0, -1, 1.0])
    async def test_query_rejects_invalid_n_results(
        self,
        store: VectorStore,
        invalid_n_results: bool | float | int,
    ) -> None:
        """n_results 应拒绝 bool、零、负数和非整数。"""
        with pytest.raises(ValueError, match="n_results 必须是正整数或 None"):
            await store.query(
                [1.0, 0.0],
                n_results=cast(int, invalid_n_results),
            )

    async def test_query_rejects_mismatched_result_shape(
        self, store: VectorStore
    ) -> None:
        """IDs 与 distances 数量不一致时不得静默截断。"""
        store._collection = cast(
            Any,
            _QueryResultCollection({"ids": [["1", "2"]], "distances": [[0.0]]}),
        )

        with pytest.raises(RuntimeError, match="数量不一致"):
            await store.query([1.0, 0.0], n_results=2)

    async def test_upsert_does_not_retain_embedding_alias(
        self, store: VectorStore
    ) -> None:
        """写入后修改输入列表不得改变已存向量。"""
        embedding = [1.0, 0.0]
        await store.upsert(1, embedding)

        embedding[0] = 0.0

        snapshot = await store.snapshot_records([1])
        assert snapshot[0].embedding == pytest.approx([1.0, 0.0])


class TestRemove:
    """测试单条与批量删除。"""

    async def test_remove_existing(self, store: VectorStore) -> None:
        """删除已存在记录后查询不应返回该 ID。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])

        await store.remove(1)

        assert store.count() == 1
        hits = await store.query([1.0, 0.0], n_results=2)
        assert [hit.entry_id for hit in hits] == [2]

    async def test_remove_nonexistent_silent(self, store: VectorStore) -> None:
        """删除不存在记录应静默。"""
        await store.remove(999)

    async def test_remove_many(self, store: VectorStore) -> None:
        """批量删除应移除全部目标 ID。"""
        for entry_id in range(1, 5):
            await store.upsert(entry_id, [float(entry_id), 0.0])

        await store.remove_many([1, 2])

        assert store.count() == 2

    async def test_remove_many_empty_noop(self, store: VectorStore) -> None:
        """空列表批量删除应 no-op。"""
        await store.remove_many([])


class TestRebuildAll:
    """测试携带合集 metadata 的全量重建。"""

    async def test_rebuild_all_replaces_with_collection_metadata(
        self, store: VectorStore
    ) -> None:
        """重建应替换所有记录并写入各自合集编号。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])

        await store.rebuild_all([(10, [1.0, 1.0], 1), (20, [0.5, 0.5], 2)])

        assert store.count() == 2
        assert await store.get_collection_ids() == {10: 1, 20: 2}
        assert [
            hit.entry_id
            for hit in await store.query([1.0, 1.0], n_results=2, collection_id=1)
        ] == [10]

    async def test_rebuild_all_empty(self, store: VectorStore) -> None:
        """空重建应清空 collection。"""
        await store.upsert(1, [1.0, 0.0])

        await store.rebuild_all([])

        assert store.count() == 0

    async def test_rebuild_all_accepts_legacy_pairs_as_root_metadata(
        self, store: VectorStore
    ) -> None:
        """过渡期二元组调用应按根目录 collection_id=0 重建。"""
        await store.rebuild_all([(10, [1.0, 0.0])])

        assert await store.get_collection_ids() == {10: 0}

    @pytest.mark.parametrize("invalid_collection_id", [True, 1.0, -1])
    async def test_rebuild_all_rejects_invalid_collection_id_before_clearing(
        self,
        store: VectorStore,
        invalid_collection_id: bool | float | int,
    ) -> None:
        """非法合集编号应在清空原 collection 前被拒绝。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError):
            await store.rebuild_all(
                [
                    (
                        2,
                        [0.0, 1.0],
                        cast(int, invalid_collection_id),
                    )
                ]
            )

        assert await store.get_all_ids() == {1}

    @pytest.mark.parametrize("invalid_entry_id", [True, 0, -1, 1.0])
    async def test_rebuild_all_rejects_invalid_entry_id_before_clearing(
        self,
        store: VectorStore,
        invalid_entry_id: bool | float | int,
    ) -> None:
        """非法 ID 应在清空原 collection 前被拒绝。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError, match="entry_id 必须是正整数"):
            await store.rebuild_all([(cast(int, invalid_entry_id), [0.0, 1.0], 2)])

        assert await store.get_all_ids() == {1}

    @pytest.mark.parametrize(
        "invalid_embedding",
        [
            [],
            [True],
            ["1.0"],
            [float("nan")],
            [float("inf")],
        ],
    )
    async def test_rebuild_all_rejects_invalid_embedding_before_clearing(
        self,
        store: VectorStore,
        invalid_embedding: list[object],
    ) -> None:
        """非法向量应在清空原 collection 前被拒绝。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError, match="embedding 必须"):
            await store.rebuild_all([(2, cast(list[float], invalid_embedding), 2)])

        assert await store.get_all_ids() == {1}

    async def test_rebuild_all_rejects_mixed_pair_and_triple_items_before_clearing(
        self,
        store: VectorStore,
    ) -> None:
        """二元与三元记录混用存在歧义，应在清空前整体拒绝。"""
        await store.upsert(1, [1.0, 0.0])

        mixed_items = cast(
            list[tuple[int, list[float]]],
            [(2, [0.0, 1.0]), (3, [1.0, 1.0], 3)],
        )
        with pytest.raises(ValueError, match="不能混合"):
            await store.rebuild_all(mixed_items)

        assert await store.get_all_ids() == {1}

    async def test_rebuild_all_rejects_duplicate_ids_before_clearing(
        self,
        store: VectorStore,
    ) -> None:
        """重复 ID 会被 Chroma 拒绝，必须在清空旧数据前发现。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError, match="entry_id 重复"):
            await store.rebuild_all([(2, [0.0, 1.0], 2), (2, [1.0, 1.0], 3)])

        assert await store.get_all_ids() == {1}

    async def test_rebuild_all_rejects_inconsistent_dimensions_before_clearing(
        self,
        store: VectorStore,
    ) -> None:
        """批量向量维度不一致时应在清空旧数据前拒绝。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError, match="维度必须一致"):
            await store.rebuild_all([(2, [0.0, 1.0], 2), (3, [1.0], 3)])

        assert await store.get_all_ids() == {1}


class TestMetadata:
    """测试 metadata 读取、覆盖更新和合集编号合并更新。"""

    async def test_get_collection_ids_includes_missing_metadata(
        self, store: VectorStore
    ) -> None:
        """原无 metadata 的记录应保留并映射为 None。"""
        await store.upsert(1, [1.0, 0.0], collection_id=3)
        store._require_collection().add(ids=["2"], embeddings=[[0.0, 1.0]])

        assert await store.get_collection_ids() == {1: 3, 2: None}

    async def test_get_metadatas_includes_missing_metadata_and_returns_copy(
        self, store: VectorStore
    ) -> None:
        """metadata 缺失映射为空字典，返回值不得泄漏内部别名。"""
        await store.upsert(1, [1.0, 0.0], collection_id=3)
        store._require_collection().add(ids=["2"], embeddings=[[0.0, 1.0]])

        first = await store.get_metadatas()
        first[1]["collection_id"] = 99
        first[2]["source"] = "mutated"

        assert await store.get_metadatas() == {
            1: {"collection_id": 3},
            2: {},
        }

    async def test_update_metadata_replaces_complete_metadata(
        self, store: VectorStore
    ) -> None:
        """update_metadata 应执行完整覆盖而非隐式合并。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)

        await store.update_metadata(1, {"source": "legacy", "enabled": True})

        assert await store.get_metadatas() == {1: {"source": "legacy", "enabled": True}}
        assert await store.get_collection_ids() == {1: None}

    async def test_update_metadata_copies_input(self, store: VectorStore) -> None:
        """更新后修改输入字典不得改变已存 metadata。"""
        await store.upsert(1, [1.0, 0.0])
        metadata: VectorMetadata = {"source": "legacy"}

        await store.update_metadata(1, metadata)
        metadata["source"] = "mutated"

        assert await store.get_metadatas() == {1: {"source": "legacy"}}

    async def test_update_metadata_can_clear_all_metadata(
        self, store: VectorStore
    ) -> None:
        """空字典完整覆盖应删除全部已有 metadata。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)

        await store.update_metadata(1, {})

        assert await store.get_metadatas() == {1: {}}

    async def test_update_metadata_empty_to_empty_is_noop(
        self, store: VectorStore
    ) -> None:
        """Chroma 不接受空更新；原值和目标均为空时应安全 no-op。"""
        store._require_collection().add(ids=["1"], embeddings=[[1.0, 0.0]])

        await store.update_metadata(1, {})

        assert await store.get_metadatas() == {1: {}}

    async def test_update_metadata_rejects_missing_id(self, store: VectorStore) -> None:
        """更新不存在记录应抛 ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await store.update_metadata(999, {"source": "legacy"})

    @pytest.mark.parametrize("invalid_entry_id", [True, 0, -1, 1.0])
    async def test_update_metadata_rejects_invalid_entry_id(
        self,
        store: VectorStore,
        invalid_entry_id: bool | float | int,
    ) -> None:
        """metadata 更新应拒绝 bool、零、负数和非整数 ID。"""
        with pytest.raises(ValueError, match="entry_id 必须是正整数"):
            await store.update_metadata(
                cast(int, invalid_entry_id),
                {"source": "legacy"},
            )

    @pytest.mark.parametrize(
        "invalid_metadata",
        [
            {1: "value"},
            {"source": None},
            {"source": ["legacy"]},
        ],
    )
    async def test_update_metadata_rejects_invalid_types(
        self,
        store: VectorStore,
        invalid_metadata: dict[object, object],
    ) -> None:
        """metadata 应只接受字符串键和 Chroma 标量值。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError):
            await store.update_metadata(1, cast(VectorMetadata, invalid_metadata))

    async def test_update_collection_id_preserves_embedding_and_other_metadata(
        self, store: VectorStore
    ) -> None:
        """只替换 collection_id 时应保留向量和其他 metadata。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)
        await store.update_metadata(1, {"collection_id": 1, "source": "legacy"})

        await store.update_collection_id(1, 2)

        assert await store.get_metadatas() == {
            1: {"collection_id": 2, "source": "legacy"}
        }
        hits = await store.query([1.0, 0.0], n_results=1, collection_id=2)
        assert hits[0].entry_id == 1
        assert hits[0].similarity == pytest.approx(1.0)

    async def test_update_collection_id_rejects_missing_id(
        self, store: VectorStore
    ) -> None:
        """更新不存在记录的合集编号应抛 ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await store.update_collection_id(999, 2)

    @pytest.mark.parametrize("invalid_entry_id", [True, 0, -1, 1.0])
    async def test_update_collection_id_rejects_invalid_entry_id(
        self,
        store: VectorStore,
        invalid_entry_id: bool | float | int,
    ) -> None:
        """合集更新应拒绝 bool、零、负数和非整数 ID。"""
        with pytest.raises(ValueError, match="entry_id 必须是正整数"):
            await store.update_collection_id(cast(int, invalid_entry_id), 2)

    @pytest.mark.parametrize("invalid_collection_id", [True, 1.0, -1])
    async def test_update_collection_id_rejects_invalid_collection_id(
        self,
        store: VectorStore,
        invalid_collection_id: bool | float | int,
    ) -> None:
        """合集更新应拒绝 bool、float 和负数。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError):
            await store.update_collection_id(1, cast(int, invalid_collection_id))

    @pytest.mark.parametrize("damaged_collection_id", [True, 1.5, -1])
    async def test_get_collection_ids_rejects_damaged_metadata(
        self,
        store: VectorStore,
        damaged_collection_id: bool | float | int,
    ) -> None:
        """损坏的 collection_id 不得通过 int() 截断或隐式转换。"""
        store._require_collection().add(
            ids=["1"],
            embeddings=[[1.0, 0.0]],
            metadatas=[{"collection_id": damaged_collection_id}],
        )

        with pytest.raises((ValueError, RuntimeError)):
            await store.get_collection_ids()

    @pytest.mark.parametrize("method_name", ["get_metadatas", "get_collection_ids"])
    async def test_metadata_reads_reject_mismatched_result_shape(
        self, store: VectorStore, method_name: str
    ) -> None:
        """IDs 与 metadatas 数量不一致时不得静默截断。"""
        store._collection = cast(
            Any,
            _GetResultCollection(
                {"ids": ["1", "2"], "metadatas": [{"collection_id": 1}]}
            ),
        )

        method = getattr(store, method_name)
        with pytest.raises(RuntimeError, match="数量不一致"):
            await method()


class TestSnapshotAndRestore:
    """测试完整记录快照与补偿恢复。"""

    async def test_snapshot_preserves_request_order_and_deduplicates_ids(
        self, store: VectorStore
    ) -> None:
        """快照应按首次请求顺序返回并忽略后续重复 ID。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)
        await store.upsert(2, [0.0, 1.0], collection_id=2)

        records = await store.snapshot_records([2, 1, 2])

        assert [record.entry_id for record in records] == [2, 1]

    async def test_snapshot_rejects_any_missing_id_without_partial_result(
        self, store: VectorStore
    ) -> None:
        """任一 ID 缺失时应整体拒绝快照。"""
        await store.upsert(1, [1.0, 0.0])

        with pytest.raises(ValueError, match="不存在"):
            await store.snapshot_records([1, 999])

    @pytest.mark.parametrize("invalid_entry_id", [True, 0, -1, 1.0])
    async def test_snapshot_rejects_invalid_entry_id(
        self,
        store: VectorStore,
        invalid_entry_id: bool | float | int,
    ) -> None:
        """快照应拒绝 bool、零、负数和非整数 ID。"""
        with pytest.raises(ValueError, match="entry_ids 必须只包含正整数"):
            await store.snapshot_records([cast(int, invalid_entry_id)])

    async def test_snapshot_returns_independent_lists_and_dicts(
        self, store: VectorStore
    ) -> None:
        """修改返回记录不得改变 Chroma 中的向量或 metadata。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)

        first = await store.snapshot_records([1])
        first[0].metadata["collection_id"] = 99
        second = await store.snapshot_records([1])

        assert second[0].embedding == pytest.approx([1.0, 0.0])
        assert second[0].metadata == {"collection_id": 1}

    async def test_snapshot_and_restore_removes_added_metadata(
        self, store: VectorStore
    ) -> None:
        """原无 metadata 的记录恢复后应仍为空字典。"""
        store._require_collection().add(ids=["1"], embeddings=[[1.0, 0.0]])
        snapshot = await store.snapshot_records([1])
        await store.update_collection_id(1, 2)

        await store.restore_records(snapshot)

        assert await store.get_metadatas() == {1: {}}
        restored = await store.snapshot_records([1])
        assert restored[0].embedding == pytest.approx([1.0, 0.0])

    async def test_restore_replaces_embedding_and_metadata(
        self, store: VectorStore
    ) -> None:
        """恢复应完整替换目标 ID 的向量与 metadata。"""
        await store.upsert(1, [1.0, 0.0], collection_id=1)
        snapshot = await store.snapshot_records([1])
        await store.upsert(1, [0.0, 1.0], collection_id=2)

        await store.restore_records(snapshot)

        restored = await store.snapshot_records([1])
        assert restored[0].embedding == pytest.approx([1.0, 0.0])
        assert restored[0].metadata == {"collection_id": 1}

    async def test_restore_copies_input_lists_and_dicts(
        self, store: VectorStore
    ) -> None:
        """恢复后修改输入 record 不得改变已存数据。"""
        embedding = (1.0, 0.0)
        metadata: VectorMetadata = {"collection_id": 3}
        record = VectorRecord(1, embedding, metadata)

        await store.restore_records([record])
        metadata["collection_id"] = 9

        restored = await store.snapshot_records([1])
        assert restored[0].embedding == pytest.approx([1.0, 0.0])
        assert restored[0].metadata == {"collection_id": 3}

    async def test_restore_empty_noop(self, store: VectorStore) -> None:
        """空恢复应 no-op。"""
        await store.upsert(1, [1.0, 0.0])

        await store.restore_records([])

        assert await store.get_all_ids() == {1}

    @pytest.mark.parametrize(
        "records",
        [
            [VectorRecord(0, (1.0,), {})],
            [VectorRecord(1, (), {})],
            [VectorRecord(1, (cast(float, True),), {})],
            [VectorRecord(1, (float("nan"),), {})],
            [VectorRecord(1, (1.0,), {"invalid": cast(Any, None)})],
            [VectorRecord(1, (1.0,), {}), VectorRecord(1, (2.0,), {})],
        ],
    )
    async def test_restore_validates_all_records_before_deleting(
        self, store: VectorStore, records: list[VectorRecord]
    ) -> None:
        """可预见的非法记录应在修改 Chroma 前整体拒绝。"""
        await store.upsert(9, [9.0], collection_id=9)

        with pytest.raises(ValueError):
            await store.restore_records(records)

        assert await store.get_all_ids() == {9}

    @pytest.mark.parametrize(
        "result",
        [
            {
                "ids": ["1", "2"],
                "embeddings": [[1.0, 0.0]],
                "metadatas": [{}, {}],
            },
            {
                "ids": ["1"],
                "embeddings": [[1.0, 0.0]],
                "metadatas": [],
            },
            {"ids": ["1"], "embeddings": None, "metadatas": [{}]},
            {
                "ids": ["1"],
                "embeddings": [None],
                "metadatas": [{}],
            },
            {
                "ids": ["1"],
                "embeddings": [["1.0"]],
                "metadatas": [{}],
            },
            {
                "ids": ["1"],
                "embeddings": [[True]],
                "metadatas": [{}],
            },
            {
                "ids": ["1"],
                "embeddings": [[float("nan")]],
                "metadatas": [{}],
            },
            {
                "ids": ["1"],
                "embeddings": 1,
                "metadatas": [{}],
            },
        ],
    )
    async def test_snapshot_rejects_invalid_chroma_result_shape(
        self, store: VectorStore, result: dict[str, Any]
    ) -> None:
        """快照应拒绝长度不一致或缺失 embedding 的 Chroma 结果。"""
        store._collection = cast(Any, _GetResultCollection(result))

        with pytest.raises(RuntimeError):
            await store.snapshot_records([1, 2])


class TestGetAllIds:
    """测试读取全部内部 ID。"""

    async def test_get_all_ids_empty(self, store: VectorStore) -> None:
        """空 collection 应返回空集。"""
        assert await store.get_all_ids() == set()

    async def test_get_all_ids_returns_all(self, store: VectorStore) -> None:
        """多条写入后应返回全部 entry_id。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        await store.upsert(42, [1.0, 1.0])

        assert await store.get_all_ids() == {1, 2, 42}

    async def test_get_all_ids_after_remove(self, store: VectorStore) -> None:
        """remove 后被删 ID 不应出现在集合中。"""
        await store.upsert(1, [1.0, 0.0])
        await store.upsert(2, [0.0, 1.0])
        await store.upsert(42, [1.0, 1.0])

        await store.remove(2)

        assert await store.get_all_ids() == {1, 42}
