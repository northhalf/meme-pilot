# 索引管理与向量搜索重构（ChromaDB + SQLite3）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 meme-pilot 的双 JSON 索引（`index.json` + `embeddings.json`）重构为 SQLite3 元数据 + ChromaDB 向量库，移除 `text_hash`，OCR 文本去除所有空白，对外数据类统一 `image_path` / `entry_id:int`。

**Architecture:** 拆出两个公开存储类 —— `MetadataStore`（sqlite3，存 id/图片路径/文字/说话人/标记词）与 `VectorStore`（chromadb PersistentClient，存 id/向量），二者 id 一一对应。`IndexManager` 退化为薄编排层，持有两个 Store + providers，负责压缩→OCR→Embed 管道、sync 四阶段（含阶段0跨库一致性修复）、跨库写入一致性、全局锁、去重/无文字移图。`AIMatcher` 改用 `VectorStore.query` 召回 + `MetadataStore` 取 metadata；`KeywordSearcher` 改用 `MetadataStore.get_all_entries`。迁移脚本 `scripts/migrate_json_to_db.py` 复用旧向量零 API 迁移。

**Tech Stack:** Python 3.12、sqlite3（标准库）、chromadb（PersistentClient + cosine collection）、asyncio + threading.Lock + asyncio.to_thread（同步 I/O 不阻塞事件循环）、NoneBot2 + OneBot v11、jieba.posseg + pylcs、BAAI/bge-m3（1024 维）、DeepSeek-OCR/PaddleOCR、uv 包管理。

**关联设计文档：** `docs/superpowers/specs/2026-06-30-chromadb-sqlite-refactor-design.md`（已批准）

---

## 前置准备：创建工作分支

本重构规模大，在隔离分支上推进，各任务自行提交到该分支；**全部完成后暂停，由用户审核并决定 merge 回 `main`**。

- [ ] **Step 0: 确认工作区干净并创建分支**

Run:
```bash
git status --short
git checkout -b refactor/chromadb-sqlite
```
Expected: `git status` 无输出（工作区干净）；切换到新分支 `refactor/chromadb-sqlite`。

> 后续每个任务的"提交"步骤都在此分支上 `git add` + `git commit`。**禁止 merge 或 push，merge 由用户在计划末尾审核后执行。**

---

## 全局类型约定（所有任务共同遵守）

| 类型 | 定义 |
|---|---|
| `MemeEntry` | `(id:int, image_path:str, text:str, speaker:str\|None, tags:list[str])` |
| `VectorHit` | `(entry_id:int, similarity:float)` |
| `SearchResult` | `(entry_id:int, image_path:str, text:str, similarity:float)` |
| `AIMatchCandidate` | `(rank:int, entry_id:int, image_path:str, text:str, similarity:float)` |
| `AIMatchResult` | `(entry_id:int, image_path:str, text:str, similarity:float, source:str)` |
| `AddResult` | `(entry_id:int\|None, reason:str, text:str, replaced_image_path:str\|None, moved_to:str\|None)` |
| `SyncResult` | `(added:int, deleted:int, deduped:int, no_text_moved:int, failed:list[str])` — 字段不变 |

**写入顺序统一：先 sqlite 后 chroma**（添加路径 sqlite 提交后 chroma upsert，失败可回滚 sqlite；删除路径 sqlite 删除后 chroma 删除，chroma 失败靠 sync 阶段0 清孤儿）。

**OCR 文本约定：** `deepseek_ocr.py` / `paddle_ocr.py` 的 `ocr()` 返回前做 `"".join(result.split())`，存储与搜索文本均无空格。无文字判定统一为 `not text`（text 即去空格后的串，空即 `""`）。

**内部反向索引命名：** `MetadataStore._text_to_id: dict[str, int]`（text → id）。

**集成测试 skipif 说明：** `tests/integration/test_*_api.py` 均带 `pytestmark = pytest.mark.skipif(not os.environ.get("SILICONFLOW_API_KEY") ...)`。无 API key 环境下它们自动跳过，因此 T6/T7/T9 改造期间"全量 pytest"在无 key 环境仍全绿；改造集成测试本身在 T12 完成。

---

## 文件结构

### 新建

| 文件 | 职责 |
|---|---|
| `bot/engine/metadata_store.py` | `MemeEntry` 数据类 + `MetadataStore`（sqlite3 CRUD、`_text_to_id`、`find_next_id`、事务） |
| `bot/engine/vector_store.py` | `VectorHit` 数据类 + `VectorStore`（chromadb upsert/remove/query/rebuild_all，`to_thread` + `threading.Lock`） |
| `scripts/migrate_json_to_db.py` | 旧 JSON → sqlite + chroma 手动迁移脚本（复用旧向量，幂等） |
| `tests/conftest.py` | `tmp_sqlite_path`、`tmp_chroma_dir` fixture |
| `tests/unit/engine/test_metadata_store.py` | MetadataStore 单元测试 |
| `tests/unit/engine/test_vector_store.py` | VectorStore 单元测试 |
| `tests/unit/test_migrate_script.py` | 迁移脚本测试 |

### 修改

| 文件 | 改造点 |
|---|---|
| `bot/config.py` | 新增 `INDEX_DB_PATH`、`CHROMA_DIR` 常量 |
| `bot/engine/index_manager.py` | 重写为薄编排（依赖两个 Store），删除 JSON 读写/text_hash/encode_decode/normalize/dedup_key/is_blank_text |
| `bot/engine/ai_matcher.py` | 改用 `VectorStore.query` + `MetadataStore.get_entry`；`AIMatchCandidate`/`AIMatchResult` 字段改名 |
| `bot/engine/keyword_searcher.py` | 改用 `MetadataStore.get_all_entries`；`SearchResult` 字段改名 |
| `bot/engine/deepseek_ocr.py` | `ocr()` 返回前去所有空白 |
| `bot/engine/paddle_ocr.py` | `ocr()` 返回前去所有空白 |
| `bot/engine/protocols.py` | 不变（`EmbeddingProvider` 保留） |
| `bot/engine/__init__.py` | 导出 `MetadataStore`/`VectorStore`/`MemeEntry`/`VectorHit`，移除已删符号 |
| `bot/app_state.py` | 新增 `get_metadata_store()`/`get_vector_store()`，`init_app` 多收两参数 |
| `bot/bot.py` | startup 创建并注入两个 Store |
| `bot/plugins/_search_utils.py` | `results[0].filename` → `.image_path`、`result.filename` → `.image_path` |
| `bot/plugins/meme_ai.py` | `match_result.filename` → `.image_path` |
| `bot/plugins/meme_add.py` | `AddResult.replaced_filename` → `.replaced_image_path`（仅在引用处） |
| `bot/Dockerfile` | 无需改（`uv sync` 自动拉 chromadb）；接受镜像变大 |
| `pyproject.toml` | 新增 `chromadb`，移除 `ujson` |
| `docs/api/API.md` 及子文档 | 新增/更新接口文档 |
| `docs/PRD.md`、`CONTEXT.md`、`README.md`、`.env.example`、`CLAUDE.md` | 同步术语/结构/升级提示 |

### 删除

| 文件 | 原因 |
|---|---|
| `tests/unit/engine/test_embedding_codec.py` | `encode/decode_embedding` 已删 |
| `data/index.json`（迁移后手动） | bot 不再读写 |
| `data/embeddings.json`（迁移后手动） | bot 不再读写 |

---

## Task 1: 安装 chromadb 依赖

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`（由 uv 自动更新）

- [ ] **Step 1: 添加 chromadb 生产依赖**

Run:
```bash
uv add chromadb
```
Expected: `pyproject.toml` 的 `dependencies` 增加 `chromadb>=...`，`uv.lock` 更新成功。

- [ ] **Step 2: 验证依赖可导入**

Run:
```bash
uv run python -c "import chromadb; print(chromadb.__version__)"
```
Expected: 打印 chromadb 版本号，无异常。

- [ ] **Step 3: 确认现有测试仍通过（chromadb 加入不应破坏既有代码）**

Run:
```bash
uv run pytest -q
```
Expected: 全部通过（集成测试无 key 自动 skip）。

- [ ] **Step 4: 提交到分支**

```bash
git add pyproject.toml uv.lock
git commit -m "build: 新增 chromadb 依赖用于向量索引库"
```

> 说明：`ujson` 暂不移除，待 Task 14 在 `index_manager.py` 重写后统一移除。

---

## Task 2: 创建 tests/conftest.py 测试基础设施

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: 创建 conftest，提供 sqlite 与 chroma 临时路径 fixture**

写入 `tests/conftest.py`：

```python
"""pytest 共享 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_sqlite_path(tmp_path: Path) -> Path:
    """返回一个不存在的 sqlite 数据库文件路径（在 tmp_path 下）。

    MetadataStore.load() 会自动创建该文件与目录。
    """
    return tmp_path / "index.db"


@pytest.fixture
def tmp_chroma_dir(tmp_path: Path) -> Path:
    """返回一个 chroma PersistentClient 目录路径（在 tmp_path 下）。

    VectorStore.load() 会自动创建该目录。
    """
    return tmp_path / "chroma"
```

- [ ] **Step 2: 验证 fixture 可被收集**

Run:
```bash
uv run pytest tests/ -q --co -k "dummy_never_matches" 2>&1 | head -5
```
Expected: pytest 正常启动并收集（无 import 错误）；`conftest.py` 被 pytest 自动发现。

- [ ] **Step 3: 提交到分支**

```bash
git add tests/conftest.py
git commit -m "test: 新增 conftest 提供 sqlite/chroma 临时路径 fixture"
```

---

## Task 3: config.py 新增 INDEX_DB_PATH / CHROMA_DIR 常量

**Files:**
- Modify: `bot/config.py`
- Modify: `tests/unit/test_config.py`（若不存在则创建）

- [ ] **Step 1: 检查是否已有 config 测试文件**

Run:
```bash
fd test_config.py tests/
```
若不存在，本任务创建 `tests/unit/test_config.py`；若存在，在其上追加测试。

- [ ] **Step 2: 写失败测试 — 验证两个新常量**

在 `tests/unit/test_config.py`（新建或追加）中加入：

```python
"""bot.config 全局路径常量与配置读取测试。"""

from pathlib import Path

from bot.config import CHROMA_DIR, INDEX_DB_PATH, MEMES_DIR, PROJECT_ROOT


def test_index_db_path_under_data() -> None:
    """INDEX_DB_PATH 位于 <项目根>/data/index.db。"""
    assert INDEX_DB_PATH == PROJECT_ROOT / "data" / "index.db"


def test_chroma_dir_under_data() -> None:
    """CHROMA_DIR 位于 <项目根>/data/chroma。"""
    assert CHROMA_DIR == PROJECT_ROOT / "data" / "chroma"
```

- [ ] **Step 3: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/test_config.py -v
```
Expected: FAIL，`ImportError: cannot import name 'CHROMA_DIR'` 或类似。

- [ ] **Step 4: 实现 — 在 config.py 增加常量并导出**

编辑 `bot/config.py`，在 `MEMES_DIR = PROJECT_ROOT / "memes"` 之后加入：

```python
# 索引数据目录与文件
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DB_PATH = DATA_DIR / "index.db"
CHROMA_DIR = DATA_DIR / "chroma"
```

并更新 `__all__`，加入 `"DATA_DIR"`, `"INDEX_DB_PATH"`, `"CHROMA_DIR"`：

```python
__all__ = [
    "PROJECT_ROOT",
    "MEMES_DIR",
    "DATA_DIR",
    "INDEX_DB_PATH",
    "CHROMA_DIR",
    "read_session_timeout",
    "read_ocr_provider",
]
```

- [ ] **Step 5: 运行测试，确认通过**

Run:
```bash
uv run pytest tests/unit/test_config.py -v
```
Expected: PASS。

- [ ] **Step 6: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿（新常量不破坏任何现有引用）。

- [ ] **Step 7: 提交到分支**

```bash
git add bot/config.py tests/unit/test_config.py
git commit -m "feat(config): 新增 INDEX_DB_PATH/CHROMA_DIR/DATA_DIR 路径常量"
```

---

## Task 4: 新建 MetadataStore（sqlite3）+ MemeEntry

**Files:**
- Create: `bot/engine/metadata_store.py`
- Create: `tests/unit/engine/test_metadata_store.py`

### 4.1 设计要点

- SQLite schema（`load()` 时建表，`IF NOT EXISTS`）：
  ```sql
  CREATE TABLE IF NOT EXISTS meme (
      id INTEGER PRIMARY KEY, image_path TEXT NOT NULL, text TEXT NOT NULL, speaker TEXT
  );
  CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_image_path ON meme(image_path);
  CREATE TABLE IF NOT EXISTS meme_tag (
      meme_id INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (meme_id, tag),
      FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
  );
  CREATE INDEX IF NOT EXISTS idx_meme_tag_tag ON meme_tag(tag);
  ```
- `find_next_id` 用纯 SQL（注入虚拟 0 行覆盖表头空洞）：
  ```sql
  SELECT MIN(t.id) + 1 AS next_id
  FROM (SELECT 0 AS id UNION ALL SELECT id FROM meme) t
  WHERE NOT EXISTS(SELECT 1 FROM meme t2 WHERE t2.id = t.id + 1);
  ```
- `sqlite3.connect(check_same_thread=False)` + 内部 `threading.Lock` 串行化所有访问；公开方法为同步，调用方（IndexManager）用 `asyncio.to_thread` 包装。
- `_text_to_id: dict[str, int]` 在 `load()` 时从 sqlite 全量重建，`add`/`update`/`remove` 同步维护。
- `meme_tag` 用 `ON DELETE CASCADE`：删 `meme` 行自动清其全部 tag 行（需 `PRAGMA foreign_keys = ON`）。
- 本次 `speaker`/`tags` 一律写 `NULL`/不写行，但接口支持以备未来。

- [ ] **Step 1: 写失败测试 — load 建表 + entry_count + add + get_entry**

写入 `tests/unit/engine/test_metadata_store.py`：

```python
"""MetadataStore 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.engine.metadata_store import MemeEntry, MetadataStore


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
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/engine/test_metadata_store.py -v
```
Expected: FAIL，`ModuleNotFoundError: bot.engine.metadata_store`。

- [ ] **Step 3: 实现 MetadataStore**

写入 `bot/engine/metadata_store.py`：

```python
"""元数据存储模块 — 基于 sqlite3。

存储每条表情包的 id、图片路径（memes/ 下相对路径）、OCR 文字（去除所有空白）、
说话人、标记词。id 与 VectorStore 的向量 id 完全一一对应。

设计要点：
- sqlite3 标准库，INTEGER PRIMARY KEY 手动分配（复用最小空洞，不用 AUTOINCREMENT）。
- meme_tag 关联表存多值标记词，ON DELETE CASCADE 随 meme 行删除。
- check_same_thread=False + 内部 threading.Lock 串行化所有 sqlite 访问；
  公开方法为同步，调用方用 asyncio.to_thread 包装以避免阻塞事件循环。
- _text_to_id 内存反向索引（text→id），load 时全量重建，增删同步维护，加速去重判定。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meme ("
    "    id INTEGER PRIMARY KEY,"
    "    image_path TEXT NOT NULL,"
    "    text TEXT NOT NULL,"
    "    speaker TEXT"
    ");",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_image_path ON meme(image_path);",
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


@dataclass
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
            self._text_to_id = {
                row["text"]: row["id"]
                for row in self._conn.execute("SELECT id, text FROM meme")
            }
        logger.info(
            "MetadataStore 加载完成: %s, 共 %d 条记录",
            self._db_path,
            len(self._text_to_id),
        )

    def close(self) -> None:
        """关闭连接。"""
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_all_entries(self) -> dict[int, MemeEntry]:
        """返回全部条目，key=int(id)，tags 从 meme_tag 组装。

        Returns:
            id → MemeEntry 映射。
        """
        with self._lock:
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
        return {
            row["id"]: MemeEntry(
                id=row["id"],
                image_path=row["image_path"],
                text=row["text"],
                speaker=row["speaker"],
                tags=tags_by_id.get(row["id"], []),
            )
            for row in rows
        }

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按 id 查询单条记录。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, image_path, text, speaker FROM meme WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                return None
            tags = [
                r["tag"]
                for r in self._conn.execute(
                    "SELECT tag FROM meme_tag WHERE meme_id = ? ORDER BY tag",
                    (entry_id,),
                )
            ]
        return MemeEntry(
            id=row["id"],
            image_path=row["image_path"],
            text=row["text"],
            speaker=row["speaker"],
            tags=tags,
        )

    def get_by_filename(self, image_path: str) -> MemeEntry | None:
        """按图片路径查询。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, image_path, text, speaker FROM meme WHERE image_path = ?",
                (image_path,),
            ).fetchone()
            if row is None:
                return None
        return self.get_entry(row["id"])

    def get_id_by_text(self, text: str) -> int | None:
        """按 text 查 id（走 _text_to_id）。"""
        with self._lock:
            return self._text_to_id.get(text)

    def find_next_id(self) -> int:
        """纯 SQL 查找最小空洞 id。"""
        with self._lock:
            row = self._conn.execute(_FIND_NEXT_ID_SQL).fetchone()
        return int(row["next_id"])

    def entry_count(self) -> int:
        """条目总数。"""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM meme").fetchone()
        return int(row["c"])

    def get_all_text(self) -> list[tuple[int, str]]:
        """返回全部 (id, text)，供 sync 阶段0 全量重 embed 用。"""
        with self._lock:
            rows = list(self._conn.execute("SELECT id, text FROM meme ORDER BY id"))
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

        Returns:
            分配的 int id。
        """
        with self._lock:
            entry_id = int(
                self._conn.execute(_FIND_NEXT_ID_SQL).fetchone()["next_id"]
            )
            self._conn.execute(
                "INSERT INTO meme (id, image_path, text, speaker) VALUES (?, ?, ?, ?)",
                (entry_id, image_path, text, speaker),
            )
            self._write_tags(entry_id, tags)
            self._conn.commit()
            self._text_to_id[text] = entry_id
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

        Returns:
            写入的 int id（即传入的 entry_id）。
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO meme (id, image_path, text, speaker) VALUES (?, ?, ?, ?)",
                (entry_id, image_path, text, speaker),
            )
            self._write_tags(entry_id, tags)
            self._conn.commit()
            self._text_to_id[text] = entry_id
        return entry_id

    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = None,
        text: str | None = None,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """更新单条记录的可选字段。

        text 变更时同步更新 _text_to_id（删旧键加新键）。
        tags 非 None 时整体替换该条 tag 行。

        Returns:
            True 表示找到并更新，False 表示 id 不存在。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id, text FROM meme WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return False

            sets: list[str] = []
            params: list[object] = []
            if image_path is not None:
                sets.append("image_path = ?")
                params.append(image_path)
            if text is not None:
                sets.append("text = ?")
                params.append(text)
            if speaker is not None:
                sets.append("speaker = ?")
                params.append(speaker)

            if sets:
                params.append(entry_id)
                self._conn.execute(
                    f"UPDATE meme SET {', '.join(sets)} WHERE id = ?", params
                )

            if tags is not None:
                self._conn.execute(
                    "DELETE FROM meme_tag WHERE meme_id = ?", (entry_id,)
                )
                self._write_tags(entry_id, tags)

            self._conn.commit()

            # 维护 _text_to_id
            old_text = row["text"]
            new_text = text if text is not None else old_text
            if old_text in self._text_to_id and self._text_to_id[old_text] == entry_id:
                del self._text_to_id[old_text]
            self._text_to_id[new_text] = entry_id
        return True

    def remove(self, entry_id: int) -> bool:
        """删除单条记录；CASCADE 删 meme_tag，同步 _text_to_id。

        Returns:
            True 表示删除成功，False 表示 id 不存在。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT text FROM meme WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return False
            self._conn.execute("DELETE FROM meme WHERE id = ?", (entry_id,))
            self._conn.commit()
            text = row["text"]
            if self._text_to_id.get(text) == entry_id:
                del self._text_to_id[text]
        return True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _write_tags(self, entry_id: int, tags: list[str] | None) -> None:
        """写入 tag 行（调用方已持 _lock）。"""
        if not tags:
            return
        self._conn.executemany(
            "INSERT OR IGNORE INTO meme_tag (meme_id, tag) VALUES (?, ?)",
            [(entry_id, t) for t in tags],
        )
```

- [ ] **Step 4: 运行 TestLoadAndCount / TestAddAndGet，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_metadata_store.py -v
```
Expected: PASS（当前两个测试类）。

- [ ] **Step 5: 追加 find_next_id 五例 + update + remove + tags 组装测试**

在 `tests/unit/engine/test_metadata_store.py` 末尾追加：

```python
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
        assert entry.tags == ["搞笑", "猫"]

    def test_cascade_delete_removes_tags(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["搞笑"])
        store.remove(eid)
        # 重新插入同 id，tags 应为空（CASCADE 清掉了旧 tag 行）
        store.add_with_id(eid, "b.jpg", "乙")
        assert store.get_entry(eid).tags == []

    def test_update_replaces_tags(self, store: MetadataStore) -> None:
        eid = store.add("a.jpg", "甲", tags=["旧"])
        store.update(eid, tags=["新1", "新2"])
        assert store.get_entry(eid).tags == ["新1", "新2"]


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
```

- [ ] **Step 6: 运行全部 metadata_store 测试**

Run:
```bash
uv run pytest tests/unit/engine/test_metadata_store.py -v
```
Expected: PASS（全部测试类）。

- [ ] **Step 7: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿（新模块未接入，不影响现有代码）。

- [ ] **Step 8: 提交到分支**

```bash
git add bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
git commit -m "feat(engine): 新增 MetadataStore sqlite3 元数据存储 + MemeEntry"
```

---

## Task 5: 新建 VectorStore（chromadb）+ VectorHit

**Files:**
- Create: `bot/engine/vector_store.py`
- Create: `tests/unit/engine/test_vector_store.py`

### 5.1 设计要点

- `chromadb.PersistentClient(path=chroma_path)`，`get_or_create_collection("memes", metadata={"hnsw:space": "cosine"})`。
- 每条存 `id=str(int)` + `embedding=list[float]`，不存 metadata。
- `query(query_embeddings=[vec], n_results=n)` → `chroma` 返回 `(ids, distances)`，`similarity = 1 - distance`（cosine distance ∈ [0,2]，similarity ∈ [-1,1]）。
- chroma 同步调用阻塞事件循环 → `asyncio.to_thread` 包装 `upsert`/`remove`/`remove_many`/`query`/`rebuild_all`。
- chroma 并发写冲突 → 内部 `threading.Lock` 串行化所有 chroma 访问。
- `remove` 不存在静默（chroma `delete` 不存在会抛错 → 用 try/except 或先查；实现里捕获并忽略）。

- [ ] **Step 1: 写失败测试 — load + upsert + query + count**

写入 `tests/unit/engine/test_vector_store.py`：

```python
"""VectorStore 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.engine.vector_store import VectorHit, VectorStore


