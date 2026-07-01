# text UNIQUE 约束 + DuplicateEntryError 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `meme.text` 列增加 DB 层 UNIQUE 约束，写入/更新冲突时 catch `sqlite3.IntegrityError` 并转为语义异常 `DuplicateEntryError`（汇总所有命中字段），迁移脚本捕获后跳过计数。

**Architecture:** 在 `MetadataStore` 层加 `_SCHEMA` 索引、新增 `DuplicateEntryError` 类与 `_detect_conflicts` 探测方法，改造 `add`/`add_with_id`/`update` 三方法包裹 INSERT/UPDATE。迁移脚本 catch 语义异常跳过计数。不改 `IndexManager` 去重逻辑（UNIQUE 仅兜底）。

**Tech Stack:** Python 3.12、sqlite3 标准库、pytest、NoneBot2 项目 `meme-pilot`

**Spec:** `docs/superpowers/specs/2026-07-01-text-unique-constraint-design.md`

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `bot/engine/metadata_store.py` | sqlite 元数据存储 | Modify：加 UNIQUE 索引、异常类、探测方法、三方法 catch |
| `scripts/migrate_json_to_db.py` | 旧 JSON → sqlite+chroma 迁移 | Modify：catch DuplicateEntryError 跳过计数 |
| `tests/unit/engine/test_metadata_store.py` | MetadataStore 单元测试 | Modify：新增 `TestDuplicateEntryError` 类（9 用例） |
| `tests/unit/test_migrate_script.py` | 迁移脚本单元测试 | Modify：新增 `dup_text_data_dir` fixture + `TestMigrationDuplicate` 类（1 用例） |
| `docs/api/API.md` | API 参考索引 | Modify：metadata_store.md 段 + index_manager.md 段 |
| `docs/PRD.md` | 产品需求文档 | Modify：schema SQL 块 + text 说明 |
| `CONTEXT.md` | 术语表 | Modify：index.db 词条 + 去重键词条 |
| `README.md` | 用户文档 | Modify：schema SQL 块 |

---

## Task 1: Schema 加 text UNIQUE 索引 + 模块 docstring 更新

**Files:**
- Modify: `bot/engine/metadata_store.py:12-13`（docstring）、`bot/engine/metadata_store.py:24-39`（`_SCHEMA`）

- [ ] **Step 1: 更新模块 docstring**

`bot/engine/metadata_store.py:12-13` 原文：

```python
- text 假定唯一：schema 未对 text 加 UNIQUE 约束，_text_to_id 为单值映射，
  故调用方（IndexManager）须在 add 前用 get_id_by_text 做去重检查，避免写入重复 text。
```

改为：

```python
- text 与 image_path 均有 UNIQUE INDEX 约束：DB 层兜底保证唯一性，
  IndexManager 仍在 add 前用 get_id_by_text 做去重检查；冲突时抛 DuplicateEntryError。
  _text_to_id 为单值映射，与 DB 唯一性保持一致。
```

- [ ] **Step 2: 在 `_SCHEMA` 追加 text UNIQUE 索引**

`bot/engine/metadata_store.py:24-39` 的 `_SCHEMA` 元组，在 `idx_meme_image_path` 行之后插入 `idx_meme_text` 行。改后完整 `_SCHEMA`：

```python
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
```

- [ ] **Step 3: 语法检查**

Run: `uv run python -m compileall bot/engine/metadata_store.py`
Expected: `Compiling 'bot/engine/metadata_store.py'...` 无报错

