"""_WriteCoordinator - 写入编排：worker 循环 + queue + WriteOp 字典派发 + move 补偿。

从 IndexManager 抽离，收口全部写入编排逻辑。持有写入队列与 worker task，按
_write_dispatch 字典派发串行处理 ADD / EDIT_TEXT / SET_SPEAKER / ADD_TAG / DELETE /
MOVE / CREATE_COLLECTION / DELETE_COLLECTION / RENAME_COLLECTION 九类写入请求，move
支持补偿式事务与调用者取消后可靠等待事务完成。门面 IndexManager 持有本类实例并经
薄委托/property 转发保留测试 monkeypatch 接缝。
"""

import asyncio
import logging
import os
import shutil
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from bot.engine.collection_manager import CollectionNotFoundError, validate_collection_name
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.types import GLOBAL_COLLECTION_NAME, CollectionSelection, MemeCollection
from bot.engine.utils import (
    SecureMoveError,
    SecureMoveResult,
    get_regular_file_identity,
    resolve_unique_filename,
    secure_move_file,
)
from bot.engine.vector_store import VectorRecord, VectorStore

from .image_pipeline import wait_task_through_cancellation
from .index_types import (
    AddResult,
    AddTagResult,
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionDeleteError,
    CollectionNotEmptyError,
    CollectionPathConflictError,
    CollectionRenameTargetExistsError,
    CreateCollectionResult,
    DeleteCollectionResult,
    DeleteResult,
    DuplicateMemeInCollectionError,
    DuplicateTextError,
    EditTextResult,
    EmbeddingError,
    IndexAddCancelledError,
    MemeMoveError,
    MemeMoveSourceExpiredError,
    MoveResult,
    RenameCollectionResult,
    SetSpeakerResult,
    WriteOp,
    _WriteRequest,
)
from .rwlock import IndexRwLock

if TYPE_CHECKING:
    from bot.engine.collection_manager import CollectionManager
    from bot.session import ChatScope

logger = logging.getLogger(__name__)


