# 表情包合集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MemePilot 增加目录合集、持久化 `/switch`、公开复合 ID、按合集检索、`/mv` 和显式离线迁移工具，同时保留 SQLite 内部整数 ID 与 Chroma 向量 ID。

**Architecture:** `meme.id` 继续作为内部稳定 ID；`collection_id + local_id` 构成用户可见 ID。`MetadataStore` 管理合集、ChatScope 选择和合集维度缓存，`CollectionManager` 负责名称及 ID 解析，`IndexManager` 继续持锁编排文件、SQLite 和 Chroma。Chroma 记录增加 `collection_id` 元数据，语义搜索用 `where` 过滤；旧库只能通过 `scripts/migrate_meme_collections.py` 显式升级。

**Tech Stack:** Python 3.12、sqlite3、pathlib、shutil、ChromaDB 1.5.9+、NoneBot2、OneBot v11、pytest、ty、ruff。

---

## 实施约束

- 设计依据：`docs/superpowers/specs/2026-07-13-meme-collections-design.md`。
- 当前分支是 `main`。不执行 `git add`、`git commit` 或 `git merge`。每个任务末尾只检查差异，交给用户审核。
- 实施前若需要隔离环境，先调用 `superpowers:using-git-worktrees`，不得在本计划阶段自行创建 worktree。
- 每个行为变更先写失败测试，再实现最小代码。
- 每完成一个模块，同步更新对应的 `docs/api/bot/...` 文档；创建新模块时同时更新 `docs/api/API.md`。
- Python 函数使用中文 Google 风格 docstring，并写完整参数和返回类型。
- 不使用 `from __future__ import annotations`。
- 不新增运行时依赖。Chroma API 使用项目现有 `chromadb>=1.5.9`。
- Chroma metadata 更新会覆盖整份 metadata。更新 `collection_id` 前必须先 `get(include=["metadatas"])`，合并原 metadata 后再 `update(ids=..., metadatas=...)`。
- 代码质量命令使用 `uv run ruff` 与 `uv run ty check`，避免依赖全局工具版本。

## 文件结构

### 新建

- `bot/engine/collection_manager.py`：公开 ID、合集名称和 ChatScope 选择的领域解析。
- `bot/plugins/_collection_utils.py`：插件共享的当前合集及公开 ID 解析适配。
- `bot/plugins/switch.py`：`/switch` 命令。
- `bot/plugins/move.py`：`/mv` 确认命令。
- `scripts/migrate_meme_collections.py`：`upgrade-schema` 与 `move-root` 子命令。
- `tests/unit/engine/test_collection_manager.py`
- `tests/unit/plugins/test_switch.py`
- `tests/unit/plugins/test_move.py`
- `tests/unit/test_migrate_meme_collections.py`
- `docs/api/bot/engine/collection_manager.md`
- `docs/api/bot/plugins/_collection_utils.md`
- `docs/api/bot/plugins/switch.md`
- `docs/api/bot/plugins/move.md`

### 主要修改

- `bot/engine/types.py`：公开 ID、合集、选择上下文与搜索 DTO。
- `bot/engine/metadata_store.py`：新 Schema、合集缓存、局部编号、ChatScope 持久化。
- `bot/engine/vector_store.py`：Chroma metadata、过滤查询、metadata 更新。
- `bot/engine/protocols.py`：合集感知的 Store/Vector 协议。
- `bot/engine/keyword_searcher.py`
- `bot/engine/random_searcher.py`
- `bot/engine/combined_searcher.py`
- `bot/engine/semantic_searcher.py`
- `bot/engine/ai_matcher.py`
- `bot/engine/index_manager.py`：合集过滤、递归同步、合集写入、移动。
- `bot/app_state.py`、`bot/bot.py`：构造并注册 `CollectionManager`。
- `bot/plugins/_search_utils.py`：公开 ID 与合集名称展示。
- `bot/plugins/add.py`、`addtag.py`、`delete.py`、`edit.py`、`setspeaker.py`、`info.py`。
- `bot/plugins/plain_text.py`、`query.py`、`rand.py`、`sim.py`、`ai.py`、`refresh.py`。
- `bot/plugins/_help_text.py`。
- `scripts/regenerate_embeddings.py`、`scripts/convert_memes_to_webp.py`。
- 对应单元测试、集成测试和 API 文档。

---

### Task 1: 定义公开 ID、合集 DTO 与解析器

**Files:**
- Modify: `bot/engine/types.py`
- Create: `bot/engine/collection_manager.py`
- Create: `tests/unit/engine/test_collection_manager.py`
- Create: `docs/api/bot/engine/collection_manager.md`
- Modify: `docs/api/bot/engine/types.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 写公开 ID 与短号解析失败测试**

创建 `tests/unit/engine/test_collection_manager.py`，先覆盖前导零、规范化、合集 0 短号拒绝和非法格式：

```python
"""CollectionManager 单元测试。"""

from dataclasses import dataclass

import pytest

from bot.engine.collection_manager import (
    CollectionManager,
    InvalidPublicIdError,
    ShortIdUnavailableError,
)
from bot.engine.types import MemeCollection, MemePublicId


@dataclass(frozen=True)
class FakeScope:
    user_id: int = 10001
    chat_type: str = "private"
    chat_id: int = 10001


class FakeCollectionStore:
    def __init__(self) -> None:
        self.collections = {
            1: MemeCollection(id=1, name="新三国"),
            2: MemeCollection(id=2, name="1"),
        }
        self.selected = 0

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        return self.collections.get(collection_id)

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        return next((c for c in self.collections.values() if c.name == name), None)

    def list_collections(self) -> list[MemeCollection]:
        return list(self.collections.values())

    def get_selected_collection(
        self, user_id: int, chat_type: str, chat_id: int
    ) -> int:
        return self.selected

    def set_selected_collection(
        self,
        user_id: int,
        chat_type: str,
        chat_id: int,
        collection_id: int,
    ) -> None:
        self.selected = collection_id

    def collection_entry_count(self, collection_id: int | None) -> int:
        return 10 if collection_id is None else collection_id


def test_parse_full_id_accepts_leading_zeroes() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.parse_meme_id("01.002", selected_collection_id=0) == MemePublicId(
        collection_id=1,
        local_id=2,
    )
    assert str(MemePublicId(1, 2)) == "1.2"


def test_parse_short_id_uses_current_collection() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.parse_meme_id("002", selected_collection_id=1) == MemePublicId(1, 2)


def test_parse_short_id_rejects_all_collections_scope() -> None:
    manager = CollectionManager(FakeCollectionStore())

    with pytest.raises(ShortIdUnavailableError):
        manager.parse_meme_id("2", selected_collection_id=0)


@pytest.mark.parametrize("raw", ["", "1.0", "+1.2", "1.2.3", "１.２", ".2", "1."])
def test_parse_meme_id_rejects_invalid_syntax(raw: str) -> None:
    manager = CollectionManager(FakeCollectionStore())

    with pytest.raises(InvalidPublicIdError):
        manager.parse_meme_id(raw, selected_collection_id=1)
```

- [ ] **Step 2: 写合集解析优先级和 ChatScope 选择失败测试**

在同一文件追加：

```python
def test_numeric_target_prefers_collection_id_then_exact_name() -> None:
    store = FakeCollectionStore()
    manager = CollectionManager(store)

    assert manager.resolve_collection("1").name == "新三国"
    del store.collections[1]
    assert manager.resolve_collection("1").name == "1"


def test_zero_resolves_to_all_collections_selection() -> None:
    manager = CollectionManager(FakeCollectionStore())

    selection = manager.resolve_selection("000")

    assert selection.collection_id == 0
    assert selection.name == "全部合集"
    assert selection.search_filter is None


def test_get_and_set_scope_selection() -> None:
    store = FakeCollectionStore()
    manager = CollectionManager(store)
    scope = FakeScope()

    manager.set_selected(scope, 1)

    selection = manager.get_selected(scope)
    assert selection.collection_id == 1
    assert selection.name == "新三国"
    assert selection.search_filter == 1
```

- [ ] **Step 3: 运行测试并确认模块缺失**

Run:

```bash
uv run pytest tests/unit/engine/test_collection_manager.py -q
```

Expected: collection 报错，原因是 `bot.engine.collection_manager` 或新类型尚不存在。

- [ ] **Step 4: 在 `types.py` 增加不可变领域类型**

在 `bot/engine/types.py` 增加：

```python
GLOBAL_COLLECTION_ID = 0
GLOBAL_COLLECTION_NAME = "全局"
ALL_COLLECTIONS_NAME = "全部合集"


@dataclass(frozen=True, slots=True)
class MemePublicId:
    """用户可见的表情包复合 ID。"""

    collection_id: int
    local_id: int

    def __post_init__(self) -> None:
        if self.collection_id < 0 or self.local_id < 1:
            raise ValueError("公开 ID 数值范围无效")

    def __str__(self) -> str:
        return f"{self.collection_id}.{self.local_id}"


@dataclass(frozen=True, slots=True)
class MemeCollection:
    """普通表情包合集。"""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class CollectionSelection:
    """ChatScope 当前合集及对应搜索过滤。"""

    collection_id: int
    name: str
    search_filter: int | None


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    """用于 `/switch` 列表展示的合集统计。"""

    collection_id: int
    name: str
    entry_count: int
    selected: bool
```

- [ ] **Step 5: 实现 `CollectionManager` 最小接口**

创建 `bot/engine/collection_manager.py`：

```python
"""表情包合集名称、公开 ID 与 ChatScope 选择解析。"""

import re
from typing import Protocol

from .types import (
    ALL_COLLECTIONS_NAME,
    GLOBAL_COLLECTION_ID,
    CollectionSelection,
    CollectionSummary,
    MemeCollection,
    MemePublicId,
)

_FULL_ID_RE = re.compile(r"^[0-9]+\.[0-9]+$", re.ASCII)
_SHORT_ID_RE = re.compile(r"^[0-9]+$", re.ASCII)


class CollectionNotFoundError(ValueError):
    """未找到指定合集。"""


class InvalidCollectionNameError(ValueError):
    """合集名称不能映射为安全的单层目录名。"""


class InvalidPublicIdError(ValueError):
    """公开 ID 语法或数值范围无效。"""


class ShortIdUnavailableError(InvalidPublicIdError):
    """全部合集模式下不能解析局部短号。"""


class ScopeLike(Protocol):
    """CollectionManager 使用的最小聊天作用域接口。"""

    user_id: int
    chat_type: str
    chat_id: int


class CollectionStoreProtocol(Protocol):
    """CollectionManager 所需的元数据接口。"""

    def get_collection(self, collection_id: int) -> MemeCollection | None: ...
    def get_collection_by_name(self, name: str) -> MemeCollection | None: ...
    def list_collections(self) -> list[MemeCollection]: ...
    def get_selected_collection(
        self, user_id: int, chat_type: str, chat_id: int
    ) -> int: ...
    def set_selected_collection(
        self, user_id: int, chat_type: str, chat_id: int, collection_id: int
    ) -> None: ...
    def collection_entry_count(self, collection_id: int | None) -> int: ...


class CollectionManager:
    """集中实现合集参数与公开 ID 规则。"""

    def __init__(self, store: CollectionStoreProtocol) -> None:
        self._store = store

    def parse_meme_id(
        self, raw: str, *, selected_collection_id: int
    ) -> MemePublicId:
        text = raw.strip()
        if _FULL_ID_RE.fullmatch(text):
            collection_text, local_text = text.split(".", maxsplit=1)
            try:
                return MemePublicId(int(collection_text), int(local_text))
            except ValueError as exc:
                raise InvalidPublicIdError(text) from exc
        if _SHORT_ID_RE.fullmatch(text):
            if selected_collection_id == GLOBAL_COLLECTION_ID:
                raise ShortIdUnavailableError(text)
            try:
                return MemePublicId(selected_collection_id, int(text))
            except ValueError as exc:
                raise InvalidPublicIdError(text) from exc
        raise InvalidPublicIdError(text)

    def resolve_collection(self, raw: str) -> MemeCollection:
        text = raw.strip()
        if _SHORT_ID_RE.fullmatch(text):
            by_id = self._store.get_collection(int(text))
            if by_id is not None:
                return by_id
        by_name = self._store.get_collection_by_name(text)
        if by_name is None:
            raise CollectionNotFoundError(text)
        return by_name

    def resolve_selection(self, raw: str) -> CollectionSelection:
        text = raw.strip()
        if _SHORT_ID_RE.fullmatch(text) and int(text) == GLOBAL_COLLECTION_ID:
            return CollectionSelection(0, ALL_COLLECTIONS_NAME, None)
        collection = self.resolve_collection(text)
        return CollectionSelection(collection.id, collection.name, collection.id)

    def get_selected(self, scope: ScopeLike) -> CollectionSelection:
        collection_id = self._store.get_selected_collection(
            scope.user_id, scope.chat_type, scope.chat_id
        )
        if collection_id == GLOBAL_COLLECTION_ID:
            return CollectionSelection(0, ALL_COLLECTIONS_NAME, None)
        collection = self._store.get_collection(collection_id)
        if collection is None:
            self._store.set_selected_collection(
                scope.user_id, scope.chat_type, scope.chat_id, 0
            )
            return CollectionSelection(0, ALL_COLLECTIONS_NAME, None)
        return CollectionSelection(collection.id, collection.name, collection.id)

    def set_selected(self, scope: ScopeLike, collection_id: int) -> None:
        self._store.set_selected_collection(
            scope.user_id, scope.chat_type, scope.chat_id, collection_id
        )

    def list_summaries(self, scope: ScopeLike) -> list[CollectionSummary]:
        selected = self.get_selected(scope).collection_id
        summaries = [
            CollectionSummary(
                collection_id=0,
                name=ALL_COLLECTIONS_NAME,
                entry_count=self._store.collection_entry_count(None),
                selected=selected == 0,
            )
        ]
        summaries.extend(
            CollectionSummary(
                collection_id=collection.id,
                name=collection.name,
                entry_count=self._store.collection_entry_count(collection.id),
                selected=selected == collection.id,
            )
            for collection in self._store.list_collections()
        )
        return summaries
```

- [ ] **Step 6: 运行解析测试**

Run:

```bash
uv run pytest tests/unit/engine/test_collection_manager.py -q
```

Expected: 全部 PASS。

- [ ] **Step 7: 更新 API 文档**

在 `docs/api/bot/engine/types.md` 记录 `MemePublicId`、`MemeCollection`、`CollectionSelection`、`CollectionSummary`。创建 `docs/api/bot/engine/collection_manager.md`，逐项记录异常、Protocol 和公开方法。在 `docs/api/API.md` 的引擎模块加入：

```markdown
- [`collection_manager`](bot/engine/collection_manager.md)
```

- [ ] **Step 8: 检查任务差异**

Run:

```bash
uv run ruff check bot/engine/types.py bot/engine/collection_manager.py tests/unit/engine/test_collection_manager.py
uv run ruff format --check bot/engine/types.py bot/engine/collection_manager.py tests/unit/engine/test_collection_manager.py
git diff --check
git status --short
```

Expected: ruff 与 diff 检查通过；工作区包含本任务文件，不执行提交。

---

### Task 2: 升级 `MetadataStore` Schema 与合集内表情包缓存

**Files:**
- Modify: `bot/engine/metadata_store.py`
- Modify: `tests/unit/engine/test_metadata_store.py`
- Modify: `docs/api/bot/engine/metadata_store.md`

- [ ] **Step 1: 写新库 Schema 与旧库拒绝测试**

在 `tests/unit/engine/test_metadata_store.py` 增加：

```python
import sqlite3

import pytest

from bot.engine.metadata_store import CURRENT_SCHEMA_VERSION, SchemaVersionError


def test_new_database_creates_current_schema(tmp_sqlite_path: Path) -> None:
    store = MetadataStore(str(tmp_sqlite_path))
    store.load()
    store.close()

    with sqlite3.connect(tmp_sqlite_path) as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()
        meme_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(meme)").fetchall()
        }

    assert version == (CURRENT_SCHEMA_VERSION,)
    assert {"collection_id", "local_id"}.issubset(meme_columns)