@pytest.fixture
def store(tmp_chroma_dir: Path) -> VectorStore:
    """已 load 的 VectorStore。"""
    s = VectorStore(str(tmp_chroma_dir))
    s.load()
    return s


class TestLoadAndCount:
    def test_load_creates_collection(self, tmp_chroma_dir: Path) -> None:
        s = VectorStore(str(tmp_chroma_dir))
        s.load()
        assert s.count() == 0
        assert tmp_chroma_dir.exists()

    def test_reload_preserves_data(self, tmp_chroma_dir: Path) -> None:
        s1 = VectorStore(str(tmp_chroma_dir))
        s1.load()
        s1.upsert(1, [1.0, 0.0])
        s1.close()

        s2 = VectorStore(str(tmp_chroma_dir))
        s2.load()
        assert s2.count() == 1


class TestUpsertAndQuery:
    def test_upsert_then_query_returns_hit(self, store: VectorStore) -> None:
        store.upsert(1, [1.0, 0.0])
        store.upsert(2, [0.0, 1.0])
        hits = store.query([1.0, 0.0], n_results=2)
        assert len(hits) == 2
        assert all(isinstance(h, VectorHit) for h in hits)
        # 与 [1.0, 0.0] 完全一致 → similarity ≈ 1.0
        assert hits[0].entry_id == 1
        assert abs(hits[0].similarity - 1.0) < 1e-5

    def test_upsert_overwrites(self, store: VectorStore) -> None:
        store.upsert(1, [1.0, 0.0])
        store.upsert(1, [0.0, 1.0])  # 覆盖
        assert store.count() == 1
        hits = store.query([0.0, 1.0], n_results=1)
        assert hits[0].entry_id == 1
        assert abs(hits[0].similarity - 1.0) < 1e-5

    def test_query_returns_int_entry_id(self, store: VectorStore) -> None:
        store.upsert(42, [1.0, 0.0])
        hits = store.query([1.0, 0.0], n_results=1)
        assert hits[0].entry_id == 42
        assert isinstance(hits[0].entry_id, int)

    def test_query_empty_collection_returns_empty(self, store: VectorStore) -> None:
        assert store.query([1.0, 0.0], n_results=10) == []

    def test_query_n_results_limits(self, store: VectorStore) -> None:
        for i in range(5):
            store.upsert(i, [float(i), 0.0])
        hits = store.query([0.0, 0.0], n_results=3)
        assert len(hits) == 3
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/engine/test_vector_store.py -v
```
Expected: FAIL，`ModuleNotFoundError: bot.engine.vector_store`。

- [ ] **Step 3: 实现 VectorStore**

写入 `bot/engine/vector_store.py`：

```python
"""向量存储模块 — 基于 chromadb PersistentClient。

仅存 id（与 sqlite 完全一一对应）+ embedding（1024 维 float32）。
cosine 距离的 HNSW 索引，query 返回 Top-N (entry_id, similarity)。

设计要点：
- chroma 为同步库，所有公开方法用 asyncio.to_thread 包装以避免阻塞事件循环。
- chroma 并发写冲突 → 内部 threading.Lock 串行化所有访问。
- id 在内部与 chroma 交互时转 str，对外保持 int。
- similarity = 1 - distance（cosine distance ∈ [0,2]）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass

import chromadb

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    """单条向量召回结果。

    Attributes:
        entry_id: 索引 id（int，与 sqlite 一一对应）。
        similarity: 余弦相似度，= 1 - distance。
    """

    entry_id: int
    similarity: float


class VectorStore:
    """chromadb 向量存储。

    Attributes:
        _chroma_path: PersistentClient 数据目录。
        _collection_name: collection 名，默认 "memes"。
        _client: chromadb.PersistentClient。
        _collection: chroma Collection（cosine）。
        _lock: threading.Lock，串行化所有 chroma 访问。
    """

    def __init__(self, chroma_path: str, collection_name: str = "memes") -> None:
        """初始化 VectorStore。

        Args:
            chroma_path: chroma PersistentClient 数据目录，load() 时自动创建。
            collection_name: collection 名，默认 "memes"。
        """
        self._chroma_path = chroma_path
        self._collection_name = collection_name
        self._client: chromadb.PersistentClient | None = None
        self._collection = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """创建 PersistentClient 并 get_or_create_collection（cosine）。"""
        self._client = chromadb.PersistentClient(path=self._chroma_path)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "VectorStore 加载完成: %s, collection=%s, 共 %d 条向量",
            self._chroma_path,
            self._collection_name,
            self.count(),
        )

    def close(self) -> None:
        """释放客户端（chroma PersistentClient 无显式 close，置空引用）。"""
        self._collection = None
        self._client = None

    def _upsert_sync(self, entry_id: int, embedding: list[float]) -> None:
        with self._lock:
            self._collection.upsert(
                ids=[str(entry_id)],
                embeddings=[embedding],
            )

    async def upsert(self, entry_id: int, embedding: list[float]) -> None:
        """插入或覆盖一条向量（id 内部转 str）。"""
        await asyncio.to_thread(self._upsert_sync, entry_id, embedding)

    def _remove_sync(self, entry_id: int) -> None:
        with self._lock:
            try:
                self._collection.delete(ids=[str(entry_id)])
            except Exception:
                # id 不存在时 chroma 可能抛错，静默忽略
                logger.debug("VectorStore.remove: id=%s 不存在或已删除", entry_id)

    async def remove(self, entry_id: int) -> None:
        """删除一条向量，不存在静默。"""
        await asyncio.to_thread(self._remove_sync, entry_id)

    def _remove_many_sync(self, entry_ids: list[int]) -> None:
        if not entry_ids:
            return
        with self._lock:
            try:
                self._collection.delete(ids=[str(i) for i in entry_ids])
            except Exception:
                logger.debug("VectorStore.remove_many 部分不存在: %s", entry_ids)

    async def remove_many(self, entry_ids: list[int]) -> None:
        """批量删除向量，不存在的静默。"""
        await asyncio.to_thread(self._remove_many_sync, entry_ids)

    def _query_sync(
        self, query_embedding: list[float], n_results: int
    ) -> list[VectorHit]:
        with self._lock:
            if self._collection.count() == 0:
                return []
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
            )
        ids: list[list[str]] = result.get("ids", [[]])
        distances: list[list[float]] = result.get("distances", [[]])
        if not ids or not ids[0]:
            return []
        hits: list[VectorHit] = []
        for raw_id, dist in zip(ids[0], distances[0]):
            hits.append(VectorHit(entry_id=int(raw_id), similarity=1.0 - float(dist)))
        return hits

    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]:
        """召回 Top-N，entry_id 转 int 返回。"""
        return await asyncio.to_thread(self._query_sync, query_embedding, n_results)

    def _rebuild_all_sync(self, items: list[tuple[int, list[float]]]) -> None:
        with self._lock:
            # 先清空再批量写入
            try:
                self._collection.delete(
                    ids=[str(i) for i, _ in self._collection.get()["ids"] and [] or []]
                )
            except Exception:
                pass
            # 安全的清空方式：删除 collection 并重建
            self._client.delete_collection(self._collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            if items:
                self._collection.upsert(
                    ids=[str(i) for i, _ in items],
                    embeddings=[vec for _, vec in items],
                )

    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None:
        """全量重建：删 collection 重建并批量写入。"""
        await asyncio.to_thread(self._rebuild_all_sync, items)

    def count(self) -> int:
        """当前向量数。"""
        with self._lock:
            return int(self._collection.count())
```

- [ ] **Step 4: 运行 TestLoadAndCount / TestUpsertAndQuery**

Run:
```bash
uv run pytest tests/unit/engine/test_vector_store.py -v
```
Expected: PASS。

> 若 `rebuild_all` 的清空实现触发告警，本步骤只测了 upsert/query/count，应稳定通过。`rebuild_all` 测试在 Step 5。

- [ ] **Step 5: 追加 remove / remove_many / rebuild_all 测试**

在 `tests/unit/engine/test_vector_store.py` 末尾追加：

```python
class TestRemove:
    def test_remove_existing(self, store: VectorStore) -> None:
        store.upsert(1, [1.0, 0.0])
        store.upsert(2, [0.0, 1.0])
        store.remove(1)
        assert store.count() == 1
        hits = store.query([1.0, 0.0], n_results=2)
        assert [h.entry_id for h in hits] == [2]

    def test_remove_nonexistent_silent(self, store: VectorStore) -> None:
        # 不应抛异常
        store.remove(999)

    def test_remove_many(self, store: VectorStore) -> None:
        for i in range(4):
            store.upsert(i, [float(i), 0.0])
        store.remove_many([0, 1])
        assert store.count() == 2

    def test_remove_many_empty_noop(self, store: VectorStore) -> None:
        store.remove_many([])  # 不抛异常


class TestRebuildAll:
    def test_rebuild_all_replaces(self, store: VectorStore) -> None:
        store.upsert(1, [1.0, 0.0])
        store.upsert(2, [0.0, 1.0])
        # 重建为完全不同的集合
        store.rebuild_all([(10, [1.0, 1.0]), (20, [0.5, 0.5])])
        assert store.count() == 2
        hits = store.query([1.0, 1.0], n_results=2)
        ids = {h.entry_id for h in hits}
        assert ids == {10, 20}

    def test_rebuild_all_empty(self, store: VectorStore) -> None:
        store.upsert(1, [1.0, 0.0])
        store.rebuild_all([])
        assert store.count() == 0
```

- [ ] **Step 6: 运行全部 vector_store 测试**

Run:
```bash
uv run pytest tests/unit/engine/test_vector_store.py -v
```
Expected: PASS。

> 若 `rebuild_all` 的清空逻辑（`delete_collection` 后重建）在并发或重入下有问题，优先修复 `_rebuild_all_sync`；本测试为顺序调用，应通过。

- [ ] **Step 7: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿。

- [ ] **Step 8: 提交到分支**

```bash
git add bot/engine/vector_store.py tests/unit/engine/test_vector_store.py
git commit -m "feat(engine): 新增 VectorStore chromadb 向量存储 + VectorHit"
```

---

## Task 6: 改造 KeywordSearcher（依赖 MetadataStore + SearchResult 字段改名）

**Files:**
- Modify: `bot/engine/keyword_searcher.py`
- Modify: `tests/unit/engine/test_keyword_searcher.py`

### 6.1 改造点

- 协议从 `IndexProvider.get_entries() -> dict[str, dict]` 改为 `MetadataStoreProvider.get_all_entries() -> dict[int, MemeEntry]`。
- `SearchResult.entry_id: str → int`、`filename → image_path`。
- 搜索逻辑不变（jieba.posseg 去助词 + pylcs LCS）。`entry.text` 直接来自 `MemeEntry.text`（已无空格），无需再 strip。

- [ ] **Step 1: 重写测试 — MockIndex 改为 mock MetadataStore**

将 `tests/unit/engine/test_keyword_searcher.py` 整体替换为：

```python
"""KeywordSearcher 单元测试。"""

from __future__ import annotations

import pytest

from bot.engine.keyword_searcher import KeywordSearcher, SearchResult
from bot.engine.metadata_store import MemeEntry


class MockMetadataStore:
    """模拟 MetadataStore，返回预定义的 entries 字典。"""

    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    """标准测试用的表情包索引数据（text 已无空格）。"""
    return {
        1: MemeEntry(id=1, image_path="cat.jpg", text="一只猫在跳起来抓蝴蝶哈哈哈"),
        2: MemeEntry(id=2, image_path="overtime.jpg", text="加班到凌晨三点的我"),
        3: MemeEntry(
            id=3, image_path="suspect.jpg", text="人家一片热忱你怎能以小人之心度君子之腹呢"
        ),
        4: MemeEntry(id=4, image_path="empty.jpg", text=""),
        5: MemeEntry(id=5, image_path="boss.jpg", text="当你的老板说今天要加班"),
        6: MemeEntry(id=6, image_path="sunday.jpg", text="周日晚上的加班通知"),
    }


@pytest.fixture
def searcher(sample_entries: dict[int, MemeEntry]) -> KeywordSearcher:
    return KeywordSearcher(MockMetadataStore(sample_entries))


class TestSearchResult:
    def test_create(self) -> None:
        r = SearchResult(
            entry_id=1, image_path="cat.jpg", text="一只猫", similarity=85.5
        )
        assert r.entry_id == 1
        assert r.image_path == "cat.jpg"
        assert r.text == "一只猫"
        assert r.similarity == 85.5


class TestInit:
    def test_default_threshold(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._threshold == 60.0

    def test_custom_threshold(self) -> None:
        assert KeywordSearcher(MockMetadataStore(), threshold=80.0)._threshold == 80.0

    def test_default_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._limit == 10

    def test_custom_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore(), limit=5)._limit == 5


class TestSearchExactSubstring:
    def test_short_keyword_in_long_text(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("小人之心")
        assert len(results) == 1
        assert results[0].entry_id == 3
        assert results[0].similarity == 100.0

    def test_keyword_hits_multiple(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        assert {r.entry_id for r in results} == {2, 5, 6}

    def test_full_text_match(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班到凌晨三点的我")
        assert len(results) == 1
        assert results[0].entry_id == 2
        assert results[0].similarity == 100.0


class TestSearchFuzzy:
    def test_partial_overlap(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_non_contiguous_match(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班凌晨通知")
        assert len(results) >= 1
        assert all(60.0 <= r.similarity < 100.0 for r in results)


class TestSearchWithParticleRemoval:
    def test_drops_particles(self, sample_entries: dict[int, MemeEntry]) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        results = s.search("了加班吗")
        assert {r.entry_id for r in results} == {2, 5, 6}
        assert all(r.similarity == 100.0 for r in results)

    def test_all_particles_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search("的呢吗") == []

    def test_content_word_with_embedded_particle_char(self) -> None:
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="了解详情请咨询")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("了解")
        assert len(results) == 1
        assert results[0].similarity == 100.0


class TestSearchEdgeCases:
    def test_empty_keyword(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("") == []

    def test_whitespace_keyword(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("   ") == []

    def test_no_match(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("火星文xyz") == []

    def test_empty_entries(self) -> None:
        assert KeywordSearcher(MockMetadataStore({})).search("加班") == []

    def test_all_empty_text(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text=""),
            2: MemeEntry(id=2, image_path="b.jpg", text=""),
        }
        assert KeywordSearcher(MockMetadataStore(entries)).search("加班") == []

    def test_below_threshold_filtered(self) -> None:
        entries = {1: MemeEntry(id=1, image_path="x.jpg", text="今天天气真好")}
        s = KeywordSearcher(MockMetadataStore(entries), threshold=90.0)
        assert s.search("加班") == []


class TestSearchResultOrder:
    def test_limit_truncation(self) -> None:
        entries = {
            i: MemeEntry(id=i, image_path=f"meme_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        assert len(s.search("加班")) == 5

    def test_perfect_score_filters_others(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班"),
            2: MemeEntry(id=2, image_path="b.jpg", text="加班到凌晨"),
            3: MemeEntry(id=3, image_path="c.jpg", text="加斑"),  # LCS=1, score=50
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班")
        assert {r.entry_id for r in results} == {1, 2}
        assert all(r.similarity == 100.0 for r in results)


class TestParticleRemovalFn:
    def test_removes_structural_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("的加班") == "加班"

    def test_removes_modal_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("加班了吗") == "加班"

    def test_all_particles_returns_empty(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert "".join(_remove_particles("的呢吗").split()) == ""
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -v
```
Expected: FAIL（`SearchResult` 无 `image_path` 字段、`MockIndex` 无 `get_all_entries`）。

- [ ] **Step 3: 重写 keyword_searcher.py**

将 `bot/engine/keyword_searcher.py` 整体替换为：

```python
"""关键词模糊搜索模块。

对 MetadataStore 中的 OCR 文本（已去除所有空白）使用 LCS（最长公共子序列）进行匹配。
"""

import logging
from dataclasses import dataclass
from typing import Protocol

import pylcs
import jieba.posseg as pseg

from bot.engine.metadata_store import MemeEntry

logger = logging.getLogger(__name__)

# jieba 词性标注中与助词相关的标签
# uj=助词(的/地/得), ul=语气词(了), uz=时态助词(着/了/过)
# us=结构助词(所/得以), y=语气词(吗/呢/吧), e=叹词(嗯/哦)
_PARTICLE_POS_TAGS: frozenset[str] = frozenset({"uj", "ul", "uz", "us", "y", "e"})


class MetadataStoreProvider(Protocol):
    """元数据提供者协议。"""

    def get_all_entries(self) -> dict[int, MemeEntry]:
        """返回全部条目，key=int(id)。"""
        ...


@dataclass
class SearchResult:
    """单条关键词搜索结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本（无空格）。
        similarity: 相似度分数，0-100。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float


def _remove_particles(text: str) -> str:
    """使用 jieba.posseg 过滤助词，返回纯文本。

    Args:
        text: 待处理的文本（搜索关键词）。

    Returns:
        移除助词后的纯文本；如果全部为助词则返回空字符串。
    """
    return "".join(
        word for word, flag in pseg.cut(text) if flag not in _PARTICLE_POS_TAGS
    )


