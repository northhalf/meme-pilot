# 设计文档 — 为 `meme.text` 增加 UNIQUE 约束

> 日期：2026-07-01
> 状态：待实现
> 范围：`bot/engine/metadata_store.py`、`scripts/migrate_json_to_db.py`、4 处文档、测试

---

## 1. 背景与目标

### 1.1 现状

`bot/engine/metadata_store.py` 的 `meme` 表 schema：

- `image_path` 已有 `CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path)`。
- **`text` 没有 DB 层 UNIQUE 约束**。模块 docstring（第 12-13 行）明确："text 假定唯一：schema 未对 text 加 UNIQUE 约束，`_text_to_id` 为单值映射，故调用方（IndexManager）须在 add 前用 `get_id_by_text` 做去重检查。"

也就是说，`text` 唯一性完全依赖应用层 check-then-act：`IndexManager._write_entry`（`index_manager.py:534-565`）先 `get_id_by_text(text)` 查 `_text_to_id` 内存索引，命中走"去重替换"分支（只 `update` image_path，不改 text），未命中才 `add`。

### 1.2 问题

1. **DB 无兜底**：若 `_text_to_id` 与 sqlite 一致性被破坏（跨进程写入、迁移脚本、未来 Web 管理界面、调用方漏查重），会静默写入重复 text 脏数据。
2. **`load()` 重建隐患**：`load()` 用 dict comprehension `{row["text"]: row["id"]}` 重建 `_text_to_id`，若库里真有重复 text，后写行覆盖前写行，前一行变成"查不到但 DB 存在"的孤儿。
3. **契约脆弱**：`add()` docstring 写"调用方须先用 get_id_by_text 去重"是口头契约，无强制力。

### 1.3 目标

- 为 `text` 列增加 DB 层 `UNIQUE INDEX` 约束，把"应用层记忆"升级为"DB 强制不变式"。
- 写入/更新触发 UNIQUE/PRIMARY KEY 冲突时，catch `sqlite3.IntegrityError` 并转为语义异常 `DuplicateEntryError`，汇总所有命中冲突字段。
- 不改变现有去重机制（`get_id_by_text` check-then-act 不变），UNIQUE 仅作兜底。
- 迁移脚本捕获 `DuplicateEntryError` 后跳过并计数，与现有"空文本/坏 id/坏 embedding 跳过"风格一致。

### 1.4 非目标

- 不改 `IndexManager` 去重逻辑（`_write_entry` 的 check-then-act 保持不变）。
- 不改 `VectorStore`。
- 不处理存量脏数据（假设开发期库为空；若 `CREATE UNIQUE INDEX` 因存量重复 text 失败，视为库已脏、需人工清理）。
- 不改 `_text_to_id` 内存索引的设计与维护逻辑。

---

## 2. 决策记录

| 决策点 | 取舍 | 理由 |
|--------|------|------|
| A. 是否现在加 UNIQUE | 加 | v1.0 开发期，影响小 |
| B. 存量数据 | 假设库为空，不加检测/清理 | 开发期；失败即视为库脏需人工处理 |
| C. catch 范围 | `add` + `add_with_id` + `update` 全覆盖 | 任何路径的 UNIQUE 冲突都不让裸 IntegrityError 冒泡 |
| D. 异常类设计 | 单一 `DuplicateEntryError`，携带 `conflicts: list[tuple[str,str]]` | 不分裂类；多字段冲突汇总；调用方按 `conflicts` 分支 |
| E. 冲突列探测 | catch 后对每个可能碰撞字段依次探测，汇总全部命中字段 | 比"猜 text"准确；多列同时冲突时全部报告 |
| F. 迁移脚本 | 跳过并计数（`skipped_dup`） | 与现有跳过风格一致，单冲突不中断迁移 |
| G. IndexManager 中间层 | 不 catch `DuplicateEntryError`，原样透传到插件层 | IndexManager 只 catch `VectorStore.upsert` 异常转 `EmbeddingError`；`metadata_store` 异常本就冒泡 |
| H. 迁移脚本测试 | 纳入本次范围 | 复用 `test_migrate_script.py` 的 fixture 模式 |

