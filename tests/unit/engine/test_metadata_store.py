"""MetadataStore 单元测试。"""

import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal, cast

import pytest

import bot.engine.metadata_store as metadata_store_module
from bot.engine.metadata_store import (
    CURRENT_SCHEMA_VERSION,
    DuplicateEntryError,
    MemeEntry,
    MetadataStore,
    SchemaVersionError,
    create_current_schema,
)
from bot.engine.types import GLOBAL_COLLECTION_NAME, MemeCollection, MemePublicId
from bot.session import ChatScope


def _scope(user_id: int, chat_type: str, chat_id: int) -> ChatScope:
    """构造测试用 ChatScope，chat_type 经 cast 满足 Literal 约束。

    运行时不校验 chat_type（ChatScope 无 __post_init__），非法值由
    MetadataStore._validate_chat_type 在调用处拒绝，用于覆盖非法类型用例。
    """
    return ChatScope(
        user_id=user_id,
        chat_type=cast(Literal["private", "group"], chat_type),
        chat_id=chat_id,
    )


@pytest.fixture
def store(tmp_sqlite_path: Path) -> MetadataStore:
    """已 load 的 MetadataStore。"""
    s = MetadataStore(str(tmp_sqlite_path))
    s.load()
    return s


def _create_manual_current_schema(
    conn: sqlite3.Connection,
    *,
    meme_id_definition: str = "INTEGER PRIMARY KEY",
    collection_id_definition: str = "INTEGER PRIMARY KEY",
    collection_name_unique: bool = True,
    meme_tag_primary_key: str | None = "meme_id, tag",
    chat_scope_primary_key: bool = True,
) -> None:
    """创建可替换身份约束的最小版本 2 Schema。"""
    name_constraint = " UNIQUE" if collection_name_unique else ""
    tag_primary_key = (
        f", PRIMARY KEY ({meme_tag_primary_key})"
        if meme_tag_primary_key is not None
        else ""
    )
    scope_primary_key = (
        ", PRIMARY KEY (user_id, chat_type, chat_id)" if chat_scope_primary_key else ""
    )
    conn.executescript(
        f"""
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES ({CURRENT_SCHEMA_VERSION});
        CREATE TABLE meme_collection (
            id {collection_id_definition},
            name TEXT NOT NULL{name_constraint}
        );
        CREATE TABLE meme (
            id {meme_id_definition},
            collection_id INTEGER NOT NULL,
            local_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            text TEXT NOT NULL,
            speaker TEXT
        );
        CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
        CREATE UNIQUE INDEX idx_meme_collection_local
            ON meme(collection_id, local_id);
        CREATE UNIQUE INDEX idx_meme_collection_text
            ON meme(collection_id, text);
        CREATE TABLE meme_tag (
            meme_id INTEGER NOT NULL,
            tag TEXT NOT NULL
            {tag_primary_key},
            FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
        );
        CREATE INDEX idx_meme_tag_tag ON meme_tag(tag);
        CREATE TABLE chat_collection_scope (
            user_id INTEGER NOT NULL,
            chat_type TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            selected_collection_id INTEGER NOT NULL
            {scope_primary_key}
        );
        """
    )


