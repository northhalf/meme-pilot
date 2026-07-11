"""IndexManager 新 API 单元测试。

验证并发控制、读写锁、refresh/add 互斥、close 取消等核心行为。
使用 Fake Store 与 mock providers，无真实 sqlite/chroma I/O。
"""

import asyncio
from pathlib import Path

import pytest

from typing import cast

from bot.engine.ai_matcher import AIMatcher
from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    DuplicateTextError,
    EmbeddingError,
    IndexAddCancelledError,
    IndexCorruptedError,
    IndexManager,
    OcrError,
    RefreshInProgressError,
    SyncResult,
    WriteOp,
    _WriteRequest,
    resolve_unique_filename,
)
from bot.engine.image_optimizer import OptimizeResult
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.vector_store import VectorHit

# 哨兵值，区分「不修改字段」与显式的 None
_UNSET = object()


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


class FakeMetadataStore:
    """内存 MetadataStore，实现真接口，用于 IndexManager 测试。"""

    def __init__(self) -> None:
        self._entries: dict[int, MemeEntry] = {}
        self._next_auto = 1
        self.add_order: list[int] = []

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return dict(self._entries)

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)

    def get_by_filename(self, image_path: str) -> MemeEntry | None:
        for e in self._entries.values():
            if e.image_path == image_path:
                return e
        return None

    def get_id_by_text(self, text: str) -> int | None:
        for eid, e in self._entries.items():
            if e.text == text:
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

    def entry_count(self) -> int:
        return len(self._entries)

    def get_all_text(self) -> list[tuple[int, str]]:
        return [(eid, e.text) for eid, e in sorted(self._entries.items())]

    def add(self, image_path, text, speaker=None, tags=None) -> int:
        eid = self.find_next_id()
        self._entries[eid] = MemeEntry(
            id=eid, image_path=image_path, text=text, speaker=speaker, tags=tags or []
        )
        self.add_order.append(eid)
        return eid

    def add_with_id(self, entry_id, image_path, text, speaker=None, tags=None) -> int:
        self._entries[entry_id] = MemeEntry(
            id=entry_id,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=tags or [],
        )
        return entry_id

    def update(
        self, entry_id, *, image_path=_UNSET, text=_UNSET, speaker=_UNSET, tags=None
    ) -> bool:
        e = self._entries.get(entry_id)
        if e is None:
            return False
        new_image = image_path if image_path is not _UNSET else e.image_path
        new_text = text if text is not _UNSET else e.text
        new_speaker = speaker if speaker is not _UNSET else e.speaker
        new_tags = tags if tags is not None else e.tags
        self._entries[entry_id] = MemeEntry(
            id=entry_id,
            image_path=new_image,  # type: ignore[arg-type]
            text=new_text,  # type: ignore[arg-type]
            speaker=new_speaker,  # type: ignore[arg-type]
            tags=new_tags,
        )
        return True

    def remove(self, entry_id) -> bool:
        return self._entries.pop(entry_id, None) is not None


class FakeVectorStore:
    """内存 VectorStore，实现真接口。"""

    def __init__(self) -> None:
        self._vecs: dict[int, list[float]] = {}
        self.upsert_error_for: set[int] | None = None  # 触发这些 id 的 upsert 抛错

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def count(self) -> int:
        return len(self._vecs)

    async def upsert(self, entry_id, embedding) -> None:
        if self.upsert_error_for is not None and entry_id in self.upsert_error_for:
            raise RuntimeError(f"upsert failed for {entry_id}")
        self._vecs[entry_id] = list(embedding)

    async def remove(self, entry_id) -> None:
        self._vecs.pop(entry_id, None)

    async def remove_many(self, entry_ids) -> None:
        for i in entry_ids:
            self._vecs.pop(i, None)

    async def query(self, query_embedding: list[float], n_results: int | None = 10) -> list[VectorHit]:
        sims = [
            (eid, sum(a * b for a, b in zip(query_embedding, vec)))
            for eid, vec in self._vecs.items()
        ]
        sims.sort(key=lambda x: -x[1])
        if n_results is None:
            return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims]
        return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims[:n_results]]

    async def rebuild_all(self, items) -> None:
        self._vecs = {eid: list(vec) for eid, vec in items}

    async def get_all_ids(self) -> set[int]:
        return set(self._vecs.keys())

    def has(self, entry_id) -> bool:
        return entry_id in self._vecs


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
    metadata_store = FakeMetadataStore()
    vector_store = FakeVectorStore()
    keyword_searcher = KeywordSearcher(metadata_store)
    embedding_provider = MockEmbeddingProvider()
    ai_matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
    )
    random_searcher = RandomSearcher(metadata_store, keyword_searcher)
    semantic_searcher = SemanticSearcher(metadata_store, vector_store)
    from bot.engine.combined_searcher import CombinedSearcher
    combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        ocr_provider=MockOcrProvider(),
        embedding_provider=embedding_provider,
        optimizer=MockOptimizer(),
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        random_searcher=random_searcher,
        semantic_searcher=semantic_searcher,
        combined_searcher=combined_searcher,
    )
    asyncio.run(manager.load())
    return manager