- [ ] **Step 4: 验证现有测试仍通过（UNIQUE 索引不破坏现有用例）**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py -v`
Expected: 全部 PASS（现有用例用唯一 text/image_path，不触发 UNIQUE）

- [ ] **Step 5: Commit**

```bash
git add bot/engine/metadata_store.py
git commit -m "feat(engine): meme.text 增加 UNIQUE 约束 + docstring 同步"
```

---

## Task 2: 新增 DuplicateEntryError 异常类

**Files:**
- Modify: `bot/engine/metadata_store.py`（在 `_FIND_NEXT_ID_SQL` 之后、`MemeEntry` 之前插入，约第 46-47 行）

- [ ] **Step 1: 在 `_FIND_NEXT_ID_SQL` 定义之后、`@dataclass class MemeEntry` 之前插入异常类**

在 `bot/engine/metadata_store.py` 第 45 行（`_FIND_NEXT_ID_SQL` 闭合的 `)` 之后空一行）插入：

```python


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
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/engine/metadata_store.py`
Expected: 无报错

- [ ] **Step 3: 验证可导入**

Run: `uv run python -c "from bot.engine.metadata_store import DuplicateEntryError; e = DuplicateEntryError([('text','加班')]); print(e, e.conflicts)"`
Expected: 输出 `重复字段冲突: text='加班' [('text', '加班')]`

- [ ] **Step 4: Commit**

```bash
git add bot/engine/metadata_store.py
git commit -m "feat(engine): 新增 DuplicateEntryError 语义异常"
```

---

## Task 3: 新增 `_detect_conflicts` 探测方法

**Files:**
- Modify: `bot/engine/metadata_store.py`（在 `_write_tags` 方法之后追加，约第 435 行后）

- [ ] **Step 1: 在 `_write_tags` 方法末尾（第 435 行 `)` 闭合后）追加 `_detect_conflicts`**

在 `bot/engine/metadata_store.py` 的 `_write_tags` 方法之后（`class MetadataStore` 内部末尾）追加：

```python

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
            if conn.execute(
                "SELECT 1 FROM meme WHERE id = ?", (entry_id,)
            ).fetchone():
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
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/engine/metadata_store.py`
Expected: 无报错

- [ ] **Step 3: Commit**

```bash
git add bot/engine/metadata_store.py
git commit -m "feat(engine): MetadataStore 新增 _detect_conflicts 冲突探测方法"
```

---

## Task 4: 改造 `add` 方法（TDD）

**Files:**
- Modify: `tests/unit/engine/test_metadata_store.py`（导入 + 新增 `TestDuplicateEntryError.add` 相关用例）
- Modify: `bot/engine/metadata_store.py:273-301`（`add` 方法）

- [ ] **Step 1: 更新测试文件导入**

`tests/unit/engine/test_metadata_store.py:7` 原文：

```python
from bot.engine.metadata_store import MemeEntry, MetadataStore
```

改为：

```python
from bot.engine.metadata_store import DuplicateEntryError, MemeEntry, MetadataStore
```

- [ ] **Step 2: 写 add 重复 text 的失败测试**

在 `tests/unit/engine/test_metadata_store.py` 末尾（`TestPersistence` 类之后）追加新类（先只放 add 的 text 冲突用例）：

```python