class TestLoadAndCount:
    def test_load_creates_empty_db(self, tmp_sqlite_path: Path) -> None:
        """load 后 entry_count 为 0。"""
        s = MetadataStore(str(tmp_sqlite_path))
        s.load()
        assert s.entry_count() == 0
        assert tmp_sqlite_path.exists()

    def test_new_database_creates_current_schema(self, tmp_sqlite_path: Path) -> None:
        """新数据库一次性创建当前 Schema 和唯一版本行。"""
        s = MetadataStore(str(tmp_sqlite_path))
        s.load()
        s.close()

        with sqlite3.connect(tmp_sqlite_path) as conn:
            versions = conn.execute("SELECT version FROM schema_version").fetchall()
            meme_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(meme)").fetchall()
            }
            table_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        assert versions == [(CURRENT_SCHEMA_VERSION,)]
        assert {"collection_id", "local_id"}.issubset(meme_columns)
        assert {
            "schema_version",
            "meme_collection",
            "meme",
            "meme_tag",
            "chat_collection_scope",
        }.issubset(table_names)

    def test_create_current_schema_creates_one_version_row(self) -> None:
        """Schema helper 在调用方保证为空时写入完整当前 Schema。"""
        with sqlite3.connect(":memory:") as conn:
            create_current_schema(conn)
            assert conn.execute("SELECT version FROM schema_version").fetchall() == [
                (CURRENT_SCHEMA_VERSION,)
            ]

    def test_legacy_database_is_rejected_without_mutation(
        self, tmp_sqlite_path: Path
    ) -> None:
        """无版本表的旧库拒绝加载且不改结构和数据。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            conn.execute(
                "CREATE TABLE meme ("
                "id INTEGER PRIMARY KEY, image_path TEXT, text TEXT, speaker TEXT)"
            )
            conn.execute(
                "INSERT INTO meme (id, image_path, text) VALUES (42, 'a.webp', '文本')"
            )
            conn.commit()

        s = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(
            SchemaVersionError,
            match="停止 Bot.*scripts.migrate_meme_collections upgrade-schema",
        ):
            s.load()

        assert s._conn is None
        with sqlite3.connect(tmp_sqlite_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(meme)")}
            row = conn.execute("SELECT id, image_path, text FROM meme").fetchone()
            table_names = {
                item[0]
                for item in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        assert "collection_id" not in columns
        assert "schema_version" not in table_names
        assert row == (42, "a.webp", "文本")

    @pytest.mark.parametrize("versions", [[CURRENT_SCHEMA_VERSION, 1], [1]])
    def test_invalid_schema_versions_are_rejected(
        self, tmp_sqlite_path: Path, versions: list[int]
    ) -> None:
        """版本表多行或版本错误时拒绝运行。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
            conn.executemany(
                "INSERT INTO schema_version (version) VALUES (?)",
                [(version,) for version in versions],
            )
            conn.commit()

        s = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError):
            s.load()
        assert s._conn is None

    @pytest.mark.parametrize("version", [2.9, "2", "02", "2.0", True])
    def test_non_integer_schema_version_values_are_rejected(
        self, tmp_sqlite_path: Path, version: float | str | bool
    ) -> None:
        """REAL 或 TEXT 版本值不得经数值转换后伪装为当前版本。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            conn.execute("CREATE TABLE schema_version (version)")
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (version,),
            )
            conn.commit()

        s = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError):
            s.load()
        assert s._conn is None

    def test_failed_reload_clears_all_caches(self, tmp_sqlite_path: Path) -> None:
        """同一实例重新加载失败后不暴露上一次成功加载的数据。"""
        s = MetadataStore(str(tmp_sqlite_path))
        s.load()
        collection = s.create_collection("合集")
        entry_id = s.add("a.webp", "文本", collection_id=collection.id)
        assert s.get_entry(entry_id) is not None

        with sqlite3.connect(tmp_sqlite_path) as conn:
            conn.execute("UPDATE schema_version SET version = 1")
            conn.commit()

        with pytest.raises(SchemaVersionError):
            s.load()

        assert s._conn is None
        assert s._entries == {}
        assert s._text_to_id == {}
        assert s._entries_by_collection == {}
        assert s._id_to_collections == {}
        assert s._collection_name_to_id == {}
        assert s._selected_collections == {}
        assert s.get_entry(entry_id) is None
        assert s.get_entries() == {}
        assert s.get_collection(collection.id) is None

    def test_missing_required_unique_index_is_rejected(
        self, tmp_sqlite_path: Path
    ) -> None:
        """版本正确但缺少关键唯一索引时拒绝加载。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            create_current_schema(conn)
            conn.execute("DROP INDEX idx_meme_collection_text")
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError, match="结构"):
            store.load()

        assert store._conn is None
        assert store.get_entries() == {}

    def test_missing_meme_id_primary_key_is_rejected(
        self, tmp_sqlite_path: Path
    ) -> None:
        """meme.id 缺少单列主键时拒绝版本 2 库。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(
                conn,
                meme_id_definition="INTEGER NOT NULL",
            )

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError, match="meme.*主键"):
            store.load()

    def test_missing_collection_id_primary_key_is_rejected(
        self, tmp_sqlite_path: Path
    ) -> None:
        """meme_collection.id 缺少单列主键时拒绝版本 2 库。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(
                conn,
                collection_id_definition="INTEGER NOT NULL",
            )

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError, match="meme_collection.*主键"):
            store.load()

    def test_missing_collection_name_unique_constraint_is_rejected(
        self, tmp_sqlite_path: Path
    ) -> None:
        """合集名称没有任何完整唯一保障时拒绝版本 2 库。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn, collection_name_unique=False)

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError, match="name.*唯一"):
            store.load()

    def test_missing_meme_tag_composite_primary_key_is_rejected(
        self, tmp_sqlite_path: Path
    ) -> None:
        """meme_tag 缺少 `(meme_id, tag)` 复合主键时拒绝。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn, meme_tag_primary_key=None)

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(SchemaVersionError, match="meme_tag.*主键"):
            store.load()

    @pytest.mark.parametrize(
        ("first_text", "second_local_id", "second_text"),
        [
            ("甲", sqlite3.Binary(b"1"), "乙"),
            ("b'foo'", 2, sqlite3.Binary(b"foo")),
        ],
    )
    def test_duplicate_collection_keys_are_rejected_during_cache_rebuild(
        self,
        tmp_sqlite_path: Path,
        first_text: str,
        second_local_id: int | memoryview,
        second_text: str | memoryview,
    ) -> None:
        """SQLite 唯一但 Python 归一后重复的键不能被 dict 静默覆盖。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            create_current_schema(conn)
            conn.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (1, 0, 1, 'a.webp', ?, NULL)",
                (first_text,),
            )
            conn.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (2, 0, ?, 'b.webp', ?, NULL)",
                (second_local_id, second_text),
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="重复"):
            store.load()

        assert store._conn is None
        assert store.get_entries() == {}

    def test_duplicate_internal_id_is_rejected_during_load(
        self, tmp_sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """即使结构校验失效，重复内部 ID 也不能覆盖缓存条目。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(
                conn,
                meme_id_definition="INTEGER NOT NULL",
            )
            conn.executemany(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (?, 0, ?, ?, ?, NULL)",
                [
                    (1, 1, "a.webp", "甲"),
                    (1, 2, "b.webp", "乙"),
                ],
            )
            conn.commit()
        monkeypatch.setattr(
            MetadataStore,
            "_validate_schema_structure",
            staticmethod(lambda conn, table_names: None),
        )

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="重复内部 ID"):
            store.load()

    def test_duplicate_collection_id_is_rejected_during_load(
        self, tmp_sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """即使结构校验失效，重复合集 ID 也不能覆盖合集缓存。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(
                conn,
                collection_id_definition="INTEGER NOT NULL",
            )
            conn.executemany(
                "INSERT INTO meme_collection (id, name) VALUES (1, ?)",
                [("合集一",), ("合集二",)],
            )
            conn.commit()
        monkeypatch.setattr(
            MetadataStore,
            "_validate_schema_structure",
            staticmethod(lambda conn, table_names: None),
        )

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="重复合集 ID"):
            store.load()

    def test_duplicate_collection_name_is_rejected_during_load(
        self, tmp_sqlite_path: Path
    ) -> None:
        """SQLite 类型不同但 Python 名称相同的合集不能覆盖名称缓存。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn)
            conn.executemany(
                "INSERT INTO meme_collection (id, name) VALUES (?, ?)",
                [(1, "b'foo'"), (2, sqlite3.Binary(b"foo"))],
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="重复合集名称"):
            store.load()

    @pytest.mark.parametrize(
        ("collection_id", "local_id", "message"),
        [(-1, 1, "collection_id"), (0, 0, "local_id")],
    )
    def test_invalid_collection_or_local_id_is_rejected_during_load(
        self,
        tmp_sqlite_path: Path,
        collection_id: int,
        local_id: int,
        message: str,
    ) -> None:
        """负合集编号或非正局部编号视为损坏数据。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn)
            conn.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (1, ?, ?, 'bad.webp', '损坏', NULL)",
                (collection_id, local_id),
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match=message):
            store.load()

    def test_duplicate_tag_is_rejected_during_load(self, tmp_sqlite_path: Path) -> None:
        """SQLite 类型不同但 Python 标签键相同的行视为重复。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn)
            conn.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (1, 0, 1, 'a.webp', '甲', NULL)"
            )
            conn.executemany(
                "INSERT INTO meme_tag (meme_id, tag) VALUES (?, '标签')",
                [(1,), (sqlite3.Binary(b"1"),)],
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="重复标签"):
            store.load()

    def test_orphan_tag_is_rejected_during_load(self, tmp_sqlite_path: Path) -> None:
        """标签指向不存在的 meme 时拒绝加载。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn)
            conn.execute("INSERT INTO meme_tag (meme_id, tag) VALUES (99, '孤儿标签')")
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="不存在的表情包"):
            store.load()

    @pytest.mark.parametrize("entry_id", [0, -1])
    def test_non_positive_internal_id_is_rejected_on_load(
        self, tmp_sqlite_path: Path, entry_id: int
    ) -> None:
        """Schema 允许但运行时不接受非正内部 ID。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            create_current_schema(conn)
            conn.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (?, 0, 1, 'bad.webp', '损坏', NULL)",
                (entry_id,),
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="内部 ID"):
            store.load()

        assert store._conn is None
        assert store.get_entries() == {}

    def test_load_rebuilds_text_to_id(self, store: MetadataStore) -> None:
        """load 后 _text_to_id 为空（空库）。"""
        assert store._text_to_id == {}


class _TrackingRLock:
    """记录指定线程尝试加锁时机的可重入锁代理。"""

    def __init__(self, tracked_thread_name: str) -> None:
        self._lock = threading.RLock()
        self._tracked_thread_name = tracked_thread_name
        self.tracked_thread_waiting = threading.Event()

    def __enter__(self) -> None:
        if threading.current_thread().name == self._tracked_thread_name:
            self.tracked_thread_waiting.set()
        self._lock.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self._lock.release()


class TestLifecycleConcurrency:
    def test_concurrent_loads_serialize_directory_preparation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两个 load 调用从目录准备开始串行执行。"""
        db_path = tmp_path / "nested" / "index.db"
        store = MetadataStore(str(db_path))
        tracking_lock = _TrackingRLock("second-load")
        store._lock = cast(threading.RLock, tracking_lock)
        real_makedirs = metadata_store_module.os.makedirs
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        call_lock = threading.Lock()
        call_count = 0
        errors: list[BaseException] = []

        def controlled_makedirs(path: str, *, exist_ok: bool) -> None:
            nonlocal call_count
            with call_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_entered.set()
                assert release_first.wait(2)
            else:
                second_entered.set()
            real_makedirs(path, exist_ok=exist_ok)

        def run_load() -> None:
            try:
                store.load()
            except BaseException as exc:
                errors.append(exc)

        monkeypatch.setattr(metadata_store_module.os, "makedirs", controlled_makedirs)
        first = threading.Thread(target=run_load, name="first-load")
        second = threading.Thread(target=run_load, name="second-load")
        first.start()
        assert first_entered.wait(2)
        second.start()
        assert tracking_lock.tracked_thread_waiting.wait(2)
        assert not second_entered.is_set()
        release_first.set()
        first.join(2)
        second.join(2)

        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert second_entered.is_set()
        assert store._conn is not None

    def test_load_publishes_connection_only_after_cache_rebuild(
        self, tmp_sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load 完成校验和缓存重建前不发布局部连接。"""
        store = MetadataStore(str(tmp_sqlite_path))
        rebuild_entered = threading.Event()
        release_rebuild = threading.Event()
        errors: list[BaseException] = []
        real_rebuild = store._rebuild_caches

        def controlled_rebuild(conn: sqlite3.Connection) -> None:
            rebuild_entered.set()
            assert release_rebuild.wait(2)
            real_rebuild(conn)

        monkeypatch.setattr(store, "_rebuild_caches", controlled_rebuild)

        def run_load() -> None:
            try:
                store.load()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run_load)
        thread.start()
        assert rebuild_entered.wait(2)
        assert store._conn is None
        release_rebuild.set()
        thread.join(2)

        assert not thread.is_alive()
        assert errors == []
        assert store._conn is not None

    def test_concurrent_close_calls_are_safe(self, store: MetadataStore) -> None:
        """两个 close 调用不会让后到线程对 None 重复 close。"""
        real_conn = store._conn
        assert real_conn is not None
        tracking_lock = _TrackingRLock("second-close")
        store._lock = cast(threading.RLock, tracking_lock)
        close_entered = threading.Event()
        release_close = threading.Event()
        errors: list[BaseException] = []

        class _BlockingCloseConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real
                self._first = True

            def close(self) -> None:
                if self._first:
                    self._first = False
                    close_entered.set()
                    assert release_close.wait(2)
                self._real.close()

            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        store._conn = cast(sqlite3.Connection, _BlockingCloseConn(real_conn))

        def run_close() -> None:
            try:
                store.close()
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=run_close, name="first-close")
        second = threading.Thread(target=run_close, name="second-close")
        first.start()
        assert close_entered.wait(2)
        second.start()
        assert tracking_lock.tracked_thread_waiting.wait(2)
        release_close.set()
        first.join(2)
        second.join(2)

        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert store._conn is None

    def test_directory_preparation_failure_clears_loaded_state(
        self, tmp_path: Path
    ) -> None:
        """父路径为文件时 load 失败并清空旧连接与缓存。"""
        valid_db = tmp_path / "valid" / "index.db"
        store = MetadataStore(str(valid_db))
        store.load()
        entry_id = store.add("a.webp", "文本")
        blocking_file = tmp_path / "blocking"
        blocking_file.write_text("not a directory", encoding="utf-8")
        store._db_path = str(blocking_file / "index.db")

        with pytest.raises(FileExistsError):
            store.load()

        assert store._conn is None
        assert store.get_entry(entry_id) is None
        assert store.get_entries() == {}
        assert store._id_to_collections == {}
        assert store._selected_collections == {}