class KeywordSearcher:
    """关键词模糊搜索引擎。

    使用 LCS 对 OCR 文本进行匹配：
    - 关键词是 OCR 文本的子串时，相似度为 100（精确命中）。
    - 否则按 LCS 长度与关键词长度的比值计算相似度。

    Attributes:
        threshold: 最低相似度阈值，默认 60。
        limit: 最大返回结果数，默认 10。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        threshold: float = 60.0,
        limit: int = 10,
    ) -> None:
        """初始化关键词搜索引擎。

        Args:
            metadata_store: 元数据存储，需实现 get_all_entries() 方法。
            threshold: 最低相似度阈值，默认 60。
            limit: 最大返回结果数，默认 10。
        """
        self._metadata_store = metadata_store
        self._threshold = threshold
        self._limit = limit

    @staticmethod
    def _compute_similarity(keyword: str, text: str) -> float:
        """计算关键词与文本的相似度。

        优先精确子串匹配（返回 100），否则使用 LCS 算法。

        Args:
            keyword: 搜索关键词（已去助词去空格）。
            text: OCR 文本（无空格）。

        Returns:
            相似度分数，0-100。
        """
        if keyword in text:
            return 100.0
        lcs_len = pylcs.lcs_sequence_length(keyword, text)
        return (lcs_len / len(keyword)) * 100

    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        先对 keyword 做分词 + 助词过滤 + 去所有空白，再用过滤后的文本做 LCS 匹配。

        Args:
            keyword: 用户输入的搜索关键词。

        Returns:
            按相似度降序排列的搜索结果列表，最多返回 limit 条。
            无匹配时返回空列表。
        """
        keyword = keyword.strip()
        if not keyword:
            logger.debug("关键词为空，返回空结果")
            return []

        cleaned = _remove_particles(keyword)
        cleaned = "".join(cleaned.split())  # 删除所有空白字符
        if not cleaned:
            logger.debug("关键词去助词后为空，返回空结果")
            return []

        entries = self._metadata_store.get_all_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        results: list[SearchResult] = []
        for entry in entries.values():
            text = entry.text
            if not text:
                continue

            score = self._compute_similarity(cleaned, text)
            if score >= self._threshold:
                results.append(
                    SearchResult(
                        entry_id=entry.id,
                        image_path=entry.image_path,
                        text=text,
                        similarity=score,
                    )
                )

        results.sort(key=lambda r: r.similarity, reverse=True)

        # 如果存在分数为 100 的结果，只返回分数为 100 的结果
        perfect_results = [r for r in results if r.similarity == 100.0]
        if perfect_results:
            results = perfect_results

        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            min(len(results), self._limit),
        )

        return results[: self._limit]
```

- [ ] **Step 4: 运行测试，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -v
```
Expected: PASS。

- [ ] **Step 5: 全量回归（无 key 环境集成测试自动 skip）**

Run:
```bash
uv run pytest -q
```
Expected: 全绿。

> 说明：`bot/bot.py` 仍把旧 `IndexManager`（实现 `get_entries`）注入给 `KeywordSearcher`，但单元测试用 mock，不触发；集成测试无 key 时 skip。`bot.py` 注入在 Task 10 修复。

- [ ] **Step 6: 提交到分支**

```bash
git add bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git commit -m "refactor(engine): KeywordSearcher 改用 MetadataStore，SearchResult 字段改 int id/image_path"
```

---

## Task 7: 改造 AIMatcher（VectorStore.query + MetadataStore + 字段改名）+ rerank 测试

**Files:**
- Modify: `bot/engine/ai_matcher.py`
- Modify: `tests/unit/engine/test_ai_matcher.py`
- Modify: `tests/unit/engine/test_rerank_service.py`

### 7.1 改造点

- 删除 `AIIndexProvider` 协议、`_coerce_vector`/`_cosine_similarity`/`_vector_norm`/`_entry_id_sort_key`/`_build_candidates`（纯 Python 余弦计算全部交给 ChromaDB）。
- `AIMatcher.__init__(metadata_store, vector_store, embedding_provider, rerank_provider=None, limit=10)`。
- `match` 新流程：`embed(description)` → `VectorStore.query(vec, n=limit)` → 对每个 `VectorHit` 用 `MetadataStore.get_entry(hit.entry_id)` 取 `image_path`/`text` 构候选 → rerank fallback → `AIMatchResult`。
- `AIMatchCandidate`/`AIMatchResult`：`entry_id:str→int`、`filename→image_path`。
- 用户描述 embedding 校验保留（空列表/零向量抛 `ValueError`），但维度匹配、坏索引向量等由 ChromaDB 处理（不再逐条校验）。
- 空索引判定：`VectorStore.count() == 0` 时返回 None（不调用 embed）。

- [ ] **Step 1: 重写 test_ai_matcher.py**

将 `tests/unit/engine/test_ai_matcher.py` 整体替换为：

```python
"""AIMatcher 单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from bot.engine.ai_matcher import AIMatchCandidate, AIMatchResult, AIMatcher
from bot.engine.metadata_store import MemeEntry
from bot.engine.vector_store import VectorHit


class MockVectorStore:
    """模拟 VectorStore。"""

    def __init__(
        self,
        hits: list[VectorHit] | None = None,
        count: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._hits = hits or []
        self._count = count
        self._error = error

    async def query(self, query_embedding: list[float], n_results: int = 10) -> list[VectorHit]:
        if self._error is not None:
            raise self._error
        return self._hits[:n_results]

    def count(self) -> int:
        return self._count


class MockMetadataStore:
    """模拟 MetadataStore，按 id 返回 MemeEntry。"""

    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)


class MockEmbeddingProvider:
    def __init__(
        self,
        embedding: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._embedding = embedding or [0.1, 0.2, 0.3]
        self._error = error
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return self._embedding


class MockReranker:
    def __init__(self, result: Any = 0, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[tuple[str, list[AIMatchCandidate]]] = []

    async def rerank(
        self, description: str, candidates: list[AIMatchCandidate]
    ) -> int:
        self.calls.append((description, candidates))
        if self._exc is not None:
            raise self._exc
        return self._result


def _make_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="cat.jpg", text="猫猫开心"),
        2: MemeEntry(id=2, image_path="work.jpg", text="加班心累"),
    }


def test_candidate_create() -> None:
    c = AIMatchCandidate(
        rank=1, entry_id=1, image_path="cat.jpg", text="一只猫", similarity=0.95
    )
    assert c.entry_id == 1
    assert c.image_path == "cat.jpg"


def test_result_create() -> None:
    r = AIMatchResult(
        entry_id=1, image_path="cat.jpg", text="一只猫", similarity=0.95, source="embedding"
    )
    assert r.entry_id == 1
    assert r.image_path == "cat.jpg"
    assert r.source == "embedding"


@pytest.mark.anyio
async def test_empty_description_returns_none_without_embedding_call() -> None:
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(
        MockMetadataStore(), MockVectorStore(count=0), provider, MockReranker()
    )
    result = await matcher.match("   ")
    assert result is None
    assert provider.calls == []


@pytest.mark.anyio
async def test_empty_vector_store_returns_none() -> None:
    """VectorStore.count()==0 时返回 None，不调用 embed。"""
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(
        MockMetadataStore(_make_entries()), MockVectorStore(count=0), provider
    )
    result = await matcher.match("找猫")
    assert result is None
    assert provider.calls == []


class TestEmbeddingRecall:
    @pytest.mark.anyio
    async def test_returns_top_hit(self) -> None:
        hits = [VectorHit(entry_id=2, similarity=0.9), VectorHit(entry_id=1, similarity=0.8)]
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(hits=hits, count=2),
            MockEmbeddingProvider(),
        )
        result = await matcher.match("心累加班")
        assert result == AIMatchResult(
            entry_id=2, image_path="work.jpg", text="加班心累",
            similarity=0.9, source="embedding",
        )

    @pytest.mark.anyio
    async def test_skip_hit_with_missing_metadata(self) -> None:
        """VectorHit 对应的 metadata 不存在时跳过该候选。"""
        hits = [VectorHit(entry_id=999, similarity=0.9), VectorHit(entry_id=1, similarity=0.8)]
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(hits=hits, count=2),
            MockEmbeddingProvider(),
        )
        result = await matcher.match("找猫")
        assert result is not None
        assert result.entry_id == 1

    @pytest.mark.anyio
    async def test_all_hits_missing_metadata_returns_none(self) -> None:
        hits = [VectorHit(entry_id=999, similarity=0.9)]
        matcher = AIMatcher(
            MockMetadataStore({}),
            MockVectorStore(hits=hits, count=1),
            MockEmbeddingProvider(),
        )
        result = await matcher.match("找猫")
        assert result is None

    @pytest.mark.anyio
    async def test_limit_passed_to_query(self) -> None:
        class CountingVectorStore(MockVectorStore):
            def __init__(self) -> None:
                super().__init__(hits=[VectorHit(1, 0.9)], count=1)
                self.last_n: int = 0

            async def query(self, query_embedding, n_results=10):
                self.last_n = n_results
                return await super().query(query_embedding, n_results)

        vs = CountingVectorStore()
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()), vs, MockEmbeddingProvider(), limit=5
        )
        await matcher.match("找猫")
        assert vs.last_n == 5


class TestEmbeddingProviderErrors:
    @pytest.mark.anyio
    async def test_provider_error_bubbles_up(self) -> None:
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(count=2),
            MockEmbeddingProvider(error=RuntimeError("embedding down")),
        )
        with pytest.raises(RuntimeError, match="embedding down"):
            await matcher.match("心累加班")

    @pytest.mark.anyio
    async def test_empty_query_vector_raises_value_error(self) -> None:
        provider = MockEmbeddingProvider()
        provider._embedding = []
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()), MockVectorStore(count=2), provider
        )
        with pytest.raises(ValueError, match="非空列表"):
            await matcher.match("心累加班")

    @pytest.mark.anyio
    async def test_zero_query_vector_raises_value_error(self) -> None:
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(count=2),
            MockEmbeddingProvider([0.0, 0.0]),
        )
        with pytest.raises(ValueError, match="零向量"):
            await matcher.match("心累加班")


class TestRerank:
    def _matcher(self, reranker: MockReranker, limit: int = 10) -> AIMatcher:
        hits = [
            VectorHit(entry_id=1, similarity=0.9),
            VectorHit(entry_id=2, similarity=0.8),
            VectorHit(entry_id=3, similarity=0.7),
        ]
        entries = {
            1: MemeEntry(id=1, image_path="first.jpg", text="第一张"),
            2: MemeEntry(id=2, image_path="second.jpg", text="第二张"),
            3: MemeEntry(id=3, image_path="third.jpg", text="第三张"),
        }
        return AIMatcher(
            MockMetadataStore(entries),
            MockVectorStore(hits=hits, count=3),
            MockEmbeddingProvider(),
            rerank_provider=reranker,
            limit=limit,
        )

    @pytest.mark.anyio
    async def test_valid_rank_selects_candidate(self) -> None:
        reranker = MockReranker(result=2)
        result = await self._matcher(reranker).match("选第二张")
        assert result is not None
        assert result.entry_id == 2
        assert result.source == "rerank"
        assert [c.rank for c in reranker.calls[0][1]] == [1, 2, 3]

    @pytest.mark.anyio
    async def test_limit_controls_candidates(self) -> None:
        reranker = MockReranker(result=1)
        m = self._matcher(reranker, limit=2)
        await m.match("只看两个")
        assert len(reranker.calls[0][1]) == 2

    @pytest.mark.anyio
    async def test_zero_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(result=0)).match("放弃精排")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_out_of_range_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(result=99)).match("越界")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_non_integer_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(result="2")).match("非整数")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_exception_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(exc=RuntimeError("down"))).match("失败")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```
Expected: FAIL（`AIMatcher` 签名不符、`AIMatchCandidate` 无 `image_path`）。

- [ ] **Step 3: 重写 ai_matcher.py**

将 `bot/engine/ai_matcher.py` 整体替换为：

```python
"""AI 语义匹配模块。

先用 embedding 做语义召回（ChromaDB），再可选调用精排 provider 选出最终表情包。
"""

import logging
import math
from dataclasses import dataclass, replace
from typing import Protocol

from bot.engine.metadata_store import MetadataStore, MemeEntry
from bot.engine.protocols import EmbeddingProvider
from bot.engine.vector_store import VectorHit, VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIMatchCandidate:
    """Embedding 阶段的候选表情包。

    Attributes:
        rank: 临时候选序号，1-based。
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本。
        similarity: 余弦相似度。
    """

    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float


class RerankProvider(Protocol):
    """候选精排服务协议。"""

    async def rerank(
        self, description: str, candidates: list[AIMatchCandidate]
    ) -> int:
        """从候选中选出最匹配的临时序号。

        Args:
            description: 用户自然语言描述。
            candidates: embedding 阶段 Top N 候选。

        Returns:
            1-based 临时候选序号；返回 0 表示放弃精排。
        """
        ...


@dataclass(frozen=True)
class AIMatchResult:
    """AI 匹配最终结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本。
        similarity: embedding 余弦相似度。
        source: 结果来源，取值为 "embedding" 或 "rerank"。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str


class AIMatcher:
    """AI 表情包匹配器。

    先对用户描述生成 embedding，再用 VectorStore.query 从 ChromaDB 召回 Top N，
    从 MetadataStore 取 metadata 构候选。可选 reranker 精排。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None:
        """初始化 AI 匹配器。

        Args:
            metadata_store: 元数据存储，按 id 取 MemeEntry。
            vector_store: 向量存储，query 召回 Top-N。
            embedding_provider: 文本向量化服务提供者。
            rerank_provider: 可选的精排服务提供者。
            limit: 候选召回上限。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._rerank_provider = rerank_provider
        self._limit = limit

    async def match(self, description: str) -> AIMatchResult | None:
        """根据自然语言描述匹配一个表情包。

        Args:
            description: 用户输入的自然语言描述。

        Returns:
            匹配结果；空描述、向量库为空或无有效候选时返回 None。

        Raises:
            ValueError: 用户描述 embedding 为空、非数字或为零向量。
        """
        description = description.strip()
        if not description:
            logger.debug("AI 匹配描述为空，返回空结果")
            return None

        if self._vector_store.count() == 0:
            logger.debug("向量库为空，返回空结果")
            return None

        query_vector = _coerce_vector(
            await self._embedding_provider.embed(description),
            context="用户描述 embedding",
        )
        if _vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")

        hits = await self._vector_store.query(query_vector, n_results=self._limit)
        candidates = self._build_candidates(hits)
        if not candidates:
            logger.info("AI 召回无候选：description=%r", description)
            return None

        if self._rerank_provider is None:
            return _candidate_to_result(candidates[0], source="embedding")

        rank = await self._rerank(description, candidates)
        if rank is None:
            return _candidate_to_result(candidates[0], source="embedding")

        return _candidate_to_result(candidates[rank - 1], source="rerank")

    async def _rerank(
        self, description: str, candidates: list[AIMatchCandidate]
    ) -> int | None:
        """调用 reranker 精排，失败或返回不可用时返回 None。"""
        try:
            rank = await self._rerank_provider.rerank(description, candidates)  # type: ignore[union-attr]
        except Exception:
            logger.warning("AI 精排调用失败，回退 embedding Top 1", exc_info=True)
            return None

        if type(rank) is not int:
            logger.warning("AI 精排返回非整数：rank=%r，回退 embedding Top 1", rank)
            return None

        if rank == 0:
            logger.info("AI 精排返回 0，回退 embedding Top 1")
            return None

        if rank < 1 or rank > len(candidates):
            logger.warning(
                "AI 精排返回越界序号：rank=%s, candidates=%d，回退 embedding Top 1",
                rank,
                len(candidates),
            )
            return None

        return rank

    def _build_candidates(self, hits: list[VectorHit]) -> list[AIMatchCandidate]:
        """将 VectorHit 转为候选，跳过 metadata 缺失的 hit。

        Args:
            hits: VectorStore.query 返回的召回结果（已按相似度降序）。

        Returns:
            带 rank 的候选列表（1-based，顺序与 hits 一致）。
        """
        candidates: list[AIMatchCandidate] = []
        for hit in hits:
            entry = self._metadata_store.get_entry(hit.entry_id)
            if entry is None:
                logger.warning("召回 hit 的 metadata 缺失，跳过：entry_id=%s", hit.entry_id)
                continue
            candidates.append(
                AIMatchCandidate(
                    rank=0,
                    entry_id=entry.id,
                    image_path=entry.image_path,
                    text=entry.text,
                    similarity=hit.similarity,
                )
            )
        return [
            replace(candidate, rank=rank)
            for rank, candidate in enumerate(candidates, start=1)
        ]


def _candidate_to_result(candidate: AIMatchCandidate, source: str) -> AIMatchResult:
    """将候选转换为最终结果。"""
    return AIMatchResult(
        entry_id=candidate.entry_id,
        image_path=candidate.image_path,
        text=candidate.text,
        similarity=candidate.similarity,
        source=source,
    )


def _coerce_vector(vector: object, *, context: str) -> list[float]:
    """将向量转换为浮点数列表。

    Args:
        vector: 原始向量数据。
        context: 出错时用于日志的上下文描述。

    Returns:
        浮点数列表。

    Raises:
        ValueError: 向量不是非空列表或包含非有限数字元素。
    """
    if not isinstance(vector, list) or not vector:
        raise ValueError(f"{context} 不是非空列表")

    values: list[float] = []
    for value in vector:
        if isinstance(value, bool):
            raise ValueError(f"{context} 包含非数字元素")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context} 包含非数字元素") from exc
        if not math.isfinite(number):
            raise ValueError(f"{context} 包含非有限数字")
        values.append(number)

    return values


def _vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))
```

- [ ] **Step 4: 运行 test_ai_matcher，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```
Expected: PASS。

- [ ] **Step 5: 修改 test_rerank_service.py 的 AIMatchCandidate 字段**

在 `tests/unit/engine/test_rerank_service.py` 中，将 `sample_candidates` fixture 的 `filename=` 改为 `image_path=`、`entry_id="1"` 改为 `entry_id=1`：

```python
@pytest.fixture
def sample_candidates() -> list[AIMatchCandidate]:
    """创建测试用候选列表。"""
    return [
        AIMatchCandidate(rank=1, entry_id=1, image_path="a.jpg", text="开心", similarity=0.9),
        AIMatchCandidate(rank=2, entry_id=2, image_path="b.jpg", text="难过", similarity=0.8),
        AIMatchCandidate(rank=3, entry_id=3, image_path="c.jpg", text="生气", similarity=0.7),
    ]
```

- [ ] **Step 6: 运行 test_rerank_service，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_rerank_service.py -v
```
Expected: PASS。

- [ ] **Step 7: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿（集成测试无 key skip）。

- [ ] **Step 8: 提交到分支**

```bash
git add bot/engine/ai_matcher.py tests/unit/engine/test_ai_matcher.py tests/unit/engine/test_rerank_service.py
git commit -m "refactor(engine): AIMatcher 改用 VectorStore.query + MetadataStore，候选/结果字段改 int id/image_path"
```

---

## Task 8: OCR 返回去除所有空白

**Files:**
- Modify: `bot/engine/deepseek_ocr.py`
- Modify: `bot/engine/paddle_ocr.py`
- Modify: `tests/unit/engine/test_deepseek_ocr.py`
- Modify: `tests/unit/engine/test_paddle_ocr.py`

### 8.1 改造点

- `deepseek_ocr.py`：`ocr()` 返回前 `return "".join(text.split())`。
- `paddle_ocr.py`：`ocr()` 返回前 `return "".join(full_text.split())`。
- docstring 更新：返回值约定为"去除所有空白后的文本"。
- 测试断言更新：含空格的预期文本改为去空格版本。

- [ ] **Step 1: 修改 deepseek_ocr.py — ocr() 返回前去空格**

编辑 `bot/engine/deepseek_ocr.py`，将 `ocr()` 末尾：

```python
        raw = response.choices[0].message.content or ""
        text = _clean_ocr_result(raw)
        logger.debug("OCR 完成: %s → %d 字符", path.name, len(text))
        return text
```

改为：

```python
        raw = response.choices[0].message.content or ""
        text = "".join(_clean_ocr_result(raw).split())
        logger.debug("OCR 完成: %s → %d 字符", path.name, len(text))
        return text
```

并更新 `ocr()` docstring 的 Returns 为：

```
            识别到的文本字符串（已清洗定位标记并去除所有空白字符）。
```

- [ ] **Step 2: 修改 paddle_ocr.py — ocr() 返回前去空格**

编辑 `bot/engine/paddle_ocr.py`，将 `ocr()` 末尾：

```python
        full_text = " ".join(texts)
        logger.debug("PaddleOCR 完成: %s → %s", image_path, full_text)
        return full_text
```

改为：

```python
        full_text = "".join(" ".join(texts).split())
        logger.debug("PaddleOCR 完成: %s → %s", image_path, full_text)
        return full_text
```

并更新 `ocr()` docstring 的 Returns 为：

```
            识别到的文本字符串（已去除所有空白字符，可能为空字符串）。
```

- [ ] **Step 3: 更新 test_deepseek_ocr.py 断言**

在 `tests/unit/engine/test_deepseek_ocr.py` 的 `TestOcr` 中：
- `test_normal_ocr_with_ref_tags`：`assert result == "不可惊扰先生真乃奇人也"`（原 `"不可惊扰 先生真乃奇人也"` 去空格）。
- 其余不含空格的断言（`"纯文本识别结果"`、`""`）不变。

