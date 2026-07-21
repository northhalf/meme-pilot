"""元数据存储模块 — 基于 sqlite3。

存储每条表情包的内部 id、合集内公开编号、图片路径、OCR 文字、说话人和标记词。
内部 id 与 VectorStore 的向量 id 完全一一对应。
"""

import logging
import os
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from bot.log_context import timed
from bot.session import ChatScope

from .types import GLOBAL_COLLECTION_NAME, MemeCollection, MemePublicId

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2
_UNSET = object()  # 哨兵值，区分「不修改字段」与显式的 None

_SCHEMA = (
    "CREATE TABLE schema_version (version INTEGER NOT NULL);",
    "CREATE TABLE meme_collection ("
    "id INTEGER PRIMARY KEY CHECK (id > 0),"
    "name TEXT NOT NULL UNIQUE"
    ");",
    "CREATE TABLE meme ("
    "id INTEGER PRIMARY KEY,"
    "collection_id INTEGER NOT NULL CHECK (collection_id >= 0),"
    "local_id INTEGER NOT NULL CHECK (local_id > 0),"
    "image_path TEXT NOT NULL,"
    "text TEXT NOT NULL,"
    "speaker TEXT"
    ");",
    "CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);",
    "CREATE UNIQUE INDEX idx_meme_collection_local ON meme(collection_id, local_id);",
    "CREATE UNIQUE INDEX idx_meme_collection_text ON meme(collection_id, text);",
    "CREATE TABLE meme_tag ("
    "meme_id INTEGER NOT NULL,"
    "tag TEXT NOT NULL,"
    "PRIMARY KEY (meme_id, tag),"
    "FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE"
    ");",
    "CREATE INDEX idx_meme_tag_tag ON meme_tag(tag);",
    "CREATE TABLE chat_collection_scope ("
    "user_id INTEGER NOT NULL,"
    "chat_type TEXT NOT NULL CHECK (chat_type IN ('private', 'group')),"
    "chat_id INTEGER NOT NULL,"
    "selected_collection_id INTEGER NOT NULL "
    "CHECK (selected_collection_id >= 0),"
    "PRIMARY KEY (user_id, chat_type, chat_id)"
    ");",
)

_FIND_NEXT_ID_SQL = (
    "SELECT MIN(t.id) + 1 AS next_id "
    "FROM (SELECT 0 AS id UNION ALL SELECT id FROM meme WHERE id > 0) t "
    "WHERE NOT EXISTS(SELECT 1 FROM meme t2 WHERE t2.id = t.id + 1)"
)

_FIND_NEXT_COLLECTION_ID_SQL = (
    "SELECT MIN(t.id) + 1 AS next_id "
    "FROM (SELECT 0 AS id UNION ALL SELECT id FROM meme_collection) t "
    "WHERE NOT EXISTS("
    "SELECT 1 FROM meme_collection t2 WHERE t2.id = t.id + 1"
    ")"
)

_FIND_NEXT_LOCAL_ID_SQL = (
    "SELECT MIN(t.local_id) + 1 AS next_id "
    "FROM ("
    "SELECT 0 AS local_id "
    "UNION ALL "
    "SELECT local_id FROM meme WHERE collection_id = ?"
    ") t "
    "WHERE NOT EXISTS("
    "SELECT 1 FROM meme t2 "
    "WHERE t2.collection_id = ? AND t2.local_id = t.local_id + 1"
    ")"
)


class SchemaVersionError(RuntimeError):
    """数据库 Schema 版本与当前程序不兼容。"""


class DuplicateEntryError(sqlite3.IntegrityError):
    """写入或更新时触发唯一约束冲突。

    Attributes:
        conflicts: 所有命中的冲突字段列表，每项为 ``(column, value)``。
            顺序固定为 id、image_path、local_id、text。
    """

    def __init__(self, conflicts: list[tuple[str, str]]) -> None:
        """初始化冲突异常。

        Args:
            conflicts: 命中的字段和值。
        """
        self.conflicts = conflicts
        detail = "，".join(f"{column}={value!r}" for column, value in conflicts)
        super().__init__(f"重复字段冲突: {detail}")


def create_current_schema(conn: sqlite3.Connection) -> None:
    """在已确认为空的 SQLite 连接中创建当前 Schema。

    调用方负责确认连接中没有既有表，并决定何时提交事务。本函数创建全部表和索引，
    同时向 ``schema_version`` 插入唯一版本行。

    Args:
        conn: 目标 SQLite 连接。
    """
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO schema_version (version) VALUES (?)",
        (CURRENT_SCHEMA_VERSION,),
    )


@dataclass(frozen=True, slots=True)
class MemeEntry:
    """单条表情包元数据。

    Attributes:
        id: 内部索引 id，与 VectorStore 向量 id 一一对应。
        image_path: memes/ 目录下相对路径。
        text: 按中文逗号拼接的 OCR 文本。
        speaker: 说话人，可空。
        tags: 标记词元组（不可变）。
        collection_id: 所属合集编号，0 表示全局根目录。
        local_id: 合集内正整数编号。
        collection_name: 所属合集名称。
    """

    id: int
    image_path: str
    text: str
    speaker: str | None = None
    tags: Sequence[str] = field(default_factory=tuple)
    collection_id: int = 0
    local_id: int = 1
    collection_name: str = GLOBAL_COLLECTION_NAME

    def __post_init__(self) -> None:
        """规范化 tags 为不可变 tuple。

        frozen dataclass 无法直接赋值，用 object.__setattr__ 绕过冻结，
        保证无论构造时传入 list 还是 tuple 都规范化为 tuple，使实例完全
        不可变，_snapshot_entry 可安全零拷贝返回原对象引用。

        Args:
            self: 当前 MemeEntry 实例。
        """
        object.__setattr__(self, "tags", tuple(self.tags))

    @property
    def public_id(self) -> MemePublicId:
        """返回用户可见的复合 ID。

        Returns:
            当前条目的合集编号和合集内编号。
        """
        return MemePublicId(self.collection_id, self.local_id)