class TestDuplicateEntryError:
    """text/image_path/id UNIQUE 约束与 DuplicateEntryError 多字段汇总测试。"""

    def test_add_duplicate_text_raises(self, store: MetadataStore) -> None:
        """add 写入与已有 text 相同时抛 DuplicateEntryError，conflicts 含 text。"""
        store.add(image_path="a.jpg", text="加班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add(image_path="b.jpg", text="加班")
        assert ("text", "加班") in exc_info.value.conflicts
        assert ("image_path", "b.jpg") not in exc_info.value.conflicts
```

- [ ] **Step 3: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError::test_add_duplicate_text_raises -v`
Expected: FAIL —— 当前 `add` 不 catch，抛裸 `sqlite3.IntegrityError` 而非 `DuplicateEntryError`（`pytest.raises(DuplicateEntryError)` 不匹配 `IntegrityError` 的子类……注意：`DuplicateEntryError` 继承 `IntegrityError`，但裸 `IntegrityError` 不是 `DuplicateEntryError`，故 raises 不匹配，FAIL）

- [ ] **Step 4: 改造 `add` 方法**

`bot/engine/metadata_store.py:273-301` 的 `add` 方法，将 INSERT 语句用 try/except 包裹。改后完整方法：

```python
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
        return entry_id
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError::test_add_duplicate_text_raises -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/unit/engine/test_metadata_store.py bot/engine/metadata_store.py
git commit -m "feat(engine): add 方法 catch IntegrityError 转 DuplicateEntryError"
```

---

## Task 5: 改造 `add_with_id` 方法（TDD）

**Files:**
- Modify: `tests/unit/engine/test_metadata_store.py`（追加 add_with_id 用例）
- Modify: `bot/engine/metadata_store.py:303-332`（`add_with_id` 方法）

- [ ] **Step 1: 写 add_with_id 重复 id 与重复 text 的失败测试**

在 `TestDuplicateEntryError` 类中追加两个方法：

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError::test_add_with_id_duplicate_id_reported tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError::test_add_with_id_duplicate_text_reported -v`
Expected: FAIL（`add_with_id` 未 catch，抛裸 `IntegrityError`）

- [ ] **Step 3: 改造 `add_with_id` 方法**

`bot/engine/metadata_store.py:303-332` 的 `add_with_id` 方法。改后完整方法：

```python
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
        return entry_id
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError::test_add_with_id_duplicate_id_reported tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError::test_add_with_id_duplicate_text_reported -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/engine/test_metadata_store.py bot/engine/metadata_store.py
git commit -m "feat(engine): add_with_id 方法 catch IntegrityError 转 DuplicateEntryError"
```

---

## Task 6: 改造 `update` 方法（TDD）

**Files:**
- Modify: `tests/unit/engine/test_metadata_store.py`（追加 update 用例）
- Modify: `bot/engine/metadata_store.py:334-394`（`update` 方法）

- [ ] **Step 1: 写 update 撞行的失败测试**

在 `TestDuplicateEntryError` 类中追加四个方法：

```python
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

    def test_update_both_fields_collision_reports_both(self, store: MetadataStore) -> None:
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
        # 改成自身 image_path/text 不应抛
        assert store.update(eid, image_path="a.jpg", text="加班") is True
```

> 注：本步同时补全 Task 4 未放的 `test_add_duplicate_image_path_raises`、`test_add_both_fields_duplicate_reports_both`（验证 add 的 image_path 冲突与多字段汇总），它们与 `add` 改造（Task 4 已完成）配合，应直接 PASS。

- [ ] **Step 2: 运行测试验证 update 用例失败、add 补充用例通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError -v`
Expected: `test_update_*` 三个用例 FAIL（`update` 未 catch）；`test_add_*` 与 `test_add_with_id_*` 用例 PASS

- [ ] **Step 3: 改造 `update` 方法的 UPDATE 语句**

`bot/engine/metadata_store.py:378-380`（`update` 方法内 `if sets:` 分支）。原文：

```python
            if sets:
                params.append(entry_id)
                conn.execute(f"UPDATE meme SET {', '.join(sets)} WHERE id = ?", params)
```

改为（用 try/except 包裹 UPDATE）：

```python
            if sets:
                params.append(entry_id)
                try:
                    conn.execute(f"UPDATE meme SET {', '.join(sets)} WHERE id = ?", params)
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    conflicts = self._detect_conflicts(
                        image_path=image_path,
                        text=text,
                        exclude_id=entry_id,
                    )
                    raise DuplicateEntryError(conflicts) from exc
```

- [ ] **Step 4: 在 `update` 的 docstring 补充 Raises**

`bot/engine/metadata_store.py` 的 `update` 方法 docstring（约 343-356 行），在 `Returns:` 之前插入 `Raises:` 段：

```python
        Raises:
            DuplicateEntryError: image_path 或 text 撞他行的 UNIQUE 约束。
```

- [ ] **Step 5: 运行 TestDuplicateEntryError 全部用例验证通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestDuplicateEntryError -v`
Expected: 全部 PASS（9 用例）

- [ ] **Step 6: 运行整个 test_metadata_store.py 验证无回归**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add tests/unit/engine/test_metadata_store.py bot/engine/metadata_store.py
git commit -m "feat(engine): update 方法 catch IntegrityError 转 DuplicateEntryError + 补全 add 用例"
```

---

## Task 7: 迁移脚本 catch DuplicateEntryError（TDD）

**Files:**
- Modify: `tests/unit/test_migrate_script.py`（追加 fixture + 测试类）
- Modify: `scripts/migrate_json_to_db.py:22,113-117,146-153,169`

- [ ] **Step 1: 在测试文件追加 `dup_text_data_dir` fixture 与失败测试**

在 `tests/unit/test_migrate_script.py` 末尾追加：

```python


@pytest.fixture
def dup_text_data_dir(tmp_path: Path) -> Path:
    """构造含重复 text 的旧 JSON 数据目录（id 1、3 同 text "加班"）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    index_json = {
        "version": 1,
        "entries": {
            "1": {"filename": "a.jpg", "text": "加班", "text_hash": "h1"},
            "2": {"filename": "b.jpg", "text": "下班", "text_hash": "h2"},
            "3": {"filename": "c.jpg", "text": "加班", "text_hash": "h3"},  # 与 id 1 重复
        },
    }
    (data_dir / "index.json").write_text(
        json.dumps(index_json, ensure_ascii=False), encoding="utf-8"
    )
    embeddings_json = {
        "version": 2,
        "entries": {
            "1": {"text_hash": "h1", "embedding": _encode_emb([0.1] * 1024)},
            "2": {"text_hash": "h2", "embedding": _encode_emb([0.2] * 1024)},
            "3": {"text_hash": "h3", "embedding": _encode_emb([0.3] * 1024)},
        },
    }
    (data_dir / "embeddings.json").write_text(
        json.dumps(embeddings_json, ensure_ascii=False), encoding="utf-8"
    )
    return data_dir


class TestMigrationDuplicate:
    def test_duplicate_text_skipped_and_counted(self, dup_text_data_dir: Path, capsys) -> None:
        """重复 text 跳过、计数、不中断迁移。"""
        _run_migration(dup_text_data_dir)
        captured = capsys.readouterr()
        assert "UNIQUE 冲突" in captured.out

        from bot.engine.metadata_store import MetadataStore
        from bot.engine.vector_store import VectorStore
        md = MetadataStore(str(dup_text_data_dir / "index.db"))
        md.load()
        entries = md.get_all_entries()
        # id 1、2 写入，id 3 因 text 重复跳过
        assert {1, 2} == set(entries)
        assert entries[1].text == "加班"
        md.close()

        vs = VectorStore(str(dup_text_data_dir / "chroma"))
        vs.load()
        assert vs.count() == 2  # 无重复向量
        vs.close()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/test_migrate_script.py::TestMigrationDuplicate::test_duplicate_text_skipped_and_counted -v`
Expected: FAIL —— 迁移脚本未 catch，`add_with_id` 抛 `DuplicateEntryError` 中断迁移（`_run_migration` 抛异常，`capsys` 无 "UNIQUE 冲突" 输出）

- [ ] **Step 3: 更新迁移脚本导入**

`scripts/migrate_json_to_db.py:22` 原文：

```python
from bot.engine.metadata_store import MetadataStore
```

改为：

```python
from bot.engine.metadata_store import DuplicateEntryError, MetadataStore
```

- [ ] **Step 4: 新增 `skipped_dup` 计数器**

`scripts/migrate_json_to_db.py:113-117` 原文：

```python
    migrated = 0
    skipped_blank = 0
    skipped_bad_id = 0
    skipped_bad_emb = 0
    pending: list[tuple[int, list[float]]] = []
```

改为（在 `skipped_bad_emb` 后加 `skipped_dup`）：

```python
    migrated = 0
    skipped_blank = 0
    skipped_bad_id = 0
    skipped_bad_emb = 0
    skipped_dup = 0
    pending: list[tuple[int, list[float]]] = []
```

- [ ] **Step 5: 包裹 `add_with_id` 调用**

`scripts/migrate_json_to_db.py:146-153` 原文：

```python
        # 写入 sqlite（保留旧 id），向量收集后批量写入 chroma（复用旧向量）
        new_id = metadata_store.add_with_id(
            entry_id=old_id,
            image_path=image_path,
            text=text_new,
            speaker=None,
            tags=[],
        )
        pending.append((new_id, embedding))
        migrated += 1
```

改为：

```python
        # 写入 sqlite（保留旧 id），向量收集后批量写入 chroma（复用旧向量）
        try:
            new_id = metadata_store.add_with_id(
                entry_id=old_id,
                image_path=image_path,
                text=text_new,
                speaker=None,
                tags=[],
            )
        except DuplicateEntryError as exc:
            conflicts = ", ".join(f"{col}={val!r}" for col, val in exc.conflicts)
            print(f"跳过 UNIQUE 冲突: id={id_str}, 冲突字段=[{conflicts}]")
            skipped_dup += 1
            continue
        pending.append((new_id, embedding))
        migrated += 1
```

- [ ] **Step 6: 更新统计输出**

`scripts/migrate_json_to_db.py:169` 原文：

```python
    print(f"统计：迁移 {migrated}，空文本跳过 {skipped_blank}，非数字 id 跳过 {skipped_bad_id}，embedding 异常跳过 {skipped_bad_emb}")
```

改为：

```python
    print(f"统计：迁移 {migrated}，空文本跳过 {skipped_blank}，非数字 id 跳过 {skipped_bad_id}，embedding 异常跳过 {skipped_bad_emb}，UNIQUE 冲突跳过 {skipped_dup}")
```

- [ ] **Step 7: 运行测试验证通过**

Run: `uv run pytest tests/unit/test_migrate_script.py::TestMigrationDuplicate::test_duplicate_text_skipped_and_counted -v`
Expected: PASS

- [ ] **Step 8: 运行整个 test_migrate_script.py 验证无回归**

Run: `uv run pytest tests/unit/test_migrate_script.py -v`
Expected: 全部 PASS（原 7 用例 + 新 1 用例）

- [ ] **Step 9: Commit**

```bash
git add tests/unit/test_migrate_script.py scripts/migrate_json_to_db.py
git commit -m "feat(scripts): 迁移脚本 catch DuplicateEntryError 跳过计数"
```

---

## Task 8: 文档同步

**Files:**
- Modify: `docs/api/API.md`（metadata_store.md 段 + index_manager.md 段）
- Modify: `docs/PRD.md`（schema SQL 块 + text 说明）
- Modify: `CONTEXT.md`（index.db 词条 + 去重键词条）
- Modify: `README.md`（schema SQL 块）

- [ ] **Step 1: 更新 API.md 的 metadata_store.md 段（schema 说明 + 异常类签名 + Raises）**

`docs/api/API.md` 第 212 行（metadata_store.md 段末尾的 schema 说明）。原文：

```
基于 sqlite3。schema：`meme(id INTEGER PRIMARY KEY, image_path, text, speaker)` + `UNIQUE INDEX` on `image_path` + `meme_tag(meme_id, tag, FK ON DELETE CASCADE)`。`PRAGMA foreign_keys = ON`。`text` 假定唯一（无 UNIQUE 约束，调用方需用 `get_id_by_text` 去重）。
```

改为：

```
基于 sqlite3。schema：`meme(id INTEGER PRIMARY KEY, image_path, text, speaker)` + `UNIQUE INDEX` on `image_path` 与 `text` + `meme_tag(meme_id, tag, FK ON DELETE CASCADE)`。`PRAGMA foreign_keys = ON`。`text` 与 `image_path` 均有 UNIQUE 约束，写入/更新冲突抛 `DuplicateEntryError`；`IndexManager` 仍用 `get_id_by_text` 在写入前去重。
```

- [ ] **Step 2: 在 API.md 的 metadata_store.md 段插入 DuplicateEntryError 签名**

在 `docs/api/API.md` 的 metadata_store.md 段（`### \`docs/api/bot/engine/metadata_store.md\`` 标题之下、`@dataclass class MemeEntry` 之前，约第 169 行后）插入：

```python
class DuplicateEntryError(sqlite3.IntegrityError):
    # 写入/更新触发 UNIQUE/PRIMARY KEY 冲突时抛出
    conflicts: list[tuple[str, str]]  # (column, value) 列表，顺序 id→image_path→text

```

- [ ] **Step 3: 在 API.md 的 add/add_with_id/update 注释补 Raises**

`docs/api/API.md` 中：
- `add` 方法的 `) -> int  # 自动分配最小空洞 id` 改为 `) -> int  # 自动分配最小空洞 id；Raises DuplicateEntryError`
- `add_with_id` 方法的 `) -> int  # 迁移专用：保留旧 id` 改为 `) -> int  # 迁移专用：保留旧 id；Raises DuplicateEntryError`
- `update` 方法的 `) -> bool` 改为 `) -> bool  # Raises DuplicateEntryError`

- [ ] **Step 4: 更新 API.md 的 index_manager.md 段（add_single_file Raises）**

`docs/api/API.md` 第 152 行（index_manager.md 段 `add_single_file` 的 Raises 注释）。原文：

```
    async def add_single_file(self, filename: str) -> AddResult
    # Raises: CompressionError, OcrError, EmbeddingError
```

改为：

```
    async def add_single_file(self, filename: str) -> AddResult
    # Raises: CompressionError, OcrError, EmbeddingError, DuplicateEntryError
```

- [ ] **Step 5: 更新 PRD schema SQL 块（追加 text UNIQUE 索引）**

`docs/PRD.md` 第 305 行（schema SQL 块内，`CREATE UNIQUE INDEX idx_meme_image_path` 之后）。原文：

```sql
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
```

改为：

```sql
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
CREATE UNIQUE INDEX idx_meme_text ON meme(text);
```

- [ ] **Step 6: 更新 PRD text 说明**

`docs/PRD.md` 第 315 行（schema SQL 块之后的说明）。原文：

```
`meme` 表以 `id` 为主键（`INTEGER PRIMARY KEY`，手动分配最小空洞 id，不用 `AUTOINCREMENT`），`image_path` 为 `memes/` 下相对路径（扁平结构下即文件名），`text` 为 OCR 去除所有空白后的文本，`speaker` 为说话人（v1.0 预留，不填充）。`meme_tag` 关联表存多值标记词，`ON DELETE CASCADE` 随 `meme` 行删除。`PRAGMA foreign_keys = ON`。`text` 假定唯一但 schema 未加 `UNIQUE` 约束（`UNIQUE INDEX` 加在 `image_path` 上），去重由调用方（`IndexManager`）通过 `MetadataStore.get_id_by_text` 在写入前检查。
```

改为：

```
`meme` 表以 `id` 为主键（`INTEGER PRIMARY KEY`，手动分配最小空洞 id，不用 `AUTOINCREMENT`），`image_path` 为 `memes/` 下相对路径（扁平结构下即文件名），`text` 为 OCR 去除所有空白后的文本，`speaker` 为说话人（v1.0 预留，不填充）。`meme_tag` 关联表存多值标记词，`ON DELETE CASCADE` 随 `meme` 行删除。`PRAGMA foreign_keys = ON`。`text` 与 `image_path` 均加 `UNIQUE INDEX` 约束；`IndexManager` 仍通过 `get_id_by_text` 在写入前去重，DB 层 UNIQUE 作为兜底，冲突抛 `DuplicateEntryError`。
```

- [ ] **Step 7: 更新 CONTEXT.md 的 index.db 词条**

`CONTEXT.md` 第 36 行。原文：

```
| **index.db** | 业务索引数据库，sqlite3 格式，存于 `data/index.db`；`meme` 表保存每个 id 对应的 `image_path`、OCR `text`（去空白后）、`speaker`，`meme_tag` 关联表保存多值标记词；`UNIQUE INDEX` 加在 `image_path` 上，`PRAGMA foreign_keys = ON` |
```

改为：

```
| **index.db** | 业务索引数据库，sqlite3 格式，存于 `data/index.db`；`meme` 表保存每个 id 对应的 `image_path`、OCR `text`（去空白后）、`speaker`，`meme_tag` 关联表保存多值标记词；`UNIQUE INDEX` 加在 `image_path` 与 `text` 上，`PRAGMA foreign_keys = ON` |
```

- [ ] **Step 8: 更新 CONTEXT.md 的去重键词条**

`CONTEXT.md` 第 22 行。原文：

```
| **去重键** | OCR 文本去除所有空白字符（含半角/全角空格、制表符、换行）后的纯文本；用于在 `/add` 和 `sync_with_filesystem` 新增阶段判定「是否完全相同的图片」，通过 `MetadataStore.get_id_by_text` 查询，实时计算不落盘 |
```

改为：

```
| **去重键** | OCR 文本去除所有空白字符（含半角/全角空格、制表符、换行）后的纯文本；用于在 `/add` 和 `sync_with_filesystem` 新增阶段判定「是否完全相同的图片」，通过 `MetadataStore.get_id_by_text` 查询，实时计算不落盘；DB 层 `text` UNIQUE 约束兜底，冲突抛 `DuplicateEntryError` |
```

- [ ] **Step 9: 更新 README schema SQL 块（追加 text UNIQUE 注释）**

`README.md` 第 161 行（schema SQL 块内，`speaker TEXT` 那行之后、`);` 之前或 `CREATE TABLE meme_tag` 之前）。原文 schema 块：

```sql
CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    image_path TEXT NOT NULL,   -- memes/ 下相对路径
    text TEXT NOT NULL,         -- OCR 去除所有空白后的文本
    speaker TEXT                -- 说话人，v1.0 预留
);
CREATE TABLE meme_tag (
```

在 `CREATE TABLE meme` 的 `);` 之后、`CREATE TABLE meme_tag` 之前插入 UNIQUE 索引行：

```sql
CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    image_path TEXT NOT NULL,   -- memes/ 下相对路径
    text TEXT NOT NULL,         -- OCR 去除所有空白后的文本
    speaker TEXT                -- 说话人，v1.0 预留
);
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
CREATE UNIQUE INDEX idx_meme_text ON meme(text);
CREATE TABLE meme_tag (
```

- [ ] **Step 10: 验证文档无语法错误**

Run: `uv run python -m compileall bot tests scripts`
Expected: 无报错（文档改动不影响 Python 编译；此处顺便确认代码未被误改）

- [ ] **Step 11: Commit**

```bash
git add docs/api/API.md docs/PRD.md CONTEXT.md README.md
git commit -m "docs: 同步 text UNIQUE 约束与 DuplicateEntryError 文档"
```

---

## Task 9: 全量验证

**Files:** 无（仅验证）

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -v`
Expected: 全部 PASS（含原有用例 + Task 4-7 新增用例）

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot tests scripts`
Expected: 无报错

- [ ] **Step 3: 验证迁移脚本可运行（dry run 幂等检查）**

Run: `uv run python -c "import scripts.migrate_json_to_db as m; print('import ok')"`
Expected: 输出 `import ok`（确认导入无循环依赖）

- [ ] **Step 4: 最终状态确认**

Run: `git log --oneline -8`
Expected: 看到 Task 1-8 的 8 个 commit

- [ ] **Step 5: 提示用户审核**

不自行 push 或合并。告知用户实现完成，等待审核。

---

## 完成标准

- [ ] `meme.text` 有 `UNIQUE INDEX idx_meme_text`
- [ ] `DuplicateEntryError` 继承 `sqlite3.IntegrityError`，携带 `conflicts: list[tuple[str,str]]`
- [ ] `add` / `add_with_id` / `update` 三方法 catch `IntegrityError` 转 `DuplicateEntryError`
- [ ] `_detect_conflicts` 探测 id→image_path→text 全部命中字段
- [ ] 迁移脚本 catch `DuplicateEntryError` 跳过并计入 `skipped_dup`
- [ ] 4 处文档同步（API.md / PRD / CONTEXT.md / README）
- [ ] 全量测试通过，无回归
- [ ] 未自行 push 或合并