具体替换：

```python
        result = await service.ocr(str(img))
        assert result == "不可惊扰先生真乃奇人也"
```

并在 `TestOcr` 末尾新增一个去空格断言用例：

```python
    @pytest.mark.asyncio
    async def test_ocr_strips_all_whitespace(self, tmp_path) -> None:
        """OCR 返回去除所有空白字符。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "<|ref|>加 班\t心\n累<|/ref|><|det|>[[1,2]]<|/det|>"
        mock_response.choices = [mock_choice]

        service = DeepSeekOcrService(api_key="test-key")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == "加班心累"
```

- [ ] **Step 4: 更新 test_paddle_ocr.py 断言**

在 `tests/unit/engine/test_paddle_ocr.py` 中，将所有含空格的预期结果改为去空格版本：
- `assert result == "第一行 第二行"` → `assert result == "第一行第二行"`（多处）
- `assert result == "hello world"` → `assert result == "helloworld"`
- `assert result == "皇叔入住的话 能使我东吴人丁兴旺"` → `assert result == "皇叔入住的话能使我东吴人丁兴旺"`
- `assert result == "你走了我们吃什么"` 不变（无空格）
- `assert result == "从dict提取的文本"` 不变
- `assert result == "12345"` 不变

新增一个去空格用例：

```python
    @pytest.mark.asyncio
    async def test_ocr_strips_all_whitespace(self) -> None:
        """OCR 返回去除所有空白字符。"""
        service = PaddleOcrClientService(access_token="t")
        mock_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = "加 班\t心\n累"
        mock_result.pages = [mock_page]
        service._client.ocr = AsyncMock(return_value=mock_result)

        result = await service.ocr("/path/to/image.png")
        assert result == "加班心累"
```

> 具体行号以实际文件为准；用 `rg "第一行 第二行|hello world|皇叔入住的话 能使我东吴人丁兴旺" tests/unit/engine/test_paddle_ocr.py` 定位全部需改断言。

- [ ] **Step 5: 运行两个 OCR 测试**

Run:
```bash
uv run pytest tests/unit/engine/test_deepseek_ocr.py tests/unit/engine/test_paddle_ocr.py -v
```
Expected: PASS。

- [ ] **Step 6: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿。

- [ ] **Step 7: 提交到分支**

```bash
git add bot/engine/deepseek_ocr.py bot/engine/paddle_ocr.py tests/unit/engine/test_deepseek_ocr.py tests/unit/engine/test_paddle_ocr.py
git commit -m "feat(ocr): OCR 返回去除所有空白字符，存储/搜索文本统一无空格"
```

---

## Task 9: 重写 IndexManager 为薄编排 + 删除 embedding_codec 测试

**Files:**
- Modify: `bot/engine/index_manager.py`（整体重写）
- Modify: `tests/unit/engine/test_index_manager.py`（整体重写）
- Delete: `tests/unit/engine/test_embedding_codec.py`

### 9.1 设计要点

**保留**：`SyncResult`、`AddResult`（字段改名：`entry_id:str|None→int|None`、`replaced_filename→replaced_image_path`）、`IndexCorruptedError`、`CompressionError`、`OcrError`、`EmbeddingError`、`OcrProvider` 协议、`resolve_unique_filename` 纯函数、`SUPPORTED_EXTENSIONS` 类属性。

**删除**：`encode_embedding`/`decode_embedding`/`normalize_text`/`compute_text_hash`/`dedup_key`/`is_blank_text`，全部 JSON 读写方法，`_entries`/`_dedup_index`/`_embeddings`/`index_version` 属性。

**薄编排接口**（spec 5.5）：`__init__(metadata_store, vector_store, memes_dir, no_text_dir=None, ocr_provider=None, embedding_provider=None, optimizer=None, sync_concurrency=None)` / `load()` / `sync_with_filesystem()` / `add_single_file(filename)` / `acquire_lock()` / `release_lock()` / `is_locked` / `entry_count`。

**sync 四阶段**（在 `_lock` 独占下，spec §6）：
- 阶段0 一致性修复：对齐 sqlite↔chroma id 集合（sqlite 有 chroma 无 → 重 embed upsert；chroma 有 sqlite 无 → remove 孤儿；chroma 为空 sqlite 有数据 → rebuild_all）。
- 阶段1 删除：`entry.image_path ∉ existing_files` → `MetadataStore.remove(id)` 后 `VectorStore.remove(id)`。
- 阶段2 新增：新图并行 OCR→embed，串行三分类（`not text` 移图 / `text ∈ winner_keys` 删新图 / 正常 `add`+`upsert`）。`winner_keys` 初始 = 已有条目 text 集合。

**写入一致性**：统一"先 sqlite 后 chroma"。`/add` 正常新增 `add` 后 `upsert` 失败回滚 `remove`+删图；去重替换 `update(image_path=新)` 后 `upsert` 失败回滚 `update(image_path=旧)`+删新图，最后删旧图；`/refresh` 单条 upsert 失败回滚 `remove` 记 `failed` 继续。

**同步 I/O 包装**：`MetadataStore` 方法为同步，用 `asyncio.to_thread` 包装避免阻塞事件循环；`VectorStore` 方法已 async。

**测试策略**：用 `FakeMetadataStore`（内存 `dict[int, MemeEntry]`，实现真接口）+ `FakeVectorStore`（内存 `dict[int, list[float]]`）+ `MockOcr`/`MockEmbedding`/`MockOptimizer`，端到端验证 sync 与 add 逻辑，无真实 sqlite/chroma I/O。

- [ ] **Step 1: 重写 test_index_manager.py（完整替换）**

将 `tests/unit/engine/test_index_manager.py` 整体替换为：

```python
"""IndexManager 薄编排单元测试。

用 FakeMetadataStore / FakeVectorStore（内存实现真接口）+ mock providers，
端到端验证 sync 四阶段与 add_single_file，无真实 sqlite/chroma I/O。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    EmbeddingError,
    IndexCorruptedError,
    IndexManager,
    OcrError,
    SyncResult,
    resolve_unique_filename,
)
from bot.engine.metadata_store import MemeEntry


# ---------------------------------------------------------------------------
# Fake stores
# ---------------------------------------------------------------------------


class FakeMetadataStore:
    """内存 MetadataStore，实现真接口，用于 IndexManager 测试。"""

    def __init__(self) -> None:
        self._entries: dict[int, MemeEntry] = {}
        self._next_auto = 1

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return dict(self._entries)

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)

    def get_by_filename(self, image_path: str) -> MemeEntry | None:
        for e in self._entries.values():
            if e.image_path == image_path:
                return e
        return None

    def get_id_by_text(self, text: str) -> int | None:
        for eid, e in self._entries.items():
            if e.text == text:
                return eid
        return None

    def find_next_id(self) -> int:
        if not self._entries:
            return 1
        ids = set(self._entries)
        for i in range(1, max(ids) + 2):
            if i not in ids:
                return i
        return max(ids) + 1

    def entry_count(self) -> int:
        return len(self._entries)

    def get_all_text(self) -> list[tuple[int, str]]:
        return [(eid, e.text) for eid, e in sorted(self._entries.items())]

    def add(self, image_path, text, speaker=None, tags=None) -> int:
        eid = self.find_next_id()
        self._entries[eid] = MemeEntry(id=eid, image_path=image_path, text=text, speaker=speaker, tags=tags or [])
        return eid

    def add_with_id(self, entry_id, image_path, text, speaker=None, tags=None) -> int:
        self._entries[entry_id] = MemeEntry(id=entry_id, image_path=image_path, text=text, speaker=speaker, tags=tags or [])
        return entry_id

    def update(self, entry_id, *, image_path=None, text=None, speaker=None, tags=None) -> bool:
        e = self._entries.get(entry_id)
        if e is None:
            return False
        new_image = image_path if image_path is not None else e.image_path
        new_text = text if text is not None else e.text
        new_speaker = speaker if speaker is not None else e.speaker
        new_tags = tags if tags is not None else e.tags
        self._entries[entry_id] = MemeEntry(id=entry_id, image_path=new_image, text=new_text, speaker=new_speaker, tags=new_tags)
        return True

    def remove(self, entry_id) -> bool:
        return self._entries.pop(entry_id, None) is not None


class FakeVectorStore:
    """内存 VectorStore，实现真接口。"""

    def __init__(self) -> None:
        self._vecs: dict[int, list[float]] = {}
        self.upsert_error_for: int | None = None  # 触发某 id upsert 抛错

    def load(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def upsert(self, entry_id, embedding) -> None:
        if self.upsert_error_for == entry_id:
            raise RuntimeError(f"upsert failed for {entry_id}")
        self._vecs[entry_id] = list(embedding)

    async def remove(self, entry_id) -> None:
        self._vecs.pop(entry_id, None)

    async def remove_many(self, entry_ids) -> None:
        for i in entry_ids:
            self._vecs.pop(i, None)

    async def query(self, query_embedding, n_results=10) -> list:
        from bot.engine.vector_store import VectorHit
        sims = [(eid, sum(a * b for a, b in zip(query_embedding, vec))) for eid, vec in self._vecs.items()]
        sims.sort(key=lambda x: -x[1])
        return [VectorHit(entry_id=eid, similarity=s) for eid, s in sims[:n_results]]

    async def rebuild_all(self, items) -> None:
        self._vecs = {eid: list(vec) for eid, vec in items}

    def count(self) -> int:
        return len(self._vecs)

    def has(self, entry_id) -> bool:
        return entry_id in self._vecs


class MockOcr:
    def __init__(self, texts: dict[str, str] | None = None, default: str = "文字") -> None:
        self._texts = texts or {}
        self._default = default

    async def ocr(self, image_path: str) -> str:
        name = Path(image_path).name
        return self._texts.get(name, self._default)


class MockEmbed:
    def __init__(self, error_on: str | None = None) -> None:
        self._error_on = error_on
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._error_on is not None and text == self._error_on:
            raise RuntimeError("embed failed")
        # 用 text 长度生成确定性向量，便于测试稳定
        return [float(len(text)), 0.0]


class MockOptimizer:
    async def optimize(self, image_path) -> None:
        pass


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def work(tmp_path: Path) -> dict[str, Path]:
    memes = tmp_path / "memes"
    no_text = tmp_path / "meme_no_text"
    memes.mkdir()
    return {"memes": memes, "no_text": no_text, "data": tmp_path / "data"}


@pytest.fixture
def manager(work: dict[str, Path]) -> IndexManager:
    md = FakeMetadataStore()
    vs = FakeVectorStore()
    m = IndexManager(
        metadata_store=md,
        vector_store=vs,
        memes_dir=str(work["memes"]),
        no_text_dir=str(work["no_text"]),
        ocr_provider=MockOcr(),
        embedding_provider=MockEmbed(),
        optimizer=MockOptimizer(),
    )
    m.load()
    return m


# ---------------------------------------------------------------------------
# 数据类与异常
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_sync_result(self) -> None:
        r = SyncResult(added=3, deleted=1, failed=["bad.jpg"])
        assert r.added == 3 and r.deleted == 1 and r.deduped == 0
        assert r.no_text_moved == 0 and r.failed == ["bad.jpg"]

    def test_add_result_added(self) -> None:
        r = AddResult(entry_id=1, reason="added", text="猫")
        assert r.entry_id == 1 and r.reason == "added"
        assert r.replaced_image_path is None and r.moved_to is None

    def test_add_result_replaced(self) -> None:
        r = AddResult(entry_id=3, reason="replaced", replaced_image_path="old.jpg")
        assert r.entry_id == 3 and r.replaced_image_path == "old.jpg"

    def test_add_result_no_text(self) -> None:
        r = AddResult(entry_id=None, reason="no_text", moved_to="/x/blank.jpg")
        assert r.entry_id is None and r.moved_to == "/x/blank.jpg"


class TestExceptions:
    def test_index_corrupted(self) -> None:
        with pytest.raises(IndexCorruptedError):
            raise IndexCorruptedError("x")

    def test_compression_error(self) -> None:
        with pytest.raises(CompressionError):
            raise CompressionError("x")

    def test_ocr_error(self) -> None:
        with pytest.raises(OcrError):
            raise OcrError("x")

    def test_embedding_error(self) -> None:
        with pytest.raises(EmbeddingError):
            raise EmbeddingError("x")


class TestResolveUniqueFilename:
    def test_no_conflict(self, tmp_path: Path) -> None:
        p = resolve_unique_filename(tmp_path, "a.jpg")
        assert p == tmp_path / "a.jpg"

    def test_conflict_appends_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "a.jpg").write_text("x")
        p = resolve_unique_filename(tmp_path, "a.jpg")
        assert p == tmp_path / "a_2.jpg"


# ---------------------------------------------------------------------------
# load / 锁 / entry_count
# ---------------------------------------------------------------------------


class TestLoadAndLock:
    def test_load_delegates_to_stores(self, work: dict[str, Path]) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        m = IndexManager(metadata_store=md, vector_store=vs, memes_dir=str(work["memes"]))
        m.load()
        assert m.entry_count == 0

    def test_entry_count_reflects_store(self, manager: IndexManager) -> None:
        manager._metadata_store.add("a.jpg", "甲")  # type: ignore[attr-defined]
        assert manager.entry_count == 1

    @pytest.mark.asyncio
    async def test_acquire_lock(self, manager: IndexManager) -> None:
        assert await manager.acquire_lock() is True
        assert manager.is_locked is True
        assert await manager.acquire_lock() is False

    @pytest.mark.asyncio
    async def test_release_lock(self, manager: IndexManager) -> None:
        await manager.acquire_lock()
        manager.release_lock()
        assert manager.is_locked is False

    def test_release_when_not_locked_safe(self, manager: IndexManager) -> None:
        manager.release_lock()
        assert manager.is_locked is False


# ---------------------------------------------------------------------------
# sync 阶段0 一致性修复
# ---------------------------------------------------------------------------


class TestSyncPhase0Consistency:
    @pytest.mark.asyncio
    async def test_orphan_vector_removed(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """chroma 有 id、sqlite 无 → 删孤儿向量。"""
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        await vs.add_with_id if False else None
        # 直接塞一条孤儿向量（sqlite 无对应 entry）
        vs._vecs[99] = [1.0, 0.0]
        result = await manager.sync_with_filesystem()
        assert not vs.has(99)
        # 没有图片新增/删除
        assert result.added == 0 and result.deleted == 0

    @pytest.mark.asyncio
    async def test_missing_vector_re_embedded(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """sqlite 有 id、chroma 无 → 按 sqlite text 重 embed upsert。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        # sqlite 有条目，chroma 空
        md._entries[1] = MemeEntry(id=1, image_path="a.jpg", text="猫")
        # memes/ 里要有对应图，否则阶段1 会删它
        (work["memes"] / "a.jpg").write_text("x")
        result = await manager.sync_with_filesystem()
        assert vs.has(1)
        assert result.added == 0  # 不是新增，是阶段0 修复

    @pytest.mark.asyncio
    async def test_chroma_empty_rebuild_all(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """chroma 完全为空、sqlite 有数据 → rebuild_all 全量重建。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="a.jpg", text="猫")
        md._entries[2] = MemeEntry(id=2, image_path="b.jpg", text="狗")
        (work["memes"] / "a.jpg").write_text("x")
        (work["memes"] / "b.jpg").write_text("x")
        await manager.sync_with_filesystem()
        assert vs.count() == 2
        assert vs.has(1) and vs.has(2)


# ---------------------------------------------------------------------------
# sync 阶段1 删除
# ---------------------------------------------------------------------------


class TestSyncPhase1Delete:
    @pytest.mark.asyncio
    async def test_delete_removed_image(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """图片已删、索引还在 → sqlite + chroma 都删。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="gone.jpg", text="猫")
        vs._vecs[1] = [1.0, 0.0]
        # memes/ 无 gone.jpg
        result = await manager.sync_with_filesystem()
        assert result.deleted == 1
        assert md.get_entry(1) is None
        assert not vs.has(1)


# ---------------------------------------------------------------------------
# sync 阶段2 新增
# ---------------------------------------------------------------------------


class TestSyncPhase2Add:
    @pytest.mark.asyncio
    async def test_add_new_image(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """新图 OCR 有文字 → 进 sqlite + chroma。"""
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="新文字")  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.added == 1
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        assert md.entry_count() == 1
        assert vs.count() == 1

    @pytest.mark.asyncio
    async def test_no_text_moved(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """OCR 无文字 → 移到 meme_no_text/，不进索引。"""
        (work["memes"] / "blank.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="   ")  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.no_text_moved == 1
        assert result.added == 0
        assert not (work["memes"] / "blank.jpg").exists()
        assert (work["no_text"] / "blank.jpg").exists()

    @pytest.mark.asyncio
    async def test_dedup_new_image_same_text(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """新图 text 命中已有条目 → 删新图，deduped++。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="old.jpg", text="重复文字")
        (work["memes"] / "old.jpg").write_text("x")
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="重复文字")  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.deduped == 1
        assert result.added == 0
        # 新图被删，旧图保留
        assert not (work["memes"] / "new.jpg").exists()
        assert (work["memes"] / "old.jpg").exists()

    @pytest.mark.asyncio
    async def test_idempotent_second_sync(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """重复同步无变化。"""
        (work["memes"] / "a.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="文字")  # type: ignore[attr-defined]
        r1 = await manager.sync_with_filesystem()
        assert r1.added == 1
        r2 = await manager.sync_with_filesystem()
        assert r2.added == 0 and r2.deleted == 0 and r2.deduped == 0

    @pytest.mark.asyncio
    async def test_ocr_failure_recorded(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """单图 OCR 失败 → 记 failed，其他图继续。"""
        (work["memes"] / "bad.jpg").write_text("x")
        (work["memes"] / "good.jpg").write_text("x")
        manager._ocr_provider = MockOcr(texts={"bad.jpg": ""}, default="正常")  # type: ignore[attr-defined]
        # 让 bad.jpg 的 OCR 抛错：用自定义 provider
        class FailOcr:
            async def ocr(self, image_path: str) -> str:
                if Path(image_path).name == "bad.jpg":
                    raise RuntimeError("ocr down")
                return "正常"
        manager._ocr_provider = FailOcr()  # type: ignore[attr-defined]
        result = await manager.sync_with_filesystem()
        assert result.added == 1
        assert "bad.jpg" in result.failed

    @pytest.mark.asyncio
    async def test_empty_memes_dir(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """memes/ 为空 → 无变化。"""
        result = await manager.sync_with_filesystem()
        assert result.added == 0 and result.deleted == 0 and result.failed == []


# ---------------------------------------------------------------------------
# add_single_file
# ---------------------------------------------------------------------------


class TestAddSingleFile:
    @pytest.mark.asyncio
    async def test_added(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """正常新增。"""
        (work["memes"] / "pic.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="新文字")  # type: ignore[attr-defined]
        result = await manager.add_single_file("pic.jpg")
        assert result.entry_id == 1
        assert result.reason == "added"
        assert result.text == "新文字"
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        assert md.entry_count() == 1
        assert vs.count() == 1

    @pytest.mark.asyncio
    async def test_no_text_moved(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """无文字 → 移图，不进索引。"""
        (work["memes"] / "blank.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="   ")  # type: ignore[attr-defined]
        result = await manager.add_single_file("blank.jpg")
        assert result.entry_id is None
        assert result.reason == "no_text"
        assert result.moved_to is not None
        assert (work["no_text"] / "blank.jpg").exists()

    @pytest.mark.asyncio
    async def test_replaced_dedup(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """去重命中已有条目 → update image_path + upsert，删旧图。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="old.jpg", text="重复")
        vs._vecs[1] = [1.0, 0.0]
        (work["memes"] / "old.jpg").write_text("x")
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="重复")  # type: ignore[attr-defined]
        result = await manager.add_single_file("new.jpg")
        assert result.entry_id == 1
        assert result.reason == "replaced"
        assert result.replaced_image_path == "old.jpg"
        # sqlite 指向新图，text 不变
        assert md.get_entry(1).image_path == "new.jpg"
        assert md.get_entry(1).text == "重复"
        # 旧图删，新图留
        assert not (work["memes"] / "old.jpg").exists()
        assert (work["memes"] / "new.jpg").exists()

    @pytest.mark.asyncio
    async def test_added_upsert_failure_rolls_back(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """正常新增时 upsert 失败 → 回滚 sqlite + 删图 + 抛 EmbeddingError。"""
        (work["memes"] / "pic.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="新文字")  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        vs.upsert_error_for = 1  # 让 id=1 的 upsert 抛错
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        with pytest.raises(EmbeddingError):
            await manager.add_single_file("pic.jpg")
        # sqlite 回滚
        assert md.entry_count() == 0
        # 图片删除
        assert not (work["memes"] / "pic.jpg").exists()

    @pytest.mark.asyncio
    async def test_replaced_upsert_failure_rolls_back(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """去重替换时 upsert 失败 → 回滚 update(image_path=旧) + 删新图，旧图保留。"""
        md: FakeMetadataStore = manager._metadata_store  # type: ignore[attr-defined]
        vs: FakeVectorStore = manager._vector_store  # type: ignore[attr-defined]
        md._entries[1] = MemeEntry(id=1, image_path="old.jpg", text="重复")
        vs._vecs[1] = [1.0, 0.0]
        (work["memes"] / "old.jpg").write_text("x")
        (work["memes"] / "new.jpg").write_text("x")
        manager._ocr_provider = MockOcr(default="重复")  # type: ignore[attr-defined]
        vs.upsert_error_for = 1
        with pytest.raises(EmbeddingError):
            await manager.add_single_file("new.jpg")
        # 回滚：sqlite 仍指向旧图
        assert md.get_entry(1).image_path == "old.jpg"
        # 新图删，旧图保留
        assert not (work["memes"] / "new.jpg").exists()
        assert (work["memes"] / "old.jpg").exists()

    @pytest.mark.asyncio
    async def test_ocr_failure_raises(self, manager: IndexManager, work: dict[str, Path]) -> None:
        """OCR 失败 → 抛 OcrError。"""
        (work["memes"] / "pic.jpg").write_text("x")
        class FailOcr:
            async def ocr(self, image_path: str) -> str:
                raise RuntimeError("ocr down")
        manager._ocr_provider = FailOcr()  # type: ignore[attr-defined]
        with pytest.raises(OcrError):
            await manager.add_single_file("pic.jpg")
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/engine/test_index_manager.py -v 2>&1 | head -20
```
Expected: FAIL，`IndexManager` 旧签名不兼容、`AddResult.replaced_image_path` 不存在、`metadata_store`/`vector_store` 参数不接受。

