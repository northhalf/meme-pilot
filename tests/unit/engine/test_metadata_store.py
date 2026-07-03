"""MetadataStore 单元测试。"""

from pathlib import Path

import pytest

from bot.engine.metadata_store import DuplicateEntryError, MemeEntry, MetadataStore


@pytest.fixture
def store(tmp_sqlite_path: Path) -> MetadataStore:
    """已 load 的 MetadataStore。"""
    s = MetadataStore(str(tmp_sqlite_path))
    s.load()
    return s


class TestLoadAndCount:
    def test_load_creates_empty_db(self, tmp_sqlite_path: Path) -> None:
        """load 后 entry_count 为 0。"""
        s = MetadataStore(str(tmp_sqlite_path))
        s.load()
        assert s.entry_count() == 0
        assert tmp_sqlite_path.exists()

    def test_load_rebuilds_text_to_id(self, store: MetadataStore) -> None:
        """load 后 _text_to_id 为空（空库）。"""
        assert store._text_to_id == {}


class TestAddAndGet:
    def test_add_returns_int_id_and_persists(self, store: MetadataStore) -> None:
        """add 返回 int id，get_entry 可取回 MemeEntry。"""
        eid = store.add(image_path="cat.jpg", text="一只猫")
        assert eid == 1
        entry = store.get_entry(1)
        assert entry == MemeEntry(
            id=1, image_path="cat.jpg", text="一只猫", speaker=None, tags=[]
        )

    def test_add_increments_id(self, store: MetadataStore) -> None:
        store.add(image_path="a.jpg", text="甲")
        eid2 = store.add(image_path="b.jpg", text="乙")
        assert eid2 == 2

    def test_get_entry_nonexistent_returns_none(self, store: MetadataStore) -> None:
        assert store.get_entry(999) is None

    def test_get_by_filename(self, store: MetadataStore) -> None:
        store.add(image_path="cat.jpg", text="猫")
        entry = store.get_by_filename("cat.jpg")
        assert entry is not None
        assert entry.id == 1
        assert store.get_by_filename("nope.jpg") is None

    def test_get_id_by_text(self, store: MetadataStore) -> None:
        eid = store.add(image_path="x.jpg", text="加班")
        assert store.get_id_by_text("加班") == eid
        assert store.get_id_by_text("不在") is None


class TestFindNextId:
    def test_empty_returns_1(self, store: MetadataStore) -> None:
        assert store.find_next_id() == 1

    def test_sequential_no_holes(self, store: MetadataStore) -> None:
        store.add("a.jpg", "甲")
        store.add("b.jpg", "乙")
        store.add("c.jpg", "丙")
        assert store.find_next_id() == 4

    def test_reuses_smallest_hole(self, store: MetadataStore) -> None:
        store.add_with_id(1, "a.jpg", "甲")
        store.add_with_id(3, "c.jpg", "丙")
        store.add_with_id(5, "e.jpg", "戊")
        assert store.find_next_id() == 2

    def test_reuses_hole_after_delete(self, store: MetadataStore) -> None:
        store.add_with_id(1, "a.jpg", "甲")
        store.add_with_id(2, "b.jpg", "乙")
        store.add_with_id(4, "d.jpg", "丁")
        assert store.find_next_id() == 3

    def test_head_hole_returns_1(self, store: MetadataStore) -> None:
        """表头空洞：最小 id 从 3 开始，应返回 1。"""
        store.add_with_id(3, "c.jpg", "丙")
        assert store.find_next_id() == 1


class TestUpdate:
    def test_update_image_path(self, store: MetadataStore) -> None:
        eid = store.add("old.jpg", "猫")
        assert store.update(eid, image_path="new.jpg") is True
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.image_path == "new.jpg"
        assert entry.text == "猫"  # text 不变

    def test_update_text_refreshes_text_to_id(self, store: MetadataStore) -> None:
        eid = store.add("x.jpg", "加班")
        assert store.get_id_by_text("加班") == eid
        store.update(eid, text="下班")
        assert store.get_id_by_text("加班") is None
        assert store.get_id_by_text("下班") == eid

    def test_update_nonexistent_returns_false(self, store: MetadataStore) -> None:
        assert store.update(999, image_path="x.jpg") is False

    def test_update_speaker_set(self, store: MetadataStore) -> None:
        """设置 speaker。"""
        eid = store.add("test.jpg", "text")
        assert store.update(eid, speaker="张三") is True
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.speaker == "张三"
        assert entry.text == "text"  # 其他字段不变

    def test_update_speaker_clear(self, store: MetadataStore) -> None:
        """清空 speaker（设为 None）。"""
        eid = store.add("test.jpg", "text", speaker="张三")
        assert store.update(eid, speaker=None) is True
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.speaker is None
        assert entry.text == "text"  # 其他字段不变

    def test_update_speaker_unchanged(self, store: MetadataStore) -> None:
        """不传 speaker 参数时保持原值。"""
        eid = store.add("test.jpg", "text", speaker="张三")
        assert store.update(eid, text="新文本") is True
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.speaker == "张三"  # speaker 不变
        assert entry.text == "新文本"


