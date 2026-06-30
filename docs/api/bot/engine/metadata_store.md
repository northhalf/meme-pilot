# bot/engine/metadata_store.py — 元数据存储 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

基于 sqlite3 的元数据存储。每条表情包的 `id` 与 `VectorStore` 的向量 `id` 完全一一对应。

设计要点：
- sqlite3 标准库，`id` 为 `INTEGER PRIMARY KEY`，手动分配（复用最小空洞，不用 `AUTOINCREMENT`）。
- `meme_tag` 关联表存多值标记词，`ON DELETE CASCADE` 随 `meme` 行删除。
- `check_same_thread=False` + 内部 `threading.Lock` 串行化所有 sqlite 访问；公开方法为同步，调用方用 `asyncio.to_thread` 包装以避免阻塞事件循环。
- `_text_to_id` 内存反向索引（`text → id`），`load()` 时全量重建，增删同步维护，加速去重判定。
- `text` 假定唯一：schema 未对 `text` 加 `UNIQUE` 约束（`UNIQUE INDEX` 加在 `image_path` 上），`_text_to_id` 为单值映射，故调用方（`IndexManager`）须在 `add` 前用 `get_id_by_text` 做去重检查，避免写入重复 `text`。

## 数据类

### `MemeEntry`

```python
@dataclass
class MemeEntry:
    id: int
    image_path: str
    text: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `id` | `int` | 必填 | 索引 id，与 `VectorStore` 向量 id 一一对应 |
| `image_path` | `str` | 必填 | `memes/` 目录下相对路径（扁平结构下即文件名） |
| `text` | `str` | 必填 | OCR 去除所有空白后的文本（无空格） |
| `speaker` | `str \| None` | `None` | 说话人，可空（本次不填充） |
| `tags` | `list[str]` | `[]` | 标记词列表，从 `meme_tag` 组装（本次为空 `[]`） |

---

## 数据库 Schema

```sql
CREATE TABLE IF NOT EXISTS meme (
    id INTEGER PRIMARY KEY,
    image_path TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_image_path ON meme(image_path);

CREATE TABLE IF NOT EXISTS meme_tag (
    meme_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (meme_id, tag),
    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meme_tag_tag ON meme_tag(tag);
```

`PRAGMA foreign_keys = ON`。最小空洞 id 复用通过纯 SQL 查询实现（`SELECT MIN(t.id) + 1 ... WHERE NOT EXISTS(...)`）。

---

## `MetadataStore` 类

### `__init__(db_path: str) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `db_path` | `str` | 必填 | sqlite 数据库文件路径，`load()` 时自动创建父目录与文件 |

---

### `load() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `sqlite3.DatabaseError` | 数据库文件存在但非 sqlite 格式（损坏） |

打开连接、建表/建索引、重建 `_text_to_id`。`PRAGMA foreign_keys = ON`。`load()` 前不可调用其他方法。

---

### `close() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

关闭连接并置空引用；重复调用安全。

---

### `get_all_entries() -> dict[int, MemeEntry]`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[int, MemeEntry]` | key 为 `int(id)`，value 为 `MemeEntry`；`tags` 从 `meme_tag` 组装 |

实现 `keyword_searcher.MetadataStoreProvider` 协议。

---

### `get_entry(entry_id: int) -> MemeEntry | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `MemeEntry \| None` | 匹配条目；不存在时返回 `None` |

---

### `get_by_filename(image_path: str) -> MemeEntry | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `image_path` | `str` | `memes/` 下相对路径 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `MemeEntry \| None` | 匹配条目；不存在时返回 `None` |

---

### `get_id_by_text(text: str) -> int | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `text` | `str` | OCR 文本 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int \| None` | 走 `_text_to_id` 内存反向索引；不存在时返回 `None` |

用于 `IndexManager` 在新增前做去重检查。

---

### `find_next_id() -> int`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 当前最小可用空洞 id；无空洞时为最大 id + 1 |

纯 SQL 查询（不走 `AUTOINCREMENT`）。

---

### `entry_count() -> int`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | `meme` 表条目总数 |

---

### `get_all_text() -> list[tuple[int, str]]`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `list[tuple[int, str]]` | 全部 `(id, text)`，按 id 升序 |

供 `IndexManager.sync_with_filesystem()` 阶段0 全量重 embed 使用。

---

### `add(image_path: str, text: str, speaker: str | None = None, tags: list[str] | None = None) -> int`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `image_path` | `str` | 必填 | `memes/` 下相对路径 |
| `text` | `str` | 必填 | OCR 文本（去空白后） |
| `speaker` | `str \| None` | `None` | 说话人，可空 |
| `tags` | `list[str] \| None` | `None` | 标记词列表，`None` 或空时不写入 tag 行 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 自动分配的最小空洞 id |

调用方需在调用前用 `get_id_by_text` 自行去重；`add` 不会检查 `text` 是否已存在。写入后同步更新 `_text_to_id`。

---

### `add_with_id(entry_id: int, image_path: str, text: str, speaker: str | None = None, tags: list[str] | None = None) -> int`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `entry_id` | `int` | 必填 | 指定的 id（保留旧索引 id 数值，迁移专用） |
| `image_path` | `str` | 必填 | `memes/` 下相对路径 |
| `text` | `str` | 必填 | OCR 文本（去空白后） |
| `speaker` | `str \| None` | `None` | 说话人，可空 |
| `tags` | `list[str] \| None` | `None` | 标记词列表 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 写入的 id（即传入的 `entry_id`） |

供 `scripts/migrate_json_to_db.py` 迁移旧索引时保留原 id 使用。

---

### `update(entry_id: int, *, image_path: str | None = None, text: str | None = None, speaker: str | None = None, tags: list[str] | None = None) -> bool`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `entry_id` | `int` | 必填 | 待更新 id（位置参数） |
| `image_path` | `str \| None` | `None` | 仅在非 `None` 时更新 |
| `text` | `str \| None` | `None` | 仅在非 `None` 时更新，并同步维护 `_text_to_id`（删旧键、加新键） |
| `speaker` | `str \| None` | `None` | 仅在非 `None` 时更新 |
| `tags` | `list[str] \| None` | `None` | 非 `None` 时整体替换该条 tag 行（先删后写） |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 找到并更新；`False` id 不存在 |

---

### `remove(entry_id: int) -> bool`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 待删除 id |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 删除成功；`False` id 不存在 |

删除 `meme` 行后 `CASCADE` 删除 `meme_tag` 关联行，同步维护 `_text_to_id`。