# ---------------------------------------------------------------------------
# 数据类与异常
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_sync_result(self) -> None:
        r = SyncResult(added=3, deleted=1, failed=["bad.jpg"])
        assert r.added == 3 and r.deleted == 1 and r.deduped == 0
        assert r.no_text_moved == 0 and r.failed == ["bad.jpg"]

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
        m = IndexManager(metadata_store=md, vector_store=vs, memes_dir=str(tmp_path))
        asyncio.run(m.load())
        assert m.entry_count == 0

    def test_entry_count_reflects_store(self, index_manager: IndexManager) -> None:
        index_manager._metadata_store.add("a.jpg", "甲")  # type: ignore[attr-defined]
        assert index_manager.entry_count == 1


# ---------------------------------------------------------------------------
# 新并发 API 核心测试
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_returns_add_result(index_manager: IndexManager) -> None:
    (Path(index_manager._memes_dir) / "test.jpg").write_bytes(b"fake")
    result = await index_manager.add("test.jpg")
    assert result.reason == "added"
    assert result.entry_id is not None


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_refresh_rejects_pending_add(index_manager: IndexManager) -> None:
    # 通过 monkeypatch _process_image_pipeline 使其挂住，模拟管道阻塞
    original = index_manager._process_image_pipeline
    started = asyncio.Event()

    async def slow_pipeline(filename: str) -> tuple[str, str, list[float]]:
        started.set()
        await asyncio.sleep(10)
        return await original(filename)

    index_manager._process_image_pipeline = slow_pipeline

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


@pytest.mark.anyio
async def test_refresh_rejects_new_add(index_manager: IndexManager) -> None:
    # 让 refresh 长期持有写锁
    original = index_manager._run_sync_internal

    async def slow_refresh() -> SyncResult:
        await asyncio.sleep(10)
        return await original()

    index_manager._run_sync_internal = slow_refresh

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