def test_legacy_database_is_rejected_without_mutation(tmp_sqlite_path: Path) -> None:
    with sqlite3.connect(tmp_sqlite_path) as conn:
        conn.execute(
            "CREATE TABLE meme ("
            "id INTEGER PRIMARY KEY, image_path TEXT, text TEXT, speaker TEXT)"
        )
        conn.execute(
            "INSERT INTO meme (id, image_path, text) VALUES (42, 'a.webp', '文本')"
        )
        conn.commit()

    store = MetadataStore(str(tmp_sqlite_path))
    with pytest.raises(SchemaVersionError):
        store.load()

    with sqlite3.connect(tmp_sqlite_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(meme)")}
        row = conn.execute("SELECT id, image_path, text FROM meme").fetchone()

    assert "collection_id" not in columns
    assert row == (42, "a.webp", "文本")
```

- [ ] **Step 2: 写合集内编号与文本唯一性失败测试**

增加：

```python
def test_local_ids_are_independent_and_reuse_smallest_gap(store: MetadataStore) -> None:
    first = store.add("a.webp", "甲", collection_id=1)
    second = store.add("b.webp", "乙", collection_id=1)
    other = store.add("c.webp", "丙", collection_id=2)
    store.remove(first)
    reused = store.add("d.webp", "丁", collection_id=1)

    assert store.get_entry(second).public_id == MemePublicId(1, 2)
    assert store.get_entry(other).public_id == MemePublicId(2, 1)
    assert store.get_entry(reused).public_id == MemePublicId(1, 1)


def test_same_text_allowed_across_collections(store: MetadataStore) -> None:
    first = store.add("a.webp", "相同文本", collection_id=1)
    second = store.add("b.webp", "相同文本", collection_id=2)

    assert first != second
    assert store.get_id_by_text("相同文本", collection_id=1) == first
    assert store.get_id_by_text("相同文本", collection_id=2) == second


def test_same_text_rejected_within_collection(store: MetadataStore) -> None:
    store.add("a.webp", "相同文本", collection_id=1)

    with pytest.raises(DuplicateEntryError):
        store.add("b.webp", "相同文本", collection_id=1)
```

保持现有 `store` fixture 只负责 `load()`，避免普通合集预置数据影响编号测试。需要普通合集的测试在自身 Arrange 阶段创建：

```python
def test_same_text_allowed_across_collections(store: MetadataStore) -> None:
    first_collection = store.create_collection("合集一")
    second_collection = store.create_collection("合集二")
    first = store.add(
        "a.webp", "相同文本", collection_id=first_collection.id
    )
    second = store.add(
        "b.webp", "相同文本", collection_id=second_collection.id
    )

    assert first != second
    assert store.get_id_by_text(
        "相同文本", collection_id=first_collection.id
    ) == first
    assert store.get_id_by_text(
        "相同文本", collection_id=second_collection.id
    ) == second
```

`test_local_ids_are_independent_and_reuse_smallest_gap` 和同合集冲突测试也在测试体内创建所需合集，不修改模块级 fixture。

- [ ] **Step 3: 运行新增测试并确认失败**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_metadata_store.py::test_new_database_creates_current_schema \
  tests/unit/engine/test_metadata_store.py::test_legacy_database_is_rejected_without_mutation \
  tests/unit/engine/test_metadata_store.py::test_local_ids_are_independent_and_reuse_smallest_gap \
  tests/unit/engine/test_metadata_store.py::test_same_text_allowed_across_collections \
  -v
```

Expected: FAIL，原因包括 Schema 缺字段、`SchemaVersionError`/`create_collection` 缺失或 `add()` 不接受 `collection_id`。

- [ ] **Step 4: 替换 Schema 常量并加入版本检查**

在 `bot/engine/metadata_store.py` 定义：

```python
CURRENT_SCHEMA_VERSION = 2

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
    "CREATE UNIQUE INDEX idx_meme_collection_local "
    "ON meme(collection_id, local_id);",
    "CREATE UNIQUE INDEX idx_meme_collection_text "
    "ON meme(collection_id, text);",
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


class SchemaVersionError(RuntimeError):
    """数据库 Schema 版本与当前程序不兼容。"""
```

把 `load()` 的建库分支改为“空数据库创建、已有数据库只校验”：

```python
with self._lock:
    conn = self._require_conn()
    table_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if not table_names:
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        conn.commit()
    elif "schema_version" not in table_names:
        conn.close()
        self._conn = None
        raise SchemaVersionError(
            "检测到旧版 index.db，请停止 Bot 后运行 "
            "scripts.migrate_meme_collections upgrade-schema"
        )
    else:
        versions = [
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_version")
        ]
        if versions != [CURRENT_SCHEMA_VERSION]:
            conn.close()
            self._conn = None
            raise SchemaVersionError(
                f"不支持的 Schema 版本: {versions!r}，"
                "请先运行显式迁移脚本"
            )
```

把空库创建抽成模块内部函数，迁移脚本复用同一份 Schema 定义，避免运行时和迁移脚本漂移：

```python
def create_current_schema(conn: sqlite3.Connection) -> None:
    """在空 SQLite 连接中创建当前 Schema 并写入版本。"""
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO schema_version (version) VALUES (?)",
        (CURRENT_SCHEMA_VERSION,),
    )
```

`load()` 的空库分支调用 `create_current_schema(conn)` 后提交。该函数要求调用方已确认目标表不存在；它不判断旧库，也不执行迁移。

- [ ] **Step 5: 扩展 `MemeEntry` 与缓存结构**

将 `MemeEntry` 改为：

```python
@dataclass(frozen=True, slots=True)
class MemeEntry:
    """单条表情包元数据。"""

    id: int
    image_path: str
    text: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
    collection_id: int = 0
    local_id: int = 1
    collection_name: str = GLOBAL_COLLECTION_NAME

    @property
    def public_id(self) -> MemePublicId:
        """返回用户可见复合 ID。"""
        return MemePublicId(self.collection_id, self.local_id)
```

在构造函数增加：

```python
self._text_to_id: dict[tuple[int, str], int] = {}
self._entries: dict[int, MemeEntry] = {}
self._entries_by_collection: dict[int, dict[int, int]] = {}
self._collections: dict[int, MemeCollection] = {}
self._collection_name_to_id: dict[str, int] = {}
self._selected_collections: dict[tuple[int, str, int], int] = {}
```

`load()` 用 `LEFT JOIN meme_collection` 读取条目：

```python
rows = list(
    conn.execute(
        "SELECT m.id, m.collection_id, m.local_id, m.image_path, "
        "m.text, m.speaker, c.name AS collection_name "
        "FROM meme AS m "
        "LEFT JOIN meme_collection AS c ON c.id = m.collection_id "
        "ORDER BY m.id"
    )
)
```

根目录使用 `GLOBAL_COLLECTION_NAME`，普通合集缺少关联记录时抛 `sqlite3.DatabaseError`，不要静默加载孤儿条目。

- [ ] **Step 6: 扩展新增、更新、删除方法**

保持现有位置参数兼容，把合集设为 keyword-only：

```python
def add(
    self,
    image_path: str,
    text: str,
    speaker: str | None = None,
    tags: list[str] | None = None,
    *,
    collection_id: int = 0,
) -> int:
    """在指定合集新增条目并分配内部 ID 与局部 ID。"""
    with self._lock:
        conn = self._require_conn()
        if collection_id != 0 and collection_id not in self._collections:
            raise ValueError(f"collection_id={collection_id} 不存在")
        entry_id = int(conn.execute(_FIND_NEXT_ID_SQL).fetchone()["next_id"])
        used_local_ids = self._entries_by_collection.get(collection_id, {})
        local_id = 1
        while local_id in used_local_ids:
            local_id += 1
        conn.execute(
            "INSERT INTO meme "
            "(id, collection_id, local_id, image_path, text, speaker) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, collection_id, local_id, image_path, text, speaker),
        )
        self._write_tags(entry_id, tags)
        conn.commit()
        collection_name = (
            GLOBAL_COLLECTION_NAME
            if collection_id == 0
            else self._collections[collection_id].name
        )
        entry = MemeEntry(
            id=entry_id,
            image_path=image_path,
            text=text,
            speaker=speaker,
            tags=sorted(set(tags or [])),
            collection_id=collection_id,
            local_id=local_id,
            collection_name=collection_name,
        )
        self._entries[entry_id] = entry
        self._entries_by_collection.setdefault(collection_id, {})[local_id] = entry_id
        self._text_to_id[(collection_id, text)] = entry_id
        return entry_id


def get_id_by_text(self, text: str, *, collection_id: int = 0) -> int | None:
    with self._lock:
        return self._text_to_id.get((collection_id, text))

def get_entries(self, collection_id: int | None = None) -> dict[int, MemeEntry]:
    with self._lock:
        if collection_id is None:
            return self._entries.copy()
        internal_ids = self._entries_by_collection.get(collection_id, {}).values()
        return {entry_id: self._entries[entry_id] for entry_id in internal_ids}

def get_entry_by_public_id(self, public_id: MemePublicId) -> MemeEntry | None:
    with self._lock:
        entry_id = self._entries_by_collection.get(public_id.collection_id, {}).get(
            public_id.local_id
        )
        return self._entries.get(entry_id) if entry_id is not None else None
```

`add()` 在同一 `_lock` 内：

1. 校验普通合集存在；
2. 用 `SELECT` 查内部 ID 最小空洞；
3. 用 `(collection_id, local_id)` 查局部最小空洞；
4. 插入六个字段；
5. 更新 `_text_to_id[(collection_id, text)]`、`_entries`、`_entries_by_collection`。

`update()` 增加 keyword-only 参数：

```python
collection_id: int | None = _UNSET
local_id: int | None = _UNSET
```

当合集或文本变化时，先删除旧 `(collection_id, text)` 键，再写新键；同步移动 `_entries_by_collection` 槽位。`remove()` 同步清理两个缓存。

`_detect_conflicts()` 增加 `collection_id` 参数，文本冲突 SQL 必须限定合集：

```python
sql = "SELECT 1 FROM meme WHERE collection_id = ? AND text = ?"
params = [collection_id, text]
if exclude_id is not None:
    sql += " AND id != ?"
    params.append(exclude_id)
```

image_path 仍全库唯一。`add()`、`add_with_id()` 和 `update()` 捕获 `IntegrityError` 时传入最终 `collection_id`，确保异常报告与联合唯一索引一致。

- [ ] **Step 7: 修订旧 MetadataStore 测试调用**

现有不关心合集的测试继续使用默认 `collection_id=0`。只有测试普通合集时显式传 `collection_id`。把旧断言：

```python
assert store.get_id_by_text("猫") == eid
```

保留为全局默认；增加普通合集断言：

```python
assert store.get_id_by_text("猫", collection_id=1) == collection_entry_id
```

- [ ] **Step 8: 运行 MetadataStore 测试**

Run:

```bash
uv run pytest tests/unit/engine/test_metadata_store.py -q
```

Expected: 全部 PASS。

- [ ] **Step 9: 更新 MetadataStore API 文档并检查**

更新 `docs/api/bot/engine/metadata_store.md` 的 Schema、`MemeEntry`、缓存、`add`、`add_with_id`、`update`、`get_entries`、`get_entry_by_public_id`、`get_id_by_text` 和 `SchemaVersionError`。

Run:

```bash
uv run ruff check bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
uv run ruff format --check bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
uv run ty check
git diff --check
```

Expected: 全部成功，不提交。

---

### Task 3: 实现合集注册表与 ChatScope 持久化

**Files:**
- Modify: `bot/engine/metadata_store.py`
- Modify: `tests/unit/engine/test_metadata_store.py`
- Modify: `tests/unit/engine/test_collection_manager.py`
- Modify: `docs/api/bot/engine/metadata_store.md`
- Modify: `docs/api/bot/engine/collection_manager.md`

- [ ] **Step 1: 写合集编号复用测试**

```python
def test_collection_ids_reuse_smallest_gap(store: MetadataStore) -> None:
    first = store.create_collection("新三国")
    second = store.create_collection("甄嬛传")
    store.delete_collection_and_reset_scopes(first.id)
    reused = store.create_collection("水浒传")

    assert first.id == 1
    assert second.id == 2
    assert reused.id == 1
    assert [c.name for c in store.list_collections()] == ["水浒传", "甄嬛传"]
```

- [ ] **Step 2: 写 ChatScope 持久化和批量回退测试**

```python
def test_scope_selection_persists_after_reload(tmp_sqlite_path: Path) -> None:
    first = MetadataStore(str(tmp_sqlite_path))
    first.load()
    collection = first.create_collection("新三国")
    first.set_selected_collection(10001, "group", 20002, collection.id)
    first.close()

    second = MetadataStore(str(tmp_sqlite_path))
    second.load()
    try:
        assert second.get_selected_collection(10001, "group", 20002) == collection.id
    finally:
        second.close()


def test_delete_collection_resets_all_scopes_atomically(store: MetadataStore) -> None:
    collection = store.create_collection("新三国")
    store.set_selected_collection(1, "private", 1, collection.id)
    store.set_selected_collection(2, "group", 99, collection.id)

    reset_count = store.delete_collection_and_reset_scopes(collection.id)

    assert reset_count == 2
    assert store.get_selected_collection(1, "private", 1) == 0
    assert store.get_selected_collection(2, "group", 99) == 0
    assert store.get_collection(collection.id) is None
```

- [ ] **Step 3: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/engine/test_metadata_store.py -k "collection or scope" -q
```

Expected: FAIL，原因是合集和 ChatScope 方法缺失。

- [ ] **Step 4: 实现合集查询与最小空号创建**

在 `MetadataStore` 增加：

```python
def list_collections(self) -> list[MemeCollection]:
    with self._lock:
        return [self._collections[key] for key in sorted(self._collections)]


def get_collection(self, collection_id: int) -> MemeCollection | None:
    with self._lock:
        return self._collections.get(collection_id)


def get_collection_by_name(self, name: str) -> MemeCollection | None:
    with self._lock:
        collection_id = self._collection_name_to_id.get(name)
        return self._collections.get(collection_id) if collection_id is not None else None


def create_collection(
    self, name: str, *, collection_id: int | None = None
) -> MemeCollection:
    with self._lock:
        conn = self._require_conn()
        if collection_id is None:
            row = conn.execute(
                "SELECT COALESCE(MIN(c.id + 1), 1) AS next_id "
                "FROM (SELECT 0 AS id UNION ALL SELECT id FROM meme_collection) c "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM meme_collection used WHERE used.id = c.id + 1)"
            ).fetchone()
            collection_id = int(row["next_id"])
        conn.execute(
            "INSERT INTO meme_collection (id, name) VALUES (?, ?)",
            (collection_id, name),
        )
        conn.commit()
        collection = MemeCollection(collection_id, name)
        self._collections[collection_id] = collection
        self._collection_name_to_id[name] = collection_id
        self._entries_by_collection.setdefault(collection_id, {})
        return collection
```

`load()` 同时加载 `meme_collection` 和 `chat_collection_scope` 缓存。

- [ ] **Step 5: 实现 ChatScope 选择与删除事务**

```python
def get_selected_collection(
    self, user_id: int, chat_type: str, chat_id: int
) -> int:
    key = (user_id, chat_type, chat_id)
    with self._lock:
        return self._selected_collections.get(key, 0)


def set_selected_collection(
    self,
    user_id: int,
    chat_type: str,
    chat_id: int,
    collection_id: int,
) -> None:
    key = (user_id, chat_type, chat_id)
    with self._lock:
        if collection_id != 0 and collection_id not in self._collections:
            raise ValueError(f"collection_id={collection_id} 不存在")
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO chat_collection_scope "
            "(user_id, chat_type, chat_id, selected_collection_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, chat_type, chat_id) DO UPDATE SET "
            "selected_collection_id = excluded.selected_collection_id",
            (user_id, chat_type, chat_id, collection_id),
        )
        conn.commit()
        self._selected_collections[key] = collection_id