class _WriteCoordinator:
    """写入编排：worker 循环 + queue + WriteOp 字典派发 + 九个 _execute_* + move 补偿。

    Args:
        metadata_store: 元数据存储。
        vector_store: 向量存储。
        collection_manager: 合集管理器。
        memes_dir: 表情包图片目录。
        deleted_dir: 已删除图目录。
        replaced_dir: 被替换旧图归档目录。
        rwlock: 读写锁（注入，写锁在 worker 内获取）。
        write_drained: 无排队且无执行中写请求时 set 的 Event（注入，操作权在此）。
        write_entry: ADD 分支调用的单条写入回调（门面 lambda 包装
            ``self._write_entry``，保留测试对门面 ``_write_entry`` 的 monkeypatch
            接缝）。
        validate_collection_selection_locked: ADD 写锁内校验合集选择快照的回调。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        collection_manager: "CollectionManager",
        memes_dir: Path,
        deleted_dir: Path,
        replaced_dir: Path,
        rwlock: IndexRwLock,
        write_drained: asyncio.Event,
        write_entry: Callable[..., Awaitable[AddResult]],
        validate_collection_selection_locked: Callable[
            ["ChatScope", "CollectionSelection"], None
        ],
    ) -> None:
        """初始化 _WriteCoordinator。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            collection_manager: 合集管理器实例。
            memes_dir: 表情包图片目录路径。
            deleted_dir: 已删除图目录路径。
            replaced_dir: 被替换旧图归档目录路径。
            rwlock: 读写锁实例。
            write_drained: 写入排空 Event（已 set 的初始状态由调用方保证）。
            write_entry: ADD 分支单条写入回调。
            validate_collection_selection_locked: ADD 写锁内合集选择校验回调。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._collection_manager = collection_manager
        self._memes_dir = memes_dir
        self._deleted_dir = deleted_dir
        self._replaced_dir = replaced_dir
        self._rwlock = rwlock
        self._write_drained = write_drained
        self._write_entry = write_entry
        self._validate_collection_selection_locked = (
            validate_collection_selection_locked
        )
        self._write_queue: asyncio.Queue[_WriteRequest] = asyncio.Queue()
        self._write_worker_task: asyncio.Task | None = None
        # WriteOp -> 写锁内执行器映射（worker 循环按 op 派发，替代 if/elif 链）。
        # MOVE / CREATE_COLLECTION / DELETE_COLLECTION 经 guarded 包装：取得写锁后重检 future 取消。
        self._write_dispatch: dict[
            WriteOp, Callable[[_WriteRequest], Awaitable[Any]]
        ] = {
            WriteOp.ADD: self._execute_add,
            WriteOp.EDIT_TEXT: self._execute_edit_text,
            WriteOp.SET_SPEAKER: self._execute_set_speaker,
            WriteOp.ADD_TAG: self._execute_add_tags,
            WriteOp.DELETE: self._execute_delete,
            WriteOp.MOVE: self._execute_move_guarded,
            WriteOp.CREATE_COLLECTION: self._execute_create_collection_guarded,
            WriteOp.DELETE_COLLECTION: self._execute_delete_collection_guarded,
            WriteOp.RENAME_COLLECTION: self._execute_rename_collection_guarded,
        }

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    @property
    def write_queue(self) -> asyncio.Queue[_WriteRequest]:
        """写入队列（无 setter；门面 property 转发保留测试 put/read 接缝）。"""
        return self._write_queue

    @property
    def write_drained(self) -> asyncio.Event:
        """写入排空 Event（无 setter；门面 property 转发保留测试 clear/is_set 接缝）。"""
        return self._write_drained

    async def submit(self, req: _WriteRequest) -> Any:
        """提交写入请求并等待执行完成。

        调用方需先调 ``ensure_worker`` 启动 worker（门面经
        ``self._ensure_write_worker`` 薄委托调用以保留测试 monkeypatch 接缝）。

        Args:
            req: 已构造好的写入请求单元。

        Returns:
            worker 执行结果（类型随 op 不同）。
        """
        self._enqueue_write_request(req)
        return await req.future

    async def submit_move(self, req: _WriteRequest) -> MoveResult:
        """提交 MOVE 请求并等待补偿式事务完成。

        不能用普通 ``submit``：调用者取消等待时，事务未开始则取消 future 让 worker
        跳过；已开始则事务仍在 Write Worker 中完成或补偿，随后再向调用者传播取消，
        避免留下半移动状态。

        Args:
            req: 已构造好的 MOVE 写入请求（含 transaction_started Event）。

        Returns:
            从持久 SQLite 条目构造的实际移动结果。

        Raises:
            asyncio.CancelledError: 调用者被取消（事务可靠收尾后传播）。
        """
        future = cast("asyncio.Future[MoveResult]", req.future)
        self._enqueue_write_request(req)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            assert req.transaction_started is not None
            if not req.transaction_started.is_set():
                future.cancel()
                raise
            waiter = asyncio.create_task(self._await_move_future(future))
            _, error, _ = await wait_task_through_cancellation(waiter)
            if error is not None and not isinstance(error, asyncio.CancelledError):
                logger.error(
                    "MOVE 调用者取消后事务执行失败: entry_id=%s, error=%s",
                    req.entry_id,
                    error,
                )
            raise

    def ensure_worker(self) -> None:
        """确保 Write Worker task 已启动（延迟启动）。"""
        if self._write_worker_task is None or self._write_worker_task.done():
            self._write_worker_task = asyncio.create_task(self._write_worker_loop())

    def cancel_worker(self) -> asyncio.Task | None:
        """取消 Write Worker 并返回其 task，供调用方与其他 task 一并等待结束。

        Returns:
            已取消的 worker task；未启动或已结束时返回 None。
        """
        task = self._write_worker_task
        if task is None or task.done():
            return None
        task.cancel()
        return task

    def _enqueue_write_request(self, req: _WriteRequest) -> None:
        """标记写入未排空并以 FIFO 顺序加入无界队列。

        Args:
            req: 待处理的写入请求。
        """
        self._write_drained.clear()
        self._write_queue.put_nowait(req)

    # ------------------------------------------------------------------
    # worker 循环
    # ------------------------------------------------------------------

    async def _write_worker_loop(self) -> None:
        """串行处理 ADD、编辑、删除、移动、创建与删除合集任务（写锁保护）。"""
        try:
            while True:
                req = await self._write_queue.get()

                if req.future.done():
                    # 已被取消/放弃的请求，跳过不写，避免孤儿写入
                    if self._write_queue.empty():
                        self._write_drained.set()
                    continue

                try:
                    async with self._rwlock.write():
                        try:
                            handler = self._write_dispatch.get(req.op)
                            if handler is None:
                                raise ValueError(f"未知写入操作: {req.op}")
                            result = await handler(req)
                            if not req.future.done():
                                req.future.set_result(result)
                        except asyncio.CancelledError:
                            if not req.future.done():
                                req.future.set_exception(
                                    IndexAddCancelledError("写入工作线程被取消")
                                )
                            raise
                        except Exception as exc:
                            if not req.future.done():
                                req.future.set_exception(exc)
                        finally:
                            if self._write_queue.empty():
                                self._write_drained.set()
                except asyncio.CancelledError:
                    if not req.future.done():
                        req.future.set_exception(
                            IndexAddCancelledError("写入工作线程被取消")
                        )
                    raise
        finally:
            while True:
                try:
                    pending = self._write_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not pending.future.done():
                    pending.future.set_exception(
                        IndexAddCancelledError("写入工作线程已停止")
                    )
            self._write_drained.set()

    @staticmethod
    async def _await_move_future(future: asyncio.Future[MoveResult]) -> MoveResult:
        """等待移动结果，供可靠取消等待 helper 托管。

        Args:
            future: Write Worker 的移动结果 Future。

        Returns:
            移动成功结果。
        """
        return await asyncio.shield(future)

    # ------------------------------------------------------------------
    # add 执行器
    # ------------------------------------------------------------------

    async def _execute_add(self, req: _WriteRequest) -> AddResult:
        """写锁内执行 ADD 写入：校验 embedding 与合集选择快照后写单条。

        Args:
            req: 写入任务单元（embedding 必非 None）。

        Returns:
            单条写入结果。

        Raises:
            ValueError: embedding 为 None，或 expected_selection 存在但缺少 scope。
            CollectionSelectionExpiredError: 合集选择快照已失效。
        """
        if req.embedding is None:
            raise ValueError("req 中的 embedding 为 None")
        if req.expected_selection is not None:
            if req.scope is None:
                raise ValueError("ADD 请求缺少 scope")
            self._validate_collection_selection_locked(
                req.scope,
                req.expected_selection,
            )
        return await self._write_entry(
            req.filename,
            req.text,
            req.embedding,
            req.speaker,
            req.tags,
            collection_id=req.collection_id,
        )

    # ------------------------------------------------------------------
    # create_collection 执行器
    # ------------------------------------------------------------------

    @staticmethod
    def _get_collection_directory_identity(target: Path) -> tuple[int, int]:
        """读取非符号链接普通目录的文件系统身份。

        Args:
            target: 待检查的合集目录路径。

        Returns:
            目录的设备号与 inode。

        Raises:
            CollectionPathConflictError: 路径不存在或不是非符号链接普通目录。
        """
        try:
            target_stat = os.lstat(target)
        except OSError as exc:
            raise CollectionPathConflictError(target.name) from exc
        if not stat.S_ISDIR(target_stat.st_mode):
            raise CollectionPathConflictError(target.name)
        return target_stat.st_dev, target_stat.st_ino

    def _execute_create_collection(
        self, raw_name: str
    ) -> CreateCollectionResult:
        """在写锁内创建目录并登记合集。

        Args:
            raw_name: 已解析的合集名称；仍会重新执行领域校验。

        Returns:
            已持久化的合集创建结果。

        Raises:
            CollectionAlreadyExistsError: 名称已被登记。
            CollectionPathConflictError: 同名路径不是安全普通目录。
            CollectionCreateError: 目录身份变化或 SQLite 失败后无法安全回滚。
        """
        name = validate_collection_name(raw_name)
        existing = self._metadata_store.get_collection_by_name(name)
        if existing is not None:
            raise CollectionAlreadyExistsError(existing)

        target = self._memes_dir / name
        created_directory = False
        registered_existing_directory = False

        try:
            target.mkdir()
            created_directory = True
        except FileExistsError:
            registered_existing_directory = True
        except OSError:
            logger.exception("创建合集目录失败: name=%r", name)
            raise

        try:
            directory_identity = self._get_collection_directory_identity(target)
        except CollectionPathConflictError as identity_exc:
            if created_directory:
                logger.critical(
                    "本次创建的合集目录身份异常: name=%r",
                    name,
                    exc_info=True,
                )
                raise CollectionCreateError("创建合集目录身份异常") from identity_exc
            raise

        try:
            current_identity = self._get_collection_directory_identity(target)
        except CollectionPathConflictError as identity_exc:
            if created_directory:
                logger.critical(
                    "SQLite 写入前合集目录身份发生变化: name=%r",
                    name,
                    exc_info=True,
                )
                raise CollectionCreateError("创建合集目录身份发生变化") from identity_exc
            raise
        if current_identity != directory_identity:
            if created_directory:
                try:
                    raise OSError("SQLite 写入前合集目录身份发生变化")
                except OSError as identity_exc:
                    logger.critical(
                        "SQLite 写入前合集目录身份发生变化: name=%r",
                        name,
                        exc_info=True,
                    )
                    raise CollectionCreateError(
                        "创建合集目录身份发生变化"
                    ) from identity_exc
            raise CollectionPathConflictError(name)

        try:
            collection = self._metadata_store.create_collection(name)
        except Exception:
            if created_directory:
                try:
                    try:
                        cleanup_identity = self._get_collection_directory_identity(
                            target
                        )
                    except CollectionPathConflictError as identity_exc:
                        raise OSError("目录回滚前合集目录身份发生变化") from identity_exc
                    if cleanup_identity != directory_identity:
                        raise OSError("目录回滚前合集目录身份发生变化")
                    target.rmdir()
                except OSError as cleanup_exc:
                    logger.critical(
                        "创建合集数据库失败且目录回滚失败: name=%r",
                        name,
                        exc_info=True,
                    )
                    raise CollectionCreateError(
                        "创建合集失败且目录回滚失败"
                    ) from cleanup_exc
            raise

        logger.info(
            "合集创建完成: id=%d, name=%r, existing_directory=%s",
            collection.id,
            collection.name,
            registered_existing_directory,
        )
        return CreateCollectionResult(
            collection=collection,
            registered_existing_directory=registered_existing_directory,
        )

    async def _execute_create_collection_guarded(
        self, req: _WriteRequest
    ) -> CreateCollectionResult | None:
        """写锁内执行 CREATE_COLLECTION：取得写锁后重检取消再创建合集。

        Args:
            req: 写入任务单元（collection_name 已完成领域校验）。

        Returns:
            合集创建结果；等待写锁期间请求已被取消时返回 None（外层不 set_result）。
        """
        if req.future.done():
            return None
        return self._execute_create_collection(req.collection_name)

    async def _execute_delete_collection_guarded(
        self, req: _WriteRequest
    ) -> DeleteCollectionResult | None:
        """写锁内执行 DELETE_COLLECTION：取得写锁后重检取消再删除合集。

        Args:
            req: 写入任务单元（collection_id 已解析）。

        Returns:
            删除结果；等待写锁期间请求已被取消时返回 None（外层不 set_result）。
        """
        if req.future.done():
            return None
        return await self._execute_delete_collection(req)

    async def _execute_delete_collection(
        self, req: _WriteRequest
    ) -> DeleteCollectionResult:
        """写锁内删除空合集：先 rmdir 空目录、后删 DB（rmdir 失败 DB 未动）。

        Args:
            req: 写入任务单元，含目标合集编号。

        Returns:
            删除结果，含回退到全部合集的 ChatScope 行数。

        Raises:
            CollectionNotFoundError: 合集不存在。
            CollectionNotEmptyError: 合集仍含表情包。
            CollectionPathConflictError: 同名路径不是普通目录。
            CollectionDeleteError: rmdir 失败或删 DB 失败且补偿失败。
        """
        collection = await asyncio.to_thread(
            self._metadata_store.get_collection, req.collection_id
        )
        if collection is None:
            raise CollectionNotFoundError(str(req.collection_id))

        if (
            await asyncio.to_thread(
                self._metadata_store.collection_entry_count, req.collection_id
            )
            > 0
        ):
            raise CollectionNotEmptyError(
                f"合集 {req.collection_id} 仍含表情包，不能删除"
            )

        target = self._memes_dir / collection.name
        # 目录身份校验：非普通目录（文件/符号链接/不存在）拒绝
        await asyncio.to_thread(self._get_collection_directory_identity, target)
        # 写锁内二次复核目录为空，防 DB 判空与 rmdir 之间并发写入
        try:
            with os.scandir(target) as entries:
                if any(True for _ in entries):
                    raise CollectionDeleteError(
                        f"合集目录非空，无法删除: {collection.name}"
                    )
        except OSError as exc:
            raise CollectionDeleteError(
                f"检查合集目录失败: {collection.name}"
            ) from exc

        try:
            target.rmdir()
        except OSError as exc:
            raise CollectionDeleteError(
                f"删除合集目录失败: {collection.name}"
            ) from exc

        try:
            reset_count = await asyncio.to_thread(
                self._metadata_store.delete_collection_and_reset_scopes,
                req.collection_id,
            )
        except Exception as exc:
            # 目录已删、DB 删除失败：补偿恢复空目录
            try:
                target.mkdir()
            except OSError as restore_exc:
                logger.critical(
                    "删除合集 DB 失败且目录恢复失败: id=%d, name=%r, error=%s",
                    req.collection_id,
                    collection.name,
                    restore_exc,
                    exc_info=True,
                )
            else:
                logger.critical(
                    "删除合集 DB 失败，已恢复空目录: id=%d, name=%r, error=%s",
                    req.collection_id,
                    collection.name,
                    exc,
                    exc_info=True,
                )
            raise CollectionDeleteError(
                f"删除合集 DB 失败: {collection.name}"
            ) from exc

        logger.info(
            "合集删除完成: id=%d, name=%r, reset_scopes=%d",
            req.collection_id,
            collection.name,
            reset_count,
        )
        return DeleteCollectionResult(
            collection=collection,
            reset_scope_count=reset_count,
        )

    # ------------------------------------------------------------------
    # rename_collection 执行器
    # ------------------------------------------------------------------

    async def _execute_rename_collection_guarded(
        self, req: _WriteRequest
    ) -> RenameCollectionResult | None:
        """写锁内执行 RENAME_COLLECTION：取得写锁后重检取消再重命名。

        Args:
            req: 写入任务单元（collection_id 与 new_collection_name 已解析）。

        Returns:
            重命名结果；等待写锁期间请求已被取消时返回 None（外层不 set_result）。
        """
        if req.future.done():
            return None
        return await self._execute_rename_collection(req)

    async def _execute_rename_collection(
        self, req: _WriteRequest
    ) -> RenameCollectionResult:
        """写锁内重命名合集：先改 SQLite（name+image_path），后重命名目录。

        目录 rename 失败时调 ``rename_collection(cid, old_name)`` 回滚 SQLite
        与缓存；补偿失败记 critical 日志，抛 CollectionCreateError。

        Args:
            req: 写入任务单元，含源合集编号与已校验的新名称。

        Returns:
            重命名结果，含受 image_path 首段变更影响的条目数。

        Raises:
            CollectionNotFoundError: 源合集不存在。
            CollectionRenameTargetExistsError: 目标名称已登记。
            CollectionPathConflictError: 源/目标路径不是普通目录或目标已存在。
            CollectionCreateError: 目录 rename 失败且补偿失败。
        """
        collection = await asyncio.to_thread(
            self._metadata_store.get_collection, req.collection_id
        )
        if collection is None:
            raise CollectionNotFoundError(str(req.collection_id))
        old_name = collection.name
        new_name = req.new_collection_name

        existing = await asyncio.to_thread(
            self._metadata_store.get_collection_by_name, new_name
        )
        if existing is not None:
            raise CollectionRenameTargetExistsError(existing)

        old_dir = self._memes_dir / old_name
        await asyncio.to_thread(self._get_collection_directory_identity, old_dir)
        new_dir = self._memes_dir / new_name
        if new_dir.exists():
            raise CollectionPathConflictError(new_name)

        entry_count = await asyncio.to_thread(
            self._metadata_store.collection_entry_count, req.collection_id
        )

        await asyncio.to_thread(
            self._metadata_store.rename_collection,
            req.collection_id,
            new_name,
        )

        try:
            old_dir.rename(new_dir)
        except OSError as exc:
            # SQLite 已改、目录未动：回滚 SQLite 与缓存到旧名
            try:
                await asyncio.to_thread(
                    self._metadata_store.rename_collection,
                    req.collection_id,
                    old_name,
                )
            except Exception as rollback_exc:
                logger.critical(
                    "重命名目录失败且回滚 SQLite 失败: id=%d, old=%r, new=%r, error=%s",
                    req.collection_id,
                    old_name,
                    new_name,
                    rollback_exc,
                    exc_info=True,
                )
                raise CollectionCreateError(
                    "重命名合集失败且回滚失败"
                ) from rollback_exc
            logger.critical(
                "重命名目录失败，已回滚 SQLite: id=%d, old=%r, new=%r, error=%s",
                req.collection_id,
                old_name,
                new_name,
                exc,
                exc_info=True,
            )
            raise CollectionCreateError(
                "重命名合集目录失败"
            ) from exc

        logger.info(
            "合集重命名完成: id=%d, old=%r, new=%r, entries=%d",
            req.collection_id,
            old_name,
            new_name,
            entry_count,
        )
        return RenameCollectionResult(
            collection=MemeCollection(req.collection_id, new_name),
            old_name=old_name,
            new_name=new_name,
            entry_count=entry_count,
        )

    # ------------------------------------------------------------------
    # edit_text / set_speaker / add_tags 执行器
    # ------------------------------------------------------------------

    async def _execute_edit_text(self, req: _WriteRequest) -> EditTextResult:
        """写锁内执行 edit_text 写入（先 sqlite 后 chroma，失败回滚）。

        Args:
            req: 写入任务单元。

        Returns:
            EditTextResult 描述修改结果。

        Raises:
            DuplicateTextError: new_text 已被其他条目使用。
            ValueError: entry_id 不存在。
            EmbeddingError: chroma upsert 失败，已回滚 sqlite。
        """
        entry = await asyncio.to_thread(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在")

        # 写锁内 TOCTOU 检查 text 冲突（同一合集内唯一）
        existing_id = await asyncio.to_thread(
            self._metadata_store.get_id_by_text,
            req.text,
            collection_id=entry.collection_id,
        )
        if existing_id is not None and existing_id != req.entry_id:
            raise DuplicateTextError(
                f"OCR 文本「{req.text}」已被 entry_id={existing_id} 使用",
            )

        # 先 sqlite
        success = await asyncio.to_thread(
            self._metadata_store.update,
            req.entry_id,
            text=req.text,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在")

        # 后 chroma，失败回滚 sqlite
        assert req.embedding is not None
        try:
            await self._vector_store.upsert(
                req.entry_id, req.embedding, collection_id=entry.collection_id
            )
        except Exception as exc:
            try:
                await asyncio.to_thread(
                    self._metadata_store.update,
                    req.entry_id,
                    text=req.old_text,
                )
            except Exception as rollback_exc:
                logger.error(
                    "edit_text 回滚失败: id=%s, error=%s", req.entry_id, rollback_exc
                )
            raise EmbeddingError(
                f"edit_text upsert 失败，已回滚: entry_id={req.entry_id}",
            ) from exc

        return EditTextResult(
            entry_id=req.entry_id,
            old_text=req.old_text,
            new_text=req.text,
        )

    async def _execute_set_speaker(self, req: _WriteRequest) -> SetSpeakerResult:
        """写锁内执行 set_speaker 写入（仅 sqlite update，无 chroma 操作）。

        Args:
            req: 写入任务单元。

        Returns:
            SetSpeakerResult 描述修改结果。

        Raises:
            ValueError: entry_id 在写锁内已不存在。
        """
        # TOCTOU 防护：写锁内重新检查 entry 是否存在
        entry = await asyncio.to_thread(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")
        old_speaker = entry.speaker

        # 写 sqlite
        success = await asyncio.to_thread(
            self._metadata_store.update,
            req.entry_id,
            speaker=req.speaker,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在（update 返回 False）")

        return SetSpeakerResult(
            entry_id=req.entry_id,
            old_speaker=old_speaker,
            new_speaker=req.speaker,
        )

    async def _execute_add_tags(self, req: _WriteRequest) -> AddTagResult:
        """写锁内执行 add_tags 写入（仅 sqlite，无 chroma 操作）。

        Args:
            req: 写入任务单元，包含 entry_id 与待追加标签。

        Returns:
            AddTagResult 描述添加结果。

        Raises:
            ValueError: entry_id 在写锁内已不存在。
        """
        entry = await asyncio.to_thread(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")

        current_tags = set(entry.tags)
        new_tags = set(req.tags or [])
        added_tags = list(new_tags - current_tags)
        merged_tags = list(current_tags | new_tags)

        if not added_tags:
            return AddTagResult(
                entry_id=req.entry_id,
                added_tags=(),
                all_tags=tuple(current_tags),
            )

        success = await asyncio.to_thread(
            self._metadata_store.update,
            req.entry_id,
            tags=merged_tags,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在（update 返回 False）")

        return AddTagResult(
            entry_id=req.entry_id,
            added_tags=tuple(added_tags),
            all_tags=tuple(merged_tags),
        )

    # ------------------------------------------------------------------
    # delete 执行器
    # ------------------------------------------------------------------

    async def _execute_delete(self, req: _WriteRequest) -> DeleteResult:
        """写锁内执行 delete（先归档文件到 memes_deleted/，再先 sqlite 后 chroma 删索引）。

        先移文件可避免「索引已删但移图失败」导致文件残留 memes/、下次 refresh 被重新
        入库（已删表情包复活）。移图失败时索引原样保留，仅将本次 id 记为失败；文件
        本就不在 memes/ 时跳过移动直接删索引。sqlite 与 chroma 删除仍按「先 sqlite
        后 chroma」，残留孤儿向量由 refresh 阶段0 自愈。

        Args:
            req: 写入任务单元，包含待删除 entry_id 列表。

        Returns:
            DeleteResult 描述删除结果，含成功、未找到、失败三类 id。
        """
        self._deleted_dir.mkdir(parents=True, exist_ok=True)

        deleted_ids: list[int] = []
        not_found_ids: list[int] = []
        failed_ids: list[tuple[int, str]] = []

        for entry_id in req.entry_ids or []:
            entry = await asyncio.to_thread(self._metadata_store.get_entry, entry_id)
            if entry is None:
                not_found_ids.append(entry_id)
                continue

            try:
                # 先归档文件：移图失败时索引原样保留（仅标记本次失败），避免文件
                # 残留 memes/ 在下次 refresh 被重新入库（已删表情包复活）。
                src = self._memes_dir / entry.image_path
                if src.exists():
                    dst = resolve_unique_filename(
                        self._deleted_dir, Path(entry.image_path).name
                    )
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # 用 shutil.move 而非 Path.rename：memes/ 与 memes_deleted/ 在
                    # Docker 中是不同 bind mount，rename 跨设备会抛 EXDEV (errno 18)。
                    shutil.move(str(src), str(dst))

                # 文件已归档（或本就不在 memes/），再删索引：先 sqlite 后 chroma
                await asyncio.to_thread(self._metadata_store.remove, entry_id)
                await self._vector_store.remove(entry_id)

                deleted_ids.append(entry_id)
            except Exception as exc:
                logger.error("删除条目失败: id=%s, error=%s", entry_id, exc)
                failed_ids.append((entry_id, str(exc)))

        return DeleteResult(
            deleted_ids=tuple(deleted_ids),
            not_found_ids=tuple(not_found_ids),
            failed_ids=tuple(failed_ids),
        )

    # ------------------------------------------------------------------
    # move 执行器与补偿
    # ------------------------------------------------------------------

    async def _execute_move_guarded(self, req: _WriteRequest) -> MoveResult | None:
        """写锁内执行 MOVE：取消重检 + transaction_started 置位 + 可靠等待事务。

        取得写锁后重检 future：调用者已取消且事务未开始则跳过（返回 None，外层不
        set_result）。事务开始后调用者取消不再中断 worker，由
        ``wait_task_through_cancellation`` 等待事务完成或补偿后再传播取消。

        Args:
            req: 写入任务单元（transaction_started 必非 None）。

        Returns:
            移动结果；等待写锁期间请求已被取消时返回 None。

        Raises:
            ValueError: 缺少 transaction_started。
            asyncio.CancelledError: worker 被取消（事务已可靠收尾）。
        """
        if req.future.done():
            return None
        if req.transaction_started is None:
            raise ValueError("MOVE 请求缺少 transaction_started")
        req.transaction_started.set()
        move_task = asyncio.create_task(self._execute_move(req))
        result, move_error, cancelled = await wait_task_through_cancellation(
            move_task
        )
        if move_error is not None:
            raise move_error
        if cancelled:
            raise asyncio.CancelledError
        assert result is not None
        return result

    def resolve_move_target_name(self, target_collection_id: int) -> str:
        """重新校验移动目标合集并返回安全目录名。

        Args:
            target_collection_id: 目标合集编号。

        Returns:
            全局显示名称或普通合集目录名。

        Raises:
            CollectionNotFoundError: 目标普通合集不存在。
            MemeMoveError: 持久合集名称不再是安全的单层目录名。
        """
        if target_collection_id == 0:
            return GLOBAL_COLLECTION_NAME
        target = self._metadata_store.get_collection(target_collection_id)
        if target is None:
            raise CollectionNotFoundError(str(target_collection_id))
        name_path = Path(target.name)
        if (
            not target.name.strip()
            or target.name.startswith(".")
            or "\\" in target.name
            or name_path.is_absolute()
            or len(name_path.parts) != 1
            or target.name in {".", ".."}
            or "\x00" in target.name
        ):
            raise MemeMoveError("目标合集目录名不安全")
        return target.name

    def _resolve_move_paths(
        self,
        old_entry: MemeEntry,
        target_collection_id: int,
        target_name: str,
    ) -> tuple[Path, Path]:
        """解析安全 helper 使用的源路径与目标目录相对路径。

        Args:
            old_entry: 移动前条目快照。
            target_collection_id: 目标合集编号。
            target_name: 已校验的目标合集名称。

        Returns:
            规范源相对路径与目标目录相对路径。

        Raises:
            MemeMoveError: 源路径逃逸、经过符号链接或目标目录不安全。
        """
        memes_root = self._memes_dir.resolve()
        source_path = self._memes_dir / old_entry.image_path
        try:
            resolved_source = source_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MemeMoveError("源图片路径不存在或无法解析") from exc
        if source_path.is_symlink() or not resolved_source.is_relative_to(memes_root):
            raise MemeMoveError("源图片路径超出 memes/ 或经过符号链接")
        if not source_path.is_file():
            raise MemeMoveError("源图片路径必须是普通文件")
        current = source_path.parent
        while current != self._memes_dir:
            if current.is_symlink():
                raise MemeMoveError("源图片路径经过符号链接目录")
            if current == current.parent:
                raise MemeMoveError("源图片路径无法归属于 memes/")
            current = current.parent

        target_relative_dir = (
            Path(".") if target_collection_id == 0 else Path(target_name)
        )
        return Path(old_entry.image_path), target_relative_dir

    async def _execute_move(self, req: _WriteRequest) -> MoveResult:
        """在写锁内执行文件、SQLite 与 Chroma 的补偿式移动。

        任一步抛出异常（包括同步调用已产生副作用后抛错）都会按移动前 SQLite 与
        Chroma 快照及文件最终存在状态恢复，并复核最终快照。

        Args:
            req: 包含源内部 ID 与目标合集编号的写请求。

        Returns:
            从持久 SQLite 条目构造的实际移动结果。

        Raises:
            ValueError: 源条目不存在或已属于目标合集。
            CollectionNotFoundError: 目标普通合集不存在。
            DuplicateMemeInCollectionError: 目标合集存在相同文本。
            MemeMoveError: 移动或补偿失败。
        """
        old_entry = await asyncio.to_thread(
            self._metadata_store.get_entry,
            req.entry_id,
        )
        if old_entry is None:
            if req.expected_source is not None:
                raise MemeMoveSourceExpiredError("源表情包已不存在")
            raise ValueError(f"entry_id={req.entry_id} 不存在")
        if req.expected_source is not None and old_entry != req.expected_source.entry:
            raise MemeMoveSourceExpiredError("源表情包元数据已变化")
        source_relative_path = Path(old_entry.image_path)
        try:
            source_identity = await asyncio.to_thread(
                get_regular_file_identity,
                self._memes_dir,
                source_relative_path,
            )
        except SecureMoveError as exc:
            if req.expected_source is not None:
                raise MemeMoveSourceExpiredError("源表情包文件已变化") from exc
            raise MemeMoveError(str(exc)) from exc
        if (
            req.expected_source is not None
            and source_identity != req.expected_source.file_identity
        ):
            raise MemeMoveSourceExpiredError("源表情包文件身份已变化")
        target_name = await asyncio.to_thread(
            self.resolve_move_target_name,
            req.target_collection_id,
        )
        if (
            req.expected_target_name is not None
            and target_name != req.expected_target_name
        ):
            raise CollectionNotFoundError(req.expected_target_name)
        if old_entry.collection_id == req.target_collection_id:
            raise ValueError("表情包已属于目标合集")
        conflict_id = await asyncio.to_thread(
            self._metadata_store.get_id_by_text,
            old_entry.text,
            collection_id=req.target_collection_id,
        )
        if conflict_id is not None:
            raise DuplicateMemeInCollectionError(conflict_id)
        local_id = await asyncio.to_thread(
            self._metadata_store.find_next_local_id,
            req.target_collection_id,
        )
        source_relative_path, target_relative_dir = await asyncio.to_thread(
            self._resolve_move_paths,
            old_entry,
            req.target_collection_id,
            target_name,
        )
        vector_store = self._vector_store
        old_vector_records = await vector_store.snapshot_records([old_entry.id])
        move_state: SecureMoveResult | SecureMoveError | None = None

        try:
            move_state = await asyncio.to_thread(
                secure_move_file,
                self._memes_dir,
                source_relative_path,
                target_relative_dir,
                first_suffix=1,
                expected_source_identity=source_identity,
            )
            new_relative_path = move_state.relative_path.as_posix()
            updated = await asyncio.to_thread(
                self._metadata_store.update,
                old_entry.id,
                image_path=new_relative_path,
                collection_id=req.target_collection_id,
                local_id=local_id,
            )
            if not updated:
                raise ValueError(f"entry_id={old_entry.id} 不存在")
            await vector_store.update_collection_id(
                old_entry.id,
                req.target_collection_id,
            )
            persisted_entry = await asyncio.to_thread(
                self._metadata_store.get_entry,
                old_entry.id,
            )
            if persisted_entry is None:
                raise RuntimeError("移动完成后无法读取持久条目")
        except BaseException as exc:
            if isinstance(exc, SecureMoveError):
                move_state = exc
            compensation_errors = await self._compensate_move(
                old_entry,
                old_vector_records,
                move_state,
            )
            if compensation_errors:
                logger.critical(
                    "移动补偿失败: entry_id=%s, errors=%s",
                    old_entry.id,
                    compensation_errors,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            raise MemeMoveError(str(exc)) from exc

        return MoveResult(
            entry_id=persisted_entry.id,
            old_public_id=old_entry.public_id,
            new_public_id=persisted_entry.public_id,
            target_collection_name=persisted_entry.collection_name,
            old_image_path=old_entry.image_path,
            new_image_path=persisted_entry.image_path,
        )

    async def _compensate_move(
        self,
        old_entry: MemeEntry,
        old_vector_records: list[VectorRecord],
        move_state: SecureMoveResult | SecureMoveError | None,
    ) -> list[str]:
        """恢复移动前的 SQLite、文件与完整 Chroma 快照。

        Args:
            old_entry: 移动前 SQLite 条目快照。
            old_vector_records: 移动前完整向量快照。
            move_state: 安全文件 helper 返回或抛出的最终状态。

        Returns:
            补偿或最终复核失败信息；空列表表示完整恢复。
        """
        errors: list[str] = []
        try:
            await asyncio.to_thread(
                self._metadata_store.update,
                old_entry.id,
                image_path=old_entry.image_path,
                collection_id=old_entry.collection_id,
                local_id=old_entry.local_id,
            )
        except BaseException as exc:
            errors.append(f"SQLite 恢复调用失败: {exc}")

        try:
            await asyncio.to_thread(
                self._restore_move_file,
                old_entry,
                move_state,
            )
        except BaseException as exc:
            errors.append(f"文件恢复失败: {exc}")

        vector_store = self._vector_store
        try:
            await vector_store.restore_records(old_vector_records)
        except BaseException as exc:
            errors.append(f"Chroma 恢复调用失败: {exc}")

        try:
            persisted = await asyncio.to_thread(
                self._metadata_store.get_entry,
                old_entry.id,
            )
            if persisted != old_entry:
                errors.append(f"SQLite 最终快照不一致: {persisted!r}")
        except BaseException as exc:
            errors.append(f"SQLite 最终快照检查失败: {exc}")
        try:
            source_path = self._memes_dir / old_entry.image_path
            if not source_path.is_file() or source_path.is_symlink():
                errors.append("源文件最终状态不一致")
            if move_state is not None and move_state.relative_path is not None:
                target_path = self._memes_dir / move_state.relative_path
                if target_path.exists() or target_path.is_symlink():
                    errors.append("目标文件最终仍存在")
        except BaseException as exc:
            errors.append(f"文件最终快照检查失败: {exc}")
        try:
            restored_records = await vector_store.snapshot_records([old_entry.id])
            if restored_records != old_vector_records:
                errors.append("Chroma 最终快照不一致")
        except BaseException as exc:
            errors.append(f"Chroma 最终快照检查失败: {exc}")
        return errors

    def _restore_move_file(
        self,
        old_entry: MemeEntry,
        move_state: SecureMoveResult | SecureMoveError | None,
    ) -> None:
        """使用同一安全 helper 按最终状态恢复源文件。

        Args:
            old_entry: 移动前条目快照。
            move_state: 安全移动 helper 的最终状态。

        Raises:
            OSError: 无法恢复源文件或清理目标目录。
        """
        if move_state is None or move_state.relative_path is None:
            return
        source_path = self._memes_dir / old_entry.image_path
        if move_state.source_removed:
            source_parent = Path(old_entry.image_path).parent
            restore_result = secure_move_file(
                self._memes_dir,
                move_state.relative_path,
                source_parent,
                target_filename=Path(old_entry.image_path).name,
                expected_source_identity=move_state.target_identity,
            )
            if restore_result.relative_path != Path(old_entry.image_path):
                raise OSError("源文件未恢复到原目录项")
        if not source_path.is_file() or source_path.is_symlink():
            raise OSError("源文件恢复后不是普通文件")

    def move_to_replaced(self, filename: str) -> str:
        """将被替换的文件移动到 memes_replaced/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._replaced_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._replaced_dir, Path(filename).name)
        shutil.move(str(src), str(dst))
        logger.info("已归档被替换文件: %s -> %s", filename, dst)
        return str(dst)