---

## 3. 详细设计

### 3.1 Schema 改动

`bot/engine/metadata_store.py:24-39` 的 `_SCHEMA` 元组，在 `image_path` UNIQUE 索引后追加 `text` UNIQUE 索引：

```python
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meme ("
    "    id INTEGER PRIMARY KEY,"
    "    image_path TEXT NOT NULL,"
    "    text TEXT NOT NULL,"
    "    speaker TEXT"
    ");",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_image_path ON meme(image_path);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_text ON meme(text);",   # 新增
    "CREATE TABLE IF NOT EXISTS meme_tag ("
    "    meme_id INTEGER NOT NULL,"
    "    tag TEXT NOT NULL,"
    "    PRIMARY KEY (meme_id, tag),"
    "    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_meme_tag_tag ON meme_tag(tag);",
)
```

- 用 `IF NOT EXISTS`，已有库的 `load()` 不会重建已存在的索引。
- 存量风险：若库已存在重复 text，`CREATE UNIQUE INDEX` 失败抛 `sqlite3.IntegrityError`。按决策 B 不额外加检测/清理；失败视为库已脏。

模块 docstring 第 12-13 行同步更新：原"text 假定唯一：schema 未对 text 加 UNIQUE 约束"改为"schema 已对 text 加 UNIQUE 约束，DB 层兜底"。

### 3.2 异常类

在 `bot/engine/metadata_store.py` 模块级（`_SCHEMA`/`_FIND_NEXT_ID_SQL` 之后、`MemeEntry` 之前）新增：

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

设计要点：
- 继承 `sqlite3.IntegrityError`，保留"DB 约束冲突"类型语义；上层已有 `except sqlite3.IntegrityError` 仍能捕获，向后兼容。
- 单一类 + `conflicts` 列表，不分裂为 `DuplicateTextError`/`DuplicateImagepathError`。
- `column` 取值范围：`"text"` / `"image_path"` / `"id"`。

### 3.3 冲突探测辅助方法

新增 `MetadataStore._detect_conflicts`，对每个可能碰撞字段依次探测，返回全部命中字段列表：

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

探测顺序固定 id → image_path → text，`conflicts` 列表元素顺序一致，便于断言与日志。

### 3.4 三方法 catch 改造

各方法的探测范围：

| 方法 | 探测字段 | exclude_id |
|------|---------|------------|
| `add` | image_path, text | 不传（INSERT 无自身行） |
| `add_with_id` | id, image_path, text | 不传 |
| `update` | 仅本次实际更新的字段（`image_path is not None` 才探测；`text is not None` 才探测） | `exclude_id=entry_id` |

**`add()` 改造**（原 metadata_store.py:273-301）：

```python
def add(self, image_path, text, speaker=None, tags=None) -> int:
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

**`add_with_id()` 改造**（原 metadata_store.py:303-332）：

```python
def add_with_id(self, entry_id, image_path, text, speaker=None, tags=None) -> int:
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

**`update()` 改造**（原 metadata_store.py:334-394，仅 `if sets:` 分支包裹）：

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

要点：
- `_text_to_id` 维护逻辑不变——只在 INSERT/UPDATE 成功后才更新内存索引，catch 在 `conn.execute` 之后、`self._text_to_id[text] = ...` 之前，失败抛异常时不会污染 `_text_to_id`。
- `update` 的 `_text_to_id` 维护在 catch 之后，若 UPDATE 抛异常已 raise，不会执行到 `_text_to_id` 修改。
- `conn.rollback()`：catch 后回滚，清理失败事务状态（原代码无 catch 时有此隐患，加 UNIQUE 后触发概率上升，一并补上）。
- 不 catch `_write_tags` 异常——tag 表无 UNIQUE 冲突（`INSERT OR IGNORE`）。
- `update` 探测时 `exclude_id=entry_id`，避免查到自身未变更的 image_path/text（`test_update_same_row_not_reported` 覆盖）。