@pytest.mark.anyio
async def test_search_holds_read_lock(index_manager: IndexManager) -> None:
    # 先 add 一条
    (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
    await index_manager.add("cat.jpg")

    results = await index_manager.search("猫")
    assert isinstance(results, list)


@pytest.mark.anyio
async def test_close_cancels_pending_add(index_manager: IndexManager) -> None:
    entered = asyncio.Event()

    original = index_manager._process_image_pipeline

    async def slow_pipeline(filename: str) -> tuple[str, str, list[float]]:
        entered.set()
        await asyncio.sleep(10)
        return await original(filename)

    index_manager._process_image_pipeline = slow_pipeline

    (Path(index_manager._memes_dir) / "pending.jpg").write_bytes(b"fake")
    task = asyncio.create_task(index_manager.add("pending.jpg"))
    await entered.wait()

    await index_manager.close()

    with pytest.raises(IndexAddCancelledError):
        await task


@pytest.mark.anyio
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


@pytest.mark.anyio
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

    index_manager._run_sync_internal = hanging_sync

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

    @pytest.mark.anyio
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
        assert entry.tags == ["吐槽"]

    @pytest.mark.anyio
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
        result = await index_manager.add(
            "new.jpg", speaker="新说话人", tags=["新标签"]
        )
        entry = index_manager._metadata_store.get_entry(1)
        assert entry is not None
        assert entry.speaker == "新说话人"
        assert entry.tags == ["新标签"]

        # 验证旧图已被归档到 memes_replaced/
        replaced_dir = Path(index_manager._replaced_dir)
        assert (replaced_dir / "old.jpg").exists()
        # 验证新图仍保留在 memes/
        assert (Path(index_manager._memes_dir) / "new.jpg").exists()
        # 验证 AddResult 携带归档路径
        assert result.archived_path == str(replaced_dir / "old.jpg")

    @pytest.mark.anyio
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
        # 通过手动调用 _move_to_replaced 模拟同名冲突
        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"3")
        archived = await asyncio.to_thread(
            index_manager._move_to_replaced, "old.jpg"
        )
        assert archived == str(replaced_dir / "old_1.jpg")
        assert (replaced_dir / "old_1.jpg").exists()

    @pytest.mark.anyio
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
        assert entry.tags == ["旧标签"]

        # 验证旧图仍在 memes/（upsert 失败时不应移动旧图），新图应已被清理
        assert (Path(index_manager._memes_dir) / "old.jpg").exists()
        assert not (Path(index_manager._memes_dir) / "new.jpg").exists()

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_edit_text_entry_not_found(self, index_manager: IndexManager) -> None:
        """entry_id 不存在 → ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await index_manager.edit_text(999, "新文本")

    @pytest.mark.anyio
    async def test_edit_text_duplicate_text(self, index_manager: IndexManager) -> None:
        """text 被其他条目使用 → DuplicateTextError。"""
        (Path(index_manager._memes_dir) / "a.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "b.jpg").write_bytes(b"fake")
        r1 = await index_manager.add("a.jpg")
        r2 = await index_manager.add("b.jpg")
        assert r1.entry_id is not None and r2.entry_id is not None

        with pytest.raises(DuplicateTextError, match="已被 entry_id="):
            await index_manager.edit_text(r2.entry_id, "a")  # "a" 是 a.jpg 的 OCR 文本

    @pytest.mark.anyio
    async def test_edit_text_refresh_active(self, index_manager: IndexManager) -> None:
        """refresh 进行中 → RefreshInProgressError。"""
        index_manager._refresh_active = True

        with pytest.raises(RefreshInProgressError):
            await index_manager.edit_text(1, "新文本")

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

        index_manager._embedding_provider.embed = embed_and_activate  # type: ignore[method-assign]

        with pytest.raises(RefreshInProgressError):
            await index_manager.edit_text(eid, "加班")

    @pytest.mark.anyio
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

        index_manager._embedding_provider.embed = embed_and_shutdown  # type: ignore[method-assign]

        with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
            await index_manager.edit_text(eid, "新文本")


# ---------------------------------------------------------------------------
# IndexManager.set_speaker()
# ---------------------------------------------------------------------------


class TestSetSpeaker:
    """IndexManager.set_speaker() 单元测试。"""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_set_speaker_entry_not_found(
        self, index_manager: IndexManager
    ) -> None:
        """entry_id 不存在 → ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await index_manager.set_speaker(999, "张三")

    @pytest.mark.anyio
    async def test_set_speaker_refresh_active(
        self, index_manager: IndexManager
    ) -> None:
        """refresh 进行中 → RefreshInProgressError。"""
        index_manager._refresh_active = True
        with pytest.raises(RefreshInProgressError):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.anyio
    async def test_set_speaker_shutting_down(self, index_manager: IndexManager) -> None:
        """shutting_down → IndexAddCancelledError。"""
        index_manager._shutting_down = True
        with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.anyio
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

        store.get_entry = get_entry_and_delete  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="不存在"):
            await index_manager.set_speaker(eid, "张三")


# ---------------------------------------------------------------------------
# 并发控制与 Write Queue drain 行为测试
# ---------------------------------------------------------------------------


