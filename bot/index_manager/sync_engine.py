"""SyncEngine - 索引刷新全流程：phase0 一致性 / phase1 删除多余 / phase2 补缺失。

从 IndexManager 抽离，承接 refresh 的 phase 编排（扫描 -> 一致性 -> 删除 -> 合集 -> 新增）。
门面 IndexManager 持有本类实例，_run_sync_internal 直指各 phase 方法；
_scan_meme_files / _get_chroma_ids 经门面薄委托保留测试 monkeypatch 接缝。
_refresh_active / _refresh_task 真实状态归本类，门面经 forwarding property 透传。
"""

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.protocols import EmbeddingProvider
from bot.engine.vector_store import VectorStore
from bot.log_context import timed

from .image_pipeline import ImagePipeline
from .index_types import FileSystemSnapshot

logger = logging.getLogger(__name__)


class SyncEngine:
    """索引刷新引擎：phase0 一致性 / phase1 删除多余 / phase2 补缺失。

    Args:
        metadata_store: 元数据存储。
        vector_store: 向量存储。
        memes_dir: 表情包图片目录。
        image_pipeline: 图片管道（phase2 处理新图、phase0 读 embedding provider）。
        process_image_pipeline: 图片管道回调（保留门面 monkeypatch 接缝）。
        move_to_replaced: 去重归档回调（指向门面 coordinator._move_to_replaced）。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
        image_pipeline: ImagePipeline,
        process_image_pipeline: Callable[[str], Awaitable[tuple[str, str, list[float]]]],
        move_to_replaced: Callable[[str], str],
    ) -> None:
        """初始化 SyncEngine。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            memes_dir: 表情包图片目录路径。
            image_pipeline: 图片管道实例。
            process_image_pipeline: 图片处理回调（filename -> (final_filename, text, embedding)）。
            move_to_replaced: 去重归档回调（filename -> 归档路径）。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = memes_dir
        self._image_pipeline = image_pipeline
        self._process_image_pipeline = process_image_pipeline
        self._move_to_replaced = move_to_replaced
        self._refresh_active = False
        self._refresh_task: asyncio.Task | None = None

    @property
    def _embedding_provider(self) -> EmbeddingProvider | None:
        """转发至 ImagePipeline._embedding_provider（保留测试 rebind 接缝）。"""
        return self._image_pipeline._embedding_provider

    @timed(logger, "索引刷新-阶段0")
    async def _sync_phase0_consistency(self, failed: list[str]) -> None:
        """阶段0：对齐 sqlite ↔ chroma 的 id 集合。

        - chroma 为空且 sqlite 有数据 -> 全量重 embed 后 rebuild_all。
        - sqlite 有、chroma 无的 id -> 逐条重 embed 并 upsert。
        - chroma 有、sqlite 无的 id -> 删孤儿向量。

        Args:
            failed: 失败文件名收集列表，阶段0 重 embed 失败的 image_path 追加至此。
        """
        entries = await asyncio.to_thread(self._metadata_store.get_all_entries)
        sqlite_ids = set(entries)
        vs_count = self._vector_store.count()
        chroma_ids = await self.get_chroma_ids()

        # chroma 损坏/为空、sqlite 有数据 -> rebuild_all 全量重 embed
        if vs_count == 0 and sqlite_ids:
            await self._rebuild_all_from_sqlite(entries, failed)
            return

        # sqlite 有、chroma 无 -> 重 embed upsert
        missing = sqlite_ids - chroma_ids
        restored: set[int] = set()
        for idx, eid in enumerate(missing):
            text = entries[eid].text
            if not text:
                continue
            try:
                vec = await self._embedding_provider.embed(text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
            except Exception as exc:
                logger.error("阶段0 重 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            try:
                await self._vector_store.upsert(
                    eid, vec, collection_id=entries[eid].collection_id
                )
            except Exception as exc:
                logger.error("阶段0 补写向量失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            restored.add(eid)
            logger.debug("已处理 %d/%d 张图片", idx + 1, len(missing))

        # chroma 有、sqlite 无 -> 删孤儿向量
        orphans = chroma_ids - sqlite_ids
        if orphans:
            logger.info("阶段0 清理孤儿向量: %s", orphans)
            await self._vector_store.remove_many(list(orphans))

        chroma_ids = (chroma_ids | restored) - orphans
        vector_store = self._vector_store
        vector_collections = await vector_store.get_collection_ids()
        for entry_id, entry in entries.items():
            if entry_id in chroma_ids and (
                vector_collections.get(entry_id) != entry.collection_id
            ):
                await vector_store.update_collection_id(entry_id, entry.collection_id)

    async def get_chroma_ids(self) -> set[int]:
        """调用 VectorStore.get_all_ids() 获取全部向量 ID，与 embedding 维度无关。

        Returns:
            chroma 中现存向量对应的 entry_id 集合；chroma 为空时返回空集。
        """
        return await self._vector_store.get_all_ids()

    async def _rebuild_all_from_sqlite(
        self, entries: dict[int, MemeEntry], failed: list[str]
    ) -> None:
        """chroma 为空、sqlite 有数据 -> 全量重 embed 后 rebuild_all。

        Args:
            entries: sqlite 当前全量条目（id -> MemeEntry）。
            failed: 失败文件名收集列表，重 embed 失败的 image_path 追加至此。
        """
        items: list[tuple[int, list[float], int]] = []
        for idx, (eid, entry) in enumerate(entries.items()):
            if not entry.text:
                continue
            try:
                vec = await self._embedding_provider.embed(entry.text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
            except Exception as exc:
                logger.error("阶段0 全量重建 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entry.image_path)
                continue
            items.append((eid, vec, entry.collection_id))
            logger.debug("已处理 %d/%d 张图片", idx + 1, len(entries))
        await self._vector_store.rebuild_all(items)

    @timed(logger, "索引刷新-阶段1")
    async def _sync_phase1_delete(self, existing: set[str], failed: list[str]) -> int:
        """阶段1：删除缺失图片索引，向量删除失败时恢复 SQLite 条目。

        Args:
            existing: sync 开始时扫描的 POSIX 相对路径集合。
            failed: 失败图片路径收集列表。

        Returns:
            两个存储均删除成功的图片数量。
        """
        entries = await asyncio.to_thread(self._metadata_store.get_all_entries)
        metadata_store = self._metadata_store
        deleted = 0
        for eid, entry in entries.items():
            if entry.image_path in existing:
                continue
            logger.info(
                "图片已删除，移除索引: id=%s, image_path=%s",
                eid,
                entry.image_path,
            )
            await asyncio.to_thread(self._metadata_store.remove, eid)
            try:
                await self._vector_store.remove(eid)
            except Exception as exc:
                logger.error(
                    "删除向量调用异常，确认最终状态: id=%s, image_path=%s, error=%s",
                    eid,
                    entry.image_path,
                    exc,
                )
                try:
                    vector_ids = await self._vector_store.get_all_ids()
                except Exception as state_exc:
                    logger.critical(
                        "删除向量异常且无法确认最终状态: id=%s, error=%s",
                        eid,
                        state_exc,
                    )
                    raise
                if eid not in vector_ids:
                    logger.warning("删除向量调用异常但删除已生效: id=%s", eid)
                    deleted += 1
                    continue
                try:
                    await asyncio.to_thread(
                        metadata_store.add_with_id,
                        entry.id,
                        entry.image_path,
                        entry.text,
                        entry.speaker,
                        list(entry.tags),
                        collection_id=entry.collection_id,
                        local_id=entry.local_id,
                    )
                except Exception as rollback_exc:
                    logger.critical(
                        "删除向量失败且 sqlite 恢复失败: id=%s, error=%s",
                        eid,
                        rollback_exc,
                    )
                    raise
                failed.append(entry.image_path)
                continue
            deleted += 1
        return deleted

    async def _sync_collections_delete(
        self, existing_directories: set[str]
    ) -> tuple[int, int]:
        """删除一级目录已消失的空合集，并回退引用它的聊天窗口。

        Args:
            existing_directories: 当前实际存在的非隐藏一级目录名称。

        Returns:
            删除合集数与回退窗口数。
        """
        deleted = reset = 0
        metadata_store = self._metadata_store
        for collection in metadata_store.list_collections():
            if collection.name in existing_directories:
                continue
            reset += await asyncio.to_thread(
                metadata_store.delete_collection_and_reset_scopes,
                collection.id,
            )
            deleted += 1
        return deleted, reset

    async def _sync_collections_add(self, directories_with_images: set[str]) -> int:
        """登记递归包含受支持图片的新一级目录。

        Args:
            directories_with_images: 含图片的一级目录名称。

        Returns:
            本次新登记的合集数量。
        """
        added = 0
        metadata_store = self._metadata_store
        for name in sorted(directories_with_images):
            if metadata_store.get_collection_by_name(name) is not None:
                continue
            await asyncio.to_thread(metadata_store.create_collection, name)
            added += 1
        return added

    @timed(logger, "索引刷新-阶段2")
    async def _sync_phase2_add(
        self, existing: dict[str, str | None], failed: list[str]
    ) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR->embed，按合集串行分类与写入。

        Args:
            existing: POSIX 相对路径到一级合集目录名的映射；根目录图片值为 None。
            failed: 失败文件名收集列表，处理异常或 upsert 失败回滚的文件名追加至此。

        Returns:
            (added, deduped, no_text_moved) 三元组：新增、去重删除、无文字移走数量。
        """
        entries = await asyncio.to_thread(self._metadata_store.get_all_entries)
        metadata_store = self._metadata_store
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
        if not new_files:
            return (0, 0, 0)

        logger.info("开始并行处理 %d 张新增图片", len(new_files))

        raw = await asyncio.gather(
            *(self._process_image_pipeline(filename) for filename in new_files),
            return_exceptions=True,
        )

        success: dict[str, tuple[str, list[float], int]] = {}
        for idx, (filename, result) in enumerate(zip(new_files, raw)):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
                continue
            final_filename, text, embedding = result
            collection_name = existing[filename]
            if collection_name is None:
                collection_id = 0
            else:
                collection = metadata_store.get_collection_by_name(collection_name)
                if collection is None:
                    logger.error("新增图片所属合集未登记: filename=%s", filename)
                    failed.append(filename)
                    continue
                collection_id = collection.id
            # 并发同名去重：多张新增图转 webp 后可能产出同名 final_filename
            # （_convert_image_to_webp 并行 resolve 的 TOCTOU 竞态兜底）。
            # 基于 success dict 已有 key 去重，不依赖文件存在性。
            if final_filename in success:
                final_path = Path(final_filename)
                parent = final_path.parent
                stem = final_path.stem
                suffix = final_path.suffix
                old_path = self._memes_dir / final_path
                n = 1
                candidate = parent / f"{stem}_{n}{suffix}"
                while (
                    candidate.as_posix() in success
                    or (self._memes_dir / candidate).exists()
                ):
                    n += 1
                    candidate = parent / f"{stem}_{n}{suffix}"
                final_filename = candidate.as_posix()
                new_path = self._memes_dir / candidate
                if old_path.exists():
                    shutil.move(str(old_path), str(new_path))
                logger.info("并发同名去重 rename: %s", final_filename)
            success[final_filename] = (text, embedding, collection_id)
            logger.debug("已处理 %d/%d 张图片", idx + 1, len(new_files))

        winner_keys: set[tuple[int, str]] = {
            (entry.collection_id, entry.text)
            for entry in entries.values()
            if entry.text
        }

        added = deduped = no_text_moved = 0
        for filename in sorted(success):
            text, embedding, collection_id = success[filename]
            if not text:
                await asyncio.to_thread(self._image_pipeline.move_to_no_text, filename)
                no_text_moved += 1
                continue
            dedupe_key = (collection_id, text)
            if dedupe_key in winner_keys:
                try:
                    archived_path = await asyncio.to_thread(
                        self._move_to_replaced, filename
                    )
                except Exception as exc:
                    logger.error(
                        "去重新图归档失败，跳过该文件: filename=%s, error=%s",
                        filename,
                        exc,
                    )
                    failed.append(filename)
                    continue
                logger.info(
                    "新图与已有索引去重，已归档新图: filename=%s, archived=%s",
                    filename,
                    archived_path,
                )
                deduped += 1
                continue
            # 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite
            eid = await asyncio.to_thread(
                self._metadata_store.add,
                filename,
                text,
                collection_id=collection_id,
            )
            try:
                await self._vector_store.upsert(
                    eid, embedding, collection_id=collection_id
                )
            except Exception as exc:
                logger.error("新增 upsert 失败，回滚 sqlite: id=%s, error=%s", eid, exc)
                await asyncio.to_thread(self._metadata_store.remove, eid)
                failed.append(filename)
                continue
            winner_keys.add(dedupe_key)
            added += 1
            logger.info("新增图片已加入索引: id=%s, filename=%s", eid, filename)

        return (added, deduped, no_text_moved)

    @timed(logger, "扫描 memes/ 目录")
    def scan_meme_files(self) -> FileSystemSnapshot:
        """递归扫描 memes/，且不跟随任何符号链接。

        使用 os.scandir 的 DirEntry 缓存判定 symlink/类型，避免 os.walk +
        Path.is_symlink 对每个文件重复 lstat；在 bind mount / WSL2 下显著
        降低扫描耗时。

        Returns:
            包含图片路径、一级目录和含图片目录的文件系统快照。
        """
        files: dict[str, str | None] = {}
        directories: set[str] = set()
        directories_with_images: set[str] = set()

        for entry in os.scandir(self._memes_dir):
            if entry.is_symlink():
                continue
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_file(follow_symlinks=False):
                if ImagePipeline.has_supported_ext(name):
                    files[name] = None
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            directories.add(name)
            if self._scan_collection_dir(entry, name, files):
                directories_with_images.add(name)

        return FileSystemSnapshot(files, directories, directories_with_images)

    def _scan_collection_dir(
        self,
        top_entry: "os.DirEntry[str]",
        collection_name: str,
        files: dict[str, str | None],
    ) -> bool:
        """递归扫描单个合集目录，登记受支持图片，不跟随符号链接。

        用显式栈替代 os.walk，保留 DirEntry 以复用 d_type 缓存，避免对每个
        文件单独 lstat 判定 symlink。

        Args:
            top_entry: 合集一级目录的 DirEntry。
            collection_name: 合集一级目录名，作为 image_path 的归属标记。
            files: 待填充的相对路径 -> 合集名映射。

        Returns:
            该合集是否含至少一张受支持图片。
        """
        has_image = False
        memes_root = self._memes_dir
        stack: list["os.DirEntry[str]"] = [top_entry]
        while stack:
            current = stack.pop()
            for entry in os.scandir(current):
                if entry.is_symlink():
                    continue
                name = entry.name
                if name.startswith("."):
                    continue
                if entry.is_file(follow_symlinks=False):
                    if not ImagePipeline.has_supported_ext(name):
                        continue
                    relative_path = os.path.relpath(
                        entry.path, memes_root
                    ).replace(os.sep, "/")
                    files[relative_path] = collection_name
                    has_image = True
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(entry)
        return has_image
