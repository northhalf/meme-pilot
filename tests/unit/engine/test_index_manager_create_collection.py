"""IndexManager 创建合集测试。"""

import asyncio
import logging
import os
import sqlite3
import stat
import traceback
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from bot.index_manager import (
    AddResult,
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    IndexAddCancelledError,
    IndexManager,
    RefreshInProgressError,
    SyncResult,
)
from bot.engine.metadata_store import MetadataStore
from bot.engine.types import MemeCollection
from bot.engine.vector_store import VectorStore
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


async def _wait_for_queue_size(index_manager: IndexManager, size: int) -> None:
    """等待写队列达到指定长度。

    Args:
        index_manager: 被观测的索引管理器。
        size: 期望的最小队列长度。
    """
    async with asyncio.timeout(1.0):
        while index_manager._coordinator.write_queue.qsize() < size:
            await asyncio.sleep(0)


async def _fake_pipeline(relative_path: str) -> tuple[str, str, list[float]]:
    """返回无需真实 OCR 与 embedding 的固定管道结果。

    Args:
        relative_path: 输入的图片相对路径。

    Returns:
        文件名、OCR 文本和测试向量。
    """
    return relative_path, "测试文本", [1.0]


def _replace_target_directory(target: Path, path_kind: str) -> tuple[int, int] | None:
    """把目标目录移走并替换为指定路径类型。

    Args:
        target: 本次创建的目标目录。
        path_kind: 替换类型，支持 symlink、fifo、directory。

    Returns:
        替换目录的设备号与 inode；非目录类型返回 None。
    """
    moved = target.with_name(f"{target.name}-moved")
    target.rename(moved)
    if path_kind == "symlink":
        source = target.with_name(f"{target.name}-source")
        source.mkdir()
        target.symlink_to(source, target_is_directory=True)
        return None
    if path_kind == "fifo":
        os.mkfifo(target)
        return None
    if path_kind == "directory":
        replacement = target.with_name(f"{target.name}-replacement")
        replacement.mkdir()
        replacement_stat = os.lstat(replacement)
        replacement.rename(target)
        return replacement_stat.st_dev, replacement_stat.st_ino
    raise AssertionError(f"未知测试路径类型: {path_kind}")


def _assert_replacement_survives(
    target: Path,
    path_kind: str,
    expected_identity: tuple[int, int] | None,
) -> None:
    """断言外部替换路径未被合集创建补偿删除。

    Args:
        target: 被替换的目标路径。
        path_kind: 替换类型。
        expected_identity: directory 类型的预期身份。
    """
    target_stat = os.lstat(target)
    if path_kind == "symlink":
        assert stat.S_ISLNK(target_stat.st_mode)
    elif path_kind == "fifo":
        assert stat.S_ISFIFO(target_stat.st_mode)
    else:
        assert stat.S_ISDIR(target_stat.st_mode)
        assert (target_stat.st_dev, target_stat.st_ino) == expected_identity


@pytest.mark.asyncio
async def test_create_collection_creates_directory_and_row(
    index_manager: IndexManager,
) -> None:
    result = await index_manager.create_collection("  新三国  ")

    assert result.collection.id == 1
    assert result.collection.name == "新三国"
    assert result.registered_existing_directory is False
    assert (index_manager._memes_dir / "新三国").is_dir()
    assert index_manager._metadata_store.get_collection_by_name("新三国") == (
        result.collection
    )


@pytest.mark.asyncio
async def test_create_collection_registers_existing_directory_without_refresh(
    index_manager: IndexManager,
) -> None:
    target = index_manager._memes_dir / "新三国"
    target.mkdir()
    (target / "existing.webp").write_bytes(b"not-indexed-yet")

    result = await index_manager.create_collection("新三国")

    assert result.registered_existing_directory is True
    assert target.joinpath("existing.webp").exists()
    assert (
        index_manager._metadata_store.collection_entry_count(result.collection.id) == 0
    )


@pytest.mark.asyncio
async def test_create_collection_does_not_change_selected_scope(
    index_manager: IndexManager,
) -> None:
    existing = index_manager._metadata_store.create_collection("已有")
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, existing.id)

    await index_manager.create_collection("新建")

    assert index_manager._metadata_store.get_selected_collection(scope) == existing.id


