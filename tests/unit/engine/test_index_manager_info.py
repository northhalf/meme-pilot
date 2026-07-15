"""IndexManager.info() 单元测试。"""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from bot.engine.index_manager import IndexInfo, IndexManager
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.types import MemeCollection, MemePublicId
from bot.engine.vector_store import VectorStore
from bot.session import ChatScope, session_manager


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


def _make_test_scope(user_id: str = "1001") -> ChatScope:
    """构造测试用私聊 ChatScope。"""
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))


class FakeMetadataStore:
    """info() 测试专用内存 MetadataStore。"""

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

    def get_entry_by_public_id(self, public_id: "MemePublicId") -> MemeEntry | None:
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

    def list_collections(self) -> list["MemeCollection"]:
        return []

    def create_collection(self, name: str) -> MemeCollection:
        return MemeCollection(id=1, name=name)

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        return None

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        return None

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


class FakeVectorStore:
    """info() 测试专用内存 VectorStore（无实际行为）。"""

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

    async def rebuild_all(
        self,
        items: list[tuple[int, list[float]]] | list[tuple[int, list[float], int]],
    ) -> None:
        pass

    async def get_all_ids(self) -> set[int]:
        return set()


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
# info() 测试
# ---------------------------------------------------------------------------


class TestInfo:
    """IndexManager.info() 单元测试。"""

    @pytest.mark.asyncio
    async def test_info_entry_count_and_ranking(
        self, index_manager: IndexManager
    ) -> None:
        """返回正确的条目总数与 speaker 使用频率排行（前 10，不足 10 则全返回）。"""
        metadata_store = index_manager._metadata_store
        # speaker 使用频次：甲 x4, 乙 x3, 丙 x2, None x1
        for _ in range(4):
            metadata_store.add("a.jpg", "文本", speaker="甲")
        for _ in range(3):
            metadata_store.add("b.jpg", "文本", speaker="乙")
        for _ in range(2):
            metadata_store.add("c.jpg", "文本", speaker="丙")
        metadata_store.add("d.jpg", "文本", speaker=None)

        info = await index_manager.info()

        assert isinstance(info, IndexInfo)
        assert info.entry_count == 10
        assert info.speaker_ranking == [("甲", 4), ("乙", 3), ("丙", 2), (None, 1)]
        assert info.status == "空闲"

    @pytest.mark.asyncio
    async def test_info_ranking_truncates_to_ten(
        self, index_manager: IndexManager
    ) -> None:
        """speaker 种类超过 10 个时，排行截断到前 10。"""
        metadata_store = index_manager._metadata_store
        for i in range(12):
            metadata_store.add(f"m{i}.jpg", f"文本{i}", speaker=f"speaker{i}")

        info = await index_manager.info()

        assert len(info.speaker_ranking) == 10
        # 每个 speaker 各 1 条，排序稳定：按 count 降序、speaker 升序
        assert info.speaker_ranking[0][1] == 1

    @pytest.mark.asyncio
    async def test_info_status_decoupled_from_session(
        self, index_manager: IndexManager
    ) -> None:
        """engine 不再感知 bot.session：激活会话后 status 仍为"空闲"（证明解耦）。"""
        matcher = MagicMock()
        scope = _make_test_scope("1001")

        try:
            activated = session_manager.activate_chat(scope, "search", matcher)
            assert activated is True

            # 会话活跃时 engine 仍报告空闲（命令态由插件层覆写）
            info = await index_manager.info()
            assert info.status == "空闲"
        finally:
            session_manager.deactivate_chat(scope)

        # 会话清理后仍空闲
        info_after = await index_manager.info()
        assert info_after.status == "空闲"


class TestGetEntry:
    """IndexManager.get_entry() 单元测试。"""

    @pytest.mark.asyncio
    async def test_get_entry_existing(self, index_manager: IndexManager) -> None:
        """存在的 id 返回对应 MemeEntry。"""
        metadata_store = index_manager._metadata_store
        metadata_store.add("a.jpg", "加班心累", speaker="小明", tags=["吐槽"])

        entry = await index_manager.get_entry(1)

        assert entry is not None
        assert entry.id == 1
        assert entry.image_path == "a.jpg"
        assert entry.text == "加班心累"
        assert entry.speaker == "小明"
        assert entry.tags == ["吐槽"]

    @pytest.mark.asyncio
    async def test_get_entry_not_found(self, index_manager: IndexManager) -> None:
        """不存在的 id 返回 None。"""
        entry = await index_manager.get_entry(999)
        assert entry is None
