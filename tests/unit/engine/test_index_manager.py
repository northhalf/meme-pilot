"""IndexManager 薄编排单元测试。

用 FakeMetadataStore / FakeVectorStore（内存实现真接口）+ mock providers，
端到端验证 sync 四阶段与 add_single_file，无真实 sqlite/chroma I/O。
"""

import asyncio
from pathlib import Path

import pytest

from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    EmbeddingError,
    IndexCorruptedError,
    IndexManager,
    OcrError,
    SyncResult,
    resolve_unique_filename,
)
from bot.engine.image_optimizer import OptimizeResult
from bot.engine.metadata_store import MemeEntry


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


class FakeMetadataStore:
    """内存 MetadataStore，实现真接口，用于 IndexManager 测试。"""

    def __init__(self) -> None:
        self._entries: dict[int, MemeEntry] = {}
        self._next_auto = 1

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
        self._entries[eid] = MemeEntry(id=eid, image_path=image_path, text=text, speaker=speaker, tags=tags or [])
        return eid

    def add_with_id(self, entry_id, image_path, text, speaker=None, tags=None) -> int:
        self._entries[entry_id] = MemeEntry(id=entry_id, image_path=image_path, text=text, speaker=speaker, tags=tags or [])
        return entry_id

    def update(self, entry_id, *, image_path=None, text=None, speaker=None, tags=None) -> bool:
        e = self._entries.get(entry_id)
        if e is None:
            return False
        new_image = image_path if image_path is not None else e.image_path
        new_text = text if text is not None else e.text
        new_speaker = speaker if speaker is not None else e.speaker
        new_tags = tags if tags is not None else e.tags
        self._entries[entry_id] = MemeEntry(id=entry_id, image_path=new_image, text=new_text, speaker=new_speaker, tags=new_tags)
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
        sims = [(eid, sum(a * b for a, b in zip(query_embedding, vec))) for eid, vec in self._vecs.items()]
        sims.sort(key=lambda x: -x[1])
        return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims[:n_results]]

    async def rebuild_all(self, items) -> None:
        self._vecs = {eid: list(vec) for eid, vec in items}

    def count(self) -> int:
        return len(self._vecs)

    def has(self, entry_id) -> bool:
        return entry_id in self._vecs


class MockOcr:
    def __init__(self, texts: dict[str, str] | None = None, default: str = "文字") -> None:
        self._texts = texts or {}
        self._default = default

    async def ocr(self, image_path: str) -> str:
        name = Path(image_path).name
        return self._texts.get(name, self._default)


class MockEmbed:
    def __init__(self, error_on: str | None = None) -> None:
        self._error_on = error_on
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._error_on is not None and text == self._error_on:
            raise RuntimeError("embed failed")
        # 用 text 长度生成确定性向量，便于测试稳定
        return [float(len(text)), 0.0]


class MockOptimizer:
    async def optimize(self, image_path) -> OptimizeResult:
        return OptimizeResult(0, 0, 0, skipped=True)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def work(tmp_path: Path) -> dict[str, Path]:
    memes = tmp_path / "memes"
    no_text = tmp_path / "meme_no_text"
    memes.mkdir()
    return {"memes": memes, "no_text": no_text, "data": tmp_path / "data"}


@pytest.fixture
def manager(work: dict[str, Path]) -> IndexManager:
    md = FakeMetadataStore()
    vs = FakeVectorStore()
    m = IndexManager(
        metadata_store=md,
        vector_store=vs,
        memes_dir=str(work["memes"]),
        no_text_dir=str(work["no_text"]),
        ocr_provider=MockOcr(),
        embedding_provider=MockEmbed(),
        optimizer=MockOptimizer(),
    )
    m.load()
    return m


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
# load / 锁 / entry_count
# ---------------------------------------------------------------------------


class TestLoadAndLock:
    def test_load_delegates_to_stores(self, work: dict[str, Path]) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        m = IndexManager(metadata_store=md, vector_store=vs, memes_dir=str(work["memes"]))
        m.load()
        assert m.entry_count == 0

    def test_entry_count_reflects_store(self, manager: IndexManager) -> None:
        manager._metadata_store.add("a.jpg", "甲")  # type: ignore[attr-defined]
        assert manager.entry_count == 1

    @pytest.mark.asyncio
    async def test_acquire_lock(self, manager: IndexManager) -> None:
        assert await manager.acquire_lock() is True
        assert manager.is_locked is True
        assert await manager.acquire_lock() is False

    @pytest.mark.asyncio
    async def test_release_lock(self, manager: IndexManager) -> None:
        await manager.acquire_lock()
        manager.release_lock()
        assert manager.is_locked is False

    def test_release_when_not_locked_safe(self, manager: IndexManager) -> None:
        manager.release_lock()
        assert manager.is_locked is False