class TestCollectionRegistry:
    def test_collections_allocate_smallest_ids_and_list_in_order(
        self, store: MetadataStore
    ) -> None:
        """自动分配最小正整数，并按 ID 返回独立列表。"""
        second = store.create_collection("合集二", collection_id=2)
        first = store.create_collection("合集一")

        collections = store.list_collections()

        assert first == MemeCollection(id=1, name="合集一")
        assert second == MemeCollection(id=2, name="合集二")
        assert collections == [first, second]
        collections.clear()
        assert store.list_collections() == [first, second]

    def test_collection_name_lookup_is_exact_and_case_sensitive(
        self, store: MetadataStore
    ) -> None:
        """合集名称仅支持区分大小写的精确匹配。"""
        collection = store.create_collection("Meme")

        assert store.get_collection_by_name("Meme") == collection
        assert store.get_collection_by_name("meme") is None
        assert store.get_collection_by_name(" Meme ") is None

    def test_collection_name_and_explicit_id_conflicts_preserve_registry(
        self, store: MetadataStore
    ) -> None:
        """名称或显式 ID 冲突保留 SQLite 异常与原缓存。"""
        existing = store.create_collection("已有", collection_id=3)

        with pytest.raises(sqlite3.IntegrityError):
            store.create_collection("已有")
        with pytest.raises(sqlite3.IntegrityError):
            store.create_collection("其他", collection_id=3)

        assert store.list_collections() == [existing]
        assert store.get_collection_by_name("已有") == existing
        assert store.get_collection_by_name("其他") is None

    @pytest.mark.parametrize("collection_id", [0, -1])
    def test_create_collection_rejects_non_positive_explicit_id(
        self, store: MetadataStore, collection_id: int
    ) -> None:
        """显式合集 ID 必须为正整数。"""
        with pytest.raises(ValueError, match="collection_id 必须为正整数"):
            store.create_collection("非法", collection_id=collection_id)

        assert store.list_collections() == []

    def test_create_collection_rejects_ignored_insert(
        self, store: MetadataStore
    ) -> None:
        """INSERT 被合法触发器忽略时回滚且不发布合集缓存。"""
        conn = store._conn
        assert conn is not None
        conn.execute(
            "CREATE TRIGGER ignore_collection_insert "
            "BEFORE INSERT ON meme_collection "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        conn.commit()

        with pytest.raises(sqlite3.DatabaseError, match="创建合集.*影响 1 行"):
            store.create_collection("被忽略")

        assert conn.execute("SELECT COUNT(*) FROM meme_collection").fetchone()[0] == 0
        assert store.list_collections() == []
        assert store.get_collection_by_name("被忽略") is None
        assert store._entries_by_collection == {0: {}}

    def test_collection_entry_count_supports_all_and_single_collection(
        self, store: MetadataStore
    ) -> None:
        """合集计数支持全库、普通合集、全局和未知合集。"""
        collection = store.create_collection("合集")
        store.add("global.webp", "全局")
        store.add("local-1.webp", "局部一", collection_id=collection.id)
        store.add("local-2.webp", "局部二", collection_id=collection.id)

        assert store.collection_entry_count(None) == 3
        assert store.collection_entry_count(0) == 1
        assert store.collection_entry_count(collection.id) == 2
        assert store.collection_entry_count(99) == 0

    def test_find_next_local_id_reuses_smallest_gap(self, store: MetadataStore) -> None:
        """公开局部编号查询在删除条目后复用最小空洞。"""
        collection = store.create_collection("合集")
        first = store.add("a.webp", "甲", collection_id=collection.id)
        store.add("b.webp", "乙", collection_id=collection.id)

        assert store.find_next_local_id(collection.id) == 3
        assert store.remove(first)
        assert store.find_next_local_id(collection.id) == 1
        assert store.find_next_local_id(0) == 1

    def test_find_next_local_id_rejects_missing_regular_collection(
        self, store: MetadataStore
    ) -> None:
        """不存在的普通合集不能继续分配局部编号。"""
        with pytest.raises(ValueError, match="collection_id=99 不存在"):
            store.find_next_local_id(99)


class TestChatScopePersistence:
    def test_scope_defaults_to_global_and_keys_are_independent(
        self, store: MetadataStore
    ) -> None:
        """未设置默认为 0，用户、聊天类型和聊天编号分别参与键计算。"""
        private_key = (10001, "private", 10001)
        group_key = (10001, "group", 20001)
        other_chat_key = (10001, "group", 20002)
        collection = store.create_collection("合集")

        assert store.get_selected_collection(_scope(*private_key)) == 0
        store.set_selected_collection(_scope(*private_key), collection.id)
        store.set_selected_collection(_scope(*group_key), collection.id)

        assert store.get_selected_collection(_scope(*private_key)) == collection.id
        assert store.get_selected_collection(_scope(*group_key)) == collection.id
        assert store.get_selected_collection(_scope(*other_chat_key)) == 0
        assert store.get_selected_collection(_scope(10002, "private", 10001)) == 0

    def test_scope_upsert_updates_same_key_and_persists_after_reload(
        self, tmp_sqlite_path: Path
    ) -> None:
        """同键更新覆盖旧选择，关闭重开后仍保留。"""
        store = MetadataStore(str(tmp_sqlite_path))
        store.load()
        first = store.create_collection("合集一")
        second = store.create_collection("合集二")
        key = (10001, "private", 10001)

        store.set_selected_collection(_scope(*key), first.id)
        store.set_selected_collection(_scope(*key), second.id)
        store.close()

        reloaded = MetadataStore(str(tmp_sqlite_path))
        reloaded.load()
        assert reloaded.get_selected_collection(_scope(*key)) == second.id
        conn = reloaded._conn
        assert conn is not None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_collection_scope WHERE "
                "user_id = ? AND chat_type = ? AND chat_id = ?",
                key,
            ).fetchone()[0]
            == 1
        )
        reloaded.close()

    @pytest.mark.parametrize("chat_type", ["", "PRIVATE", "channel"])
    def test_scope_methods_reject_invalid_chat_type_before_sql(
        self, store: MetadataStore, chat_type: str
    ) -> None:
        """公开读写方法提前拒绝非法聊天类型。"""
        collection = store.create_collection("合集")

        with pytest.raises(ValueError, match="chat_type"):
            store.get_selected_collection(_scope(1, chat_type, 2))
        with pytest.raises(ValueError, match="chat_type"):
            store.set_selected_collection(_scope(1, chat_type, 2), collection.id)

        assert store._selected_collections == {}

    @pytest.mark.parametrize("collection_id", [-1, 99])
    def test_set_scope_rejects_invalid_collection(
        self, store: MetadataStore, collection_id: int
    ) -> None:
        """负编号和不存在的普通合集不得写入选择。"""
        with pytest.raises(ValueError, match=f"collection_id={collection_id} 不存在"):
            store.set_selected_collection(_scope(1, "private", 1), collection_id)

        assert store.get_selected_collection(_scope(1, "private", 1)) == 0

    def test_set_scope_rejects_ignored_upsert(self, store: MetadataStore) -> None:
        """UPSERT 被合法触发器忽略时回滚且不发布选择缓存。"""
        collection = store.create_collection("合集")
        key = (10001, "private", 10001)
        conn = store._conn
        assert conn is not None
        conn.execute(
            "CREATE TRIGGER ignore_scope_insert "
            "BEFORE INSERT ON chat_collection_scope "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        conn.commit()

        with pytest.raises(sqlite3.DatabaseError, match="保存 ChatScope.*影响 1 行"):
            store.set_selected_collection(_scope(*key), collection.id)

        assert (
            conn.execute("SELECT COUNT(*) FROM chat_collection_scope").fetchone()[0]
            == 0
        )
        assert store.get_selected_collection(_scope(*key)) == 0
        assert store._selected_collections == {}

    def test_set_scope_commit_failure_rolls_back_without_cache_pollution(
        self, store: MetadataStore
    ) -> None:
        """upsert 提交失败时数据库和缓存都保留旧选择。"""
        first = store.create_collection("合集一")
        second = store.create_collection("合集二")
        key = (10001, "private", 10001)
        store.set_selected_collection(_scope(*key), first.id)
        real_conn = TestTransactionAtomicity._fail_next_commit(store)

        with pytest.raises(sqlite3.OperationalError, match="模拟 commit 失败"):
            store.set_selected_collection(_scope(*key), second.id)

        assert store.get_selected_collection(_scope(*key)) == first.id
        assert (
            real_conn.execute(
                "SELECT selected_collection_id FROM chat_collection_scope WHERE "
                "user_id = ? AND chat_type = ? AND chat_id = ?",
                key,
            ).fetchone()[0]
            == first.id
        )


class TestCorruptedChatScopes:
    @pytest.mark.parametrize(
        ("chat_type", "selected_collection_id", "message"),
        [
            ("channel", 0, "chat_type"),
            ("private", -1, "selected_collection_id"),
            ("group", 99, "不存在的合集"),
        ],
    )
    def test_load_rejects_invalid_scope_rows(
        self,
        tmp_sqlite_path: Path,
        chat_type: str,
        selected_collection_id: int,
        message: str,
    ) -> None:
        """损坏的聊天类型、选择范围或悬空合集不得进入缓存。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn)
            conn.execute(
                "INSERT INTO chat_collection_scope "
                "(user_id, chat_type, chat_id, selected_collection_id) "
                "VALUES (1, ?, 2, ?)",
                (chat_type, selected_collection_id),
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match=message):
            store.load()

        assert store._conn is None
        assert store._selected_collections == {}

    @pytest.mark.parametrize(
        ("column", "invalid_value"),
        [
            ("selected_collection_id", -0.5),
            ("selected_collection_id", 1.9),
            ("selected_collection_id", "损坏选择"),
            ("user_id", 1.9),
            ("user_id", "损坏用户"),
            ("chat_id", 2.9),
            ("chat_id", "损坏聊天"),
        ],
    )
    def test_load_rejects_non_integer_scope_identities(
        self,
        tmp_sqlite_path: Path,
        column: str,
        invalid_value: float | str,
    ) -> None:
        """ChatScope 身份与选择不得把 SQLite REAL/TEXT 归一为整数。"""
        scope_values: dict[str, object] = {
            "user_id": 1,
            "chat_type": "private",
            "chat_id": 2,
            "selected_collection_id": 0,
        }
        scope_values[column] = invalid_value
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn)
            conn.execute("INSERT INTO meme_collection (id, name) VALUES (1, '合集')")
            conn.execute(
                "INSERT INTO chat_collection_scope "
                "(user_id, chat_type, chat_id, selected_collection_id) "
                "VALUES (?, ?, ?, ?)",
                (
                    scope_values["user_id"],
                    scope_values["chat_type"],
                    scope_values["chat_id"],
                    scope_values["selected_collection_id"],
                ),
            )
            conn.commit()

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match=f"{column} 必须为整数"):
            store.load()

        assert store._conn is None
        assert store._text_to_id == {}
        assert store._entries == {}
        assert store._entries_by_collection == {}
        assert store._id_to_collections == {}
        assert store._collection_name_to_id == {}
        assert store._selected_collections == {}

    def test_load_rejects_duplicate_scope_key_when_schema_check_is_bypassed(
        self, tmp_sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """即使绕过结构校验，重复整数 ChatScope key 也不得静默覆盖。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            _create_manual_current_schema(conn, chat_scope_primary_key=False)
            conn.executemany(
                "INSERT INTO chat_collection_scope "
                "(user_id, chat_type, chat_id, selected_collection_id) "
                "VALUES (1, 'private', 2, 0)",
                [(), ()],
            )
            conn.commit()
        monkeypatch.setattr(
            MetadataStore,
            "_validate_schema_structure",
            staticmethod(lambda conn, table_names: None),
        )

        store = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="重复 ChatScope"):
            store.load()

        assert store._conn is None
        assert store._selected_collections == {}


