"""IndexManager 新 API 单元测试。

验证并发控制、读写锁、refresh/add 互斥、close 取消等核心行为。
使用 Fake Store 与 mock providers，无真实 sqlite/chroma I/O。
"""

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from typing import cast

from bot.engine.collection_manager import CollectionManager, CollectionNotFoundError
from bot.index_manager import (
    AddResult,
    CollectionSelectionExpiredError,
    CompressionError,
    DuplicateTextError,
    EmbeddingError,
    IndexAddCancelledError,
    IndexCorruptedError,
    FileSystemSnapshot,
    IndexManager,
    OcrError,
    RefreshInProgressError,
    SyncResult,
    WriteOp,
)
from bot.index_manager.index_types import _WriteRequest
from bot.engine.utils import resolve_unique_filename
from bot.engine.image_optimizer import ImageOptimizer, OptimizeResult
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.types import (
    GLOBAL_COLLECTION_NAME,
    CollectionSelection,
    CollectionSummary,
    MemeCollection,
    MemePublicId,
)
from bot.engine.vector_store import VectorHit, VectorRecord, VectorStore
from bot.session import ChatScope

# 哨兵值，区分「不修改字段」与显式的 None
_UNSET = object()


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


class FakeMetadataStore:
    """内存 MetadataStore，实现真接口，用于 IndexManager 测试。"""

    def __init__(self) -> None:
        self._entries: dict[int, MemeEntry] = {}
        self._collections: dict[int, MemeCollection] = {}
        self._collection_name_to_id: dict[str, int] = {}
        self._entries_by_collection: dict[int, dict[int, int]] = {0: {}}
        self._selected_collections: dict[ChatScope, int] = {}
        self._next_auto = 1
        self.add_order: list[int] = []

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self.get_entries()

    def get_entries(self, collection_id: int | None = None) -> dict[int, MemeEntry]:
        if collection_id is None:
            return dict(self._entries)
        result: dict[int, MemeEntry] = {}
        for local_id, eid in self._entries_by_collection.get(collection_id, {}).items():
            entry = self._entries.get(eid)
            if entry is not None:
                result[eid] = entry
        return result

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)

    def get_entry_by_public_id(self, public_id: MemePublicId) -> MemeEntry | None:
        eid = self._entries_by_collection.get(public_id.collection_id, {}).get(
            public_id.local_id
        )
        if eid is None:
            return None
        return self._entries.get(eid)

    def get_by_filename(self, image_path: str) -> MemeEntry | None:
        for e in self._entries.values():
            if e.image_path == image_path:
                return e
        return None

    def get_id_by_text(self, text: str, *, collection_id: int = 0) -> int | None:
        for eid, e in self._entries.items():
            if e.collection_id == collection_id and e.text == text:
                return eid
        return None

    def find_next_id(self) -> int:
        if not self._entries:
            return 1
        ids = set(self._entries)
        for i in range(1, max(ids) + 2):
            if i not in ids:
                return i
        return max(ids) + 1

    def find_next_local_id(self, collection_id: int) -> int:
        local_ids = set(self._entries_by_collection.get(collection_id, {}))
        if not local_ids:
            return 1
        for i in range(1, max(local_ids) + 2):
            if i not in local_ids:
                return i
        return max(local_ids) + 1

    def entry_count(self) -> int:
        return len(self._entries)

    def collection_entry_count(self, collection_id: int | None) -> int:
        if collection_id is None:
            return len(self._entries)
        return len(self._entries_by_collection.get(collection_id, {}))

    def get_all_text(self) -> list[tuple[int, str]]:
        return [(eid, e.text) for eid, e in sorted(self._entries.items())]

    def create_collection(self, name: str) -> MemeCollection:
        collection_id = 1 if not self._collections else max(self._collections) + 1
        collection = MemeCollection(id=collection_id, name=name)
        self._collections[collection_id] = collection
        self._collection_name_to_id[name] = collection_id
        self._entries_by_collection.setdefault(collection_id, {})
        return collection

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        return self._collections.get(collection_id)

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        collection_id = self._collection_name_to_id.get(name)
        if collection_id is None:
            return None
        return self._collections.get(collection_id)

    def list_collections(self) -> list[MemeCollection]:
        return [self._collections[key] for key in sorted(self._collections)]

    def get_selected_collection(self, scope: ChatScope) -> int:
        return self._selected_collections.get(scope, 0)

    def set_selected_collection(self, scope: ChatScope, collection_id: int) -> None:
        self._selected_collections[scope] = collection_id

    def delete_collection_and_reset_scopes(self, collection_id: int) -> int:
        collection = self._collections.pop(collection_id)
        self._collection_name_to_id.pop(collection.name)
        self._entries_by_collection.pop(collection_id, None)
        reset = 0
        for scope, selected_id in list(self._selected_collections.items()):
            if selected_id == collection_id:
                self._selected_collections[scope] = 0
                reset += 1
        return reset

    def _collection_name(self, collection_id: int) -> str:
        if collection_id == 0:
            return GLOBAL_COLLECTION_NAME
        return self._collections[collection_id].name

    def add(
        self,
        image_path,
        text,
        speaker=None,
        tags=None,
        *,
        collection_id: int = 0,
    ) -> int:
        eid = self.find_next_id()
        local_id = self.find_next_local_id(collection_id)
        self._entries[eid] = MemeEntry(
            id=eid,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=sorted(set(tags or [])),
            collection_id=collection_id,
            local_id=local_id,
            collection_name=self._collection_name(collection_id),
        )
        self._entries_by_collection.setdefault(collection_id, {})[local_id] = eid
        self.add_order.append(eid)
        return eid

    def add_with_id(
        self,
        entry_id,
        image_path,
        text,
        speaker=None,
        tags=None,
        *,
        collection_id: int = 0,
        local_id: int | None = None,
    ) -> int:
        final_local_id = local_id if local_id is not None else entry_id
        self._entries[entry_id] = MemeEntry(
            id=entry_id,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=sorted(set(tags or [])),
            collection_id=collection_id,
            local_id=final_local_id,
            collection_name=self._collection_name(collection_id),
        )
        self._entries_by_collection.setdefault(collection_id, {})[final_local_id] = (
            entry_id
        )
        return entry_id

    def update(
        self,
        entry_id,
        *,
        image_path=_UNSET,
        text=_UNSET,
        speaker=_UNSET,
        tags=None,
        collection_id=_UNSET,
        local_id=_UNSET,
    ) -> bool:
        e = self._entries.get(entry_id)
        if e is None:
            return False
        new_image = cast(str, image_path if image_path is not _UNSET else e.image_path)
        new_text = cast(str, text if text is not _UNSET else e.text)
        new_speaker = cast(str | None, speaker if speaker is not _UNSET else e.speaker)
        new_tags = tags if tags is not None else e.tags
        new_collection_id = cast(
            int, collection_id if collection_id is not _UNSET else e.collection_id
        )
        new_local_id = cast(int, local_id if local_id is not _UNSET else e.local_id)

        if new_collection_id != e.collection_id or new_local_id != e.local_id:
            self._entries_by_collection[e.collection_id].pop(e.local_id, None)
            self._entries_by_collection.setdefault(new_collection_id, {})[
                new_local_id
            ] = entry_id

        self._entries[entry_id] = MemeEntry(
            id=entry_id,
            image_path=new_image,
            text=new_text,
            speaker=new_speaker,
            tags=new_tags,
            collection_id=new_collection_id,
            local_id=new_local_id,
            collection_name=self._collection_name(new_collection_id),
        )
        return True

    def remove(self, entry_id) -> bool:
        entry = self._entries.pop(entry_id, None)
        if entry is None:
            return False
        collection_entries = self._entries_by_collection.get(entry.collection_id)
        if collection_entries is not None:
            if collection_entries.get(entry.local_id) == entry_id:
                del collection_entries[entry.local_id]
        return True


class FakeVectorStore:
    """内存 VectorStore，实现真接口。"""

    def __init__(self) -> None:
        self._vecs: dict[int, list[float]] = {}
        self._collection_ids: dict[int, int] = {}
        self.upsert_error_for: set[int] | None = None  # 触发这些 id 的 upsert 抛错
        self.upsert_write_then_error_for: set[int] | None = None

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def count(self) -> int:
        return len(self._vecs)

    async def upsert(self, entry_id, embedding, *, collection_id: int = 0) -> None:
        if self.upsert_error_for is not None and entry_id in self.upsert_error_for:
            raise RuntimeError(f"upsert failed for {entry_id}")
        self._vecs[entry_id] = list(embedding)
        self._collection_ids[entry_id] = collection_id
        if (
            self.upsert_write_then_error_for is not None
            and entry_id in self.upsert_write_then_error_for
        ):
            raise RuntimeError(f"upsert committed then failed for {entry_id}")

    async def remove(self, entry_id) -> None:
        self._vecs.pop(entry_id, None)
        self._collection_ids.pop(entry_id, None)

    async def remove_many(self, entry_ids) -> None:
        for i in entry_ids:
            self._vecs.pop(i, None)
            self._collection_ids.pop(i, None)

    async def query(
        self,
        query_embedding: list[float],
        n_results: int | None = 10,
        *,
        collection_id: int | None = None,
    ) -> list[VectorHit]:
        sims = [
            (eid, sum(a * b for a, b in zip(query_embedding, vec)))
            for eid, vec in self._vecs.items()
            if collection_id is None or self._collection_ids.get(eid) == collection_id
        ]
        sims.sort(key=lambda x: -x[1])
        if n_results is None:
            return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims]
        return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims[:n_results]]

    async def rebuild_all(self, items) -> None:
        self._vecs = {}
        self._collection_ids = {}
        for item in items:
            if len(item) == 2:
                eid, vec = item
                collection_id = 0
            else:
                eid, vec, collection_id = item
            self._vecs[eid] = list(vec)
            self._collection_ids[eid] = collection_id

    async def get_all_ids(self) -> set[int]:
        return set(self._vecs.keys())

    def has(self, entry_id) -> bool:
        return entry_id in self._vecs

    async def get_collection_ids(self) -> dict[int, int | None]:
        return dict(self._collection_ids)

    async def update_collection_id(self, entry_id: int, collection_id: int) -> None:
        if entry_id in self._vecs:
            self._collection_ids[entry_id] = collection_id

    async def snapshot_records(self, entry_ids: list[int]) -> list[VectorRecord]:
        return [
            VectorRecord(
                entry_id=entry_id,
                embedding=tuple(self._vecs[entry_id]),
                metadata={"collection_id": self._collection_ids[entry_id]},
            )
            for entry_id in entry_ids
        ]

    async def restore_records(self, records: list[VectorRecord]) -> None:
        for record in records:
            self._vecs[record.entry_id] = list(record.embedding)
            self._collection_ids[record.entry_id] = int(
                record.metadata["collection_id"]
            )


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------


class MockOcrProvider:
    """OCR provider mock：固定返回传入文件名（不含扩展名）的文本。"""

    async def ocr(self, image_path: str) -> str:
        return Path(image_path).stem

    async def close(self) -> None:
        pass


class MockEmbeddingProvider:
    """Embedding provider mock：返回固定维度向量。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, text: str) -> list[float]:
        return [float(ord(text[0]) if text else 0)] * self._dim

    async def close(self) -> None:
        pass


class MockOptimizer:
    """图片优化器 mock：不做任何操作。"""

    async def optimize(self, image_path: str) -> OptimizeResult:
        _ = image_path
        return OptimizeResult(
            original_size=0,
            optimized_size=0,
            saved=0,
            skipped=True,
            output_path=image_path,
        )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def index_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造带 Fake Store 的 IndexManager。"""
    monkeypatch.setenv("READ_LOCK_TIMEOUT", "30")
    monkeypatch.setenv("ADD_COMMAND_TIMEOUT", "60")

    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = cast(MetadataStore, FakeMetadataStore())
    vector_store = cast(VectorStore, FakeVectorStore())
    keyword_searcher = KeywordSearcher(metadata_store)
    embedding_provider = MockEmbeddingProvider()
    random_searcher = RandomSearcher(metadata_store, keyword_searcher)
    semantic_searcher = SemanticSearcher(metadata_store, vector_store)
    from bot.engine.collection_manager import CollectionManager
    from bot.engine.combined_searcher import CombinedSearcher

    combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)
    collection_manager = CollectionManager(metadata_store)

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        ocr_provider=MockOcrProvider(),
        embedding_provider=embedding_provider,
        optimizer=cast(ImageOptimizer, MockOptimizer()),
        keyword_searcher=keyword_searcher,
        random_searcher=random_searcher,
        semantic_searcher=semantic_searcher,
        combined_searcher=combined_searcher,
        collection_manager=collection_manager,
    )
    asyncio.run(manager.load())
    return manager


@pytest.fixture
def collection_manager(index_manager: IndexManager):
    """返回 IndexManager 内部构造的 CollectionManager。"""
    return index_manager._collection_manager


# ---------------------------------------------------------------------------
# 数据类与异常
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_sync_result(self) -> None:
        r = SyncResult(added=3, deleted=1, failed=("bad.jpg",))
        assert r.added == 3 and r.deleted == 1 and r.deduped == 0
        assert r.no_text_moved == 0 and r.failed == ("bad.jpg",)

    def test_add_result_added(self) -> None:
        r = AddResult(entry_id=1, reason="added", text="猫")
        assert r.entry_id == 1 and r.reason == "added"
        assert r.replaced_image_path is None and r.moved_to is None

    def test_add_result_replaced(self) -> None:
        r = AddResult(entry_id=3, reason="replaced", replaced_image_path="old.jpg")
        assert r.entry_id == 3 and r.replaced_image_path == "old.jpg"

    def test_add_result_no_text(self) -> None:
        r = AddResult(entry_id=None, reason="no_text", moved_to="/x/blank.jpg")
        assert r.entry_id is None and r.moved_to == "/x/blank.jpg"


