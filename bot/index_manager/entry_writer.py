"""EntryWriter — 把单条 MemeEntry + 向量写入两个 Store 的无状态 helper。

从 IndexManager._write_entry 抽离，保持三分类写入（无文字移图 / 去重替换 / 正常新增）
与"先 sqlite 后 chroma"的写入顺序，失败可回滚。无状态、无并发逻辑，由门面持有并委托。
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from bot.engine.collection_manager import CollectionNotFoundError
from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore

from .index_types import AddResult, EmbeddingError

logger = logging.getLogger(__name__)


class EntryWriter:
    """无状态写入器：先 sqlite 后 chroma，失败可回滚。

    Args:
        metadata_store: 元数据存储。
        vector_store: 向量存储。
        memes_dir: memes/ 目录路径。
        move_to_no_text: 无文字移图回调，filename -> 归档路径。
        move_to_replaced: 去重替换归档旧图回调，filename -> 归档路径。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
        move_to_no_text: Callable[[str], str],
        move_to_replaced: Callable[[str], str],
    ) -> None:
        """初始化 EntryWriter。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            memes_dir: memes/ 目录路径。
            move_to_no_text: 无文字移图回调，filename -> 归档路径。
            move_to_replaced: 去重替换归档旧图回调，filename -> 归档路径。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = memes_dir
        self._move_to_no_text = move_to_no_text
        self._move_to_replaced = move_to_replaced

    async def write_entry(
        self,
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        """三分类写入：无文字移图 / 去重替换 / 正常新增。

        写入顺序统一"先 sqlite 后 chroma"，失败可回滚。

        Args:
            filename: memes/ 下的文件名。
            text: OCR 按空白分割后以中文逗号拼接的文本（空串表示无文字）。
            embedding: 与 text 对应的 embedding 向量（list 或 tuple 均可）。
            speaker: 可选说话人。
            tags: 可选标签序列（list 或 tuple 均可）。
            collection_id: 目标合集编号，默认 0（全局）。

        Returns:
            AddResult 描述本次写入结果（added / replaced / no_text）。

        Raises:
            CollectionNotFoundError: 目标普通合集在写入前已不存在。
            EmbeddingError: 去重替换或正常新增时 upsert 失败，已回滚 sqlite 后重抛。
        """
        if collection_id != 0:
            collection = await asyncio.to_thread(
                self._metadata_store.get_collection, collection_id
            )
            if collection is None:
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise CollectionNotFoundError(str(collection_id))

        # 1. 无文字 → 移图，不进索引
        if not text:
            moved_to = await asyncio.to_thread(self._move_to_no_text, filename)
            logger.info("OCR 无文字，已移至无文字目录: filename=%s", filename)
            return AddResult(
                entry_id=None,
                reason="no_text",
                moved_to=moved_to,
                speaker=None,
                tags=(),
            )

        # 2. 去重命中已有条目 → update image_path + upsert，删旧图
        old_id = await asyncio.to_thread(
            self._metadata_store.get_id_by_text,
            text,
            collection_id=collection_id,
        )
        if old_id is not None:
            old_entry = await asyncio.to_thread(self._metadata_store.get_entry, old_id)
            old_image_path = old_entry.image_path if old_entry else ""
            old_speaker = old_entry.speaker if old_entry else None
            old_tags = list(old_entry.tags) if old_entry else []
            old_collection_id = old_entry.collection_id if old_entry else collection_id
            vector_store = self._vector_store
            old_vector_records = await vector_store.snapshot_records([old_id])
            # 顺序：先改 sqlite 指向新图，再 upsert 向量，最后删旧图
            await asyncio.to_thread(
                self._metadata_store.update,
                old_id,
                image_path=filename,
                speaker=speaker,
                tags=tags,
            )
            try:
                await self._vector_store.upsert(
                    old_id, embedding, collection_id=old_collection_id
                )
            except Exception as exc:
                logger.error(
                    "去重替换 upsert 失败，回滚 update: id=%s, error=%s", old_id, exc
                )
                await asyncio.to_thread(
                    self._metadata_store.update,
                    old_id,
                    image_path=old_image_path,
                    speaker=old_speaker,
                    tags=old_tags,
                )
                try:
                    await vector_store.restore_records(old_vector_records)
                except Exception as rollback_exc:
                    logger.critical(
                        "去重替换向量回滚失败: id=%s, error=%s",
                        old_id,
                        rollback_exc,
                    )
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise EmbeddingError(f"去重替换 upsert 失败: {filename}") from exc
            # 归档旧图（最后移动，保证前序失败时旧图仍在）
            archived_path: str | None = None
            if old_image_path and old_image_path != filename:
                try:
                    archived_path = await asyncio.to_thread(
                        self._move_to_replaced, old_image_path
                    )
                except Exception:
                    await asyncio.to_thread(
                        self._metadata_store.update,
                        old_id,
                        image_path=old_image_path,
                        speaker=old_speaker,
                        tags=old_tags,
                    )
                    try:
                        await vector_store.restore_records(old_vector_records)
                    except Exception as rollback_exc:
                        logger.critical(
                            "去重替换归档失败且向量回滚失败: id=%s, error=%s",
                            old_id,
                            rollback_exc,
                        )
                    (self._memes_dir / filename).unlink(missing_ok=True)
                    raise
            logger.info(
                "去重替换: id=%s, 旧=%s, 新=%s, archived=%s",
                old_id,
                old_image_path,
                filename,
                archived_path,
            )
            persisted_entry = await asyncio.to_thread(
                self._metadata_store.get_entry, old_id
            )
            assert persisted_entry is not None
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                text=text,
                public_id=persisted_entry.public_id,
                collection_name=persisted_entry.collection_name,
                replaced_image_path=old_image_path,
                archived_path=archived_path,
                speaker=persisted_entry.speaker,
                tags=tuple(persisted_entry.tags),
            )

        # 3. 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite + 删图
        eid = await asyncio.to_thread(
            self._metadata_store.add,
            filename,
            text,
            speaker,
            tags,
            collection_id=collection_id,
        )
        persisted_entry = await asyncio.to_thread(self._metadata_store.get_entry, eid)
        assert persisted_entry is not None
        try:
            await self._vector_store.upsert(eid, embedding, collection_id=collection_id)
        except Exception as exc:
            logger.error(
                "新增 upsert 失败，回滚 sqlite + 删图: id=%s, error=%s", eid, exc
            )
            await asyncio.to_thread(self._metadata_store.remove, eid)
            (self._memes_dir / filename).unlink(missing_ok=True)
            raise EmbeddingError(f"新增 upsert 失败: {filename}") from exc
        logger.info("已添加索引记录: id=%s, filename=%s", eid, filename)
        return AddResult(
            entry_id=eid,
            reason="added",
            text=text,
            public_id=persisted_entry.public_id,
            collection_name=persisted_entry.collection_name,
            speaker=persisted_entry.speaker,
            tags=tuple(persisted_entry.tags),
        )
