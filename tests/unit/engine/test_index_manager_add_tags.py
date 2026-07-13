"""IndexManager.add_tags() 单元测试。"""

from pathlib import Path

import pytest
import pytest_asyncio

from bot.engine.index_manager import (
    IndexManager,
)
from bot.engine.metadata_store import MemeEntry


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


class FakeMetadataStore:
    """add_tags 测试专用内存 MetadataStore。"""

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
    """add_tags 测试专用内存 VectorStore（无实际行为）。"""

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

    async def get_all_ids(self) -> set[int]:
        return set()

    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None:
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
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    await manager.load()
    return manager


# ---------------------------------------------------------------------------
# add_tags 测试
# ---------------------------------------------------------------------------


class TestAddTags:
    """IndexManager.add_tags() 单元测试。"""

    @pytest.mark.asyncio
    async def test_add_tags_appends_new_tags(self, index_manager: IndexManager) -> None:
        """追加新标签到已有条目，返回新增标签与合并后全部标签。"""
        index_manager._metadata_store.add("a.jpg", "文本", tags=["旧标签"])
        result = await index_manager.add_tags(1, ["新标签1", "新标签2", "旧标签"])

        assert result.entry_id == 1
        assert set(result.added_tags) == {"新标签1", "新标签2"}
        assert set(result.all_tags) == {"旧标签", "新标签1", "新标签2"}

        entry = index_manager._metadata_store.get_entry(1)
        assert entry is not None
        assert set(entry.tags) == {"旧标签", "新标签1", "新标签2"}

    @pytest.mark.asyncio
    async def test_add_tags_entry_not_found(self, index_manager: IndexManager) -> None:
        """entry_id 不存在时抛出 ValueError。"""
        with pytest.raises(ValueError, match="entry_id=999 不存在"):
            await index_manager.add_tags(999, ["标签"])

    @pytest.mark.asyncio
    async def test_add_tags_all_existing(self, index_manager: IndexManager) -> None:
        """所有待添加标签都已存在时，added_tags 为空，不触发 update。"""
        index_manager._metadata_store.add("a.jpg", "文本", tags=["甲", "乙"])
        result = await index_manager.add_tags(1, ["乙", "甲"])

        assert result.entry_id == 1
        assert result.added_tags == []
        assert set(result.all_tags) == {"甲", "乙"}