class TestExceptions:
    def test_index_corrupted(self) -> None:
        with pytest.raises(IndexCorruptedError):
            raise IndexCorruptedError("x")

    def test_compression_error(self) -> None:
        with pytest.raises(CompressionError):
            raise CompressionError("x")

    def test_ocr_error(self) -> None:
        with pytest.raises(OcrError):
            raise OcrError("x")

    def test_embedding_error(self) -> None:
        with pytest.raises(EmbeddingError):
            raise EmbeddingError("x")


class TestResolveUniqueFilename:
    def test_no_conflict(self, tmp_path: Path) -> None:
        p = resolve_unique_filename(tmp_path, "a.jpg")
        assert p == tmp_path / "a.jpg"

    def test_conflict_appends_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "a.jpg").write_text("x")
        p = resolve_unique_filename(tmp_path, "a.jpg")
        assert p == tmp_path / "a_1.jpg"


# ---------------------------------------------------------------------------
# load / entry_count
# ---------------------------------------------------------------------------


class TestLoadAndCount:
    def test_load_delegates_to_stores(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        m = IndexManager(
            metadata_store=cast(MetadataStore, md),
            vector_store=cast(VectorStore, vs),
            memes_dir=str(tmp_path),
        )
        asyncio.run(m.load())
        assert m.entry_count == 0

    def test_load_wraps_sqlite_database_error(self, tmp_path: Path) -> None:
        """sqlite 损坏时 load() 归并 DatabaseError 为 IndexCorruptedError（PRD 971 拒绝启动）。"""
        db_path = tmp_path / "data" / "index.db"
        db_path.parent.mkdir()
        db_path.write_bytes(b"definitely not a sqlite file" * 100)
        m = IndexManager(
            metadata_store=MetadataStore(str(db_path)),
            vector_store=cast(VectorStore, FakeVectorStore()),
            memes_dir=str(tmp_path / "memes"),
        )
        with pytest.raises(IndexCorruptedError):
            asyncio.run(m.load())

    def test_entry_count_reflects_store(self, index_manager: IndexManager) -> None:
        index_manager._metadata_store.add("a.jpg", "甲")  # type: ignore[attr-defined]
        assert index_manager.entry_count == 1


# ---------------------------------------------------------------------------
# 新并发 API 核心测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_returns_add_result(index_manager: IndexManager) -> None:
    (Path(index_manager._memes_dir) / "test.jpg").write_bytes(b"fake")
    result = await index_manager.add("test.jpg")
    assert result.reason == "added"
    assert result.entry_id is not None


@pytest.mark.asyncio
async def test_add_comma_joins_multi_token_ocr_text(
    index_manager: IndexManager,
) -> None:
    """image_pipeline 应将 OCR 多 token 文本按英文逗号拼接入库（非去空白）。"""

    class CommaOcrProvider:
        async def ocr(self, image_path: str) -> str:
            return "加 班\t心\n累"

        async def close(self) -> None:
            pass

    index_manager._ocr_provider = CommaOcrProvider()
    (Path(index_manager._memes_dir) / "comma.jpg").write_bytes(b"fake")

    result = await index_manager.add("comma.jpg")
    assert result.reason == "added"
    assert result.entry_id is not None
    # 按空白分割后以英文逗号拼接，而非旧版去除所有空白
    assert result.text == "加,班,心,累"


@pytest.mark.asyncio
async def test_add_fifo_order(index_manager: IndexManager) -> None:
    for i in range(3):
        (Path(index_manager._memes_dir) / f"img{i}.jpg").write_bytes(b"fake")
    results = await asyncio.gather(
        index_manager.add("img0.jpg"),
        index_manager.add("img1.jpg"),
        index_manager.add("img2.jpg"),
    )
    ids: list[int] = [r.entry_id for r in results if r.entry_id is not None]
    assert len(ids) == 3
    # 实际写入顺序应与提交顺序一致，验证 FIFO
    metadata_store = cast(FakeMetadataStore, index_manager._metadata_store)
    assert metadata_store.add_order == ids


@pytest.mark.asyncio
async def test_refresh_rejects_pending_add(index_manager: IndexManager) -> None:
    # 通过 monkeypatch _process_image_pipeline 使其挂住，模拟管道阻塞
    original = index_manager._process_image_pipeline
    started = asyncio.Event()

    async def slow_pipeline(filename: str) -> tuple[str, str, list[float]]:
        started.set()
        await asyncio.sleep(10)
        return await original(filename)

    index_manager._process_image_pipeline = slow_pipeline  # ty: ignore[invalid-assignment]

    (Path(index_manager._memes_dir) / "hold.jpg").write_bytes(b"fake")
    add_task = asyncio.create_task(index_manager.add("hold.jpg"))
    await started.wait()

    # 提交第二个 add——此时 refresh 尚未激活，也会进入 pipeline
    (Path(index_manager._memes_dir) / "drop.jpg").write_bytes(b"fake")
    pending_task = asyncio.create_task(index_manager.add("drop.jpg"))
    await asyncio.sleep(0.05)

    # 触发 refresh；新行为：add_task 和 pending_task 完成 pipeline 后，
    # TOCTOU 检查发现 _refresh_active → 均抛 RefreshInProgressError
    refresh_task = asyncio.create_task(index_manager.refresh())
    await asyncio.sleep(0.05)

    with pytest.raises(RefreshInProgressError):
        await add_task
    with pytest.raises(RefreshInProgressError):
        await pending_task
    await refresh_task


@pytest.mark.asyncio
async def test_refresh_rejects_new_add(index_manager: IndexManager) -> None:
    # 让 refresh 长期持有写锁
    original = index_manager._run_sync_internal

    async def slow_refresh() -> SyncResult:
        await asyncio.sleep(10)
        return await original()

    index_manager._run_sync_internal = slow_refresh  # ty: ignore[invalid-assignment]

    refresh_task = asyncio.create_task(index_manager.refresh())
    await asyncio.sleep(0.05)

    (Path(index_manager._memes_dir) / "blocked.jpg").write_bytes(b"fake")
    with pytest.raises(RefreshInProgressError):
        await index_manager.add("blocked.jpg")

    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_refresh_wraps_sqlite_database_error(
    index_manager: IndexManager,
) -> None:
    """refresh() 把同步期间的 sqlite3.DatabaseError 归并为 IndexCorruptedError（PRD 971 拒绝刷新）。"""

    async def corrupt_sync() -> SyncResult:
        raise sqlite3.DatabaseError("file is not a database")

    index_manager._run_sync_internal = corrupt_sync  # ty: ignore[invalid-assignment]

    with pytest.raises(IndexCorruptedError):
        await index_manager.refresh()


@pytest.mark.asyncio
async def test_search_holds_read_lock(index_manager: IndexManager) -> None:
    # 先 add 一条
    (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
    await index_manager.add("cat.jpg")

    results = await index_manager.search("猫")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_close_cancels_pending_add(index_manager: IndexManager) -> None:
    entered = asyncio.Event()

    original = index_manager._process_image_pipeline

    async def slow_pipeline(filename: str) -> tuple[str, str, list[float]]:
        entered.set()
        await asyncio.sleep(10)
        return await original(filename)

    index_manager._process_image_pipeline = slow_pipeline  # ty: ignore[invalid-assignment]

    (Path(index_manager._memes_dir) / "pending.jpg").write_bytes(b"fake")
    task = asyncio.create_task(index_manager.add("pending.jpg"))
    await entered.wait()

    await index_manager.close()

    with pytest.raises(IndexAddCancelledError):
        await task


@pytest.mark.asyncio
async def test_process_image_pipeline_empty_text(
    index_manager: IndexManager,
) -> None:
    """OCR 返回空串时 _process_image_pipeline 应短路返回 ('', []) 且不调用 embed。"""

    class EmptyOcrProvider:
        async def ocr(self, image_path: str) -> str:
            return ""

        async def close(self) -> None:
            pass

    class SpyEmbeddingProvider:
        async def embed(self, text: str) -> list[float]:
            raise AssertionError("空文本不应调用 embed")

        async def close(self) -> None:
            pass

    index_manager._ocr_provider = EmptyOcrProvider()
    index_manager._embedding_provider = SpyEmbeddingProvider()
    (Path(index_manager._memes_dir) / "blank.jpg").write_bytes(b"fake")

    final_filename, text, embedding = await index_manager._process_image_pipeline(
        "blank.jpg"
    )
    assert final_filename == "blank.jpg"
    assert text == ""
    assert embedding == []


@pytest.mark.asyncio
async def test_close_cancels_running_refresh(
    index_manager: IndexManager,
) -> None:
    """close() 应取消正在运行的 refresh task 并正常关闭 stores。"""
    entered = asyncio.Event()
    blocked = asyncio.Event()
    original = index_manager._run_sync_internal

    async def hanging_sync() -> SyncResult:
        entered.set()
        await blocked.wait()  # 永不完成，模拟 refresh 长期持锁
        return await original()

    index_manager._run_sync_internal = hanging_sync  # ty: ignore[invalid-assignment]

    (Path(index_manager._memes_dir) / "a.jpg").write_bytes(b"fake")
    task = asyncio.create_task(index_manager.refresh())
    await entered.wait()

    # F7: refresh 运行中应已记录自身 task，供 close() 取消
    assert index_manager._refresh_task is task

    await index_manager.close()

    with pytest.raises(asyncio.CancelledError):
        await task
    # finally 应清理 _refresh_task
    assert index_manager._refresh_task is None


# ---------------------------------------------------------------------------
# add 测试
# ---------------------------------------------------------------------------


class TestAdd:
    """IndexManager.add() 单元测试。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("relative_path", ["../outside.jpg", "/tmp/outside.jpg"])
    async def test_add_rejects_unsafe_relative_path_before_pipeline(
        self,
        index_manager: IndexManager,
        relative_path: str,
    ) -> None:
        """绝对路径和父目录逃逸必须在调用 provider 前拒绝。"""

        class FailIfCalledOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                raise AssertionError("非法路径不应调用 optimizer")

        outside = Path(index_manager._memes_dir).parent / "outside.jpg"
        outside.write_bytes(b"outside")
        index_manager._optimizer = cast(ImageOptimizer, FailIfCalledOptimizer())

        with pytest.raises(ValueError, match="relative_path"):
            await index_manager.add(relative_path, collection_id=99)

        assert outside.read_bytes() == b"outside"

    @pytest.mark.asyncio
    async def test_add_rejects_symlink_path_before_pipeline(
        self, index_manager: IndexManager, tmp_path: Path
    ) -> None:
        """指向 memes 外部的符号链接不得作为 add 输入。"""
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"outside")
        link = Path(index_manager._memes_dir) / "linked.jpg"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("当前平台不支持创建符号链接")

        with pytest.raises(ValueError, match="relative_path"):
            await index_manager.add("linked.jpg")

        assert outside.read_bytes() == b"outside"

    @pytest.mark.asyncio
    async def test_add_passes_speaker_and_tags(
        self, index_manager: IndexManager
    ) -> None:
        """/add 应把 speaker/tags 写入 sqlite。"""
        (Path(index_manager._memes_dir) / "text.jpg").write_bytes(b"fake")
        await index_manager.add("text.jpg", speaker="小明", tags=["吐槽"])
        metadata_store = cast(FakeMetadataStore, index_manager._metadata_store)
        entry = metadata_store.get_by_filename("text.jpg")
        assert entry is not None
        assert entry.speaker == "小明"
        assert entry.tags == ("吐槽",)

    @pytest.mark.asyncio
    async def test_add_returns_persisted_speaker_and_tags(
        self, index_manager: IndexManager
    ) -> None:
        """AddResult 应返回 MetadataStore 规范化后的持久快照。"""
        image_path = Path(index_manager._memes_dir) / "normalized.jpg"
        image_path.write_bytes(b"fake")

        result = await index_manager.add(
            "normalized.jpg",
            speaker="小明",
            tags=["乙", "甲", "乙"],
        )

        assert result.speaker == "小明"
        assert result.tags == tuple(sorted({"乙", "甲"}))

    @pytest.mark.asyncio
    async def test_add_duplicate_replaces_speaker_and_tags(
        self, index_manager: IndexManager
    ) -> None:
        """去重替换时应覆盖旧 speaker/tags。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        # 让两次 add 的 OCR 文本相同，触发去重替换
        index_manager._ocr_provider = ConstantOcrProvider()
        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "new.jpg").write_bytes(b"fake")
        await index_manager.add("old.jpg", speaker="旧说话人", tags=["旧标签"])
        result = await index_manager.add("new.jpg", speaker="新说话人", tags=["新标签"])
        entry = index_manager._metadata_store.get_entry(1)
        assert entry is not None
        assert entry.speaker == "新说话人"
        assert entry.tags == ("新标签",)

        # 验证旧图已被归档到 memes_replaced/
        replaced_dir = Path(index_manager._replaced_dir)
        assert (replaced_dir / "old.jpg").exists()
        # 验证新图仍保留在 memes/
        assert (Path(index_manager._memes_dir) / "new.jpg").exists()
        # 验证 AddResult 携带归档路径
        assert result.archived_path == str(replaced_dir / "old.jpg")

    @pytest.mark.asyncio
    async def test_add_duplicate_archives_old_image_with_unique_name(
        self, index_manager: IndexManager
    ) -> None:
        """多次替换同名旧图时，memes_replaced/ 中应生成 _1、_2 等不冲突文件名。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        replaced_dir = Path(index_manager._replaced_dir)

        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"1")
        await index_manager.add("old.jpg")

        # 第一次替换
        (Path(index_manager._memes_dir) / "new1.jpg").write_bytes(b"2")
        r1 = await index_manager.add("new1.jpg")
        assert r1.archived_path == str(replaced_dir / "old.jpg")

        # 第二次替换：再次把同名 old.jpg 移入 memes_replaced/
        # 通过手动调用 move_to_replaced 模拟同名冲突
        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"3")
        archived = await asyncio.to_thread(index_manager._coordinator.move_to_replaced, "old.jpg")
        assert archived == str(replaced_dir / "old_1.jpg")
        assert (replaced_dir / "old_1.jpg").exists()

    @pytest.mark.asyncio
    async def test_add_duplicate_upsert_failure_rolls_back_speaker_and_tags(
        self, index_manager: IndexManager
    ) -> None:
        """去重替换时 chroma upsert 失败，sqlite 应回滚到旧 speaker/tags。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "new.jpg").write_bytes(b"fake")

        # 第一次添加，建立旧 speaker/tags
        first = await index_manager.add("old.jpg", speaker="旧说话人", tags=["旧标签"])
        assert first.entry_id is not None
        old_id = first.entry_id

        # 让去重替换时的 upsert 失败
        vs = cast(FakeVectorStore, index_manager._vector_store)
        vs.upsert_error_for = {old_id}

        with pytest.raises(EmbeddingError, match="去重替换 upsert 失败"):
            await index_manager.add("new.jpg", speaker="新说话人", tags=["新标签"])

        # 验证 sqlite 已回滚到旧 speaker/tags
        entry = index_manager._metadata_store.get_entry(old_id)
        assert entry is not None
        assert entry.speaker == "旧说话人"
        assert entry.tags == ("旧标签",)

        # 验证旧图仍在 memes/（upsert 失败时不应移动旧图），新图应已被清理
        assert (Path(index_manager._memes_dir) / "old.jpg").exists()
        assert not (Path(index_manager._memes_dir) / "new.jpg").exists()

    @pytest.mark.asyncio
    async def test_add_duplicate_archive_failure_restores_old_state(
        self, index_manager: IndexManager
    ) -> None:
        """旧图归档失败时应恢复 SQLite、向量并清理新图。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        old_path = memes_dir / "old.jpg"
        new_path = memes_dir / "new.jpg"
        old_path.write_bytes(b"old")
        first = await index_manager.add("old.jpg")
        assert first.entry_id is not None
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        old_vector = list(vector_store._vecs[first.entry_id])
        new_path.write_bytes(b"new")
        index_manager._embedding_provider = FakeEmbeddingProvider([9.0] * 1024)

        def fail_archive(filename: str) -> str:
            raise OSError("archive failed")

        index_manager._coordinator.move_to_replaced = fail_archive  # ty: ignore[invalid-assignment]

        with pytest.raises(OSError, match="archive failed"):
            await index_manager.add("new.jpg")

        entry = index_manager._metadata_store.get_entry(first.entry_id)
        assert entry is not None
        assert entry.image_path == "old.jpg"
        assert vector_store._vecs[first.entry_id] == old_vector
        assert old_path.read_bytes() == b"old"
        assert not new_path.exists()

    @pytest.mark.asyncio
    async def test_add_duplicate_upsert_commit_then_error_restores_old_vector(
        self, index_manager: IndexManager
    ) -> None:
        """替换 upsert 已落库后抛错时应恢复旧向量快照。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "old.jpg").write_bytes(b"old")
        first = await index_manager.add("old.jpg")
        assert first.entry_id is not None
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        old_vector = list(vector_store._vecs[first.entry_id])
        (memes_dir / "new.jpg").write_bytes(b"new")
        index_manager._embedding_provider = FakeEmbeddingProvider([9.0] * 1024)
        vector_store.upsert_write_then_error_for = {first.entry_id}

        with pytest.raises(EmbeddingError):
            await index_manager.add("new.jpg")

        assert vector_store._vecs[first.entry_id] == old_vector
        entry = index_manager._metadata_store.get_entry(first.entry_id)
        assert entry is not None
        assert entry.image_path == "old.jpg"

    @pytest.mark.asyncio
    async def test_add_uses_global_collection_by_default(
        self, index_manager: IndexManager
    ) -> None:
        """未指定目标合集时写入全局，并返回公开 ID 与合集名称。"""
        image_path = Path(index_manager._memes_dir) / "global.jpg"
        image_path.write_bytes(b"fake")

        result = await index_manager.add("global.jpg")

        assert result.public_id == MemePublicId(0, 1)
        assert result.collection_name == GLOBAL_COLLECTION_NAME
        entry = index_manager._metadata_store.get_entry(result.entry_id or 0)
        assert entry is not None
        assert entry.collection_id == 0
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        assert vector_store._collection_ids[entry.id] == 0

    @pytest.mark.asyncio
    async def test_add_assigns_public_id_in_target_collection(
        self, index_manager: IndexManager
    ) -> None:
        """普通合集新增写入 SQLite 与 Chroma，并返回持久条目的公开字段。"""
        collection = index_manager._metadata_store.create_collection("新三国")
        image_path = Path(index_manager._memes_dir) / "新三国" / "a.webp"
        image_path.parent.mkdir()
        image_path.write_bytes(b"image")

        result = await index_manager.add(
            "新三国/a.webp",
            collection_id=collection.id,
        )

        assert result.public_id == MemePublicId(collection.id, 1)
        assert result.collection_name == "新三国"
        assert result.entry_id is not None
        entry = index_manager._metadata_store.get_entry(result.entry_id)
        assert entry is not None
        assert entry.collection_id == collection.id
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        assert vector_store._collection_ids[entry.id] == collection.id

    @pytest.mark.asyncio
    async def test_add_allows_same_text_in_different_collections(
        self, index_manager: IndexManager
    ) -> None:
        """跨合集相同 OCR 文本应分别入库。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        first = index_manager._metadata_store.create_collection("新三国")
        second = index_manager._metadata_store.create_collection("甄嬛传")
        for collection, filename in ((first, "a.webp"), (second, "b.webp")):
            path = Path(index_manager._memes_dir) / collection.name / filename
            path.parent.mkdir()
            path.write_bytes(collection.name.encode())

        first_result = await index_manager.add("新三国/a.webp", collection_id=first.id)
        second_result = await index_manager.add(
            "甄嬛传/b.webp", collection_id=second.id
        )

        assert first_result.entry_id != second_result.entry_id
        assert first_result.public_id == MemePublicId(first.id, 1)
        assert second_result.public_id == MemePublicId(second.id, 1)

    @pytest.mark.asyncio
    async def test_add_duplicate_in_collection_preserves_public_id(
        self, index_manager: IndexManager
    ) -> None:
        """同合集替换复用内部 ID 和合集内编号。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        collection = index_manager._metadata_store.create_collection("新三国")
        directory = Path(index_manager._memes_dir) / collection.name
        directory.mkdir()
        (directory / "old.webp").write_bytes(b"old")
        first = await index_manager.add("新三国/old.webp", collection_id=collection.id)
        (directory / "new.webp").write_bytes(b"new")

        replaced = await index_manager.add(
            "新三国/new.webp", collection_id=collection.id
        )

        assert replaced.reason == "replaced"
        assert replaced.entry_id == first.entry_id
        assert replaced.public_id == first.public_id == MemePublicId(collection.id, 1)
        assert replaced.collection_name == collection.name

    @pytest.mark.asyncio
    async def test_add_rejects_collection_deleted_during_pipeline(
        self, index_manager: IndexManager
    ) -> None:
        """管线处理中合集被删除时，清理转换后的实际文件且不误删其他文件。"""
        pipeline_started = asyncio.Event()
        resume_pipeline = asyncio.Event()

        class ConvertingOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                target = source.with_suffix(".webp")
                target.write_bytes(source.read_bytes())
                source.unlink()
                return OptimizeResult(4, 3, 1, output_path=str(target))

        class BlockingOcrProvider:
            async def ocr(self, image_path: str) -> str:
                pipeline_started.set()
                await resume_pipeline.wait()
                return "并发删除"

            async def close(self) -> None:
                pass

        index_manager._optimizer = cast(ImageOptimizer, ConvertingOptimizer())
        index_manager._ocr_provider = BlockingOcrProvider()
        collection = index_manager._metadata_store.create_collection("新三国")
        directory = Path(index_manager._memes_dir) / collection.name
        directory.mkdir()
        source = directory / "a.jpg"
        final_path = directory / "a.webp"
        unrelated = directory / "keep.webp"
        source.write_bytes(b"image")
        unrelated.write_bytes(b"keep")

        add_task = asyncio.create_task(
            index_manager.add("新三国/a.jpg", collection_id=collection.id)
        )
        await pipeline_started.wait()
        metadata_store = cast(FakeMetadataStore, index_manager._metadata_store)
        metadata_store.delete_collection_and_reset_scopes(collection.id)
        resume_pipeline.set()

        with pytest.raises(CollectionNotFoundError):
            await add_task
        assert not source.exists()
        assert not final_path.exists()
        assert unrelated.read_bytes() == b"keep"
        assert index_manager._metadata_store.entry_count() == 0

    @pytest.mark.asyncio
    async def test_add_serializes_optimizer_with_refresh_for_same_target(
        self, index_manager: IndexManager
    ) -> None:
        """add 与 refresh 的同父目录同 stem 优化必须共享目标锁。"""
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        class BlockingOptimizer:
            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0
                self.calls = 0

            async def optimize(self, image_path: str) -> OptimizeResult:
                self.calls += 1
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.calls == 1:
                    first_entered.set()
                    await release_first.wait()
                self.active -= 1
                return OptimizeResult(4, 4, 0, output_path=image_path)

        optimizer = BlockingOptimizer()
        index_manager._optimizer = cast(ImageOptimizer, optimizer)
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "same.jpg").write_bytes(b"first")
        first = asyncio.create_task(index_manager._process_image_pipeline("same.jpg"))
        await first_entered.wait()
        (memes_dir / "same.png").write_bytes(b"second")
        second = asyncio.create_task(index_manager._process_image_pipeline("same.png"))
        await asyncio.sleep(0.05)

        assert optimizer.calls == 1
        release_first.set()
        await asyncio.gather(first, second)
        assert optimizer.max_active == 1

    @pytest.mark.asyncio
    async def test_optimizer_lock_registry_releases_unique_keys(
        self, index_manager: IndexManager
    ) -> None:
        """大量顺序唯一 key 完成后目标锁注册表应回到空状态。"""
        memes_dir = Path(index_manager._memes_dir)
        for index in range(50):
            filename = f"unique-{index}.jpg"
            (memes_dir / filename).write_bytes(b"image")
            await index_manager._process_image_pipeline(filename)

        assert index_manager._optimizer_target_locks == {}

    @pytest.mark.asyncio
    async def test_optimizer_lock_registry_cleans_cancelled_waiter(
        self, index_manager: IndexManager
    ) -> None:
        """同 key waiter 取消后引用计数与注册表必须完整清理。"""
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        class BlockingOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                first_entered.set()
                await release_first.wait()
                return OptimizeResult(4, 4, 0, output_path=image_path)

        index_manager._optimizer = cast(ImageOptimizer, BlockingOptimizer())
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "wait.jpg").write_bytes(b"first")
        (memes_dir / "wait.png").write_bytes(b"second")
        first = asyncio.create_task(index_manager._process_image_pipeline("wait.jpg"))
        await first_entered.wait()
        waiter = asyncio.create_task(index_manager._process_image_pipeline("wait.png"))
        await asyncio.sleep(0.05)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release_first.set()
        await first

        assert index_manager._optimizer_target_locks == {}

    @pytest.mark.asyncio
    async def test_optimizer_context_exit_cancel_cleans_registry_and_output(
        self, index_manager: IndexManager
    ) -> None:
        """optimizer 返回后退出上下文被取消仍应清理引用与任务输出。"""
        optimizer_ready = asyncio.Event()
        allow_optimizer_return = asyncio.Event()

        class CreatingOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                target = source.with_suffix(".webp")
                target.write_bytes(source.read_bytes())
                source.unlink()
                optimizer_ready.set()
                await allow_optimizer_return.wait()
                return OptimizeResult(4, 3, 1, output_path=str(target))

        index_manager._optimizer = cast(ImageOptimizer, CreatingOptimizer())
        source = Path(index_manager._memes_dir) / "exit-cancel.jpg"
        final_path = source.with_suffix(".webp")
        source.write_bytes(b"image")
        task = asyncio.create_task(index_manager._process_image_pipeline(source.name))
        await optimizer_ready.wait()
        await index_manager._optimizer_registry_guard.acquire()
        allow_optimizer_return.set()
        await asyncio.sleep(0)
        task.cancel()
        index_manager._optimizer_registry_guard.release()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert not final_path.exists()
        assert index_manager._optimizer_target_locks == {}

    @pytest.mark.asyncio
    async def test_cancelled_optimizer_stays_locked_until_background_finishes(
        self, index_manager: IndexManager
    ) -> None:
        """外部取消后仍等待 optimizer 真正结束，并阻止同目标任务进入。"""
        first_entered = asyncio.Event()
        release_first = threading.Event()
        second_entered = asyncio.Event()
        calls = 0

        class ThreadStyleOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                nonlocal calls
                calls += 1
                if calls == 1:
                    first_entered.set()
                    await asyncio.to_thread(release_first.wait)
                    source = Path(image_path)
                    target = source.with_suffix(".webp")
                    target.write_bytes(source.read_bytes())
                    source.unlink()
                    return OptimizeResult(4, 3, 1, output_path=str(target))
                second_entered.set()
                return OptimizeResult(4, 4, 0, output_path=image_path)

        index_manager._optimizer = cast(ImageOptimizer, ThreadStyleOptimizer())
        memes_dir = Path(index_manager._memes_dir)
        first_source = memes_dir / "thread.jpg"
        second_source = memes_dir / "thread.png"
        first_output = first_source.with_suffix(".webp")
        first_source.write_bytes(b"first")
        second_source.write_bytes(b"second")
        first = asyncio.create_task(
            index_manager._process_image_pipeline(first_source.name)
        )
        await first_entered.wait()
        first.cancel()
        second = asyncio.create_task(
            index_manager._process_image_pipeline(second_source.name)
        )
        await asyncio.sleep(0.05)

        assert not first.done()
        assert not second_entered.is_set()
        first.cancel()
        await asyncio.sleep(0.05)
        assert not first.done()
        assert not second_entered.is_set()
        first.cancel()
        await asyncio.sleep(0.05)
        assert not first.done()
        assert not second_entered.is_set()
        release_first.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert not first_output.exists()
        assert index_manager._optimizer_target_locks == {}

    @pytest.mark.asyncio
    async def test_cancelled_optimizer_consumes_background_exception(
        self, index_manager: IndexManager
    ) -> None:
        """外部取消优先传播，并消费 optimizer 随后产生的异常。"""
        optimizer_entered = asyncio.Event()
        release_optimizer = threading.Event()

        class FailingThreadStyleOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                optimizer_entered.set()
                await asyncio.to_thread(release_optimizer.wait)
                raise RuntimeError("background failure")

        index_manager._optimizer = cast(ImageOptimizer, FailingThreadStyleOptimizer())
        source = Path(index_manager._memes_dir) / "background.jpg"
        source.write_bytes(b"image")
        task = asyncio.create_task(index_manager._process_image_pipeline(source.name))
        await optimizer_entered.wait()
        task.cancel()
        release_optimizer.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert index_manager._optimizer_target_locks == {}

    @pytest.mark.asyncio
    async def test_pipeline_failure_does_not_delete_other_task_output(
        self, index_manager: IndexManager
    ) -> None:
        """等待目标锁的任务失败时不得删除前一任务创建的输出。"""
        first_optimizer_entered = asyncio.Event()
        allow_first_create = asyncio.Event()
        first_ocr_entered = asyncio.Event()
        allow_first_ocr = asyncio.Event()

        class SharedOutputOptimizer:
            def __init__(self) -> None:
                self.calls = 0

            async def optimize(self, image_path: str) -> OptimizeResult:
                self.calls += 1
                target = Path(image_path).with_name("same.webp")
                if self.calls == 1:
                    first_optimizer_entered.set()
                    await allow_first_create.wait()
                    target.write_bytes(b"first-output")
                return OptimizeResult(4, 3, 1, output_path=str(target))

        class SecondFailingOcrProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def ocr(self, image_path: str) -> str:
                self.calls += 1
                if self.calls == 1:
                    first_ocr_entered.set()
                    await allow_first_ocr.wait()
                    return "第一张"
                raise RuntimeError("second failed")

            async def close(self) -> None:
                pass

        index_manager._optimizer = cast(ImageOptimizer, SharedOutputOptimizer())
        index_manager._ocr_provider = SecondFailingOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "same.jpg").write_bytes(b"first")
        (memes_dir / "same.png").write_bytes(b"second")
        first = asyncio.create_task(index_manager._process_image_pipeline("same.jpg"))
        await first_optimizer_entered.wait()
        second = asyncio.create_task(index_manager._process_image_pipeline("same.png"))
        await asyncio.sleep(0.05)
        allow_first_create.set()
        await first_ocr_entered.wait()

        with pytest.raises(OcrError):
            await second
        assert (memes_dir / "same.webp").read_bytes() == b"first-output"
        allow_first_ocr.set()
        await first

    @pytest.mark.asyncio
    async def test_add_cleans_created_optimizer_output_when_ocr_fails(
        self, index_manager: IndexManager
    ) -> None:
        """转换新建的最终文件在 OCR 失败后应清理。"""

        class ConvertingOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                target = source.with_suffix(".webp")
                target.write_bytes(source.read_bytes())
                source.unlink()
                return OptimizeResult(4, 3, 1, output_path=str(target))

        class FailingOcrProvider:
            async def ocr(self, image_path: str) -> str:
                raise RuntimeError("ocr failed")

            async def close(self) -> None:
                pass

        index_manager._optimizer = cast(ImageOptimizer, ConvertingOptimizer())
        index_manager._ocr_provider = FailingOcrProvider()
        source = Path(index_manager._memes_dir) / "failed.jpg"
        final_path = source.with_suffix(".webp")
        source.write_bytes(b"image")

        with pytest.raises(OcrError):
            await index_manager.add("failed.jpg")

        assert not source.exists()
        assert not final_path.exists()

    @pytest.mark.asyncio
    async def test_add_cleans_created_optimizer_output_when_ocr_cancelled(
        self, index_manager: IndexManager
    ) -> None:
        """转换后取消 OCR 时应清理本任务新建输出并原样传播取消。"""
        ocr_entered = asyncio.Event()

        class ConvertingOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                target = source.with_suffix(".webp")
                target.write_bytes(source.read_bytes())
                source.unlink()
                return OptimizeResult(4, 3, 1, output_path=str(target))

        class BlockingOcrProvider:
            async def ocr(self, image_path: str) -> str:
                ocr_entered.set()
                await asyncio.Event().wait()
                raise AssertionError("不可达")

            async def close(self) -> None:
                pass

        index_manager._optimizer = cast(ImageOptimizer, ConvertingOptimizer())
        index_manager._ocr_provider = BlockingOcrProvider()
        source = Path(index_manager._memes_dir) / "cancelled.jpg"
        final_path = source.with_suffix(".webp")
        source.write_bytes(b"image")
        task = asyncio.create_task(index_manager.add("cancelled.jpg"))
        await ocr_entered.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not source.exists()
        assert not final_path.exists()

    @pytest.mark.asyncio
    async def test_add_preserves_preexisting_output_when_ocr_cancelled(
        self, index_manager: IndexManager
    ) -> None:
        """取消 OCR 时不得删除 optimizer 返回的预存输出。"""
        ocr_entered = asyncio.Event()

        class ExistingOutputOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                return OptimizeResult(4, 4, 0, output_path=image_path)

        class BlockingOcrProvider:
            async def ocr(self, image_path: str) -> str:
                ocr_entered.set()
                await asyncio.Event().wait()
                raise AssertionError("不可达")

            async def close(self) -> None:
                pass

        index_manager._optimizer = cast(ImageOptimizer, ExistingOutputOptimizer())
        index_manager._ocr_provider = BlockingOcrProvider()
        source = Path(index_manager._memes_dir) / "existing-cancelled.jpg"
        source.write_bytes(b"original")
        task = asyncio.create_task(index_manager.add(source.name))
        await ocr_entered.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert source.read_bytes() == b"original"

    @pytest.mark.asyncio
    async def test_add_preserves_preexisting_optimizer_output_when_ocr_fails(
        self, index_manager: IndexManager
    ) -> None:
        """optimizer 返回预存文件时，pipeline 失败不得误删该文件。"""

        class ExistingOutputOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                return OptimizeResult(4, 4, 0, output_path=image_path)

        class FailingOcrProvider:
            async def ocr(self, image_path: str) -> str:
                raise RuntimeError("ocr failed")

            async def close(self) -> None:
                pass

        index_manager._optimizer = cast(ImageOptimizer, ExistingOutputOptimizer())
        index_manager._ocr_provider = FailingOcrProvider()
        source = Path(index_manager._memes_dir) / "existing.jpg"
        source.write_bytes(b"original")

        with pytest.raises(OcrError):
            await index_manager.add("existing.jpg")

        assert source.read_bytes() == b"original"

    @pytest.mark.asyncio
    async def test_add_no_text_moves_file(self, index_manager: IndexManager) -> None:
        """无文字图片 add() 应移入 meme_no_text/ 并返回 reason=no_text。"""

        class EmptyOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return ""

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = EmptyOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        no_text_dir = Path(index_manager._no_text_dir)
        (memes_dir / "blank.jpg").write_bytes(b"fake")

        result = await index_manager.add("blank.jpg")
        assert result.reason == "no_text"
        assert result.public_id is None
        assert result.collection_name is None
        assert result.moved_to is not None
        assert not (memes_dir / "blank.jpg").exists()
        assert Path(result.moved_to).exists()
        assert Path(result.moved_to).parent == no_text_dir

        await index_manager.close()