- [ ] **Step 3: 重写 index_manager.py（整体替换）**

将 `bot/engine/index_manager.py` 整体替换为：

```python
"""索引管理模块 — 薄编排层。

持有 MetadataStore + VectorStore + providers，负责压缩→OCR→Embed 管道编排、
sync 四阶段（含阶段0跨库一致性修复）、跨库写入一致性、全局锁、并发上限、
去重/无文字移图。不直接写 SQL/Chroma，全部委托两个 Store。
"""

import asyncio
import itertools
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bot.engine.image_optimizer import ImageOptimizer
from bot.engine.metadata_store import MetadataStore, MemeEntry
from bot.engine.protocols import EmbeddingProvider
from bot.engine.vector_store import VectorStore

logger = logging.getLogger(__name__)


def resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。

    Returns:
        目标目录下不冲突的完整路径。
    """
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for n in itertools.count(2):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")


class IndexCorruptedError(Exception):
    """索引数据库结构损坏时抛出。"""


class CompressionError(RuntimeError):
    """图片压缩失败。"""


class OcrError(RuntimeError):
    """OCR 识别失败。"""


class EmbeddingError(RuntimeError):
    """Embedding 生成失败。"""


class OcrProvider(Protocol):
    """OCR 服务提供者协议。ocr() 返回去除所有空白后的文本。"""

    async def ocr(self, image_path: str) -> str:
        ...


@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因 text 命中已有条目而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class AddResult:
    """add_single_file() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID（int）；无文字移图场景为 None。
        reason: 结果类别：added / replaced / no_text。
        text: OCR 文本（无空格）。
        replaced_image_path: reason="replaced" 时为被删旧图路径，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
    """

    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    moved_to: str | None = None


class IndexManager:
    """索引管理薄编排层。

    持有 MetadataStore + VectorStore + providers，负责管道编排与跨库一致性。

    Attributes:
        _metadata_store: 元数据存储。
        _vector_store: 向量存储。
        _memes_dir: 表情包图片目录。
        _no_text_dir: 无文字图目录。
        _ocr_provider / _embedding_provider / _optimizer: providers。
        _lock: sync 独占 asyncio.Lock。
        _is_syncing: sync 是否在执行。
        _sync_semaphore / _add_sem: 并发上限信号量。

    Class Attributes:
        SUPPORTED_EXTENSIONS: 支持的图片扩展名集合。
        DEFAULT_SYNC_CONCURRENCY: 并行同步默认并发上限。
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )
    DEFAULT_SYNC_CONCURRENCY: int = 5

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: str,
        no_text_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizer | None = None,
        sync_concurrency: int | None = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            memes_dir: 表情包图片目录路径。
            no_text_dir: 无文字图目录；None 时取 memes_dir 同级的 meme_no_text/。
            ocr_provider: OCR 服务提供者。
            embedding_provider: Embedding 服务提供者。
            optimizer: 图片压缩器。
            sync_concurrency: 并发上限，None/非正用默认值。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = Path(memes_dir)
        if no_text_dir is not None:
            self._no_text_dir = Path(no_text_dir)
        else:
            self._no_text_dir = Path(memes_dir).parent / "meme_no_text"
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider
        self._optimizer = optimizer

        self._lock = asyncio.Lock()
        concurrency = (
            sync_concurrency
            if isinstance(sync_concurrency, int) and sync_concurrency > 0
            else self.DEFAULT_SYNC_CONCURRENCY
        )
        self._sync_semaphore = asyncio.Semaphore(concurrency)
        self._add_sem = asyncio.Semaphore(concurrency)
        self._is_syncing: bool = False

    # ------------------------------------------------------------------
    # load / 锁
    # ------------------------------------------------------------------

    def load(self) -> None:
        """委托两个 Store.load()。"""
        self._metadata_store.load()
        self._vector_store.load()
        logger.info("IndexManager 加载完成: %d 条记录", self.entry_count)

    async def acquire_lock(self) -> bool:
        """非阻塞尝试获取索引更新锁。"""
        if self._lock.locked():
            return False
        await self._lock.acquire()
        self._is_syncing = True
        logger.debug("索引更新锁已获取")
        return True

    def release_lock(self) -> None:
        """释放索引更新锁。"""
        self._is_syncing = False
        if self._lock.locked():
            self._lock.release()
            logger.debug("索引更新锁已释放")

    @property
    def is_locked(self) -> bool:
        """索引是否处于锁定状态。"""
        return self._is_syncing

    @property
    def entry_count(self) -> int:
        """当前索引条目总数。"""
        return self._metadata_store.entry_count()

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------

    async def sync_with_filesystem(self) -> SyncResult:
        """按文件名同步索引与 memes/ 目录（四阶段）。

        0. 一致性修复：对齐 sqlite ↔ chroma id 集合。
        1. 删除：memes/ 已不存在的图片。
        2. 新增：新图并行 OCR→embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []

        await self._sync_phase0_consistency(failed)
        deleted_count = await self._sync_phase1_delete()
        added_count, deduped_count, no_text_count = await self._sync_phase2_add(failed)

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
            added_count, deleted_count, deduped_count, no_text_count, len(failed),
        )
        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            failed=failed,
        )

    async def _run_sync(self, fn, *args, **kwargs):
        """用 asyncio.to_thread 包装 MetadataStore 同步方法，避免阻塞事件循环。"""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _sync_phase0_consistency(self, failed: list[str]) -> None:
        """阶段0：对齐 sqlite ↔ chroma。"""
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        sqlite_ids = set(entries)
        vs_count = self._vector_store.count()
        chroma_ids = await self._get_chroma_ids()

        # chroma 损坏/为空、sqlite 有数据 → rebuild_all 全量重 embed
        if vs_count == 0 and sqlite_ids:
            await self._rebuild_all_from_sqlite(entries, failed)
            return

        # sqlite 有、chroma 无 → 重 embed upsert
        missing = sqlite_ids - chroma_ids
        for eid in missing:
            text = entries[eid].text
            if not text:
                continue
            try:
                vec = await self._embedding_provider.embed(text)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("阶段0 重 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            await self._vector_store.upsert(eid, vec)

        # chroma 有、sqlite 无 → 删孤儿向量
        orphans = chroma_ids - sqlite_ids
        if orphans:
            logger.info("阶段0 清理孤儿向量: %s", orphans)
            await self._vector_store.remove_many(list(orphans))

    async def _get_chroma_ids(self) -> set[int]:
        """获取 chroma 当前所有 id（用 query 全量召回实现）。"""
        if self._vector_store.count() == 0:
            return set()
        # 用零向量召回最多 count 条，提取 id
        n = self._vector_store.count()
        hits = await self._vector_store.query([0.0] * 1024, n_results=n)
        return {h.entry_id for h in hits}

    async def _rebuild_all_from_sqlite(
        self, entries: dict[int, MemeEntry], failed: list[str]
    ) -> None:
        """chroma 为空、sqlite 有数据 → 全量重 embed 后 rebuild_all。"""
        items: list[tuple[int, list[float]]] = []
        for eid, entry in entries.items():
            if not entry.text:
                continue
            try:
                vec = await self._embedding_provider.embed(entry.text)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("阶段0 全量重建 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entry.image_path)
                continue
            items.append((eid, vec))
        await self._vector_store.rebuild_all(items)

    async def _sync_phase1_delete(self) -> int:
        """阶段1：删除 memes/ 已不存在的图片对应记录。先 sqlite 后 chroma。"""
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        deleted = 0
        for eid, entry in entries.items():
            if entry.image_path not in existing:
                logger.info("图片已删除，移除索引: id=%s, image_path=%s", eid, entry.image_path)
                await self._run_sync(self._metadata_store.remove, eid)
                await self._vector_store.remove(eid)
                deleted += 1
        return deleted

    async def _sync_phase2_add(
        self, failed: list[str]
    ) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR→embed，串行三分类。"""
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
        if not new_files:
            return (0, 0, 0)

        logger.info("开始并行处理 %d 张新增图片", len(new_files))
        raw = await asyncio.gather(
            *(self._process_new_file(fn) for fn in new_files),
            return_exceptions=True,
        )

        success: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                _, text, embedding = result
                success[filename] = (text, embedding)

        # winner_keys 初始 = 已有条目的 text 集合
        winner_keys: set[str] = {e.text for e in entries.values() if e.text}

        added = deduped = no_text_moved = 0
        for filename in sorted(success):
            text, embedding = success[filename]
            if not text:
                await self._run_sync(self._move_to_no_text, filename)
                no_text_moved += 1
                continue
            if text in winner_keys:
                (self._memes_dir / filename).unlink(missing_ok=True)
                logger.info("新图与已有索引去重，删除新图: filename=%s", filename)
                deduped += 1
                continue
            # 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite
            eid = await self._run_sync(self._metadata_store.add, filename, text)
            try:
                await self._vector_store.upsert(eid, embedding)
            except Exception as exc:
                logger.error("新增 upsert 失败，回滚 sqlite: id=%s, error=%s", eid, exc)
                await self._run_sync(self._metadata_store.remove, eid)
                failed.append(filename)
                continue
            winner_keys.add(text)
            added += 1
            logger.info("新增图片已加入索引: id=%s, filename=%s", eid, filename)

        return (added, deduped, no_text_moved)

    # ------------------------------------------------------------------
    # add_single_file
    # ------------------------------------------------------------------

    async def add_single_file(self, filename: str) -> AddResult:
        """处理单张已保存的图片：压缩→OCR→Embed→写入索引。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            AddResult 描述添加结果。

        Raises:
            CompressionError / OcrError / EmbeddingError: 管道失败。
        """
        async with self._add_sem:
            text, embedding = await self._process_image_pipeline(filename)
        return await self._write_entry(filename, text, embedding)

    async def _write_entry(self, filename: str, text: str, embedding: list[float]) -> AddResult:
        """三分类写入：无文字移图 / 去重替换 / 正常新增。

        写入顺序统一"先 sqlite 后 chroma"，失败可回滚。
        """
        # 1. 无文字 → 移图，不进索引
        if not text:
            moved_to = await self._run_sync(self._move_to_no_text, filename)
            logger.info("OCR 无文字，已移至无文字目录: filename=%s", filename)
            return AddResult(entry_id=None, reason="no_text", moved_to=moved_to)

        # 2. 去重命中已有条目 → update image_path + upsert，删旧图
        old_id = await self._run_sync(self._metadata_store.get_id_by_text, text)
        if old_id is not None:
            old_entry = await self._run_sync(self._metadata_store.get_entry, old_id)
            old_image_path = old_entry.image_path if old_entry else ""
            # 顺序：先改 sqlite 指向新图，再 upsert 向量，最后删旧图
            await self._run_sync(
                self._metadata_store.update, old_id, image_path=filename
            )
            try:
                await self._vector_store.upsert(old_id, embedding)
            except Exception as exc:
                logger.error("去重替换 upsert 失败，回滚 update: id=%s, error=%s", old_id, exc)
                await self._run_sync(
                    self._metadata_store.update, old_id, image_path=old_image_path
                )
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise EmbeddingError(f"去重替换 upsert 失败: {filename}") from exc
            # 删旧图（最后删，保证前序失败时旧图仍在）
            if old_image_path and old_image_path != filename:
                (self._memes_dir / old_image_path).unlink(missing_ok=True)
            logger.info("去重替换: id=%s, 旧=%s, 新=%s", old_id, old_image_path, filename)
            return AddResult(
                entry_id=old_id, reason="replaced", text=text,
                replaced_image_path=old_image_path,
            )

        # 3. 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite + 删图
        eid = await self._run_sync(self._metadata_store.add, filename, text)
        try:
            await self._vector_store.upsert(eid, embedding)
        except Exception as exc:
            logger.error("新增 upsert 失败，回滚 sqlite + 删图: id=%s, error=%s", eid, exc)
            await self._run_sync(self._metadata_store.remove, eid)
            (self._memes_dir / filename).unlink(missing_ok=True)
            raise EmbeddingError(f"新增 upsert 失败: {filename}") from exc
        logger.info("已添加索引记录: id=%s, filename=%s", eid, filename)
        return AddResult(entry_id=eid, reason="added", text=text)

    # ------------------------------------------------------------------
    # 管道与工具
    # ------------------------------------------------------------------

    def _scan_meme_files(self) -> set[str]:
        """扫描 memes/，返回受支持扩展名的文件名集合。"""
        return {
            f.name
            for f in self._memes_dir.iterdir()
            if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        }

    async def _process_image_pipeline(self, filename: str) -> tuple[str, list[float]]:
        """压缩 → OCR → Embedding。"""
        image_path = self._memes_dir / filename
        if self._optimizer is not None:
            try:
                await self._optimizer.optimize(str(image_path))
            except Exception as exc:
                raise CompressionError(f"图片压缩失败: {filename}") from exc
        if self._ocr_provider is None:
            raise OcrError("OCR 服务未注入")
        try:
            text = await self._ocr_provider.ocr(str(image_path))
        except Exception as exc:
            raise OcrError(f"OCR 调用失败: {filename}") from exc
        if self._embedding_provider is None:
            raise EmbeddingError("Embedding 服务未注入")
        try:
            embedding = await self._embedding_provider.embed(text)
        except Exception as exc:
            raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc
        return text, embedding

    async def _process_new_file(self, filename: str) -> tuple[str, str, list[float]]:
        """处理单张新增图片（受 _sync_semaphore 约束）。"""
        async with self._sync_semaphore:
            text, embedding = await self._process_image_pipeline(filename)
        return filename, text, embedding

    def _move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._no_text_dir, filename)
        shutil.move(str(src), str(dst))
        logger.warning("OCR 未识别到文字，已移至无文字目录: %s -> %s", filename, dst)
        return str(dst)
```

- [ ] **Step 4: 运行 test_index_manager，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```
Expected: PASS（全部测试类）。

> 若 `_get_chroma_ids` 在 FakeVectorStore 上因零向量维度问题失败，检查 FakeVectorStore.query 是否容忍任意维度输入（测试用的是 `[0.0]*1024`，Fake query 按 zip 计算，维度不匹配会返回 0 相似但仍返回 id——可接受）。若真实 VectorStore 在阶段0 用零向量 query 报错，改为用 `collection.get()` 获取全部 id；本任务先用 query 方案，集成测试（Task 12）再验证真实 chroma 行为，必要时切换为 `collection.get()`。

- [ ] **Step 5: 删除 test_embedding_codec.py**

Run:
```bash
git rm tests/unit/engine/test_embedding_codec.py
```
或直接删除文件：
```bash
rm tests/unit/engine/test_embedding_codec.py
```
说明：`encode_embedding`/`decode_embedding` 已删除，该测试不再适用（`decode_embedding` 逻辑内联在 Task 13 迁移脚本）。

- [ ] **Step 6: 更新 engine/__init__.py 导出**

编辑 `bot/engine/__init__.py`，整体替换为：

```python
"""engine 包 — MemePilot 核心引擎模块。

导出各子模块的公共接口，供插件层和外部代码使用。
"""

from .ai_matcher import (
    AIMatcher,
    AIMatchCandidate,
    AIMatchResult,
    RerankProvider,
)
from .embedding_service import EmbeddingService
from .image_optimizer import ImageOptimizer, OptimizeResult
from .index_manager import (
    AddResult,
    IndexCorruptedError,
    IndexManager,
    OcrProvider,
    SyncResult,
    resolve_unique_filename,
)
from .keyword_searcher import (
    KeywordSearcher,
    SearchResult,
)
from .metadata_store import MemeEntry, MetadataStore
from .vector_store import VectorHit, VectorStore
from .deepseek_ocr import DeepSeekOcrService
from .paddle_ocr import PaddleOcrClientService
from .protocols import EmbeddingProvider
from .rerank_service import RerankService

__all__ = [
    # protocols
    "EmbeddingProvider",
    # ai_matcher
    "AIMatcher",
    "AIMatchCandidate",
    "AIMatchResult",
    "RerankProvider",
    # embedding_service
    "EmbeddingService",
    # image_optimizer
    "ImageOptimizer",
    "OptimizeResult",
    # index_manager
    "AddResult",
    "IndexCorruptedError",
    "IndexManager",
    "OcrProvider",
    "SyncResult",
    "resolve_unique_filename",
    # keyword_searcher
    "KeywordSearcher",
    "SearchResult",
    # metadata_store
    "MemeEntry",
    "MetadataStore",
    # vector_store
    "VectorHit",
    "VectorStore",
    # ocr
    "DeepSeekOcrService",
    "PaddleOcrClientService",
    # rerank
    "RerankService",
]
```

> 移除：`AIIndexProvider`（ai_matcher 已删该协议）、`IndexProvider`（keyword_searcher 已删该协议）、`CompressionError`/`OcrError`/`EmbeddingError` 是否导出？它们仍在 `index_manager.py`，被 `meme_add.py` import。原 `__init__` 未导出它们（meme_add 直接 `from bot.engine.index_manager import`）。保持不导出，新增 `IndexCorruptedError` 导出。

- [ ] **Step 7: 语法检查 + 全量回归**

Run:
```bash
uv run python -m compileall bot tests
uv run pytest -q
```
Expected: 编译通过；单元测试全绿。

> 集成测试 `test_index_manager_api.py` / `test_ai_matcher_api.py` 仍用旧 `IndexManager(data_dir=..., memes_dir=..., ocr_provider=..., embedding_provider=...)` 签名，会失败——但无 key 环境下它们 skipif 自动跳过。有 key 环境下将在 Task 12 改造。

- [ ] **Step 8: 提交到分支**

```bash
git add bot/engine/index_manager.py bot/engine/__init__.py tests/unit/engine/test_index_manager.py tests/unit/engine/test_embedding_codec.py
git commit -m "refactor(engine): IndexManager 重写为薄编排（MetadataStore+VectorStore），删除 JSON/text_hash 纯函数与 embedding_codec 测试"
```

---

## Task 10: app_state + bot.py 注入新组件

**Files:**
- Modify: `bot/app_state.py`
- Modify: `bot/bot.py`
- Modify: `tests/unit/test_app_state.py`
- Modify: `tests/unit/test_bot.py`

### 10.1 改造点

- `app_state`：新增 `_metadata_store` / `_vector_store` 全局，`get_metadata_store()` / `get_vector_store()`，`init_app` 多收 `metadata_store` / `vector_store` 参数（位置参数，放在 `index_manager` 后、`ocr_service` 前）。
- `bot.py _on_startup`：创建 `MetadataStore(INDEX_DB_PATH)` + `VectorStore(CHROMA_DIR)` → `IndexManager(metadata_store, vector_store, memes_dir, ocr_provider, embedding_provider, optimizer, sync_concurrency)` → `AIMatcher(metadata_store, vector_store, embedding, rerank)` → `KeywordSearcher(metadata_store)` → `init_app(...)`。
- `_on_shutdown`：新增关闭两个 Store（`MetadataStore.close()` 同步、`VectorStore.close()` 同步）。

- [ ] **Step 1: 写失败测试 — app_state 新增 getter**

在 `tests/unit/test_app_state.py` 的 `_reset_globals` fixture 中加入新全局的清理，并新增测试类。

更新 `_reset_globals` fixture（在 yield 前后都加）：

```python
    app_state._metadata_store = None
    app_state._vector_store = None
