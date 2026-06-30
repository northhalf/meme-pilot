"""索引管理模块 — 薄编排层。

持有 MetadataStore + VectorStore + providers，负责压缩→OCR→Embed 管道编排、
sync 四阶段（含阶段0跨库一致性修复）、跨库写入一致性、全局锁、并发上限、
去重/无文字移图。不直接写 SQL/Chroma，全部委托两个 Store。
"""

import asyncio
import itertools
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from bot.engine.image_optimizer import OptimizeResult
from bot.engine.metadata_store import MemeEntry
from bot.engine.protocols import EmbeddingProvider
from bot.engine.vector_store import VectorHit

logger = logging.getLogger(__name__)


def resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。

    Returns:
        目标目录下不冲突的完整路径。
    """
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for n in itertools.count(2):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")


class IndexCorruptedError(Exception):
    """索引数据库结构损坏时抛出。"""


class CompressionError(RuntimeError):
    """图片压缩失败。"""


class OcrError(RuntimeError):
    """OCR 识别失败。"""


class EmbeddingError(RuntimeError):
    """Embedding 生成失败。"""


class OcrProvider(Protocol):
    """OCR 服务提供者协议。ocr() 返回去除所有空白后的文本。"""

    async def ocr(self, image_path: str) -> str: ...


class MetadataStoreProtocol(Protocol):
    """元数据存储协议（IndexManager 编排所需接口子集）。

    仅声明 IndexManager 实际调用的方法（load/entry_count/get_all_entries/
    get_entry/get_id_by_text/add/update/remove）。
    """

    def load(self) -> None: ...
    def entry_count(self) -> int: ...
    def get_all_entries(self) -> dict[int, MemeEntry]: ...
    def get_entry(self, entry_id: int) -> MemeEntry | None: ...
    def get_id_by_text(self, text: str) -> int | None: ...
    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int: ...
    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = None,
        text: str | None = None,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> bool: ...
    def remove(self, entry_id: int) -> bool: ...


class VectorStoreProtocol(Protocol):
    """向量存储协议（IndexManager 编排所需接口子集）。

    仅声明 IndexManager 实际调用的方法（load/count/upsert/remove/
    remove_many/query/rebuild_all）。
    """

    def load(self) -> None: ...
    def count(self) -> int: ...

    async def upsert(self, entry_id: int, embedding: list[float]) -> None: ...
    async def remove(self, entry_id: int) -> None: ...
    async def remove_many(self, entry_ids: list[int]) -> None: ...
    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]: ...
    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None: ...


class ImageOptimizerProtocol(Protocol):
    """图片压缩器协议。IndexManager 仅调用 optimize。"""

    async def optimize(self, image_path: str) -> OptimizeResult: ...


@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因 text 命中已有条目而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class AddResult:
    """add_single_file() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID（int）；无文字移图场景为 None。
        reason: 结果类别：added / replaced / no_text。
        text: OCR 文本（无空格）。
        replaced_image_path: reason="replaced" 时为被删旧图路径，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
    """

    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    moved_to: str | None = None


class IndexManager:
    """索引管理薄编排层。

    持有 MetadataStore + VectorStore + providers，负责管道编排与跨库一致性。

    Attributes:
        _metadata_store: 元数据存储。
        _vector_store: 向量存储。
        _memes_dir: 表情包图片目录。
        _no_text_dir: 无文字图目录。
        _ocr_provider / _embedding_provider / _optimizer: providers。
        _lock: sync 独占 asyncio.Lock。
        _is_syncing: sync 是否在执行。
        _sync_semaphore / _add_sem: 并发上限信号量。

    Class Attributes:
        SUPPORTED_EXTENSIONS: 支持的图片扩展名集合。
        DEFAULT_SYNC_CONCURRENCY: 并行同步默认并发上限。
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )
    DEFAULT_SYNC_CONCURRENCY: int = 5

    def __init__(
        self,
        metadata_store: MetadataStoreProtocol,
        vector_store: VectorStoreProtocol,
        memes_dir: str,
        no_text_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizerProtocol | None = None,
        sync_concurrency: int | None = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            memes_dir: 表情包图片目录路径。
            no_text_dir: 无文字图目录；None 时取 memes_dir 同级的 meme_no_text/。
            ocr_provider: OCR 服务提供者。
            embedding_provider: Embedding 服务提供者。
            optimizer: 图片压缩器。
            sync_concurrency: 并发上限，None/非正用默认值。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = Path(memes_dir)
        if no_text_dir is not None:
            self._no_text_dir = Path(no_text_dir)
        else:
            self._no_text_dir = Path(memes_dir).parent / "meme_no_text"
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider
        self._optimizer = optimizer

        self._lock = asyncio.Lock()
        concurrency = (
            sync_concurrency
            if isinstance(sync_concurrency, int) and sync_concurrency > 0
            else self.DEFAULT_SYNC_CONCURRENCY
        )
        self._sync_semaphore = asyncio.Semaphore(concurrency)
        self._add_sem = asyncio.Semaphore(concurrency)
        self._is_syncing: bool = False

    # ------------------------------------------------------------------
    # load / 锁
    # ------------------------------------------------------------------

    def load(self) -> None:
        """委托两个 Store.load()，并记录当前条目数。

        启动时必须调用此方法后再使用其他查询或写入方法。
        """
        self._metadata_store.load()
        self._vector_store.load()
        logger.info("IndexManager 加载完成: %d 条记录", self.entry_count)

    async def acquire_lock(self) -> bool:
        """非阻塞尝试获取索引更新锁。

        Returns:
            True 表示成功获取锁；False 表示锁已被占用，调用方应回复
            "索引正在更新，请稍后再试"。
        """
        if self._lock.locked():
            return False
        await self._lock.acquire()
        self._is_syncing = True
        logger.debug("索引更新锁已获取")
        return True

    def release_lock(self) -> None:
        """释放索引更新锁。未锁定时调用安全。"""
        self._is_syncing = False
        if self._lock.locked():
            self._lock.release()
            logger.debug("索引更新锁已释放")

    @property
    def is_locked(self) -> bool:
        """索引是否处于锁定状态。

        Returns:
            True 表示索引更新锁被持有，False 表示未锁定。
        """
        return self._is_syncing

    @property
    def entry_count(self) -> int:
        """当前索引条目总数。

        Returns:
            当前 sqlite 中的条目数量。
        """
        return self._metadata_store.entry_count()

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------

    async def sync_with_filesystem(self) -> SyncResult:
        """按文件名同步索引与 memes/ 目录（四阶段）。

        0. 一致性修复：对齐 sqlite ↔ chroma id 集合。
        1. 删除：memes/ 已不存在的图片。
        2. 新增：新图并行 OCR→embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []

        await self._sync_phase0_consistency(failed)
        deleted_count = await self._sync_phase1_delete()
        added_count, deduped_count, no_text_count = await self._sync_phase2_add(failed)

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
            added_count,
            deleted_count,
            deduped_count,
            no_text_count,
            len(failed),
        )
        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            failed=failed,
        )

    async def _run_sync[_T](
        self, fn: Callable[..., _T], *args: Any, **kwargs: Any
    ) -> _T:
        """用 asyncio.to_thread 包装 MetadataStore 同步方法，避免阻塞事件循环。

        Args:
            fn: 要在线程中执行的同步函数（通常是 MetadataStore 方法）。
            *args: 传给 fn 的位置参数。
            **kwargs: 传给 fn 的关键字参数。

        Returns:
            fn 的返回值（类型由 fn 签名推断）。
        """
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _sync_phase0_consistency(self, failed: list[str]) -> None:
        """阶段0：对齐 sqlite ↔ chroma 的 id 集合。

        - chroma 为空且 sqlite 有数据 → 全量重 embed 后 rebuild_all。
        - sqlite 有、chroma 无的 id → 逐条重 embed 并 upsert。
        - chroma 有、sqlite 无的 id → 删孤儿向量。

        Args:
            failed: 失败文件名收集列表，阶段0 重 embed 失败的 image_path 追加至此。
        """
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        sqlite_ids = set(entries)
        vs_count = self._vector_store.count()
        chroma_ids = await self._get_chroma_ids()

        # chroma 损坏/为空、sqlite 有数据 → rebuild_all 全量重 embed
        if vs_count == 0 and sqlite_ids:
            await self._rebuild_all_from_sqlite(entries, failed)
            return

        # sqlite 有、chroma 无 → 重 embed upsert
        missing = sqlite_ids - chroma_ids
        for eid in missing:
            text = entries[eid].text
            if not text:
                continue
            try:
                vec = await self._embedding_provider.embed(text)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("阶段0 重 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            await self._vector_store.upsert(eid, vec)

        # chroma 有、sqlite 无 → 删孤儿向量
        orphans = chroma_ids - sqlite_ids
        if orphans:
            logger.info("阶段0 清理孤儿向量: %s", orphans)
            await self._vector_store.remove_many(list(orphans))

    async def _get_chroma_ids(self) -> set[int]:
        """获取 chroma 当前所有 id（用 query 全量召回实现）。

        Returns:
            chroma 中现存向量对应的 entry_id 集合；chroma 为空时返回空集。
        """
        if self._vector_store.count() == 0:
            return set()
        # 用零向量召回最多 count 条，提取 id
        n = self._vector_store.count()
        hits = await self._vector_store.query([0.0] * 1024, n_results=n)
        return {h.entry_id for h in hits}

    async def _rebuild_all_from_sqlite(
        self, entries: dict[int, MemeEntry], failed: list[str]
    ) -> None:
        """chroma 为空、sqlite 有数据 → 全量重 embed 后 rebuild_all。

        Args:
            entries: sqlite 当前全量条目（id → MemeEntry）。
            failed: 失败文件名收集列表，重 embed 失败的 image_path 追加至此。
        """
        items: list[tuple[int, list[float]]] = []
        for eid, entry in entries.items():
            if not entry.text:
                continue
            try:
                vec = await self._embedding_provider.embed(entry.text)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("阶段0 全量重建 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entry.image_path)
                continue
            items.append((eid, vec))
        await self._vector_store.rebuild_all(items)

    async def _sync_phase1_delete(self) -> int:
        """阶段1：删除 memes/ 已不存在的图片对应记录。先 sqlite 后 chroma。

        Returns:
            本次删除的图片数量。
        """
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        deleted = 0
        for eid, entry in entries.items():
            if entry.image_path not in existing:
                logger.info(
                    "图片已删除，移除索引: id=%s, image_path=%s", eid, entry.image_path
                )
                await self._run_sync(self._metadata_store.remove, eid)
                await self._vector_store.remove(eid)
                deleted += 1
        return deleted

    async def _sync_phase2_add(self, failed: list[str]) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR→embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Args:
            failed: 失败文件名收集列表，处理异常或 upsert 失败回滚的文件名追加至此。

        Returns:
            (added, deduped, no_text_moved) 三元组：新增、去重删除、无文字移走数量。
        """
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
        if not new_files:
            return (0, 0, 0)

        logger.info("开始并行处理 %d 张新增图片", len(new_files))
        raw = await asyncio.gather(
            *(self._process_new_file(fn) for fn in new_files),
            return_exceptions=True,
        )

        success: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                _, text, embedding = result
                success[filename] = (text, embedding)

        # winner_keys 初始 = 已有条目的 text 集合
        winner_keys: set[str] = {e.text for e in entries.values() if e.text}

        added = deduped = no_text_moved = 0
        for filename in sorted(success):
            text, embedding = success[filename]
            if not text:
                await self._run_sync(self._move_to_no_text, filename)
                no_text_moved += 1
                continue
            if text in winner_keys:
                (self._memes_dir / filename).unlink(missing_ok=True)
                logger.info("新图与已有索引去重，删除新图: filename=%s", filename)
                deduped += 1
                continue
            # 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite
            eid = await self._run_sync(self._metadata_store.add, filename, text)
            try:
                await self._vector_store.upsert(eid, embedding)
            except Exception as exc:
                logger.error("新增 upsert 失败，回滚 sqlite: id=%s, error=%s", eid, exc)
                await self._run_sync(self._metadata_store.remove, eid)
                failed.append(filename)
                continue
            winner_keys.add(text)
            added += 1
            logger.info("新增图片已加入索引: id=%s, filename=%s", eid, filename)

        return (added, deduped, no_text_moved)

    # ------------------------------------------------------------------
    # add_single_file
    # ------------------------------------------------------------------

    async def add_single_file(self, filename: str) -> AddResult:
        """处理单张已保存的图片：压缩→OCR→Embed→写入索引。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            AddResult 描述添加结果。

        Raises:
            CompressionError / OcrError / EmbeddingError: 管道失败。
        """
        async with self._add_sem:
            text, embedding = await self._process_image_pipeline(filename)
        return await self._write_entry(filename, text, embedding)

    async def _write_entry(
        self, filename: str, text: str, embedding: list[float]
    ) -> AddResult:
        """三分类写入：无文字移图 / 去重替换 / 正常新增。

        写入顺序统一"先 sqlite 后 chroma"，失败可回滚。

        Args:
            filename: memes/ 下的文件名。
            text: OCR 去除所有空白后的文本（空串表示无文字）。
            embedding: 与 text 对应的 embedding 向量。

        Returns:
            AddResult 描述本次写入结果（added / replaced / no_text）。

        Raises:
            EmbeddingError: 去重替换或正常新增时 upsert 失败，已回滚 sqlite 后重抛。
        """
        # 1. 无文字 → 移图，不进索引
        if not text:
            moved_to = await self._run_sync(self._move_to_no_text, filename)
            logger.info("OCR 无文字，已移至无文字目录: filename=%s", filename)
            return AddResult(entry_id=None, reason="no_text", moved_to=moved_to)

        # 2. 去重命中已有条目 → update image_path + upsert，删旧图
        old_id = await self._run_sync(self._metadata_store.get_id_by_text, text)
        if old_id is not None:
            old_entry = await self._run_sync(self._metadata_store.get_entry, old_id)
            old_image_path = old_entry.image_path if old_entry else ""
            # 顺序：先改 sqlite 指向新图，再 upsert 向量，最后删旧图
            await self._run_sync(
                self._metadata_store.update, old_id, image_path=filename
            )
            try:
                await self._vector_store.upsert(old_id, embedding)
            except Exception as exc:
                logger.error(
                    "去重替换 upsert 失败，回滚 update: id=%s, error=%s", old_id, exc
                )
                await self._run_sync(
                    self._metadata_store.update, old_id, image_path=old_image_path
                )
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise EmbeddingError(f"去重替换 upsert 失败: {filename}") from exc
            # 删旧图（最后删，保证前序失败时旧图仍在）
            if old_image_path and old_image_path != filename:
                (self._memes_dir / old_image_path).unlink(missing_ok=True)
            logger.info(
                "去重替换: id=%s, 旧=%s, 新=%s", old_id, old_image_path, filename
            )
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                text=text,
                replaced_image_path=old_image_path,
            )

        # 3. 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite + 删图
        eid = await self._run_sync(self._metadata_store.add, filename, text)
        try:
            await self._vector_store.upsert(eid, embedding)
        except Exception as exc:
            logger.error(
                "新增 upsert 失败，回滚 sqlite + 删图: id=%s, error=%s", eid, exc
            )
            await self._run_sync(self._metadata_store.remove, eid)
            (self._memes_dir / filename).unlink(missing_ok=True)
            raise EmbeddingError(f"新增 upsert 失败: {filename}") from exc
        logger.info("已添加索引记录: id=%s, filename=%s", eid, filename)
        return AddResult(entry_id=eid, reason="added", text=text)

    # ------------------------------------------------------------------
    # 管道与工具
    # ------------------------------------------------------------------

    def _scan_meme_files(self) -> set[str]:
        """扫描 memes/，返回受支持扩展名的文件名集合。

        Returns:
            memes/ 下所有受支持图片扩展名的文件名集合（仅文件名，不含路径）。
        """
        return {
            f.name
            for f in self._memes_dir.iterdir()
            if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        }

    async def _process_image_pipeline(self, filename: str) -> tuple[str, list[float]]:
        """压缩 → OCR → Embedding 管道。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            (text, embedding) 二元组：text 为去空白后的 OCR 文本（可能为空串），
            embedding 为对应向量。

        Raises:
            CompressionError: 图片压缩失败。
            OcrError: OCR 服务未注入或调用失败。
            EmbeddingError: Embedding 服务未注入或调用失败。
        """
        image_path = self._memes_dir / filename
        if self._optimizer is not None:
            try:
                await self._optimizer.optimize(str(image_path))
            except Exception as exc:
                raise CompressionError(f"图片压缩失败: {filename}") from exc
        if self._ocr_provider is None:
            raise OcrError("OCR 服务未注入")
        try:
            text = await self._ocr_provider.ocr(str(image_path))
        except Exception as exc:
            raise OcrError(f"OCR 调用失败: {filename}") from exc
        text = "".join(
            text.split()
        )  # 统一去除所有空白（plan line 46 约定 + T8 不变量）
        if self._embedding_provider is None:
            raise EmbeddingError("Embedding 服务未注入")
        try:
            embedding = await self._embedding_provider.embed(text)
        except Exception as exc:
            raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc
        return text, embedding

    async def _process_new_file(self, filename: str) -> tuple[str, str, list[float]]:
        """处理单张新增图片（受 _sync_semaphore 约束）。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            (filename, text, embedding) 三元组，供阶段2 串行分类使用。
        """
        async with self._sync_semaphore:
            text, embedding = await self._process_image_pipeline(filename)
        return filename, text, embedding

    def _move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._no_text_dir, filename)
        shutil.move(str(src), str(dst))
        logger.warning("OCR 未识别到文字，已移至无文字目录: %s -> %s", filename, dst)
        return str(dst)
