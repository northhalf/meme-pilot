"""IndexManager 删除合集测试。"""

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from bot.engine.collection_manager import CollectionNotFoundError
from bot.engine.metadata_store import MetadataStore
from bot.engine.types import MemeCollection
from bot.engine.vector_store import VectorStore
from bot.index_manager import (
    CollectionDeleteError,
    CollectionNotEmptyError,
    IndexAddCancelledError,
    IndexManager,
    RefreshInProgressError,
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


@pytest.mark.asyncio
async def test_delete_collection_removes_directory_db_and_resets_scope(
    index_manager: IndexManager,
) -> None:
    """删除空合集：rmdir 空目录 + 删 DB 记录 + 回退引用它的 ChatScope。"""
    result = await index_manager.create_collection("新三国")
    cid = result.collection.id
    target = index_manager._memes_dir / "新三国"
    assert target.is_dir()
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, cid)

    deleted = await index_manager.delete_collection("新三国")

    assert deleted.collection == MemeCollection(cid, "新三国")
    assert deleted.reset_scope_count == 1
    assert not target.exists()
    assert index_manager._metadata_store.get_collection(cid) is None
    assert index_manager._metadata_store.get_selected_collection(scope) == 0


@pytest.mark.asyncio
async def test_delete_collection_accepts_numeric_id(
    index_manager: IndexManager,
) -> None:
    """按编号删除合集。"""
    result = await index_manager.create_collection("编号合集")
    deleted = await index_manager.delete_collection(str(result.collection.id))
    assert deleted.collection.id == result.collection.id
    assert index_manager._metadata_store.get_collection(result.collection.id) is None


@pytest.mark.asyncio
async def test_delete_collection_rejects_non_empty(
    index_manager: IndexManager,
) -> None:
    """非空合集拒绝删除，DB 与目录都不动。"""
    result = await index_manager.create_collection("非空")
    cid = result.collection.id
    target = index_manager._memes_dir / "非空"
    (target / "a.webp").write_bytes(b"x")
    index_manager._metadata_store.add("非空/a.webp", "文字", collection_id=cid)

    with pytest.raises(CollectionNotEmptyError):
        await index_manager.delete_collection("非空")

    assert target.is_dir()
    assert index_manager._metadata_store.get_collection(cid) is not None


@pytest.mark.asyncio
async def test_delete_collection_rejects_unknown(index_manager: IndexManager) -> None:
    """不存在的合集抛 CollectionNotFoundError。"""
    with pytest.raises(CollectionNotFoundError):
        await index_manager.delete_collection("不存在")


@pytest.mark.asyncio
async def test_delete_collection_rmdir_failure_leaves_db_intact(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rmdir 失败时 DB 未动，抛 CollectionDeleteError。"""
    result = await index_manager.create_collection("rmdir失败")
    cid = result.collection.id
    target = index_manager._memes_dir / "rmdir失败"

    def fail_rmdir(self: Path) -> None:
        raise OSError("rmdir failed")

    monkeypatch.setattr(Path, "rmdir", fail_rmdir)

    with pytest.raises(CollectionDeleteError):
        await index_manager.delete_collection("rmdir失败")

    assert target.is_dir()
    assert index_manager._metadata_store.get_collection(cid) is not None


@pytest.mark.asyncio
async def test_delete_collection_db_failure_restores_directory(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """删 DB 失败时补偿恢复空目录，记 critical 日志，抛 CollectionDeleteError。"""
    result = await index_manager.create_collection("db失败")
    cid = result.collection.id
    target = index_manager._memes_dir / "db失败"

    def fail_delete(cid_arg: int) -> int:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        index_manager._metadata_store,
        "delete_collection_and_reset_scopes",
        fail_delete,
    )
    caplog.set_level(logging.CRITICAL, logger="bot.index_manager.write_coordinator")

    with pytest.raises(CollectionDeleteError):
        await index_manager.delete_collection("db失败")

    assert target.is_dir()
    assert index_manager._metadata_store.get_collection(cid) is not None
    assert any(record.levelno == logging.CRITICAL for record in caplog.records)


@pytest.mark.asyncio
async def test_delete_collection_rejected_while_refresh_active(
    index_manager: IndexManager,
) -> None:
    await index_manager.create_collection("刷新中")
    index_manager._sync_engine._refresh_active = True
    with pytest.raises(RefreshInProgressError):
        await index_manager.delete_collection("刷新中")


@pytest.mark.asyncio
async def test_delete_collection_rejected_while_shutting_down(
    index_manager: IndexManager,
) -> None:
    index_manager._shutting_down = True
    with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
        await index_manager.delete_collection("关闭中")


@pytest.mark.asyncio
async def test_delete_collection_rejects_global_zero(
    index_manager: IndexManager,
) -> None:
    """目标 0（全局）不在 _id_to_collections，resolve_collection 抛 CollectionNotFoundError。"""
    with pytest.raises(CollectionNotFoundError):
        await index_manager.delete_collection("0")


@pytest.mark.asyncio
async def test_delete_collection_raises_when_directory_non_empty_but_db_empty(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 判空通过但目录实际非空（DB/目录不一致）时 scandir 重检抛 CollectionDeleteError。

    模拟 collection_entry_count 返回 0，但目录里有未索引文件，写锁内 os.scandir
    二次复核应拒绝删除，DB 未动。
    """
    result = await index_manager.create_collection("不一致")
    cid = result.collection.id
    target = index_manager._memes_dir / "不一致"
    (target / "stray.webp").write_bytes(b"x")

    monkeypatch.setattr(
        index_manager._metadata_store, "collection_entry_count", lambda cid_arg: 0
    )

    with pytest.raises(CollectionDeleteError):
        await index_manager.delete_collection("不一致")

    assert target.is_dir()
    assert index_manager._metadata_store.get_collection(cid) is not None