```

新增测试类（追加到文件末尾）：

```python
class TestGetMetadataStore:
    def test_returns_instance(self) -> None:
        from bot.engine import MetadataStore

        md = MagicMock(spec=MetadataStore)
        app_state.init_app(
            MagicMock(), md, MagicMock(), MagicMock(), MagicMock()
        )
        assert app_state.get_metadata_store() is md

    def test_raises_when_not_initialized(self) -> None:
        with pytest.raises(RuntimeError, match="MetadataStore 尚未初始化"):
            app_state.get_metadata_store()


class TestGetVectorStore:
    def test_returns_instance(self) -> None:
        from bot.engine import VectorStore

        vs = MagicMock(spec=VectorStore)
        app_state.init_app(
            MagicMock(), MagicMock(), vs, MagicMock(), MagicMock()
        )
        assert app_state.get_vector_store() is vs

    def test_raises_when_not_initialized(self) -> None:
        with pytest.raises(RuntimeError, match="VectorStore 尚未初始化"):
            app_state.get_vector_store()
```

> 注意：`init_app` 新签名是 `init_app(index_manager, metadata_store, vector_store, ocr_service, embedding_service, image_optimizer=None, ai_matcher=None, keyword_searcher=None)`。现有 `TestInitApp` 测试用 `init_app(im, ocr, emb, ...)` 需同步更新为 5 个位置参数。更新 `test_sets_all_globals` 与 `test_overwrites_existing`：

```python
    def test_sets_all_globals(self) -> None:
        im = MagicMock()
        md = MagicMock()
        vs = MagicMock()
        ocr = MagicMock()
        emb = MagicMock()
        ai = MagicMock()
        ks = MagicMock()
        app_state.init_app(im, md, vs, ocr, emb, ai_matcher=ai, keyword_searcher=ks)
        assert app_state._index_manager is im
        assert app_state._metadata_store is md
        assert app_state._vector_store is vs
        assert app_state._ocr_service is ocr
        assert app_state._embedding_service is emb
        assert app_state._ai_matcher is ai
        assert app_state._keyword_searcher is ks

    def test_overwrites_existing(self) -> None:
        im1, md1, vs1, ocr1, emb1 = (MagicMock() for _ in range(5))
        im2, md2, vs2, ocr2, emb2 = (MagicMock() for _ in range(5))
        app_state.init_app(im1, md1, vs1, ocr1, emb1)
        app_state.init_app(im2, md2, vs2, ocr2, emb2)
        assert app_state._index_manager is im2
        assert app_state._metadata_store is md2
        assert app_state._vector_store is vs2
        assert app_state._ocr_service is ocr2
        assert app_state._embedding_service is emb2
```

并更新 `TestGetIndexManager` / `TestGetOcrService` / `TestGetEmbeddingService` 中所有 `init_app(MagicMock(), MagicMock(), MagicMock())` 调用为 5 个位置参数 `init_app(MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())`。

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/test_app_state.py -v
```
Expected: FAIL（`init_app` 不接受 `metadata_store`/`vector_store`、无 `get_metadata_store`）。

- [ ] **Step 3: 重写 app_state.py**

将 `bot/app_state.py` 整体替换为：

```python
"""共享实例管理模块。

模块级单例模式，供插件获取 IndexManager、MetadataStore、VectorStore、
OcrService、EmbeddingService、AIMatcher、KeywordSearcher。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from .engine import (
    AIMatcher,
    DeepSeekOcrService,
    EmbeddingService,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    MetadataStore,
    PaddleOcrClientService,
    VectorStore,
)

_index_manager: IndexManager | None = None
_metadata_store: MetadataStore | None = None
_vector_store: VectorStore | None = None
_ocr_service: DeepSeekOcrService | PaddleOcrClientService | None = None
_embedding_service: EmbeddingService | None = None
_image_optimizer: ImageOptimizer | None = None
_ai_matcher: AIMatcher | None = None
_keyword_searcher: KeywordSearcher | None = None


def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    vector_store: VectorStore,
    ocr_service: DeepSeekOcrService | PaddleOcrClientService,
    embedding_service: EmbeddingService,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        metadata_store: 元数据存储实例。
        vector_store: 向量存储实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
        image_optimizer: 图片压缩器实例，可选。
        ai_matcher: AI 匹配器实例，可选。
        keyword_searcher: 关键词搜索器实例，可选。
    """
    global _index_manager, _metadata_store, _vector_store, _ocr_service
    global _embedding_service, _image_optimizer, _ai_matcher, _keyword_searcher
    _index_manager = index_manager
    _metadata_store = metadata_store
    _vector_store = vector_store
    _ocr_service = ocr_service
    _embedding_service = embedding_service
    _image_optimizer = image_optimizer
    _ai_matcher = ai_matcher
    _keyword_searcher = keyword_searcher


def get_index_manager() -> IndexManager:
    """获取 IndexManager 单例。"""
    if _index_manager is None:
        raise RuntimeError("IndexManager 尚未初始化，请先调用 init_app()")
    return _index_manager


def get_metadata_store() -> MetadataStore:
    """获取 MetadataStore 单例。"""
    if _metadata_store is None:
        raise RuntimeError("MetadataStore 尚未初始化，请先调用 init_app()")
    return _metadata_store


def get_vector_store() -> VectorStore:
    """获取 VectorStore 单例。"""
    if _vector_store is None:
        raise RuntimeError("VectorStore 尚未初始化，请先调用 init_app()")
    return _vector_store


def get_ocr_service() -> DeepSeekOcrService | PaddleOcrClientService:
    """获取 OCR 服务单例。"""
    if _ocr_service is None:
        raise RuntimeError("OCR 服务尚未初始化，请先调用 init_app()")
    return _ocr_service


def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例。"""
    if _embedding_service is None:
        raise RuntimeError("EmbeddingService 尚未初始化，请先调用 init_app()")
    return _embedding_service


def get_image_optimizer() -> ImageOptimizer | None:
    """获取 ImageOptimizer 单例（未注入时返回 None）。"""
    return _image_optimizer


def get_ai_matcher() -> AIMatcher:
    """获取 AIMatcher 单例。"""
    if _ai_matcher is None:
        raise RuntimeError("AIMatcher 尚未初始化，请先调用 init_app()")
    return _ai_matcher


def get_keyword_searcher() -> KeywordSearcher:
    """获取 KeywordSearcher 单例。"""
    if _keyword_searcher is None:
        raise RuntimeError("KeywordSearcher 尚未初始化，请先调用 init_app()")
    return _keyword_searcher
```

- [ ] **Step 4: 运行 test_app_state，确认通过**

Run:
```bash
uv run pytest tests/unit/test_app_state.py -v
```
Expected: PASS。

- [ ] **Step 5: 重写 bot.py _on_startup / _on_shutdown**

编辑 `bot/bot.py`：

1. 更新 import（加入 `MetadataStore`、`VectorStore`、`INDEX_DB_PATH`、`CHROMA_DIR`，移除 `data_dir` 概念）：

```python
from bot.app_state import init_app
from bot.config import CHROMA_DIR, INDEX_DB_PATH, MEMES_DIR, PROJECT_ROOT, read_ocr_provider
from bot.engine import (
    AIMatcher,
    DeepSeekOcrService,
    EmbeddingService,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    MetadataStore,
    PaddleOcrClientService,
    RerankService,
    VectorStore,
)
```

2. 替换 `_on_startup` 中第 3 步起的索引装配逻辑（原 `data_dir = str(PROJECT_ROOT / "data")` 到 `init_app(...)` 之间）：

```python
    # 3. 创建存储与 IndexManager 并加载索引
    memes_dir = str(MEMES_DIR)
    sync_concurrency = _read_sync_concurrency()

    metadata_store = MetadataStore(str(INDEX_DB_PATH))
    vector_store = VectorStore(str(CHROMA_DIR))
    index_manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=memes_dir,
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        optimizer=image_optimizer,
        sync_concurrency=sync_concurrency,
    )
    index_manager.load()

    # 4. 创建搜索和匹配服务（可立即使用已有索引）
    ai_matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )
    keyword_searcher = KeywordSearcher(metadata_store)

    # 5. 注册到 app_state（Bot 立即可用）
    init_app(
        index_manager=index_manager,
        metadata_store=metadata_store,
        vector_store=vector_store,
        ocr_service=ocr_service,
        embedding_service=embedding_service,
        image_optimizer=image_optimizer,
        ai_matcher=ai_matcher,
        keyword_searcher=keyword_searcher,
    )
```

3. 更新 `_on_shutdown`，关闭两个 Store：

```python
async def _on_shutdown() -> None:
    """NoneBot2 关闭钩子 — 释放 OCR 服务与存储的会话/连接。"""
    from bot.app_state import get_ocr_service, get_metadata_store, get_vector_store

    try:
        ocr_service = get_ocr_service()
        await ocr_service.close()
        logger.info("OCR 服务 HTTP 会话已关闭")
    except RuntimeError:
        pass
    try:
        get_metadata_store().close()
        get_vector_store().close()
        logger.info("存储已关闭")
    except RuntimeError:
        pass
```

4. 更新 `_on_startup` docstring 与文件顶部 docstring 中"初始化引擎服务"的描述（提及 MetadataStore/VectorStore）。docstring 更新为：

```
    流程：
    1. 配置日志
    2. 创建 OCR / Embedding / Rerank / ImageOptimizer 服务
    3. 创建 MetadataStore + VectorStore + IndexManager 并加载索引
    4. 创建 AIMatcher / KeywordSearcher
    5. 注册到 app_state 供插件获取（Bot 立即可用）
    6. 后台执行 sync_with_filesystem()（不阻塞启动）
```

- [ ] **Step 6: 语法检查 bot.py**

Run:
```bash
uv run python -m compileall bot/bot.py
```
Expected: 编译通过。

- [ ] **Step 7: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 单元测试全绿（`test_bot.py` 只测 `_read_sync_concurrency` / `_read_bot_port`，不受影响；集成测试无 key skip）。

- [ ] **Step 8: 提交到分支**

```bash
git add bot/app_state.py bot/bot.py tests/unit/test_app_state.py
git commit -m "refactor(app): app_state 新增 metadata/vector store getter，bot.py 注入两个 Store"
```

---

## Task 11: 插件层 .filename → .image_path + 插件测试改造

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Modify: `bot/plugins/meme_ai.py`
- Modify: `bot/plugins/meme_add.py`
- Modify: `tests/unit/plugins/test_search_utils.py`
- Modify: `tests/unit/plugins/test_meme_ai.py`
- Modify: `tests/unit/plugins/test_meme_search.py`
- Modify: `tests/unit/plugins/test_meme_plain_text.py`
- Modify: `tests/unit/plugins/test_meme_add.py`
- Modify: `tests/unit/plugins/test_meme_refresh.py`

### 11.1 改造点

- `_search_utils.py`：两处 `results[0].filename` / `result.filename` → `.image_path`。局部变量 `image_path` 已存在（`image_path = MEMES_DIR / ...`），不冲突。
- `meme_ai.py`：`match_result.filename` → `.image_path`。
- `meme_add.py`：`result.replaced_filename` → `.replaced_image_path`（仅在 `result.reason == "replaced"` 分支的 `result.text` 引用旁无 replaced_filename 引用——检查：meme_add 当前只读 `result.reason` 和 `result.text`，不读 `replaced_filename`。所以 meme_add.py 可能无需改！确认后再动）。
- 测试：所有 `SearchResult(filename=...)` / `AIMatchResult(filename=...)` / `AddResult(entry_id="1", ...)` 改 `image_path=` / `entry_id=1`。

- [ ] **Step 1: 改 _search_utils.py**

编辑 `bot/plugins/_search_utils.py`，将：

```python
        image_path = MEMES_DIR / results[0].filename
```
改为：
```python
        image_path = MEMES_DIR / results[0].image_path
```

将：
```python
            image_path = MEMES_DIR / result.filename
```
改为：
```python
            image_path = MEMES_DIR / result.image_path
```

- [ ] **Step 2: 改 meme_ai.py**

编辑 `bot/plugins/meme_ai.py`，将：

```python
        image_path = MEMES_DIR / match_result.filename
```
改为：
```python
        image_path = MEMES_DIR / match_result.image_path
```

- [ ] **Step 3: 确认 meme_add.py 是否引用 replaced_filename**

Run:
```bash
rg -n "replaced_filename|\.filename" bot/plugins/meme_add.py
```
Expected: 无输出（meme_add 只读 `result.reason` / `result.text`，不引用 `replaced_filename`）。若有输出，将 `result.replaced_filename` → `result.replaced_image_path`。

> 说明：`meme_add.py` 的 `result.text` 在 `reason == "replaced"` 分支被 `_format_ocr_text(result.text)` 使用，`text` 字段名不变，无需改。

- [ ] **Step 4: 改 test_search_utils.py**

编辑 `tests/unit/plugins/test_search_utils.py`，将 `_make_search_result` 改为 `image_path=`：

```python
def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id, image_path=image_path, text=text, similarity=similarity
    )
```

并更新所有调用处的 `filename=` → `image_path=`、`entry_id="1"/"2"` → `entry_id=1/2`：
- `_make_search_result(entry_id="1", filename="a.jpg", text="甲")` → `_make_search_result(entry_id=1, image_path="a.jpg", text="甲")`
- `_make_search_result(entry_id="2", filename="b.jpg", text="乙")` → `(entry_id=2, image_path="b.jpg", text="乙")`
- `_make_search_result(entry_id="2", ...)` 的 `assert result.entry_id == "2"` → `assert result.entry_id == 2`
- `assert result.filename == "b.jpg"` → `assert result.image_path == "b.jpg"`
- `_make_search_result(filename="加班心累.jpg")` → `_make_search_result(image_path="加班心累.jpg")`

用 `rg -n "filename|entry_id=\"|entry_id='" tests/unit/plugins/test_search_utils.py` 定位全部需改处。

- [ ] **Step 5: 改 test_meme_ai.py**

编辑 `tests/unit/plugins/test_meme_ai.py`：
- `_make_ai_matcher` 默认 `AIMatchResult(entry_id="1", filename="加班心累.jpg", ...)` → `AIMatchResult(entry_id=1, image_path="加班心累.jpg", ...)`
- 第 373-375 行 `AIMatchResult(entry_id="1", filename="test.jpg", ...)` → `(entry_id=1, image_path="test.jpg", ...)`，对应 `assert result.entry_id == "1"`（若有）→ `== 1`。

用 `rg -n "filename|entry_id=\"|entry_id='" tests/unit/plugins/test_meme_ai.py` 定位。

- [ ] **Step 6: 改 test_meme_search.py**

编辑 `tests/unit/plugins/test_meme_search.py`，将 `_make_search_result` 定义与调用处的 `filename=` → `image_path=`、`entry_id="1"/"2"` → `entry_id=1/2`：

用 `rg -n "filename|entry_id=\"|entry_id='" tests/unit/plugins/test_meme_search.py` 定位全部需改处。典型：
- `entry_id: str = "1"` → `entry_id: int = 1`
- `filename: str = "test.jpg"` → `image_path: str = "test.jpg"`
- `entry_id=entry_id, filename=filename, ...` → `entry_id=entry_id, image_path=image_path, ...`
- `_make_search_result(filename="a.jpg")` → `_make_search_result(image_path="a.jpg")`
- `_make_search_result(entry_id="2", filename="b.jpg", text="乙")` → `(entry_id=2, image_path="b.jpg", text="乙")`
- `_make_search_result(entry_id="1", filename="a.jpg", text="甲")` → `(entry_id=1, image_path="a.jpg", text="甲")`
- `assert result.entry_id == "2"` → `== 2`

- [ ] **Step 7: 改 test_meme_plain_text.py**

Run:
```bash
rg -n "filename|entry_id=\"|entry_id='|SearchResult\(" tests/unit/plugins/test_meme_plain_text.py
```
若有 `SearchResult(filename=...)` 或 `entry_id="..."` 构造，改 `image_path=` / `entry_id=int`。若仅 `from bot.engine.keyword_searcher import SearchResult`（无构造），则无需改。

- [ ] **Step 8: 改 test_meme_add.py**

编辑 `tests/unit/plugins/test_meme_add.py`，将所有 `AddResult(entry_id="1", ...)` → `AddResult(entry_id=1, ...)`：

用 `rg -n "AddResult\(|entry_id=\"|replaced_filename" tests/unit/plugins/test_meme_add.py` 定位。典型：
- `AddResult(entry_id="1", reason="added", text="加班心好累")` → `AddResult(entry_id=1, reason="added", text="加班心好累")`
- 若有 `replaced_filename=` 断言 → `replaced_image_path=`

- [ ] **Step 9: 确认 test_meme_refresh.py 字段**

Run:
```bash
rg -n "filename|entry_id=\"|image_path|replaced" tests/unit/plugins/test_meme_refresh.py
```
`test_meme_refresh.py` 只用 `SyncResult(added=..., deleted=..., failed=...)`（字段名不变），应无需改。若 rg 输出含 `filename`/`entry_id="..."` 才改。

- [ ] **Step 10: 运行插件单元测试**

Run:
```bash
uv run pytest tests/unit/plugins/ -v
```
Expected: PASS。

- [ ] **Step 11: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿。

- [ ] **Step 12: 提交到分支**

```bash
git add bot/plugins/_search_utils.py bot/plugins/meme_ai.py tests/unit/plugins/test_search_utils.py tests/unit/plugins/test_meme_ai.py tests/unit/plugins/test_meme_search.py tests/unit/plugins/test_meme_plain_text.py tests/unit/plugins/test_meme_add.py
git commit -m "refactor(plugins): 结果类字段 filename→image_path、entry_id:str→int 同步改造插件与测试"
```

---

## Task 12: 集成测试改造（真实 sqlite + chroma）

**Files:**
- Modify: `tests/integration/test_index_manager_api.py`
- Modify: `tests/integration/test_ai_matcher_api.py`
- Modify: `tests/integration/test_rerank_service_api.py`

### 12.1 改造点

- `test_index_manager_api.py`：`IndexManager(data_dir=..., memes_dir=..., ocr_provider=..., embedding_provider=..., no_text_dir=...)` → 新签名（注入 `MetadataStore(INDEX_DB_PATH)` + `VectorStore(CHROMA_DIR)` 临时实例）。验证改为 `MetadataStore.get_all_entries()` / `VectorStore.count()`。`get_entries`/`get_embeddings` 不再存在。
- `test_ai_matcher_api.py`：`AIMatcher(index_provider=manager, embedding_provider=..., rerank_provider=...)` → `AIMatcher(metadata_store=..., vector_store=..., embedding_provider=..., rerank_provider=...)`。`result.filename` → `result.image_path`。
- `test_rerank_service_api.py`：`AIMatchCandidate(entry_id="1", filename="tired.jpg", ...)` → `(entry_id=1, image_path="tired.jpg", ...)`。

> 集成测试带 `skipif`，无 key 环境自动跳过；本任务改造其代码使其在新 API 下可运行，有 key 时才能验证。

- [ ] **Step 1: 改 test_index_manager_api.py**

将 `tests/integration/test_index_manager_api.py` 的 import 与 fixture 更新：

```python
from bot.engine.embedding_service import EmbeddingService
from bot.engine.index_manager import IndexManager
from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore
from bot.engine.deepseek_ocr import DeepSeekOcrService
```

更新 `work_dirs` fixture，加入 `index_db` / `chroma_dir`：

```python
@pytest.fixture
def work_dirs(tmp_path: Path) -> dict[str, Path]:
    data_dir = tmp_path / "data"
    memes_dir = tmp_path / "memes"
    no_text_dir = tmp_path / "meme_no_text"
    data_dir.mkdir()
    memes_dir.mkdir()
    return {
        "data_dir": data_dir,
        "memes_dir": memes_dir,
        "no_text_dir": no_text_dir,
        "index_db": data_dir / "index.db",
        "chroma_dir": data_dir / "chroma",
    }
```