def delete_collection_and_reset_scopes(self, collection_id: int) -> int:
    with self._lock:
        if self._entries_by_collection.get(collection_id):
            raise ValueError("合集仍包含表情包，不能删除")
        conn = self._require_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE chat_collection_scope SET selected_collection_id = 0 "
                "WHERE selected_collection_id = ?",
                (collection_id,),
            )
            conn.execute("DELETE FROM meme_collection WHERE id = ?", (collection_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        reset_count = cursor.rowcount
        collection = self._collections.pop(collection_id, None)
        if collection is not None:
            self._collection_name_to_id.pop(collection.name, None)
        self._entries_by_collection.pop(collection_id, None)
        for key, selected in list(self._selected_collections.items()):
            if selected == collection_id:
                self._selected_collections[key] = 0
        return reset_count
```

增加：

```python
def collection_entry_count(self, collection_id: int | None) -> int:
    with self._lock:
        if collection_id is None:
            return len(self._entries)
        return len(self._entries_by_collection.get(collection_id, {}))


def find_next_local_id(self, collection_id: int) -> int:
    with self._lock:
        used = self._entries_by_collection.get(collection_id, {})
        candidate = 1
        while candidate in used:
            candidate += 1
        return candidate
```

- [ ] **Step 6: 让 `CollectionManager` 测试使用真实 Store 契约**

补充真实 SQLite 测试：

```python
def test_collection_manager_lists_zero_and_regular_collections(
    store: MetadataStore,
) -> None:
    collection = store.create_collection("新三国")
    store.add("新三国/a.webp", "文本", collection_id=collection.id)
    manager = CollectionManager(store)
    scope = FakeScope()

    summaries = manager.list_summaries(scope)

    assert [(item.collection_id, item.entry_count) for item in summaries] == [
        (0, 1),
        (collection.id, 1),
    ]
```

- [ ] **Step 7: 运行 Store 与 Manager 测试**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_metadata_store.py \
  tests/unit/engine/test_collection_manager.py \
  -q
```

Expected: 全部 PASS。

- [ ] **Step 8: 更新 API 文档并检查**

记录合集 CRUD、编号复用、ChatScope 缓存和原子回退。

Run:

```bash
uv run ruff check bot/engine/metadata_store.py bot/engine/collection_manager.py tests/unit/engine/test_metadata_store.py tests/unit/engine/test_collection_manager.py
uv run ruff format --check bot/engine/metadata_store.py bot/engine/collection_manager.py tests/unit/engine/test_metadata_store.py tests/unit/engine/test_collection_manager.py
uv run ty check
git diff --check
```

Expected: 全部成功，不提交。

---

### Task 4: 为 Chroma 增加合集 metadata 与过滤查询

**Files:**
- Modify: `bot/engine/vector_store.py`
- Modify: `bot/engine/protocols.py`
- Modify: `tests/unit/engine/test_vector_store.py`
- Modify: `docs/api/bot/engine/vector_store.md`
- Modify: `docs/api/bot/engine/protocols.md`

- [ ] **Step 1: 写 metadata 写入和 where 查询失败测试**

```python
@pytest.mark.asyncio
async def test_query_filters_by_collection(store: VectorStore) -> None:
    await store.upsert(1, [1.0, 0.0], collection_id=1)
    await store.upsert(2, [1.0, 0.0], collection_id=2)

    hits = await store.query([1.0, 0.0], n_results=10, collection_id=2)

    assert [hit.entry_id for hit in hits] == [2]


@pytest.mark.asyncio
async def test_get_collection_ids_returns_metadata(store: VectorStore) -> None:
    await store.upsert(1, [1.0, 0.0], collection_id=3)

    assert await store.get_collection_ids() == {1: 3}
```

- [ ] **Step 2: 写 metadata 更新不改变向量测试**

```python
@pytest.mark.asyncio
async def test_update_collection_id_preserves_embedding(store: VectorStore) -> None:
    await store.upsert(1, [1.0, 0.0], collection_id=1)

    await store.update_collection_id(1, 2)

    assert await store.get_collection_ids() == {1: 2}
    hits = await store.query([1.0, 0.0], n_results=1, collection_id=2)
    assert hits[0].entry_id == 1
    assert hits[0].similarity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_update_collection_id_preserves_other_metadata(
    store: VectorStore,
) -> None:
    await store.upsert(1, [1.0, 0.0], collection_id=1)
    await store.update_metadata(1, {"collection_id": 1, "source": "legacy"})

    await store.update_collection_id(1, 2)

    assert await store.get_metadatas() == {
        1: {"collection_id": 2, "source": "legacy"}
    }


@pytest.mark.asyncio
async def test_snapshot_and_restore_records_removes_added_metadata(
    store: VectorStore,
) -> None:
    collection = store._require_collection()
    collection.add(ids=["1"], embeddings=[[1.0, 0.0]])
    snapshot = await store.snapshot_records([1])
    await store.update_collection_id(1, 2)

    await store.restore_records(snapshot)

    assert await store.get_metadatas() == {1: {}}
    hits = await store.query([1.0, 0.0], n_results=1)
    assert hits[0].entry_id == 1
```

- [ ] **Step 3: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/engine/test_vector_store.py -k "collection" -q
```

Expected: FAIL，原因是新参数和方法缺失。

- [ ] **Step 4: 修改 `upsert`、`query` 和 `rebuild_all`**

在模块顶部增加 Chroma metadata 值类型：

```python
VectorMetadata = dict[str, str | int | float | bool]
```

公开签名改为：

```python
async def upsert(
    self, entry_id: int, embedding: list[float], *, collection_id: int = 0
) -> None:
    await asyncio.to_thread(self._upsert_sync, entry_id, embedding, collection_id)


async def query(
    self,
    query_embedding: list[float],
    n_results: int | None = 10,
    *,
    collection_id: int | None = None,
) -> list[VectorHit]:
    return await asyncio.to_thread(
        self._query_sync, query_embedding, n_results, collection_id
    )


async def rebuild_all(
    self, items: list[tuple[int, list[float], int]]
) -> None:
    await asyncio.to_thread(self._rebuild_all_sync, items)
```

同步实现传 metadata：

```python
collection.upsert(
    ids=[str(entry_id)],
    embeddings=[embedding],
    metadatas=[{"collection_id": collection_id}],
)
```

查询时：

```python
if collection_id is None:
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )
else:
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where={"collection_id": collection_id},
    )
```

全量重建使用：

```python
collection.upsert(
    ids=[str(entry_id) for entry_id, _, _ in items],
    embeddings=[embedding for _, embedding, _ in items],
    metadatas=[
        {"collection_id": collection_id}
        for _, _, collection_id in items
    ],
)
```

- [ ] **Step 5: 实现 metadata 读取、合并更新和可恢复快照**

在 `vector_store.py` 增加：

```python
@dataclass(frozen=True, slots=True)
class VectorRecord:
    """迁移补偿使用的完整 Chroma 记录。"""

    entry_id: int
    embedding: list[float]
    metadata: VectorMetadata
```

公开方法：

```python
async def get_metadatas(self) -> dict[int, VectorMetadata]:
    return await asyncio.to_thread(self._get_metadatas_sync)


async def update_metadata(
    self, entry_id: int, metadata: VectorMetadata
) -> None:
    await asyncio.to_thread(self._update_metadata_sync, entry_id, metadata)


async def get_collection_ids(self) -> dict[int, int | None]:
    return await asyncio.to_thread(self._get_collection_ids_sync)


def _get_collection_ids_sync(self) -> dict[int, int | None]:
    with self._lock:
        result = self._require_collection().get(include=["metadatas"])
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    return {
        int(raw_id): (
            int(metadata["collection_id"])
            if metadata is not None and "collection_id" in metadata
            else None
        )
        for raw_id, metadata in zip(ids, metadatas)
    }


async def update_collection_id(self, entry_id: int, collection_id: int) -> None:
    metadata = (await self.get_metadatas()).get(entry_id)
    if metadata is None:
        raise ValueError(f"向量 id={entry_id} 不存在")
    updated = dict(metadata)
    updated["collection_id"] = collection_id
    await self.update_metadata(entry_id, updated)


async def snapshot_records(self, entry_ids: list[int]) -> list[VectorRecord]:
    return await asyncio.to_thread(self._snapshot_records_sync, entry_ids)


async def restore_records(self, records: list[VectorRecord]) -> None:
    await asyncio.to_thread(self._restore_records_sync, records)
```

同步 helpers：

```python
def _get_metadatas_sync(self) -> dict[int, VectorMetadata]:
    with self._lock:
        result = self._require_collection().get(include=["metadatas"])
    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    return {
        int(raw_id): dict(metadata or {})
        for raw_id, metadata in zip(ids, metadatas)
    }


def _update_metadata_sync(
    self, entry_id: int, metadata: VectorMetadata
) -> None:
    with self._lock:
        collection = self._require_collection()
        existing = collection.get(ids=[str(entry_id)], include=["metadatas"])
        if not (existing.get("ids") or []):
            raise ValueError(f"向量 id={entry_id} 不存在")
        collection.update(ids=[str(entry_id)], metadatas=[metadata])


def _snapshot_records_sync(self, entry_ids: list[int]) -> list[VectorRecord]:
    with self._lock:
        result = self._require_collection().get(
            ids=[str(entry_id) for entry_id in entry_ids],
            include=["embeddings", "metadatas"],
        )
    ids = result.get("ids") or []
    embeddings = result.get("embeddings")
    metadatas = result.get("metadatas") or []
    if embeddings is None:
        raise RuntimeError("Chroma 未返回 embeddings")
    return [
        VectorRecord(
            entry_id=int(raw_id),
            embedding=list(embedding),
            metadata=dict(metadata or {}),
        )
        for raw_id, embedding, metadata in zip(ids, embeddings, metadatas)
    ]


def _restore_records_sync(self, records: list[VectorRecord]) -> None:
    if not records:
        return
    with self._lock:
        collection = self._require_collection()
        ids = [str(record.entry_id) for record in records]
        collection.delete(ids=ids)
        with_metadata = [record for record in records if record.metadata]
        without_metadata = [record for record in records if not record.metadata]
        if with_metadata:
            collection.add(
                ids=[str(record.entry_id) for record in with_metadata],
                embeddings=[record.embedding for record in with_metadata],
                metadatas=[record.metadata for record in with_metadata],
            )
        if without_metadata:
            collection.add(
                ids=[str(record.entry_id) for record in without_metadata],
                embeddings=[record.embedding for record in without_metadata],
            )
```

恢复操作先删再加，使原来没有 metadata 的记录恢复为“无 metadata”，而不是残留迁移时新增的 `collection_id`。

- [ ] **Step 6: 更新共享 Protocol**

`VectorQueryProvider.query` 增加 keyword-only `collection_id`。`IndexManager.VectorStoreProtocol` 同步加入：

```python
async def update_collection_id(self, entry_id: int, collection_id: int) -> None: ...
async def get_collection_ids(self) -> dict[int, int | None]: ...
async def get_metadatas(self) -> dict[int, VectorMetadata]: ...
async def update_metadata(
    self, entry_id: int, metadata: VectorMetadata
) -> None: ...
async def snapshot_records(self, entry_ids: list[int]) -> list[VectorRecord]: ...
async def restore_records(self, records: list[VectorRecord]) -> None: ...
```

并把 `upsert`、`rebuild_all` 改成新签名。

- [ ] **Step 7: 修订现有 VectorStore 测试与调用**

全局向量测试可继续依赖 `collection_id=0` 默认值。把重建测试改成：

```python
await store.rebuild_all(
    [(10, [1.0, 1.0], 1), (20, [0.5, 0.5], 2)]
)
```

- [ ] **Step 8: 运行测试并更新文档**

Run:

```bash
uv run pytest tests/unit/engine/test_vector_store.py -q
uv run ruff check bot/engine/vector_store.py bot/engine/protocols.py tests/unit/engine/test_vector_store.py
uv run ruff format --check bot/engine/vector_store.py bot/engine/protocols.py tests/unit/engine/test_vector_store.py
uv run ty check
```

Expected: 全部成功。更新 VectorStore/Protocol API 文档，明确 metadata 更新覆盖语义和合并步骤。

---

### Task 5: 让搜索结果和搜索器携带公开 ID 并接受条目子集

**Files:**
- Modify: `bot/engine/types.py`
- Modify: `bot/engine/keyword_searcher.py`
- Modify: `bot/engine/random_searcher.py`
- Modify: `bot/engine/combined_searcher.py`
- Modify: `bot/engine/semantic_searcher.py`
- Modify: `bot/engine/ai_matcher.py`
- Modify: `bot/engine/protocols.py`
- Modify: `tests/unit/engine/test_keyword_searcher.py`
- Modify: `tests/unit/engine/test_random_searcher.py`
- Modify: `tests/unit/engine/test_combined_searcher.py`
- Modify: `tests/unit/engine/test_semantic_searcher.py`
- Modify: `tests/unit/engine/test_ai_matcher.py`
- Modify: matching API docs

- [ ] **Step 1: 写 SearchResult 公开 ID 测试**

在 `tests/unit/engine/test_keyword_searcher.py` 增加：

```python
def test_search_result_keeps_collection_identity() -> None:
    entry = MemeEntry(
        id=42,
        image_path="新三国/a.webp",
        text="丞相何故发笑",
        collection_id=1,
        local_id=3,
        collection_name="新三国",
    )
    store = Mock()
    store.get_all_entries.return_value = {42: entry}
    searcher = KeywordSearcher(store)

    result = searcher.search("丞相")

    assert result[0].entry_id == 42
    assert result[0].public_id == MemePublicId(1, 3)
    assert result[0].collection_name == "新三国"
```

- [ ] **Step 2: 写 Random/Combined 子集契约测试**

```python
def test_random_search_only_uses_supplied_entries() -> None:
    entries = {
        2: MemeEntry(
            id=2,
            image_path="新三国/a.webp",
            text="文本",
            collection_id=1,
            local_id=1,
            collection_name="新三国",
        )
    }

    results = searcher.search_random_in(entries, limit=10)

    assert [result.entry_id for result in results] == [2]


def test_combined_search_only_uses_supplied_entries() -> None:
    entries = {
        2: MemeEntry(
            id=2,
            image_path="新三国/a.webp",
            text="文本",
            tags=["三国"],
            collection_id=1,
            local_id=1,
            collection_name="新三国",
        )
    }

    results = searcher.search_in(entries, None, [], ["三国"])

    assert [result.public_id for result in results] == [MemePublicId(1, 1)]
```

- [ ] **Step 3: 写语义与 AI 的 Chroma 过滤参数测试**

```python
@pytest.mark.asyncio
async def test_semantic_search_passes_collection_filter() -> None:
    vector_store = AsyncMock()
    vector_store.query.return_value = []
    metadata_store = Mock()
    metadata_store.get_all_entries.return_value = {}
    searcher = SemanticSearcher(metadata_store, vector_store)

    await searcher.search_semantic([1.0, 0.0], limit=10, collection_id=2)

    vector_store.query.assert_awaited_once_with(
        [1.0, 0.0], n_results=10, collection_id=2
    )


@pytest.mark.asyncio
async def test_ai_match_passes_collection_filter() -> None:
    vector_store = AsyncMock()
    vector_store.count.return_value = 1
    vector_store.query.return_value = []
    metadata_store = Mock()
    embedding_provider = AsyncMock()
    matcher = AIMatcher(metadata_store, vector_store, embedding_provider)

    await matcher.match_with_vector("描述", [1.0, 0.0], collection_id=2)

    vector_store.query.assert_awaited_once_with(
        [1.0, 0.0], n_results=10, collection_id=2
    )
```

- [ ] **Step 4: 运行目标测试并确认失败**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_keyword_searcher.py \
  tests/unit/engine/test_random_searcher.py \
  tests/unit/engine/test_combined_searcher.py \
  tests/unit/engine/test_semantic_searcher.py \
  tests/unit/engine/test_ai_matcher.py \
  -q
```

Expected: 新断言或新签名测试 FAIL。

- [ ] **Step 5: 扩展 `SearchResult` 并集中构造**

```python
@dataclass(slots=True)
class SearchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
    collection_id: int = 0
    local_id: int = 1
    collection_name: str = GLOBAL_COLLECTION_NAME

    @property
    def public_id(self) -> MemePublicId:
        return MemePublicId(self.collection_id, self.local_id)

    @classmethod
    def from_entry(cls, entry: MemeEntry, similarity: float) -> "SearchResult":
        return cls(
            entry_id=entry.id,
            image_path=entry.image_path,
            text=entry.text,
            similarity=similarity,
            speaker=entry.speaker,
            tags=entry.tags,
            collection_id=entry.collection_id,
            local_id=entry.local_id,
            collection_name=entry.collection_name,
        )
```

把搜索器中的重复构造替换为 `SearchResult.from_entry(entry, score)`。

- [ ] **Step 6: 把 Random/Combined 改成纯子集搜索**