@pytest.mark.asyncio
async def test_create_collection_rejects_registered_duplicate(
    index_manager: IndexManager,
) -> None:
    existing = index_manager._metadata_store.create_collection("新三国")

    with pytest.raises(CollectionAlreadyExistsError) as caught:
        await index_manager.create_collection("新三国")

    assert caught.value.collection == existing


@pytest.mark.asyncio
@pytest.mark.parametrize("path_kind", ["file", "symlink"])
async def test_create_collection_rejects_non_directory_path(
    index_manager: IndexManager, path_kind: str
) -> None:
    target = index_manager._memes_dir / "冲突"
    if path_kind == "file":
        target.write_text("file", encoding="utf-8")
    else:
        source = index_manager._memes_dir / "source"
        source.mkdir()
        target.symlink_to(source, target_is_directory=True)

    with pytest.raises(CollectionPathConflictError):
        await index_manager.create_collection("冲突")


@pytest.mark.asyncio
@pytest.mark.parametrize("path_kind", ["symlink", "fifo", "directory"])
async def test_database_failure_does_not_remove_replaced_directory(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path_kind: str,
) -> None:
    """SQLite 失败补偿前身份变化时不得调用 rmdir 删除替换路径。"""
    target = index_manager._memes_dir / "补偿身份变化"
    replacement_identity: tuple[int, int] | None = None
    rmdir_calls: list[Path] = []
    original_rmdir = Path.rmdir

    def fail_after_replace(name: str) -> None:
        nonlocal replacement_identity
        replacement_identity = _replace_target_directory(target, path_kind)
        raise sqlite3.OperationalError("database unavailable")

    def record_rmdir(path: Path) -> None:
        rmdir_calls.append(path)
        original_rmdir(path)

    monkeypatch.setattr(
        index_manager._metadata_store,
        "create_collection",
        fail_after_replace,
    )
    monkeypatch.setattr(Path, "rmdir", record_rmdir)
    caplog.set_level(logging.CRITICAL, logger="bot.index_manager.manager")

    with pytest.raises(CollectionCreateError):
        await index_manager.create_collection("补偿身份变化")

    assert rmdir_calls == []
    assert index_manager._metadata_store.get_collection_by_name("补偿身份变化") is None
    _assert_replacement_survives(target, path_kind, replacement_identity)
    critical_record = next(
        record
        for record in caplog.records
        if record.levelno == logging.CRITICAL and "目录回滚失败" in record.message
    )
    assert critical_record.exc_info is not None