在每个测试里替换 `IndexManager(...)` 构造（以 `test_sync_single_image` 为例）：

```python
    metadata_store = MetadataStore(str(work_dirs["index_db"]))
    vector_store = VectorStore(str(work_dirs["chroma_dir"]))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()
```

更新断言（`test_sync_single_image`）：

```python
    assert result.added == 1
    assert result.deleted == 0
    assert result.failed == []
    assert manager.entry_count == 1

    # 验证 sqlite 内容
    entries = metadata_store.get_all_entries()
    entry = list(entries.values())[0]
    assert "听天由命吧" in entry.text
    assert entry.image_path == "听天由命吧.png"

    # 验证 chroma 向量存在且数量正确
    assert vector_store.count() == 1
```

对 `test_sync_multiple_images` / `test_sync_delete_removed_image` / `test_sync_idempotent` 同样替换 `IndexManager(...)` 构造，并更新断言：删除 `manager.get_entries()` / `manager.get_embeddings()` 调用，改用 `metadata_store.get_all_entries()` / `vector_store.count()` / `entry.image_path`。

- [ ] **Step 2: 改 test_ai_matcher_api.py**

更新 import：

```python
from bot.engine.ai_matcher import AIMatcher
from bot.engine.embedding_service import EmbeddingService
from bot.engine.index_manager import IndexManager
from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore
from bot.engine.deepseek_ocr import DeepSeekOcrService
from bot.engine.rerank_service import RerankService
```

更新 `_build_index` helper：

```python
async def _build_index(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    image_names: list[str],
) -> tuple[IndexManager, MetadataStore, VectorStore]:
    """同步索引并返回就绪的 IndexManager 与两个 Store。"""
    _copy_fixture_images(work_dirs["memes_dir"], image_names)
    metadata_store = MetadataStore(str(work_dirs["index_db"]))
    vector_store = VectorStore(str(work_dirs["chroma_dir"]))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()
    await manager.sync_with_filesystem()
    return manager, metadata_store, vector_store
```

更新 `work_dirs` fixture（同 Step 1 加 `index_db`/`chroma_dir`）。

更新各测试，解包 `_build_index` 返回值并用新 `AIMatcher` 签名：

```python
    manager, metadata_store, vector_store = await _build_index(
        work_dirs, ocr_service, embedding_service, images
    )

    matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
    )
```

将 `assert result.filename in images` → `assert result.image_path in images`。其他 `result.text` / `result.source` / `result.similarity` 断言不变。

- [ ] **Step 3: 改 test_rerank_service_api.py**

更新 `meme_candidates` fixture：

```python
@pytest.fixture
def meme_candidates() -> list[AIMatchCandidate]:
    return [
        AIMatchCandidate(rank=1, entry_id=1, image_path="tired.jpg", text="加班到凌晨好累啊", similarity=0.85),
        AIMatchCandidate(rank=2, entry_id=2, image_path="happy.jpg", text="今天心情真好开心", similarity=0.82),
        AIMatchCandidate(rank=3, entry_id=3, image_path="angry.jpg", text="气死我了这什么鬼", similarity=0.78),
        AIMatchCandidate(rank=4, entry_id=4, image_path="sad.jpg", text="好难过想哭", similarity=0.75),
        AIMatchCandidate(rank=5, entry_id=5, image_path="laugh.jpg", text="笑死我了哈哈哈", similarity=0.72),
    ]
```

> 注意 OCR 文本现在无空格，候选 text 也应无空格（如 `加班到凌晨好累啊` 而非 `加班到凌晨 好累啊`）。这是语义一致性修正。

- [ ] **Step 4: 语法检查**

Run:
```bash
uv run python -m compileall tests/integration
```
Expected: 编译通过。

- [ ] **Step 5: 全量回归（无 key 环境集成测试自动 skip）**

Run:
```bash
uv run pytest -q
```
Expected: 全绿（集成测试 skip）。

> 若有 API key，可运行 `uv run pytest tests/integration/test_index_manager_api.py tests/integration/test_ai_matcher_api.py -v -s` 验证真实 sqlite/chroma 端到端。注意：Task 9 的 `_get_chroma_ids` 用零向量 query 召回全部 id，若真实 chroma 对零向量 query 报错或返回 0 条，需在 `index_manager.py` 改用 `collection.get()` 获取全部 id（见 Task 9 Step 4 备注）。本步骤是验证该假设的时机。

- [ ] **Step 6: 提交到分支**

```bash
git add tests/integration/test_index_manager_api.py tests/integration/test_ai_matcher_api.py tests/integration/test_rerank_service_api.py
git commit -m "test(integration): 集成测试适配新 IndexManager/AIMatcher 签名与 image_path 字段"
```

---

## Task 13: 迁移脚本 + 迁移脚本测试

**Files:**
- Create: `scripts/migrate_json_to_db.py`
- Create: `tests/unit/test_migrate_script.py`

### 13.1 设计要点（spec §7.1）

- 用标准库 `json` 读取 `data/index.json` + `data/embeddings.json`（无需 ujson）。
- 初始化 `MetadataStore(data/index.db)` + `VectorStore(data/chroma)`，`.load()`。
- 幂等：若 `meme` 表已有数据 → 提示"已迁移，跳过"并退出。
- 对每条旧 entry：
  - `text_new = "".join(entry["text"].split())`（去所有空白）
  - 旧 `id_str` 无法 `int()` 时跳过并提示（非数字 id 防御）
  - `new_id = MetadataStore.add_with_id(int(id_str), image_path=entry["filename"], text=text_new, speaker=None, tags=[])`
  - `VectorStore.upsert(new_id, decode_embedding(旧 embedding))`（复用旧向量）
- `decode_embedding` 逻辑内联在脚本：`base64.b64decode` + `struct.unpack("!Nf", ...)`。
- 格式兼容：旧 `embeddings.json` 按 `version` 字段判断，v2（`embedding` 为 base64 str → decode），v1（`embedding` 为 `list[float]` → 直接用）。
- 迁移后去空格 text 为空 → 跳过该条不写入（脚本不碰 `memes/` 文件）。
- 维度校验：embedding 维度非 1024 跳过并提示。
- 打印三行提示（spec 7.1）。
- 命令行参数：可选 `--data-dir`（默认 `data`）、`--memes-dir`（默认 `memes`，本次不读但保留）；或固定路径。简化：固定 `PROJECT_ROOT / "data"`。

- [ ] **Step 1: 写失败测试 — 迁移脚本端到端（fixture 旧 JSON）**

写入 `tests/unit/test_migrate_script.py`：

```python
"""迁移脚本 migrate_json_to_db.py 单元测试。"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import pytest


def _encode_emb(vec: list[float]) -> str:
    """用旧 v2 格式编码 embedding（base64 big-endian float32）。"""
    return base64.b64encode(struct.pack(f"!{len(vec)}f", *vec)).decode("ascii")


@pytest.fixture
def old_data_dir(tmp_path: Path) -> Path:
    """构造旧 JSON 数据目录（index.json + embeddings.json v2）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    index_json = {
        "version": 1,
        "entries": {
            "1": {"filename": "cat.jpg", "text": "一只 猫\t在跳", "text_hash": "sha256:abc"},
            "2": {"filename": "dog.jpg", "text": "狗在 跑", "text_hash": "sha256:def"},
            "3": {"filename": "blank.jpg", "text": "   ", "text_hash": "sha256:ghi"},
        },
    }
    (data_dir / "index.json").write_text(
        json.dumps(index_json, ensure_ascii=False), encoding="utf-8"
    )
    embeddings_json = {
        "version": 2,
        "entries": {
            "1": {"text_hash": "sha256:abc", "embedding": _encode_emb([0.1, 0.2, 0.3])},
            "2": {"text_hash": "sha256:def", "embedding": _encode_emb([0.4, 0.5, 0.6])},
            "3": {"text_hash": "sha256:ghi", "embedding": _encode_emb([0.0, 0.0, 0.0])},
        },
    }
    (data_dir / "embeddings.json").write_text(
        json.dumps(embeddings_json, ensure_ascii=False), encoding="utf-8"
    )
    return data_dir


def _run_migration(data_dir: Path) -> None:
    """以指定 data_dir 运行迁移脚本。"""
    import importlib
    mod = importlib.import_module("scripts.migrate_json_to_db")
    importlib.reload(mod)
    mod.run_migration(data_dir=str(data_dir))


class TestMigration:
    def test_migrates_entries_to_sqlite_and_chroma(self, old_data_dir: Path) -> None:
        from bot.engine.metadata_store import MetadataStore
        from bot.engine.vector_store import VectorStore

        _run_migration(old_data_dir)

        md = MetadataStore(str(old_data_dir / "index.db"))
        md.load()
        entries = md.get_all_entries()
        # id 1、2 写入（3 去空格后为空，跳过）
        assert {1, 2} == set(entries)
        assert entries[1].image_path == "cat.jpg"
        assert entries[1].text == "一只猫在跳"  # 去所有空白
        assert entries[2].text == "狗在跑"
        md.close()

        vs = VectorStore(str(old_data_dir / "chroma"))
        vs.load()
        assert vs.count() == 2
        vs.close()

    def test_preserves_old_ids(self, old_data_dir: Path) -> None:
        from bot.engine.metadata_store import MetadataStore

        _run_migration(old_data_dir)
        md = MetadataStore(str(old_data_dir / "index.db"))
        md.load()
        # 保留旧 id 数值
        assert md.get_entry(1) is not None
        assert md.get_entry(2) is not None
        md.close()

    def test_reuses_old_vectors(self, old_data_dir: Path) -> None:
        from bot.engine.vector_store import VectorStore

        _run_migration(old_data_dir)
        vs = VectorStore(str(old_data_dir / "chroma"))
        vs.load()
        # id=1 向量应为旧 [0.1,0.2,0.3]
        hits = vs.query([0.1, 0.2, 0.3], n_results=2)
        assert hits[0].entry_id == 1
        vs.close()

    def test_idempotent_second_run_skips(self, old_data_dir: Path, capsys) -> None:
        _run_migration(old_data_dir)
        # 第二次运行应提示已迁移、跳过
        _run_migration(old_data_dir)
        captured = capsys.readouterr()
        assert "已迁移" in captured.out or "跳过" in captured.out

    def test_blank_text_skipped(self, old_data_dir: Path) -> None:
        from bot.engine.metadata_store import MetadataStore

        _run_migration(old_data_dir)
        md = MetadataStore(str(old_data_dir / "index.db"))
        md.load()
        # id=3 去空格后为空，不写入
        assert md.get_entry(3) is None
        md.close()

    def test_non_numeric_id_skipped(self, tmp_path: Path) -> None:
        """非数字 id 跳过且不中断迁移。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        index_json = {
            "version": 1,
            "entries": {
                "abc": {"filename": "x.jpg", "text": "文字", "text_hash": "h"},
                "5": {"filename": "y.jpg", "text": "好", "text_hash": "h2"},
            },
        }
        (data_dir / "index.json").write_text(json.dumps(index_json), encoding="utf-8")
        emb_json = {
            "version": 2,
            "entries": {
                "5": {"text_hash": "h2", "embedding": _encode_emb([1.0, 0.0])},
            },
        }
        (data_dir / "embeddings.json").write_text(json.dumps(emb_json), encoding="utf-8")

        _run_migration(data_dir)

        from bot.engine.metadata_store import MetadataStore
        md = MetadataStore(str(data_dir / "index.db"))
        md.load()
        assert md.get_entry(5) is not None  # 数字 id 正常迁移
        md.close()

    def test_v1_embeddings_format_direct_list(self, tmp_path: Path) -> None:
        """v1 格式 embedding 为 list[float]，直接使用不走 decode。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "index.json").write_text(
            json.dumps({"version": 1, "entries": {
                "1": {"filename": "a.jpg", "text": "猫", "text_hash": "h"}
            }}), encoding="utf-8"
        )
        (data_dir / "embeddings.json").write_text(
            json.dumps({"version": 1, "entries": {
                "1": {"text_hash": "h", "embedding": [0.7, 0.8, 0.9]}
            }}), encoding="utf-8"
        )
        _run_migration(data_dir)

        from bot.engine.vector_store import VectorStore
        vs = VectorStore(str(data_dir / "chroma"))
        vs.load()
        assert vs.count() == 1
        vs.close()
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
uv run pytest tests/unit/test_migrate_script.py -v
```
Expected: FAIL，`ModuleNotFoundError: scripts.migrate_json_to_db`。

- [ ] **Step 3: 确认 scripts 目录存在**

Run:
```bash
fd scripts/ --type d
```
若 `scripts/` 目录不存在，创建它（写文件时父目录会自动创建）。若项目无 `scripts/__init__.py`，迁移脚本作为独立脚本运行，但测试用 `importlib.import_module("scripts.migrate_json_to_db")` 需 `scripts` 是可导入包。创建 `scripts/__init__.py`（空文件）使包可导入。

- [ ] **Step 4: 实现迁移脚本**

写入 `scripts/migrate_json_to_db.py`：

```python
"""旧 JSON 索引 → sqlite + chroma 迁移脚本（手动运行）。

读取 data/index.json + data/embeddings.json，写入 data/index.db（sqlite）与
data/chroma（chromadb）。保留旧 id 数值，复用旧 embedding 向量（零 API 消耗）。

运行方式：
    uv run python scripts/migrate_json_to_db.py
    uv run python scripts/migrate_json_to_db.py --data-dir /path/to/data

幂等：若 sqlite meme 表已有数据，提示已迁移并退出。
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import struct
import sys
from pathlib import Path

from bot.config import PROJECT_ROOT
from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


def _decode_embedding(data: str) -> list[float]:
    """将 v2 格式的 base64 float32 编码解码为浮点数列表（内联自旧 decode_embedding）。"""
    packed = base64.b64decode(data)
    return list(struct.unpack(f"!{len(packed) // 4}f", packed))


def _load_old_index(data_dir: Path) -> dict:
    """读取旧 index.json。"""
    index_path = data_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"未找到 {index_path}，无需迁移")
    return json.loads(index_path.read_text(encoding="utf-8"))


def _load_old_embeddings(data_dir: Path) -> dict:
    """读取旧 embeddings.json，返回 entries 字典（可能 v1 或 v2）。

    不存在时返回空 dict（迁移后由 sync 全量重建）。
    """
    emb_path = data_dir / "embeddings.json"
    if not emb_path.exists():
        return {"version": 1, "entries": {}}
    return json.loads(emb_path.read_text(encoding="utf-8"))


def _resolve_embedding(entry: dict, version: int) -> list[float] | None:
    """按 version 解析单条 embedding。

    v2: embedding 为 base64 字符串，需 decode。
    v1: embedding 为 list[float]，直接用。
    """
    raw = entry.get("embedding")
    if raw is None:
        return None
    if version == 2:
        if not isinstance(raw, str):
            return None
        try:
            return _decode_embedding(raw)
        except Exception as exc:
            logger.warning("embedding 解码失败，跳过: %s", exc)
            return None
    # v1 或无 version：直接是 list
    if isinstance(raw, list):
        return [float(x) for x in raw]
    return None


def run_migration(data_dir: str) -> None:
    """执行迁移。

    Args:
        data_dir: 数据目录路径（含 index.json/embeddings.json，输出 index.db/chroma）。
    """
    data_dir_path = Path(data_dir)
    db_path = str(data_dir_path / "index.db")
    chroma_path = str(data_dir_path / "chroma")

    metadata_store = MetadataStore(db_path)
    vector_store = VectorStore(chroma_path)
    metadata_store.load()
    vector_store.load()

    # 幂等检查
    if metadata_store.entry_count() > 0:
        print(f"已迁移：data/index.db 已有 {metadata_store.entry_count()} 条记录，跳过。")
        metadata_store.close()
        vector_store.close()
        return

    try:
        old_index = _load_old_index(data_dir_path)
    except FileNotFoundError as exc:
        print(str(exc))
        metadata_store.close()
        vector_store.close()
        return

    old_embeddings = _load_old_embeddings(data_dir_path)
    emb_version = old_embeddings.get("version", 1)
    emb_entries = old_embeddings.get("entries", {})

    index_entries = old_index.get("entries", {})
    migrated = 0
    skipped_blank = 0
    skipped_bad_id = 0
    skipped_bad_emb = 0

    for id_str, entry in index_entries.items():
        # 非数字 id 防御
        try:
            old_id = int(id_str)
        except (ValueError, TypeError):
            print(f"跳过非数字 id: {id_str}")
            skipped_bad_id += 1
            continue

        # 去所有空白
        text_new = "".join(str(entry.get("text", "")).split())
        if not text_new:
            print(f"跳过去空格后为空的条目: id={id_str}, filename={entry.get('filename')}")
            skipped_blank += 1
            continue

        image_path = str(entry.get("filename", ""))

        # 解析旧 embedding
        emb_record = emb_entries.get(id_str, {})
        embedding = _resolve_embedding(emb_record, emb_version)
        if embedding is None or len(embedding) != EMBEDDING_DIM:
            print(f"跳过 embedding 缺失/维度异常: id={id_str}, dim={len(embedding) if embedding else 0}")
            skipped_bad_emb += 1
            continue

        # 写入 sqlite（保留旧 id）+ chroma（复用旧向量）
        new_id = metadata_store.add_with_id(
            entry_id=old_id,
            image_path=image_path,
            text=text_new,
            speaker=None,
            tags=[],
        )
        import asyncio
        asyncio.run(vector_store.upsert(new_id, embedding))
        migrated += 1

    print(f"迁移完成：{migrated} 条记录写入 data/index.db 与 data/chroma/")
    print(
        "embedding 复用旧向量（基于含空格 text 生成）。如需与无空格 text 严格一致，"
        "可删除 data/chroma/ 后重启 Bot，后台同步会按 sqlite text 全量重建 embedding。"
    )
    print(f"旧文件 data/index.json、data/embeddings.json 已保留，可自行归档或删除。")
    print(f"统计：迁移 {migrated}，空文本跳过 {skipped_blank}，非数字 id 跳过 {skipped_bad_id}，embedding 异常跳过 {skipped_bad_emb}")

    metadata_store.close()
    vector_store.close()


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="旧 JSON 索引 → sqlite + chroma 迁移")
    parser.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / "data"),
        help="数据目录路径（默认 <项目根>/data）",
    )
    args = parser.parse_args()
    run_migration(args.data_dir)


if __name__ == "__main__":
    main()
```

并创建 `scripts/__init__.py`（空文件，使包可导入）：

```bash
touch scripts/__init__.py
```
（或用 Write 工具写入空内容）

- [ ] **Step 5: 运行迁移脚本测试**

Run:
```bash
uv run pytest tests/unit/test_migrate_script.py -v
```
Expected: PASS。

> 若 `asyncio.run(vector_store.upsert(...))` 在测试循环内反复创建事件循环报错，改为在 `run_migration` 顶部创建单一事件循环统一调度所有 upsert；或改为同步写入。若报错，将 `run_migration` 中的向量写入改为：
> ```python
> import asyncio
> async def _write_all():
>     for new_id, emb in pending_upserts:
>         await vector_store.upsert(new_id, emb)
> asyncio.run(_write_all())
> ```
> 先收集 `pending_upserts: list[tuple[int, list[float]]]` 再一次性 `asyncio.run`。本步骤若测试通过则保持当前逐条 `asyncio.run` 写法。

- [ ] **Step 6: 全量回归**

Run:
```bash
uv run pytest -q
```
Expected: 全绿。

- [ ] **Step 7: 提交到分支**

```bash
git add scripts/migrate_json_to_db.py scripts/__init__.py tests/unit/test_migrate_script.py
git commit -m "feat(scripts): 新增 migrate_json_to_db 迁移脚本，复用旧向量零 API 迁移 + 测试"
```

---