class MetadataStore:
    """sqlite3 元数据存储。"""

    def __init__(self, db_path: str) -> None:
        """初始化 MetadataStore。

        Args:
            db_path: sqlite 数据库文件路径，load() 时自动创建父目录与文件。
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._text_to_id: dict[tuple[int, str], int] = {}
        self._entries: dict[int, MemeEntry] = {}
        # collection_id -> [local_id, entries_id]
        self._entries_by_collection: dict[int, dict[int, int]] = {}
        self._id_to_collections: dict[int, MemeCollection] = {}
        self._collection_name_to_id: dict[str, int] = {}
        self._selected_collections: dict[ChatScope, int] = {}

    def load(self) -> None:
        """打开当前版本数据库并重建全部内存缓存。

        完全空的数据库会创建当前 Schema。已有数据库只执行版本校验，运行时不会自动
        迁移或执行 ``ALTER TABLE``。

        Raises:
            SchemaVersionError: 数据库无版本信息、版本错误或版本行数不为一。
            sqlite3.DatabaseError: 数据库损坏或普通条目引用了不存在的合集。
        """
        with self._lock:
            conn: sqlite3.Connection | None = None
            try:
                self._clear_caches()
                self._close_connection()
                db_dir = os.path.dirname(self._db_path)
                if db_dir:
                    os.makedirs(db_dir, exist_ok=True)

                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")

                table_names = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if not table_names:
                    conn.execute("BEGIN IMMEDIATE")
                    create_current_schema(conn)
                    conn.commit()
                elif "schema_version" not in table_names:
                    raise SchemaVersionError(
                        "检测到旧版 index.db，请停止 Bot 后运行 "
                        "scripts.migrate_meme_collections upgrade-schema"
                    )
                else:
                    try:
                        version_rows = list(
                            conn.execute(
                                "SELECT version, typeof(version) AS storage_type "
                                "FROM schema_version"
                            )
                        )
                    except sqlite3.DatabaseError as exc:
                        raise SchemaVersionError(
                            "无法识别数据库 Schema 版本，请停止 Bot 后运行 "
                            "scripts.migrate_meme_collections upgrade-schema"
                        ) from exc
                    valid_version = (
                        len(version_rows) == 1
                        and version_rows[0]["storage_type"] == "integer"
                        and type(version_rows[0]["version"]) is int
                        and version_rows[0]["version"] == CURRENT_SCHEMA_VERSION
                    )
                    if not valid_version:
                        versions = [
                            (row["version"], row["storage_type"])
                            for row in version_rows
                        ]
                        raise SchemaVersionError(
                            f"不支持的 Schema 版本: {versions!r}，请停止 Bot 后运行 "
                            "scripts.migrate_meme_collections upgrade-schema"
                        )
                    self._validate_schema_structure(conn, table_names)

                self._rebuild_caches(conn)
            except Exception:
                self._clear_caches()
                try:
                    if conn is not None:
                        conn.rollback()
                finally:
                    if conn is not None:
                        conn.close()
                raise

            self._conn = conn
            logger.info(
                "MetadataStore 加载完成: %s, 共 %d 条记录",
                self._db_path,
                len(self._entries),
            )

    def close(self) -> None:
        """关闭连接，重复或并发调用安全。"""
        with self._lock:
            self._close_connection()

    def _close_connection(self) -> None:
        """关闭并清除当前连接，调用方已持锁。"""
        conn = self._conn
        self._conn = None
        if conn is not None:
            conn.close()

    def _require_conn(self) -> sqlite3.Connection:
        """返回当前连接。

        Returns:
            当前 sqlite3.Connection 实例。

        Raises:
            RuntimeError: 尚未成功调用 load()。
        """
        if self._conn is None:
            raise RuntimeError("MetadataStore 未 load，请先调用 load()")
        return self._conn

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_all_entries(self) -> dict[int, MemeEntry]:
        """返回全库条目浅拷贝。

        Returns:
            内部 id 到 frozen MemeEntry 的映射。
        """
        return self.get_entries()

    def get_entries(self, collection_id: int | None = None) -> dict[int, MemeEntry]:
        """返回全库或指定合集条目的浅拷贝。

        Args:
            collection_id: 合集编号；None 表示全库。

        Returns:
            内部 id 到 frozen MemeEntry 的映射。
        """
        with self._lock:
            if collection_id is None:
                selected_entries = self._entries.items()
            else:
                internal_ids = self._entries_by_collection.get(
                    collection_id, {}
                ).values()
                selected_entries = (
                    (entry_id, self._entries[entry_id]) for entry_id in internal_ids
                )
            entries = {
                entry_id: self._snapshot_entry(entry)
                for entry_id, entry in selected_entries
            }
        logger.debug("返回 %d 条元数据, collection_id=%s", len(entries), collection_id)
        return entries

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按内部 id 查询单条记录。

        Args:
            entry_id: 内部索引 id。

        Returns:
            缓存内 MemeEntry；不存在时返回 None。
        """
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                logger.debug("未找到元数据: %d", entry_id)
                return None
            return self._snapshot_entry(entry)

    def get_entry_by_public_id(self, public_id: MemePublicId) -> MemeEntry | None:
        """按用户可见复合 ID 查询条目。

        Args:
            public_id: 合集编号与合集内编号。

        Returns:
            缓存内 MemeEntry；不存在时返回 None。
        """
        with self._lock:
            entry_id = self._entries_by_collection.get(public_id.collection_id, {}).get(
                public_id.local_id
            )
            if entry_id is None:
                return None
            entry = self._entries.get(entry_id)
            return self._snapshot_entry(entry) if entry is not None else None

    def get_by_filename(self, image_path: str) -> MemeEntry | None:
        """按图片路径查询。

        Args:
            image_path: memes/ 下相对路径。

        Returns:
            对应 MemeEntry；路径不存在时返回 None。
        """
        with self._lock:
            row = (
                self._require_conn()
                .execute(
                    "SELECT id FROM meme WHERE image_path = ?",
                    (image_path,),
                )
                .fetchone()
            )
            if row is None:
                logger.debug("未找到元数据: %s", image_path)
                return None
            entry = self._entries.get(int(row["id"]))
            return self._snapshot_entry(entry) if entry is not None else None

    def get_id_by_text(self, text: str, *, collection_id: int = 0) -> int | None:
        """按合集和 text 查询内部 id。

        Args:
            text: 按中文逗号拼接的 OCR 文本。
            collection_id: 合集编号，默认全局合集。

        Returns:
            对应内部 id；不存在时返回 None。
        """
        with self._lock:
            return self._text_to_id.get((collection_id, text))

    def entry_count(self) -> int:
        """返回全库条目总数。

        Returns:
            缓存中的条目数。
        """
        with self._lock:
            return len(self._entries)

    def list_collections(self) -> list[MemeCollection]:
        """按编号升序返回全部普通合集。

        Returns:
            不与缓存共享的合集列表。
        """
        with self._lock:
            return [
                self._id_to_collections[key] for key in sorted(self._id_to_collections)
            ]

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        """按编号返回普通合集。

        Args:
            collection_id: 普通合集编号。

        Returns:
            对应合集；不存在或传入 0 时返回 None。
        """
        with self._lock:
            return self._id_to_collections.get(collection_id)

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        """按区分大小写的精确名称返回普通合集。

        Args:
            name: 合集名称。

        Returns:
            对应合集；不存在时返回 None。
        """
        with self._lock:
            collection_id = self._collection_name_to_id.get(name)
            if collection_id is None:
                return None
            return self._id_to_collections.get(collection_id)

    def collection_entry_count(self, collection_id: int | None) -> int:
        """返回全库或指定合集的条目数。

        Args:
            collection_id: 合集编号；None 表示全库。

        Returns:
            缓存中的条目数；未知合集返回 0。
        """
        with self._lock:
            if collection_id is None:
                return len(self._entries)
            return len(self._entries_by_collection.get(collection_id, {}))

    def find_next_local_id(self, collection_id: int) -> int:
        """返回指定合集内最小可用的正整数局部编号。

        Args:
            collection_id: 目标合集编号；0 表示全局合集。

        Returns:
            可分配的最小局部编号。

        Raises:
            ValueError: 普通合集不存在。
        """
        with self._lock:
            self._validate_collection_id(collection_id)
            return self._find_next_local_id(self._require_conn(), collection_id)

    def get_selected_collection(self, scope: ChatScope) -> int:
        """返回 ChatScope 当前选择的合集编号。

        Args:
            scope: 聊天作用域。

        Returns:
            已持久化的合集编号；未设置时返回 0。

        Raises:
            ValueError: 聊天类型非法。
        """
        self._validate_chat_type(scope.chat_type)
        with self._lock:
            return self._selected_collections.get(scope, 0)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def create_collection(
        self, name: str, *, collection_id: int | None = None
    ) -> MemeCollection:
        """创建普通合集并在提交成功后更新缓存。

        未指定编号时复用最小正整数空洞。

        Args:
            name: 合集名称。
            collection_id: 显式合集编号；None 时自动分配。

        Returns:
            新建的 MemeCollection。

        Raises:
            ValueError: 显式编号不是正整数。
            sqlite3.IntegrityError: 编号或名称已存在。
        """
        with self._lock:
            conn = self._require_conn()
            if collection_id is None:
                row = conn.execute(_FIND_NEXT_COLLECTION_ID_SQL).fetchone()
                final_collection_id = int(row["next_id"])
            else:
                final_collection_id = collection_id
            if final_collection_id <= 0:
                raise ValueError("collection_id 必须为正整数")

            try:
                cursor = conn.execute(
                    "INSERT INTO meme_collection (id, name) VALUES (?, ?)",
                    (final_collection_id, name),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError("创建合集必须影响 1 行")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            collection = MemeCollection(final_collection_id, name)
            self._id_to_collections[final_collection_id] = collection
            self._collection_name_to_id[name] = final_collection_id
            self._entries_by_collection[final_collection_id] = {}
            return collection

    def set_selected_collection(self, scope: ChatScope, collection_id: int) -> None:
        """保存 ChatScope 当前选择的合集编号。

        Args:
            scope: 聊天作用域。
            collection_id: 选择的合集编号；0 表示全部合集。

        Raises:
            ValueError: 聊天类型非法、合集编号为负或普通合集不存在。
        """
        self._validate_chat_type(scope.chat_type)
        with self._lock:
            conn = self._require_conn()
            self._validate_collection_id(collection_id)
            try:
                cursor = conn.execute(
                    "INSERT INTO chat_collection_scope "
                    "(user_id, chat_type, chat_id, selected_collection_id) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, chat_type, chat_id) DO UPDATE SET "
                    "selected_collection_id = excluded.selected_collection_id",
                    (scope.user_id, scope.chat_type, scope.chat_id, collection_id),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError("保存 ChatScope 必须影响 1 行")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            self._selected_collections[scope] = collection_id

    def delete_collection_and_reset_scopes(self, collection_id: int) -> int:
        """删除空合集并原子回退所有引用它的 ChatScope。

        Args:
            collection_id: 要删除的普通合集编号。

        Returns:
            实际回退到全部合集的 ChatScope 行数。

        Raises:
            ValueError: 编号为 0、普通合集不存在或合集仍含表情包。
        """
        with self._lock:
            conn = self._require_conn()
            if collection_id == 0:
                raise ValueError("不能删除全局合集")
            collection = self._id_to_collections.get(collection_id)
            if collection is None:
                raise ValueError(f"collection_id={collection_id} 不存在")
            if self._entries_by_collection.get(collection_id):
                raise ValueError(f"合集 {collection_id} 仍含表情包，不能删除")

            try:
                conn.execute("BEGIN IMMEDIATE")
                entry_row = conn.execute(
                    "SELECT 1 FROM meme WHERE collection_id = ? LIMIT 1",
                    (collection_id,),
                ).fetchone()
                if entry_row is not None:
                    raise ValueError(f"合集 {collection_id} 仍含表情包，不能删除")
                scope_cursor = conn.execute(
                    "UPDATE chat_collection_scope SET selected_collection_id = 0 "
                    "WHERE selected_collection_id = ?",
                    (collection_id,),
                )
                if scope_cursor.rowcount < 0:
                    raise sqlite3.DatabaseError("无法确定 ChatScope 回退影响行数")
                reset_count = scope_cursor.rowcount
                delete_cursor = conn.execute(
                    "DELETE FROM meme_collection WHERE id = ?",
                    (collection_id,),
                )
                if delete_cursor.rowcount != 1:
                    raise sqlite3.DatabaseError("删除合集必须影响 1 行")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            del self._id_to_collections[collection_id]
            self._collection_name_to_id.pop(collection.name, None)
            self._entries_by_collection.pop(collection_id, None)
            for key, selected_collection_id in list(self._selected_collections.items()):
                if selected_collection_id == collection_id:
                    self._selected_collections[key] = 0
            return reset_count

    def rename_collection(self, collection_id: int, new_name: str) -> MemeCollection:
        """重命名普通合集，并同步更新该合集所有条目的 image_path 首段。

        在单个 SQLite 事务内修改 ``meme_collection.name`` 与该合集每条
        ``meme.image_path`` 的首段（目录名）；提交成功后再更新内存缓存。
        任何异常回滚事务且缓存不动。

        Args:
            collection_id: 要重命名的普通合集编号；必须为正整数。
            new_name: 已通过领域校验的新合集名称。

        Returns:
            重命名后的合集快照（新名、同编号）。

        Raises:
            ValueError: 编号非正整数或合集不存在。
            sqlite3.IntegrityError: 新名称已被其他合集使用（DB UNIQUE 兜底）。
        """
        with self._lock:
            conn = self._require_conn()
            if collection_id <= 0:
                raise ValueError("collection_id 必须为正整数")
            collection = self._id_to_collections.get(collection_id)
            if collection is None:
                raise ValueError(f"collection_id={collection_id} 不存在")
            old_name = collection.name
            if new_name == old_name:
                return collection

            # 收集需要改 image_path 首段的条目（按 entry_id 升序，稳定）
            entry_ids = sorted(
                self._entries_by_collection.get(collection_id, {}).values()
            )
            updates: list[tuple[int, str]] = []
            for eid in entry_ids:
                entry = self._entries[eid]
                head, sep, rest = entry.image_path.partition("/")
                if not sep:
                    # 普通合集条目 image_path 必以 <合集名>/ 开头；兜底跳过
                    continue
                updates.append((eid, f"{new_name}/{rest}"))

            try:
                cursor = conn.execute(
                    "UPDATE meme_collection SET name = ? WHERE id = ?",
                    (new_name, collection_id),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.DatabaseError("重命名合集必须影响 1 行")
                for eid, new_path in updates:
                    conn.execute(
                        "UPDATE meme SET image_path = ? WHERE id = ?",
                        (new_path, eid),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            # 提交成功后更新缓存：先合集名，再重建 frozen 条目
            renamed = MemeCollection(collection_id, new_name)
            self._id_to_collections[collection_id] = renamed
            self._collection_name_to_id.pop(old_name, None)
            self._collection_name_to_id[new_name] = collection_id
            for eid, new_path in updates:
                old_entry = self._entries[eid]
                self._entries[eid] = replace(
                    old_entry,
                    image_path=new_path,
                    collection_name=new_name,
                )
            return renamed

    @timed(logger, "MetadataStore 添加")
    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> int:
        """在指定合集新增条目并分配最小内部 id 与局部 id。

        Args:
            image_path: memes/ 下相对路径。
            text: 按中文逗号拼接的 OCR 文本。
            speaker: 说话人，可空。
            tags: 标记词列表，可空。
            collection_id: 目标合集编号，默认全局合集。

        Returns:
            分配的内部 id。

        Raises:
            ValueError: 普通合集不存在。
            DuplicateEntryError: 图片路径全库冲突或文本在目标合集内冲突。
        """
        with self._lock:
            conn = self._require_conn()
            self._validate_collection_id(collection_id)
            entry_id = int(conn.execute(_FIND_NEXT_ID_SQL).fetchone()["next_id"])
            local_id = self._find_next_local_id(conn, collection_id)
            try:
                conn.execute(
                    "INSERT INTO meme "
                    "(id, collection_id, local_id, image_path, text, speaker) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (entry_id, collection_id, local_id, image_path, text, speaker),
                )
                self._write_tags(entry_id, tags)
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                conflicts = self._detect_conflicts(
                    image_path=image_path,
                    text=text,
                    entry_id=entry_id,
                    collection_id=collection_id,
                    local_id=local_id,
                )
                if not conflicts:
                    raise
                raise DuplicateEntryError(conflicts) from exc
            except Exception:
                conn.rollback()
                raise

            entry = self._make_entry(
                entry_id=entry_id,
                collection_id=collection_id,
                local_id=local_id,
                image_path=image_path,
                text=text,
                speaker=speaker,
                tags=tags,
            )
            self._cache_entry(entry)
        logger.debug("添加元数据: id=%d, image_path=%s", entry_id, image_path)
        return entry_id

    def add_with_id(
        self,
        entry_id: int,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
        local_id: int | None = None,
    ) -> int:
        """用指定内部 id 写入条目。

        旧调用未传合集参数时写入全局合集，局部 id 默认沿用内部 id，以保持历史编号。

        Args:
            entry_id: 指定的内部 id。
            image_path: memes/ 下相对路径。
            text: 按中文逗号拼接的 OCR 文本。
            speaker: 说话人，可空。
            tags: 标记词列表，可空。
            collection_id: 目标合集编号，默认全局合集。
            local_id: 指定局部 id；None 时沿用 entry_id。

        Returns:
            传入的内部 id。

        Raises:
            ValueError: 普通合集不存在或局部 id 不是正整数。
            DuplicateEntryError: 任一唯一约束冲突。
        """
        if entry_id <= 0:
            raise ValueError("entry_id 必须为正整数")
        final_local_id = entry_id if local_id is None else local_id
        if final_local_id <= 0:
            raise ValueError("local_id 必须为正整数")

        with self._lock:
            conn = self._require_conn()
            self._validate_collection_id(collection_id)
            try:
                conn.execute(
                    "INSERT INTO meme "
                    "(id, collection_id, local_id, image_path, text, speaker) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        entry_id,
                        collection_id,
                        final_local_id,
                        image_path,
                        text,
                        speaker,
                    ),
                )
                self._write_tags(entry_id, tags)
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                conflicts = self._detect_conflicts(
                    image_path=image_path,
                    text=text,
                    entry_id=entry_id,
                    collection_id=collection_id,
                    local_id=final_local_id,
                )
                if not conflicts:
                    raise
                raise DuplicateEntryError(conflicts) from exc
            except Exception:
                conn.rollback()
                raise

            entry = self._make_entry(
                entry_id=entry_id,
                collection_id=collection_id,
                local_id=final_local_id,
                image_path=image_path,
                text=text,
                speaker=speaker,
                tags=tags,
            )
            self._cache_entry(entry)
        logger.debug("添加元数据: id=%d, image_path=%s", entry_id, image_path)
        return entry_id

    @timed(logger, "MetadataStore 更新")
    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = _UNSET,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        text: str | None = _UNSET,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        speaker: str | None = _UNSET,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        tags: Sequence[str] | None = None,
        collection_id: int | None = _UNSET,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        local_id: int | None = _UNSET,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
    ) -> bool:
        """更新单条记录的可选字段并同步联合缓存。

        Args:
            entry_id: 要更新的内部 id。
            image_path: 新图片路径；_UNSET 表示不变。
            text: 新 OCR 文本；_UNSET 表示不变。
            speaker: 新说话人；_UNSET 表示不变，None 表示清空。
            tags: 新标签列表；None 表示不变。
            collection_id: 新合集编号；_UNSET 表示不变。
            local_id: 新合集内编号；_UNSET 表示不变。

        Returns:
            找到并更新时返回 True，不存在时返回 False。

        Raises:
            ValueError: 目标合集不存在，或合集编号、局部编号无效。
            DuplicateEntryError: 最终图片路径、局部编号或合集内文本冲突。
        """
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT collection_id, local_id, image_path, text, speaker "
                "FROM meme WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                logger.debug("未找到元数据: %d", entry_id)
                return False

            old_collection_id = int(row["collection_id"])
            old_local_id = int(row["local_id"])
            old_text = str(row["text"])

            if image_path is None:
                raise ValueError("image_path 不能为 None")
            if text is None:
                raise ValueError("text 不能为 None")
            if collection_id is _UNSET:
                final_collection_id = old_collection_id
            elif collection_id is None:
                raise ValueError("collection_id 不能为 None")
            else:
                final_collection_id = collection_id
            self._validate_collection_id(final_collection_id)

            if local_id is _UNSET:
                final_local_id = old_local_id
            elif local_id is None or local_id <= 0:
                raise ValueError("local_id 必须为正整数")
            else:
                final_local_id = local_id

            final_text = old_text if text is _UNSET else text
            sets: list[str] = []
            params: list[object] = []
            if image_path is not _UNSET:
                sets.append("image_path = ?")
                params.append(image_path)
            if text is not _UNSET:
                sets.append("text = ?")
                params.append(text)
            if speaker is not _UNSET:
                sets.append("speaker = ?")
                params.append(speaker)
            if collection_id is not _UNSET:
                sets.append("collection_id = ?")
                params.append(final_collection_id)
            if local_id is not _UNSET:
                sets.append("local_id = ?")
                params.append(final_local_id)

            try:
                if sets:
                    params.append(entry_id)
                    conn.execute(
                        f"UPDATE meme SET {', '.join(sets)} WHERE id = ?",
                        params,
                    )
                if tags is not None:
                    conn.execute("DELETE FROM meme_tag WHERE meme_id = ?", (entry_id,))
                    self._write_tags(entry_id, tags)
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                conflicts = self._detect_conflicts(
                    image_path=(image_path if image_path is not _UNSET else None),
                    text=(
                        final_text
                        if text is not _UNSET or collection_id is not _UNSET
                        else None
                    ),
                    collection_id=final_collection_id,
                    local_id=(
                        final_local_id
                        if local_id is not _UNSET or collection_id is not _UNSET
                        else None
                    ),
                    exclude_id=entry_id,
                )
                if not conflicts:
                    raise
                raise DuplicateEntryError(conflicts) from exc
            except Exception:
                conn.rollback()
                raise

            old_entry = self._entries[entry_id]
            self._uncache_entry(old_entry)
            updated_entry = replace(
                old_entry,
                image_path=(
                    image_path if image_path is not _UNSET else old_entry.image_path
                ),
                text=final_text,
                speaker=speaker if speaker is not _UNSET else old_entry.speaker,
                tags=tuple(sorted(set(tags))) if tags is not None else old_entry.tags,
                collection_id=final_collection_id,
                local_id=final_local_id,
                collection_name=self._collection_name(final_collection_id),
            )
            self._cache_entry(updated_entry)

        logger.debug("更新元数据: %d", entry_id)
        return True

    @timed(logger, "MetadataStore 删除")
    def remove(self, entry_id: int) -> bool:
        """删除单条记录并清理全部条目缓存。

        Args:
            entry_id: 要删除的内部 id。

        Returns:
            删除成功时返回 True，不存在时返回 False。
        """
        with self._lock:
            conn = self._require_conn()
            entry = self._entries.get(entry_id)
            if entry is None:
                logger.debug("未找到元数据: %d", entry_id)
                return False
            try:
                conn.execute("DELETE FROM meme WHERE id = ?", (entry_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            self._uncache_entry(entry)

        logger.debug("删除元数据: %d", entry_id)
        return True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_entry(entry: MemeEntry) -> MemeEntry:
        """返回缓存条目供公开读取。

        MemeEntry 为 frozen + 全字段不可变（tags 为 tuple），无需复制即可
        安全共享引用，零拷贝。

        Args:
            entry: 缓存内条目。

        Returns:
            缓存内同一对象引用。
        """
        return entry

    def _clear_caches(self) -> None:
        """清空全部内存缓存，调用方已持锁。"""
        self._text_to_id = {}
        self._entries = {}
        self._entries_by_collection = {}
        self._id_to_collections = {}
        self._collection_name_to_id = {}
        self._selected_collections = {}

    @staticmethod
    def _validate_schema_structure(
        conn: sqlite3.Connection, table_names: set[str]
    ) -> None:
        """校验当前版本运行所需的最小数据库结构。

        Args:
            conn: 已通过版本校验的 SQLite 连接。
            table_names: 当前数据库中的表名集合。

        Raises:
            SchemaVersionError: 必需表、列、索引、外键或主键缺失。
        """
        required_tables = {
            "schema_version",
            "meme_collection",
            "meme",
            "meme_tag",
            "chat_collection_scope",
        }
        if not required_tables.issubset(table_names):
            missing = sorted(required_tables - table_names)
            raise SchemaVersionError(f"当前 Schema 结构缺少表: {missing!r}")

        required_columns = {
            "meme_collection": {"id", "name"},
            "meme": {
                "id",
                "collection_id",
                "local_id",
                "image_path",
                "text",
                "speaker",
            },
            "meme_tag": {"meme_id", "tag"},
            "chat_collection_scope": {
                "user_id",
                "chat_type",
                "chat_id",
                "selected_collection_id",
            },
        }
        table_info: dict[str, list[sqlite3.Row]] = {}
        for table_name, expected_columns in required_columns.items():
            column_rows = list(conn.execute(f"PRAGMA table_info({table_name})"))
            table_info[table_name] = column_rows
            actual_columns = {str(row["name"]) for row in column_rows}
            if not expected_columns.issubset(actual_columns):
                missing = sorted(expected_columns - actual_columns)
                raise SchemaVersionError(
                    f"当前 Schema 结构的 {table_name} 缺少列: {missing!r}"
                )

        required_primary_keys = {
            "meme": ["id"],
            "meme_collection": ["id"],
            "meme_tag": ["meme_id", "tag"],
            "chat_collection_scope": ["user_id", "chat_type", "chat_id"],
        }
        for table_name, expected_columns in required_primary_keys.items():
            actual_columns = [
                str(row["name"])
                for row in sorted(
                    table_info[table_name],
                    key=lambda row: int(row["pk"]) if row["pk"] else 999,
                )
                if row["pk"]
            ]
            if actual_columns != expected_columns:
                raise SchemaVersionError(f"当前 Schema 结构的 {table_name} 主键错误")

        collection_name_unique = False
        for index_row in conn.execute("PRAGMA index_list(meme_collection)"):
            if not index_row["unique"] or index_row["partial"]:
                continue
            index_name = str(index_row["name"])
            escaped_name = index_name.replace('"', '""')
            index_columns = [
                str(row["name"])
                for row in conn.execute(f'PRAGMA index_info("{escaped_name}")')
            ]
            if index_columns == ["name"]:
                collection_name_unique = True
                break
        if not collection_name_unique:
            raise SchemaVersionError(
                "当前 Schema 结构的 meme_collection.name 缺少唯一约束"
            )

        required_indexes = {
            "idx_meme_image_path": ("meme", True, ["image_path"]),
            "idx_meme_collection_local": (
                "meme",
                True,
                ["collection_id", "local_id"],
            ),
            "idx_meme_collection_text": (
                "meme",
                True,
                ["collection_id", "text"],
            ),
            "idx_meme_tag_tag": ("meme_tag", False, ["tag"]),
        }
        for index_name, (
            table_name,
            unique,
            expected_columns,
        ) in required_indexes.items():
            index_rows = {
                str(row["name"]): row
                for row in conn.execute(f"PRAGMA index_list({table_name})")
            }
            index_row = index_rows.get(index_name)
            if (
                index_row is None
                or bool(index_row["unique"]) is not unique
                or bool(index_row["partial"])
            ):
                raise SchemaVersionError(
                    f"当前 Schema 结构缺少索引或唯一性错误: {index_name}"
                )
            actual_columns = [
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_info({index_name})")
            ]
            if actual_columns != expected_columns:
                raise SchemaVersionError(f"当前 Schema 结构的索引列错误: {index_name}")

        tag_foreign_keys = list(conn.execute("PRAGMA foreign_key_list(meme_tag)"))
        if not any(
            row["table"] == "meme"
            and row["from"] == "meme_id"
            and row["to"] == "id"
            and row["on_delete"] == "CASCADE"
            for row in tag_foreign_keys
        ):
            raise SchemaVersionError("当前 Schema 结构缺少 meme_tag 级联外键")

    def _rebuild_caches(self, conn: sqlite3.Connection) -> None:
        """从当前 Schema 重建所有内存缓存。

        Args:
            conn: 已校验版本且由调用方持锁的连接。

        Raises:
            sqlite3.DatabaseError: 普通条目引用不存在的合集。
        """
        collections: dict[int, MemeCollection] = {}
        collection_name_to_id: dict[str, int] = {}
        for row in conn.execute("SELECT id, name FROM meme_collection ORDER BY id"):
            collection_id = int(row["id"])
            collection_name = str(row["name"])
            if collection_id <= 0:
                raise sqlite3.DatabaseError(
                    f"普通合集 ID 必须为正整数: {collection_id}"
                )
            if collection_id in collections:
                raise sqlite3.DatabaseError(f"存在重复合集 ID: {collection_id}")
            if collection_name in collection_name_to_id:
                raise sqlite3.DatabaseError(f"存在重复合集名称: {collection_name!r}")
            collections[collection_id] = MemeCollection(collection_id, collection_name)
            collection_name_to_id[collection_name] = collection_id

        rows = list(
            conn.execute(
                "SELECT m.id, m.collection_id, m.local_id, m.image_path, "
                "m.text, m.speaker, c.name AS collection_name "
                "FROM meme AS m "
                "LEFT JOIN meme_collection AS c ON c.id = m.collection_id "
                "ORDER BY m.id"
            )
        )
        tag_rows = list(
            conn.execute("SELECT meme_id, tag FROM meme_tag ORDER BY meme_id, tag")
        )

        entries: dict[int, MemeEntry] = {}
        entries_by_collection: dict[int, dict[int, int]] = {
            0: {},
            **{collection_id: {} for collection_id in collections},
        }
        text_to_id: dict[tuple[int, str], int] = {}
        for row in rows:
            entry_id = int(row["id"])
            collection_id = int(row["collection_id"])
            local_id = int(row["local_id"])
            text = str(row["text"])
            if entry_id <= 0:
                raise sqlite3.DatabaseError(f"表情包内部 ID 必须为正整数: {entry_id}")
            if entry_id in entries:
                raise sqlite3.DatabaseError(f"存在重复内部 ID: {entry_id}")
            if collection_id < 0:
                raise sqlite3.DatabaseError(
                    f"表情包 collection_id 不能为负数: {collection_id}"
                )
            if local_id <= 0:
                raise sqlite3.DatabaseError(f"表情包 local_id 必须为正整数: {local_id}")
            collection_entries = entries_by_collection.setdefault(collection_id, {})
            if local_id in collection_entries:
                raise sqlite3.DatabaseError(
                    f"合集 {collection_id} 存在重复 local_id={local_id}"
                )
            if (collection_id, text) in text_to_id:
                raise sqlite3.DatabaseError(
                    f"合集 {collection_id} 存在重复 text={text!r}"
                )
            raw_collection_name = row["collection_name"]
            if collection_id == 0:
                collection_name = GLOBAL_COLLECTION_NAME
            elif raw_collection_name is None:
                raise sqlite3.DatabaseError(
                    f"表情包 id={entry_id} 引用了不存在的合集 {collection_id}"
                )
            else:
                collection_name = str(raw_collection_name)
            entry = MemeEntry(
                id=entry_id,
                image_path=str(row["image_path"]),
                text=text,
                speaker=(str(row["speaker"]) if row["speaker"] is not None else None),
                tags=(),
                collection_id=collection_id,
                local_id=local_id,
                collection_name=collection_name,
            )
            entries[entry_id] = entry
            collection_entries[local_id] = entry_id
            text_to_id[(collection_id, text)] = entry_id

        # 累积标签到 tags_by_meme（frozen MemeEntry 的 tags 为不可变 tuple，
        # 不能原地 append；先聚合，再用 replace 给对应 entry 补充 tags）。
        tags_by_meme: dict[int, list[str]] = {}
        seen_tags: set[tuple[int, str]] = set()
        for row in tag_rows:
            meme_id = int(row["meme_id"])
            tag = str(row["tag"])
            tag_key = (meme_id, tag)
            if tag_key in seen_tags:
                raise sqlite3.DatabaseError(f"表情包 {meme_id} 存在重复标签: {tag!r}")
            entry = entries.get(meme_id)
            if entry is None:
                raise sqlite3.DatabaseError(
                    f"标签 {tag!r} 指向不存在的表情包 {meme_id}"
                )
            seen_tags.add(tag_key)
            tags_by_meme.setdefault(meme_id, []).append(tag)

        # 给有标签的 entry 补充 tags（frozen 不可变，需 replace 重建）
        for meme_id, tag_list in tags_by_meme.items():
            entries[meme_id] = replace(entries[meme_id], tags=tuple(tag_list))

        selected_collections: dict[ChatScope, int] = {}
        for row in conn.execute(
            "SELECT user_id, chat_type, chat_id, selected_collection_id "
            "FROM chat_collection_scope"
        ):
            user_id = row["user_id"]
            chat_type = row["chat_type"]
            chat_id = row["chat_id"]
            selected_collection_id = row["selected_collection_id"]
            for column, value in (
                ("user_id", user_id),
                ("chat_id", chat_id),
                ("selected_collection_id", selected_collection_id),
            ):
                if type(value) is not int:
                    raise sqlite3.DatabaseError(
                        f"ChatScope {column} 必须为整数: {value!r}"
                    )
            if type(chat_type) is not str or chat_type not in {"private", "group"}:
                raise sqlite3.DatabaseError(f"ChatScope chat_type 非法: {chat_type!r}")
            key = ChatScope(user_id=user_id, chat_type=chat_type, chat_id=chat_id)
            if selected_collection_id < 0:
                raise sqlite3.DatabaseError(
                    "ChatScope selected_collection_id 不能为负数: "
                    f"{selected_collection_id}"
                )
            if (
                selected_collection_id != 0
                and selected_collection_id not in collections
            ):
                raise sqlite3.DatabaseError(
                    f"ChatScope 指向不存在的合集 {selected_collection_id}"
                )
            if key in selected_collections:
                raise sqlite3.DatabaseError(f"存在重复 ChatScope key: {key!r}")
            selected_collections[key] = selected_collection_id

        self._id_to_collections = collections
        self._collection_name_to_id = collection_name_to_id
        self._entries = entries
        self._entries_by_collection = entries_by_collection
        self._text_to_id = text_to_id
        self._selected_collections = selected_collections

    @staticmethod
    def _validate_chat_type(chat_type: str) -> None:
        """校验聊天类型。

        Args:
            chat_type: 待校验的聊天类型。

        Raises:
            ValueError: 聊天类型不是 ``private`` 或 ``group``。
        """
        if chat_type not in {"private", "group"}:
            raise ValueError("chat_type 必须为 private 或 group")

    def _validate_collection_id(self, collection_id: int) -> None:
        """校验条目目标合集存在。

        Args:
            collection_id: 目标合集编号。

        Raises:
            ValueError: 编号为负数或普通合集不存在。
        """
        if collection_id < 0 or (
            collection_id != 0 and collection_id not in self._id_to_collections
        ):
            raise ValueError(f"collection_id={collection_id} 不存在")

    @staticmethod
    def _find_next_local_id(conn: sqlite3.Connection, collection_id: int) -> int:
        """查找指定合集内的最小正整数空洞。

        Args:
            conn: 当前 SQLite 连接。
            collection_id: 目标合集编号。

        Returns:
            可分配的局部 id。
        """
        row = conn.execute(
            _FIND_NEXT_LOCAL_ID_SQL,
            (collection_id, collection_id),
        ).fetchone()
        return int(row["next_id"])

    def _collection_name(self, collection_id: int) -> str:
        """返回合集显示名称。

        Args:
            collection_id: 已校验的合集编号。

        Returns:
            全局或普通合集名称。
        """
        if collection_id == 0:
            return GLOBAL_COLLECTION_NAME
        return self._id_to_collections[collection_id].name

    def _make_entry(
        self,
        *,
        entry_id: int,
        collection_id: int,
        local_id: int,
        image_path: str,
        text: str,
        speaker: str | None,
        tags: Sequence[str] | None,
    ) -> MemeEntry:
        """构造与数据库写入一致的缓存条目。

        Args:
            entry_id: 内部 id。
            collection_id: 合集编号。
            local_id: 合集内编号。
            image_path: 图片相对路径。
            text: OCR 文本。
            speaker: 说话人。
            tags: 原始标签列表。

        Returns:
            规范化标签后的 MemeEntry。
        """
        return MemeEntry(
            id=entry_id,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=tuple(sorted(set(tags or []))),
            collection_id=collection_id,
            local_id=local_id,
            collection_name=self._collection_name(collection_id),
        )

    def _cache_entry(self, entry: MemeEntry) -> None:
        """把条目写入全部相关缓存。

        Args:
            entry: 要写入的条目。
        """
        self._entries[entry.id] = entry
        self._entries_by_collection.setdefault(entry.collection_id, {})[
            entry.local_id
        ] = entry.id
        self._text_to_id[(entry.collection_id, entry.text)] = entry.id

    def _uncache_entry(self, entry: MemeEntry) -> None:
        """从全部相关缓存移除条目。

        Args:
            entry: 要移除的条目快照。
        """
        self._entries.pop(entry.id, None)
        collection_entries = self._entries_by_collection.get(entry.collection_id)
        if collection_entries is not None:
            if collection_entries.get(entry.local_id) == entry.id:
                del collection_entries[entry.local_id]
        text_key = (entry.collection_id, entry.text)
        if self._text_to_id.get(text_key) == entry.id:
            del self._text_to_id[text_key]

    def _write_tags(self, entry_id: int, tags: Sequence[str] | None) -> None:
        """写入 tag 行，调用方已持锁。

        Args:
            entry_id: 所属 meme 内部 id。
            tags: 标记词列表；为空时不写入。
        """
        if not tags:
            return
        self._require_conn().executemany(
            "INSERT OR IGNORE INTO meme_tag (meme_id, tag) VALUES (?, ?)",
            [(entry_id, tag) for tag in tags],
        )

    def _detect_conflicts(
        self,
        *,
        image_path: str | None = None,
        text: str | None = None,
        entry_id: int | None = None,
        collection_id: int = 0,
        local_id: int | None = None,
        exclude_id: int | None = None,
    ) -> list[tuple[str, str]]:
        """探测所有命中的唯一或主键冲突字段。

        Args:
            image_path: 待探测的全库图片路径。
            text: 待探测的合集内文本。
            entry_id: 待探测的内部 id。
            collection_id: 最终合集编号。
            local_id: 待探测的合集内编号。
            exclude_id: update 时排除自身内部 id。

        Returns:
            命中的 ``(column, value)`` 列表，顺序为 id、image_path、local_id、text。
        """
        conn = self._require_conn()
        conflicts: list[tuple[str, str]] = []

        if (
            entry_id is not None
            and conn.execute("SELECT 1 FROM meme WHERE id = ?", (entry_id,)).fetchone()
        ):
            conflicts.append(("id", str(entry_id)))

        if image_path is not None:
            image_params: list[object] = [image_path]
            image_sql = "SELECT 1 FROM meme WHERE image_path = ?"
            if exclude_id is not None:
                image_sql += " AND id != ?"
                image_params.append(exclude_id)
            if conn.execute(image_sql, image_params).fetchone():
                conflicts.append(("image_path", image_path))

        if local_id is not None:
            local_params: list[object] = [collection_id, local_id]
            local_sql = "SELECT 1 FROM meme WHERE collection_id = ? AND local_id = ?"
            if exclude_id is not None:
                local_sql += " AND id != ?"
                local_params.append(exclude_id)
            if conn.execute(local_sql, local_params).fetchone():
                conflicts.append(("local_id", str(local_id)))

        if text is not None:
            text_params: list[object] = [collection_id, text]
            text_sql = "SELECT 1 FROM meme WHERE collection_id = ? AND text = ?"
            if exclude_id is not None:
                text_sql += " AND id != ?"
                text_params.append(exclude_id)
            if conn.execute(text_sql, text_params).fetchone():
                conflicts.append(("text", text))

        return conflicts