@pytest.mark.asyncio
async def test_database_failure_removes_new_empty_directory(
    index_manager: IndexManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = index_manager._memes_dir / "失败合集"

    def fail_create(name: str) -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(index_manager._metadata_store, "create_collection", fail_create)

    with pytest.raises(sqlite3.OperationalError):
        await index_manager.create_collection("失败合集")

    assert not target.exists()


@pytest.mark.asyncio
async def test_database_failure_preserves_existing_directory(
    index_manager: IndexManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = index_manager._memes_dir / "已有目录"
    target.mkdir()

    def fail_create(name: str) -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(index_manager._metadata_store, "create_collection", fail_create)

    with pytest.raises(sqlite3.OperationalError):
        await index_manager.create_collection("已有目录")

    assert target.is_dir()


@pytest.mark.asyncio
async def test_create_collection_rejected_while_refresh_active(
    index_manager: IndexManager,
) -> None:
    index_manager._sync_engine._refresh_active = True

    with pytest.raises(RefreshInProgressError):
        await index_manager.create_collection("新三国")


@pytest.mark.asyncio
async def test_cleanup_failure_raises_collection_create_error(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = index_manager._memes_dir / "失败合集"

    def fail_create(name: str) -> None:
        target.joinpath("race.webp").write_bytes(b"added externally")
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(index_manager._metadata_store, "create_collection", fail_create)
    caplog.set_level(logging.CRITICAL, logger="bot.index_manager.manager")

    with pytest.raises(CollectionCreateError) as caught:
        await index_manager.create_collection("失败合集")

    assert target.is_dir()
    assert index_manager._metadata_store.get_collection_by_name("失败合集") is None
    cleanup_error = caught.value.__cause__
    assert isinstance(cleanup_error, OSError)
    assert isinstance(cleanup_error.__context__, sqlite3.OperationalError)
    critical_record = next(
        record
        for record in caplog.records
        if record.levelno == logging.CRITICAL and "目录回滚失败" in record.message
    )
    assert critical_record.exc_info is not None
    assert critical_record.exc_info[1] is cleanup_error
    formatted_traceback = "".join(traceback.format_exception(*critical_record.exc_info))
    assert "sqlite3.OperationalError: database unavailable" in formatted_traceback
    assert "OSError" in formatted_traceback


@pytest.mark.asyncio
async def test_create_collection_reuses_smallest_collection_id_hole(
    index_manager: IndexManager,
) -> None:
    first = index_manager._metadata_store.create_collection("一")
    second = index_manager._metadata_store.create_collection("二")
    index_manager._metadata_store.create_collection("三")
    assert first.id == 1
    assert second.id == 2
    index_manager._metadata_store.delete_collection_and_reset_scopes(second.id)

    result = await index_manager.create_collection("复用")

    assert result.collection.id == 2


@pytest.mark.asyncio
async def test_create_collection_rejects_fifo_path(
    index_manager: IndexManager,
) -> None:
    target = index_manager._memes_dir / "管道"
    os.mkfifo(target)
    try:
        with pytest.raises(CollectionPathConflictError):
            await index_manager.create_collection("管道")
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_create_collection_rejected_while_shutting_down(
    index_manager: IndexManager,
) -> None:
    index_manager._shutting_down = True

    with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
        await index_manager.create_collection("新三国")


@pytest.mark.asyncio
async def test_cancelled_queued_create_is_skipped(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_worker = index_manager._coordinator.ensure_worker
    monkeypatch.setattr(index_manager._coordinator, "ensure_worker", lambda: None)
    task = asyncio.create_task(index_manager.create_collection("取消合集"))
    await _wait_for_queue_size(index_manager, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    monkeypatch.setattr(index_manager._coordinator, "ensure_worker", start_worker)
    sentinel = await index_manager.create_collection("哨兵合集")

    assert sentinel.collection.name == "哨兵合集"
    assert not (index_manager._memes_dir / "取消合集").exists()
    assert index_manager._metadata_store.get_collection_by_name("取消合集") is None


@pytest.mark.asyncio
async def test_close_cancels_in_flight_add_and_queued_create(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 close 必须有限时间结算当前 ADD 与后续创建 future。"""
    add_started = asyncio.Event()

    async def block_write(
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        add_started.set()
        await asyncio.Event().wait()
        raise AssertionError("阻塞 ADD 不应正常返回")

    monkeypatch.setattr(index_manager._image_pipeline, "process", _fake_pipeline)
    monkeypatch.setattr(index_manager._entry_writer, "write_entry", block_write)

    add_task = asyncio.create_task(index_manager.add("close.webp"))
    await add_started.wait()
    create_task = asyncio.create_task(index_manager.create_collection("关闭排队合集"))
    await _wait_for_queue_size(index_manager, 1)
    close_task = asyncio.create_task(index_manager.close())

    add_result, create_result, close_result = await asyncio.wait_for(
        asyncio.gather(
            add_task,
            create_task,
            close_task,
            return_exceptions=True,
        ),
        timeout=1.0,
    )

    assert isinstance(add_result, IndexAddCancelledError)
    assert isinstance(create_result, IndexAddCancelledError)
    assert close_result is None
    assert not (index_manager._memes_dir / "关闭排队合集").exists()
    assert index_manager._metadata_store.get_collection_by_name("关闭排队合集") is None


@pytest.mark.asyncio
async def test_create_collection_waits_behind_existing_write_request(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_started = asyncio.Event()
    release_add = asyncio.Event()
    order: list[str] = []

    async def block_write(
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        order.append("add-start")
        add_started.set()
        await release_add.wait()
        order.append("add-end")
        return AddResult(entry_id=1, reason="added", text=text)

    original_create = index_manager._metadata_store.create_collection

    def record_create(name: str) -> MemeCollection:
        order.append("create")
        return original_create(name)

    monkeypatch.setattr(index_manager._image_pipeline, "process", _fake_pipeline)
    monkeypatch.setattr(index_manager._entry_writer, "write_entry", block_write)
    monkeypatch.setattr(
        index_manager._metadata_store, "create_collection", record_create
    )

    add_task = asyncio.create_task(index_manager.add("queued.webp"))
    await add_started.wait()
    create_task = asyncio.create_task(index_manager.create_collection("排队合集"))
    await _wait_for_queue_size(index_manager, 1)

    assert not create_task.done()
    assert not (index_manager._memes_dir / "排队合集").exists()

    release_add.set()
    add_result, create_result = await asyncio.gather(add_task, create_task)

    assert add_result.reason == "added"
    assert create_result.collection.name == "排队合集"
    assert order == ["add-start", "add-end", "create"]


@pytest.mark.asyncio
async def test_refresh_waits_for_queued_create_collection(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_started = asyncio.Event()
    release_add = asyncio.Event()
    order: list[str] = []

    async def block_write(
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        order.append("add-start")
        add_started.set()
        await release_add.wait()
        order.append("add-end")
        return AddResult(entry_id=1, reason="added", text=text)

    original_create = index_manager._metadata_store.create_collection

    def record_create(name: str) -> MemeCollection:
        order.append("create")
        return original_create(name)

    async def record_refresh() -> SyncResult:
        order.append("refresh")
        return SyncResult()

    monkeypatch.setattr(index_manager._image_pipeline, "process", _fake_pipeline)
    monkeypatch.setattr(index_manager._entry_writer, "write_entry", block_write)
    monkeypatch.setattr(
        index_manager._metadata_store, "create_collection", record_create
    )
    monkeypatch.setattr(index_manager, "_run_sync_internal", record_refresh)

    add_task = asyncio.create_task(index_manager.add("queued.webp"))
    await add_started.wait()
    create_task = asyncio.create_task(index_manager.create_collection("刷新前合集"))
    await _wait_for_queue_size(index_manager, 1)
    refresh_task = asyncio.create_task(index_manager.refresh())
    await asyncio.sleep(0)

    assert not create_task.done()
    assert not refresh_task.done()

    release_add.set()
    add_result, create_result, refresh_result = await asyncio.gather(
        add_task,
        create_task,
        refresh_task,
    )

    assert add_result.reason == "added"
    assert create_result.collection.name == "刷新前合集"
    assert refresh_result == SyncResult()
    assert order == ["add-start", "add-end", "create", "refresh"]


@pytest.mark.asyncio
async def test_refresh_waits_for_dequeued_create_blocked_before_write_lock(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh 必须等待已 dequeue 但尚未取得写锁的创建请求。"""
    order: list[str] = []
    original_create = index_manager._metadata_store.create_collection
    start_worker = index_manager._coordinator.ensure_worker
    lock_held = True
    create_task: asyncio.Task | None = None
    refresh_task: asyncio.Task | None = None

    def record_create(name: str) -> MemeCollection:
        order.append("create")
        return original_create(name)

    async def record_refresh() -> SyncResult:
        order.append("refresh")
        return SyncResult()

    monkeypatch.setattr(
        index_manager._metadata_store,
        "create_collection",
        record_create,
    )
    monkeypatch.setattr(index_manager, "_run_sync_internal", record_refresh)
    monkeypatch.setattr(index_manager._coordinator, "ensure_worker", lambda: None)
    await index_manager._rwlock.acquire_write()
    try:
        create_task = asyncio.create_task(index_manager.create_collection("已取出合集"))
        await _wait_for_queue_size(index_manager, 1)
        assert not index_manager._coordinator.write_drained.is_set()

        monkeypatch.setattr(index_manager._coordinator, "ensure_worker", start_worker)
        start_worker()
        async with asyncio.timeout(1.0):
            while not index_manager._coordinator.write_queue.empty():
                await asyncio.sleep(0)

        refresh_task = asyncio.create_task(index_manager.refresh())
        await asyncio.sleep(0)
        assert not refresh_task.done()
        assert order == []

        await index_manager._rwlock.release_write()
        lock_held = False
        create_result, refresh_result = await asyncio.gather(
            create_task,
            refresh_task,
        )

        assert create_result.collection.name == "已取出合集"
        assert refresh_result == SyncResult()
        assert order == ["create", "refresh"]
    finally:
        if lock_held:
            await index_manager._rwlock.release_write()
        for task in (create_task, refresh_task):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