class TestDeleteCollectionAndResetScopes:
    def test_delete_resets_scopes_persists_and_reuses_collection_id(
        self, tmp_sqlite_path: Path
    ) -> None:
        """删除空合集原子回退多个选择，保留无关选择并释放合集编号。"""
        store = MetadataStore(str(tmp_sqlite_path))
        store.load()
        deleted = store.create_collection("待删除")
        unrelated = store.create_collection("保留")
        reset_keys = [
            (10001, "private", 10001),
            (10001, "group", 20001),
            (10002, "group", 20001),
        ]
        unrelated_key = (10003, "private", 10003)
        global_key = (10004, "private", 10004)
        for key in reset_keys:
            store.set_selected_collection(_scope(*key), deleted.id)
        store.set_selected_collection(_scope(*unrelated_key), unrelated.id)
        store.set_selected_collection(_scope(*global_key), 0)

        reset_count = store.delete_collection_and_reset_scopes(deleted.id)

        assert reset_count == len(reset_keys)
        assert store.get_collection(deleted.id) is None
        assert store.get_collection_by_name(deleted.name) is None
        assert store.list_collections() == [unrelated]
        assert deleted.id not in store._entries_by_collection
        assert all(
            store.get_selected_collection(_scope(*key)) == 0 for key in reset_keys
        )
        assert store.get_selected_collection(_scope(*unrelated_key)) == unrelated.id
        assert store.get_selected_collection(_scope(*global_key)) == 0
        replacement = store.create_collection("复用")
        assert replacement.id == deleted.id
        store.close()

        reloaded = MetadataStore(str(tmp_sqlite_path))
        reloaded.load()
        assert reloaded.get_collection_by_name("待删除") is None
        assert reloaded.get_collection(replacement.id) == replacement
        assert all(
            reloaded.get_selected_collection(_scope(*key)) == 0
            for key in reset_keys
        )
        assert (
            reloaded.get_selected_collection(_scope(*unrelated_key)) == unrelated.id
        )
        reloaded.close()

    def test_delete_collection_with_entries_is_rejected(
        self, store: MetadataStore
    ) -> None:
        """仍含表情包的合集不得删除或回退选择。"""
        collection = store.create_collection("非空合集")
        store.add("a.webp", "甲", collection_id=collection.id)
        key = (10001, "private", 10001)
        store.set_selected_collection(_scope(*key), collection.id)

        with pytest.raises(ValueError, match="仍含表情包"):
            store.delete_collection_and_reset_scopes(collection.id)

        assert store.get_collection(collection.id) == collection
        assert store.get_selected_collection(_scope(*key)) == collection.id

    def test_delete_rechecks_database_after_external_entry_insert(
        self, store: MetadataStore
    ) -> None:
        """缓存建立后的外部条目写入也必须阻止删除合集。"""
        collection = store.create_collection("合集")
        key = (10001, "private", 10001)
        store.set_selected_collection(_scope(*key), collection.id)
        with sqlite3.connect(store._db_path) as external:
            external.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (1, ?, 1, 'external.webp', '外部写入', NULL)",
                (collection.id,),
            )
            external.commit()
        assert store.collection_entry_count(collection.id) == 0

        with pytest.raises(ValueError, match="仍含表情包"):
            store.delete_collection_and_reset_scopes(collection.id)

        conn = store._conn
        assert conn is not None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM meme_collection WHERE id = ?", (collection.id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM meme WHERE collection_id = ?", (collection.id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT selected_collection_id FROM chat_collection_scope WHERE "
                "user_id = ? AND chat_type = ? AND chat_id = ?",
                key,
            ).fetchone()[0]
            == collection.id
        )
        assert store.get_collection(collection.id) == collection
        assert store.get_collection_by_name(collection.name) == collection
        assert store.get_selected_collection(_scope(*key)) == collection.id
        assert store._entries_by_collection[collection.id] == {}

    @pytest.mark.parametrize("collection_id", [0, 99])
    def test_delete_rejects_global_or_missing_collection(
        self, store: MetadataStore, collection_id: int
    ) -> None:
        """全局编号和不存在的普通合集均明确拒绝。"""
        with pytest.raises(ValueError, match="不能删除|不存在"):
            store.delete_collection_and_reset_scopes(collection_id)

    def test_delete_rejects_ignored_collection_delete(
        self, store: MetadataStore
    ) -> None:
        """DELETE 被合法触发器忽略时回滚 scope 更新且不修改缓存。"""
        collection = store.create_collection("合集")
        key = (10001, "private", 10001)
        store.set_selected_collection(_scope(*key), collection.id)
        conn = store._conn
        assert conn is not None
        conn.execute(
            "CREATE TRIGGER ignore_collection_delete "
            "BEFORE DELETE ON meme_collection "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
        conn.commit()

        with pytest.raises(sqlite3.DatabaseError, match="删除合集.*影响 1 行"):
            store.delete_collection_and_reset_scopes(collection.id)

        assert (
            conn.execute(
                "SELECT COUNT(*) FROM meme_collection WHERE id = ?", (collection.id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT selected_collection_id FROM chat_collection_scope WHERE "
                "user_id = ? AND chat_type = ? AND chat_id = ?",
                key,
            ).fetchone()[0]
            == collection.id
        )
        assert store.get_collection(collection.id) == collection
        assert store.get_collection_by_name(collection.name) == collection
        assert store.get_selected_collection(_scope(*key)) == collection.id
        assert store._entries_by_collection[collection.id] == {}

    def test_delete_rejects_unknown_scope_update_rowcount(
        self, store: MetadataStore
    ) -> None:
        """scope UPDATE 无法确定影响行数时回滚合集和选择。"""
        collection = store.create_collection("合集")
        key = (10001, "private", 10001)
        store.set_selected_collection(_scope(*key), collection.id)
        real_conn = store._conn
        assert real_conn is not None

        class _UnknownRowcountCursor:
            """仅把 rowcount 暴露为未知值的游标代理。"""

            rowcount = -1

            def __init__(self, cursor: sqlite3.Cursor) -> None:
                self._cursor = cursor

            def __getattr__(self, name: str) -> object:
                return getattr(self._cursor, name)

        class _UnknownUpdateRowcountConn:
            """仅包装 scope UPDATE 结果的连接代理。"""

            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def execute(self, sql: str, parameters: tuple[int, ...] = ()) -> Any:
                cursor = self._real.execute(sql, parameters)
                if sql.startswith("UPDATE chat_collection_scope"):
                    return _UnknownRowcountCursor(cursor)
                return cursor

            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        store._conn = cast(
            sqlite3.Connection,
            _UnknownUpdateRowcountConn(real_conn),
        )

        with pytest.raises(sqlite3.DatabaseError, match="无法确定"):
            store.delete_collection_and_reset_scopes(collection.id)

        assert (
            real_conn.execute(
                "SELECT COUNT(*) FROM meme_collection WHERE id = ?", (collection.id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            real_conn.execute(
                "SELECT selected_collection_id FROM chat_collection_scope WHERE "
                "user_id = ? AND chat_type = ? AND chat_id = ?",
                key,
            ).fetchone()[0]
            == collection.id
        )
        assert store.get_collection(collection.id) == collection
        assert store.get_selected_collection(_scope(*key)) == collection.id

    @pytest.mark.parametrize("failure_stage", ["update", "delete", "commit"])
    def test_delete_failure_rolls_back_collection_and_scopes(
        self, store: MetadataStore, failure_stage: str
    ) -> None:
        """UPDATE、DELETE 或 commit 失败均不得留下部分数据库或缓存变更。"""
        collection = store.create_collection("合集")
        keys = [
            (10001, "private", 10001),
            (10001, "group", 20001),
        ]
        for key in keys:
            store.set_selected_collection(_scope(*key), collection.id)
        real_conn = store._conn
        assert real_conn is not None

        if failure_stage == "update":
            real_conn.execute(
                "CREATE TRIGGER fail_scope_update "
                "BEFORE UPDATE ON chat_collection_scope "
                "BEGIN SELECT RAISE(ABORT, '模拟 UPDATE 失败'); END"
            )
            real_conn.commit()
        elif failure_stage == "delete":
            real_conn.execute(
                "CREATE TRIGGER fail_collection_delete "
                "BEFORE DELETE ON meme_collection "
                "BEGIN SELECT RAISE(ABORT, '模拟 DELETE 失败'); END"
            )
            real_conn.commit()
        else:
            real_conn = TestTransactionAtomicity._fail_next_commit(store)

        with pytest.raises(sqlite3.DatabaseError, match="模拟"):
            store.delete_collection_and_reset_scopes(collection.id)

        assert store.get_collection(collection.id) == collection
        assert store.get_collection_by_name(collection.name) == collection
        assert all(
            store.get_selected_collection(_scope(*key)) == collection.id
            for key in keys
        )
        assert (
            real_conn.execute(
                "SELECT name FROM meme_collection WHERE id = ?", (collection.id,)
            ).fetchone()["name"]
            == collection.name
        )
        selected_rows = real_conn.execute(
            "SELECT selected_collection_id FROM chat_collection_scope ORDER BY chat_type"
        ).fetchall()
        assert [row["selected_collection_id"] for row in selected_rows] == [
            collection.id,
            collection.id,
        ]


class TestCollectionAwareEntries:
    def test_local_ids_are_independent_and_reuse_smallest_gap(
        self, store: MetadataStore
    ) -> None:
        """局部编号按合集独立分配，并复用删除后的最小空洞。"""
        first_collection = store.create_collection("合集一")
        second_collection = store.create_collection("合集二")
        first = store.add("a.webp", "甲", collection_id=first_collection.id)
        second = store.add("b.webp", "乙", collection_id=first_collection.id)
        other = store.add("c.webp", "丙", collection_id=second_collection.id)
        store.remove(first)
        reused = store.add("d.webp", "丁", collection_id=first_collection.id)

        second_entry = store.get_entry(second)
        other_entry = store.get_entry(other)
        reused_entry = store.get_entry(reused)
        assert second_entry is not None
        assert other_entry is not None
        assert reused_entry is not None
        assert second_entry.public_id == MemePublicId(first_collection.id, 2)
        assert other_entry.public_id == MemePublicId(second_collection.id, 1)
        assert reused_entry.public_id == MemePublicId(first_collection.id, 1)

    def test_same_text_allowed_across_collections(self, store: MetadataStore) -> None:
        """相同文本可分别存在于不同合集。"""
        first_collection = store.create_collection("合集一")
        second_collection = store.create_collection("合集二")
        first = store.add("a.webp", "相同文本", collection_id=first_collection.id)
        second = store.add("b.webp", "相同文本", collection_id=second_collection.id)

        assert first != second
        assert (
            store.get_id_by_text("相同文本", collection_id=first_collection.id) == first
        )
        assert (
            store.get_id_by_text("相同文本", collection_id=second_collection.id)
            == second
        )

    def test_same_text_rejected_within_collection(self, store: MetadataStore) -> None:
        """同合集内相同文本仍映射为 DuplicateEntryError。"""
        collection = store.create_collection("合集一")
        store.add("a.webp", "相同文本", collection_id=collection.id)

        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add("b.webp", "相同文本", collection_id=collection.id)

        assert exc_info.value.conflicts == [("text", "相同文本")]

    def test_global_defaults_preserve_legacy_behavior(
        self, store: MetadataStore
    ) -> None:
        """旧调用默认写入全局合集并继续按 text 查询。"""
        entry_id = store.add("global.webp", "全局文本")
        entry = store.get_entry(entry_id)

        assert entry == MemeEntry(
            id=entry_id,
            image_path="global.webp",
            text="全局文本",
            speaker=None,
            tags=[],
            collection_id=0,
            local_id=1,
            collection_name=GLOBAL_COLLECTION_NAME,
        )
        assert store.get_id_by_text("全局文本") == entry_id

    def test_get_entry_by_public_id_and_get_entries_subset(
        self, store: MetadataStore
    ) -> None:
        """公开 ID 精确解析条目，合集读取返回独立子集副本。"""
        collection = store.create_collection("合集一")
        global_id = store.add("global.webp", "全局")
        local_id = store.add("local.webp", "局部", collection_id=collection.id)

        entry = store.get_entry_by_public_id(MemePublicId(collection.id, 1))
        subset = store.get_entries(collection.id)
        all_entries = store.get_entries()

        assert entry == store.get_entry(local_id)
        assert subset == {local_id: entry}
        assert global_id not in subset
        assert set(all_entries) == {global_id, local_id}
        subset.clear()
        assert store.get_entries(collection.id) == {local_id: entry}
        assert store.get_all_entries() == all_entries

    def test_load_rejects_orphaned_collection_entry(
        self, tmp_sqlite_path: Path
    ) -> None:
        """普通合集条目缺少合集关联时拒绝加载。"""
        with sqlite3.connect(tmp_sqlite_path) as conn:
            create_current_schema(conn)
            conn.execute(
                "INSERT INTO meme "
                "(id, collection_id, local_id, image_path, text, speaker) "
                "VALUES (1, 99, 1, 'orphan.webp', '孤儿', NULL)"
            )
            conn.commit()

        s = MetadataStore(str(tmp_sqlite_path))
        with pytest.raises(sqlite3.DatabaseError, match="合集"):
            s.load()
        assert s._conn is None

    def test_unknown_collection_is_rejected(self, store: MetadataStore) -> None:
        """写入不存在的普通合集时拒绝且不改变缓存。"""
        with pytest.raises(ValueError, match="collection_id=99 不存在"):
            store.add("a.webp", "甲", collection_id=99)
        assert store.get_all_entries() == {}


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

    def test_find_next_id_ignores_non_positive_dirty_rows(
        self, store: MetadataStore
    ) -> None:
        """最小空洞查询只基于正内部 ID。"""
        conn = store._conn
        assert conn is not None
        conn.execute(
            "INSERT INTO meme "
            "(id, collection_id, local_id, image_path, text, speaker) "
            "VALUES (-1, 0, 99, 'dirty.webp', '脏数据', NULL)"
        )
        conn.commit()

        assert store.find_next_id() == 1

    @pytest.mark.parametrize("entry_id", [0, -1])
    def test_add_with_id_rejects_non_positive_id(
        self, store: MetadataStore, entry_id: int
    ) -> None:
        """显式内部 ID 必须为正整数。"""
        with pytest.raises(ValueError, match="entry_id 必须为正整数"):
            store.add_with_id(entry_id, "bad.webp", "损坏")

        assert store.get_entries() == {}


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

    def test_update_collection_local_and_text_refreshes_caches(
        self, store: MetadataStore
    ) -> None:
        """移动合集并改局部号和文本后全部联合缓存同步。"""
        source = store.create_collection("来源")
        target = store.create_collection("目标")
        entry_id = store.add("a.webp", "旧", collection_id=source.id)

        assert store.update(
            entry_id,
            collection_id=target.id,
            local_id=3,
            text="新",
        )

        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.public_id == MemePublicId(target.id, 3)
        assert entry.collection_name == target.name
        assert store.get_entry_by_public_id(MemePublicId(source.id, 1)) is None
        assert store.get_entry_by_public_id(MemePublicId(target.id, 3)) == entry
        assert store.get_id_by_text("旧", collection_id=source.id) is None
        assert store.get_id_by_text("新", collection_id=target.id) == entry_id
        assert store.get_entries(source.id) == {}
        assert store.get_entries(target.id) == {entry_id: entry}

    def test_update_text_conflict_is_limited_to_final_collection(
        self, store: MetadataStore
    ) -> None:
        """更新冲突以最终合集判定，跨合集相同文本不冲突。"""
        first = store.create_collection("合集一")
        second = store.create_collection("合集二")
        existing = store.add("a.webp", "相同", collection_id=first.id)
        moved = store.add("b.webp", "不同", collection_id=second.id)

        assert store.update(moved, text="相同") is True
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.update(moved, collection_id=first.id, local_id=2)

        assert exc_info.value.conflicts == [("text", "相同")]
        existing_entry = store.get_entry_by_public_id(MemePublicId(first.id, 1))
        assert existing_entry is not None
        assert existing_entry.id == existing
        entry = store.get_entry(moved)
        assert entry is not None
        assert entry.public_id == MemePublicId(second.id, 1)

    @pytest.mark.parametrize("field", ["image_path", "text"])
    def test_update_rejects_explicit_none_for_required_fields(
        self, store: MetadataStore, field: str
    ) -> None:
        """NOT NULL 字段显式传 None 时在 SQL 前抛 ValueError。"""
        entry_id = store.add("a.webp", "甲")

        with pytest.raises(ValueError, match=f"{field} 不能为 None"):
            store.update(entry_id, **{field: None})

        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.image_path == "a.webp"
        assert entry.text == "甲"

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            (None, "collection_id 不能为 None"),
            (-1, "collection_id=-1 不存在"),
            (99, "collection_id=99 不存在"),
        ],
    )
    def test_update_rejects_invalid_collection_id(
        self,
        store: MetadataStore,
        value: int | None,
        message: str,
    ) -> None:
        """合集非法值在 SQL 前拒绝。"""
        entry_id = store.add("a.webp", "甲")

        with pytest.raises(ValueError, match=message):
            store.update(entry_id, collection_id=value)

        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.public_id == MemePublicId(0, 1)

    @pytest.mark.parametrize("value", [None, 0, -1])
    def test_update_rejects_invalid_local_id(
        self, store: MetadataStore, value: int | None
    ) -> None:
        """局部编号非法值在 SQL 前拒绝。"""
        entry_id = store.add("a.webp", "甲")

        with pytest.raises(ValueError, match="local_id 必须为正整数"):
            store.update(entry_id, local_id=value)

        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.public_id == MemePublicId(0, 1)

    def test_unmapped_integrity_error_preserves_original_type(
        self, store: MetadataStore
    ) -> None:
        """无法映射到已知唯一冲突时不抛空 DuplicateEntryError。"""
        entry_id = store.add("a.webp", "甲")
        conn = store._conn
        assert conn is not None
        conn.execute(
            "CREATE TRIGGER reject_meme_update "
            "BEFORE UPDATE ON meme "
            "BEGIN SELECT RAISE(ABORT, '自定义完整性失败'); END"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            store.update(entry_id, speaker="说话人")

        assert type(exc_info.value) is sqlite3.IntegrityError
        assert "自定义完整性失败" in str(exc_info.value)
        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.speaker is None

    def test_update_local_id_conflict_preserves_cache(
        self, store: MetadataStore
    ) -> None:
        """联合局部号冲突映射为 DuplicateEntryError 且缓存不变。"""
        collection = store.create_collection("合集")
        first = store.add("a.webp", "甲", collection_id=collection.id)
        second = store.add("b.webp", "乙", collection_id=collection.id)

        with pytest.raises(DuplicateEntryError) as exc_info:
            store.update(second, local_id=1)

        assert exc_info.value.conflicts == [("local_id", "1")]
        first_entry = store.get_entry(first)
        second_entry = store.get_entry(second)
        assert first_entry is not None
        assert second_entry is not None
        assert first_entry.public_id == MemePublicId(collection.id, 1)
        assert second_entry.public_id == MemePublicId(collection.id, 2)


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


class TestTransactionAtomicity:
    @staticmethod
    def _fail_next_commit(store: MetadataStore) -> sqlite3.Connection:
        """安装仅下一次 commit 失败的连接代理，并返回真实连接。"""
        real_conn = store._conn
        assert real_conn is not None

        class _FailingCommitConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real
                self._should_fail = True

            def commit(self) -> None:
                if self._should_fail:
                    self._should_fail = False
                    raise sqlite3.OperationalError("模拟 commit 失败")
                self._real.commit()

            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        store._conn = cast(sqlite3.Connection, _FailingCommitConn(real_conn))
        return real_conn

    def test_create_collection_rolls_back_when_commit_fails(
        self, store: MetadataStore
    ) -> None:
        """合集 commit 失败时不更新数据库或缓存，后续写入不提交失败行。"""
        real_conn = self._fail_next_commit(store)

        with pytest.raises(sqlite3.OperationalError, match="模拟 commit 失败"):
            store.create_collection("失败合集")

        assert store.get_collection(1) is None
        assert store.get_collection_by_name("失败合集") is None
        assert store.list_collections() == []
        created = store.create_collection("成功合集")
        assert created.id == 1
        rows = real_conn.execute(
            "SELECT id, name FROM meme_collection ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["name"]) for row in rows] == [(1, "成功合集")]

    def test_remove_rolls_back_when_commit_fails(self, store: MetadataStore) -> None:
        """删除 commit 失败时保留数据库和缓存，后续写入不提交失败删除。"""
        entry_id = store.add("a.webp", "甲")
        real_conn = self._fail_next_commit(store)

        with pytest.raises(sqlite3.OperationalError, match="模拟 commit 失败"):
            store.remove(entry_id)

        assert store.get_entry(entry_id) is not None
        assert store.add("b.webp", "乙") == 2
        rows = real_conn.execute(
            "SELECT id, image_path FROM meme ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["image_path"]) for row in rows] == [
            (1, "a.webp"),
            (2, "b.webp"),
        ]

    def test_new_schema_creation_rolls_back_partial_ddl(
        self, tmp_sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """新库中途 DDL 失败时不残留业务表。"""

        def fail_after_first_table(conn: sqlite3.Connection) -> None:
            conn.execute("CREATE TABLE partial_business_table (id INTEGER)")
            raise RuntimeError("模拟 DDL 失败")

        monkeypatch.setattr(
            metadata_store_module,
            "create_current_schema",
            fail_after_first_table,
        )
        store = MetadataStore(str(tmp_sqlite_path))

        with pytest.raises(RuntimeError, match="模拟 DDL 失败"):
            store.load()

        assert store._conn is None
        with sqlite3.connect(tmp_sqlite_path) as conn:
            table_names = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        assert table_names == []

    def test_add_rolls_back_when_tag_write_fails(
        self, store: MetadataStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """add 的标签写入失败时回滚主行，后续写入不提交失败数据。"""

        def fail_write_tags(entry_id: int, tags: list[str] | None) -> None:
            raise RuntimeError("标签写入失败")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_write_tags", fail_write_tags)
            with pytest.raises(RuntimeError, match="标签写入失败"):
                store.add("failed.webp", "失败", tags=["标签"])

        assert store.get_all_entries() == {}
        assert store.get_id_by_text("失败") is None
        assert store.add("success.webp", "成功") == 1

        with sqlite3.connect(store._db_path) as conn:
            rows = conn.execute(
                "SELECT id, image_path, text FROM meme ORDER BY id"
            ).fetchall()
        assert rows == [(1, "success.webp", "成功")]

    def test_add_with_id_rolls_back_when_tag_write_fails(
        self, store: MetadataStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """add_with_id 标签失败后可用同一内部 ID 重新写入。"""

        def fail_write_tags(entry_id: int, tags: list[str] | None) -> None:
            raise RuntimeError("标签写入失败")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_write_tags", fail_write_tags)
            with pytest.raises(RuntimeError, match="标签写入失败"):
                store.add_with_id(7, "failed.webp", "失败", tags=["标签"])

        assert store.get_entry(7) is None
        assert store.get_id_by_text("失败") is None
        assert store.add_with_id(7, "success.webp", "成功") == 7

        with sqlite3.connect(store._db_path) as conn:
            rows = conn.execute(
                "SELECT id, image_path, text FROM meme ORDER BY id"
            ).fetchall()
        assert rows == [(7, "success.webp", "成功")]

    def test_update_rolls_back_when_tag_write_fails(
        self, store: MetadataStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """update 标签失败时回滚字段和标签，后续更新只提交自身变更。"""
        entry_id = store.add("a.webp", "原文", tags=["旧标签"])

        def fail_write_tags(inner_entry_id: int, tags: list[str] | None) -> None:
            raise RuntimeError("标签写入失败")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_write_tags", fail_write_tags)
            with pytest.raises(RuntimeError, match="标签写入失败"):
                store.update(entry_id, text="失败文本", tags=["新标签"])

        cached = store.get_entry(entry_id)
        assert cached is not None
        assert cached.text == "原文"
        assert cached.tags == ("旧标签",)
        assert store.get_id_by_text("失败文本") is None

        assert store.update(entry_id, speaker="说话人") is True
        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.text == "原文"
        assert entry.tags == ("旧标签",)
        assert entry.speaker == "说话人"

        store.close()
        store.load()
        reloaded = store.get_entry(entry_id)
        assert reloaded == entry


class TestTagsAndCascade:
    def test_tags_assembled_in_entry(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["搞笑", "猫"])
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == ("搞笑", "猫")

    def test_cascade_delete_removes_tags(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["搞笑"])
        store.remove(eid)
        # 重新插入同 id，tags 应为空（CASCADE 清掉了旧 tag 行）
        store.add_with_id(eid, "b.jpg", "乙")
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == ()

    def test_update_replaces_tags(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["旧"])
        store.update(eid, tags=["新1", "新2"])
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == ("新1", "新2")


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

    def test_add_with_id_tracks_explicit_collection_and_local_id(
        self, store: MetadataStore
    ) -> None:
        """迁移写入可显式指定合集与局部号并同步缓存。"""
        collection = store.create_collection("合集")

        result = store.add_with_id(
            7,
            "a.webp",
            "甲",
            tags=["标签"],
            collection_id=collection.id,
            local_id=4,
        )

        entry = store.get_entry(7)
        assert result == 7
        assert entry is not None
        assert entry.public_id == MemePublicId(collection.id, 4)
        assert entry.collection_name == collection.name
        assert entry.tags == ("标签",)
        assert store.get_entry_by_public_id(MemePublicId(collection.id, 4)) == entry
        assert store.get_id_by_text("甲", collection_id=collection.id) == 7

    def test_add_with_id_local_conflict_reported(self, store: MetadataStore) -> None:
        """add_with_id 联合局部号冲突报告 local_id。"""
        collection = store.create_collection("合集")
        store.add_with_id(
            7,
            "a.webp",
            "甲",
            collection_id=collection.id,
            local_id=4,
        )

        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add_with_id(
                8,
                "b.webp",
                "乙",
                collection_id=collection.id,
                local_id=4,
            )

        assert exc_info.value.conflicts == [("local_id", "4")]
        assert store.get_entry(8) is None

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


class TestFrozenAndEntriesCache:
    """MemeEntry frozen 与 _entries 缓存重建测试。"""

    def test_meme_entry_is_frozen(self, store: MetadataStore) -> None:
        """MemeEntry frozen 后字段不可赋值，抛 FrozenInstanceError。"""
        import dataclasses

        store.add("a.jpg", "甲")
        entry = store.get_entry(1)
        assert entry is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.image_path = "b.jpg"  # type: ignore[misc, ty:invalid-assignment]

    def test_load_rebuilds_entries_and_text_to_id(self, tmp_sqlite_path: Path) -> None:
        """load 后 _entries 与 _text_to_id 同步填充，含 tags。"""
        s1 = MetadataStore(str(tmp_sqlite_path))
        s1.load()
        s1.add("a.jpg", "甲", tags=["搞笑", "猫"])
        s1.close()

        s2 = MetadataStore(str(tmp_sqlite_path))
        s2.load()
        assert 1 in s2._entries
        assert s2._entries[1].text == "甲"
        assert s2._entries[1].tags == ("搞笑", "猫")
        assert s2._text_to_id == {(0, "甲"): 1}
        s2.close()


class TestWriteMaintainsEntriesCache:
    """写操作同步维护 _entries 缓存。"""

    def test_add_updates_entries_cache(self, store: MetadataStore) -> None:
        """add 后 _entries 同步含新条目，_text_to_id 同步。"""
        eid = store.add("a.jpg", "甲", tags=["搞笑", "猫"])
        assert eid in store._entries
        assert store._entries[eid].text == "甲"
        assert store._entries[eid].tags == ("搞笑", "猫")
        assert store._text_to_id[(0, "甲")] == eid

    def test_add_dedup_sort_tags_in_cache(self, store: MetadataStore) -> None:
        """缓存 tags 去重 + 字典序排序，与 SQL 存储一致。"""
        eid = store.add("b.jpg", "乙", tags=["x", "x", "a"])
        assert store._entries[eid].tags == ("a", "x")
        # 与 get_entry（当前仍走 SQL）返回的 tags 一致
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == store._entries[eid].tags

    def test_add_with_id_updates_entries_cache(self, store: MetadataStore) -> None:
        """add_with_id 同步维护 _entries。"""
        store.add_with_id(5, "e.jpg", "戊", tags=["t"])
        assert 5 in store._entries
        assert store._entries[5].tags == ("t",)
        assert store._text_to_id[(0, "戊")] == 5

    def test_update_refreshes_entries_cache(self, store: MetadataStore) -> None:
        """update 后 _entries 同步更新字段，_text_to_id 同步 text。"""
        eid = store.add("a.jpg", "甲", tags=["旧"])
        store.update(eid, text="乙", tags=["新1", "新2"])
        assert store._entries[eid].text == "乙"
        assert store._entries[eid].tags == ("新1", "新2")
        assert (0, "甲") not in store._text_to_id
        assert store._text_to_id[(0, "乙")] == eid

    def test_update_image_path_in_cache(self, store: MetadataStore) -> None:
        """update image_path 同步到 _entries。"""
        eid = store.add("old.jpg", "甲")
        store.update(eid, image_path="new.jpg")
        assert store._entries[eid].image_path == "new.jpg"

    def test_update_nonexistent_leaves_cache(self, store: MetadataStore) -> None:
        """id 不存在时不动缓存。"""
        store.update(999, image_path="x.jpg")
        assert 999 not in store._entries

    def test_remove_updates_entries_cache(self, store: MetadataStore) -> None:
        """remove 后 _entries 与 _text_to_id 同步删除。"""
        eid = store.add("a.jpg", "甲")
        store.remove(eid)
        assert eid not in store._entries
        assert (0, "甲") not in store._text_to_id


class TestReadPathHitsCache:
    """读路径命中缓存，但不泄漏可变 tags 引用。"""

    @pytest.mark.parametrize(
        "reader",
        [
            lambda store, entry_id: store.get_entry(entry_id),
            lambda store, entry_id: store.get_entries()[entry_id],
            lambda store, entry_id: store.get_entries(0)[entry_id],
            lambda store, entry_id: store.get_all_entries()[entry_id],
            lambda store, entry_id: store.get_by_filename("a.jpg"),
            lambda store, entry_id: store.get_entry_by_public_id(MemePublicId(0, 1)),
        ],
    )
    def test_public_entry_reads_return_immutable_tags(
        self,
        store: MetadataStore,
        reader: Any,
    ) -> None:
        """公开读取结果的 tags 为不可变 tuple，外部无法修改以污染缓存。"""
        entry_id = store.add("a.jpg", "甲", tags=["原标签"])
        entry = reader(store, entry_id)
        assert entry is not None

        # tags 为不可变 tuple，append 抛 AttributeError，缓存天然防污染
        with pytest.raises(AttributeError):
            entry.tags.append("外部修改")

        reread = store.get_entry(entry_id)
        assert reread is not None
        assert reread.tags == ("原标签",)
        assert store._entries[entry_id].tags == ("原标签",)
        store.close()
        store.load()
        reloaded = store.get_entry(entry_id)
        assert reloaded is not None
        assert reloaded.tags == ("原标签",)

    def test_get_all_entries_returns_new_dict_with_immutable_entries(
        self, store: MetadataStore
    ) -> None:
        """全量读取返回新 dict；entry 为不可变快照，零拷贝共享缓存引用。"""
        entry_id = store.add("a.jpg", "甲", tags=["标签"])
        result = store.get_all_entries()

        # dict 仍是新对象，不泄漏缓存 dict
        assert result is not store._entries
        # entry 为 frozen + tuple tags 不可变，零拷贝共享缓存引用
        assert result[entry_id] is store._entries[entry_id]
        assert result[entry_id].tags == ("标签",)

    def test_get_all_entries_no_sql_after_cache(self, store: MetadataStore) -> None:
        """缓存命中后多次 get_all_entries 不触发 SQL。

        sqlite3.Connection 为不可变 C 类型，无法 monkeypatch execute，
        故用代理包装 _conn 统计 execute 调用次数。
        """
        store.add("a.jpg", "甲")
        real_conn = store._conn
        assert real_conn is not None

        class _CountingConn:
            """代理 sqlite3.Connection，统计 execute 调用次数。"""

            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real
                self.execute_count = 0

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                self.execute_count += 1
                return self._real.execute(*args, **kwargs)

            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        counter = _CountingConn(real_conn)
        store._conn = cast(sqlite3.Connection, counter)
        try:
            for _ in range(3):
                store.get_all_entries()
        finally:
            store._conn = real_conn
        assert counter.execute_count == 0

    def test_entry_count_reads_cache_len(self, store: MetadataStore) -> None:
        """entry_count 等于 len(_entries)。"""
        store.add("a.jpg", "甲")
        store.add("b.jpg", "乙")
        assert store.entry_count() == len(store._entries) == 2


class TestRenameCollection:
    def test_rename_collection_updates_name_image_path_and_cache(
        self, store: MetadataStore
    ) -> None:
        """rename_collection 同步更新合集名、该合集条目 image_path 首段与全部缓存。"""
        collection = store.create_collection("新三国")
        cid = collection.id
        entry_id = store.add("新三国/截图/a.webp", "加班", collection_id=cid)
        other_id = store.create_collection("甄嬛传")
        store.add("甄嬛传/b.webp", "别的", collection_id=other_id.id)

        renamed = store.rename_collection(cid, "旧三国")

        assert renamed == MemeCollection(cid, "旧三国")
        # meme_collection 行
        assert store.get_collection(cid) == MemeCollection(cid, "旧三国")
        assert store.get_collection_by_name("新三国") is None
        assert store.get_collection_by_name("旧三国") is not None
        # 条目 image_path 首段替换，id 与 local_id 不变
        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.image_path == "旧三国/截图/a.webp"
        assert entry.collection_name == "旧三国"
        assert entry.collection_id == cid
        # 其他合集条目不受影响
        other = store.get_entry_by_public_id(MemePublicId(other_id.id, 1))
        assert other is not None
        assert other.image_path == "甄嬛传/b.webp"
        assert other.collection_name == "甄嬛传"
        # public_id 仍可解析到同一条目
        assert store.get_entry_by_public_id(MemePublicId(cid, 1)) is not None

    def test_rename_collection_rejects_duplicate_name(
        self, store: MetadataStore
    ) -> None:
        """重命名为已登记名称时回滚，DB 与缓存不变。"""
        first = store.create_collection("一")
        second = store.create_collection("二")
        store.add("二/a.webp", "文字", collection_id=second.id)

        with pytest.raises(sqlite3.IntegrityError):
            store.rename_collection(second.id, "一")

        # 缓存与 DB 均未变
        assert store.get_collection(second.id) == MemeCollection(second.id, "二")
        assert store.get_collection_by_name("一") == MemeCollection(first.id, "一")
        entry = store.get_entry_by_public_id(MemePublicId(second.id, 1))
        assert entry is not None
        assert entry.image_path == "二/a.webp"
        assert entry.collection_name == "二"

    def test_rename_collection_rejects_unknown_id(self, store: MetadataStore) -> None:
        """不存在的合集编号抛 ValueError。"""
        with pytest.raises(ValueError):
            store.rename_collection(999, "新名")

    def test_rename_collection_rejects_zero_id(self, store: MetadataStore) -> None:
        """编号 0（全局）抛 ValueError。"""
        with pytest.raises(ValueError):
            store.rename_collection(0, "新名")

    def test_rename_collection_same_name_is_noop(self, store: MetadataStore) -> None:
        """新名等于旧名时直接返回，不触发任何 SQL 写入。"""
        collection = store.create_collection("同名")
        result = store.rename_collection(collection.id, "同名")
        assert result == collection
