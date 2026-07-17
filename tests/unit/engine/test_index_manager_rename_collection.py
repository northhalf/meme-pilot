"""IndexManager 重命名合集测试。"""

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from bot.engine.collection_manager import CollectionNotFoundError
from bot.engine.collection_manager import InvalidCollectionNameError
from bot.engine.metadata_store import MetadataStore
from bot.engine.types import MemeCollection
from bot.engine.vector_store import VectorStore
from bot.index_manager import (
    CollectionCreateError,
    CollectionPathConflictError,
    CollectionRenameTargetExistsError,
    IndexManager,
)
from bot.session import ChatScope


@pytest_asyncio.fixture
async def index_manager(
    tmp_path: Path,
) -> AsyncGenerator[IndexManager, None]:
    """创建使用真实 SQLite 与临时目录的 IndexManager。"""
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = MetadataStore(str(tmp_path / "data" / "index.db"))
    metadata_store.load()
    vector_store = cast(VectorStore, MagicMock(spec=VectorStore))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    yield manager
    await manager.close()


async def _seed_collection_with_entry(
    index_manager: IndexManager, name: str
) -> tuple[MemeCollection, int]:
    """创建合集并写入一条条目，返回合集与 entry_id。"""
    result = await index_manager.create_collection(name)
    cid = result.collection.id
    target = index_manager._memes_dir / name
    (target / "a.webp").write_bytes(b"x")
    eid = index_manager._metadata_store.add(
        f"{name}/a.webp", "文字", collection_id=cid
    )
    return result.collection, eid


@pytest.mark.asyncio
async def test_rename_collection_updates_db_dir_and_image_path(
    index_manager: IndexManager,
) -> None:
    """重命名：DB name + 目录重命名 + image_path 首段 + collection_id 不变。"""
    collection, eid = await _seed_collection_with_entry(index_manager, "新三国")
    cid = collection.id
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, cid)

    result = await index_manager.rename_collection("新三国", "旧三国")

    assert result.collection == MemeCollection(cid, "旧三国")
    assert result.old_name == "新三国"
    assert result.new_name == "旧三国"
    assert result.entry_count == 1
    # 目录已重命名
    assert not (index_manager._memes_dir / "新三国").exists()
    assert (index_manager._memes_dir / "旧三国").is_dir()
    # DB name 更新
    assert index_manager._metadata_store.get_collection(cid) == MemeCollection(
        cid, "旧三国"
    )
    # image_path 首段替换
    entry = index_manager._metadata_store.get_entry(eid)
    assert entry is not None
    assert entry.image_path == "旧三国/a.webp"
    assert entry.collection_id == cid
    # ChatScope 不受影响（collection_id 不变）
    assert index_manager._metadata_store.get_selected_collection(scope) == cid


@pytest.mark.asyncio
async def test_rename_collection_accepts_numeric_id(
    index_manager: IndexManager,
) -> None:
    """按编号重命名。"""
    collection, _ = await _seed_collection_with_entry(index_manager, "编号合集")
    await index_manager.rename_collection(str(collection.id), "新名")
    assert index_manager._metadata_store.get_collection(collection.id) == MemeCollection(
        collection.id, "新名"
    )


@pytest.mark.asyncio
async def test_rename_collection_rejects_duplicate_target(
    index_manager: IndexManager,
) -> None:
    """目标名已被其他合集使用时拒绝，DB 与目录都不动。"""
    await index_manager.create_collection("甄嬛传")
    collection, _ = await _seed_collection_with_entry(index_manager, "新三国")

    with pytest.raises(CollectionRenameTargetExistsError) as caught:
        await index_manager.rename_collection("新三国", "甄嬛传")

    assert caught.value.collection.name == "甄嬛传"
    assert (index_manager._memes_dir / "新三国").is_dir()
    assert index_manager._metadata_store.get_collection(collection.id) == MemeCollection(
        collection.id, "新三国"
    )


@pytest.mark.asyncio
async def test_rename_collection_rejects_existing_target_directory(
    index_manager: IndexManager,
) -> None:
    """目标名对应路径已存在（未登记目录）时拒绝。"""
    collection, _ = await _seed_collection_with_entry(index_manager, "新三国")
    (index_manager._memes_dir / "未登记目录").mkdir()

    with pytest.raises(CollectionPathConflictError):
        await index_manager.rename_collection("新三国", "未登记目录")

    assert (index_manager._memes_dir / "新三国").is_dir()
    assert index_manager._metadata_store.get_collection(collection.id) == MemeCollection(
        collection.id, "新三国"
    )


@pytest.mark.asyncio
async def test_rename_collection_rejects_unknown_source(
    index_manager: IndexManager,
) -> None:
    with pytest.raises(CollectionNotFoundError):
        await index_manager.rename_collection("不存在", "新名")


@pytest.mark.asyncio
async def test_rename_collection_rejects_invalid_new_name(
    index_manager: IndexManager,
) -> None:
    """新名称走 validate_collection_name 校验。"""
    await _seed_collection_with_entry(index_manager, "新三国")
    with pytest.raises(InvalidCollectionNameError):
        await index_manager.rename_collection("新三国", "全 局")


@pytest.mark.asyncio
async def test_rename_collection_dir_rename_failure_rolls_back_sqlite(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """目录 rename 失败时回滚 SQLite 与缓存，抛 CollectionCreateError。"""
    collection, eid = await _seed_collection_with_entry(index_manager, "新三国")
    cid = collection.id

    def fail_rename(self: Path, target: Path) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "rename", fail_rename)
    caplog.set_level(logging.CRITICAL, logger="bot.index_manager.write_coordinator")

    with pytest.raises(CollectionCreateError):
        await index_manager.rename_collection("新三国", "旧三国")

    # SQLite 回滚：合集名仍为旧名，image_path 仍为旧首段
    assert index_manager._metadata_store.get_collection(cid) == MemeCollection(
        cid, "新三国"
    )
    entry = index_manager._metadata_store.get_entry(eid)
    assert entry is not None
    assert entry.image_path == "新三国/a.webp"


@pytest.mark.asyncio
async def test_rename_collection_does_not_touch_vector_store(
    index_manager: IndexManager,
) -> None:
    """重命名不调用 vector_store 任何方法（collection_id 不变，chroma 无需更新）。"""
    await _seed_collection_with_entry(index_manager, "新三国")
    vector_store = cast(MagicMock, index_manager._vector_store)

    await index_manager.rename_collection("新三国", "旧三国")

    assert not vector_store.method_calls, (
        f"重命名不应调用 vector_store，实际调用: {vector_store.method_calls}"
    )


@pytest.mark.asyncio
async def test_rename_collection_rejects_global_zero(
    index_manager: IndexManager,
) -> None:
    """目标 0（全局）不在 _id_to_collections，resolve_collection 抛 CollectionNotFoundError。"""
    await _seed_collection_with_entry(index_manager, "新三国")

    with pytest.raises(CollectionNotFoundError):
        await index_manager.rename_collection("0", "旧三国")