`RandomSearcher` 保留构造函数依赖以兼容现有调用，但 IndexManager 使用显式子集入口。将方法命名为 `search_random_in()`，避免改变旧 `search_random(keyword, limit)` 的位置参数语义：

```python
def search_random_in(
    self,
    entries: dict[int, MemeEntry],
    keyword: str | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    if keyword:
        candidates = [
            SearchResult.from_entry(entry, 0.0)
            for result in self.keyword_searcher.search_in(entries, keyword)
            if (entry := entries.get(result.entry_id)) is not None
        ]
    else:
        candidates = [
            SearchResult.from_entry(entry, 0.0)
            for entry in entries.values()
            if entry.text
        ]
    if len(candidates) <= limit:
        return candidates
    return random.sample(candidates, limit)
```

`CombinedSearcher` 新增：

```python
def search_in(
    self,
    entries: dict[int, MemeEntry],
    keyword: str | None,
    speakers: list[str],
    tags: list[str] | None = None,
) -> list[SearchResult]:
    tags = tags or []
    filtered = self._filter_entries(entries, speakers, tags)
    if keyword:
        return _shuffle_within_similarity_groups(
            self._keyword_searcher.search_in(filtered, keyword)
        )
    results = [SearchResult.from_entry(entry, 0.0) for entry in filtered.values()]
    random.shuffle(results)
    return results
```

保留 `RandomSearcher.search_random(keyword, limit)` 和 `CombinedSearcher.search(...)` 作为全库兼容薄包装，分别委托新 `search_random_in()` 与 `search_in()`；后续 IndexManager 只使用显式子集入口。

- [ ] **Step 7: 扩展 SemanticSearcher 和 AIMatcher**

`SemanticSearcher.search_semantic()` 增加：

```python
collection_id: int | None = None
```

并调用：

```python
hits = await self.vector_store.query(
    query_vector,
    n_results=limit,
    collection_id=collection_id,
)
```

`AIMatcher.match_with_vector()` 同样增加 keyword-only `collection_id`，传给 `query()`。扩展 `AIMatchCandidate` 和 `AIMatchResult`，加入 `collection_id`、`local_id`、`collection_name` 与 `public_id` 属性；从 `MemeEntry` 复制这些字段。

- [ ] **Step 8: 运行搜索器测试并更新文档**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_keyword_searcher.py \
  tests/unit/engine/test_random_searcher.py \
  tests/unit/engine/test_combined_searcher.py \
  tests/unit/engine/test_semantic_searcher.py \
  tests/unit/engine/test_ai_matcher.py \
  -q
uv run ty check
```

Expected: 全部 PASS。更新 `types.md`、五个搜索器文档、`ai_matcher.md` 和 `protocols.md`。

---

### Task 6: 将合集上下文接入 IndexManager、app_state 与启动流程

**Files:**
- Modify: `bot/engine/index_manager.py`
- Modify: `bot/app_state.py`
- Modify: `bot/bot.py`
- Modify: `tests/unit/engine/test_index_manager.py`
- Modify: `tests/unit/engine/test_index_manager_info.py`
- Modify: `tests/unit/test_app_state.py`
- Modify: `tests/unit/test_bot.py`
- Modify: `docs/api/bot/engine/index_manager.md`
- Modify: `docs/api/bot/app_state.md`
- Modify: `docs/api/bot/bot.md`

- [ ] **Step 1: 写 IndexManager 合集过滤测试**

```python
@pytest.mark.asyncio
async def test_search_uses_requested_collection(index_manager: IndexManager) -> None:
    store = index_manager._metadata_store
    first = store.create_collection("新三国")
    second = store.create_collection("甄嬛传")
    store.add("新三国/a.webp", "相同关键词", collection_id=first.id)
    store.add("甄嬛传/b.webp", "相同关键词", collection_id=second.id)

    results = await index_manager.search("关键词", collection_id=first.id)

    assert [str(result.public_id) for result in results] == ["1.1"]


@pytest.mark.asyncio
async def test_search_none_collection_uses_all_entries(
    index_manager: IndexManager,
) -> None:
    store = index_manager._metadata_store
    first = store.create_collection("新三国")
    second = store.create_collection("甄嬛传")
    store.add("新三国/a.webp", "相同关键词", collection_id=first.id)
    store.add("甄嬛传/b.webp", "相同关键词", collection_id=second.id)

    results = await index_manager.search("关键词", collection_id=None)

    assert {result.collection_id for result in results} == {1, 2}
```

- [ ] **Step 2: 写选择解析和 `/info` 范围测试**

```python
@pytest.mark.asyncio
async def test_resolve_entry_uses_scope_short_id(
    index_manager: IndexManager,
    collection_manager: CollectionManager,
) -> None:
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    collection = index_manager._metadata_store.create_collection("新三国")
    index_manager._metadata_store.add(
        "新三国/a.webp", "文本", collection_id=collection.id
    )
    collection_manager.set_selected(scope, collection.id)

    entry = await index_manager.resolve_entry(scope, "001")

    assert entry.public_id == MemePublicId(1, 1)


@pytest.mark.asyncio
async def test_info_ranks_speakers_in_selected_range(
    index_manager: IndexManager,
) -> None:
    first = index_manager._metadata_store.create_collection("新三国")
    second = index_manager._metadata_store.create_collection("甄嬛传")
    index_manager._metadata_store.add(
        "新三国/a.webp", "甲", speaker="曹操", collection_id=first.id
    )
    index_manager._metadata_store.add(
        "甄嬛传/b.webp", "乙", speaker="皇后", collection_id=second.id
    )

    info = await index_manager.info(collection_id=first.id)

    assert info.entry_count == 2
    assert info.current_entry_count == 1
    assert info.collection_count == 2
    assert info.speaker_ranking == [("曹操", 1)]
```

- [ ] **Step 3: 运行目标测试并确认失败**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_index_manager.py \
  tests/unit/engine/test_index_manager_info.py \
  -k "collection or selected or scope" -q
```

Expected: FAIL，原因是 IndexManager 尚未接收 `CollectionManager` 或合集参数。

- [ ] **Step 4: 注入 CollectionManager 并增加读接口**

`IndexManager.__init__` 增加尾部可选参数，保持现有测试替身和外部构造兼容；生产启动流程仍显式注入同一个实例：

```python
collection_manager: CollectionManager | None = None
```

保存为：

```python
self._collection_manager = collection_manager or CollectionManager(metadata_store)
```

在 `tests/unit/engine/test_index_manager.py` 的 `FakeMetadataStore` 增加 `_collections`、`_entries_by_collection`、`get_entries()`、合集 CRUD、局部编号、ChatScope 选择方法；`FakeVectorStore` 增加 `collection_ids`、`update_collection_id()`、`get_collection_ids()`，并让 `upsert/query/rebuild_all` 接受 Task 4 的新参数。新增方法沿用 Task 2–4 给出的完整签名和语义，不为 fake 另造接口。

其他专用 Fake Store 文件只需更新已实现方法的签名：

- `tests/unit/engine/test_index_manager_add_tags.py`
- `tests/unit/engine/test_index_manager_delete.py`
- `tests/unit/engine/test_index_manager_info.py`
- `tests/integration/test_index_manager_api.py`
- `tests/integration/test_ai_matcher_api.py`

对这些 fake：`get_id_by_text(..., collection_id=0)` 只在对应合集查找；`upsert(..., collection_id=0)` 记录 metadata；`query(..., collection_id=None)` 在非 `None` 时过滤；`rebuild_all()` 接收三元组。然后增加：

```python
async def get_selected_collection(self, scope: ScopeLike) -> CollectionSelection:
    async with self._rwlock.read(timeout=self.read_timeout):
        return await asyncio.to_thread(self._collection_manager.get_selected, scope)


async def list_collections(self, scope: ScopeLike) -> list[CollectionSummary]:
    async with self._rwlock.read(timeout=self.read_timeout):
        return await asyncio.to_thread(self._collection_manager.list_summaries, scope)


async def switch_collection(
    self, scope: ScopeLike, target: str
) -> CollectionSelection:
    async with self._rwlock.read(timeout=self.read_timeout):
        selection = await asyncio.to_thread(
            self._collection_manager.resolve_selection, target
        )
        await asyncio.to_thread(
            self._collection_manager.set_selected,
            scope,
            selection.collection_id,
        )
        return selection


async def resolve_collection_selection(
    self, target: str
) -> CollectionSelection:
    """持读锁解析 `/mv` 的目标合集，避免与刷新删除并发。"""
    async with self._rwlock.read(timeout=self.read_timeout):
        return await asyncio.to_thread(
            self._collection_manager.resolve_selection, target
        )


async def resolve_entry(self, scope: ScopeLike, raw_id: str) -> MemeEntry:
    async with self._rwlock.read(timeout=self.read_timeout):
        selection = await asyncio.to_thread(self._collection_manager.get_selected, scope)
        public_id = self._collection_manager.parse_meme_id(
            raw_id,
            selected_collection_id=selection.collection_id,
        )
        entry = await asyncio.to_thread(
            self._metadata_store.get_entry_by_public_id, public_id
        )
        if entry is None:
            raise MemeNotFoundError(str(public_id))
        return entry
```

在 `collection_manager.py` 增加 `MemeNotFoundError`。

- [ ] **Step 5: 给所有搜索入口增加 `collection_id`**

签名：

```python
async def search(
    self, keyword: str, *, collection_id: int | None = None
) -> list[SearchResult]: ...

async def random_search(
    self, keyword: str | None = None, *, collection_id: int | None = None
) -> list[SearchResult]: ...

async def search_combined(
    self,
    keyword: str | None,
    speakers: list[str],
    tags: list[str],
    *,
    collection_id: int | None = None,
) -> list[SearchResult]: ...

async def semantic_search(
    self,
    description: str,
    limit: int | None = 10,
    *,
    collection_id: int | None = None,
) -> list[SearchResult]: ...

async def ai_match(
    self, description: str, *, collection_id: int | None = None
) -> AIMatchResult | None: ...
```

关键词、随机和组合检索先取：

```python
entries = self._metadata_store.get_entries(collection_id)
```

再调用 `KeywordSearcher.search_in(entries, keyword)`、`RandomSearcher.search_random_in(entries, keyword)` 或 `CombinedSearcher.search_in(entries, keyword, speakers, tags)`。语义和 AI 把 `collection_id` 传给 Chroma 查询链。

- [ ] **Step 6: 扩展 IndexInfo**

```python
@dataclass(slots=True)
class IndexInfo:
    entry_count: int
    current_entry_count: int
    collection_count: int
    speaker_ranking: list[tuple[str | None, int]]
    status: str
```

`info(collection_id)` 从 `get_entries(collection_id)` 统计当前范围 speaker，从 `entry_count()` 取全库总数，从 `list_collections()` 取普通合集数。

- [ ] **Step 7: 在启动流程构造和注册 CollectionManager**

`bot/bot.py`：

```python
metadata_store = MetadataStore(str(INDEX_DB_PATH))
vector_store = VectorStore(str(CHROMA_DIR))
collection_manager = CollectionManager(metadata_store)

ai_matcher = AIMatcher(
    metadata_store=metadata_store,
    vector_store=vector_store,
    embedding_provider=embedding_service,
    rerank_provider=rerank_service,
)
keyword_searcher = KeywordSearcher(metadata_store)
random_searcher = RandomSearcher(metadata_store, keyword_searcher)
semantic_searcher = SemanticSearcher(metadata_store, vector_store)
combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)

index_manager = IndexManager(
    metadata_store=metadata_store,
    vector_store=vector_store,
    collection_manager=collection_manager,
    memes_dir=str(MEMES_DIR),
    deleted_dir=str(MEMES_DELETED_DIR),
    replaced_dir=str(MEMES_REPLACED_DIR),
    ocr_provider=ocr_service,
    embedding_provider=embedding_service,
    optimizer=image_optimizer,
    keyword_searcher=keyword_searcher,
    ai_matcher=ai_matcher,
    random_searcher=random_searcher,
    semantic_searcher=semantic_searcher,
    combined_searcher=combined_searcher,
)
```

`app_state.init_app()` 增加 `collection_manager` 参数和 `get_collection_manager()`。启动注册调用明确传入：

```python
init_app(
    index_manager=index_manager,
    metadata_store=metadata_store,
    vector_store=vector_store,
    ocr_service=ocr_service,
    embedding_service=embedding_service,
    collection_manager=collection_manager,
    image_optimizer=image_optimizer,
    ai_matcher=ai_matcher,
    keyword_searcher=keyword_searcher,
    random_searcher=random_searcher,
    semantic_searcher=semantic_searcher,
    combined_searcher=combined_searcher,
)
```

更新 `tests/unit/test_app_state.py` 的初始化、覆盖初始化和未初始化异常断言。

- [ ] **Step 8: 运行 IndexManager、app_state、bot 测试**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_index_manager.py \
  tests/unit/engine/test_index_manager_info.py \
  tests/unit/test_app_state.py \
  tests/unit/test_bot.py \
  -q
uv run ty check
```

Expected: 全部 PASS。

- [ ] **Step 9: 更新 API 文档并检查**

更新 IndexManager、app_state、bot 文档的构造签名、搜索参数、选择解析和 IndexInfo。

Run:

```bash
uv run ruff check bot/engine/index_manager.py bot/app_state.py bot/bot.py
uv run ruff format --check bot/engine/index_manager.py bot/app_state.py bot/bot.py
git diff --check
```

Expected: 全部成功，不提交。

---

### Task 7: 实现递归扫描、合集生命周期与合集感知刷新

**Files:**
- Modify: `bot/engine/index_manager.py`
- Modify: `tests/unit/engine/test_index_manager.py`
- Modify: `tests/unit/plugins/test_refresh.py`
- Modify: `bot/plugins/refresh.py`
- Modify: `docs/api/bot/engine/index_manager.md`
- Modify: `docs/api/bot/plugins/refresh.md`

- [ ] **Step 1: 写递归扫描和隐藏/符号链接测试**

```python
def test_scan_assigns_nested_files_to_first_directory(
    index_manager: IndexManager, tmp_path: Path
) -> None:
    (tmp_path / "root.webp").write_bytes(b"root")
    nested = tmp_path / "新三国" / "截图"
    nested.mkdir(parents=True)
    (nested / "a.webp").write_bytes(b"nested")
    hidden = tmp_path / "新三国" / ".cache"
    hidden.mkdir()
    (hidden / "ignored.webp").write_bytes(b"hidden")

    snapshot = index_manager._scan_meme_files()

    assert snapshot.files == {
        "root.webp": None,
        "新三国/截图/a.webp": "新三国",
    }
    assert snapshot.directories == {"新三国"}
    assert snapshot.directories_with_images == {"新三国"}
```

在支持符号链接的平台增加：

```python
def test_scan_skips_symlinked_files_and_directories(
    index_manager: IndexManager, tmp_path: Path
) -> None:
    outside = tmp_path.parent / "outside.webp"
    outside.write_bytes(b"outside")
    (tmp_path / "linked.webp").symlink_to(outside)

    snapshot = index_manager._scan_meme_files()

    assert "linked.webp" not in snapshot.files
```

- [ ] **Step 2: 写合集登记、空合集保留和目录删除测试**

```python
@pytest.mark.asyncio
async def test_refresh_creates_collection_only_for_directory_with_image(
    index_manager: IndexManager, tmp_path: Path
) -> None:
    (tmp_path / "空目录").mkdir()
    with_image = tmp_path / "新三国"
    with_image.mkdir()
    (with_image / "a.webp").write_bytes(b"image")

    await index_manager.refresh()

    assert index_manager._metadata_store.get_collection_by_name("空目录") is None
    assert index_manager._metadata_store.get_collection_by_name("新三国").id == 1


@pytest.mark.asyncio
async def test_refresh_deletes_missing_collection_and_resets_scopes(
    index_manager: IndexManager, tmp_path: Path
) -> None:
    directory = tmp_path / "新三国"
    directory.mkdir()
    collection = index_manager._metadata_store.create_collection("新三国")
    index_manager._metadata_store.set_selected_collection(
        1, "private", 1, collection.id
    )
    directory.rmdir()

    result = await index_manager.refresh()

    assert result.collections_deleted == 1
    assert result.scopes_reset == 1
    assert index_manager._metadata_store.get_collection(collection.id) is None