## Task 14: 移除 ujson 依赖

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`（由 uv 自动更新）

### 14.1 改造点

- 确认 `ujson` 已无任何引用（`index_manager.py` 重写后不再 import）。
- `uv remove ujson`。

- [ ] **Step 1: 确认 ujson 无引用**

Run:
```bash
rg -n "ujson" bot/ tests/ scripts/ --type py
```
Expected: 无输出（或仅在 `tests/unit/engine/test_index_manager.py` 旧测试中——该文件已在 Task 9 重写，应无 ujson）。若有残留引用，先清除再继续。

- [ ] **Step 2: 移除 ujson 依赖**

Run:
```bash
uv remove ujson
```
Expected: `pyproject.toml` 移除 `ujson>=...`，`uv.lock` 更新。

- [ ] **Step 3: 验证导入与全量测试**

Run:
```bash
uv run python -c "import bot.engine.index_manager; import bot.bot; print('ok')"
uv run pytest -q
```
Expected: 导入成功；全量测试全绿。

- [ ] **Step 4: 提交到分支**

```bash
git add pyproject.toml uv.lock
git commit -m "build: 移除 ujson 依赖（JSON 索引已废弃，迁移脚本用标准库 json）"
```

---

## Task 15: 文档同步

**Files:**
- Modify: `docs/api/bot/engine/metadata_store.md`（新建）
- Modify: `docs/api/bot/engine/vector_store.md`（新建）
- Modify: `docs/api/bot/engine/index_manager.md`
- Modify: `docs/api/bot/engine/ai_matcher.md`
- Modify: `docs/api/bot/engine/keyword_searcher.md`
- Modify: `docs/api/bot/engine/deepseek_ocr.md`
- Modify: `docs/api/bot/engine/paddle_ocr.md`
- Modify: `docs/api/bot/app_state.md`
- Modify: `docs/api/bot/bot.md`
- Modify: `docs/api/bot/config.md`
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

### 15.1 改造点（spec §10.3 文档同步清单）

- **API.md 子文档**：
  - 新建 `metadata_store.md`：`MemeEntry` 数据类、`MetadataStore` 全部公开方法签名与返回值。
  - 新建 `vector_store.md`：`VectorHit` 数据类、`VectorStore` 全部公开方法。
  - 更新 `index_manager.md`：薄编排签名、移除 `get_entries`/`get_embeddings`/`save_index`/`save_embeddings`/`encode_embedding`/`decode_embedding`/`normalize_text`/`compute_text_hash`/`dedup_key`/`is_blank_text`，`AddResult.replaced_filename→replaced_image_path`、`entry_id:int|None`。
  - 更新 `ai_matcher.md`：新构造签名、`AIMatchCandidate`/`AIMatchResult` 字段改名、移除 `AIIndexProvider`。
  - 更新 `keyword_searcher.md`：`MetadataStoreProvider`、`SearchResult` 字段改名、移除 `IndexProvider`。
  - 更新 `deepseek_ocr.md` / `paddle_ocr.md`：`ocr()` 返回去除所有空白。
  - 更新 `app_state.md`：新增 `get_metadata_store`/`get_vector_store`、`init_app` 新参数。
  - 更新 `bot.md`：startup 创建并注入两个 Store。
  - 更新 `config.md`：新增 `INDEX_DB_PATH`/`CHROMA_DIR`/`DATA_DIR`。
- **PRD.md**：技术栈增 ChromaDB/sqlite3；OCR 文本=去除所有空白；AI 召回改 ChromaDB；索引管理删 text_hash 自动重建条款、新增阶段0一致性修复；索引文件格式改 sqlite schema + chroma collection；边界情况表更新；项目结构增删；依赖清单增 chromadb 移 ujson。
- **CONTEXT.md**：术语：`index.json`→sqlite `index.db`、`embeddings.json`→chroma 向量库、去 `text_hash`、增 `image_path`/`speaker`/`标记词`、去重键=`text`、OCR 文本=去除所有空白、`entry_id` 类型 `int`。
- **README.md**：索引文件说明（`data/index.db` + `data/chroma/`）、项目结构、升级提示（先运行迁移脚本再启动 Bot）、依赖列表增 chromadb 移 ujson。
- **.env.example**：无新增环境变量（spec 10.3 确认），但若有 OCR 文本相关说明可补一句"OCR 文本自动去除空白"。实际上无需改，本任务核对即可。
- **CLAUDE.md**："当前实现注意事项"更新模块清单（新增 MetadataStore/VectorStore/迁移脚本，移除 text_hash/embedding codec）、数据目录说明（`data/index.db` + `data/chroma/`）。

- [ ] **Step 1: 新建 metadata_store.md API 文档**

写入 `docs/api/bot/engine/metadata_store.md`，按现有 API.md 格式（`# bot/engine/metadata_store.py — 元数据存储 API` + 模块级数据类/方法表格）。内容覆盖 `MemeEntry` 字段表、`MetadataStore.__init__/load/close/get_all_entries/get_entry/get_by_filename/get_id_by_text/find_next_id/entry_count/get_all_text/add/add_with_id/update/remove` 每个的参数/返回/异常表。

- [ ] **Step 2: 新建 vector_store.md API 文档**

写入 `docs/api/bot/engine/vector_store.md`，覆盖 `VectorHit` 字段表、`VectorStore.__init__/load/close/upsert/remove/remove_many/query/rebuild_all/count`。

- [ ] **Step 3: 更新 index_manager.md**

编辑 `docs/api/bot/engine/index_manager.md`：
- 移除 `normalize_text`/`compute_text_hash`/`dedup_key`/`encode_embedding`/`decode_embedding` 各小节。
- `AddResult` 表：`entry_id: int|None`、`replaced_image_path: str|None`（原 `replaced_filename`）。
- `IndexManager.__init__` 参数表改为 `metadata_store/vector_store/memes_dir/no_text_dir/ocr_provider/embedding_provider/optimizer/sync_concurrency`。
- 移除 `get_entries`/`get_embeddings`/`get_entry`/`get_by_filename`/`save_index`/`save_embeddings`/`add_entry`/`remove_entry` 各小节（职责移交两个 Store）。
- 保留 `SyncResult`/`IndexCorruptedError`/`CompressionError`/`OcrError`/`EmbeddingError`/`OcrProvider`/`resolve_unique_filename`/`acquire_lock`/`release_lock`/`is_locked`/`entry_count`/`load`/`sync_with_filesystem`/`add_single_file`。
- `sync_with_filesystem` 说明更新为"四阶段（阶段0一致性修复 + 删除 + 新增）"。

- [ ] **Step 4: 更新 ai_matcher.md / keyword_searcher.md**

- `ai_matcher.md`：`AIMatchCandidate`/`AIMatchResult` 字段表 `entry_id: int`、`image_path: str`；`AIMatcher.__init__` 参数 `metadata_store/vector_store/embedding_provider/rerank_provider/limit`；移除 `AIIndexProvider` 小节；`match` 流程说明改为"VectorStore.query 召回 + MetadataStore 取 metadata"。
- `keyword_searcher.md`：`SearchResult` 字段表 `entry_id: int`、`image_path: str`；`KeywordSearcher.__init__` 参数 `metadata_store/threshold/limit`；`IndexProvider` 协议改名 `MetadataStoreProvider`（`get_all_entries -> dict[int, MemeEntry]`）。

- [ ] **Step 5: 更新 deepseek_ocr.md / paddle_ocr.md / app_state.md / bot.md / config.md**

- `deepseek_ocr.md` / `paddle_ocr.md`：`ocr()` Returns 说明改为"去除所有空白后的文本"。
- `app_state.md`：新增 `get_metadata_store()`/`get_vector_store()` 小节；`init_app` 参数表加 `metadata_store`/`vector_store`。
- `bot.md`：startup 流程说明加"创建 MetadataStore + VectorStore"。
- `config.md`：新增 `INDEX_DB_PATH`/`CHROMA_DIR`/`DATA_DIR` 常量小节。

- [ ] **Step 6: 更新 PRD.md**

编辑 `docs/PRD.md`，按 spec 10.3 同步：
- 技术栈：增 ChromaDB（HNSW cosine）、sqlite3；移除"双 JSON 索引"描述。
- OCR：文本去除所有空白。
- AI 匹配：召回改 ChromaDB `collection.query`。
- 索引管理：删 text_hash 自动重建条款；新增 sync 阶段0 一致性修复。
- 索引文件格式：sqlite schema（meme + meme_tag）+ chroma collection（memes, cosine）。
- 边界情况表：更新"图片文件被删、索引还在"为"sync 阶段1 删除"；新增"chroma 损坏/不一致"。
- 项目结构：增 `data/index.db`、`data/chroma/`、`bot/engine/metadata_store.py`、`bot/engine/vector_store.py`、`scripts/migrate_json_to_db.py`；移除 `data/index.json`、`data/embeddings.json`。
- 依赖清单：增 `chromadb`，移 `ujson`。

- [ ] **Step 7: 更新 CONTEXT.md**

编辑 `CONTEXT.md`，更新术语表：
- `index.json` → sqlite `index.db`（meme 表：id/image_path/text/speaker + meme_tag 关联表）
- `embeddings.json` → chroma 向量库（`data/chroma/`，collection `memes`，cosine）
- 移除 `text_hash`（SHA-256）
- 新增 `image_path`（memes/ 下相对路径，原 `filename`）、`speaker`（说话人，预留）、`标记词`（meme_tag 多值）
- 去重键 = `text`（去空格后）
- OCR 文本 = 去除所有空白
- `entry_id` 类型 = `int`（全栈）

- [ ] **Step 8: 更新 README.md**

编辑 `README.md`：
- 数据目录说明：`data/index.db`（sqlite 元数据）、`data/chroma/`（chroma 向量库）；旧 `index.json`/`embeddings.json` 迁移后可删除。
- 项目结构：增删同 PRD。
- 升级提示：新增段落"从旧版升级时，先运行 `uv run python scripts/migrate_json_to_db.py` 再启动 Bot。否则旧 `index.json` 不会被读取，首次启动会全量重新 OCR/embed（消耗 API）。"
- 依赖列表：增 `chromadb`，移 `ujson`。

- [ ] **Step 9: 核对 .env.example**

Run:
```bash
rg -n "index\.json|embeddings\.json|ujson" .env.example
```
Expected: 无输出（.env.example 无 JSON 相关变量）。无需改，本步骤仅核对。

- [ ] **Step 10: 更新 CLAUDE.md**

编辑项目根 `CLAUDE.md`：
- "当前实现注意事项"：模块清单新增 `metadata_store`、`vector_store`、`migrate_json_to_db` 脚本；移除 `text_hash`/`embedding codec` 相关说明。
- 数据目录说明：`data/index.db`（sqlite）、`data/chroma/`（chroma）；移除 `data/index.json`、`data/embeddings.json`。

- [ ] **Step 11: 最终全量回归**

Run:
```bash
uv run pytest -q
uv run python -m compileall bot tests scripts
```
Expected: 全量测试全绿；编译通过。

> 若有 API key，可额外运行集成测试验证真实 sqlite/chroma 端到端：
> ```bash
> uv run pytest tests/integration/ -v -s
> ```

- [ ] **Step 12: 提交到分支**

```bash
git add docs/ CONTEXT.md README.md CLAUDE.md
git commit -m "docs: 同步 ChromaDB+SQLite 重构至 API/PRD/CONTEXT/README/CLAUDE 文档"
```

> 说明：仅文档变更，已运行全量测试（见 Step 11）。按 CLAUDE.md 规则"仅文档变更可不运行测试，但需在提交说明中注明"——本任务实际已运行测试，提交说明体现。

---

## 最终步骤：暂停等待用户审核 merge

所有 15 个任务完成后，**停止并暂停**，向用户报告：

1. **分支**：`refactor/chromadb-sqlite` 上所有提交列表（`git log main..HEAD --oneline`）。
2. **变更摘要**：新建/修改/删除文件总清单。
3. **验证结果**：`uv run pytest -q` 与 `uv run python -m compileall bot tests scripts` 输出。
4. **待用户审核 merge**：由用户审查分支后执行 `git checkout main && git merge refactor/chromadb-sqlite`（或通过 PR）。

> ⚠️ 禁止自行 merge 或 push 到 main。merge 由用户审核后执行。
>
> **迁移提示（向用户转达）**：若部署环境存在旧 `data/index.json` + `data/embeddings.json`，merge 后部署前需运行 `uv run python scripts/migrate_json_to_db.py`；否则首次启动会全量重新 OCR/embed（消耗 API）。旧 JSON 文件迁移后可手动归档/删除。

---

## Self-Review（计划自检）

### 1. Spec 覆盖核对

| Spec 章节 | 覆盖任务 |
|---|---|
| §2 决策表 | T4（关联表/最小空洞SQL）、T9（_text_to_id/去重=text）、T8（OCR去空白）、T13（复用旧向量/幂等）、T9（增量写入+阶段0） |
| §3 总体架构 | T4/T5（两 Store）、T9（薄编排）、T6/T7（Searcher/AIMatcher 改依赖） |
| §4.1 sqlite schema | T4 Step 3 `_SCHEMA` |
| §4.2 最小空洞 SQL | T4 Step 3 `_FIND_NEXT_ID_SQL` + Step 5 五例测试 |
| §4.3 chroma collection | T5 Step 3（cosine collection `memes`） |
| §4.4 文件布局 | T3（INDEX_DB_PATH/CHROMA_DIR）、T15（文档） |
| §5.1 数据类 | T4（MemeEntry）、T5（VectorHit）、T6（SearchResult）、T7（AIMatchCandidate/Result）、T9（AddResult） |
| §5.2 OcrProvider | T9（保留协议，返回值约定无空格）、T8（实现） |
| §5.3 MetadataStore 签名 | T4 Step 3（全部方法） |
| §5.4 VectorStore 签名 | T5 Step 3（全部方法） |
| §5.5 IndexManager 签名 | T9 Step 3（薄编排） |
| §5.6 依赖关系 | T6/T7/T9/T10 |
| §5.7 AIMatcher.match 流程 | T7 Step 3 |
| §5.8 app_state/bot.py 注入 | T10 |
| §6.1-6.4 sync 四阶段 | T9 Step 3（_sync_phase0/1/2） + 测试 |
| §6.5 跨库写入一致性 | T9 Step 3（_write_entry 回滚） + 测试 |
| §6.6 锁与并发 | T9 Step 3（_lock/_sync_semaphore/_add_sem） + 测试 |
| §6.7 去重替换 | T9 Step 3（_write_entry replaced 分支） + 测试 |
| §6.8 无文字移图 | T9 Step 3（_move_to_no_text） + 测试 |
| §7.1 迁移脚本 | T13 |
| §7.2 启动流程 | T10（bot.py） |
| §7.3 启动时不迁移 | T10 + T15（文档说明） |
| §7.4 升级提示 | T15（README/PRD） |
| §8.1 存储层错误 | T4（load 建空库）、T9（阶段0 rebuild_all） |
| §8.2 OCR/Embedding API 失败 | T9（OcrError/EmbeddingError + failed 列表） + 测试 |
| §8.3 跨库写入失败回滚 | T9 Step 3（三种回滚路径） + 测试 |
| §8.4 边界情况 | T9 测试覆盖（空目录/无文字/去重/不支持扩展名） |
| §8.5 并发与线程安全 | T4（threading.Lock）、T5（threading.Lock+to_thread）、T9（to_thread 包装） |
| §8.6 性能考量 | T15（PRD 文档提及，不强制缓存） |
| §9.1-9.6 测试策略 | T2（conftest）、T4/T5/T13（新测试）、T6/T7/T8/T9/T10/T11/T12（改造测试） |
| §9.7 验证命令 | 各任务 Step + T15 Step 11 |
| §10.1 代码影响面 | T1-T15 全覆盖 |
| §10.2 依赖变更 | T1（add chromadb）、T14（remove ujson） |
| §10.3 文档同步 | T15 |
| §10.4 风险与回滚 | T15（升级提示+保留旧 JSON） |

**结论：Spec 全部章节均有对应任务覆盖，无遗漏。**

### 2. 占位符扫描

- ✅ 无 "TBD/TODO/implement later/fill in details"。
- ✅ 每个代码步骤含完整代码块（T4/T5/T9/T13 给出完整实现；T6/T7/T8/T10/T11/T12 给出完整关键 diff；T11 部分用 `rg` 定位批量改字段，因改动机械且明确）。
- ✅ 无 "Add appropriate error handling" 等模糊表述——错误处理在 T9 实现中具化为 OcrError/EmbeddingError/回滚逻辑。
- ✅ 无 "Similar to Task N"——重复的 mock 结构（FakeMetadataStore/FakeVectorStore）在 T9 内定义一次，因后续任务不再用同一测试模式。

> T11 用 `rg -n "filename|entry_id="` 定位批量字段改名，是机械重命名（每处改动模式完全一致：`filename=` → `image_path=`、`entry_id="N"` → `entry_id=N`），非占位符——执行者按 rg 输出逐处替换即可。

### 3. 类型一致性核对

| 类型/方法 | 定义任务 | 使用任务 | 一致性 |
|---|---|---|---|
| `MemeEntry(id:int, image_path, text, speaker, tags)` | T4 | T6/T7/T9/T10/T13 | ✅ 全栈 int id + image_path |
| `VectorHit(entry_id:int, similarity)` | T5 | T7/T9 | ✅ |
| `SearchResult(entry_id:int, image_path, text, similarity)` | T6 | T11（插件） | ✅ |
| `AIMatchCandidate(rank, entry_id:int, image_path, text, similarity)` | T7 | T7 rerank 测试/T12 | ✅ |
| `AIMatchResult(entry_id:int, image_path, text, similarity, source)` | T7 | T11/T12 | ✅ |
| `AddResult(entry_id:int\|None, reason, text, replaced_image_path, moved_to)` | T9 | T11/T12 | ✅ |
| `MetadataStore.get_all_entries() -> dict[int, MemeEntry]` | T4 | T6/T9 | ✅ |
| `MetadataStore.get_entry(int) -> MemeEntry\|None` | T4 | T7/T9 | ✅ |
| `MetadataStore.get_id_by_text(str) -> int\|None` | T4 | T9 | ✅ |
| `MetadataStore.find_next_id() -> int` | T4 | T9 | ✅ |
| `MetadataStore.add(image_path, text, speaker, tags) -> int` | T4 | T9 | ✅ |
| `MetadataStore.update(entry_id, *, image_path, text, speaker, tags) -> bool` | T4 | T9（去重替换回滚） | ✅ |
| `MetadataStore.remove(int) -> bool` | T4 | T9 | ✅ |
| `MetadataStore.add_with_id(int, image_path, text, speaker, tags) -> int` | T4 | T13 | ✅ |
| `VectorStore.upsert(int, list[float])` async | T5 | T7/T9/T13 | ✅ |
| `VectorStore.query(list[float], n_results) -> list[VectorHit]` async | T5 | T7/T9 | ✅ |
| `VectorStore.remove(int)` async | T5 | T9 | ✅ |
| `VectorStore.remove_many(list[int])` async | T5 | T9 | ✅ |
| `VectorStore.rebuild_all(list[tuple[int, list[float]]])` async | T5 | T9 | ✅ |
| `VectorStore.count() -> int` | T5 | T7/T9 | ✅ |
| `IndexManager.__init__(metadata_store, vector_store, memes_dir, no_text_dir, ocr_provider, embedding_provider, optimizer, sync_concurrency)` | T9 | T10（bot.py）/T12 | ✅ |
| `IndexManager.add_single_file(filename) -> AddResult` | T9 | T11（meme_add） | ✅ |
| `IndexManager.sync_with_filesystem() -> SyncResult` | T9 | T10/T12 | ✅ |
| `_text_to_id: dict[str, int]` | T4 | T9（通过 get_id_by_text） | ✅ 命名一致 |
| 写入顺序"先 sqlite 后 chroma" | T9 | T9 全部写入路径 | ✅ |
| OCR 返回 `"".join(result.split())` | T8 | T9（not text 判定）/T13（去空格） | ✅ |

**结论：全部类型/签名/命名跨任务一致，无矛盾。**

### 4. 已知风险点（执行时留意）

1. **T9 `_get_chroma_ids` 用零向量 query 召回全部 id**：真实 chroma 对 1024 维零向量 query 是否返回全部条目需在 T12（有 key 时）验证。若不可靠，改为 `self._collection.get()` 获取全部 id（需在 VectorStore 新增 `get_all_ids()` 方法）。计划已在 T9 Step 4 与 T12 Step 5 标注此回退路径。
2. **T13 `asyncio.run` 在循环内反复调用**：若迁移脚本测试因事件循环复用报错，改为收集 pending 后单次 `asyncio.run(_write_all())`。计划已在 T13 Step 5 标注。
3. **T11 批量字段改名**：依赖 `rg` 定位，执行者需逐处确认无遗漏（特别是 `entry_id: str` 类型标注 → `entry_id: int`）。
4. **chromadb 依赖体积**：T1 `uv add chromadb` 会显著增大 Docker 镜像；Dockerfile 无需改（`uv sync --no-dev --frozen` 自动拉取），但镜像构建时间与体积会增加（spec 10.4 接受）。

**计划自检完成，可交付执行。**