### 3.5 迁移脚本处理

`scripts/migrate_json_to_db.py`：

1. **导入**（第 22 行）：

```python
from bot.engine.metadata_store import DuplicateEntryError, MetadataStore
```

2. **新增计数器**（第 113-117 行附近）：

```python
    migrated = 0
    skipped_blank = 0
    skipped_bad_id = 0
    skipped_bad_emb = 0
    skipped_dup = 0   # 新增：UNIQUE 冲突跳过
    pending: list[tuple[int, list[float]]] = []
```

3. **包裹 `add_with_id` 调用**（第 146-153 行）：

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

4. **统计输出补充**（第 169 行）：

```python
    print(f"统计：迁移 {migrated}，空文本跳过 {skipped_blank}，非数字 id 跳过 {skipped_bad_id}，embedding 异常跳过 {skipped_bad_emb}，UNIQUE 冲突跳过 {skipped_dup}")
```

要点：
- 只 catch `DuplicateEntryError`，不 catch 裸 `sqlite3.IntegrityError`——`add_with_id` 已转为语义异常，其他意外 IntegrityError 让其冒泡中断。
- `continue` 跳过 `pending.append` 和 `migrated += 1`，冲突条目不进入向量写入队列。
- 幂等性不受影响——幂等检查在 `entry_count() > 0` 时整体跳过（第 94 行）。

---

## 4. 文档同步

全部为描述性同步，不改代码逻辑。

### 4.1 `docs/api/bot/engine/metadata_store.md`（API.md 第 159-212 行）

- schema 说明段（第 212 行）原文："`text` 假定唯一（无 UNIQUE 约束，调用方需用 `get_id_by_text` 去重）" → 改为："`text` 与 `image_path` 均有 `UNIQUE INDEX` 约束；写入冲突抛 `DuplicateEntryError`"
- 新增 `DuplicateEntryError` 签名块：

```python
class DuplicateEntryError(sqlite3.IntegrityError):
    # 写入/更新触发 UNIQUE/PRIMARY KEY 冲突时抛出
    conflicts: list[tuple[str, str]]  # (column, value) 列表，顺序 id→image_path→text
```

- `add` / `add_with_id` / `update` 的 Raises 注释补充 `DuplicateEntryError`

### 4.2 `docs/api/bot/engine/index_manager.md`（API.md 第 90-155 行）

- `add_single_file` 的 Raises 补充 `DuplicateEntryError`（`_write_entry` 调 `add`/`update` 时可能抛，透传到 IndexManager 层，再冒泡到插件层 `/add`）
- `sync_with_filesystem` 的 Returns 说明：`failed` 可能含 UNIQUE 冲突的文件名

### 4.3 `docs/PRD.md`（第 295-320 行 schema 段）

- 第 315 行原文："`text` 假定唯一但 schema 未加 `UNIQUE` 约束（`UNIQUE INDEX` 加在 `image_path` 上），去重由调用方（`IndexManager`）通过 `MetadataStore.get_id_by_text` 在写入前检查。" → 改为："`text` 与 `image_path` 均加 `UNIQUE INDEX` 约束；`IndexManager` 仍通过 `get_id_by_text` 在写入前去重，DB 层 UNIQUE 作为兜底，冲突抛 `DuplicateEntryError`。"
- schema SQL 块（第 298-313 行）补充 `CREATE UNIQUE INDEX idx_meme_text ON meme(text);`

### 4.4 `CONTEXT.md`

- `index.db` 词条（第 36 行）原文："`UNIQUE INDEX` 加在 `image_path` 上，`PRAGMA foreign_keys = ON`" → 改为："`UNIQUE INDEX` 加在 `image_path` 与 `text` 上，`PRAGMA foreign_keys = ON`"
- 「去重键」词条（第 22 行）补充："DB 层 `text` UNIQUE 约束兜底，冲突抛 `DuplicateEntryError`"

### 4.5 `README.md`（第 154-170 行 schema 块）

- SQL 块补充 `CREATE UNIQUE INDEX idx_meme_text ON meme(text);` 注释行