# ---------------------------------------------------------------------------
# edit_text 测试
# ---------------------------------------------------------------------------


class TestEditText:
    """IndexManager.edit_text() 单元测试。"""

    @pytest.mark.asyncio
    async def test_edit_text_normal(self, index_manager: IndexManager) -> None:
        """正常修改：sqlite text 更新、chroma upsert 调用。"""
        # 先 add 一条
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("cat.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        result = await index_manager.edit_text(eid, "加班到崩溃")
        assert result.entry_id == eid
        assert result.old_text == "cat"
        assert result.new_text == "加班到崩溃"

        # 验证 sqlite 已更新
        entry = index_manager._metadata_store.get_entry(eid)
        assert entry is not None
        assert entry.text == "加班到崩溃"

        # 验证 chroma 已 upsert
        vs = cast(FakeVectorStore, index_manager._vector_store)
        assert vs.has(eid)

    @pytest.mark.asyncio
    async def test_edit_text_same_text(self, index_manager: IndexManager) -> None:
        """新文本与当前文本相同 → 直接返回，无写入。"""
        (Path(index_manager._memes_dir) / "dog.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("dog.jpg")
        eid = add_result.entry_id
        assert eid is not None

        result = await index_manager.edit_text(eid, "dog")
        assert result.entry_id == eid
        assert result.old_text == "dog"
        assert result.new_text == "dog"

    @pytest.mark.asyncio
    async def test_edit_text_entry_not_found(self, index_manager: IndexManager) -> None:
        """entry_id 不存在 → ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await index_manager.edit_text(999, "新文本")

    @pytest.mark.asyncio
    async def test_edit_text_duplicate_text(self, index_manager: IndexManager) -> None:
        """text 被其他条目使用 → DuplicateTextError。"""
        (Path(index_manager._memes_dir) / "a.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "b.jpg").write_bytes(b"fake")
        r1 = await index_manager.add("a.jpg")
        r2 = await index_manager.add("b.jpg")
        assert r1.entry_id is not None and r2.entry_id is not None

        with pytest.raises(DuplicateTextError, match="已被 entry_id="):
            await index_manager.edit_text(r2.entry_id, "a")  # "a" 是 a.jpg 的 OCR 文本

    @pytest.mark.asyncio
    async def test_edit_text_refresh_active(self, index_manager: IndexManager) -> None:
        """refresh 进行中 → RefreshInProgressError。"""
        index_manager._refresh_active = True

        with pytest.raises(RefreshInProgressError):
            await index_manager.edit_text(1, "新文本")

    @pytest.mark.asyncio
    async def test_edit_text_upsert_failure(self, index_manager: IndexManager) -> None:
        """chroma upsert 失败 → sqlite 回滚到旧 text。"""
        (Path(index_manager._memes_dir) / "rollback.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("rollback.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        # 让 FakeVectorStore 对 eid 的 upsert 抛错
        vs = cast(FakeVectorStore, index_manager._vector_store)
        vs.upsert_error_for = {eid}

        with pytest.raises(EmbeddingError, match="回滚"):
            await index_manager.edit_text(eid, "加班到崩溃")

        # 验证 sqlite 已回滚到旧文本
        entry = index_manager._metadata_store.get_entry(eid)
        assert entry is not None
        assert entry.text == "rollback"  # MockOcrProvider 返回文件名(不含扩展名)

    @pytest.mark.asyncio
    async def test_edit_text_toctou_after_embed(
        self, index_manager: IndexManager
    ) -> None:
        """embed 期间 refresh 激活 → put 前拒绝。"""
        (Path(index_manager._memes_dir) / "toctou.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("toctou.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        assert index_manager._embedding_provider is not None
        original_embed = index_manager._embedding_provider.embed
        assert original_embed is not None

        async def embed_and_activate(text: str) -> list[float]:
            result = await original_embed(text)
            index_manager._refresh_active = (
                True  # 在 embed 返回后、TOCTOU 检查前激活 refresh
            )
            return result

        index_manager._embedding_provider.embed = embed_and_activate  # type: ignore[method-assign, ty:invalid-assignment]

        with pytest.raises(RefreshInProgressError):
            await index_manager.edit_text(eid, "加班")

    @pytest.mark.asyncio
    async def test_edit_text_shutting_down(self, index_manager: IndexManager) -> None:
        """shutting_down → IndexAddCancelledError，两次检查均生效。"""
        (Path(index_manager._memes_dir) / "shut.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("shut.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        # 第一次检查（entry 前）
        index_manager._shutting_down = True
        with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
            await index_manager.edit_text(eid, "新文本")

        # 第二次检查（embed 后）
        index_manager._shutting_down = False
        index_manager._refresh_active = False
        assert index_manager._embedding_provider is not None
        original_embed = index_manager._embedding_provider.embed
        assert original_embed is not None

        async def embed_and_shutdown(text: str) -> list[float]:
            result = await original_embed(text)
            index_manager._shutting_down = True  # 在 embed 返回后关闭
            return result

        index_manager._embedding_provider.embed = embed_and_shutdown  # type: ignore[method-assign, ty:invalid-assignment]

        with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
            await index_manager.edit_text(eid, "新文本")


# ---------------------------------------------------------------------------
# IndexManager.set_speaker()
# ---------------------------------------------------------------------------


class TestSetSpeaker:
    """IndexManager.set_speaker() 单元测试。"""

    @pytest.mark.asyncio
    async def test_set_speaker_normal(self, index_manager: IndexManager) -> None:
        """正常设置 speaker。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("cat.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        result = await index_manager.set_speaker(eid, "张三")
        assert result.entry_id == eid
        assert result.old_speaker is None
        assert result.new_speaker == "张三"

        # 验证 sqlite 已更新
        entry = index_manager._metadata_store.get_entry(eid)
        assert entry is not None
        assert entry.speaker == "张三"

    @pytest.mark.asyncio
    async def test_set_speaker_clear(self, index_manager: IndexManager) -> None:
        """清空 speaker（设为 None）。"""
        (Path(index_manager._memes_dir) / "dog.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("dog.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        # 先设置
        await index_manager.set_speaker(eid, "李四")
        # 再清空
        result = await index_manager.set_speaker(eid, None)
        assert result.entry_id == eid
        assert result.old_speaker == "李四"
        assert result.new_speaker is None

        entry = index_manager._metadata_store.get_entry(eid)
        assert entry is not None
        assert entry.speaker is None

    @pytest.mark.asyncio
    async def test_set_speaker_no_change(self, index_manager: IndexManager) -> None:
        """speaker 无变化 → 直接返回，不进队列。"""
        (Path(index_manager._memes_dir) / "nochange.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("nochange.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        result = await index_manager.set_speaker(eid, "王五")
        assert result.new_speaker == "王五"

        # 再次设置相同值
        result2 = await index_manager.set_speaker(eid, "王五")
        assert result2.entry_id == eid
        assert result2.old_speaker == "王五"
        assert result2.new_speaker == "王五"

    @pytest.mark.asyncio
    async def test_set_speaker_entry_not_found(
        self, index_manager: IndexManager
    ) -> None:
        """entry_id 不存在 → ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await index_manager.set_speaker(999, "张三")

    @pytest.mark.asyncio
    async def test_set_speaker_refresh_active(
        self, index_manager: IndexManager
    ) -> None:
        """refresh 进行中 → RefreshInProgressError。"""
        index_manager._refresh_active = True
        with pytest.raises(RefreshInProgressError):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.asyncio
    async def test_set_speaker_shutting_down(self, index_manager: IndexManager) -> None:
        """shutting_down → IndexAddCancelledError。"""
        index_manager._shutting_down = True
        with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.asyncio
    async def test_set_speaker_entry_deleted_concurrently(
        self, index_manager: IndexManager
    ) -> None:
        """_execute_set_speaker 内 entry 被并发删除 → ValueError。"""
        (Path(index_manager._memes_dir) / "race.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("race.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        # 启用手动模式：让 _execute_set_speaker 的 TOCTOU 检查命中不存在
        store = index_manager._metadata_store
        original_get_entry = store.get_entry

        def get_entry_and_delete(eid2: int) -> MemeEntry | None:
            entry = original_get_entry(eid2)
            if entry is not None and eid2 == eid:
                store.remove(eid2)  # 在 TOCTOU 窗口内删除
            return entry

        store.get_entry = get_entry_and_delete  # type: ignore[method-assign, ty:invalid-assignment]

        with pytest.raises(ValueError, match="不存在"):
            await index_manager.set_speaker(eid, "张三")


# ---------------------------------------------------------------------------
# 并发控制与 Write Queue drain 行为测试
# ---------------------------------------------------------------------------


class TestConcurrencyAndDrain:
    """IndexManager 并发控制与 Write Queue drain 行为测试。"""

    @pytest.mark.asyncio
    async def test_add_direct_pipeline(self, index_manager: IndexManager) -> None:
        """add() 直接调用 _process_image_pipeline（mock 验证调用一次）。"""
        call_count = 0
        original = index_manager._process_image_pipeline

        async def counting_pipeline(filename: str) -> tuple[str, str, list[float]]:
            nonlocal call_count
            call_count += 1
            return await original(filename)

        index_manager._process_image_pipeline = counting_pipeline  # ty: ignore[invalid-assignment]

        (Path(index_manager._memes_dir) / "test.jpg").write_bytes(b"fake")
        result = await index_manager.add("test.jpg")
        assert result.entry_id is not None
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_refresh_drains_write_queue(
        self, index_manager: IndexManager
    ) -> None:
        """refresh 等待 write_queue 排空后才获取写锁。"""
        original_write = index_manager._write_entry
        in_flight = asyncio.Event()

        async def slow_write(
            filename: str,
            text: str,
            embedding: list[float],
            speaker: str | None = None,
            tags: list[str] | None = None,
            *,
            collection_id: int = 0,
        ) -> AddResult:
            in_flight.set()
            await asyncio.sleep(0.3)
            return await original_write(
                filename,
                text,
                embedding,
                speaker,
                tags,
                collection_id=collection_id,
            )

        index_manager._write_entry = slow_write  # ty: ignore[invalid-assignment]

        (Path(index_manager._memes_dir) / "a.jpg").write_bytes(b"fake")
        _ = asyncio.create_task(index_manager.add("a.jpg"))
        await in_flight.wait()

        # Worker 正在处理 a.jpg，此时入队 b.jpg
        (Path(index_manager._memes_dir) / "b.jpg").write_bytes(b"fake")
        _ = asyncio.create_task(index_manager.add("b.jpg"))
        await asyncio.sleep(0.02)

        # refresh 应观察到非空队列，等待 drain
        refresh_task = asyncio.create_task(index_manager.refresh())
        await asyncio.sleep(0.05)
        assert not refresh_task.done(), "refresh 应在等待 write_queue drain"

        await asyncio.wait_for(refresh_task, timeout=5.0)

    @pytest.mark.asyncio
    async def test_write_queue_empty_no_wait(self, index_manager: IndexManager) -> None:
        """write_queue 为空时 refresh 不等待 drain。"""
        assert index_manager._write_queue.empty()
        result = await index_manager.refresh()
        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    async def test_write_worker_drain_signal(self, index_manager: IndexManager) -> None:
        """Write Worker 处理完最后一条后 _write_drained.set()。"""
        index_manager._write_drained.clear()
        (Path(index_manager._memes_dir) / "drain.jpg").write_bytes(b"fake")
        await index_manager.add("drain.jpg")
        await asyncio.sleep(0.02)
        assert index_manager._write_drained.is_set()

    @pytest.mark.asyncio
    async def test_no_concurrency_params_in_init(self) -> None:
        """IndexManager 不再接受 sync_concurrency 参数。"""
        from bot.index_manager import IndexManager

        md = FakeMetadataStore()
        vs = FakeVectorStore()
        m = IndexManager(
            metadata_store=cast(MetadataStore, md),
            vector_store=cast(VectorStore, vs),
            memes_dir="/tmp/memes",
        )
        assert not hasattr(m, "_add_concurrency")
        assert not hasattr(m, "_sync_semaphore")

    @pytest.mark.asyncio
    async def test_deleted_attrs_not_present(self, index_manager: IndexManager) -> None:
        """验证已删除属性不存在（_add_concurrency, _sync_semaphore 等）。"""
        assert not hasattr(index_manager, "_add_concurrency")
        assert not hasattr(index_manager, "_sync_semaphore")


class TestRefresh:
    """IndexManager.refresh() 去重归档测试。"""

    def test_scan_assigns_nested_files_to_first_directory(
        self, index_manager: IndexManager
    ) -> None:
        """一级目录定义合集，深层图片仍归属该一级目录。"""
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "root.webp").write_bytes(b"root")
        nested = memes_dir / "新三国" / "截图"
        nested.mkdir(parents=True)
        (nested / "a.webp").write_bytes(b"nested")
        hidden = memes_dir / "新三国" / ".cache"
        hidden.mkdir()
        (hidden / "ignored.webp").write_bytes(b"hidden")

        snapshot = index_manager._scan_meme_files()

        assert snapshot.files == {
            "root.webp": None,
            "新三国/截图/a.webp": "新三国",
        }
        assert snapshot.directories == {"新三国"}
        assert snapshot.directories_with_images == {"新三国"}

    def test_scan_skips_hidden_image_files(self, index_manager: IndexManager) -> None:
        """根目录及合集目录中的隐藏图片文件均不参与扫描。"""
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / ".root.webp").write_bytes(b"hidden")
        collection_dir = memes_dir / "新三国"
        collection_dir.mkdir()
        (collection_dir / ".hidden.webp").write_bytes(b"hidden")

        snapshot = index_manager._scan_meme_files()

        assert snapshot.files == {}
        assert snapshot.directories == {"新三国"}
        assert snapshot.directories_with_images == set()

    def test_scan_skips_hidden_root_directory(
        self, index_manager: IndexManager
    ) -> None:
        """隐藏一级目录及其整棵子树不参与扫描。"""
        hidden = Path(index_manager._memes_dir) / ".hidden"
        hidden.mkdir()
        (hidden / "ignored.webp").write_bytes(b"hidden")

        snapshot = index_manager._scan_meme_files()

        assert snapshot.files == {}
        assert snapshot.directories == set()

    def test_scan_skips_symlinked_files_and_directories(
        self, index_manager: IndexManager, tmp_path: Path
    ) -> None:
        """扫描不跟随文件或目录符号链接。"""
        memes_dir = Path(index_manager._memes_dir)
        outside_file = tmp_path / "outside.webp"
        outside_file.write_bytes(b"outside")
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "nested.webp").write_bytes(b"outside")
        try:
            (memes_dir / "linked.webp").symlink_to(outside_file)
            (memes_dir / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
        except OSError:
            pytest.skip("当前平台不支持创建符号链接")

        snapshot = index_manager._scan_meme_files()

        assert snapshot.files == {}
        assert snapshot.directories == set()

    @pytest.mark.asyncio
    async def test_refresh_creates_collection_only_for_directory_with_image(
        self, index_manager: IndexManager
    ) -> None:
        """仅递归含受支持图片的新一级目录登记为合集。"""
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "空目录").mkdir()
        with_image = memes_dir / "新三国"
        with_image.mkdir()
        (with_image / "a.webp").write_bytes(b"image")

        result = await index_manager.refresh()

        assert result.collections_added == 1
        assert index_manager._metadata_store.get_collection_by_name("空目录") is None
        collection = index_manager._metadata_store.get_collection_by_name("新三国")
        assert collection is not None
        assert collection.id == 1

    @pytest.mark.asyncio
    async def test_refresh_preserves_registered_collection_when_empty(
        self, index_manager: IndexManager
    ) -> None:
        """已登记合集变为空目录时，只要一级目录存在就保留。"""
        memes_dir = Path(index_manager._memes_dir)
        (memes_dir / "新三国").mkdir()
        collection = index_manager._metadata_store.create_collection("新三国")

        result = await index_manager.refresh()

        assert result.collections_deleted == 0
        assert index_manager._metadata_store.get_collection(collection.id) == collection

    @pytest.mark.asyncio
    async def test_refresh_deletes_missing_collection_and_resets_scopes(
        self, index_manager: IndexManager
    ) -> None:
        """一级目录消失后删除空合集，并把引用它的窗口回退到全部合集。"""
        collection = index_manager._metadata_store.create_collection("新三国")
        index_manager._metadata_store.set_selected_collection(
            ChatScope(1, "private", 1), collection.id
        )

        result = await index_manager.refresh()

        assert result.collections_deleted == 1
        assert result.scopes_reset == 1
        assert index_manager._metadata_store.get_collection(collection.id) is None
        assert (
            index_manager._metadata_store.get_selected_collection(
                ChatScope(1, "private", 1)
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_refresh_adds_nested_entry_with_collection_metadata(
        self, index_manager: IndexManager
    ) -> None:
        """新增深层图片时 SQLite 与 Chroma 都写入其一级合集编号。"""
        nested = Path(index_manager._memes_dir) / "新三国" / "截图"
        nested.mkdir(parents=True)
        (nested / "a.webp").write_bytes(b"image")

        result = await index_manager.refresh()

        entry = next(iter(index_manager._metadata_store.get_all_entries().values()))
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        assert result.added == 1
        assert entry.image_path == "新三国/截图/a.webp"
        assert entry.collection_id == 1
        assert vector_store._collection_ids[entry.id] == 1

    @pytest.mark.asyncio
    async def test_refresh_dedupes_only_within_collection(
        self, index_manager: IndexManager
    ) -> None:
        """相同 OCR 文本可跨合集入库，但同一合集内仍去重。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        for name in ("甲", "乙"):
            directory = memes_dir / name
            directory.mkdir()
            (directory / "a.webp").write_bytes(b"image")

        result = await index_manager.refresh()

        assert result.added == 2
        assert result.deduped == 0
        assert {
            entry.collection_id
            for entry in index_manager._metadata_store.get_all_entries().values()
        } == {1, 2}

    @pytest.mark.asyncio
    async def test_refresh_continues_when_missing_vector_reembed_fails(
        self, index_manager: IndexManager
    ) -> None:
        """缺失向量重建失败只记录文件，不对不存在向量修复 metadata。"""

        class FailingEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                if text == "失败文本":
                    raise RuntimeError("embed failed")
                return [1.0]

            async def close(self) -> None:
                pass

        collection = index_manager._metadata_store.create_collection("新三国")
        failed_id = index_manager._metadata_store.add(
            "新三国/failed.webp", "失败文本", collection_id=collection.id
        )
        existing_id = index_manager._metadata_store.add("root.webp", "全局文本")
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        await vector_store.upsert(existing_id, [1.0], collection_id=0)

        async def strict_update_collection_id(
            entry_id: int, collection_id: int
        ) -> None:
            if entry_id not in vector_store._vecs:
                raise ValueError(f"vector {entry_id} not found")
            vector_store._collection_ids[entry_id] = collection_id

        vector_store.update_collection_id = strict_update_collection_id  # ty: ignore[invalid-assignment]
        index_manager._embedding_provider = FailingEmbeddingProvider()
        collection_dir = Path(index_manager._memes_dir) / "新三国"
        collection_dir.mkdir()
        (collection_dir / "failed.webp").write_bytes(b"image")
        (Path(index_manager._memes_dir) / "root.webp").write_bytes(b"image")

        result = await index_manager.refresh()

        assert result.failed == ("新三国/failed.webp",)
        assert failed_id not in vector_store._vecs
        assert vector_store._collection_ids == {existing_id: 0}

    @pytest.mark.asyncio
    async def test_refresh_continues_when_missing_vector_upsert_fails(
        self, index_manager: IndexManager
    ) -> None:
        """缺失向量 upsert 失败只记录该图片，并继续处理其他缺失向量。"""
        first_id = index_manager._metadata_store.add("first.webp", "甲")
        failed_id = index_manager._metadata_store.add("failed.webp", "乙")
        existing_id = index_manager._metadata_store.add("existing.webp", "丙")
        for filename in ("first.webp", "failed.webp", "existing.webp"):
            (Path(index_manager._memes_dir) / filename).write_bytes(b"image")
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        await vector_store.upsert(existing_id, [1.0], collection_id=0)
        await vector_store.upsert(999, [2.0], collection_id=0)
        vector_store.upsert_error_for = {failed_id}

        result = await index_manager.refresh()

        assert result.failed == ("failed.webp",)
        assert vector_store.has(first_id)
        assert not vector_store.has(failed_id)
        assert vector_store.has(existing_id)
        assert not vector_store.has(999)

    @pytest.mark.asyncio
    async def test_refresh_missing_vector_keeps_collection_metadata(
        self, index_manager: IndexManager
    ) -> None:
        """阶段0补写缺失向量后不会因旧 ID 快照跳过或覆盖合集元数据。"""
        collection = index_manager._metadata_store.create_collection("新三国")
        entry_id = index_manager._metadata_store.add(
            "新三国/a.webp", "文本", collection_id=collection.id
        )
        directory = Path(index_manager._memes_dir) / "新三国"
        directory.mkdir()
        (directory / "a.webp").write_bytes(b"image")
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        other_id = index_manager._metadata_store.add("root.webp", "全局")
        await vector_store.upsert(other_id, [1.0], collection_id=0)
        (Path(index_manager._memes_dir) / "root.webp").write_bytes(b"image")

        await index_manager.refresh()

        assert vector_store._collection_ids[entry_id] == collection.id

    @pytest.mark.asyncio
    async def test_refresh_repairs_vector_collection_metadata(
        self, index_manager: IndexManager
    ) -> None:
        """阶段0以 SQLite 为准修复 Chroma 的 collection_id。"""
        collection = index_manager._metadata_store.create_collection("新三国")
        entry_id = index_manager._metadata_store.add(
            "新三国/a.webp", "文本", collection_id=collection.id
        )
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        await vector_store.upsert(entry_id, [1.0], collection_id=0)
        directory = Path(index_manager._memes_dir) / "新三国"
        directory.mkdir()
        (directory / "a.webp").write_bytes(b"image")

        await index_manager.refresh()

        assert vector_store._collection_ids[entry_id] == collection.id

    @pytest.mark.asyncio
    async def test_pipeline_preserves_nested_relative_path(
        self, index_manager: IndexManager
    ) -> None:
        """优化输出路径仍以 memes/ 为基准返回完整相对路径。"""
        nested = Path(index_manager._memes_dir) / "新三国" / "截图"
        nested.mkdir(parents=True)
        (nested / "a.jpg").write_bytes(b"image")
        index_manager._optimizer = cast(ImageOptimizer, FakeOptimizer(output_path=str(nested / "a.webp")))

        final_path, _, _ = await index_manager._process_image_pipeline(
            "新三国/截图/a.jpg"
        )

        assert final_path == "新三国/截图/a.webp"

    @pytest.mark.asyncio
    async def test_refresh_moves_nested_no_text_image_by_basename(
        self, index_manager: IndexManager
    ) -> None:
        """深层无文字图片移出 memes/ 时不在归档目录复制合集层级。"""

        class EmptyOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return ""

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = EmptyOcrProvider()
        nested = Path(index_manager._memes_dir) / "新三国" / "截图"
        nested.mkdir(parents=True)
        (nested / "blank.webp").write_bytes(b"image")

        result = await index_manager.refresh()

        assert result.no_text_moved == 1
        assert not (nested / "blank.webp").exists()
        assert (Path(index_manager._no_text_dir) / "blank.webp").exists()

    @pytest.mark.asyncio
    async def test_refresh_moves_nested_duplicate_image_by_basename(
        self, index_manager: IndexManager
    ) -> None:
        """深层重复图片归档时不在归档目录复制合集层级。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "重复文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        nested = Path(index_manager._memes_dir) / "新三国" / "截图"
        nested.mkdir(parents=True)
        (nested / "old.webp").write_bytes(b"old")
        await index_manager.refresh()
        (nested / "new.webp").write_bytes(b"new")

        result = await index_manager.refresh()

        assert result.deduped == 1
        assert not (nested / "new.webp").exists()
        assert (Path(index_manager._replaced_dir) / "new.webp").exists()

    @pytest.mark.asyncio
    async def test_refresh_dedup_moves_duplicate_to_replaced(
        self, index_manager: IndexManager
    ) -> None:
        """新图与已有条目 OCR 文本重复时，应归档到 memes_replaced/。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "重复文本"

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = ConstantOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        replaced_dir = Path(index_manager._replaced_dir)

        # 先建立一条已有索引
        (memes_dir / "old.jpg").write_bytes(b"1")
        await index_manager.add("old.jpg")

        # 再放入一张 OCR 文本相同的新图
        (memes_dir / "new.jpg").write_bytes(b"2")
        result = await index_manager.refresh()

        assert result.deduped == 1
        assert result.added == 0
        assert not (memes_dir / "new.jpg").exists()
        assert (replaced_dir / "new.jpg").exists()

    @pytest.mark.asyncio
    async def test_refresh_vector_delete_failure_restores_sqlite_and_continues(
        self, index_manager: IndexManager
    ) -> None:
        """向量删除失败时恢复 SQLite 条目，且继续清理后续缺失图片。"""
        metadata_store = cast(FakeMetadataStore, index_manager._metadata_store)
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        collection = metadata_store.create_collection("新三国")
        (Path(index_manager._memes_dir) / collection.name).mkdir()
        failed_id = metadata_store.add(
            "新三国/failed.webp",
            "失败",
            speaker="甲",
            tags=["标签"],
            collection_id=collection.id,
        )
        deleted_id = metadata_store.add("deleted.webp", "成功")
        (Path(index_manager._memes_dir) / "new.webp").write_bytes(b"image")
        await vector_store.upsert(failed_id, [1.0], collection_id=collection.id)
        await vector_store.upsert(deleted_id, [2.0], collection_id=0)
        original_remove = vector_store.remove

        async def remove_with_failure(entry_id: int) -> None:
            if entry_id == failed_id:
                raise RuntimeError("remove failed")
            await original_remove(entry_id)

        vector_store.remove = remove_with_failure  # ty: ignore[invalid-assignment]

        result = await index_manager.refresh()

        restored = metadata_store.get_entry(failed_id)
        assert result.added == 1
        assert result.deleted == 1
        assert result.failed == ("新三国/failed.webp",)
        assert restored is not None
        assert restored.id == failed_id
        assert restored.image_path == "新三国/failed.webp"
        assert restored.collection_id == collection.id
        assert restored.local_id == 1
        assert restored.text == "失败"
        assert restored.speaker == "甲"
        assert restored.tags == ("标签",)
        assert vector_store.has(failed_id)
        assert metadata_store.get_by_filename("deleted.webp") is None
        reused = metadata_store.get_entry(deleted_id)
        assert reused is not None
        assert reused.image_path == "new.webp"
        assert vector_store.has(deleted_id)

    @pytest.mark.asyncio
    async def test_refresh_remove_error_after_vector_deleted_keeps_final_deletion(
        self, index_manager: IndexManager
    ) -> None:
        """向量已删除后才抛错时，不恢复 SQLite，并继续删除消失合集。"""
        metadata_store = cast(FakeMetadataStore, index_manager._metadata_store)
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        collection = metadata_store.create_collection("待删除合集")
        entry_id = metadata_store.add(
            "待删除合集/deleted.webp",
            "已删除",
            speaker="乙",
            tags=["旧图"],
            collection_id=collection.id,
        )
        await vector_store.upsert(entry_id, [1.0], collection_id=collection.id)
        original_remove = vector_store.remove

        async def remove_then_fail(target_id: int) -> None:
            await original_remove(target_id)
            raise RuntimeError("remove failed after delete")

        vector_store.remove = remove_then_fail  # ty: ignore[invalid-assignment]

        result = await index_manager.refresh()

        assert result.deleted == 1
        assert result.collections_deleted == 1
        assert result.failed == ()
        assert metadata_store.get_entry(entry_id) is None
        assert not vector_store.has(entry_id)
        assert metadata_store.get_collection(collection.id) is None

    @pytest.mark.asyncio
    async def test_refresh_no_text_moved(self, index_manager: IndexManager) -> None:
        """refresh 遇到无文字新图应移入 meme_no_text/，计入 no_text_moved 且不计入 failed。"""

        class EmptyOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return ""

            async def close(self) -> None:
                pass

        index_manager._ocr_provider = EmptyOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        no_text_dir = Path(index_manager._no_text_dir)
        (memes_dir / "blank.jpg").write_bytes(b"fake")

        result = await index_manager.refresh()
        assert result.no_text_moved == 1
        assert result.added == 0
        assert "blank.jpg" not in result.failed
        assert not (memes_dir / "blank.jpg").exists()
        moved_files = list(no_text_dir.glob("blank*"))
        assert len(moved_files) == 1

        await index_manager.close()


@pytest.mark.asyncio
async def test_scan_meme_files_called_once_per_sync(
    index_manager: IndexManager,
) -> None:
    """sync 内 _scan_meme_files 仅调用一次（phase1/phase2 复用同一快照）。"""
    call_count = 0
    original = index_manager._scan_meme_files

    def counting_scan() -> FileSystemSnapshot:
        nonlocal call_count
        call_count += 1
        return original()

    index_manager._scan_meme_files = counting_scan  # type: ignore[assignment, ty:invalid-assignment]
    await index_manager.refresh()
    assert call_count == 1


# ---------------------------------------------------------------------------
# random_search / semantic_search 测试
# ---------------------------------------------------------------------------


class TestRandomSearch:
    @pytest.mark.asyncio
    async def test_random_search_full_random(self, index_manager: IndexManager) -> None:
        """无关键词时从全库随机返回候选。"""
        for i in range(5):
            (Path(index_manager._memes_dir) / f"img{i}.jpg").write_bytes(b"fake")
            await index_manager.add(f"img{i}.jpg")

        results = await index_manager.random_search(None)
        assert len(results) == 5
        assert len({r.entry_id for r in results}) == 5

    @pytest.mark.asyncio
    async def test_random_search_with_keyword(
        self, index_manager: IndexManager
    ) -> None:
        """有关键词时在搜索结果中随机。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "加班.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")
        await index_manager.add("加班.jpg")

        results = await index_manager.random_search("加班")
        assert len(results) == 1
        assert "加班" in results[0].text

    @pytest.mark.asyncio
    async def test_random_search_keyword_no_match(
        self, index_manager: IndexManager
    ) -> None:
        """关键词无匹配时返回空列表。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")

        results = await index_manager.random_search("火星文")
        assert results == []

    @pytest.mark.asyncio
    async def test_random_search_empty_index(self, index_manager: IndexManager) -> None:
        results = await index_manager.random_search(None)
        assert results == []

    @pytest.mark.asyncio
    async def test_random_search_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 RandomSearcher 时抛 RuntimeError。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")
        index_manager._random_searcher = None
        with pytest.raises(RuntimeError, match="RandomSearcher 未注入"):
            await index_manager.random_search(None)


class TestSemanticSearch:
    @pytest.mark.asyncio
    async def test_semantic_search_returns_results(
        self, index_manager: IndexManager
    ) -> None:
        """语义搜索返回候选列表。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "overtime.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")
        await index_manager.add("overtime.jpg")

        results = await index_manager.semantic_search("加班相关")
        assert isinstance(results, list)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_semantic_search_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        results = await index_manager.semantic_search("任意描述")
        assert results == []

    @pytest.mark.asyncio
    async def test_semantic_search_zero_vector(
        self, index_manager: IndexManager
    ) -> None:
        """embedding 返回零向量时抛 ValueError。"""

        class ZeroEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.0] * 1024

            async def close(self) -> None:
                pass

        index_manager._embedding_provider = ZeroEmbeddingProvider()
        with pytest.raises(ValueError, match="零向量"):
            await index_manager.semantic_search("任意描述")

    @pytest.mark.asyncio
    async def test_semantic_search_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 SemanticSearcher 时抛 RuntimeError。"""
        index_manager._semantic_searcher = None
        with pytest.raises(RuntimeError, match="SemanticSearcher 未注入"):
            await index_manager.semantic_search("任意描述")


class TestCombinedSearch:
    @pytest.mark.asyncio
    async def test_search_combined_with_keyword(
        self, index_manager: IndexManager
    ) -> None:
        """组合检索：关键词在过滤子集上匹配。"""
        index_manager._metadata_store.add(
            "加班.jpg", "加班到凌晨", speaker="小明", tags=["吐槽"]
        )
        results = await index_manager.search_combined("加班", ["小明"], [])
        assert len(results) == 1
        assert results[0].similarity == 100.0
        assert results[0].entry_id == 1

    @pytest.mark.asyncio
    async def test_search_combined_pure_filter(
        self, index_manager: IndexManager
    ) -> None:
        """纯过滤（无关键词）返回 similarity=0.0。"""
        index_manager._metadata_store.add(
            "加班.jpg", "加班到凌晨", speaker="小明", tags=["吐槽"]
        )
        results = await index_manager.search_combined(None, ["小明"], [])
        assert len(results) == 1
        assert results[0].similarity == 0.0

    @pytest.mark.asyncio
    async def test_search_combined_tag_filter(
        self, index_manager: IndexManager
    ) -> None:
        """tags AND 过滤。"""
        index_manager._metadata_store.add(
            "a.jpg", "加班到凌晨", speaker="小明", tags=["吐槽", "加班"]
        )
        index_manager._metadata_store.add(
            "b.jpg", "周末加班", speaker="小明", tags=["加班"]
        )
        results = await index_manager.search_combined(None, [], ["吐槽"])
        assert {r.entry_id for r in results} == {1}

    @pytest.mark.asyncio
    async def test_search_combined_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        assert await index_manager.search_combined("加班", [], []) == []

    @pytest.mark.asyncio
    async def test_search_combined_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 CombinedSearcher 时抛 RuntimeError。"""
        index_manager._metadata_store.add("a.jpg", "加班", speaker="小明")
        index_manager._combined_searcher = None
        with pytest.raises(RuntimeError, match="CombinedSearcher 未注入"):
            await index_manager.search_combined("加班", ["小明"], [])


class TestCollectionSearch:
    """Task 6: 合集过滤与选择解析测试。"""

    def test_collection_selection_expired_error_is_runtime_error(self) -> None:
        """选择快照失效异常应是明确的运行时业务错误。"""
        assert issubclass(CollectionSelectionExpiredError, RuntimeError)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("replacement_name", ["新合集", "旧合集"])
    async def test_random_snapshot_rejects_reused_collection_after_refresh(
        self,
        index_manager: IndexManager,
        replacement_name: str,
    ) -> None:
        """刷新回退 scope 后，即使编号被同名或异名合集复用也拒绝换批。"""
        store = cast(FakeMetadataStore, index_manager._metadata_store)
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        old_collection = store.create_collection("旧合集")
        old_dir = index_manager._memes_dir / old_collection.name
        old_dir.mkdir()
        old_file = old_dir / "old.webp"
        old_file.write_bytes(b"old")
        old_entry_id = store.add(
            "旧合集/old.webp", "旧合集文本", collection_id=old_collection.id
        )
        await index_manager._vector_store.upsert(
            old_entry_id,
            [float(ord("旧"))] * 1024,
            collection_id=old_collection.id,
        )
        store.set_selected_collection(scope, old_collection.id)
        selection, _results = await index_manager.random_search_for_scope(scope)

        old_file.unlink()
        old_dir.rmdir()
        await index_manager.refresh()
        replacement_dir = index_manager._memes_dir / replacement_name
        replacement_dir.mkdir()
        (replacement_dir / "new.webp").write_bytes(b"new")
        await index_manager.refresh()
        replacement = store.get_collection(old_collection.id)
        assert replacement is not None
        assert replacement.name == replacement_name
        assert store.get_selected_collection(scope) == 0

        with pytest.raises(CollectionSelectionExpiredError):
            await index_manager.random_search_for_scope_snapshot(scope, None, selection)

    @pytest.mark.asyncio
    async def test_random_snapshot_searches_when_selection_is_unchanged(
        self, index_manager: IndexManager
    ) -> None:
        """普通合集选择未变化时按首次 selection 继续随机搜索。"""
        store = cast(FakeMetadataStore, index_manager._metadata_store)
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = store.create_collection("旧合集")
        store.add("旧合集/a.webp", "文本", collection_id=collection.id)
        store.set_selected_collection(scope, collection.id)
        selection, _results = await index_manager.random_search_for_scope(scope)

        results = await index_manager.random_search_for_scope_snapshot(
            scope, None, selection
        )

        assert [result.collection_id for result in results] == [collection.id]

    @pytest.mark.asyncio
    async def test_random_snapshot_searches_all_when_global_is_unchanged(
        self, index_manager: IndexManager
    ) -> None:
        """全部合集选择未变化时仍允许全库换批。"""
        store = cast(FakeMetadataStore, index_manager._metadata_store)
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = store.create_collection("合集")
        store.add("合集/a.webp", "文本", collection_id=collection.id)
        selection, _results = await index_manager.random_search_for_scope(scope)

        results = await index_manager.random_search_for_scope_snapshot(
            scope, None, selection
        )

        assert [result.collection_id for result in results] == [collection.id]

    @pytest.mark.asyncio
    async def test_scope_search_blocks_refresh_after_selection_snapshot(
        self, index_manager: IndexManager
    ) -> None:
        """scope 选择与实际搜索共用读锁，刷新不能在两者间复用合集编号。"""
        store = cast(FakeMetadataStore, index_manager._metadata_store)
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        old_collection = store.create_collection("旧合集")
        old_entry_id = store.add(
            "旧合集/old.webp", "旧合集文本", collection_id=old_collection.id
        )
        store.set_selected_collection(scope, old_collection.id)
        vector_store = cast(FakeVectorStore, index_manager._vector_store)
        await vector_store.upsert(
            old_entry_id,
            [float(ord("旧"))] * 1024,
            collection_id=old_collection.id,
        )

        entered_query = asyncio.Event()
        release_query = asyncio.Event()
        original_query = vector_store.query

        async def blocking_query(
            query_embedding: list[float],
            n_results: int | None = 10,
            *,
            collection_id: int | None = None,
        ) -> list[VectorHit]:
            entered_query.set()
            await release_query.wait()
            return await original_query(
                query_embedding,
                n_results,
                collection_id=collection_id,
            )

        vector_store.query = blocking_query  # ty: ignore[invalid-assignment]
        search_task = asyncio.create_task(
            index_manager.semantic_search_for_scope(scope, "旧", limit=None)
        )
        await entered_query.wait()

        old_dir = index_manager._memes_dir / "旧合集"
        old_dir.mkdir()
        (old_dir / "old.webp").write_bytes(b"old")
        old_dir.rename(index_manager._memes_dir / "新合集")
        refresh_task = asyncio.create_task(index_manager.refresh())
        await asyncio.sleep(0)

        assert refresh_task.done() is False
        assert store.get_collection(old_collection.id) == old_collection

        release_query.set()
        results = await search_task
        assert [result.text for result in results] == ["旧合集文本"]

        await refresh_task
        replacement = store.get_collection(old_collection.id)
        assert replacement is not None
        assert replacement.name == "新合集"
        assert store.get_selected_collection(scope) == 0

    @pytest.mark.asyncio
    async def test_search_uses_requested_collection(
        self, index_manager: IndexManager
    ) -> None:
        """关键词搜索按 collection_id 过滤。"""
        store = index_manager._metadata_store
        first = store.create_collection("新三国")
        second = store.create_collection("甄嬛传")
        store.add("新三国/a.webp", "相同关键词", collection_id=first.id)
        store.add("甄嬛传/b.webp", "相同关键词", collection_id=second.id)

        results = await index_manager.search("关键词", collection_id=first.id)

        assert [str(result.public_id) for result in results] == ["1.1"]

    @pytest.mark.asyncio
    async def test_search_none_collection_uses_all_entries(
        self, index_manager: IndexManager
    ) -> None:
        """collection_id=None 时搜索全库。"""
        store = index_manager._metadata_store
        first = store.create_collection("新三国")
        second = store.create_collection("甄嬛传")
        store.add("新三国/a.webp", "相同关键词", collection_id=first.id)
        store.add("甄嬛传/b.webp", "相同关键词", collection_id=second.id)

        results = await index_manager.search("关键词", collection_id=None)

        assert {result.collection_id for result in results} == {1, 2}

    @pytest.mark.asyncio
    async def test_resolve_entry_uses_scope_short_id(
        self,
        index_manager: IndexManager,
        collection_manager: "CollectionManager",
    ) -> None:
        """resolve_entry 使用当前合集的短号解析。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = index_manager._metadata_store.create_collection("新三国")
        index_manager._metadata_store.add(
            "新三国/a.webp", "文本", collection_id=collection.id
        )
        collection_manager.set_selected(scope, collection.id)

        entry = await index_manager.resolve_entry(scope, "001")

        assert entry.public_id == MemePublicId(1, 1)

    @pytest.mark.asyncio
    async def test_info_ranks_speakers_in_selected_range(
        self, index_manager: IndexManager
    ) -> None:
        """info 按 collection_id 范围统计并排行。"""
        first = index_manager._metadata_store.create_collection("新三国")
        second = index_manager._metadata_store.create_collection("甄嬛传")
        index_manager._metadata_store.add(
            "新三国/a.webp", "甲", speaker="曹操", collection_id=first.id
        )
        index_manager._metadata_store.add(
            "甄嬛传/b.webp", "乙", speaker="皇后", collection_id=second.id
        )

        info = await index_manager.info(collection_id=first.id)

        assert info.entry_count == 2
        assert info.current_entry_count == 1
        assert info.collection_count == 2
        assert info.speaker_ranking == (("曹操", 1),)

    @pytest.mark.asyncio
    async def test_random_search_filters_by_collection(
        self, index_manager: IndexManager
    ) -> None:
        """random_search 按 collection_id 过滤。"""
        store = index_manager._metadata_store
        first = store.create_collection("新三国")
        second = store.create_collection("甄嬛传")
        store.add("新三国/a.webp", "甲", collection_id=first.id)
        store.add("甄嬛传/b.webp", "乙", collection_id=second.id)

        results = await index_manager.random_search(None, collection_id=first.id)

        assert len(results) == 1
        assert results[0].collection_id == first.id

    @pytest.mark.asyncio
    async def test_semantic_search_filters_by_collection(
        self, index_manager: IndexManager
    ) -> None:
        """semantic_search 按 collection_id 过滤。"""
        store = index_manager._metadata_store
        vs = cast(FakeVectorStore, index_manager._vector_store)
        first = store.create_collection("新三国")
        second = store.create_collection("甄嬛传")
        # MockEmbeddingProvider 按首字符生成向量：a=97, b=98
        eid_a = store.add("新三国/a.webp", "apple", collection_id=first.id)
        eid_b = store.add("甄嬛传/b.webp", "banana", collection_id=second.id)
        await vs.upsert(eid_a, [97.0] * 1024, collection_id=first.id)
        await vs.upsert(eid_b, [98.0] * 1024, collection_id=second.id)

        results = await index_manager.semantic_search("apple", collection_id=second.id)

        assert len(results) == 1
        assert results[0].collection_id == second.id
        assert results[0].text == "banana"

    @pytest.mark.asyncio
    async def test_search_combined_filters_by_collection(
        self, index_manager: IndexManager
    ) -> None:
        """search_combined 按 collection_id 过滤。"""
        store = index_manager._metadata_store
        first = store.create_collection("新三国")
        second = store.create_collection("甄嬛传")
        store.add("新三国/a.webp", "加班", collection_id=first.id)
        store.add("甄嬛传/b.webp", "加班", collection_id=second.id)

        results = await index_manager.search_combined(
            "加班", [], [], collection_id=second.id
        )

        assert len(results) == 1
        assert results[0].collection_id == second.id

# ---------------------------------------------------------------------------
# Task 6: 合集管理方法直接测试
# ---------------------------------------------------------------------------


class TestCollectionManagement:
    """IndexManager 合集管理方法单元测试。"""

    @pytest.mark.asyncio
    async def test_get_selected_collection(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """get_selected_collection 返回当前作用域选择。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = index_manager._metadata_store.create_collection("新三国")
        collection_manager.set_selected(scope, collection.id)

        selection = await index_manager.get_selected_collection(scope)

        assert selection == CollectionSelection(
            collection_id=collection.id, name=collection.name
        )

    @pytest.mark.asyncio
    async def test_validate_collection_selection_rejects_reused_collection(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """scope 回退后即使同名合集复用编号，旧选择快照也必须失效。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        store = cast(FakeMetadataStore, index_manager._metadata_store)
        original = store.create_collection("旧合集")
        collection_manager.set_selected(scope, original.id)
        expected = await index_manager.get_selected_collection(scope)

        store.delete_collection_and_reset_scopes(original.id)
        replacement = store.create_collection("旧合集")
        assert replacement.id == original.id

        with pytest.raises(CollectionSelectionExpiredError):
            await index_manager.validate_collection_selection(scope, expected)

    @pytest.mark.asyncio
    async def test_validate_collection_selection_accepts_unchanged_snapshot(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """当前 scope 完整选择未变化时校验成功。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = index_manager._metadata_store.create_collection("新三国")
        collection_manager.set_selected(scope, collection.id)
        expected = await index_manager.get_selected_collection(scope)

        await index_manager.validate_collection_selection(scope, expected)

    @pytest.mark.asyncio
    async def test_add_write_rejects_expired_selection_and_cleans_pipeline_file(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """管线后 scope 回退时写锁校验应拒绝写入并清理最终文件。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = index_manager._metadata_store.create_collection("旧合集")
        collection_manager.set_selected(scope, collection.id)
        expected = await index_manager.get_selected_collection(scope)
        image = index_manager._memes_dir / "旧合集/a.jpg"
        image.parent.mkdir()
        image.write_bytes(b"image")
        cast(
            FakeMetadataStore, index_manager._metadata_store
        ).delete_collection_and_reset_scopes(collection.id)
        replacement = index_manager._metadata_store.create_collection("新合集")
        assert replacement.id == collection.id

        with pytest.raises(CollectionSelectionExpiredError):
            await index_manager.add(
                "旧合集/a.jpg",
                collection_id=collection.id,
                scope=scope,
                expected_selection=expected,
            )

        assert index_manager._metadata_store.entry_count() == 0
        assert not image.exists()

    @pytest.mark.asyncio
    async def test_get_selected_collection_read_lock_timeout(
        self, index_manager: IndexManager
    ) -> None:
        """写锁持有期间 get_selected_collection 读锁超时。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        index_manager.read_timeout = 0.01

        async with index_manager._rwlock.write():
            with pytest.raises(asyncio.TimeoutError):
                await index_manager.get_selected_collection(scope)

    @pytest.mark.asyncio
    async def test_list_collections(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """list_collections 返回全部合集入口与普通合集摘要。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        index_manager._metadata_store.add("全局.webp", "全局条目")
        first = index_manager._metadata_store.create_collection("新三国")
        index_manager._metadata_store.add("新三国/a.webp", "甲", collection_id=first.id)
        collection_manager.set_selected(scope, first.id)

        summaries = await index_manager.list_collections(scope)

        assert summaries == [
            CollectionSummary(
                collection_id=0,
                name="全部合集",
                entry_count=2,
                selected=False,
            ),
            CollectionSummary(
                collection_id=first.id,
                name=first.name,
                entry_count=1,
                selected=True,
            ),
        ]

    @pytest.mark.asyncio
    async def test_list_collections_read_lock_timeout(
        self, index_manager: IndexManager
    ) -> None:
        """写锁持有期间 list_collections 读锁超时。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        index_manager.read_timeout = 0.01

        async with index_manager._rwlock.write():
            with pytest.raises(asyncio.TimeoutError):
                await index_manager.list_collections(scope)

    @pytest.mark.asyncio
    async def test_switch_collection(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """switch_collection 切换当前作用域合集。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = index_manager._metadata_store.create_collection("新三国")

        selection = await index_manager.switch_collection(scope, str(collection.id))

        assert selection.collection_id == collection.id
        assert collection_manager.get_selected(scope).collection_id == collection.id

    @pytest.mark.asyncio
    async def test_switch_collection_by_name(
        self,
        index_manager: IndexManager,
        collection_manager: CollectionManager,
    ) -> None:
        """switch_collection 支持按合集名称切换。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        collection = index_manager._metadata_store.create_collection("新三国")

        selection = await index_manager.switch_collection(scope, collection.name)

        assert selection.collection_id == collection.id
        assert collection_manager.get_selected(scope).collection_id == collection.id

    @pytest.mark.asyncio
    async def test_switch_collection_not_found(
        self, index_manager: IndexManager
    ) -> None:
        """switch_collection 目标不存在时抛 CollectionNotFoundError。"""
        scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
        from bot.engine.collection_manager import CollectionNotFoundError

        with pytest.raises(CollectionNotFoundError):
            await index_manager.switch_collection(scope, "不存在")


# ---------------------------------------------------------------------------
# F8: _get_chroma_ids 改用 get_all_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chroma_ids_uses_get_all_ids(
    index_manager: IndexManager,
) -> None:
    """_get_chroma_ids 应通过 get_all_ids 取全量 id，不再走零向量 query。"""
    vs = cast(FakeVectorStore, index_manager._vector_store)
    # 放入若干向量
    await vs.upsert(1, [1.0])
    await vs.upsert(2, [2.0])
    await vs.upsert(42, [3.0])

    # spy：若走到 query 路径则直接失败
    async def spy_query(
        query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]:
        raise AssertionError("_get_chroma_ids 不应再调用 query")

    vs.query = spy_query  # type: ignore[method-assign, ty:invalid-assignment]

    ids = await index_manager._get_chroma_ids()
    assert ids == {1, 2, 42}


# ---------------------------------------------------------------------------
# F6: add() 内部超时取消入队 future + worker 跳过 done future
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_timeout_cancels_enqueued_future(
    index_manager: IndexManager,
) -> None:
    """add() 内部超时应取消已入队的 future，避免孤儿写入。"""
    index_manager.add_user_timeout = 0.1
    (Path(index_manager._memes_dir) / "x.jpg").write_bytes(b"fake")

    # 让 _write_entry 永久挂起，模拟写操作卡住
    hang_event = asyncio.Event()

    async def hanging_write(
        filename: str,
        text: str,
        embedding: list[float],
        speaker: str | None = None,
        tags: list[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        await hang_event.wait()
        raise AssertionError("不应走到这里")

    index_manager._write_entry = hanging_write  # type: ignore[method-assign, ty:invalid-assignment]

    with pytest.raises(asyncio.TimeoutError):
        await index_manager.add("x.jpg")

    # 清理挂住的 worker
    await index_manager.close()


@pytest.mark.asyncio
async def test_write_worker_skips_cancelled_future(
    index_manager: IndexManager,
) -> None:
    """worker 取出 future 已 done 的请求时应跳过，不调用 _write_entry。"""
    loop = asyncio.get_running_loop()

    # 已取消的 future（done()==True），应被 worker 跳过
    cancelled_future: asyncio.Future[AddResult] = loop.create_future()
    assert cancelled_future.cancel() is True
    skip_req = _WriteRequest(
        op=WriteOp.ADD,
        future=cancelled_future,  # type: ignore[arg-type, ty:invalid-argument-type]
        filename="skip.jpg",
        text="t",
        embedding=(1.0,),
    )

    # 正常 future，应被 worker 处理
    normal_future: asyncio.Future[AddResult] = loop.create_future()
    normal_req = _WriteRequest(
        op=WriteOp.ADD,
        future=normal_future,  # type: ignore[arg-type, ty:invalid-argument-type]
        filename="normal.jpg",
        text="hello",
        embedding=(1.0,),
    )

    # spy _write_entry：记录调用，返回 dummy AddResult
    write_calls: list[str] = []

    async def spy_write(
        filename: str,
        text: str,
        embedding: list[float],
        speaker: str | None = None,
        tags: list[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        write_calls.append(filename)
        return AddResult(entry_id=1, reason="added", text=text)

    index_manager._write_entry = spy_write  # type: ignore[method-assign, ty:invalid-assignment]

    await index_manager._write_queue.put(skip_req)
    await index_manager._write_queue.put(normal_req)

    index_manager._ensure_write_worker()
    # 让 worker 有机会处理两个请求
    await asyncio.sleep(0.05)

    # cancelled 请求未被写入，正常请求被写入
    assert write_calls == ["normal.jpg"]
    assert normal_future.done()
    assert normal_future.result().reason == "added"

    await index_manager.close()


# ---------------------------------------------------------------------------
# Task 5: _process_image_pipeline 返回 final_filename + 降级
# ---------------------------------------------------------------------------


class FakeOptimizer:
    """optimize 测试替身，返回指定 output_path 或抛异常。"""

    def __init__(
        self, output_path: str | None = None, raises: Exception | None = None
    ) -> None:
        self._output_path = output_path
        self._raises = raises

    async def optimize(self, image_path: str) -> OptimizeResult:
        if self._raises is not None:
            raise self._raises
        return OptimizeResult(
            original_size=100,
            optimized_size=80,
            saved=20,
            output_path=self._output_path or image_path,
        )


class FakeOcrProvider:
    """OCR 测试替身，固定返回指定文本。"""

    def __init__(self, text: str = "text") -> None:
        self._text = text

    async def ocr(self, image_path: str) -> str:
        return self._text

    async def close(self) -> None:
        pass


class FakeEmbeddingProvider:
    """Embedding 测试替身，返回固定向量。"""

    def __init__(self, vec: list[float] | None = None) -> None:
        self._vec = vec or [0.1] * 1024

    async def embed(self, text: str) -> list[float]:
        return self._vec

    async def close(self) -> None:
        pass


class TestPipelineFinalFilename:
    """_process_image_pipeline 返回 final_filename 与降级测试。"""

    @pytest.mark.asyncio
    async def test_pipeline_uses_output_path(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.webp"))
        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, opt),
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.webp"
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_pipeline_keeps_filename_when_no_convert(
        self, tmp_path: Path
    ) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.jpg"))
        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, opt),
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.jpg"

    @pytest.mark.asyncio
    async def test_pipeline_degrades_on_optimize_error(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(raises=RuntimeError("convert fail"))
        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, opt),
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.jpg"
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_pipeline_empty_text_returns_empty_embedding(
        self, tmp_path: Path
    ) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.webp"))
        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=FakeOcrProvider(""),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, opt),
        )
        final_fn, text, emb = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.webp"
        assert text == ""
        assert emb == []


# ---------------------------------------------------------------------------
# Task 6: add() 与 sync 阶段2 filename 流转 + 并发同名去重
# ---------------------------------------------------------------------------


class TestAddConvertsToWebp:
    """add() 转换后 sqlite image_path 为 .webp 测试。"""

    @pytest.mark.asyncio
    async def test_add_writes_webp_image_path(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "meme_001.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "meme_001.webp"))
        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=FakeOcrProvider("加班"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, opt),
        )
        result = await im.add("meme_001.jpg", speaker="小明", tags=["吐槽"])
        assert result.reason == "added"
        assert result.entry_id is not None
        entry = md.get_entry(result.entry_id)
        assert entry is not None
        assert entry.image_path == "meme_001.webp"

    @pytest.mark.asyncio
    async def test_add_degrades_to_original_format(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "meme_002.png").write_bytes(b"x")
        opt = FakeOptimizer(raises=RuntimeError("fail"))
        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=FakeOcrProvider("心累"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, opt),
        )
        result = await im.add("meme_002.png")
        assert result.reason == "added"
        assert result.entry_id is not None
        entry = md.get_entry(result.entry_id)
        assert entry is not None
        assert entry.image_path == "meme_002.png"


class PerFileOcrProvider:
    """按文件 stem 返回不同 OCR 文本，避免 phase2 text 去重干扰。"""

    async def ocr(self, image_path: str) -> str:
        return f"text_{Path(image_path).stem}"

    async def close(self) -> None:
        pass


class CountingOcrProvider:
    """按调用次数返回不同 OCR 文本（同 stem 不同图场景）。"""

    def __init__(self) -> None:
        self._n = 0

    async def ocr(self, image_path: str) -> str:
        self._n += 1
        return f"text{self._n}"

    async def close(self) -> None:
        pass


class TestSyncConvertsToWebp:
    """sync 阶段2 转换 + 并发同名去重测试。"""

    @pytest.mark.asyncio
    async def test_sync_converts_new_files(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        (memes / "b.png").write_bytes(b"x")

        class PerFileOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                p = Path(image_path)
                new_p = p.with_suffix(".webp")
                return OptimizeResult(100, 80, 20, output_path=str(new_p))

        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=PerFileOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, PerFileOptimizer()),
        )
        sync_result = await im.refresh()
        assert sync_result.added == 2
        paths = {e.image_path for e in md.get_all_entries().values()}
        assert paths == {"a.webp", "b.webp"}

    @pytest.mark.asyncio
    async def test_sync_reserves_same_stem_webp_targets_before_optimization(
        self, tmp_path: Path
    ) -> None:
        """同目录同 stem 转 WebP 时，每个优化任务必须拥有独立输出路径。"""
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "dup.jpg").write_bytes(b"jpg")
        (memes / "dup.png").write_bytes(b"png")

        class RacingOptimizer:
            """模拟真实转换的检查竞争、目标覆盖和源文件删除。"""

            def __init__(self) -> None:
                self._entered = 0
                self._both_entered = asyncio.Event()

            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                target = resolve_unique_filename(source.parent, f"{source.stem}.webp")
                self._entered += 1
                if self._entered == 2:
                    self._both_entered.set()
                try:
                    await asyncio.wait_for(self._both_entered.wait(), timeout=0.05)
                except TimeoutError:
                    pass
                target.write_bytes(source.read_bytes())
                source.unlink()
                return OptimizeResult(100, 80, 20, output_path=str(target))

        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=CountingOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, RacingOptimizer()),
        )

        sync_result = await im.refresh()

        assert sync_result.added == 2
        paths = {entry.image_path for entry in md.get_all_entries().values()}
        assert paths == {"dup.webp", "dup_1.webp"}
        assert all((memes / path).is_file() for path in paths)

    @pytest.mark.asyncio
    async def test_sync_serializes_casefolded_webp_targets(
        self, tmp_path: Path
    ) -> None:
        """大小写折叠后同目标的源图片必须串行转换。"""
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "A.jpg").write_bytes(b"upper")
        (memes / "a.png").write_bytes(b"lower")

        class CaseInsensitiveRacingOptimizer:
            """在任意平台模拟大小写不敏感目标选择与覆盖。"""

            def __init__(self) -> None:
                self._entered = 0
                self._both_entered = asyncio.Event()

            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                existing = {path.name.casefold() for path in source.parent.iterdir()}
                stem = source.stem.casefold()
                candidate = f"{stem}.webp"
                suffix = 1
                while candidate.casefold() in existing:
                    candidate = f"{stem}_{suffix}.webp"
                    suffix += 1
                target = source.parent / candidate
                self._entered += 1
                if self._entered == 2:
                    self._both_entered.set()
                try:
                    await asyncio.wait_for(self._both_entered.wait(), timeout=0.05)
                except TimeoutError:
                    pass
                target.write_bytes(source.read_bytes())
                source.unlink()
                return OptimizeResult(100, 80, 20, output_path=str(target))

        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=CountingOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, CaseInsensitiveRacingOptimizer()),
        )

        result = await im.refresh()

        assert result.added == 2
        paths = {entry.image_path for entry in md.get_all_entries().values()}
        assert paths == {"a.webp", "a_1.webp"}
        assert all((memes / path).is_file() for path in paths)

    @pytest.mark.asyncio
    async def test_sync_releases_same_stem_lock_before_ocr(
        self, tmp_path: Path
    ) -> None:
        """同 stem 转换串行完成后，OCR 阶段恢复并发。"""
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "dup.jpg").write_bytes(b"jpg")
        (memes / "dup.png").write_bytes(b"png")

        class NoopOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                return OptimizeResult(100, 80, 20, output_path=image_path)

        class BarrierOcrProvider:
            def __init__(self) -> None:
                self._entered = 0
                self._both_entered = asyncio.Event()

            async def ocr(self, image_path: str) -> str:
                self._entered += 1
                if self._entered == 2:
                    self._both_entered.set()
                await asyncio.wait_for(self._both_entered.wait(), timeout=0.1)
                return Path(image_path).suffix

            async def close(self) -> None:
                pass

        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=BarrierOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, NoopOptimizer()),
        )

        result = await im.refresh()

        assert result.added == 2
        assert result.failed == ()

    @pytest.mark.asyncio
    async def test_sync_keeps_same_stem_optimization_parallel_across_parents(
        self, tmp_path: Path
    ) -> None:
        """不同父目录的同 stem 图片仍可并发优化。"""
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        for name in ("甲", "乙"):
            directory = memes / name
            directory.mkdir(parents=True)
            (directory / "dup.jpg").write_bytes(name.encode())

        class BarrierOptimizer:
            def __init__(self) -> None:
                self._entered = 0
                self._both_entered = asyncio.Event()

            async def optimize(self, image_path: str) -> OptimizeResult:
                source = Path(image_path)
                self._entered += 1
                if self._entered == 2:
                    self._both_entered.set()
                await asyncio.wait_for(self._both_entered.wait(), timeout=0.1)
                return OptimizeResult(100, 80, 20, output_path=str(source))

        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=PerFileOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, BarrierOptimizer()),
        )

        result = await im.refresh()

        assert result.added == 2
        assert {entry.image_path for entry in md.get_all_entries().values()} == {
            "甲/dup.jpg",
            "乙/dup.jpg",
        }

    @pytest.mark.asyncio
    async def test_sync_dedups_same_stem_final_filename(self, tmp_path: Path) -> None:
        """两张同 stem 不同扩展名新增图转 webp 后同名，需去重 rename，两张均入库。"""
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "dup.jpg").write_bytes(b"x")
        (memes / "dup.png").write_bytes(b"x")

        class SameStemOptimizer:
            """两张都返回 dup.webp（模拟未去重），由 pipeline 去重 rename。"""

            async def optimize(self, image_path: str) -> OptimizeResult:
                return OptimizeResult(100, 80, 20, output_path=str(memes / "dup.webp"))

        im = IndexManager(
            cast(MetadataStore, md),
            cast(VectorStore, vs),
            str(memes),
            ocr_provider=CountingOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=cast(ImageOptimizer, SameStemOptimizer()),
        )
        sync_result = await im.refresh()
        assert sync_result.added == 2
        paths = {e.image_path for e in md.get_all_entries().values()}
        assert "dup.webp" in paths
        assert len(paths) == 2