class TestRemove:
    def test_remove_deletes_row_and_text_to_id(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲")
        assert store.remove(eid) is True
        assert store.get_entry(eid) is None
        assert store.get_id_by_text("甲") is None
        assert store.entry_count() == 0

    def test_remove_nonexistent_returns_false(self, store: MetadataStore) -> None:
        assert store.remove(999) is False

    def test_remove_releases_id_for_reuse(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲")
        store.remove(eid)
        eid2 = store.add("b.jpg", "乙")
        assert eid2 == eid  # 复用空洞


class TestTagsAndCascade:
    def test_tags_assembled_in_entry(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["搞笑", "猫"])
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == ["搞笑", "猫"]

    def test_cascade_delete_removes_tags(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["搞笑"])
        store.remove(eid)
        # 重新插入同 id，tags 应为空（CASCADE 清掉了旧 tag 行）
        store.add_with_id(eid, "b.jpg", "乙")
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == []

    def test_update_replaces_tags(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["旧"])
        store.update(eid, tags=["新1", "新2"])
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == ["新1", "新2"]


class TestPersistence:
    def test_reload_preserves_data(self, tmp_sqlite_path: Path) -> None:
        s1 = MetadataStore(str(tmp_sqlite_path))
        s1.load()
        s1.add("cat.jpg", "猫")
        s1.close()

        s2 = MetadataStore(str(tmp_sqlite_path))
        s2.load()
        assert s2.entry_count() == 1
        assert s2.get_id_by_text("猫") == 1


class TestDuplicateEntryError:
    """text/image_path/id UNIQUE 约束与 DuplicateEntryError 多字段汇总测试。"""

    def test_add_duplicate_text_raises(self, store: MetadataStore) -> None:
        """add 写入与已有 text 相同时抛 DuplicateEntryError，conflicts 含 text。"""
        store.add(image_path="a.jpg", text="加班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add(image_path="b.jpg", text="加班")
        assert ("text", "加班") in exc_info.value.conflicts
        assert ("image_path", "b.jpg") not in exc_info.value.conflicts

    def test_add_with_id_duplicate_id_reported(self, store: MetadataStore) -> None:
        """add_with_id 撞已存在 id 时 conflicts 含 id。"""
        store.add_with_id(1, "a.jpg", "甲")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add_with_id(1, "b.jpg", "乙")  # id 冲突，image_path/text 不冲突
        assert ("id", "1") in exc_info.value.conflicts

    def test_add_with_id_duplicate_text_reported(self, store: MetadataStore) -> None:
        """add_with_id 撞已存在 text 时 conflicts 含 text。"""
        store.add_with_id(1, "a.jpg", "加班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add_with_id(2, "b.jpg", "加班")  # text 冲突
        assert ("text", "加班") in exc_info.value.conflicts

    def test_add_duplicate_image_path_raises(self, store: MetadataStore) -> None:
        """add 写入与已有 image_path 相同时抛 DuplicateEntryError，conflicts 含 image_path。"""
        store.add(image_path="a.jpg", text="甲")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add(image_path="a.jpg", text="乙")
        assert ("image_path", "a.jpg") in exc_info.value.conflicts
        assert ("text", "乙") not in exc_info.value.conflicts

    def test_add_both_fields_duplicate_reports_both(self, store: MetadataStore) -> None:
        """add 同时撞 text 和 image_path 时 conflicts 同时含两字段，顺序 id→image_path→text。"""
        store.add(image_path="a.jpg", text="加班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add(image_path="a.jpg", text="加班")
        assert exc_info.value.conflicts == [
            ("image_path", "a.jpg"),
            ("text", "加班"),
        ]

    def test_update_image_path_collision_raises(self, store: MetadataStore) -> None:
        """update 改 image_path 撞他行时抛 DuplicateEntryError，只报 image_path。"""
        store.add(image_path="a.jpg", text="甲")
        eid2 = store.add(image_path="b.jpg", text="乙")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.update(eid2, image_path="a.jpg")
        assert exc_info.value.conflicts == [("image_path", "a.jpg")]

    def test_update_text_collision_raises(self, store: MetadataStore) -> None:
        """update 改 text 撞他行时抛 DuplicateEntryError，只报 text。"""
        store.add(image_path="a.jpg", text="加班")
        eid2 = store.add(image_path="b.jpg", text="下班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.update(eid2, text="加班")
        assert exc_info.value.conflicts == [("text", "加班")]

    def test_update_both_fields_collision_reports_both(
        self, store: MetadataStore
    ) -> None:
        """update 同时改 image_path+text 撞他行时 conflicts 含两字段。"""
        store.add(image_path="a.jpg", text="加班")
        eid2 = store.add(image_path="b.jpg", text="下班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.update(eid2, image_path="a.jpg", text="加班")
        assert exc_info.value.conflicts == [
            ("image_path", "a.jpg"),
            ("text", "加班"),
        ]

    def test_update_same_row_not_reported(self, store: MetadataStore) -> None:
        """update 改成自身已有的值不报冲突（exclude_id 排除自身）。"""
        store.add(image_path="a.jpg", text="加班")
        eid = store.get_id_by_text("加班")
        assert eid is not None
        # 改成自身 image_path/text 不应抛
        assert store.update(eid, image_path="a.jpg", text="加班") is True
