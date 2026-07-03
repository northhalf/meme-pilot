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
    resolve_unique_filename,
)
from bot.engine.image_optimizer import OptimizeResult
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry

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
        self, entry_id, *, image_path=None, text=None, speaker=None, tags=None
    ) -> bool:
        e = self._entries.get(entry_id)
        if e is None:
            return False
        new_image = image_path if image_path is not None else e.image_path
        new_text = text if text is not None else e.text
        new_speaker = speaker if speaker is not None else e.speaker
        new_tags = tags if tags is not None else e.tags
        self._entries[entry_id] = MemeEntry(
            id=entry_id,
            image_path=new_image,
            text=new_text,
            speaker=new_speaker,
            tags=new_tags,
        )
        return True

    def remove(self, entry_id) -> bool:
        return self._entries.pop(entry_id, None) is not None


class FakeVectorStore:
    """内存 VectorStore，实现真接口。"""

    def __init__(self) -> None:
        self._vecs: dict[int, list[float]] = {}
        self.upsert_error_for: int | None = None  # 触发某 id upsert 抛错

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def count(self) -> int:
        return len(self._vecs)

    async def upsert(self, entry_id, embedding) -> None:
        if self.upsert_error_for == entry_id:
            raise RuntimeError(f"upsert failed for {entry_id}")
        self._vecs[entry_id] = list(embedding)

    async def remove(self, entry_id) -> None:
        self._vecs.pop(entry_id, None)

    async def remove_many(self, entry_ids) -> None:
        for i in entry_ids:
            self._vecs.pop(i, None)

    async def query(self, query_embedding, n_results=10) -> list:
        from bot.engine.vector_store import VectorHit

        sims = [
            (eid, sum(a * b for a, b in zip(query_embedding, vec)))
            for eid, vec in self._vecs.items()
        ]
        sims.sort(key=lambda x: -x[1])
        return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims[:n_results]]

    async def rebuild_all(self, items) -> None:
        self._vecs = {eid: list(vec) for eid, vec in items}

    def has(self, entry_id) -> bool:
        return entry_id in self._vecs


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------


class MockOcrProvider:
    """OCR provider mock：固定返回传入文件名（不含扩展名）的文本。"""

    async def ocr(self, image_path: str) -> str:
        return Path(image_path).stem


class MockEmbeddingProvider:
    """Embedding provider mock：返回固定维度向量。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, text: str) -> list[float]:
        return [float(ord(text[0]) if text else 0)] * self._dim


class MockOptimizer:
    """图片优化器 mock：不做任何操作。"""

    async def optimize(self, image_path: str) -> OptimizeResult:
        _ = image_path
        return OptimizeResult(original_size=0, optimized_size=0, saved=0, skipped=True)


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

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        ocr_provider=MockOcrProvider(),
        embedding_provider=embedding_provider,
        optimizer=MockOptimizer(),
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        sync_concurrency=1,
    )
    manager.load()
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
        assert p == tmp_path / "a_2.jpg"


# ---------------------------------------------------------------------------
# load / entry_count
# ---------------------------------------------------------------------------


class TestLoadAndCount:
    def test_load_delegates_to_stores(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        m = IndexManager(metadata_store=md, vector_store=vs, memes_dir=str(tmp_path))
        m.load()
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
    from typing import cast

    metadata_store = cast(FakeMetadataStore, index_manager._metadata_store)
    assert metadata_store.add_order == ids


@pytest.mark.anyio
async def test_refresh_rejects_pending_add(index_manager: IndexManager) -> None:
    # 通过 monkeypatch _process_image_pipeline 使其挂住，保证 in_flight > 0
    original = index_manager._process_image_pipeline
    started = asyncio.Event()

    async def slow_pipeline(filename: str) -> tuple[str, list[float]]:
        started.set()
        await asyncio.sleep(10)
        return await original(filename)

    index_manager._process_image_pipeline = slow_pipeline

    (Path(index_manager._memes_dir) / "hold.jpg").write_bytes(b"fake")
    add_task = asyncio.create_task(index_manager.add("hold.jpg"))
    await started.wait()

    # 提交第二个 add，它会在 pending 队列中
    (Path(index_manager._memes_dir) / "drop.jpg").write_bytes(b"fake")
    pending_task = asyncio.create_task(index_manager.add("drop.jpg"))
    await asyncio.sleep(0.05)

    # 触发 refresh
    refresh_task = asyncio.create_task(index_manager.refresh())
    with pytest.raises(RefreshInProgressError):
        await pending_task

    add_task.cancel()
    try:
        await add_task
    except (asyncio.CancelledError, IndexAddCancelledError):
        pass
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

    async def slow_pipeline(filename: str) -> tuple[str, list[float]]:
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
    async def test_edit_text_refresh_pending(self, index_manager: IndexManager) -> None:
        """refresh pending 中 → RefreshInProgressError。"""
        index_manager._refresh_pending = True

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
        vs.upsert_error_for = eid

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