```

- [ ] **Step 3: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager.py -k "scan or collection" -q
```

Expected: FAIL，现有扫描只返回根目录文件名。

- [ ] **Step 4: 定义文件系统快照和刷新统计**

在 `index_manager.py` 增加：

```python
@dataclass(frozen=True, slots=True)
class FileSystemSnapshot:
    files: dict[str, str | None]
    directories: set[str]
    directories_with_images: set[str]


@dataclass(slots=True)
class SyncResult:
    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    collections_added: int = 0
    collections_deleted: int = 0
    scopes_reset: int = 0
    failed: list[str] = field(default_factory=list)
```

- [ ] **Step 5: 实现不跟随链接的递归扫描**

```python
def _scan_meme_files(self) -> FileSystemSnapshot:
    files: dict[str, str | None] = {}
    directories: set[str] = set()
    directories_with_images: set[str] = set()

    for entry in os.scandir(self._memes_dir):
        if entry.is_symlink():
            continue
        if entry.is_file() and Path(entry.name).suffix.lower() in self.SUPPORTED_EXTENSIONS:
            files[entry.name] = None
            continue
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        directories.add(entry.name)
        for root, dir_names, file_names in os.walk(entry.path, followlinks=False):
            dir_names[:] = [
                name
                for name in dir_names
                if not name.startswith(".")
                and not (Path(root) / name).is_symlink()
            ]
            for filename in file_names:
                path = Path(root) / filename
                if path.is_symlink() or path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                    continue
                relative_path = path.relative_to(self._memes_dir).as_posix()
                files[relative_path] = entry.name
                directories_with_images.add(entry.name)

    return FileSystemSnapshot(files, directories, directories_with_images)
```

- [ ] **Step 6: 重排刷新阶段**

`_run_sync_internal()` 按以下顺序调用具体方法：

```python
snapshot = self._scan_meme_files()
await self._sync_phase0_consistency(failed)
deleted_count = await self._sync_phase1_delete(set(snapshot.files))
collections_deleted, scopes_reset = await self._sync_collections_delete(
    snapshot.directories
)
collections_added = await self._sync_collections_add(
    snapshot.directories_with_images
)
added, deduped, no_text = await self._sync_phase2_add(snapshot.files, failed)
```

新增：

```python
async def _sync_collections_delete(
    self, existing_directories: set[str]
) -> tuple[int, int]:
    deleted = reset = 0
    for collection in self._metadata_store.list_collections():
        if collection.name in existing_directories:
            continue
        reset += await asyncio.to_thread(
            self._metadata_store.delete_collection_and_reset_scopes,
            collection.id,
        )
        deleted += 1
    return deleted, reset


async def _sync_collections_add(self, directories_with_images: set[str]) -> int:
    added = 0
    for name in sorted(directories_with_images):
        if self._metadata_store.get_collection_by_name(name) is not None:
            continue
        await asyncio.to_thread(self._metadata_store.create_collection, name)
        added += 1
    return added
```

`_sync_phase2_add()` 接收 `dict[str, str | None]`，从目录名查合集编号，调用 collection-aware `add()` 和 Chroma `upsert(..., collection_id=...)`。

- [ ] **Step 7: 修正嵌套路径优化管线**

`_process_image_pipeline(relative_path)` 不得丢失父目录：

```python
image_path = self._memes_dir / relative_path
result = await self._optimizer.optimize(str(image_path))
final_path = Path(result.output_path)
final_relative_path = final_path.relative_to(self._memes_dir).as_posix()
```

返回 `(final_relative_path, text, embedding)`。并发 WebP 同名处理在各自父目录内解决冲突。

- [ ] **Step 8: 更新 Chroma 一致性阶段**

在现有 ID 对齐后读取：

```python
vector_collections = await self._vector_store.get_collection_ids()
for entry_id, entry in entries.items():
    if vector_collections.get(entry_id) != entry.collection_id:
        await self._vector_store.update_collection_id(
            entry_id, entry.collection_id
        )
```

全量 `rebuild_all` 项改为 `(entry_id, embedding, entry.collection_id)`。

- [ ] **Step 9: 更新 `/refresh` 回复**

在 `bot/plugins/refresh.py` 的摘要加入：

```python
f"新增合集：{result.collections_added}\n"
f"删除合集：{result.collections_deleted}\n"
f"回退窗口：{result.scopes_reset}\n"
```

- [ ] **Step 10: 运行刷新测试和文档检查**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_index_manager.py \
  tests/unit/plugins/test_refresh.py \
  -q
uv run ty check
```

Expected: 全部 PASS。更新 IndexManager 与 refresh API 文档。

---

### Task 8: 让 `/add` 引擎按目标合集写入和去重

**Files:**
- Modify: `bot/engine/index_manager.py`
- Modify: `tests/unit/engine/test_index_manager.py`
- Modify: `tests/unit/engine/test_index_manager_add_tags.py`
- Modify: `docs/api/bot/engine/index_manager.md`

- [ ] **Step 1: 写合集内新增与跨合集同文本测试**

```python
@pytest.mark.asyncio
async def test_add_assigns_public_id_in_target_collection(
    index_manager: IndexManager
) -> None:
    collection = index_manager._metadata_store.create_collection("新三国")
    path = index_manager._memes_dir / "新三国" / "a.webp"
    path.parent.mkdir()
    path.write_bytes(b"image")

    result = await index_manager.add(
        "新三国/a.webp",
        collection_id=collection.id,
    )

    assert result.public_id == MemePublicId(collection.id, 1)
    assert result.collection_name == "新三国"


@pytest.mark.asyncio
async def test_add_allows_same_text_in_different_collections(
    index_manager: IndexManager,
) -> None:
    first = index_manager._metadata_store.create_collection("新三国")
    second = index_manager._metadata_store.create_collection("甄嬛传")
    first_path = index_manager._memes_dir / "新三国" / "a.webp"
    second_path = index_manager._memes_dir / "甄嬛传" / "b.webp"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")

    first_result = await index_manager.add("新三国/a.webp", collection_id=first.id)
    second_result = await index_manager.add("甄嬛传/b.webp", collection_id=second.id)

    assert first_result.entry_id != second_result.entry_id
```

测试替身让 OCR 对两张图返回同一文本。

- [ ] **Step 2: 写目标合集被并发删除的测试**

```python
@pytest.mark.asyncio
async def test_add_rejects_collection_deleted_before_write(
    index_manager: IndexManager,
) -> None:
    collection = index_manager._metadata_store.create_collection("新三国")
    image_path = index_manager._memes_dir / "新三国" / "a.webp"
    image_path.parent.mkdir()
    image_path.write_bytes(b"image")
    index_manager._metadata_store.delete_collection_and_reset_scopes(collection.id)

    with pytest.raises(CollectionNotFoundError):
        await index_manager.add("新三国/a.webp", collection_id=collection.id)
    assert not image_path.exists()
```

- [ ] **Step 3: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager.py -k "add and collection" -q
```

Expected: FAIL，`add()` 不接受合集参数或结果没有公开 ID。

- [ ] **Step 4: 扩展 AddResult 与写请求**

```python
@dataclass(slots=True)
class AddResult:
    entry_id: int | None
    reason: str
    text: str = ""
    public_id: MemePublicId | None = None
    collection_name: str | None = None
    replaced_image_path: str | None = None
    archived_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

`_WriteRequest` 增加 `collection_id: int = 0`。`add()` 签名改为：

```python
async def add(
    self,
    relative_path: str,
    speaker: str | None = None,
    tags: list[str] | None = None,
    *,
    collection_id: int = 0,
) -> AddResult:
```

- [ ] **Step 5: 修改 `_write_entry`**

写锁内先校验：

```python
collection_name = GLOBAL_COLLECTION_NAME
if collection_id != 0:
    collection = self._metadata_store.get_collection(collection_id)
    if collection is None:
        (self._memes_dir / relative_path).unlink(missing_ok=True)
        raise CollectionNotFoundError(str(collection_id))
    collection_name = collection.name
```

去重改为：

```python
old_id = self._metadata_store.get_id_by_text(
    text,
    collection_id=collection_id,
)
```

新增改为：

```python
eid = self._metadata_store.add(
    relative_path,
    text,
    speaker,
    tags,
    collection_id=collection_id,
)
entry = self._metadata_store.get_entry(eid)
assert entry is not None
await self._vector_store.upsert(
    eid,
    embedding,
    collection_id=collection_id,
)
```

返回 `entry.public_id` 与 `entry.collection_name`。同合集替换保留旧 `local_id`；跨合集相同文本不会命中。

- [ ] **Step 6: 运行添加测试和回归测试**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_index_manager.py \
  tests/unit/engine/test_index_manager_add_tags.py \
  -q
uv run ty check
```

Expected: 全部 PASS。

- [ ] **Step 7: 更新 IndexManager API 文档并检查**

记录 `add(relative_path, ..., collection_id=0)`、AddResult 公开字段和合集内去重。

---

### Task 9: 在 IndexManager 实现移动预览与补偿式 `/mv`

**Files:**
- Modify: `bot/engine/index_manager.py`
- Modify: `bot/engine/utils.py`
- Modify: `tests/unit/engine/test_utils.py`
- Create: `tests/unit/engine/test_index_manager_move.py`
- Modify: `docs/api/bot/engine/index_manager.md`

- [ ] **Step 1: 写移动成功测试**

```python
"""IndexManager.move() 单元测试。"""

from pathlib import Path

import pytest
import pytest_asyncio

from bot.engine.collection_manager import CollectionManager
from bot.engine.index_manager import IndexManager
from bot.engine.metadata_store import MetadataStore
from bot.engine.types import MemePublicId
from bot.engine.vector_store import VectorStore


@pytest_asyncio.fixture
async def index_manager(tmp_path: Path):
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = MetadataStore(str(tmp_path / "index.db"))
    vector_store = VectorStore(str(tmp_path / "chroma"))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        collection_manager=CollectionManager(metadata_store),
        memes_dir=str(memes_dir),
    )
    await manager.load()
    try:
        yield manager
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_move_preserves_internal_id_and_moves_to_collection_root(
    index_manager: IndexManager,
) -> None:
    memes_dir = index_manager._memes_dir
    source = index_manager._metadata_store.create_collection("新三国")
    target = index_manager._metadata_store.create_collection("甄嬛传")
    source_path = memes_dir / "新三国" / "截图" / "a.webp"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"image")
    entry_id = index_manager._metadata_store.add(
        "新三国/截图/a.webp",
        "文本",
        collection_id=source.id,
    )
    await index_manager._vector_store.upsert(
        entry_id, [1.0, 0.0], collection_id=source.id
    )

    result = await index_manager.move(entry_id, target.id)

    assert result.entry_id == entry_id
    assert result.old_public_id == MemePublicId(source.id, 1)
    assert result.new_public_id == MemePublicId(target.id, 1)
    assert result.new_image_path == "甄嬛传/a.webp"
    assert not source_path.exists()
    assert (memes_dir / "甄嬛传" / "a.webp").exists()
```

- [ ] **Step 2: 写文本冲突和实际编号重算测试**

```python
@pytest.mark.asyncio
async def test_move_rejects_duplicate_text_in_target(
    index_manager: IndexManager,
) -> None:
    source = index_manager._metadata_store.create_collection("新三国")
    target = index_manager._metadata_store.create_collection("甄嬛传")
    source_id = index_manager._metadata_store.add(
        "新三国/a.webp", "相同", collection_id=source.id
    )
    conflict_id = index_manager._metadata_store.add(
        "甄嬛传/b.webp", "相同", collection_id=target.id
    )

    with pytest.raises(DuplicateMemeInCollectionError) as exc_info:
        await index_manager.move(source_id, target.id)

    assert exc_info.value.conflicting_entry_id == conflict_id


@pytest.mark.asyncio
async def test_move_recomputes_local_id_at_execution(
    index_manager: IndexManager,
) -> None:
    memes_dir = index_manager._memes_dir
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    source_path = memes_dir / "源" / "a.webp"
    source_path.parent.mkdir()
    source_path.write_bytes(b"source")
    source_id = index_manager._metadata_store.add(
        "源/a.webp", "源文本", collection_id=source.id
    )
    await index_manager._vector_store.upsert(
        source_id, [1.0, 0.0], collection_id=source.id
    )
    preview = await index_manager.preview_move(source_id, target.id)
    occupied_path = memes_dir / "目标" / "occupied.webp"
    occupied_path.parent.mkdir()
    occupied_path.write_bytes(b"occupied")
    index_manager._metadata_store.add(
        "目标/occupied.webp", "占位", collection_id=target.id
    )

    result = await index_manager.move(source_id, target.id)

    assert preview.expected_public_id == MemePublicId(target.id, 1)
    assert result.new_public_id == MemePublicId(target.id, 2)
```

- [ ] **Step 3: 写 SQLite/Chroma 失败补偿测试**

```python
@pytest.mark.asyncio
async def test_move_restores_file_and_metadata_when_vector_update_fails(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memes_dir = index_manager._memes_dir
    source = index_manager._metadata_store.create_collection("源")
    target = index_manager._metadata_store.create_collection("目标")
    source_path = memes_dir / "源" / "a.webp"
    source_path.parent.mkdir()
    source_path.write_bytes(b"source")
    source_id = index_manager._metadata_store.add(
        "源/a.webp", "文本", collection_id=source.id
    )
    await index_manager._vector_store.upsert(
        source_id, [1.0, 0.0], collection_id=source.id
    )
    old_entry = index_manager._metadata_store.get_entry(source_id)
    original_update = index_manager._vector_store.update_collection_id
    calls = 0

    async def fail_once(entry_id: int, collection_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("chroma failed")
        await original_update(entry_id, collection_id)

    monkeypatch.setattr(
        index_manager._vector_store,
        "update_collection_id",
        fail_once,
    )

    with pytest.raises(MemeMoveError):
        await index_manager.move(source_id, target.id)

    restored = index_manager._metadata_store.get_entry(source_id)
    assert restored == old_entry
    assert source_path.exists()
    assert await index_manager._vector_store.get_collection_ids() == {
        source_id: source.id
    }
```

- [ ] **Step 4: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager_move.py -q
```

Expected: collection 失败，移动 DTO、异常和方法尚不存在。

- [ ] **Step 5: 为移动目标定义 `_2` 起始的冲突命名**

保持归档目录现有 `_1` 行为，为 `resolve_unique_filename()` 增加 keyword-only 起始值：

```python
def resolve_unique_filename(
    target_dir: Path,
    filename: str,
    *,
    first_suffix: int = 1,
) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for number in itertools.count(first_suffix):
        candidate = target_dir / f"{stem}_{number}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")
```

在 `tests/unit/engine/test_utils.py` 增加：

```python
def test_resolve_unique_filename_can_start_from_two(tmp_path: Path) -> None:
    (tmp_path / "a.webp").write_bytes(b"existing")

    result = resolve_unique_filename(
        tmp_path, "a.webp", first_suffix=2
    )

    assert result == tmp_path / "a_2.webp"
```

`/mv` 和 `move-root` 传 `first_suffix=2`；删除和替换归档调用不传，保留 `_1`。

- [ ] **Step 6: 定义移动 DTO、异常和写操作**

```python
@dataclass(frozen=True, slots=True)
class MovePreview:
    entry_id: int
    old_public_id: MemePublicId
    source_collection_name: str
    target_collection_id: int
    target_collection_name: str
    expected_public_id: MemePublicId


@dataclass(frozen=True, slots=True)
class MoveResult:
    entry_id: int
    old_public_id: MemePublicId
    new_public_id: MemePublicId
    target_collection_name: str
    old_image_path: str
    new_image_path: str


class DuplicateMemeInCollectionError(RuntimeError):
    def __init__(self, conflicting_entry_id: int) -> None:
        self.conflicting_entry_id = conflicting_entry_id
        super().__init__(f"目标合集存在重复条目: {conflicting_entry_id}")


class MemeMoveError(RuntimeError):
    """移动文件或补偿跨存储写入失败。"""