class TestConcurrencyAndDrain:
    """IndexManager 并发控制与 Write Queue drain 行为测试。"""

    @pytest.mark.anyio
    async def test_add_direct_pipeline(self, index_manager: IndexManager) -> None:
        """add() 直接调用 _process_image_pipeline（mock 验证调用一次）。"""
        call_count = 0
        original = index_manager._process_image_pipeline

        async def counting_pipeline(filename: str) -> tuple[str, str, list[float]]:
            nonlocal call_count
            call_count += 1
            return await original(filename)

        index_manager._process_image_pipeline = counting_pipeline

        (Path(index_manager._memes_dir) / "test.jpg").write_bytes(b"fake")
        result = await index_manager.add("test.jpg")
        assert result.entry_id is not None
        assert call_count == 1

    @pytest.mark.anyio
    async def test_refresh_drains_write_queue(self, index_manager: IndexManager) -> None:
        """refresh 等待 write_queue 排空后才获取写锁。"""
        original_write = index_manager._write_entry
        in_flight = asyncio.Event()

        async def slow_write(
            filename: str,
            text: str,
            embedding: list[float],
            speaker: str | None = None,
            tags: list[str] | None = None,
        ) -> AddResult:
            in_flight.set()
            await asyncio.sleep(0.3)
            return await original_write(filename, text, embedding, speaker, tags)

        index_manager._write_entry = slow_write

        (Path(index_manager._memes_dir) / "a.jpg").write_bytes(b"fake")
        task_a = asyncio.create_task(index_manager.add("a.jpg"))
        await in_flight.wait()

        # Worker 正在处理 a.jpg，此时入队 b.jpg
        (Path(index_manager._memes_dir) / "b.jpg").write_bytes(b"fake")
        task_b = asyncio.create_task(index_manager.add("b.jpg"))
        await asyncio.sleep(0.02)

        # refresh 应观察到非空队列，等待 drain
        refresh_task = asyncio.create_task(index_manager.refresh())
        await asyncio.sleep(0.05)
        assert not refresh_task.done(), "refresh 应在等待 write_queue drain"

        await asyncio.wait_for(refresh_task, timeout=5.0)

    @pytest.mark.anyio
    async def test_write_queue_empty_no_wait(self, index_manager: IndexManager) -> None:
        """write_queue 为空时 refresh 不等待 drain。"""
        assert index_manager._write_queue.empty()
        result = await index_manager.refresh()
        assert isinstance(result, SyncResult)

    @pytest.mark.anyio
    async def test_write_worker_drain_signal(self, index_manager: IndexManager) -> None:
        """Write Worker 处理完最后一条后 _write_drained.set()。"""
        index_manager._write_drained.clear()
        (Path(index_manager._memes_dir) / "drain.jpg").write_bytes(b"fake")
        await index_manager.add("drain.jpg")
        await asyncio.sleep(0.02)
        assert index_manager._write_drained.is_set()

    @pytest.mark.anyio
    async def test_no_concurrency_params_in_init(self) -> None:
        """IndexManager 不再接受 sync_concurrency 参数。"""
        from bot.engine.index_manager import IndexManager

        md = FakeMetadataStore()
        vs = FakeVectorStore()
        m = IndexManager(metadata_store=md, vector_store=vs, memes_dir="/tmp/memes")
        assert not hasattr(m, "_add_concurrency")
        assert not hasattr(m, "_sync_semaphore")

    @pytest.mark.anyio
    async def test_deleted_attrs_not_present(
        self, index_manager: IndexManager
    ) -> None:
        """验证已删除属性不存在（_add_concurrency, _sync_semaphore 等）。"""
        assert not hasattr(index_manager, "_add_concurrency")
        assert not hasattr(index_manager, "_sync_semaphore")


class TestRefresh:
    """IndexManager.refresh() 去重归档测试。"""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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


@pytest.mark.anyio
async def test_scan_meme_files_called_once_per_sync(
    index_manager: IndexManager,
) -> None:
    """sync 内 _scan_meme_files 仅调用一次（phase1/phase2 复用同一快照）。"""
    call_count = 0
    original = index_manager._scan_meme_files

    def counting_scan() -> set[str]:
        nonlocal call_count
        call_count += 1
        return original()

    index_manager._scan_meme_files = counting_scan  # type: ignore[assignment]
    await index_manager.refresh()
    assert call_count == 1


# ---------------------------------------------------------------------------
# random_search / semantic_search 测试
# ---------------------------------------------------------------------------


class TestRandomSearch:
    @pytest.mark.anyio
    async def test_random_search_full_random(
        self, index_manager: IndexManager
    ) -> None:
        """无关键词时从全库随机返回候选。"""
        for i in range(5):
            (Path(index_manager._memes_dir) / f"img{i}.jpg").write_bytes(b"fake")
            await index_manager.add(f"img{i}.jpg")

        results = await index_manager.random_search(None)
        assert len(results) == 5
        assert len({r.entry_id for r in results}) == 5

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_random_search_keyword_no_match(
        self, index_manager: IndexManager
    ) -> None:
        """关键词无匹配时返回空列表。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")

        results = await index_manager.random_search("火星文")
        assert results == []

    @pytest.mark.anyio
    async def test_random_search_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        results = await index_manager.random_search(None)
        assert results == []

    @pytest.mark.anyio
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
    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_semantic_search_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        results = await index_manager.semantic_search("任意描述")
        assert results == []

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_semantic_search_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 SemanticSearcher 时抛 RuntimeError。"""
        index_manager._semantic_searcher = None
        with pytest.raises(RuntimeError, match="SemanticSearcher 未注入"):
            await index_manager.semantic_search("任意描述")


