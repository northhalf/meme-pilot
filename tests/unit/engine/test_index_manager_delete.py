"""IndexManager.delete() 单元测试。"""

from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio

from bot.engine.index_manager import (
    DeleteResult,
    IndexManager,
)
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.types import MemeCollection, MemePublicId
from bot.engine.vector_store import VectorStore
from bot.session import ChatScope


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


class FakeMetadataStore:
    """delete 测试专用内存 MetadataStore。"""

    def __init__(self) -> None:
        self._entries: dict[int, MemeEntry] = {}

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def entry_count(self) -> int:
        return len(self._entries)

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return dict(self._entries)

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)

    def get_entries(self, collection_id: int | None = None) -> dict[int, MemeEntry]:
        if collection_id is None:
            return dict(self._entries)
        return {
            eid: e
            for eid, e in self._entries.items()
            if e.collection_id == collection_id
        }

    def get_entry_by_public_id(self, public_id: MemePublicId) -> MemeEntry | None:
        for eid, e in self._entries.items():
            if (
                e.collection_id == public_id.collection_id
                and e.local_id == public_id.local_id
            ):
                return e
        return None

    def get_id_by_text(self, text: str, *, collection_id: int = 0) -> int | None:
        for eid, e in self._entries.items():
            if e.collection_id == collection_id and e.text == text:
                return eid
        return None

    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> int:
        eid = 1 if not self._entries else max(self._entries) + 1
        self._entries[eid] = MemeEntry(
            id=eid,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=tags or [],
            collection_id=collection_id,
            local_id=eid,
        )
        return eid

    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = None,
        text: str | None = None,
        speaker: str | None = None,
        tags: list[str] | None = None,
        collection_id: int | None = None,
        local_id: int | None = None,
    ) -> bool:
        e = self._entries.get(entry_id)
        if e is None:
            return False
        self._entries[entry_id] = MemeEntry(
            id=entry_id,
            image_path=image_path if image_path is not None else e.image_path,
            text=text if text is not None else e.text,
            speaker=speaker if speaker is not None else e.speaker,
            tags=tags if tags is not None else e.tags,
            collection_id=collection_id
            if collection_id is not None
            else e.collection_id,
            local_id=local_id if local_id is not None else e.local_id,
        )
        return True

    def remove(self, entry_id: int) -> bool:
        return self._entries.pop(entry_id, None) is not None

    def create_collection(self, name: str) -> MemeCollection:
        return MemeCollection(id=1, name=name)

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        return None

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        return None

    def list_collections(self) -> list[MemeCollection]:
        return []

    def collection_entry_count(self, collection_id: int | None) -> int:
        if collection_id is None:
            return len(self._entries)
        return len(
            [e for e in self._entries.values() if e.collection_id == collection_id]
        )

    def get_selected_collection(self, scope: ChatScope) -> int:
        return 0

    def set_selected_collection(self, scope: ChatScope, collection_id: int) -> None:
        pass


class FakeVectorStore:
    """delete 测试专用内存 VectorStore（无实际行为）。"""

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def count(self) -> int:
        return 0

    async def upsert(
        self, entry_id: int, embedding: list[float], *, collection_id: int = 0
    ) -> None:
        pass

    async def remove(self, entry_id: int) -> None:
        pass

    async def remove_many(self, entry_ids: list[int]) -> None:
        pass

    async def query(
        self,
        query_embedding: list[float],
        n_results: int | None = 10,
        *,
        collection_id: int | None = None,
    ) -> list:
        return []

    async def get_all_ids(self) -> set[int]:
        return set()

    async def rebuild_all(
        self,
        items: list[tuple[int, list[float]]] | list[tuple[int, list[float], int]],
    ) -> None:
        pass


@pytest_asyncio.fixture
async def index_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造带 Fake Store 的 IndexManager。"""
    monkeypatch.setenv("READ_LOCK_TIMEOUT", "30")
    monkeypatch.setenv("ADD_COMMAND_TIMEOUT", "60")

    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = FakeMetadataStore()
    vector_store = FakeVectorStore()

    manager = IndexManager(
        metadata_store=cast(MetadataStore, metadata_store),
        vector_store=cast(VectorStore, vector_store),
        memes_dir=str(memes_dir),
    )
    await manager.load()
    return manager


# ---------------------------------------------------------------------------
# delete 测试
# ---------------------------------------------------------------------------


class TestDelete:
    """IndexManager.delete() 单元测试。"""

    @pytest.mark.asyncio
    async def test_delete_moves_file_to_deleted_dir(
        self, index_manager: IndexManager
    ) -> None:
        """删除条目时，图片从 memes/ 移动到 memes_deleted/。"""
        image_path = "test.jpg"
        src = index_manager._memes_dir / image_path
        src.write_text("image data")
        index_manager._metadata_store.add(image_path, "文本")

        result = await index_manager.delete([1])

        assert isinstance(result, DeleteResult)
        assert result.deleted_ids == (1,)
        assert result.not_found_ids == ()
        assert result.failed_ids == ()
        assert not src.exists()
        assert (index_manager._deleted_dir / image_path).exists()
        assert index_manager._metadata_store.get_entry(1) is None

    @pytest.mark.asyncio
    async def test_delete_not_found_id(self, index_manager: IndexManager) -> None:
        """entry_id 不存在时返回 not_found_ids。"""
        result = await index_manager.delete([999])

        assert result.deleted_ids == ()
        assert result.not_found_ids == (999,)
        assert result.failed_ids == ()

    @pytest.mark.asyncio
    async def test_delete_unique_filename_on_conflict(
        self, index_manager: IndexManager
    ) -> None:
        """memes_deleted/ 已存在同名文件时，生成带序号的唯一文件名。"""
        image_path = "test.jpg"
        existing = index_manager._deleted_dir / image_path
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("old deleted")

        src = index_manager._memes_dir / image_path
        src.write_text("image data")
        index_manager._metadata_store.add(image_path, "文本")

        result = await index_manager.delete([1])

        assert result.deleted_ids == (1,)
        assert result.not_found_ids == ()
        assert result.failed_ids == ()
        assert not src.exists()
        assert existing.exists()
        assert (index_manager._deleted_dir / "test_1.jpg").exists()

    @pytest.mark.asyncio
    async def test_delete_move_failure_preserves_entry(
        self,
        index_manager: IndexManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """移图失败时索引原样保留，避免下次 refresh 重新入库（已删表情包复活）。

        先移文件再删索引：移图抛异常时 sqlite/chroma 均未删除，文件仍在 memes/，
        id 记入 failed_ids，用户重试仍可成功。
        """
        image_path = "test.jpg"
        src = index_manager._memes_dir / image_path
        src.write_text("image data")
        index_manager._metadata_store.add(image_path, "文本")

        def _fail_move(src_path: str, dst_path: str) -> None:
            raise OSError("模拟移图失败")

        monkeypatch.setattr("shutil.move", _fail_move)

        result = await index_manager.delete([1])

        assert result.deleted_ids == ()
        assert result.not_found_ids == ()
        assert result.failed_ids == ((1, "模拟移图失败"),)
        # 索引原样保留：文件仍在 memes/，条目仍在 store
        assert src.exists()
        assert index_manager._metadata_store.get_entry(1) is not None