```

`WriteOp` 增加 `MOVE`，`_WriteRequest` 增加 `target_collection_id`。

- [ ] **Step 7: 实现预览和提交入口**

```python
async def preview_move(
    self, entry_id: int, target_collection_id: int
) -> MovePreview:
    async with self._rwlock.read(timeout=self.read_timeout):
        entry = self._metadata_store.get_entry(entry_id)
        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")
        target_name = GLOBAL_COLLECTION_NAME
        if target_collection_id != 0:
            target = self._metadata_store.get_collection(target_collection_id)
            if target is None:
                raise CollectionNotFoundError(str(target_collection_id))
            target_name = target.name
        if entry.collection_id == target_collection_id:
            raise ValueError("表情包已属于目标合集")
        local_id = self._metadata_store.find_next_local_id(target_collection_id)
        return MovePreview(
            entry_id=entry.id,
            old_public_id=entry.public_id,
            source_collection_name=entry.collection_name,
            target_collection_id=target_collection_id,
            target_collection_name=target_name,
            expected_public_id=MemePublicId(target_collection_id, local_id),
        )
```

`move()` 把请求放进现有 Write Worker，确认执行时由 `_execute_move()` 重算局部 ID。

- [ ] **Step 8: 实现写锁内移动和补偿**

关键步骤：

```python
old_entry = self._metadata_store.get_entry(req.entry_id)
if old_entry is None:
    raise ValueError(f"entry_id={req.entry_id} 不存在")
conflict_id = self._metadata_store.get_id_by_text(
    old_entry.text,
    collection_id=req.target_collection_id,
)
if conflict_id is not None:
    raise DuplicateMemeInCollectionError(conflict_id)

local_id = self._metadata_store.find_next_local_id(req.target_collection_id)
target_name = GLOBAL_COLLECTION_NAME
target_dir = self._memes_dir
if req.target_collection_id != 0:
    target = self._metadata_store.get_collection(req.target_collection_id)
    if target is None:
        raise CollectionNotFoundError(str(req.target_collection_id))
    target_name = target.name
    target_dir = self._memes_dir / target.name

target_dir.mkdir(parents=True, exist_ok=True)
source_path = self._memes_dir / old_entry.image_path
target_path = resolve_unique_filename(
    target_dir, source_path.name, first_suffix=2
)
shutil.move(str(source_path), str(target_path))
new_relative_path = target_path.relative_to(self._memes_dir).as_posix()
```

随后：

```python
try:
    self._metadata_store.update(
        old_entry.id,
        image_path=new_relative_path,
        collection_id=req.target_collection_id,
        local_id=local_id,
    )
    await self._vector_store.update_collection_id(
        old_entry.id, req.target_collection_id
    )
except Exception as exc:
    try:
        self._metadata_store.update(
            old_entry.id,
            image_path=old_entry.image_path,
            collection_id=old_entry.collection_id,
            local_id=old_entry.local_id,
        )
        if target_path.exists():
            source_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target_path), str(source_path))
        await self._vector_store.update_collection_id(
            old_entry.id, old_entry.collection_id
        )
    except Exception:
        logger.critical("移动补偿失败: entry_id=%s", old_entry.id, exc_info=True)
    raise MemeMoveError(str(exc)) from exc
```

成功后重新读取 entry，构造 `MoveResult`。

- [ ] **Step 9: 运行移动测试并更新文档**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager_move.py -q
uv run ty check
```

Expected: 全部 PASS。更新 IndexManager API 文档中的 DTO、异常、预览和移动契约。

---

### Task 10: 建立插件公开 ID 适配并迁移现有管理命令

**Files:**
- Create: `bot/plugins/_collection_utils.py`
- Create: `docs/api/bot/plugins/_collection_utils.md`
- Modify: `docs/api/API.md`
- Modify: `bot/plugins/_search_utils.py`
- Modify: `bot/plugins/addtag.py`
- Modify: `bot/plugins/delete.py`
- Modify: `bot/plugins/edit.py`
- Modify: `bot/plugins/setspeaker.py`
- Modify: `bot/plugins/info.py`
- Modify: corresponding plugin tests and API docs

- [ ] **Step 1: 写共享解析与元数据格式测试**

创建共享测试或在 `test_search_utils.py` 增加：

```python
def test_format_metadata_line_uses_public_id_and_collection() -> None:
    assert format_metadata_line(
        MemePublicId(1, 3),
        "新三国",
        "曹操",
        ["吐槽"],
    ) == "1.3, 新三国, 曹操, 吐槽"


def test_format_global_metadata_line() -> None:
    assert format_metadata_line(
        MemePublicId(0, 42),
        "全局",
        None,
        [],
    ) == "0.42, 全局, 无"
```

为 management plugin 增加前导零、普通合集短号、合集 0 短号拒绝测试。例如 `test_info.py`：

```python
@pytest.mark.asyncio
async def test_info_accepts_leading_zero_public_id() -> None:
    event = make_private_event("/info 01.003")
    await handle_info(bot, event, matcher, Message("01.003"))

    index_manager.resolve_entry.assert_awaited_once_with(
        ChatScope.from_event(event), "01.003"
    )
```

- [ ] **Step 2: 运行插件目标测试并确认失败**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_search_utils.py \
  tests/unit/plugins/test_addtag.py \
  tests/unit/plugins/test_delete.py \
  tests/unit/plugins/test_edit.py \
  tests/unit/plugins/test_setspeaker.py \
  tests/unit/plugins/test_info.py \
  -q
```

Expected: 新格式和解析断言 FAIL。

- [ ] **Step 3: 创建 `_collection_utils.py`**

```python
"""插件层共享的合集和公开 ID 适配。"""

from nonebot.adapters.onebot.v11 import MessageEvent