class TestCombinedSearch:
    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_search_combined_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        assert await index_manager.search_combined("加班", [], []) == []

    @pytest.mark.anyio
    async def test_search_combined_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 CombinedSearcher 时抛 RuntimeError。"""
        index_manager._metadata_store.add("a.jpg", "加班", speaker="小明")
        index_manager._combined_searcher = None
        with pytest.raises(RuntimeError, match="CombinedSearcher 未注入"):
            await index_manager.search_combined("加班", ["小明"], [])


# ---------------------------------------------------------------------------
# F8: _get_chroma_ids 改用 get_all_ids
# ---------------------------------------------------------------------------


@pytest.mark.anyio
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
        query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]:
        raise AssertionError("_get_chroma_ids 不应再调用 query")

    vs.query = spy_query  # type: ignore[method-assign]

    ids = await index_manager._get_chroma_ids()
    assert ids == {1, 2, 42}


# ---------------------------------------------------------------------------
# F6: add() 内部超时取消入队 future + worker 跳过 done future
# ---------------------------------------------------------------------------


@pytest.mark.anyio
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
    ) -> AddResult:
        await hang_event.wait()
        raise AssertionError("不应走到这里")

    index_manager._write_entry = hanging_write  # type: ignore[method-assign]

    with pytest.raises(asyncio.TimeoutError):
        await index_manager.add("x.jpg")

    # 清理挂住的 worker
    await index_manager.close()


@pytest.mark.anyio
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
        future=cancelled_future,  # type: ignore[arg-type]
        filename="skip.jpg",
        text="t",
        embedding=[1.0],
    )

    # 正常 future，应被 worker 处理
    normal_future: asyncio.Future[AddResult] = loop.create_future()
    normal_req = _WriteRequest(
        op=WriteOp.ADD,
        future=normal_future,  # type: ignore[arg-type]
        filename="normal.jpg",
        text="hello",
        embedding=[1.0],
    )

    # spy _write_entry：记录调用，返回 dummy AddResult
    write_calls: list[str] = []

    async def spy_write(
        filename: str,
        text: str,
        embedding: list[float],
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> AddResult:
        write_calls.append(filename)
        return AddResult(entry_id=1, reason="added", text=text)

    index_manager._write_entry = spy_write  # type: ignore[method-assign]

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

    @pytest.mark.anyio
    async def test_pipeline_uses_output_path(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.webp"))
        im = IndexManager(
            md,
            vs,
            str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.webp"
        assert text == "hello"

    @pytest.mark.anyio
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
            md,
            vs,
            str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.jpg"

    @pytest.mark.anyio
    async def test_pipeline_degrades_on_optimize_error(
        self, tmp_path: Path
    ) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(raises=RuntimeError("convert fail"))
        im = IndexManager(
            md,
            vs,
            str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.jpg"
        assert text == "hello"

    @pytest.mark.anyio
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
            md,
            vs,
            str(memes),
            ocr_provider=FakeOcrProvider(""),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
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

    @pytest.mark.anyio
    async def test_add_writes_webp_image_path(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "meme_001.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "meme_001.webp"))
        im = IndexManager(
            md,
            vs,
            str(memes),
            ocr_provider=FakeOcrProvider("加班"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        result = await im.add("meme_001.jpg", speaker="小明", tags=["吐槽"])
        assert result.reason == "added"
        assert result.entry_id is not None
        entry = md.get_entry(result.entry_id)
        assert entry is not None
        assert entry.image_path == "meme_001.webp"

    @pytest.mark.anyio
    async def test_add_degrades_to_original_format(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "meme_002.png").write_bytes(b"x")
        opt = FakeOptimizer(raises=RuntimeError("fail"))
        im = IndexManager(
            md,
            vs,
            str(memes),
            ocr_provider=FakeOcrProvider("心累"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
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

    @pytest.mark.anyio
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
            md,
            vs,
            str(memes),
            ocr_provider=PerFileOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=PerFileOptimizer(),
        )
        sync_result = await im.refresh()
        assert sync_result.added == 2
        paths = {e.image_path for e in md.get_all_entries().values()}
        assert paths == {"a.webp", "b.webp"}

    @pytest.mark.anyio
    async def test_sync_dedups_same_stem_final_filename(
        self, tmp_path: Path
    ) -> None:
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
                return OptimizeResult(
                    100, 80, 20, output_path=str(memes / "dup.webp")
                )

        im = IndexManager(
            md,
            vs,
            str(memes),
            ocr_provider=CountingOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=SameStemOptimizer(),
        )
        sync_result = await im.refresh()
        assert sync_result.added == 2
        paths = {e.image_path for e in md.get_all_entries().values()}
        assert "dup.webp" in paths
        assert len(paths) == 2
