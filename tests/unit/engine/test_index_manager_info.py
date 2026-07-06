"""IndexManager.info() 单元测试。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.engine.index_manager import IndexInfo, IndexManager
from bot.engine.metadata_store import MemeEntry
from bot.session import session_manager


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


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

    def get_id_by_text(self, text: str) -> int | None:
        for eid, e in self._entries.items():
            if e.text == text:
                return eid
        return None

    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        eid = 1 if not self._entries else max(self._entries) + 1
        self._entries[eid] = MemeEntry(
            id=eid,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=tags or [],
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

    async def upsert(self, entry_id: int, embedding: list[float]) -> None:
        pass

    async def remove(self, entry_id: int) -> None:
        pass

    async def remove_many(self, entry_ids: list[int]) -> None:
        pass

    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list:
        return []

    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None:
        pass


@pytest.fixture
async def index_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造带 Fake Store 的 IndexManager。"""
    monkeypatch.setenv("READ_LOCK_TIMEOUT", "30")
    monkeypatch.setenv("ADD_COMMAND_TIMEOUT", "60")

    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = FakeMetadataStore()
    vector_store = FakeVectorStore()

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    await manager.load()
    return manager


# ---------------------------------------------------------------------------
# info() 测试
# ---------------------------------------------------------------------------


class TestInfo:
    """IndexManager.info() 单元测试。"""

    @pytest.mark.anyio
    async def test_info_entry_count_and_ranking(
        self, index_manager: IndexManager
    ) -> None:
        """返回正确的条目总数与 speaker 使用频率排行前三名。"""
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
        assert info.speaker_ranking == [("甲", 4), ("乙", 3), ("丙", 2)]
        assert info.status == "空闲"

    @pytest.mark.anyio
    async def test_info_status_processing(
        self, index_manager: IndexManager
    ) -> None:
        """存在活跃会话时状态为"正在处理命令"。"""
        matcher = MagicMock()
        user_id = "user_1"

        try:
            activated = session_manager.activate_chat(user_id, "search", matcher)
            assert activated is True

            info = await index_manager.info()
            assert info.status == "正在处理命令"
        finally:
            session_manager.deactivate_chat(user_id)

        # 会话已清理，状态恢复空闲
        info_after = await index_manager.info()
        assert info_after.status == "空闲"