from bot.app_state import get_index_manager
from bot.engine.collection_manager import (
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.engine.metadata_store import MemeEntry
from bot.session import ChatScope


async def resolve_entry_argument(event: MessageEvent, raw_id: str) -> MemeEntry:
    """按当前 ChatScope 解析用户输入并读取条目。"""
    scope = ChatScope.from_event(event)
    return await get_index_manager().resolve_entry(scope, raw_id)


def public_id_error_message(exc: ValueError) -> str:
    """把公开 ID 领域异常转换为用户提示。"""
    if isinstance(exc, ShortIdUnavailableError):
        return "全部合集模式下请使用完整 ID，例如 1.3"
    if isinstance(exc, InvalidPublicIdError):
        return "表情包 ID 格式错误，请使用“合集编号.局部编号”，例如 1.3"
    if isinstance(exc, MemeNotFoundError):
        return f"未找到 ID 为 {exc} 的表情包"
    return "表情包 ID 无效"
```

- [ ] **Step 4: 修改元数据格式函数**

```python
def format_metadata_line(
    public_id: MemePublicId,
    collection_name: str,
    speaker: str | None,
    tags: list[str],
) -> str:
    parts = [str(public_id), collection_name, speaker or "无", *tags]
    return ", ".join(parts)
```

所有调用传 `result.public_id` 与 `result.collection_name`。

- [ ] **Step 5: 迁移单 ID 管理命令**

在 `addtag.py`、`edit.py`、`setspeaker.py`、`info.py`：

```python
raw_id = parts[0]
try:
    entry = await resolve_entry_argument(event, raw_id)
except (InvalidPublicIdError, ShortIdUnavailableError, MemeNotFoundError) as exc:
    session_manager.deactivate_chat(scope)
    await reply_utils.finish(event, matcher, public_id_error_message(exc))
    return
entry_id = entry.id
public_id = entry.public_id
```

确认消息和成功/失败提示使用 `public_id`。matcher state 同时保存：

```python
matcher.state["entry_id"] = entry.id
matcher.state["public_id"] = entry.public_id
```

- [ ] **Step 6: 迁移 `/del` 批量混合 ID**

逐 token 调用 `resolve_entry_argument()`；按 `entry.id` 去重并保持顺序。matcher state 保存：

```python
matcher.state["entry_ids"] = [entry.id for entry in entries]
matcher.state["public_ids"] = {
    entry.id: entry.public_id for entry in entries
}
```

确认执行后，即使 SQLite 行已删除，也从快照映射格式化成功和失败列表。竞态导致 not-found 时同样使用快照。

- [ ] **Step 7: 运行插件测试**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_search_utils.py \
  tests/unit/plugins/test_addtag.py \
  tests/unit/plugins/test_delete.py \
  tests/unit/plugins/test_edit.py \
  tests/unit/plugins/test_setspeaker.py \
  tests/unit/plugins/test_info.py \
  -q
```

Expected: 全部 PASS。

- [ ] **Step 8: 更新 API 文档并检查**

创建 `_collection_utils.md`，更新 API 索引和五个命令文档。运行 ruff、ty、`git diff --check`。

---

### Task 11: 实现 `/switch` 命令

**Files:**
- Create: `bot/plugins/switch.py`
- Create: `tests/unit/plugins/test_switch.py`
- Create: `docs/api/bot/plugins/switch.md`
- Modify: `docs/api/API.md`
- Modify: `bot/plugins/_help_text.py`
- Modify: `tests/unit/plugins/test_help.py`
- Modify: `docs/api/bot/plugins/_help_text.md`

- [ ] **Step 1: 写无参数列表测试**

```python
@pytest.mark.asyncio
async def test_switch_without_argument_lists_collections() -> None:
    manager = AsyncMock()
    manager.list_collections.return_value = [
        CollectionSummary(0, "全部合集", 10, True),
        CollectionSummary(1, "新三国", 4, False),
    ]

    await handle_switch(bot, event, matcher, Message(""))

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "* 0. 全部合集（共 10 张）" in text
    assert "  1. 新三国（4 张）" in text
    assert "当前合集：全部合集" in text
```

- [ ] **Step 2: 写编号、名称、群聊与会话互斥测试**

```python
@pytest.mark.asyncio
async def test_switch_uses_full_remaining_text_as_name() -> None:
    await handle_switch(bot, event, matcher, Message("合集 名称"))

    index_manager.switch_collection.assert_awaited_once_with(
        ChatScope.from_event(event), "合集 名称"
    )


@pytest.mark.asyncio
async def test_switch_rejects_active_session() -> None:
    session_manager.activate_chat(scope, "search", old_matcher)

    await handle_switch(bot, event, matcher, Message("1"))

    assert "请先 /cancel" in extract_message_text(matcher.finish.await_args.args[0])
```

群聊测试断言 reply segment 存在。

- [ ] **Step 3: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/plugins/test_switch.py -q
```

Expected: collection 失败，插件不存在。

- [ ] **Step 4: 实现 `/switch`**

核心注册与处理：

```python
switch_cmd = on_command("switch", rule=to_me(), priority=5, block=True)


@switch_cmd.handle()
async def handle_switch(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)
    if not is_authorized(user_id):
        log_unauthorized(user_id, "switch")
        await matcher.finish(None)
        return
    if not session_manager.activate_chat(scope, "switch", matcher):
        await reply_utils.finish(event, matcher, "已有命令在处理中，请先 /cancel")
        return
    try:
        target = args.extract_plain_text().strip()
        if not target:
            summaries = await get_index_manager().list_collections(scope)
            await reply_utils.finish(
                event, matcher, _format_collection_list(summaries)
            )
            return
        selection = await get_index_manager().switch_collection(scope, target)
        if selection.collection_id == 0:
            message = "已切换到：全部合集（0）"
        else:
            message = (
                f"已切换到合集：{selection.name}"
                f"（{selection.collection_id}）"
            )
        await reply_utils.finish(event, matcher, message)
    except CollectionNotFoundError:
        await reply_utils.finish(
            event,
            matcher,
            f"未找到表情包合集：{target}\n发送 /switch 查看可用合集",
        )
    except asyncio.TimeoutError:
        await reply_utils.finish(event, matcher, "索引更新较慢，请稍后再试")
    finally:
        session_manager.deactivate_chat(scope)
```

`_format_collection_list()` 按设计格式输出 `0` 全库总数、普通合集数量和 `*`。

- [ ] **Step 5: 更新帮助文本**

在 `/refresh` 前加入：

```text
/switch [合集编号|名称]：查看或切换表情包合集
/mv <id> <目标合集编号|名称>：移动表情包（需确认）
```

此时 `/mv` 尚未加载，但帮助文本先锁定最终接口；Task 13 会创建插件。

- [ ] **Step 6: 运行 switch/help 测试并更新文档**

Run:

```bash
uv run pytest tests/unit/plugins/test_switch.py tests/unit/plugins/test_help.py -q
uv run ty check
```

Expected: 全部 PASS。创建 `switch.md` 并更新 API 索引和 `_help_text.md`。

---

### Task 12: 让所有搜索入口使用当前合集并更新展示与 `/info`

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Modify: `bot/plugins/plain_text.py`
- Modify: `bot/plugins/query.py`
- Modify: `bot/plugins/rand.py`
- Modify: `bot/plugins/sim.py`
- Modify: `bot/plugins/ai.py`
- Modify: `bot/plugins/info.py`
- Modify: corresponding tests and API docs

- [ ] **Step 1: 写普通文本和 `/query` 过滤参数测试**

```python
@pytest.mark.asyncio
async def test_execute_search_uses_current_collection() -> None:
    index_manager.get_selected_collection.return_value = CollectionSelection(
        1, "新三国", 1
    )

    await execute_search(bot, event, matcher, "关键词")

    index_manager.search.assert_awaited_once_with("关键词", collection_id=1)


@pytest.mark.asyncio
async def test_query_zero_selection_uses_all_collections() -> None:
    index_manager.get_selected_collection.return_value = CollectionSelection(
        0, "全部合集", None
    )

    await handle_query(bot, event, matcher, Message("关键词"))

    index_manager.search_combined.assert_awaited_once_with(
        "关键词", [], [], collection_id=None
    )
```

- [ ] **Step 2: 写 `/rand`、`/sim`、`/ai` 过滤测试**

每个测试 mock `get_selected_collection()`，断言：

```python
index_manager.random_search.assert_awaited_once_with(
    keyword,
    collection_id=selection.search_filter,
)
index_manager.semantic_search.assert_awaited_once_with(
    description,
    limit=None,
    collection_id=selection.search_filter,
)
index_manager.ai_match.assert_awaited_once_with(
    description,
    collection_id=selection.search_filter,
)
```

- [ ] **Step 3: 写 `/info` 当前范围统计测试**

```python
@pytest.mark.asyncio
async def test_info_displays_total_and_current_collection_counts() -> None:
    index_manager.get_selected_collection.return_value = CollectionSelection(
        1, "新三国", 1
    )
    index_manager.info.return_value = IndexInfo(
        entry_count=100,
        current_entry_count=30,
        collection_count=3,
        speaker_ranking=[("曹操", 10)],
        status="空闲",
    )

    await handle_info(bot, event, matcher, Message(""))

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "表情包总数：100" in text
    assert "当前合集：新三国（30 张）" in text
    assert "普通合集数：3" in text
```

- [ ] **Step 4: 运行搜索插件测试并确认失败**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_plain_text.py \
  tests/unit/plugins/test_query.py \
  tests/unit/plugins/test_rand.py \
  tests/unit/plugins/test_sim.py \
  tests/unit/plugins/test_ai.py \
  tests/unit/plugins/test_info.py \
  tests/unit/plugins/test_search_utils.py \
  -q
```

Expected: 新过滤参数与展示断言 FAIL。

- [ ] **Step 5: 在每个入口读取一次 CollectionSelection**

统一模式：

```python
scope = ChatScope.from_event(event)
selection = await get_index_manager().get_selected_collection(scope)
```

把 `selection.search_filter` 传入对应 IndexManager 方法。`/rand` 换一批时把 `collection_id` 存进 matcher state，确保同一次候选会话不因随后切换而漂移；会话互斥已阻止 `/switch`，state 仍作为显式快照。

- [ ] **Step 6: 更新候选和单图元数据格式**

`present_candidates()`、`dispatch_search_results()`、`handle_got_selection()` 和 AI/rand 单结果发送都调用：

```python
format_metadata_line(
    result.public_id,
    result.collection_name,
    result.speaker,
    result.tags,
)
```

列表前面的临时序号保持不变。

- [ ] **Step 7: 更新 `/info`**

无参数时先获取 selection，再调用：

```python
info = await index_manager.info(collection_id=selection.search_filter)
```

详情使用 `resolve_entry_argument()`，显示公开 ID、合集名称和完整相对路径。

- [ ] **Step 8: 运行插件测试并更新文档**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_plain_text.py \
  tests/unit/plugins/test_query.py \
  tests/unit/plugins/test_rand.py \
  tests/unit/plugins/test_sim.py \
  tests/unit/plugins/test_ai.py \
  tests/unit/plugins/test_info.py \
  tests/unit/plugins/test_search_utils.py \
  -q
uv run ty check
```

Expected: 全部 PASS。更新七个插件 API 文档和 `_search_utils.md`。

---

### Task 13: 让 `/add` 保存到当前合集并实现 `/mv` 插件

**Files:**
- Modify: `bot/plugins/add.py`
- Modify: `tests/unit/plugins/test_add.py`
- Create: `bot/plugins/move.py`
- Create: `tests/unit/plugins/test_move.py`
- Create: `docs/api/bot/plugins/move.md`
- Modify: `docs/api/bot/plugins/add.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 写 `/add` 目标路径测试**

```python
@pytest.mark.asyncio
async def test_add_saves_image_to_selected_collection() -> None:
    index_manager.get_selected_collection.return_value = CollectionSelection(
        1, "新三国", 1
    )

    await handle_add(bot, event, matcher, Message(""))
    await got_image(bot, image_event, matcher, image_message)

    saved_path = Path(http_download_target)
    assert saved_path.parent.name == "新三国"
    index_manager.add.assert_awaited_once()
    assert index_manager.add.await_args.kwargs["collection_id"] == 1
```

增加选择 `0` 时父目录为 `MEMES_DIR` 的测试。

- [ ] **Step 2: 写 `/mv` 确认、成功和错误测试**

```python
@pytest.mark.asyncio
async def test_move_confirmation_contains_expected_id_without_image() -> None:
    index_manager.resolve_entry.return_value = source_entry
    index_manager.preview_move.return_value = MovePreview(
        entry_id=42,
        old_public_id=MemePublicId(1, 3),
        source_collection_name="新三国",
        target_collection_id=2,
        target_collection_name="甄嬛传",
        expected_public_id=MemePublicId(2, 5),
    )

    await handle_move(bot, event, matcher, Message("1.3 甄嬛传"))

    text = extract_message_text(matcher.send.await_args.args[0])
    assert "源合集：新三国（1）" in text
    assert "预计新编号：2.5" in text
    assert not bot.send.called


@pytest.mark.asyncio
async def test_move_success_reports_actual_id() -> None:
    index_manager.move.return_value = MoveResult(
        entry_id=42,
        old_public_id=MemePublicId(1, 3),
        new_public_id=MemePublicId(2, 6),
        target_collection_name="甄嬛传",
        old_image_path="新三国/a.webp",
        new_image_path="甄嬛传/a.webp",
    )

    await got_confirm(bot, event, matcher, Message("确认"))

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "原编号：1.3" in text
    assert "新编号：2.6" in text
```

覆盖：群聊拒绝、短号在合集 0 拒绝、目标同合集、目标文本冲突、取消、超时、刷新锁。

- [ ] **Step 3: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/plugins/test_add.py tests/unit/plugins/test_move.py -q
```

Expected: `/add` 路径断言和 move 模块失败。

- [ ] **Step 4: 修改 `/add` 下载目录与调用**

`handle_add()` 保存 selection 快照：

```python
selection = await get_index_manager().get_selected_collection(scope)
matcher.state["collection_id"] = selection.collection_id
matcher.state["collection_name"] = selection.name
```

`got_image()` 选择目录：

```python
collection_id = int(matcher.state["collection_id"])
target_dir = MEMES_DIR
if collection_id != 0:
    target_dir = MEMES_DIR / str(matcher.state["collection_name"])
target_dir.mkdir(parents=True, exist_ok=True)
filepath = resolve_unique_filename(target_dir, filename)
relative_path = filepath.relative_to(MEMES_DIR).as_posix()
```

调用：

```python
result = await manager.add(
    relative_path,
    speaker,
    tags,
    collection_id=collection_id,
)
```

成功格式使用 `result.public_id` 和 `result.collection_name`。

- [ ] **Step 5: 实现 `/mv` 参数和确认入口**

```python
move_cmd = on_command("mv", rule=to_me(), priority=5, block=True)


@move_cmd.handle()
async def handle_move(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)
    if not is_authorized(user_id):
        log_unauthorized(user_id, "mv")
        await matcher.finish(None)
        return
    if event.message_type != "private":
        await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
        return
    if not session_manager.activate_chat(scope, "move", matcher):
        await reply_utils.finish(
            event, matcher, "已有命令在处理中，请先 /cancel"
        )
        return
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        await reply_utils.finish(
            event, matcher, "用法：/mv <id> <目标合集编号|名称>"
        )
        return
    source_raw, target_raw = parts
    source = await get_index_manager().resolve_entry(scope, source_raw)
    selection = await get_index_manager().resolve_collection_selection(target_raw)
    preview = await get_index_manager().preview_move(
        source.id, selection.collection_id
    )
    matcher.state["entry_id"] = source.id
    matcher.state["target_collection_id"] = selection.collection_id
    matcher.state["old_public_id"] = source.public_id
```

参数、公开 ID、目标合集或预览校验失败时，先 `session_manager.deactivate_chat(scope)` 再回复。成功进入确认状态后发送：

```python
await reply_utils.send(
    event,
    matcher,
    "确认移动表情包：\n"
    f"源合集：{preview.source_collection_name}"
    f"（{preview.old_public_id.collection_id}）\n"
    f"目标合集：{preview.target_collection_name}"
    f"（{preview.target_collection_id}）\n"
    f"当前编号：{preview.old_public_id}\n"
    f"预计新编号：{preview.expected_public_id}\n\n"
    "回复“确认”、yes 或 y 执行",
)
```

注册 `timeout_session(..., "移动已取消（超时）")`，并调用 `reset_current_task(scope)`。异常映射固定为：

- `InvalidPublicIdError` / `ShortIdUnavailableError` / `MemeNotFoundError`：`public_id_error_message()`；
- `CollectionNotFoundError`：`未找到表情包合集：{target_raw}`；
- 预览抛出的同合集 `ValueError`：`表情包已属于目标合集`；
- `asyncio.TimeoutError`：`索引更新较慢，请稍后再试`。

不能把原始异常文本发给用户。

- [ ] **Step 6: 实现确认处理器**

确认词为 `确认/yes/y`。执行：

```python
result = await get_index_manager().move(
    matcher.state["entry_id"],
    matcher.state["target_collection_id"],
)
```

确认处理器固定映射：

```python
except DuplicateMemeInCollectionError as exc:
    conflict = get_metadata_store().get_entry(exc.conflicting_entry_id)
    conflict_id = str(conflict.public_id) if conflict is not None else "未知"
    await reply_utils.finish(
        event,
        matcher,
        f"目标合集已存在相同内容的表情包：{conflict_id}",
    )
except MemeMoveError:
    await reply_utils.finish(
        event,
        matcher,
        "移动失败，索引将在下次刷新时检查一致性",
    )
except RefreshInProgressError:
    await reply_utils.finish(event, matcher, "索引正在刷新，请稍后再试")
except IndexAddCancelledError:
    await reply_utils.finish(event, matcher, "服务正在关闭，请稍后再试")
except asyncio.TimeoutError:
    await reply_utils.finish(event, matcher, "移动处理超时，请稍后再试")
else:
    await reply_utils.finish(
        event,
        matcher,
        "移动完成 ✅\n"
        f"原编号：{result.old_public_id}\n"
        f"新编号：{result.new_public_id}\n"
        f"目标合集：{result.target_collection_name}",
    )
finally:
    session_manager.deactivate_chat(scope)
```

- [ ] **Step 7: 运行插件测试并更新文档**

Run:

```bash
uv run pytest tests/unit/plugins/test_add.py tests/unit/plugins/test_move.py -q
uv run ty check
```

Expected: 全部 PASS。创建 `move.md`，更新 `add.md` 和 API 索引。

---

### Task 14: 实现 `upgrade-schema` 显式迁移子命令

**Files:**
- Create: `scripts/migrate_meme_collections.py`
- Create: `tests/unit/test_migrate_meme_collections.py`
- Modify: `bot/engine/vector_store.py`
- Modify: `bot/engine/protocols.py`
- Modify: `tests/unit/engine/test_vector_store.py`
- Modify: `docs/api/bot/engine/vector_store.md`
- Modify: `docs/api/bot/engine/protocols.md`

迁移脚本不是 Bot 对外 Python API，不在 `docs/api/API.md` 增加 scripts 条目；Task 17 在 README 和 PRD 记录 CLI。

- [ ] **Step 1: 写 dry-run 无副作用测试**

```python
@pytest.mark.asyncio
async def test_upgrade_schema_dry_run_has_no_side_effects(
    legacy_db: Path,
    tmp_path: Path,
) -> None:
    chroma_dir = tmp_path / "chroma"

    result = await run_upgrade_schema(legacy_db, chroma_dir, dry_run=True)

    assert result.upgraded_entries == 1
    assert not list(tmp_path.glob("index.db.*.bak"))
    with sqlite3.connect(legacy_db) as conn:
        assert "collection_id" not in {
            row[1] for row in conn.execute("PRAGMA table_info(meme)")
        }
```

- [ ] **Step 2: 写旧记录、标签和 Chroma metadata 迁移测试**

```python
@pytest.mark.asyncio
async def test_upgrade_schema_maps_old_id_to_global_public_id(
    legacy_db: Path,
    legacy_chroma: Path,
) -> None:
    # legacy_chroma fixture 必须用原生 collection.add(ids, embeddings)，
    # 不传 metadatas，模拟旧版本真实记录。
    result = await run_upgrade_schema(legacy_db, legacy_chroma, dry_run=False)

    assert result.upgraded_entries == 1
    with sqlite3.connect(legacy_db) as conn:
        row = conn.execute(
            "SELECT id, collection_id, local_id, image_path, text, speaker "
            "FROM meme"
        ).fetchone()
        tags = conn.execute("SELECT meme_id, tag FROM meme_tag").fetchall()
    assert row == (42, 0, 42, "a.webp", "文本", "曹操")
    assert tags == [(42, "吐槽")]
    vector_store = VectorStore(str(legacy_chroma))
    vector_store.load()
    try:
        assert await vector_store.get_collection_ids() == {42: 0}
    finally:
        vector_store.close()
```

- [ ] **Step 3: 写 Chroma 失败补偿测试**

```python
@pytest.mark.asyncio
async def test_upgrade_schema_rolls_back_sqlite_when_chroma_fails(
    legacy_db: Path,
    legacy_chroma: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VectorStore,
        "update_collection_id",
        AsyncMock(side_effect=RuntimeError("chroma failed")),
    )

    with pytest.raises(MigrationError):
        await run_upgrade_schema(legacy_db, legacy_chroma, dry_run=False)

    with sqlite3.connect(legacy_db) as conn:
        assert "collection_id" not in {
            row[1] for row in conn.execute("PRAGMA table_info(meme)")
        }
```

- [ ] **Step 4: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/test_migrate_meme_collections.py -k "upgrade" -q
```

Expected: collection 失败，迁移模块不存在。

- [ ] **Step 5: 创建 CLI、结果类型和 Schema 检测**

在脚本中定义：

```python
@dataclass(frozen=True, slots=True)
class UpgradeResult:
    upgraded_entries: int
    updated_vectors: int
    backup_path: Path | None
    already_current: bool = False


class MigrationError(RuntimeError):
    """显式迁移失败。"""


def detect_schema(conn: sqlite3.Connection) -> Literal["legacy", "current", "unknown"]:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "meme" not in tables:
        return "unknown"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(meme)")}
    if {"collection_id", "local_id"}.issubset(columns):
        return "current"
    if {"id", "image_path", "text", "speaker"}.issubset(columns):
        return "legacy"
    return "unknown"
```

CLI 使用 `argparse` subparser。`--db-path`、`--chroma-dir`、`--memes-dir` 和 `-v/--verbose` 定义在根 parser，写在子命令前；`--dry-run` 分别定义在两个子 parser，写在子命令后。默认路径从 `bot.config` 读取。主函数打印停止 Bot 警告，并用 `asyncio.run()` 调用异步迁移核心：

```python
def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    logger.warning("请确保 Bot 已停止运行；脚本不会自动检测 Bot 进程。")
    try:
        if args.command == "upgrade-schema":
            asyncio.run(
                run_upgrade_schema(args.db_path, args.chroma_dir, args.dry_run)
            )
        else:
            asyncio.run(
                run_move_root_paths(
                    args.db_path,
                    args.chroma_dir,
                    args.memes_dir,
                    args.target,
                    args.dry_run,
                )
            )
    except MigrationError as exc:
        logger.error("迁移失败：%s", exc)
        return 1
    return 0
```

`run_upgrade_schema()`、`run_move_root()` 和 `run_move_root_paths()` 均定义为 `async def`，测试直接 `await`，不得在已运行事件循环内嵌套 `asyncio.run()`。

- [ ] **Step 6: 实现 SQLite Backup 和事务迁移**

备份使用 SQLite Backup API。迁移复用 Task 2 的 `create_current_schema(conn)`，不复制 Schema SQL。执行顺序：

```python
conn.execute("PRAGMA foreign_keys = OFF")
conn.execute("BEGIN IMMEDIATE")
conn.execute("ALTER TABLE meme RENAME TO meme_legacy")
conn.execute("ALTER TABLE meme_tag RENAME TO meme_tag_legacy")
for index_name in (
    "idx_meme_image_path",
    "idx_meme_text",
    "idx_meme_tag_tag",
):
    conn.execute(f"DROP INDEX IF EXISTS {index_name}")
create_current_schema(conn)
conn.execute(
    "INSERT INTO meme "
    "(id, collection_id, local_id, image_path, text, speaker) "
    "SELECT id, 0, id, image_path, text, speaker FROM meme_legacy"
)
conn.execute(
    "INSERT INTO meme_tag (meme_id, tag) "
    "SELECT meme_id, tag FROM meme_tag_legacy"
)
conn.execute("DROP TABLE meme_tag_legacy")
conn.execute("DROP TABLE meme_legacy")
```

`create_current_schema()` 已创建所有新表、三个唯一索引、tag 索引并写入当前版本。Chroma 更新成功后提交事务；随后执行：

```python
conn.execute("PRAGMA foreign_keys = ON")
violations = conn.execute("PRAGMA foreign_key_check").fetchall()
if violations:
    raise MigrationError(f"外键检查失败: {violations!r}")
```

若外键检查失败，脚本从执行前 SQLite 备份恢复数据库并返回非零；不能在已提交事务上声称普通 rollback 生效。

- [ ] **Step 7: 在 SQLite 提交前更新 Chroma 并补偿**

在修改前读取完整 Chroma 记录快照，包含 embedding 和原 metadata：

```python
entry_ids = [int(row["id"]) for row in legacy_rows]
old_records = await vector_store.snapshot_records(entry_ids)
if {record.entry_id for record in old_records} != set(entry_ids):
    conn.rollback()
    raise MigrationError("SQLite 与 Chroma ID 不一致，拒绝迁移")
```

为每个 SQLite 内部 ID 调用 `await vector_store.update_collection_id(entry_id, 0)`。任一失败时：

1. `await vector_store.restore_records(old_records)`，完整恢复 embedding 和“无 metadata”状态；
2. `conn.rollback()`；
3. 抛 `MigrationError`。

全部成功后 `conn.commit()`。如果 commit 失败，同样调用 `restore_records(old_records)` 恢复 Chroma。提交后的 `foreign_key_check` 失败时，同时恢复 Chroma 并通过 SQLite Backup API 把备份恢复到原数据库路径。Task 4 同步增加通用接口，并在该任务完成测试，不把 VectorStore 变更拖到迁移脚本任务：

```python
async def get_metadatas(self) -> dict[int, VectorMetadata]:
    return await asyncio.to_thread(self._get_metadatas_sync)


async def update_metadata(
    self, entry_id: int, metadata: VectorMetadata
) -> None:
    await asyncio.to_thread(self._update_metadata_sync, entry_id, metadata)
```

`_get_metadatas_sync()` 使用 `get(include=["metadatas"])`；`_update_metadata_sync()` 使用 `collection.update(ids=[...], metadatas=[metadata])`。`update_collection_id()` 先读取旧 metadata、复制 dict、写入 `collection_id` 后调用 `update_metadata()`。Task 4 的 `test_update_collection_id_preserves_other_metadata` 已覆盖 metadata 中其他键不丢失；迁移任务只调用这些已验证接口。

- [ ] **Step 8: 实现幂等与 dry-run**

- current + metadata 完整：返回 `already_current=True`；
- current + metadata 缺失：只补 Chroma，失败返回非零；
- legacy + dry-run：只统计，不备份、不建表、不写 Chroma；
- unknown：抛 `MigrationError`。

- [ ] **Step 9: 运行 upgrade 测试和 CLI 帮助测试**

Run:

```bash
uv run pytest tests/unit/test_migrate_meme_collections.py -k "upgrade or help" -q
uv run ruff check scripts/migrate_meme_collections.py tests/unit/test_migrate_meme_collections.py
uv run ruff format --check scripts/migrate_meme_collections.py tests/unit/test_migrate_meme_collections.py
uv run ty check
```

Expected: 全部 PASS。

---

### Task 15: 实现 `move-root` 迁移子命令

**Files:**
- Modify: `scripts/migrate_meme_collections.py`
- Modify: `tests/unit/test_migrate_meme_collections.py`

- [ ] **Step 1: 写名称创建、局部编号、备份和未索引跳过测试**

`run_move_root_paths()` 的外层测试先断言非 dry-run 生成备份：

```python
@pytest.mark.asyncio
async def test_move_root_paths_creates_sqlite_backup(
    current_db: Path,
    current_chroma: Path,
    memes_dir: Path,
) -> None:
    await run_move_root_paths(
        current_db,
        current_chroma,
        memes_dir,
        "新三国",
        dry_run=False,
    )

    assert list(current_db.parent.glob("index.db.*.bak"))
```

逐文件逻辑测试：

```python
@pytest.mark.asyncio
async def test_move_root_creates_named_collection_and_skips_unindexed(
    current_store: MetadataStore,
    vector_store: VectorStore,
    memes_dir: Path,
) -> None:
    (memes_dir / "indexed.webp").write_bytes(b"indexed")
    (memes_dir / "unindexed.webp").write_bytes(b"unindexed")
    entry_id = current_store.add("indexed.webp", "文本")
    await vector_store.upsert(entry_id, [1.0, 0.0], collection_id=0)

    result = await run_move_root(
        current_store,
        vector_store,
        memes_dir,
        "新三国",
        dry_run=False,
    )

    moved = current_store.get_entry(entry_id)
    assert moved.public_id == MemePublicId(1, 1)
    assert moved.image_path == "新三国/indexed.webp"
    assert (memes_dir / "unindexed.webp").exists()
    assert result.moved == 1
    assert result.unindexed_skipped == ["unindexed.webp"]
```

- [ ] **Step 2: 写重名和文本冲突跳过测试**

```python
@pytest.mark.asyncio
async def test_move_root_renames_file_collision(
    current_store: MetadataStore,
    vector_store: VectorStore,
    memes_dir: Path,
) -> None:
    target = current_store.create_collection("新三国")
    target_dir = memes_dir / "新三国"
    target_dir.mkdir()
    (target_dir / "a.webp").write_bytes(b"existing")
    (memes_dir / "a.webp").write_bytes(b"source")
    entry_id = current_store.add("a.webp", "源文本")
    await vector_store.upsert(entry_id, [1.0, 0.0], collection_id=0)

    await run_move_root(
        current_store, vector_store, memes_dir, str(target.id), False
    )

    assert current_store.get_entry(entry_id).image_path == "新三国/a_2.webp"


@pytest.mark.asyncio
async def test_move_root_skips_duplicate_text_without_failure(
    current_store: MetadataStore,
    vector_store: VectorStore,
    memes_dir: Path,
) -> None:
    target = current_store.create_collection("新三国")
    conflict_id = current_store.add(
        "新三国/existing.webp", "相同", collection_id=target.id
    )
    source_id = current_store.add("source.webp", "相同")
    await vector_store.upsert(source_id, [1.0, 0.0], collection_id=0)

    result = await run_move_root(
        current_store, vector_store, memes_dir, "新三国", False
    )

    assert current_store.get_entry(source_id).public_id == MemePublicId(0, source_id)
    assert result.conflicts == [("source.webp", MemePublicId(target.id, 1))]
    assert result.failed == []
```

- [ ] **Step 3: 写单文件补偿和零成功清理测试**

```python
@pytest.mark.asyncio
async def test_move_root_rolls_back_file_when_vector_update_fails(
    current_store: MetadataStore,
    vector_store: VectorStore,
    memes_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = memes_dir / "a.webp"
    source.write_bytes(b"source")
    entry_id = current_store.add("a.webp", "文本")
    await vector_store.upsert(entry_id, [1.0, 0.0], collection_id=0)
    monkeypatch.setattr(
        vector_store,
        "update_collection_id",
        AsyncMock(side_effect=RuntimeError("failed")),
    )

    result = await run_move_root(
        current_store, vector_store, memes_dir, "新三国", False
    )

    assert source.exists()
    assert current_store.get_entry(entry_id).public_id == MemePublicId(0, entry_id)
    assert current_store.get_collection_by_name("新三国") is None
    assert result.moved == 0
    assert len(result.failed) == 1
```

- [ ] **Step 4: 运行失败测试**

Run:

```bash
uv run pytest tests/unit/test_migrate_meme_collections.py -k "move_root" -q
```

Expected: FAIL，move-root 函数和结果类型缺失。

- [ ] **Step 5: 实现目标解析和名称校验**

```python
def validate_collection_name(raw: str) -> str:
    name = raw.strip()
    if (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise InvalidCollectionNameError(raw)
    return name
```

纯数字目标先查 ID，再查同名；非数字精确查名称。名称不存在时只在存在待迁移已索引图片后创建合集和目录。

- [ ] **Step 6: 实现候选收集与 dry-run**

候选条件：

```python
entry.image_path == Path(entry.image_path).name
and Path(entry.image_path).suffix.lower() in IndexManager.SUPPORTED_EXTENSIONS
```

扫描根目录受支持文件，SQLite 无记录的路径加入 `unindexed_skipped`。`--dry-run` 计算目标合集预计编号、文件冲突和数量，不创建目录、合集、备份或 metadata。

- [ ] **Step 7: 实现逐文件迁移和补偿**

每张图：

1. 查目标 `(collection_id, text)` 冲突，冲突加入 skipped；
2. 分配最小局部编号；
3. 用 `resolve_unique_filename(target_dir, source.name, first_suffix=2)` 生成唯一目标路径；
4. 移动文件；
5. 更新 SQLite；
6. 更新 Chroma metadata；
7. 可捕获异常时恢复 SQLite 和文件，记录 failed，继续。

不实现崩溃/断电恢复日志。非 dry-run 在创建合集或移动文件前调用共享 `_backup_database(db_path, backup_path)` 创建 SQLite backup；backup 路径使用 `index.db.<YYYYMMDDHHMMSS>.bak`。运行输出明确说明强制终止需人工用备份检查。`run_move_root_paths()` 负责打开 Store、创建备份并在 `finally` 关闭 Store；`run_move_root()` 只接收已打开的 Store，供单元测试验证逐文件逻辑。

- [ ] **Step 8: 清理本次创建但零成功的目标**

若 `created_collection is not None and moved == 0`：

```python
store.delete_collection_and_reset_scopes(created_collection.id)
if created_directory and target_dir.exists() and not any(target_dir.iterdir()):
    target_dir.rmdir()
```

释放合集编号。

- [ ] **Step 9: 完成 CLI 退出码和报告**

- failed 非空：返回 `1`；
- 全部成功、全部冲突跳过、全部未索引或无事可做：返回 `0`；
- 报告 moved、conflicts、unindexed_skipped、failed；
- 最多展示前 10 个详细项，其余显示数量。

- [ ] **Step 10: 运行全部迁移测试**

Run:

```bash
uv run pytest tests/unit/test_migrate_meme_collections.py -q
uv run ty check
```

Expected: 全部 PASS。

---

### Task 16: 修复维护脚本和集成契约

**Files:**
- Modify: `scripts/regenerate_embeddings.py`
- Modify: `scripts/convert_memes_to_webp.py`
- Create: `tests/unit/test_regenerate_embeddings.py`
- Modify: `tests/unit/test_convert_memes_to_webp.py`
- Modify: `tests/integration/test_index_manager_api.py`
- Modify: `tests/integration/test_ai_matcher_api.py`
- Modify: `docs/api/bot/engine/vector_store.md`

- [ ] **Step 1: 写 regenerate_embeddings 合集 metadata 测试**

新增 `tests/unit/test_regenerate_embeddings.py`，mock `VectorStore.rebuild_all()` 和 embedding provider。把脚本核心向量项断言写成：

```python
assert rebuilt_items == [
    (1, embedding_one, 0),
    (2, embedding_two, 3),
]
```

脚本 SQL 必须读取 `id, text, collection_id`。

- [ ] **Step 2: 写嵌套路径 WebP 转换测试**

```python
def test_conversion_preserves_nested_relative_path(
    memes_dir: Path, db_path: Path
) -> None:
    source = memes_dir / "新三国" / "a.png"
    source.parent.mkdir(parents=True)
    create_png(source)
    collection_id = create_collection(db_path, "新三国")
    entry_id = add_entry(
        db_path,
        "新三国/a.png",
        collection_id=collection_id,
    )

    run_conversion(memes_dir, db_path, 85, False)

    store = MetadataStore(str(db_path))
    store.load()
    assert store.get_entry(entry_id).image_path == "新三国/a.webp"
```

- [ ] **Step 3: 修改维护脚本**

`regenerate_embeddings.py`：

```python
rows = conn.execute(
    "SELECT id, text, collection_id FROM meme ORDER BY id"
).fetchall()
items = [
    (entry_id, embedding, collection_id)
    for (entry_id, _, collection_id), embedding in zip(rows, embeddings)
]
await vector_store.rebuild_all(items)
```

`convert_memes_to_webp.py`：

```python
old_relative = src.relative_to(memes_dir).as_posix()
new_relative = webp_path.relative_to(memes_dir).as_posix()
entry = metadata_store.get_by_filename(old_relative)
metadata_store.update(entry.id, image_path=new_relative)
```

归档目录仍不查业务 SQLite。

- [ ] **Step 4: 更新集成测试构造**

所有 `IndexManager(...)` 增加：

```python
collection_manager=CollectionManager(metadata_store)
```

Vector fake 的 `upsert/query/rebuild_all` 接受新 metadata 参数。AI 集成测试增加 `collection_id=None` 默认调用断言。

- [ ] **Step 5: 运行维护脚本和非联网集成测试**

Run:

```bash
uv run pytest \
  tests/unit/test_regenerate_embeddings.py \
  tests/unit/test_convert_memes_to_webp.py \
  tests/integration/test_index_manager_api.py \
  -q
```

Expected: 全部 PASS。联网集成测试不在此步骤运行；Task 18 单独说明。

---

### Task 17: 同步产品、术语、README 与全部 API 文档

**Files:**
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `README.md`
- Modify: `docs/api/API.md`
- Modify: all affected API docs listed above

- [ ] **Step 1: 更新 PRD 数据模型和功能需求**

在 `docs/PRD.md` 增加：

- `memes/` 根目录与一级合集目录结构；
- `0.x` 全局归属和 `/switch 0` 全库语义；
- `/switch`、`/mv` 完整流程；
- 所有搜索入口按 ChatScope 过滤；
- 新 SQLite 表和 Chroma metadata；
- 启动遇旧 Schema 拒绝；
- 两个迁移子命令；
- 本规格的边界情况和性能指标。

把所有旧 `<entry_id>` 用户命令示例改为公开 ID 或说明当前合集短号。

- [ ] **Step 2: 更新术语表**

在 `CONTEXT.md` 增加“表情包合集、合集编号、合集内编号、公开 ID、全局、全部合集、当前合集”。把旧 `entry_id` 定义改为内部稳定 ID，并注明用户不再直接输入该值。

- [ ] **Step 3: 更新 README 操作说明**

加入：

```text
memes/
├── root.webp
└── 新三国/
    └── a.webp
```

加入命令：

```bash
uv run python -m scripts.migrate_meme_collections upgrade-schema --dry-run
uv run python -m scripts.migrate_meme_collections upgrade-schema
uv run python -m scripts.migrate_meme_collections move-root 新三国 --dry-run
uv run python -m scripts.migrate_meme_collections move-root 新三国
```

明确运行迁移前停止 Bot、默认执行、`--dry-run` 预演、SQLite 备份和强制中断边界。

- [ ] **Step 4: 检查 API 索引完整性**

`docs/api/API.md` 必须链接：

```markdown
- [`collection_manager`](bot/engine/collection_manager.md)
- [`_collection_utils`](bot/plugins/_collection_utils.md)
- [`switch`](bot/plugins/switch.md)
- [`move`](bot/plugins/move.md)
```

逐一确认所有修改过的公开签名与文档一致。

- [ ] **Step 5: 搜索过时术语和命令示例**

Run:

```bash
rg -n "<entry_id>|id 必须为数字|filename: memes/ 下的文件名|仅扫一次|扁平结构" \
  README.md CONTEXT.md docs bot/plugins
```

Expected: 只剩明确说明内部 ID 或历史兼容的段落；用户命令不再要求裸内部整数。

- [ ] **Step 6: 检查文档格式**

Run:

```bash
git diff --check
```

Expected: 无空白错误。

---

### Task 18: 全量验证和真实流程验收

**Files:**
- Verify all changed files
- No new implementation unless a verification failure exposes a bug

- [ ] **Step 1: 运行合集相关快速测试集**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_collection_manager.py \
  tests/unit/engine/test_metadata_store.py \
  tests/unit/engine/test_vector_store.py \
  tests/unit/engine/test_index_manager.py \
  tests/unit/engine/test_index_manager_move.py \
  tests/unit/plugins/test_switch.py \
  tests/unit/plugins/test_move.py \
  tests/unit/test_migrate_meme_collections.py \
  -q
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行完整非联网测试集**

Run:

```bash
uv run pytest tests/unit -q
```

Expected: 全部 PASS。

- [ ] **Step 3: 运行静态检查与格式检查**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Expected: 三条命令全部成功。

- [ ] **Step 4: 运行显式迁移的临时目录冒烟测试**

先复制测试 fixture 或由测试脚本生成旧库，再执行：

```bash
uv run python -m scripts.migrate_meme_collections \
  --db-path /tmp/meme-pilot-collections/index.db \
  --chroma-dir /tmp/meme-pilot-collections/chroma \
  --memes-dir /tmp/meme-pilot-collections/memes \
  upgrade-schema --dry-run

uv run python -m scripts.migrate_meme_collections \
  --db-path /tmp/meme-pilot-collections/index.db \
  --chroma-dir /tmp/meme-pilot-collections/chroma \
  --memes-dir /tmp/meme-pilot-collections/memes \
  upgrade-schema

uv run python -m scripts.migrate_meme_collections \
  --db-path /tmp/meme-pilot-collections/index.db \
  --chroma-dir /tmp/meme-pilot-collections/chroma \
  --memes-dir /tmp/meme-pilot-collections/memes \
  move-root 新三国 --dry-run
```

Expected: dry-run 无修改；升级生成备份并把旧 `N` 映射为 `0.N`；move-root 预演显示目标和数量。

- [ ] **Step 5: 执行项目真实流程验证技能**

源代码包含运行时行为变更，调用项目 `verify` 技能，至少驱动以下流程：

1. 新库启动并扫描根目录和深层合集目录；
2. `/switch` 在两个 ChatScope 中分别持久化；
3. 普通文本、`/rand`、`/sim` 使用合集过滤；
4. `/add` 在普通合集与 `0` 下落到不同目录；
5. `/mv` 显示预计 ID，确认后返回实际 ID；
6. 删除合集目录并 `/refresh`，ChatScope 回退到 `0`；
7. 重启后选择和公开 ID 保持正确。

Expected: 观察结果与规格一致。若环境缺少 QQ/NapCat 或外部 provider，记录无法执行的步骤，不虚报通过；使用 fake provider 的本地驱动覆盖其余路径。

- [ ] **Step 6: 联网集成测试按用户环境执行**

只有用户提供并授权相应 API 配置时运行：

```bash
uv run pytest tests/integration -q
```

Expected: 全部 PASS。未提供网络或密钥时明确标记 skipped，不将其计为验证成功。

- [ ] **Step 7: 最终差异与主分支保护检查**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: 只有本功能及已批准规格/计划相关差异；没有 staged 文件，没有提交。向用户报告测试、静态检查、真实流程和跳过项。
