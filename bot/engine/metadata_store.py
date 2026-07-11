"""元数据存储模块 — 基于 sqlite3。

存储每条表情包的 id、图片路径（memes/ 下相对路径）、OCR 文字（去除所有空白）、
说话人、标记词。id 与 VectorStore 的向量 id 完全一一对应。

设计要点：
- sqlite3 标准库，INTEGER PRIMARY KEY 手动分配（复用最小空洞，不用 AUTOINCREMENT）。
- meme_tag 关联表存多值标记词，ON DELETE CASCADE 随 meme 行删除。
- check_same_thread=False + 内部 threading.Lock 串行化所有 sqlite 访问；
  公开方法为同步，调用方用 asyncio.to_thread 包装以避免阻塞事件循环。
- _text_to_id 内存反向索引（text→id），load 时全量重建，增删同步维护，加速去重判定。
- text 与 image_path 均有 UNIQUE INDEX 约束：DB 层兜底保证唯一性，
  IndexManager 仍在 add 前用 get_id_by_text 做去重检查；冲突时抛 DuplicateEntryError。
  _text_to_id 为单值映射，与 DB 唯一性保持一致。
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass, field, replace

from bot.log_context import timed

logger = logging.getLogger(__name__)

_UNSET = object()  # 哨兵值，区分「不修改字段」与显式的 None

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meme ("
    "    id INTEGER PRIMARY KEY,"
    "    image_path TEXT NOT NULL,"
    "    text TEXT NOT NULL,"
    "    speaker TEXT"
    ");",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_image_path ON meme(image_path);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_text ON meme(text);",
    "CREATE TABLE IF NOT EXISTS meme_tag ("
    "    meme_id INTEGER NOT NULL,"
    "    tag TEXT NOT NULL,"
    "    PRIMARY KEY (meme_id, tag),"
    "    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_meme_tag_tag ON meme_tag(tag);",
)

_FIND_NEXT_ID_SQL = (
    "SELECT MIN(t.id) + 1 AS next_id "
    "FROM (SELECT 0 AS id UNION ALL SELECT id FROM meme) t "
    "WHERE NOT EXISTS(SELECT 1 FROM meme t2 WHERE t2.id = t.id + 1)"
)


class DuplicateEntryError(sqlite3.IntegrityError):
    """写入/更新时触发 UNIQUE/PRIMARY KEY 约束冲突。

    Attributes:
        conflicts: 所有命中的冲突字段列表，每项为 (column, value)；
                   例如 [("text", "加班心累"), ("image_path", "cat.jpg")]。
                   顺序固定为 id → image_path → text。
    """

    def __init__(self, conflicts: list[tuple[str, str]]) -> None:
        self.conflicts = conflicts
        detail = ", ".join(f"{col}={val!r}" for col, val in conflicts)
        super().__init__(f"重复字段冲突: {detail}")


@dataclass(frozen=True, slots=True)
class MemeEntry:
    """单条表情包元数据。

    Attributes:
        id: 索引 id，与 VectorStore 向量 id 一一对应。
        image_path: memes/ 目录下相对路径（扁平结构下即文件名）。
        text: OCR 去除所有空白后的文本（无空格）。
        speaker: 说话人，可空（本次不填充）。
        tags: 标记词列表，从 meme_tag 组装（本次为空 []）。
    """

    id: int
    image_path: str
    text: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)


class MetadataStore:
    """sqlite3 元数据存储。

    Attributes:
        _db_path: sqlite 数据库文件路径。
        _conn: sqlite3.Connection（check_same_thread=False）。
        _lock: threading.Lock，串行化所有 sqlite 访问。
        _text_to_id: text → id 反向索引，load 时重建，增删同步。
    """

    def __init__(self, db_path: str) -> None:
        """初始化 MetadataStore。

        Args:
            db_path: sqlite 数据库文件路径，load() 时自动创建父目录与文件。
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._text_to_id: dict[str, int] = {}
        self._entries: dict[int, MemeEntry] = {}

    def load(self) -> None:
        """打开连接、建表/索引、重建 _text_to_id。

        Raises:
            sqlite3.DatabaseError: 数据库文件存在但非 sqlite 格式（损坏）。
        """
        import os

        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()
            rows = list(
                self._conn.execute(
                    "SELECT id, image_path, text, speaker FROM meme ORDER BY id"
                )
            )
            tags_by_id: dict[int, list[str]] = {}
            for row in self._conn.execute(
                "SELECT meme_id, tag FROM meme_tag ORDER BY meme_id, tag"
            ):
                tags_by_id.setdefault(row["meme_id"], []).append(row["tag"])
            self._entries = {
                row["id"]: MemeEntry(
                    id=row["id"],
                    image_path=row["image_path"],
                    text=row["text"],
                    speaker=row["speaker"],
                    tags=tags_by_id.get(row["id"], []),
                )
                for row in rows
            }
            self._text_to_id = {entry.text: eid for eid, entry in self._entries.items()}
        logger.info(
            "MetadataStore 加载完成: %s, 共 %d 条记录",
            self._db_path,
            len(self._entries),
        )

    def close(self) -> None:
        """关闭连接。未 load 时调用安全（no-op）。"""
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        """返回当前连接，未 load 时抛 RuntimeError（同时让类型检查收窄为非 None）。

        Returns:
            当前 sqlite3.Connection 实例。

        Raises:
            RuntimeError: 未调用 load() 即使用其他方法时抛出。
        """
        if self._conn is None:
            raise RuntimeError("MetadataStore 未 load，请先调用 load()")
        return self._conn

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_all_entries(self) -> dict[int, MemeEntry]:
        """返回全部条目，key=int(id)。

        命中 _entries 缓存，返回浅拷贝 dict（value 共享 frozen MemeEntry）。

        Returns:
            id → MemeEntry 映射（value 为缓存内 frozen 引用，不可修改）。
        """
        with self._lock:
            entries = self._entries.copy()
        logger.debug("返回全部 %d 条元数据", len(entries))
        return entries

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按 id 查询单条记录。

        命中 _entries 缓存，返回共享 frozen 引用（O(1)）。

        Args:
            entry_id: 索引 id。

        Returns:
            对应 MemeEntry（缓存内 frozen 引用，不可修改）；id 不存在时返回 None。
        """
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                logger.debug("未找到元数据: %d", entry_id)
            return entry

    def get_by_filename(self, image_path: str) -> MemeEntry | None:
        """按图片路径查询。

        Args:
            image_path: memes/ 下相对路径（扁平结构下即文件名）。

        Returns:
            对应 MemeEntry；路径不存在时返回 None。
        """
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT id, image_path, text, speaker FROM meme WHERE image_path = ?",
                (image_path,),
            ).fetchone()
            if row is None:
                logger.debug("未找到元数据: %s", image_path)
                return None
        return self.get_entry(row["id"])

    def get_id_by_text(self, text: str) -> int | None:
        """按 text 查 id（走 _text_to_id 内存反向索引，O(1)）。

        Args:
            text: 去空白后的 OCR 文本（去重键）。

        Returns:
            对应 id；text 不存在时返回 None。
        """
        with self._lock:
            return self._text_to_id.get(text)

    def find_next_id(self) -> int:
        """纯 SQL 查找最小空洞 id。

        Returns:
            可分配的最小空闲 id（空表返回 1，有空洞复用最小空洞）。
        """
        with self._lock:
            row = self._require_conn().execute(_FIND_NEXT_ID_SQL).fetchone()
        return int(row["next_id"])

    def entry_count(self) -> int:
        """条目总数（读缓存，O(1)）。

        Returns:
            _entries 当前长度。
        """
        with self._lock:
            return len(self._entries)

    def get_all_text(self) -> list[tuple[int, str]]:
        """返回全部 (id, text)，供 sync 阶段0 全量重 embed 用。

        Returns:
            按 id 升序排列的 (id, text) 列表。
        """
        with self._lock:
            rows = list(
                self._require_conn().execute("SELECT id, text FROM meme ORDER BY id")
            )
        return [(int(r["id"]), r["text"]) for r in rows]

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """新增一条记录，自动分配最小空洞 id。

        Args:
            image_path: memes/ 下相对路径。
            text: 去空白后的 OCR 文本（去重键，调用方须先用 get_id_by_text 去重）。
            speaker: 说话人，可空。
            tags: 标记词列表，可空。

        Returns:
            分配的 int id。

        Raises:
            DuplicateEntryError: text 或 image_path 撞 UNIQUE 约束。
        """
        with timed(logger, "MetadataStore 添加"):
            with self._lock:
                conn = self._require_conn()
                entry_id = int(conn.execute(_FIND_NEXT_ID_SQL).fetchone()["next_id"])
                try:
                    conn.execute(
                        "INSERT INTO meme (id, image_path, text, speaker) VALUES (?, ?, ?, ?)",
                        (entry_id, image_path, text, speaker),
                    )
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    conflicts = self._detect_conflicts(image_path=image_path, text=text)
                    raise DuplicateEntryError(conflicts) from exc
                self._write_tags(entry_id, tags)
                conn.commit()
                self._text_to_id[text] = entry_id
                self._entries[entry_id] = MemeEntry(
                    id=entry_id,
                    image_path=image_path,
                    text=text,
                    speaker=speaker,
                    tags=sorted(set(tags or [])),
                )
            logger.debug("添加元数据: id=%d, image_path=%s", entry_id, image_path)
            return entry_id

    def add_with_id(
        self,
        entry_id: int,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """迁移专用：用指定 id 写入（保留旧 id 数值）。

        Args:
            entry_id: 指定写入的 id（不复用空洞，迁移场景保留旧索引编号）。
            image_path: memes/ 下相对路径。
            text: 去空白后的 OCR 文本。
            speaker: 说话人，可空。
            tags: 标记词列表，可空。

        Returns:
            写入的 int id（即传入的 entry_id）。

        Raises:
            DuplicateEntryError: id、text 或 image_path 撞 UNIQUE/PRIMARY KEY 约束。
        """
        with self._lock:
            conn = self._require_conn()
            try:
                conn.execute(
                    "INSERT INTO meme (id, image_path, text, speaker) VALUES (?, ?, ?, ?)",
                    (entry_id, image_path, text, speaker),
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                conflicts = self._detect_conflicts(
                    image_path=image_path, text=text, entry_id=entry_id
                )
                raise DuplicateEntryError(conflicts) from exc
            self._write_tags(entry_id, tags)
            conn.commit()
            self._text_to_id[text] = entry_id
            self._entries[entry_id] = MemeEntry(
                id=entry_id,
                image_path=image_path,
                text=text,
                speaker=speaker,
                tags=sorted(set(tags or [])),
            )
        logger.debug("添加元数据: id=%d, image_path=%s", entry_id, image_path)
        return entry_id

    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = _UNSET,  # type: ignore[assignment]
        text: str | None = _UNSET,  # type: ignore[assignment]
        speaker: str | None = _UNSET,  # type: ignore[assignment]
        tags: list[str] | None = None,
    ) -> bool:
        """更新单条记录的可选字段。

        text 变更时同步更新 _text_to_id（删旧键加新键）。
        tags 非 None 时整体替换该条 tag 行。

        Args:
            entry_id: 要更新的索引 id。
            image_path: 新图片路径，_UNSET 表示不变。
            text: 新 OCR 文本，_UNSET 表示不变。
            speaker: 新说话人，_UNSET 表示不变。
            tags: 新标记词列表，None 表示不变；非 None 时整体替换。

        Raises:
            DuplicateEntryError: image_path 或 text 撞他行的 UNIQUE 约束。

        Returns:
            True 表示找到并更新，False 表示 id 不存在。
        """
        with timed(logger, "MetadataStore 更新"):
            with self._lock:
                conn = self._require_conn()
                row = conn.execute(
                    "SELECT id, text FROM meme WHERE id = ?", (entry_id,)
                ).fetchone()
                if row is None:
                    logger.debug("未找到元数据: %d", entry_id)
                    return False

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

                if sets:
                    params.append(entry_id)
                    try:
                        conn.execute(
                            f"UPDATE meme SET {', '.join(sets)} WHERE id = ?", params
                        )
                    except sqlite3.IntegrityError as exc:
                        conn.rollback()
                        conflicts = self._detect_conflicts(
                            image_path=image_path if image_path is not _UNSET else None,
                            text=text if text is not _UNSET else None,
                            exclude_id=entry_id,
                        )
                        raise DuplicateEntryError(conflicts) from exc

                if tags is not None:
                    conn.execute("DELETE FROM meme_tag WHERE meme_id = ?", (entry_id,))
                    self._write_tags(entry_id, tags)

                conn.commit()

                # 维护 _text_to_id
                old_text = row["text"]
                new_text: str = text if text is not _UNSET else old_text  # type: ignore[assignment]
                if (
                    old_text in self._text_to_id
                    and self._text_to_id[old_text] == entry_id
                ):
                    del self._text_to_id[old_text]
                self._text_to_id[new_text] = entry_id

                # 维护 _entries（frozen，用 replace 生成新对象替换缓存槽位）
                old_entry = self._entries.get(entry_id)
                if old_entry is not None:
                    self._entries[entry_id] = replace(
                        old_entry,
                        image_path=(
                            image_path
                            if image_path is not _UNSET
                            else old_entry.image_path
                        ),
                        text=new_text,
                        speaker=(
                            speaker if speaker is not _UNSET else old_entry.speaker
                        ),
                        tags=sorted(set(tags)) if tags is not None else old_entry.tags,
                    )
            logger.debug("更新元数据: %d", entry_id)
            return True

    def remove(self, entry_id: int) -> bool:
        """删除单条记录；CASCADE 删 meme_tag，同步 _text_to_id。

        Args:
            entry_id: 要删除的索引 id。

        Returns:
            True 表示删除成功，False 表示 id 不存在。
        """
        with timed(logger, "MetadataStore 删除"):
            with self._lock:
                conn = self._require_conn()
                row = conn.execute(
                    "SELECT text FROM meme WHERE id = ?", (entry_id,)
                ).fetchone()
                if row is None:
                    logger.debug("未找到元数据: %d", entry_id)
                    return False
                conn.execute("DELETE FROM meme WHERE id = ?", (entry_id,))
                conn.commit()
                text = row["text"]
                if self._text_to_id.get(text) == entry_id:
                    del self._text_to_id[text]
                self._entries.pop(entry_id, None)
            logger.debug("删除元数据: %d", entry_id)
            return True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _write_tags(self, entry_id: int, tags: list[str] | None) -> None:
        """写入 tag 行（调用方已持 _lock）。

        Args:
            entry_id: 所属 meme id。
            tags: 标记词列表；为空或 None 时直接返回不写入。
        """
        if not tags:
            return
        self._require_conn().executemany(
            "INSERT OR IGNORE INTO meme_tag (meme_id, tag) VALUES (?, ?)",
            [(entry_id, t) for t in tags],
        )

    def _detect_conflicts(
        self,
        *,
        image_path: str | None = None,
        text: str | None = None,
        entry_id: int | None = None,
        exclude_id: int | None = None,
    ) -> list[tuple[str, str]]:
        """探测所有命中的 UNIQUE/PRIMARY KEY 冲突字段（调用方已持 _lock）。

        对每个可能碰撞的字段依次探测，返回全部命中的 (column, value) 列表。
        无命中时返回空列表（理论上不会发生，调用方在 IntegrityError 后才调）。

        Args:
            image_path: 待探测的 image_path 值，None 表示不探测该列。
            text: 待探测的 text 值，None 表示不探测该列。
            entry_id: 待探测的 id 值，None 表示不探测该列。
            exclude_id: 排除自身行 id（update 场景，避免查到未变更字段）。

        Returns:
            命中的 (column, value) 列表，顺序为 id → image_path → text。
        """
        conn = self._require_conn()
        conflicts: list[tuple[str, str]] = []

        if entry_id is not None:
            if conn.execute("SELECT 1 FROM meme WHERE id = ?", (entry_id,)).fetchone():
                conflicts.append(("id", str(entry_id)))

        if image_path is not None:
            params: list[object] = [image_path]
            sql = "SELECT 1 FROM meme WHERE image_path = ?"
            if exclude_id is not None:
                sql += " AND id != ?"
                params.append(exclude_id)
            if conn.execute(sql, params).fetchone():
                conflicts.append(("image_path", image_path))

        if text is not None:
            params = [text]
            sql = "SELECT 1 FROM meme WHERE text = ?"
            if exclude_id is not None:
                sql += " AND id != ?"
                params.append(exclude_id)
            if conn.execute(sql, params).fetchone():
                conflicts.append(("text", text))

        return conflicts