# ---------------------------------------------------------------------------
# sync 阶段0 一致性修复
# ---------------------------------------------------------------------------


class TestSyncPhase0Consistency:
    @pytest.mark.asyncio
    async def test_orphan_vector_removed(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """chroma 有 id、sqlite 无 → 删孤儿向量。"""
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        await vs.add_with_id if False else None
        # 直接塞一条孤儿向量（sqlite 无对应 entry）
        vs._vecs[99] = [1.0, 0.0]
        result = await manager.sync_with_filesystem()
        assert not vs.has(99)
        # 没有图片新增/删除
        assert result.added == 0 and result.deleted == 0

    @pytest.mark.asyncio
    async def test_missing_vector_re_embedded(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """sqlite 有 id、chroma 无 → 按 sqlite text 重 embed upsert。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        # sqlite 有条目，chroma 空
        md._entries[1] = MemeEntry(id=1, image_path="a.jpg", text="猫")
        # memes/ 里要有对应图，否则阶段1 会删它
        (work["memes"] / "a.jpg").write_text("x")
        result = await manager.sync_with_filesystem()
        assert vs.has(1)
        assert result.added == 0  # 不是新增，是阶段0 修复

    @pytest.mark.asyncio
    async def test_chroma_empty_rebuild_all(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """chroma 完全为空、sqlite 有数据 → rebuild_all 全量重建。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="a.jpg", text="猫")
        md._entries[2] = MemeEntry(id=2, image_path="b.jpg", text="狗")
        (work["memes"] / "a.jpg").write_text("x")
        (work["memes"] / "b.jpg").write_text("x")
        await manager.sync_with_filesystem()
        assert vs.count() == 2
        assert vs.has(1) and vs.has(2)


# ---------------------------------------------------------------------------
# sync 阶段1 删除
# ---------------------------------------------------------------------------


class TestSyncPhase1Delete:
    @pytest.mark.asyncio
    async def test_delete_removed_image(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """图片已删、索引还在 → sqlite + chroma 都删。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="gone.jpg", text="猫")
        vs._vecs[1] = [1.0, 0.0]
        # memes/ 无 gone.jpg
        result = await manager.sync_with_filesystem()
        assert result.deleted == 1
        assert md.get_entry(1) is None
        assert not vs.has(1)


# ---------------------------------------------------------------------------
# sync 阶段2 新增
# ---------------------------------------------------------------------------


class TestSyncPhase2Add:
    @pytest.mark.asyncio
    async def test_add_new_image(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """新图 OCR 有文字 → 进 sqlite + chroma。"""
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="新文字")  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.added == 1
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        assert md.entry_count() == 1
        assert vs.count() == 1

    @pytest.mark.asyncio
    async def test_no_text_moved(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """OCR 无文字 → 移到 meme_no_text/，不进索引。"""
        (work["memes"] / "blank.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="   ")  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.no_text_moved == 1
        assert result.added == 0
        assert not (work["memes"] / "blank.jpg").exists()
        assert (work["no_text"] / "blank.jpg").exists()

    @pytest.mark.asyncio
    async def test_dedup_new_image_same_text(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """新图 text 命中已有条目 → 删新图，deduped++。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="old.jpg", text="重复文字")
        (work["memes"] / "old.jpg").write_text("x")
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="重复文字")  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.deduped == 1
        assert result.added == 0
        # 新图被删，旧图保留
        assert not (work["memes"] / "new.jpg").exists()
        assert (work["memes"] / "old.jpg").exists()

    @pytest.mark.asyncio
    async def test_idempotent_second_sync(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """重复同步无变化。"""
        (work["memes"] / "a.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="文字")  # type: ignore[attr-defined]
        r1 = await manager.sync_with_filesystem()
        assert r1.added == 1
        r2 = await manager.sync_with_filesystem()
        assert r2.added == 0 and r2.deleted == 0 and r2.deduped == 0

    @pytest.mark.asyncio
    async def test_ocr_failure_recorded(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """单图 OCR 失败 → 记 failed，其他图继续。"""
        (work["memes"] / "bad.jpg").write_text("x")
        (work["memes"] / "good.jpg").write_text("x")
        manager._ocr_provider = MockOcr(texts={"bad.jpg": ""}, default="正常")  # type: ignore[attr-defined]
        # 让 bad.jpg 的 OCR 抛错：用自定义 provider
        class FailOcr:
            async def ocr(self, image_path: str) -> str:
                if Path(image_path).name == "bad.jpg":
                    raise RuntimeError("ocr down")
                return "正常"
        manager._ocr_provider = FailOcr()  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.added == 1
        assert "bad.jpg" in result.failed

    @pytest.mark.asyncio
    async def test_empty_memes_dir(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """memes/ 为空 → 无变化。"""
        result = await manager.sync_with_filesystem()
        assert result.added == 0 and result.deleted == 0 and result.failed == []


# ---------------------------------------------------------------------------
# add_single_file
# ---------------------------------------------------------------------------


class TestAddSingleFile:
    @pytest.mark.asyncio
    async def test_added(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """正常新增。"""
        (work["memes"] / "pic.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="新文字")  # type: ignore[attr-defined]
        result = await manager.add_single_file("pic.jpg")
        assert result.entry_id == 1
        assert result.reason == "added"
        assert result.text == "新文字"
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        assert md.entry_count() == 1
        assert vs.count() == 1

    @pytest.mark.asyncio
    async def test_no_text_moved(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """无文字 → 移图，不进索引。"""
        (work["memes"] / "blank.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="   ")  # type: ignore[attr-defined]
        result = await manager.add_single_file("blank.jpg")
        assert result.entry_id is None
        assert result.reason == "no_text"
        assert result.moved_to is not None
        assert (work["no_text"] / "blank.jpg").exists()

    @pytest.mark.asyncio
    async def test_replaced_dedup(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """去重命中已有条目 → update image_path + upsert，删旧图。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="old.jpg", text="重复")
        vs._vecs[1] = [1.0, 0.0]
        (work["memes"] / "old.jpg").write_text("x")
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="重复")  # type: ignore[attr-defined]
        result = await manager.add_single_file("new.jpg")
        assert result.entry_id == 1
        assert result.reason == "replaced"
        assert result.replaced_image_path == "old.jpg"
        # sqlite 指向新图，text 不变
        entry = md.get_entry(1)
        assert entry is not None
        assert entry.image_path == "new.jpg"
        assert entry.text == "重复"
        # 旧图删，新图留
        assert not (work["memes"] / "old.jpg").exists()
        assert (work["memes"] / "new.jpg").exists()

    @pytest.mark.asyncio
    async def test_added_upsert_failure_rolls_back(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """正常新增时 upsert 失败 → 回滚 sqlite + 删图 + 抛 EmbeddingError。"""
        (work["memes"] / "pic.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="新文字")  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        vs.upsert_error_for = 1  # 让 id=1 的 upsert 抛错
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        with pytest.raises(EmbeddingError):
            await manager.add_single_file("pic.jpg")
        # sqlite 回滚
        assert md.entry_count() == 0
        # 图片删除
        assert not (work["memes"] / "pic.jpg").exists()

    @pytest.mark.asyncio
    async def test_replaced_upsert_failure_rolls_back(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """去重替换时 upsert 失败 → 回滚 update(image_path=旧) + 删新图，旧图保留。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="old.jpg", text="重复")
        vs._vecs[1] = [1.0, 0.0]
        (work["memes"] / "old.jpg").write_text("x")
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="重复")  # type: ignore[attr-defined]
        vs.upsert_error_for = 1
        with pytest.raises(EmbeddingError):
            await manager.add_single_file("new.jpg")
        # 回滚：sqlite 仍指向旧图
        entry = md.get_entry(1)
        assert entry is not None
        assert entry.image_path == "old.jpg"
        # 新图删，旧图保留
        assert not (work["memes"] / "new.jpg").exists()
        assert (work["memes"] / "old.jpg").exists()

    @pytest.mark.asyncio
    async def test_ocr_failure_raises(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """OCR 失败 → 抛 OcrError。"""
        (work["memes"] / "pic.jpg").write_text("x")
        class FailOcr:
            async def ocr(self, image_path: str) -> str:
                raise RuntimeError("ocr down")
        manager._ocr_provider = FailOcr()  # type: ignore[attr-defined]
        with pytest.raises(OcrError):
            await manager.add_single_file("pic.jpg")