### 4.6 `CLAUDE.md`

- 无需改动。当前实现注意事项未涉及 schema 细节；`DuplicateEntryError` 属实现细节，不在 CLAUDE.md 层级。

---

## 5. 测试

### 5.1 `tests/unit/engine/test_metadata_store.py` 新增 `TestDuplicateEntryError`

导入补充：

```python
from bot.engine.metadata_store import DuplicateEntryError, MemeEntry, MetadataStore
```

测试用例（9 个）：

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

    def test_add_with_id_duplicate_id_reported(self, store: MetadataStore) -> None:
        """add_with_id 撞已存在 id 时 conflicts 含 id。"""
        store.add_with_id(1, "a.jpg", "甲")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add_with_id(1, "b.jpg", "乙")
        assert ("id", "1") in exc_info.value.conflicts

    def test_add_with_id_duplicate_text_reported(self, store: MetadataStore) -> None:
        """add_with_id 撞已存在 text 时 conflicts 含 text。"""
        store.add_with_id(1, "a.jpg", "加班")
        with pytest.raises(DuplicateEntryError) as exc_info:
            store.add_with_id(2, "b.jpg", "加班")
        assert ("text", "加班") in exc_info.value.conflicts

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
        assert store.update(eid, image_path="a.jpg", text="加班") is True
```

### 5.2 `tests/unit/test_migrate_script.py` 新增 UNIQUE 冲突跳过测试

复用现有 `old_data_dir` fixture 模式与 `_run_migration` helper，新增构造重复 text 的 fixture：

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

### 5.3 现有测试不受影响

- `test_add_*`、`test_update_*`、`test_remove_*` 等用唯一 text/image_path，不触发 UNIQUE。
- `test_reuses_smallest_hole` 等用 `add_with_id` 写不同 id 不同 text，不触发。
- `test_migrate_script.py` 现有 7 个测试用唯一 text，不触发。
- 迁移脚本 `test_idempotent_second_run_skips`：幂等检查在 `entry_count() > 0` 整体跳过，不涉及单条冲突。

---

## 6. 实现顺序

1. `bot/engine/metadata_store.py`：加 `_SCHEMA` 的 text UNIQUE 索引 + 模块 docstring 更新
2. `bot/engine/metadata_store.py`：新增 `DuplicateEntryError` 类
3. `bot/engine/metadata_store.py`：新增 `_detect_conflicts` 方法
4. `bot/engine/metadata_store.py`：改造 `add` / `add_with_id` / `update` 三方法
5. `scripts/migrate_json_to_db.py`：导入 + 计数器 + 包裹 + 统计输出
6. `tests/unit/engine/test_metadata_store.py`：新增 `TestDuplicateEntryError`
7. `tests/unit/test_migrate_script.py`：新增 `TestMigrationDuplicate`
8. 文档同步（API.md metadata_store/index_manager 段、PRD schema、CONTEXT.md、README schema）
9. `uv run pytest` + `uv run python -m compileall bot tests scripts` 验证

---

## 7. 风险与边界

| 场景 | 行为 |
|------|------|
| 库已存在重复 text | `CREATE UNIQUE INDEX idx_meme_text` 失败抛 `sqlite3.IntegrityError`，`load()` 失败，Bot 启动失败 → 视为库已脏，需人工清理 |
| 迁移旧 JSON 含重复 text | `add_with_id` 抛 `DuplicateEntryError`，迁移脚本跳过计数，继续处理其他条目 |
| 跨进程写入同 text | DB 层 UNIQUE 兜底，后写者抛 `DuplicateEntryError` |
| `add` 正常流程 | `IndexManager` 已 `get_id_by_text` 查重，不触发 UNIQUE；UNIQUE 仅兜底 |
| `update` 改成自身值 | `exclude_id` 排除自身，不报冲突 |
| `_text_to_id` 漂移 | DB UNIQUE 兜底，避免脏数据；`load()` 重建不会因重复 text 丢孤儿（DB 保证唯一） |
