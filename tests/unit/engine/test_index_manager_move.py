"""IndexManager 移动预览与补偿式移动测试。"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from bot.index_manager import (
    DuplicateMemeInCollectionError as ExportedDuplicateMemeInCollectionError,
)
from bot.index_manager import MemeMoveError as ExportedMemeMoveError
from bot.index_manager import MovePreview as ExportedMovePreview
from bot.index_manager import MoveResult as ExportedMoveResult
from bot.engine.collection_manager import CollectionNotFoundError, MemeNotFoundError
from bot.index_manager import (
    DuplicateMemeInCollectionError,
    IndexManager,
    MemeMoveError,
    MemeMoveSourceExpiredError,
)
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.utils import (
    SecureMoveError,
    get_regular_file_identity,
    secure_move_file,
)
from bot.engine.types import GLOBAL_COLLECTION_NAME, MemePublicId
from bot.engine.vector_store import VectorRecord, VectorStore
from bot.session import ChatScope


def test_move_public_types_are_exported_from_engine_package() -> None:
    assert ExportedDuplicateMemeInCollectionError is DuplicateMemeInCollectionError
    assert ExportedMemeMoveError is MemeMoveError
    assert ExportedMovePreview.__name__ == "MovePreview"
    assert ExportedMoveResult.__name__ == "MoveResult"


@pytest_asyncio.fixture
async def index_manager(tmp_path: Path):
    """创建使用真实 SQLite 与 Chroma 的 IndexManager。"""
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = MetadataStore(str(tmp_path / "index.db"))
    vector_store = VectorStore(str(tmp_path / "chroma"))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    await manager.load()
    try:
        yield manager
    finally:
        await manager.close()


async def _add_entry(
    manager: IndexManager,
    relative_path: str,
    text: str,
    *,
    collection_id: int,
    content: bytes = b"image",
) -> int:
    """向真实 Store 添加带文件与向量的条目。"""
    path = manager._memes_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    entry_id = manager._metadata_store.add(
        relative_path,
        text,
        collection_id=collection_id,
    )
    await manager._vector_store.upsert(
        entry_id,
        [1.0, 0.0],
        collection_id=collection_id,
    )
    return entry_id


async def _assert_old_state(
    manager: IndexManager,
    old_entry: MemeEntry,
    old_vector: VectorRecord,
    *,
    target_relative_path: str,
) -> None:
    """断言 SQLite、Chroma 与文件均恢复为移动前快照。"""
    assert manager._metadata_store.get_entry(old_entry.id) == old_entry
    assert (manager._memes_dir / old_entry.image_path).read_bytes() == b"source"
    assert not (manager._memes_dir / target_relative_path).exists()
    assert await manager._vector_store.snapshot_records([old_entry.id]) == [old_vector]


@pytest.mark.asyncio
async def test_prepare_move_resolves_source_target_and_identity_atomically(
    index_manager: IndexManager,
) -> None:
    """原子准备应返回源业务快照与文件身份。"""
    source = index_manager._metadata_store.create_collection("新三国")
    target = index_manager._metadata_store.create_collection("甄嬛传")
    entry_id = await _add_entry(
        index_manager,
        "新三国/截图/a.webp",
        "文本",
        collection_id=source.id,
    )
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)

    preview = await index_manager.prepare_move(scope, "1", target.name)

    assert preview.entry_id == entry_id
    assert preview.old_public_id == MemePublicId(source.id, 1)
    assert preview.target_collection_id == target.id
    assert preview.source_snapshot is not None
    assert preview.source_snapshot.entry == index_manager._metadata_store.get_entry(
        entry_id
    )
    assert preview.source_snapshot.file_identity == get_regular_file_identity(
        index_manager._memes_dir,
        Path("新三国/截图/a.webp"),
    )


@pytest.mark.asyncio
async def test_prepare_move_holds_one_read_lock_across_resolution_and_preview(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """源目标解析和预览完成前 refresh 不得取得写锁。"""
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    await _add_entry(index_manager, "源/a.webp", "文本", collection_id=source.id)
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)
    import threading

    entered = threading.Event()
    release = threading.Event()
    original_preview = index_manager._preview_move_locked

    def blocking_preview(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original_preview(*args, **kwargs)

    monkeypatch.setattr(index_manager, "_preview_move_locked", blocking_preview)
    prepare_task = asyncio.create_task(
        index_manager.prepare_move(scope, "1", target.name)
    )
    assert await asyncio.to_thread(entered.wait, 1)
    refresh_task = asyncio.create_task(index_manager.refresh())
    await asyncio.sleep(0.02)

    assert not refresh_task.done()
    release.set()
    preview = await prepare_task
    await refresh_task
    assert preview.old_public_id == MemePublicId(source.id, 1)


@pytest.mark.asyncio
async def test_prepare_move_has_domain_errors_for_missing_source_and_same_target(
    index_manager: IndexManager,
) -> None:
    """原子准备应保留公开 ID 与同合集领域异常。"""
    source = index_manager._metadata_store.create_collection("源")
    await _add_entry(index_manager, "源/a.webp", "文本", collection_id=source.id)
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)

    with pytest.raises(MemeNotFoundError):
        await index_manager.prepare_move(scope, "999", "0")
    with pytest.raises(CollectionNotFoundError):
        await index_manager.prepare_move(scope, "1", "999")
    with pytest.raises(ValueError, match="已属于目标合集"):
        await index_manager.prepare_move(scope, "1", source.name)

    (index_manager._memes_dir / "源/a.webp").unlink()
    with pytest.raises(MemeMoveSourceExpiredError):
        await index_manager.prepare_move(scope, "1", "0")


@pytest.mark.asyncio
async def test_prepare_move_returns_estimated_public_id(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("新三国")
    target = index_manager._metadata_store.create_collection("甄嬛传")
    entry_id = await _add_entry(
        index_manager,
        "新三国/截图/a.webp",
        "文本",
        collection_id=source.id,
    )
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)

    preview = await index_manager.prepare_move(scope, "1", target.name)

    assert preview.entry_id == entry_id
    assert preview.old_public_id == MemePublicId(source.id, 1)
    assert preview.source_collection_name == source.name
    assert preview.target_collection_id == target.id
    assert preview.target_collection_name == target.name
    assert preview.expected_public_id == MemePublicId(target.id, 1)


@pytest.mark.asyncio
async def test_move_preserves_internal_id_and_moves_to_collection_root(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("新三国")
    target = index_manager._metadata_store.create_collection("甄嬛传")
    entry_id = await _add_entry(
        index_manager,
        "新三国/截图/a.webp",
        "文本",
        collection_id=source.id,
    )

    result = await index_manager.move(entry_id, target.id)

    assert result.entry_id == entry_id
    assert result.old_public_id == MemePublicId(source.id, 1)
    assert result.new_public_id == MemePublicId(target.id, 1)
    assert result.target_collection_name == target.name
    assert result.old_image_path == "新三国/截图/a.webp"
    assert result.new_image_path == "甄嬛传/a.webp"
    assert not (index_manager._memes_dir / "新三国/截图/a.webp").exists()
    assert (index_manager._memes_dir / "甄嬛传/a.webp").read_bytes() == b"image"
    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.id == entry_id
    assert persisted.image_path == "甄嬛传/a.webp"
    assert persisted.collection_id == target.id
    assert persisted.local_id == 1
    assert await index_manager._vector_store.get_collection_ids() == {
        entry_id: target.id
    }


@pytest.mark.asyncio
async def test_move_to_global_uses_root_and_suffix_starts_at_one(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
    )
    (index_manager._memes_dir / "a.webp").write_bytes(b"occupied")

    result = await index_manager.move(entry_id, 0)

    assert result.new_public_id == MemePublicId(0, 1)
    assert result.target_collection_name == GLOBAL_COLLECTION_NAME
    assert result.new_image_path == "a_1.webp"
    assert (index_manager._memes_dir / "a.webp").read_bytes() == b"occupied"
    assert (index_manager._memes_dir / "a_1.webp").read_bytes() == b"image"


@pytest.mark.asyncio
async def test_move_rejects_duplicate_text_in_target(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("新三国")
    target = index_manager._metadata_store.create_collection("甄嬛传")
    source_id = await _add_entry(
        index_manager,
        "新三国/a.webp",
        "相同",
        collection_id=source.id,
    )
    conflict_id = await _add_entry(
        index_manager,
        "甄嬛传/b.webp",
        "相同",
        collection_id=target.id,
    )

    with pytest.raises(DuplicateMemeInCollectionError) as exc_info:
        await index_manager.move(source_id, target.id)

    assert exc_info.value.conflicting_entry_id == conflict_id
    assert (index_manager._memes_dir / "新三国/a.webp").exists()


@pytest.mark.asyncio
async def test_move_recomputes_local_id_at_execution(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    source_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "源文本",
        collection_id=source.id,
    )
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)
    preview = await index_manager.prepare_move(scope, "1", target.name)
    await _add_entry(
        index_manager,
        "目标/occupied.webp",
        "占位",
        collection_id=target.id,
    )

    result = await index_manager.move(source_id, target.id)

    assert preview.expected_public_id == MemePublicId(target.id, 1)
    assert result.new_public_id == MemePublicId(target.id, 2)


@pytest.mark.asyncio
async def test_move_rejects_reused_internal_and_public_source_identity(
    index_manager: IndexManager,
) -> None:
    """预览后源被删除且内外 ID 均复用时不得移动新条目。"""
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    old_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "旧文本",
        collection_id=source.id,
        content=b"old",
    )
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)
    preview = await index_manager.prepare_move(scope, "1", target.name)

    old_path = index_manager._memes_dir / "源/a.webp"
    old_path.unlink()
    index_manager._metadata_store.remove(old_id)
    await index_manager._vector_store.remove(old_id)
    replacement_id = await _add_entry(
        index_manager,
        "源/b.webp",
        "新文本",
        collection_id=source.id,
        content=b"replacement",
    )
    assert replacement_id == old_id
    replacement = index_manager._metadata_store.get_entry(replacement_id)
    assert replacement is not None
    assert replacement.public_id == preview.old_public_id

    with pytest.raises(MemeMoveSourceExpiredError):
        await index_manager.move(
            preview.entry_id,
            preview.target_collection_id,
            expected_source=preview.source_snapshot,
            expected_target_name=preview.target_collection_name,
        )

    assert index_manager._metadata_store.get_entry(replacement_id) == replacement
    assert (index_manager._memes_dir / "源/b.webp").read_bytes() == b"replacement"
    assert not (index_manager._memes_dir / "目标/b.webp").exists()
    assert await index_manager._vector_store.get_collection_ids() == {
        replacement_id: source.id
    }


@pytest.mark.asyncio
async def test_move_rejects_target_id_reused_by_different_name(
    index_manager: IndexManager,
) -> None:
    """确认后目标编号被不同名称合集复用时不得移动。"""
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("旧目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
    )
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, source.id)
    preview = await index_manager.prepare_move(scope, "1", target.name)
    index_manager._metadata_store.delete_collection_and_reset_scopes(target.id)
    replacement = index_manager._metadata_store.create_collection("新目标")
    assert replacement.id == target.id

    with pytest.raises(CollectionNotFoundError):
        await index_manager.move(
            entry_id,
            target.id,
            expected_target_name=preview.target_collection_name,
        )

    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.collection_id == source.id
    assert (index_manager._memes_dir / "源/a.webp").exists()


@pytest.mark.asyncio
async def test_move_revalidates_target_and_source_collection_in_worker(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
    )
    index_manager._metadata_store.delete_collection_and_reset_scopes(target.id)

    with pytest.raises(CollectionNotFoundError):
        await index_manager.move(entry_id, target.id)

    assert (index_manager._memes_dir / "源/a.webp").exists()
    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.collection_id == source.id


@pytest.mark.asyncio
async def test_move_defensively_rejects_hidden_target_name(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection(".隐藏")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
    )

    with pytest.raises(MemeMoveError, match="目录名不安全"):
        await index_manager.move(entry_id, target.id)

    assert (index_manager._memes_dir / "源/a.webp").exists()
    assert not (index_manager._memes_dir / ".隐藏").exists()


@pytest.mark.asyncio
async def test_move_rejects_source_path_outside_memes(
    index_manager: IndexManager,
    tmp_path: Path,
) -> None:
    target = index_manager._metadata_store.create_collection("目标")
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"outside")
    entry_id = index_manager._metadata_store.add("../outside.webp", "文本")
    await index_manager._vector_store.upsert(entry_id, [1.0, 0.0])

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    assert outside.read_bytes() == b"outside"
    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.image_path == "../outside.webp"


@pytest.mark.asyncio
async def test_move_rejects_source_directory(
    index_manager: IndexManager,
) -> None:
    target = index_manager._metadata_store.create_collection("目标")
    source_directory = index_manager._memes_dir / "not-a-file.webp"
    source_directory.mkdir()
    entry_id = index_manager._metadata_store.add("not-a-file.webp", "文本")
    await index_manager._vector_store.upsert(entry_id, [1.0, 0.0])

    with pytest.raises(MemeMoveError, match="普通文件"):
        await index_manager.move(entry_id, target.id)

    assert source_directory.is_dir()
    assert not (index_manager._memes_dir / "目标").exists()


@pytest.mark.asyncio
async def test_move_rejects_target_directory_replaced_by_symlink(
    index_manager: IndexManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    target_dir = index_manager._memes_dir / "目标"
    target_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original_open = os.open
    replaced = False

    def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if not replaced and path == "目标" and flags & os.O_DIRECTORY:
            replaced = True
            target_dir.rmdir()
            target_dir.symlink_to(outside, target_is_directory=True)
        return fd

    monkeypatch.setattr(os, "open", replacing_open)

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.collection_id == source.id
    assert (index_manager._memes_dir / "源/a.webp").read_bytes() == b"source"
    assert not (outside / "a.webp").exists()
    assert not (outside / "a_2.webp").exists()


@pytest.mark.asyncio
async def test_move_rejects_source_symlink_escape(
    index_manager: IndexManager,
    tmp_path: Path,
) -> None:
    target = index_manager._metadata_store.create_collection("目标")
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"outside")
    link = index_manager._memes_dir / "link.webp"
    link.symlink_to(outside)
    entry_id = index_manager._metadata_store.add("link.webp", "文本")
    await index_manager._vector_store.upsert(entry_id, [1.0, 0.0])

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    assert link.is_symlink()
    assert outside.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_move_rejects_source_inode_replaced_during_vector_snapshot(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"original",
    )
    source_path = index_manager._memes_dir / "源/a.webp"
    vector_store = index_manager._vector_store
    original_snapshot = vector_store.snapshot_records

    async def replace_during_snapshot(entry_ids: list[int]) -> list[VectorRecord]:
        records = await original_snapshot(entry_ids)
        source_path.unlink()
        source_path.write_bytes(b"replacement")
        return records

    monkeypatch.setattr(vector_store, "snapshot_records", replace_during_snapshot)

    with pytest.raises(MemeMoveError, match="身份"):
        await index_manager.move(entry_id, target.id)

    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.collection_id == source.id
    assert persisted.image_path == "源/a.webp"
    assert source_path.read_bytes() == b"replacement"
    assert not (index_manager._memes_dir / "目标/a.webp").exists()
    assert await vector_store.get_collection_ids() == {entry_id: source.id}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["before", "after"])
async def test_move_restores_final_snapshot_when_sqlite_update_fails(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    old_entry = index_manager._metadata_store.get_entry(entry_id)
    assert old_entry is not None
    old_vector = (await index_manager._vector_store.snapshot_records([entry_id]))[0]
    metadata_store = index_manager._metadata_store
    original_update = metadata_store.update
    calls = 0

    def failing_update(
        target_entry_id: int,
        *,
        image_path: str | None = None,
        text: str | None = None,
        speaker: str | None = None,
        tags: list[str] | None = None,
        collection_id: int | None = None,
        local_id: int | None = None,
    ) -> bool:
        nonlocal calls
        calls += 1
        kwargs: dict[str, Any] = {
            "image_path": image_path,
            "text": text,
            "speaker": speaker,
            "tags": tags,
            "collection_id": collection_id,
            "local_id": local_id,
        }
        if calls == 1:
            if failure_mode == "after":
                original_update(target_entry_id, **kwargs)
            raise RuntimeError(f"sqlite {failure_mode}")
        return original_update(target_entry_id, **kwargs)

    monkeypatch.setattr(index_manager._metadata_store, "update", failing_update)

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    await _assert_old_state(
        index_manager,
        old_entry,
        old_vector,
        target_relative_path="目标/a.webp",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["before", "after"])
async def test_move_restores_final_snapshot_when_chroma_update_fails(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    old_entry = index_manager._metadata_store.get_entry(entry_id)
    assert old_entry is not None
    old_vector = (await index_manager._vector_store.snapshot_records([entry_id]))[0]
    original_update = index_manager._vector_store.update_collection_id
    calls = 0

    async def failing_update(entry_id: int, collection_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure_mode == "after":
                await original_update(entry_id, collection_id)
            raise RuntimeError(f"chroma {failure_mode}")
        await original_update(entry_id, collection_id)

    monkeypatch.setattr(
        index_manager._vector_store,
        "update_collection_id",
        failing_update,
    )

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    await _assert_old_state(
        index_manager,
        old_entry,
        old_vector,
        target_relative_path="目标/a.webp",
    )


@pytest.mark.asyncio
async def test_move_restores_file_when_secure_move_succeeds_then_raises(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    old_entry = index_manager._metadata_store.get_entry(entry_id)
    assert old_entry is not None
    old_vector = (await index_manager._vector_store.snapshot_records([entry_id]))[0]
    calls = 0

    def move_then_fail(
        root_dir: Path,
        source_relative_path: Path,
        target_relative_dir: Path,
        *,
        first_suffix: int = 1,
        target_filename: str | None = None,
        expected_source_identity: tuple[int, int, int] | None = None,
    ):
        nonlocal calls
        calls += 1
        result = secure_move_file(
            root_dir,
            source_relative_path,
            target_relative_dir,
            first_suffix=first_suffix,
            target_filename=target_filename,
            expected_source_identity=expected_source_identity,
        )
        if calls == 1:
            raise SecureMoveError(
                "move committed then failed",
                relative_path=result.relative_path,
                target_created=result.target_created,
                source_removed=result.source_removed,
                target_dir_created=result.target_dir_created,
                target_identity=result.target_identity,
            )
        return result

    monkeypatch.setattr(
        "bot.index_manager.write_coordinator.secure_move_file",
        move_then_fail,
    )

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    await _assert_old_state(
        index_manager,
        old_entry,
        old_vector,
        target_relative_path="目标/a.webp",
    )
    assert (index_manager._memes_dir / "目标").is_dir()
    assert list((index_manager._memes_dir / "目标").iterdir()) == []


@pytest.mark.asyncio
async def test_move_compensation_preserves_competitor_and_target_original(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"original",
    )
    source_path = index_manager._memes_dir / "源/a.webp"
    target_path = index_manager._memes_dir / "目标/a.webp"
    vector_store = index_manager._vector_store
    old_vector = (await vector_store.snapshot_records([entry_id]))[0]
    original_unlink = os.unlink
    original_fsync = os.fsync
    source_parent = source_path.parent
    source_parent_identity = (
        source_parent.stat().st_dev,
        source_parent.stat().st_ino,
    )

    def unlink_then_recreate(path, *, dir_fd=None):
        original_unlink(path, dir_fd=dir_fd)
        if path == "a.webp" and dir_fd is not None:
            source_path.write_bytes(b"competitor")

    def fail_source_dir_fsync(fd: int) -> None:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) == source_parent_identity:
            raise OSError("source dir fsync failed")
        original_fsync(fd)

    monkeypatch.setattr(os, "unlink", unlink_then_recreate)
    monkeypatch.setattr(os, "fsync", fail_source_dir_fsync)

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.collection_id == source.id
    assert persisted.image_path == "源/a.webp"
    assert source_path.read_bytes() == b"competitor"
    assert target_path.read_bytes() == b"original"
    assert await vector_store.snapshot_records([entry_id]) == [old_vector]


@pytest.mark.asyncio
async def test_move_compensates_when_persisted_result_read_fails(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    old_entry = index_manager._metadata_store.get_entry(entry_id)
    assert old_entry is not None
    old_vector = (await index_manager._vector_store.snapshot_records([entry_id]))[0]
    original_get_entry = index_manager._metadata_store.get_entry
    calls = 0

    def fail_result_read(target_entry_id: int) -> MemeEntry | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("result read failed")
        return original_get_entry(target_entry_id)

    monkeypatch.setattr(
        index_manager._metadata_store,
        "get_entry",
        fail_result_read,
    )

    with pytest.raises(MemeMoveError):
        await index_manager.move(entry_id, target.id)

    await _assert_old_state(
        index_manager,
        old_entry,
        old_vector,
        target_relative_path="目标/a.webp",
    )


@pytest.mark.asyncio
async def test_cancelled_queued_move_is_skipped(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    first_id = await _add_entry(
        index_manager,
        "源/first.webp",
        "第一条",
        collection_id=source.id,
    )
    second_id = await _add_entry(
        index_manager,
        "源/second.webp",
        "第二条",
        collection_id=source.id,
    )
    original_execute = index_manager._coordinator._execute_move
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def block_first(req):
        if req.entry_id == first_id:
            first_started.set()
            await release_first.wait()
        return await original_execute(req)

    monkeypatch.setattr(index_manager._coordinator, "_execute_move", block_first)
    first_task = asyncio.create_task(index_manager.move(first_id, target.id))
    await first_started.wait()
    second_task = asyncio.create_task(index_manager.move(second_id, target.id))
    await asyncio.sleep(0)

    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second_task, timeout=0.5)
    release_first.set()
    await first_task
    await asyncio.sleep(0)

    second = index_manager._metadata_store.get_entry(second_id)
    assert second is not None
    assert second.collection_id == source.id
    assert (index_manager._memes_dir / "源/second.webp").exists()
    assert not (index_manager._memes_dir / "目标/second.webp").exists()


@pytest.mark.asyncio
async def test_cancel_at_move_start_boundary_has_complete_final_state(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    original_execute = index_manager._coordinator._execute_move
    dequeued = asyncio.Event()
    release = asyncio.Event()

    async def pause_at_start(req):
        dequeued.set()
        await release.wait()
        return await original_execute(req)

    monkeypatch.setattr(index_manager._coordinator, "_execute_move", pause_at_start)
    task = asyncio.create_task(index_manager.move(entry_id, target.id))
    await dequeued.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    source_exists = (index_manager._memes_dir / "源/a.webp").exists()
    target_exists = (index_manager._memes_dir / "目标/a.webp").exists()
    assert source_exists != target_exists
    if persisted.collection_id == source.id:
        assert source_exists
    else:
        assert persisted.collection_id == target.id
        assert target_exists


@pytest.mark.asyncio
async def test_move_cancellation_waits_for_compensation(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    old_entry = index_manager._metadata_store.get_entry(entry_id)
    assert old_entry is not None
    old_vector = (await index_manager._vector_store.snapshot_records([entry_id]))[0]
    original_update = index_manager._vector_store.update_collection_id
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_update(entry_id: int, collection_id: int) -> None:
        await original_update(entry_id, collection_id)
        entered.set()
        await release.wait()

    monkeypatch.setattr(
        index_manager._vector_store,
        "update_collection_id",
        blocking_update,
    )
    task = asyncio.create_task(index_manager.move(entry_id, target.id))
    await entered.wait()

    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    persisted = index_manager._metadata_store.get_entry(entry_id)
    assert persisted is not None
    assert persisted.collection_id == target.id
    assert await index_manager._vector_store.get_collection_ids() == {
        entry_id: target.id
    }
    assert not (index_manager._memes_dir / old_entry.image_path).exists()
    assert (index_manager._memes_dir / "目标/a.webp").read_bytes() == b"source"
    assert old_vector.entry_id == entry_id


@pytest.mark.asyncio
async def test_close_during_move_waits_until_transaction_finishes(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    entry_id = await _add_entry(
        index_manager,
        "源/a.webp",
        "文本",
        collection_id=source.id,
        content=b"source",
    )
    original_update = index_manager._vector_store.update_collection_id
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_update(entry_id: int, collection_id: int) -> None:
        await original_update(entry_id, collection_id)
        entered.set()
        await release.wait()

    monkeypatch.setattr(
        index_manager._vector_store,
        "update_collection_id",
        blocking_update,
    )
    move_task = asyncio.create_task(index_manager.move(entry_id, target.id))
    await entered.wait()
    close_task = asyncio.create_task(index_manager.close())
    await asyncio.sleep(0)
    assert not close_task.done()

    release.set()
    await close_task
    with pytest.raises(Exception):
        await move_task

    assert (index_manager._memes_dir / "目标/a.webp").read_bytes() == b"source"
