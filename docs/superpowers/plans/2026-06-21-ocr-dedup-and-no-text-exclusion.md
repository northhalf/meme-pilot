# OCR 文本去重与无文字图排除 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `IndexManager.add_entry` 和 `sync_with_filesystem` 新增阶段加入「去除所有空白后的 OCR 文本」去重与无文字图排除，命中时删除被替换方的索引记录与图片文件/将无文字图移到 `meme_no_text/`。

**Architecture:** 方案 A——逻辑内聚在 `index_manager.py`。新增模块级工具函数 `dedup_key`/`is_blank_text`/`_resolve_unique_filename`，新增 `AddResult` 数据类，`SyncResult` 扩展两字段，`add_entry` 返回 `AddResult` 并内联去重/无文字处理，`_sync_additions` 用 `winner_keys` 集合做增量三分类。文件副作用（删旧图/移无文字图）由 IndexManager 内部承担。

**Tech Stack:** Python 3.12, ujson, asyncio, hashlib, pathlib, shutil, pytest（已安装为 dev 依赖，`pythonpath=["."]`）

**参考文档:** `docs/superpowers/specs/2026-06-21-ocr-dedup-and-no-text-exclusion-design.md`（本计划的权威来源）

**⚠️ 项目约束（来自 CLAUDE.md）:**
- **禁止自行 `git add` / `git commit`**——每个 Task 末尾的 commit 步骤是「用户审核检查点」，需停下来请用户审阅 diff 并由用户执行提交，**不可自动提交**。
- Python 函数使用 Google 风格中文 docstring，变量/参数/返回值需类型标注，保持现有中文注释风格。
- 每实现一个模块后更新 `docs/process.md` 和 `docs/api/API.md`。

**测试基线:** 仓库已有 `tests/unit/engine/test_index_manager.py`（1330 行）。本计划会修改其中 5 个因行为变更而失效的测试（Task 2、Task 5），其余测试应保持通过。

---

### Task 1: 工具函数 `dedup_key` / `is_blank_text` / `_resolve_unique_filename`（TDD）

**Files:**
- Modify: `bot/engine/index_manager.py`（顶部 import 区第 9-14 行；第 41-55 行 `compute_text_hash` 之后插入新函数）
- Modify: `tests/unit/engine/test_index_manager.py`（新增 `TestDedupKey`、`TestIsBlankText`、`TestResolveUniqueFilename` 三个测试类）

- [ ] **Step 1: 编写失败测试 — `dedup_key` 与 `is_blank_text`**

在 `tests/unit/engine/test_index_manager.py` 顶部 import 区（第 10-17 行的 `from bot.engine.index_manager import (...)` 块）增加导入 `dedup_key`、`is_blank_text`、`_resolve_unique_filename`（`_resolve_unique_filename` 是模块级函数，直接导入即可）：

```python
from bot.engine.index_manager import (
    IndexCorruptedError,
    IndexLockedError,
    IndexManager,
    SyncResult,
    _resolve_unique_filename,
    compute_text_hash,
    dedup_key,
    is_blank_text,
    normalize_text,
)
```

在文件末尾（`TestSyncWithFilesystem` 类之后，第 1330 行后）追加三个测试类：

```python
class TestDedupKey:
    """dedup_key 工具函数测试。"""

    def test_removes_all_whitespace(self) -> None:
        """去除所有空白字符（含半角空格、制表符、换行）。"""
        assert dedup_key("一只猫 抓蝴蝶") == "一只猫抓蝴蝶"
        assert dedup_key("a\tb\nc") == "abc"

    def test_space_count_does_not_matter(self) -> None:
        """空格数量不同但字符相同视为同一键。"""
        assert dedup_key("加班 好累") == dedup_key("加班好累")
        assert dedup_key("加班  好累") == dedup_key("加班好累")

    def test_fullwidth_space_removed(self) -> None:
        """全角空格也被去除。"""
        assert dedup_key("加班　好累") == "加班好累"

    def test_empty_string(self) -> None:
        """空字符串返回空字符串。"""
        assert dedup_key("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        """纯空白返回空字符串。"""
        assert dedup_key("   \t\n  ") == ""


class TestIsBlankText:
    """is_blank_text 工具函数测试。"""

    def test_pure_whitespace_is_blank(self) -> None:
        """纯空白判定为无文字。"""
        assert is_blank_text("   \t\n  ") is True
        assert is_blank_text("") is True

    def test_has_text_not_blank(self) -> None:
        """有非空白字符则非无文字。"""
        assert is_blank_text("a") is False
        assert is_blank_text(" 一只猫 ") is False


class TestResolveUniqueFilename:
    """_resolve_unique_filename 模块级函数测试。"""

    def test_no_conflict(self, tmp_path: Path) -> None:
        """目标不存在时直接返回原路径。"""
        result = _resolve_unique_filename(tmp_path, "cat.jpg")
        assert result == tmp_path / "cat.jpg"

    def test_conflict_appends_sequence(self, tmp_path: Path) -> None:
        """目标已存在时追加 _2 序号。"""
        (tmp_path / "cat.jpg").write_text("x", encoding="utf-8")
        result = _resolve_unique_filename(tmp_path, "cat.jpg")
        assert result == tmp_path / "cat_2.jpg"

    def test_multiple_conflicts(self, tmp_path: Path) -> None:
        """_2 也存在时追加 _3。"""
        (tmp_path / "cat.jpg").write_text("x", encoding="utf-8")
        (tmp_path / "cat_2.jpg").write_text("x", encoding="utf-8")
        result = _resolve_unique_filename(tmp_path, "cat.jpg")
        assert result == tmp_path / "cat_3.jpg"

    def test_preserves_extension(self, tmp_path: Path) -> None:
        """多段扩展名保留完整后缀。"""
        (tmp_path / "a.tar.gz").write_text("x", encoding="utf-8")
        result = _resolve_unique_filename(tmp_path, "a.tar.gz")
        # Path.stem 只去掉最后一段后缀 .gz，stem="a.tar"
        assert result == tmp_path / "a.tar_2.gz"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestDedupKey tests/unit/engine/test_index_manager.py::TestIsBlankText tests/unit/engine/test_index_manager.py::TestResolveUniqueFilename -v`

Expected: FAIL — `ImportError: cannot import name 'dedup_key'`（或 `is_blank_text` / `_resolve_unique_filename`）。

- [ ] **Step 3: 实现 `dedup_key` / `is_blank_text` / `_resolve_unique_filename`**

在 `bot/engine/index_manager.py` 顶部 import 区，第 9-14 行之间插入两个新 import。修改后 import 区为：

```python
from __future__ import annotations

import hashlib
import itertools
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import ujson
```

在 `compute_text_hash` 函数之后（第 55 行 `return f"sha256:{digest}"` 之后、第 58 行 `# 自定义异常` 注释之前）插入：

```python
def dedup_key(text: str) -> str:
    """计算 OCR 文本的去重键。

    去除所有空白字符（含半角/全角空格、制表符、换行等）后的纯文本。
    比 normalize_text 更严格：normalize_text 保留单词间单空格，
    dedup_key 完全去除空格，用于判定「是否完全相同的图片」。

    Args:
        text: 原始 OCR 文本。

    Returns:
        去除所有空白字符后的文本（可能为空字符串）。
    """
    return "".join(text.split())


def is_blank_text(text: str) -> bool:
    """判断 OCR 文本是否为「无文字」。

    去除所有空白后为空即判定无文字。

    Args:
        text: OCR 文本。

    Returns:
        True 表示无文字（需移到 meme_no_text/ 不进索引）。
    """
    return dedup_key(text) == ""


def _resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    与 /add 文件名冲突策略一致：若 filename 已存在，
    在基名后追加 _2、_3... 直到不冲突。

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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestDedupKey tests/unit/engine/test_index_manager.py::TestIsBlankText tests/unit/engine/test_index_manager.py::TestResolveUniqueFilename -v`

Expected: PASS（12 个测试全过）。

- [ ] **Step 5: 运行全量测试，确认未破坏现有测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`

Expected: PASS（现有 50+ 测试仍全过；本任务只新增函数，不改既有行为）。

- [ ] **Step 6: 编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`

Expected: 无输出（编译成功）。

- [ ] **Step 7: 用户审核检查点 — 请审阅 diff 并提交**

暂停。向用户展示改动摘要：
- `bot/engine/index_manager.py`：新增 `import itertools`、`import shutil`，新增 `dedup_key`/`is_blank_text`/`_resolve_unique_filename` 三个模块级函数。
- `tests/unit/engine/test_index_manager.py`：新增三个测试类共 12 个测试。

请用户审阅 diff 后自行执行：
```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 新增 dedup_key/is_blank_text/_resolve_unique_filename 工具函数"
```

---

### Task 2: `SyncResult` 扩展 `deduped` / `no_text_moved` 字段

**Files:**
- Modify: `bot/engine/index_manager.py`（第 119-131 行 `SyncResult` 数据类）
- Modify: `tests/unit/engine/test_index_manager.py`（第 76-84 行 `TestSyncResult`）

- [ ] **Step 1: 编写失败测试 — 更新 `TestSyncResult`**

在 `tests/unit/engine/test_index_manager.py` 中，把 `TestSyncResult` 类（第 76-84 行）替换为：

```python
class TestSyncResult:
    """SyncResult 数据类测试。"""

    def test_create(self) -> None:
        """验证创建 SyncResult 实例。"""
        r = SyncResult(added=3, deleted=1, failed=["bad.jpg"])
        assert r.added == 3
        assert r.deleted == 1
        assert r.deduped == 0
        assert r.no_text_moved == 0
        assert r.failed == ["bad.jpg"]

    def test_deduped_and_no_text_defaults_zero(self) -> None:
        """deduped 与 no_text_moved 默认为 0。"""
        r = SyncResult()
        assert r.deduped == 0
        assert r.no_text_moved == 0

    def test_deduped_and_no_text_movable(self) -> None:
        """deduped 与 no_text_moved 可单独赋值。"""
        r = SyncResult(deduped=2, no_text_moved=1)
        assert r.deduped == 2
        assert r.no_text_moved == 1
        assert r.added == 0
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSyncResult -v`

Expected: FAIL — `AttributeError: 'SyncResult' object has no attribute 'deduped'`（或 `no_text_moved`）。

- [ ] **Step 3: 扩展 `SyncResult` 数据类**

在 `bot/engine/index_manager.py` 中，把 `SyncResult`（第 119-131 行）替换为：

```python
@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因去重键命中已有条目/其他新图而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSyncResult -v`

Expected: PASS（3 个测试全过）。

- [ ] **Step 5: 运行全量测试，确认未破坏现有测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`

Expected: PASS（`SyncResult` 只加字段不破坏既有用法，`test_sync_*` 系列仍通过——此时 `sync_with_filesystem` 尚未改造，仍返回旧式 `SyncResult(added=, deleted=, failed=)`，新字段默认 0，断言不冲突）。

- [ ] **Step 6: 用户审核检查点 — 请审阅 diff 并提交**

暂停。改动摘要：`SyncResult` 加 `deduped`、`no_text_moved` 两字段（默认 0），对应测试更新。

请用户审阅 diff 后自行执行：
```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): SyncResult 新增 deduped/no_text_moved 字段"
```

---

### Task 3: `AddResult` 数据类 + `IndexManager.__init__` 加 `no_text_dir` 参数

**Files:**
- Modify: `bot/engine/index_manager.py`（`SyncResult` 之后插入 `AddResult`；第 166-206 行 `__init__` 加参数与 `_no_text_dir` 属性）
- Modify: `tests/unit/engine/test_index_manager.py`（新增 `TestAddResult`；更新 `TestIndexManagerInit`）

- [ ] **Step 1: 编写失败测试 — `TestAddResult`**

在 `tests/unit/engine/test_index_manager.py` 顶部 import 块加入 `AddResult`（在 Task 1 已导入 `dedup_key`/`is_blank_text`/`_resolve_unique_filename` 的基础上追加）：

```python
from bot.engine.index_manager import (
    AddResult,
    IndexCorruptedError,
    IndexLockedError,
    IndexManager,
    SyncResult,
    _resolve_unique_filename,
    compute_text_hash,
    dedup_key,
    is_blank_text,
    normalize_text,
)
```

在 `TestSyncResult` 类之后插入 `TestAddResult`：

```python
class TestAddResult:
    """AddResult 数据类测试。"""

    def test_added(self) -> None:
        """正常新增结果。"""
        r = AddResult(entry_id="1", reason="added")
        assert r.entry_id == "1"
        assert r.reason == "added"
        assert r.replaced_filename is None
        assert r.moved_to is None

    def test_replaced(self) -> None:
        """去重覆盖结果。"""
        r = AddResult(
            entry_id="3",
            reason="replaced",
            replaced_filename="old.jpg",
        )
        assert r.entry_id == "3"
        assert r.replaced_filename == "old.jpg"
        assert r.moved_to is None

    def test_no_text(self) -> None:
        """无文字移图结果。"""
        r = AddResult(
            entry_id=None,
            reason="no_text",
            moved_to="/app/meme_no_text/blank.jpg",
        )
        assert r.entry_id is None
        assert r.moved_to == "/app/meme_no_text/blank.jpg"
        assert r.replaced_filename is None
```

并更新 `TestIndexManagerInit.test_default_dirs`（第 90-94 行），在其末尾增加对 `_no_text_dir` 默认值的断言：

```python
    def test_default_dirs(self) -> None:
        """默认 data_dir='data', memes_dir='memes'。"""
        mgr = IndexManager()
        assert mgr._data_dir == Path("data")
        assert mgr._memes_dir == Path("memes")
        assert mgr._no_text_dir == Path("meme_no_text")
```

并新增一个测试验证自定义 `no_text_dir`：

```python
    def test_custom_no_text_dir(self) -> None:
        """可自定义无文字图目录。"""
        mgr = IndexManager(no_text_dir="/tmp/blank")
        assert mgr._no_text_dir == Path("/tmp/blank")
```

> `test_custom_dirs`（第 96-100 行）保持不变，它不涉及 `no_text_dir`。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestAddResult tests/unit/engine/test_index_manager.py::TestIndexManagerInit -v`

Expected: FAIL — `ImportError: cannot import name 'AddResult'`，以及 `AttributeError: 'IndexManager' object has no attribute '_no_text_dir'`。

- [ ] **Step 3: 实现 `AddResult` 数据类**

在 `bot/engine/index_manager.py` 中，`SyncResult` 类之后（`# 数据类` 分组内，`class IndexManager` 之前）插入：

```python
@dataclass
class AddResult:
    """add_entry() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID；无文字移图场景为 None。
        reason: 结果类别，取值：
            "added"   - 正常新增；
            "replaced"- 去重命中已有条目，已复用旧 ID 覆盖；
            "no_text" - OCR 无文字，已移至 meme_no_text/ 不进索引。
        replaced_filename: reason="replaced" 时为被删旧图文件名，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
    """

    entry_id: str | None
    reason: str
    replaced_filename: str | None = None
    moved_to: str | None = None
```

- [ ] **Step 4: 扩展 `__init__` 加 `no_text_dir` 参数与 `_no_text_dir` 属性**

在 `bot/engine/index_manager.py` 中，把 `__init__` 签名（第 166-173 行）改为：

```python
    def __init__(
        self,
        data_dir: str = "data",
        memes_dir: str = "memes",
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        sync_concurrency: int | None = None,
        no_text_dir: str | None = None,
    ) -> None:
```

在 docstring 的 `Args:` 列表末尾（`sync_concurrency` 说明之后、闭合 `"""` 之前）追加参数说明：

```python
            no_text_dir: 无文字图存放目录；None 时取 memes_dir 同级的
                meme_no_text/（即 Path(memes_dir).parent / "meme_no_text"）。
                插件层无需显式传入。
```

在方法体内，`self._memes_dir = Path(memes_dir)`（第 188 行）之后插入：

```python
        self._data_dir = Path(data_dir)
        self._memes_dir = Path(memes_dir)
        if no_text_dir is not None:
            self._no_text_dir = Path(no_text_dir)
        else:
            self._no_text_dir = Path(memes_dir).parent / "meme_no_text"
        self._ocr_provider = ocr_provider
```

同步更新类 docstring 的 `Attributes:` 列表（第 143-153 行），在 `_memes_dir` 之后加一行：

```python
        _memes_dir: 表情包图片目录路径。
        _no_text_dir: 无文字图存放目录路径。
        _entries: 内存中的 index entries。
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestAddResult tests/unit/engine/test_index_manager.py::TestIndexManagerInit -v`

Expected: PASS（`TestAddResult` 3 个 + `TestIndexManagerInit` 6 个全过）。

- [ ] **Step 6: 运行全量测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`

Expected: PASS（现有 `TestAddEntry` 仍通过——`add_entry` 尚未改返回类型，本任务只加数据类和构造参数）。

- [ ] **Step 7: 编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`

Expected: 无输出。

- [ ] **Step 8: 用户审核检查点 — 请审阅 diff 并提交**

暂停。改动摘要：新增 `AddResult` 数据类；`__init__` 加 `no_text_dir` 参数与 `_no_text_dir` 属性（默认 `memes/` 同级 `meme_no_text/`）；对应测试新增。

请用户审阅 diff 后自行执行：
```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 新增 AddResult 数据类与 no_text_dir 配置"
```

---

### Task 4: `_find_entry_by_dedup_key` 与 `_move_to_no_text` 私有方法（TDD）

**Files:**
- Modify: `bot/engine/index_manager.py`（在 `remove_entry` 之后、`acquire_lock` 之前，约第 539 行后插入两个私有方法）
- Modify: `tests/unit/engine/test_index_manager.py`（新增 `TestFindEntryByDedupKey`、`TestMoveToNoText`）

- [ ] **Step 1: 编写失败测试 — `_find_entry_by_dedup_key`**

在 `tests/unit/engine/test_index_manager.py` 末尾追加：

```python
class TestFindEntryByDedupKey:
    """_find_entry_by_dedup_key 私有方法测试。"""

    def test_match_found(self) -> None:
        """去重键命中已有条目时返回其 ID。"""
        mgr = IndexManager()
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "加班 好累", "text_hash": "x"},
            "2": {"filename": "b.jpg", "text": "狗在跑", "text_hash": "y"},
        }
        # "加班 好累" 去空格 == "加班好累"
        assert mgr._find_entry_by_dedup_key("加班好累") == "1"

    def test_no_match_returns_none(self) -> None:
        """无命中返回 None。"""
        mgr = IndexManager()
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "猫", "text_hash": "x"},
        }
        assert mgr._find_entry_by_dedup_key("狗") is None

    def test_empty_entries_returns_none(self) -> None:
        """空索引返回 None。"""
        mgr = IndexManager()
        assert mgr._find_entry_by_dedup_key("anything") is None
```

- [ ] **Step 2: 编写失败测试 — `_move_to_no_text`**

在 `tests/unit/engine/test_index_manager.py` 末尾追加：

```python
class TestMoveToNoText:
    """_move_to_no_text 私有方法测试。"""

    def test_moves_file_to_no_text_dir(self, tmp_path: Path) -> None:
        """无文字图从 memes/ 移到 meme_no_text/。"""
        memes_dir = tmp_path / "memes"
        no_text_dir = tmp_path / "meme_no_text"
        memes_dir.mkdir()
        src = memes_dir / "blank.jpg"
        src.write_text("fake", encoding="utf-8")

        mgr = IndexManager(
            memes_dir=str(memes_dir),
            no_text_dir=str(no_text_dir),
        )
        moved_to = mgr._move_to_no_text("blank.jpg")

        assert no_text_dir.exists()
        assert not src.exists()
        assert Path(moved_to) == no_text_dir / "blank.jpg"
        assert (no_text_dir / "blank.jpg").read_text(encoding="utf-8") == "fake"

    def test_creates_no_text_dir_if_missing(self, tmp_path: Path) -> None:
        """meme_no_text/ 不存在时自动创建。"""
        memes_dir = tmp_path / "memes"
        no_text_dir = tmp_path / "meme_no_text"
        memes_dir.mkdir()
        (memes_dir / "b.png").write_text("x", encoding="utf-8")

        mgr = IndexManager(
            memes_dir=str(memes_dir),
            no_text_dir=str(no_text_dir),
        )
        assert not no_text_dir.exists()
        mgr._move_to_no_text("b.png")
        assert no_text_dir.exists()

    def test_name_conflict_appends_sequence(self, tmp_path: Path) -> None:
        """目标已存在同名文件时追加序号。"""
        memes_dir = tmp_path / "memes"
        no_text_dir = tmp_path / "meme_no_text"
        memes_dir.mkdir()
        no_text_dir.mkdir()
        (memes_dir / "blank.jpg").write_text("new", encoding="utf-8")
        (no_text_dir / "blank.jpg").write_text("old", encoding="utf-8")

        mgr = IndexManager(
            memes_dir=str(memes_dir),
            no_text_dir=str(no_text_dir),
        )
        moved_to = mgr._move_to_no_text("blank.jpg")

        assert Path(moved_to) == no_text_dir / "blank_2.jpg"
        assert (no_text_dir / "blank_2.jpg").read_text(encoding="utf-8") == "new"
        # 原有文件不被覆盖
        assert (no_text_dir / "blank.jpg").read_text(encoding="utf-8") == "old"
        assert not (memes_dir / "blank.jpg").exists()
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestFindEntryByDedupKey tests/unit/engine/test_index_manager.py::TestMoveToNoText -v`

Expected: FAIL — `AttributeError: 'IndexManager' object has no attribute '_find_entry_by_dedup_key'` / `'_move_to_no_text'`。

- [ ] **Step 4: 实现两个私有方法**

在 `bot/engine/index_manager.py` 中，`remove_entry` 方法结束之后（第 539 行 `return True` 之后、`# 锁管理` 分隔注释第 541 行之前）插入：

```python
    def _find_entry_by_dedup_key(self, key: str) -> str | None:
        """按去重键查找已有条目 ID。

        线性扫描 _entries，返回第一个 dedup_key(text) == key 的条目 ID。
        正常情况下去重键唯一（add/sync 已保证不引入重复键），
        返回第一个匹配即可。

        Args:
            key: dedup_key 计算结果。

        Returns:
            匹配的条目 ID，无匹配返回 None。
        """
        for entry_id, entry in self._entries.items():
            if dedup_key(entry.get("text", "")) == key:
                return entry_id
        return None

    def _move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        自动创建 meme_no_text/ 目录；目标同名时追加序号。
        shutil.move 在跨设备时会自动回退为复制+删除。

        Args:
            filename: memes/ 下的源文件名。

        Returns:
            移入 meme_no_text/ 后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = _resolve_unique_filename(self._no_text_dir, filename)
        shutil.move(str(src), str(dst))
        logger.warning(
            "OCR 未识别到文字，已移至无文字目录: %s -> %s",
            filename,
            dst,
        )
        return str(dst)
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestFindEntryByDedupKey tests/unit/engine/test_index_manager.py::TestMoveToNoText -v`

Expected: PASS（6 个测试全过）。

- [ ] **Step 6: 运行全量测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`

Expected: PASS（私有方法新增不影响现有测试）。

- [ ] **Step 7: 用户审核检查点 — 请审阅 diff 并提交**

暂停。改动摘要：新增 `_find_entry_by_dedup_key`（线性扫描去重键）、`_move_to_no_text`（移图到 `meme_no_text/` + 序号冲突处理）两个私有方法；对应测试新增。

请用户审阅 diff 后自行执行：
```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 新增 _find_entry_by_dedup_key 与 _move_to_no_text 方法"
```

---

### Task 5: 改造 `add_entry` 返回 `AddResult`（含去重覆盖与无文字处理）+ 修复既有 `TestAddEntry`

**Files:**
- Modify: `bot/engine/index_manager.py`（第 476-513 行 `add_entry` 方法整体重写）
- Modify: `tests/unit/engine/test_index_manager.py`（第 518-573 行 `TestAddEntry`：更新 3 个断言返回值的测试 + 新增去重/无文字测试）

- [ ] **Step 1: 编写失败测试 — 重写 `TestAddEntry`**

在 `tests/unit/engine/test_index_manager.py` 中，把整个 `TestAddEntry` 类（第 518-573 行）替换为：

```python
class TestAddEntry:
    """add_entry() 测试。"""

    def test_add_entry_assigns_id(self, tmp_path: Path) -> None:
        """add_entry 正常新增，返回 AddResult(entry_id, 'added')。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {}
        mgr._embeddings = {}

        result = mgr.add_entry(
            filename="new.jpg",
            text="新图片",
            embedding=[0.1, 0.2],
        )
        assert result.entry_id == "1"
        assert result.reason == "added"
        assert result.replaced_filename is None
        assert result.moved_to is None
        assert mgr._entries["1"]["filename"] == "new.jpg"
        assert mgr._entries["1"]["text"] == "新图片"
        assert mgr._entries["1"]["text_hash"] == compute_text_hash("新图片")
        assert mgr._embeddings["1"]["embedding"] == [0.1, 0.2]

    def test_add_entry_reuses_hole(self, tmp_path: Path) -> None:
        """add_entry 在有空洞时复用最小空洞 ID。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {"1": {"filename": "a.jpg", "text": "a", "text_hash": "x"}}
        mgr._embeddings = {}

        result = mgr.add_entry(
            filename="b.jpg",
            text="b",
            embedding=[0.5],
        )
        assert result.entry_id == "2"  # 无空洞，取 max+1
        assert result.reason == "added"

        # 删除 1 后添加，应复用 1
        mgr.remove_entry("1")
        result2 = mgr.add_entry(
            filename="c.jpg",
            text="c",
            embedding=[0.8],
        )
        assert result2.entry_id == "1"

    def test_add_entry_saves_to_disk(self, tmp_path: Path) -> None:
        """add_entry 后数据持久化到磁盘。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {}
        mgr._embeddings = {}
        mgr.add_entry("x.jpg", "test", [1.0])

        index_path = tmp_path / "index.json"
        assert index_path.exists()
        data = ujson.loads(index_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 1

        emb_path = tmp_path / "embeddings.json"
        assert emb_path.exists()

    def test_add_entry_replaces_on_dedup(
        self, tmp_path: Path
    ) -> None:
        """去重键命中已有条目时，复用旧 ID 覆盖并删旧图文件。

        场景：已有 a.jpg(text="加班 好累")，再 add b.jpg(text="加班好累")，
        两者 dedup_key 相同 → 复用 id=1，filename 改为 b.jpg，
        磁盘 a.jpg 删除、b.jpg 保留，返回 reason='replaced'。
        """
        memes_dir = tmp_path / "memes"
        memes_dir.mkdir()
        (memes_dir / "a.jpg").write_text("a", encoding="utf-8")
        (memes_dir / "b.jpg").write_text("b", encoding="utf-8")

        mgr = IndexManager(data_dir=str(tmp_path), memes_dir=str(memes_dir))
        mgr._entries = {
            "1": {
                "filename": "a.jpg",
                "text": "加班 好累",
                "text_hash": compute_text_hash("加班 好累"),
            }
        }
        mgr._embeddings = {}

        result = mgr.add_entry(
            filename="b.jpg",
            text="加班好累",
            embedding=[0.9],
        )
        assert result.entry_id == "1"
        assert result.reason == "replaced"
        assert result.replaced_filename == "a.jpg"
        # 旧图文件已删除
        assert not (memes_dir / "a.jpg").exists()
        # 新图文件保留
        assert (memes_dir / "b.jpg").exists()
        # 索引已覆盖：id=1 的 filename 变为 b.jpg，text 与 hash 更新
        assert mgr._entries["1"]["filename"] == "b.jpg"
        assert mgr._entries["1"]["text"] == "加班好累"
        assert mgr._entries["1"]["text_hash"] == compute_text_hash("加班好累")
        assert mgr._embeddings["1"]["embedding"] == [0.9]

    def test_add_entry_replaces_when_old_image_missing(
        self, tmp_path: Path
    ) -> None:
        """旧图文件已被外部删除时，去重覆盖仍完成索引替换（missing_ok）。"""
        memes_dir = tmp_path / "memes"
        memes_dir.mkdir()
        (memes_dir / "b.jpg").write_text("b", encoding="utf-8")
        # a.jpg 在索引里但磁盘上不存在（模拟用户手动删图但索引还在）

        mgr = IndexManager(data_dir=str(tmp_path), memes_dir=str(memes_dir))
        mgr._entries = {
            "1": {
                "filename": "a.jpg",
                "text": "猫",
                "text_hash": compute_text_hash("猫"),
            }
        }
        mgr._embeddings = {}

        result = mgr.add_entry("b.jpg", "猫", [0.5])
        assert result.reason == "replaced"
        assert result.replaced_filename == "a.jpg"
        assert mgr._entries["1"]["filename"] == "b.jpg"

    def test_add_entry_no_text_moves_file(
        self, tmp_path: Path
    ) -> None:
        """OCR 无文字时移到 meme_no_text/ 不进索引，返回 reason='no_text'。"""
        memes_dir = tmp_path / "memes"
        no_text_dir = tmp_path / "meme_no_text"
        memes_dir.mkdir()
        (memes_dir / "blank.jpg").write_text("x", encoding="utf-8")

        mgr = IndexManager(
            data_dir=str(tmp_path),
            memes_dir=str(memes_dir),
            no_text_dir=str(no_text_dir),
        )
        mgr._entries = {}
        mgr._embeddings = {}

        result = mgr.add_entry("blank.jpg", "   ", [0.0])
        assert result.entry_id is None
        assert result.reason == "no_text"
        assert result.moved_to is not None
        assert Path(result.moved_to) == no_text_dir / "blank.jpg"
        # 源文件已移走
        assert not (memes_dir / "blank.jpg").exists()
        # 未写入索引
        assert mgr._entries == {}
        assert mgr._embeddings == {}
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestAddEntry -v`

Expected: FAIL — 现有 `add_entry` 仍返回 `str`，`result.entry_id` 访问报错 `AttributeError: 'str' object has no attribute 'entry_id'`；新测试 `test_add_entry_replaces_on_dedup` / `test_add_entry_no_text_moves_file` 失败。

- [ ] **Step 3: 重写 `add_entry` 方法**

在 `bot/engine/index_manager.py` 中，把 `add_entry`（第 476-513 行）整体替换为：

```python
    def add_entry(
        self,
        filename: str,
        text: str,
        embedding: list[float],
    ) -> AddResult:
        """添加单条索引记录，处理无文字与 OCR 文本去重。

        三分支：
        1. 无文字（去所有空白后为空）→ 移图到 meme_no_text/，不进索引，
           返回 AddResult(entry_id=None, reason="no_text", moved_to=...)。
        2. 去重键命中已有条目 → 删旧图文件，复用旧 ID 覆盖记录与 embedding，
           返回 AddResult(entry_id=旧id, reason="replaced",
                         replaced_filename=旧文件名)。
        3. 正常新增 → 分配新 ID 写入，返回 AddResult(entry_id, reason="added")。

        Args:
            filename: 表情包文件名。
            text: OCR 识别文本。
            embedding: embedding 向量。

        Returns:
            描述本次结果的 AddResult。
        """
        # 1. 无文字 → 移图，不进索引
        if is_blank_text(text):
            moved_to = self._move_to_no_text(filename)
            logger.info("OCR 无文字，已移至无文字目录，不入索引: filename=%s", filename)
            return AddResult(
                entry_id=None,
                reason="no_text",
                moved_to=moved_to,
            )

        # 2. 去重键命中已有条目 → 删旧图，复用旧 ID 覆盖
        key = dedup_key(text)
        old_id = self._find_entry_by_dedup_key(key)
        if old_id is not None:
            old_filename = self._entries[old_id].get("filename", "")
            old_path = self._memes_dir / old_filename
            old_path.unlink(missing_ok=True)

            text_hash = compute_text_hash(text)
            self._entries[old_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._embeddings[old_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            self.save_index()
            self.save_embeddings()
            logger.info(
                "检测到重复 OCR 文本，已用新图替换: id=%s, 旧=%s, 新=%s",
                old_id,
                old_filename,
                filename,
            )
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                replaced_filename=old_filename,
            )

        # 3. 正常新增
        entry_id = self._find_next_id()
        text_hash = compute_text_hash(text)
        self._entries[entry_id] = {
            "filename": filename,
            "text": text,
            "text_hash": text_hash,
        }
        self._embeddings[entry_id] = {
            "text_hash": text_hash,
            "embedding": embedding,
        }
        self.save_index()
        self.save_embeddings()
        logger.info("已添加索引记录: id=%s, filename=%s", entry_id, filename)
        return AddResult(entry_id=entry_id, reason="added")
```

- [ ] **Step 4: 运行 `TestAddEntry`，确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestAddEntry -v`

Expected: PASS（6 个测试全过：3 个改写 + 3 个新增）。

- [ ] **Step 5: 运行全量测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`

Expected: 现有 `TestRemoveEntry` 等仍通过。**但 `TestSyncWithFilesystem` 中 `test_sync_adds_new_images`、`test_sync_new_images_sorted_by_filename`、`test_sync_ocr_runs_concurrently` 可能仍通过**（因为 sync 尚未改造，此时还不会触发去重）——记录任何失败，留待 Task 6 处理。

- [ ] **Step 6: 编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`

Expected: 无输出。

- [ ] **Step 7: 用户审核检查点 — 请审阅 diff 并提交**

暂停。改动摘要：`add_entry` 返回类型 `str`→`AddResult`，新增无文字移图、去重覆盖（复用旧 ID + 删旧图）两个分支；`TestAddEntry` 改写 3 个断言、新增 3 个测试。

> ⚠️ 这是**对外接口变更**（返回类型从 `str` 改为 `AddResult`）。当前 `add_entry` 无调用方（插件层未实现），不会破坏现有运行代码，但需在 Task 8 同步更新 `docs/api/API.md`。

请用户审阅 diff 后自行执行：
```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): add_entry 支持 OCR 去重覆盖与无文字排除，返回 AddResult"
```

---

### Task 6: 改造 `sync_with_filesystem` 新增阶段（`winner_keys` 三分类）+ 修复受影响的 sync 测试

**Files:**
- Modify: `bot/engine/index_manager.py`（第 571-622 行 `sync_with_filesystem` 主方法；第 762-825 行 `_sync_additions` 方法；第 854-874 行 `_persist_sync_results`）
- Modify: `tests/unit/engine/test_index_manager.py`（修复 2 个因相同 OCR 文本而受影响的测试 + 新增去重/无文字 sync 测试）

- [ ] **Step 1: 分析并修复受影响的现有 sync 测试**

现有两个测试给所有图片喂**相同** OCR 文本 `"text"`，改造后会被去重折叠。需把每个文件的 OCR 文本改成各不相同，保留原测试意图：

**测试 1：`test_sync_new_images_sorted_by_filename`（第 976-1024 行）**——原意图是验证「按文件名升序分配 ID」。把 `OrderedOcr.ocr` 返回值从 `"text"` 改为按文件名区分：

```python
        class OrderedOcr:
            async def ocr(self, image_path: str) -> str:
                # 每张图返回不同文本，避免触发去重，专注验证文件名升序分配 ID
                return f"text of {Path(image_path).name}"
```

并更新末尾断言（第 1021-1024 行）——ID 按文件名升序对应 `a.jpg`/`m.jpg`/`z.jpg`，但 OCR 文本现在带文件名，断言文件名不变：

```python
        # 按 id 数值升序排列，对应文件名应为 a.jpg, m.jpg, z.jpg
        sorted_ids = sorted(mgr._entries.keys(), key=int)
        filenames_by_id = [mgr._entries[eid]["filename"] for eid in sorted_ids]
        assert filenames_by_id == ["a.jpg", "m.jpg", "z.jpg"]
```

（此断言原文不变，只是 OCR 文本变了；确认无需改断言。）

**测试 2：`test_sync_ocr_runs_concurrently`（第 1026-1088 行）**——原意图是验证「并发上限 > 1」。把 `ConcurrentOcr.ocr` 返回值从 `"text"` 改为按文件名区分：

```python
        class ConcurrentOcr:
            async def ocr(self, image_path: str) -> str:
                nonlocal in_flight, max_in_flight
                async with counter_lock:
                    in_flight += 1
                    if in_flight > max_in_flight:
                        max_in_flight = in_flight
                # 让出控制权，让其他任务有机会并行进入
                await asyncio.sleep(0.01)
                async with counter_lock:
                    in_flight -= 1
                # 每张图返回不同文本，避免触发去重
                return f"text of {Path(image_path).name}"
```

并更新断言（第 1085 行）——`added` 仍应为 6（6 张图文本各不相同，无去重）：

```python
        assert result.added == 6
        assert result.deduped == 0
        assert max_in_flight >= 2, (
            f"OCR 未并行执行，最大同时执行数仅 {max_in_flight}"
        )
```

- [ ] **Step 2: 编写新失败测试 — sync 去重与无文字**

在 `tests/unit/engine/test_index_manager.py` 的 `TestSyncWithFilesystem` 类内末尾（`test_sync_rebuild_failure_recorded_in_failed` 之后，第 1330 行前）追加：

```python
    def test_sync_dedup_new_vs_existing(self, tmp_path: Path) -> None:
        """新图去重键命中已有条目时，现有条目赢，删新图文件，不新增。

        场景：索引已有 old.jpg(text="加班")，memes/ 放入 new.jpg
        且 OCR 得 "加 班"（去空格同键）→ 现有条目赢，new.jpg 被删，
        索引不变，deduped=1, added=0。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "old.jpg").write_text("old", encoding="utf-8")
        (memes_dir / "new.jpg").write_text("new", encoding="utf-8")

        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "old.jpg",
                    "text": "加班",
                    "text_hash": compute_text_hash("加班"),
                }
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        (data_dir / "embeddings.json").write_text(
            ujson.dumps(
                {"1": {"text_hash": compute_text_hash("加班"), "embedding": [0.1]}}
            ),
            encoding="utf-8",
        )

        class MockOcr:
            async def ocr(self, image_path: str) -> str:
                # new.jpg OCR 得 "加 班"，与已有 "加班" 去空格同键
                return "加 班"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.5]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=MockOcr(),
            embedding_provider=MockEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.added == 0
        assert result.deduped == 1
        assert result.no_text_moved == 0
        assert result.failed == []
        # 现有条目保留，新图被删
        assert not (memes_dir / "new.jpg").exists()
        assert (memes_dir / "old.jpg").exists()
        assert mgr._entries["1"]["filename"] == "old.jpg"
        assert len(mgr._entries) == 1

    def test_sync_dedup_between_new_images(self, tmp_path: Path) -> None:
        """两张新图互重时，文件名升序靠前的赢，靠后的被删。

        场景：memes/ 放入 b.jpg 和 a.jpg，OCR 都得 "同文"。
        a.jpg 靠前 → 保留并进索引；b.jpg 靠后 → 删除。added=1, deduped=1。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "b.jpg").write_text("b", encoding="utf-8")
        (memes_dir / "a.jpg").write_text("a", encoding="utf-8")

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        class SameOcr:
            async def ocr(self, image_path: str) -> str:
                return "同文"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.5]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=SameOcr(),
            embedding_provider=MockEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.added == 1
        assert result.deduped == 1
        # a.jpg 靠前保留，b.jpg 靠后被删
        assert (memes_dir / "a.jpg").exists()
        assert not (memes_dir / "b.jpg").exists()
        assert len(mgr._entries) == 1
        assert mgr._entries["1"]["filename"] == "a.jpg"

    def test_sync_no_text_image_moved(self, tmp_path: Path) -> None:
        """OCR 无文字的新图移到 meme_no_text/，不进索引。

        场景：memes/ 放入 blank.jpg（OCR 返回纯空白）和 ok.jpg（有文字）。
        blank.jpg → 移到 meme_no_text/；ok.jpg → 正常新增。
        added=1, no_text_moved=1, deduped=0。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        no_text_dir = tmp_path / "meme_no_text"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "blank.jpg").write_text("x", encoding="utf-8")
        (memes_dir / "ok.jpg").write_text("y", encoding="utf-8")

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        class MockOcr:
            async def ocr(self, image_path: str) -> str:
                if "blank" in image_path:
                    return "   "  # 纯空白
                return "有文字"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.5]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            no_text_dir=str(no_text_dir),
            ocr_provider=MockOcr(),
            embedding_provider=MockEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.added == 1
        assert result.no_text_moved == 1
        assert result.deduped == 0
        assert result.failed == []
        # blank.jpg 移到 meme_no_text/
        assert not (memes_dir / "blank.jpg").exists()
        assert (no_text_dir / "blank.jpg").exists()
        # ok.jpg 正常进索引
        assert (memes_dir / "ok.jpg").exists()
        assert len(mgr._entries) == 1
        assert mgr._entries["1"]["filename"] == "ok.jpg"

    def test_sync_counts_do_not_overlap(self, tmp_path: Path) -> None:
        """混合场景计数不重叠：2 新增 + 1 去重 + 1 无文字。

        memes/ 放 4 张新图：
        - ok1.jpg, ok2.jpg：文本不同，正常新增
        - dup.jpg：OCR 文本与 ok1.jpg 相同 → 去重删除
        - blank.jpg：OCR 纯空白 → 移到 meme_no_text/
        结果：added=2, deduped=1, no_text_moved=1, deleted=0, failed=[]。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        no_text_dir = tmp_path / "meme_no_text"
        data_dir.mkdir()
        memes_dir.mkdir()

        # 文件名升序：blank.jpg, dup.jpg, ok1.jpg, ok2.jpg
        (memes_dir / "ok1.jpg").write_text("1", encoding="utf-8")
        (memes_dir / "ok2.jpg").write_text("2", encoding="utf-8")
        (memes_dir / "dup.jpg").write_text("3", encoding="utf-8")
        (memes_dir / "blank.jpg").write_text("4", encoding="utf-8")

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        class MockOcr:
            async def ocr(self, image_path: str) -> str:
                name = Path(image_path).name
                if name == "blank.jpg":
                    return "  "
                if name == "ok1.jpg":
                    return "文本一"
                if name == "dup.jpg":
                    return "文 本一"  # 去空格 == "文本一"，与 ok1 重复
                if name == "ok2.jpg":
                    return "文本二"
                return "other"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.5]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            no_text_dir=str(no_text_dir),
            ocr_provider=MockOcr(),
            embedding_provider=MockEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.added == 2
        assert result.deduped == 1
        assert result.no_text_moved == 1
        assert result.deleted == 0
        assert result.failed == []
        # dup.jpg 被删（与 ok1 去重，ok1 文件名靠后但先成为赢家？否：ok1 文件名 > dup，dup 先处理）
        # 文件名升序处理：dup.jpg 先于 ok1.jpg。
        # dup.jpg 先处理时 winner_keys 为空（无已有条目），dup 正常新增成为赢家。
        # ok1.jpg 后处理，dedup_key("文本一") == dup 的键 → ok1 被去重删除。
        # 因此保留的是 dup.jpg，删除的是 ok1.jpg。
        assert (memes_dir / "dup.jpg").exists()
        assert not (memes_dir / "ok1.jpg").exists()
        assert (memes_dir / "ok2.jpg").exists()
        assert not (memes_dir / "blank.jpg").exists()
        assert (no_text_dir / "blank.jpg").exists()
        assert len(mgr._entries) == 2

    def test_sync_preserves_old_no_text_placeholder(
        self, tmp_path: Path
    ) -> None:
        """本功能上线前留下的「未识别到文字」占位条目，sync 后保留不清理。

        场景：index.json 已有 id=1 text="未识别到文字"（旧占位条目），
        对应文件 cat.jpg 仍在 memes/。sync 重建阶段不重新 OCR，
        该条目保留。dedup_key("未识别到文字") 非空，不触发无文字排除。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "cat.jpg").write_text("c", encoding="utf-8")

        old_placeholder = "未识别到文字"
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "cat.jpg",
                    "text": old_placeholder,
                    "text_hash": compute_text_hash(old_placeholder),
                }
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        (data_dir / "embeddings.json").write_text(
            ujson.dumps(
                {
                    "1": {
                        "text_hash": compute_text_hash(old_placeholder),
                        "embedding": [0.0],
                    }
                }
            ),
            encoding="utf-8",
        )

        mgr = IndexManager(data_dir=str(data_dir), memes_dir=str(memes_dir))
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        # 旧占位条目保留
        assert "1" in mgr._entries
        assert mgr._entries["1"]["text"] == old_placeholder
        assert result.added == 0
        assert result.deleted == 0
        assert result.deduped == 0
        assert result.no_text_moved == 0
```

- [ ] **Step 3: 运行新测试，确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_dedup_new_vs_existing tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_dedup_between_new_images tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_no_text_image_moved tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_counts_do_not_overlap tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_preserves_old_no_text_placeholder -v`

Expected: FAIL — `sync_with_filesystem` 尚未改造，`deduped`/`no_text_moved` 恒为 0，去重/无文字场景下新图被当普通新增计入 `added`。

- [ ] **Step 4: 改造 `_sync_additions` 返回三元组 + 三分类**

在 `bot/engine/index_manager.py` 中，把 `_sync_additions`（第 762-825 行）整体替换为：

```python
    async def _sync_additions(
        self,
        existing_files: set[str],
        filename_to_id: dict[str, str],
        failed: list[str],
    ) -> tuple[int, int, int]:
        """新增阶段：并行 OCR→embed，再按文件名升序串行三分类。

        三分类（基于 winner_keys 赢家集合增量判定）：
        1. 无文字（去所有空白为空）→ _move_to_no_text 移图，no_text_moved++。
        2. 去重键命中 winner_keys（已有条目或本轮更靠前的保留新图）
           → 删新图文件，deduped++。现有条目/靠前图赢。
        3. 正常新增 → 分配 ID 写入，winner_keys 加入该键，added++。

        winner_keys 初始 = 已有条目的去重键（现有条目天然是赢家），
        每张保留的新图将其键加入，从而让后续同键新图被判重。

        Args:
            existing_files: memes/ 当前文件名集合。
            filename_to_id: filename → id 映射（用于判断哪些是新增）。
            failed: 失败文件名收集列表（就地追加新增失败的 filename）。

        Returns:
            (added, deduped, no_text_moved) 三元组。
        """
        import asyncio

        new_files = sorted(f for f in existing_files if f not in filename_to_id)
        if not new_files:
            return (0, 0, 0)

        logger.info(
            "开始并行处理 %d 张新增图片，并发上限 %d",
            len(new_files),
            self._sync_concurrency,
        )

        raw_results = await asyncio.gather(
            *(self._process_new_file(fn) for fn in new_files),
            return_exceptions=True,
        )

        # 成功项以 filename 为 key 收集
        success_by_name: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw_results):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                _, text, embedding = result
                success_by_name[filename] = (text, embedding)

        # 赢家集合：初始 = 已有条目的去重键（现有条目天然是赢家）
        winner_keys: set[str] = {
            dedup_key(entry.get("text", "")) for entry in self._entries.values()
        }

        added = deduped = no_text_moved = 0

        # 按文件名升序串行分类，决定新图互重时的赢家
        for filename in sorted(success_by_name.keys()):
            text, embedding = success_by_name[filename]

            # 1. 无文字 → 移图，不进索引
            if is_blank_text(text):
                self._move_to_no_text(filename)
                no_text_moved += 1
                continue

            # 2. 去重键命中赢家 → 删新图，不进索引
            key = dedup_key(text)
            if key in winner_keys:
                new_path = self._memes_dir / filename
                new_path.unlink(missing_ok=True)
                logger.info("新图与已有索引去重，删除新图: filename=%s", filename)
                deduped += 1
                continue

            # 3. 正常新增
            entry_id = self._find_next_id()
            text_hash = compute_text_hash(text)
            self._entries[entry_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._embeddings[entry_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            winner_keys.add(key)
            added += 1
            logger.info("新增图片已加入索引: id=%s, filename=%s", entry_id, filename)

        return (added, deduped, no_text_moved)
```

- [ ] **Step 5: 改造 `sync_with_filesystem` 主方法**

在 `bot/engine/index_manager.py` 中，把 `sync_with_filesystem`（第 571-622 行）整体替换为：

```python
    async def sync_with_filesystem(self) -> SyncResult:
        """按文件名同步索引与 memes/ 目录。

        三阶段并行同步：

        1. 删除阶段：扫描 memes/，移除已不存在的图片对应记录。
        2. 重建阶段（embedding 过期修复 + 全量重建）：
           - 对文件仍存在的已有条目，比较 _entries[id].text_hash 与
             _embeddings[id].text_hash（或 _embeddings 缺该 id）；
           - 不一致则用当前 text 重建对应 embedding，覆盖 _embeddings[id]。
           - 该判定同时覆盖两类 PRD 要求：
             a) 用户手动编辑 index.json 的 text 导致 text_hash 不一致
                （load 阶段已按新 text 修复 _entries[id].text_hash）；
             b) embeddings.json 缺失/损坏导致 _embeddings 为空，全部条目
               触发全量重建。
        3. 新增阶段：对新增图片按文件名升序并行 OCR→embed，再串行三分类
           （无文字移图 / 去重删新图 / 正常新增）。去重基于 winner_keys 赢家
           集合增量判定：现有条目与靠前新图赢，靠后/重复新图被删。
           结果收集后按文件名升序统一分配 ID 写入索引（复用最小空洞 id）。

        各阶段内部并行，阶段间串行（先完成全部重建再开始新增）。
        单个图片失败不影响其他图片，记入 failed。全部处理完成后统一原子写入。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
            重建数量仅在日志中输出，不计入 SyncResult。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)

        existing_files = self._scan_meme_files()
        filename_to_id = self._build_filename_to_id()

        # 1. 删除已不存在的图片
        deleted_count = self._sync_deletions(existing_files, filename_to_id)

        # 2. 重建过期/缺失的 embedding（仅在删除后剩余的条目中）
        failed: list[str] = []
        rebuild_count = await self._sync_rebuilds(failed)

        # 3. 新增图片并行 OCR + embedding，再三分类
        added_count, deduped_count, no_text_count = await self._sync_additions(
            existing_files, filename_to_id, failed
        )

        # 4. 全部完成后统一原子写入
        self._persist_sync_results(added_count, deleted_count, rebuild_count)

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 重建=%d, 失败=%d",
            added_count,
            deleted_count,
            deduped_count,
            no_text_count,
            rebuild_count,
            len(failed),
        )
        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            failed=failed,
        )
```

> `_persist_sync_results`（第 854-874 行）**不需要修改**：写盘判定仍按 `added/deleted/rebuild`，去重与无文字不产生索引记录变更。

- [ ] **Step 6: 运行新测试，确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_dedup_new_vs_existing tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_dedup_between_new_images tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_no_text_image_moved tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_counts_do_not_overlap tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_preserves_old_no_text_placeholder -v`

Expected: PASS（5 个新测试全过）。

> 注意 `test_sync_counts_do_not_overlap` 的赢家推导：文件名升序 `blank, dup, ok1, ok2`。`blank` 无文字移走；`dup`（"文 本一"→"文本一"）先处理，winner_keys 为空 → 正常新增成赢家；`ok1`（"文本一"）后处理，键命中 `dup` → `ok1` 被删；`ok2` 正常新增。故保留 `dup`+`ok2`，删除 `ok1`。测试断言与此一致。

- [ ] **Step 7: 运行受影响的现有测试，确认修复**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_new_images_sorted_by_filename tests/unit/engine/test_index_manager.py::TestSyncWithFilesystem::test_sync_ocr_runs_concurrently -v`

Expected: PASS（两个测试因 OCR 文本改为按文件名区分，不再触发去重，added 仍为 3/6）。

- [ ] **Step 8: 运行全量测试**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`

Expected: PASS（所有测试全过——含 Task 1-6 全部新增与改写）。

- [ ] **Step 9: 编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`

Expected: 无输出。

- [ ] **Step 10: 用户审核检查点 — 请审阅 diff 并提交**

暂停。改动摘要：
- `_sync_additions` 返回 `(added, deduped, no_text_moved)` 三元组，内含 `winner_keys` 三分类逻辑。
- `sync_with_filesystem` 主方法适配三元组返回值，日志与 `SyncResult` 填充新字段。
- 修复 2 个现有 sync 测试（OCR 文本改为按文件名区分）。
- 新增 5 个 sync 测试（去重 vs 已有 / 新图互重 / 无文字 / 计数不重叠 / 旧占位条目保留）。

请用户审阅 diff 后自行执行：
```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): sync_with_filesystem 新增阶段支持去重与无文字排除"
```

---

### Task 7: 全量测试回归 + 编译检查

**Files:** 无文件改动，仅验证。

- [ ] **Step 1: 运行整个 engine 测试套件**

Run: `uv run pytest tests/unit/engine/ -v`

Expected: PASS（`test_index_manager.py` + `test_keyword_searcher.py` 全过）。

- [ ] **Step 2: 运行全仓库测试**

Run: `uv run pytest -v`

Expected: PASS（含 `test_logging_config.py`）。

- [ ] **Step 3: 编译检查整个 bot 包**

Run: `uv run python -m compileall bot`

Expected: 无输出（编译成功）。

- [ ] **Step 4: 用户审核检查点**

暂停。报告：所有测试通过、编译通过。无需提交（本任务无文件改动）。

---

### Task 8: 同步项目文档（PRD / CONTEXT / API / process / README / CLAUDE.md / docker-compose）

**Files:**
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `docs/api/API.md`
- Modify: `docs/process.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docker-compose.yml`

> 本任务为纯文档改动，无测试。每个文件按下方精确编辑指引修改。

- [ ] **Step 1: `docker-compose.yml` — 新增 `meme_no_text` 卷映射**

在 `docker-compose.yml` 的 bot 服务 `volumes` 列表（第 41-44 行），在 `- ./log:/app/log` 之后追加一行：

```yaml
    volumes:
      - ./memes:/app/memes
      - ./data:/app/data
      - ./log:/app/log
      - ./meme_no_text:/app/meme_no_text
```

- [ ] **Step 2: `docs/PRD.md` §3.3 `/add` — 增加去重与无文字处理**

在 `docs/PRD.md` 第 206 行（`/add` 交互约束最后一条「OCR 或 embedding 失败…」之后、第 207 行锁约束之前）插入两条约束：

```markdown
- `/add` 写入前按 OCR 文本去重：以「去除所有空白字符后的文本」为去重键，若命中已有表情包，则删除旧图片文件并用新图替换（复用旧索引 ID，覆盖 text、text_hash 与 embedding）；该机制默认认为去重键相同即为同一表情包，不额外校验图片内容。
- `/add` 若 OCR 结果去除所有空白后为空（无文字图片），则将该图片移动到 `memes/` 同级的 `meme_no_text/` 目录（不进索引），并回复"未识别到文字，已移至 meme_no_text/"。
```

- [ ] **Step 3: `docs/PRD.md` §3.5 第 12 条 — 扩展 `/refresh` 回复摘要**

把 `docs/PRD.md` 第 265 行（第 12 条）替换为：

```markdown
12. `/refresh` 完成后回复摘要：新增数量、删除数量、去重数量、无文字移走数量、失败数量；如有失败，最多列出前 10 个失败文件名
```

- [ ] **Step 4: `docs/PRD.md` §3.5 增量更新 — 新增第 13、14 条**

在 `docs/PRD.md` 第 266 行（`v1.0 不检测同名覆盖…」那一段之后、第 269 行权限约束之前）插入：

```markdown
13. 新增图片 OCR 后按「去除所有空白字符后的文本」去重键判定：若与已有条目或其他新增图片去重键相同，则保留已有条目或文件名升序靠前的新图，删除被判定为重复的新图文件，不写入索引；该去重在 `/refresh` 回复中以「去重数量」单独统计，不计入新增或删除。
14. 新增图片 OCR 结果去除所有空白后为空（无文字图片）时，移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引；`index.json` 中本功能上线前已存在的「未识别到文字」占位条目不清理（sync 不重新 OCR 已有条目）。
```

- [ ] **Step 5: `docs/PRD.md` §5 边界情况 — 改写「OCR 无文字」行 + 新增去重行**

把 `docs/PRD.md` 第 359 行（`| 图片 OCR 成功但识别不到文字 | index.json 中该条目的 text 写 "未识别到文字" |`）替换为：

```markdown
| 图片 OCR 成功但识别不到文字 | 移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引，日志 warning |
```

并在该行之后（第 360 行「单张新增图片 OCR 调用异常」之前）插入一行：

```markdown
| 新增图片 OCR 文本去重键命中已有条目或另一新增图片 | `/add` 用新图替换旧图（删旧图文件、复用旧 ID）；`/refresh` 保留已有/靠前者，删除被判定为重复的新图文件 |
```

- [ ] **Step 6: `docs/PRD.md` §6 项目结构 — 新增 `meme_no_text/`**

在 `docs/PRD.md` §6 项目结构树（约第 397 行 `├── memes/` 之后）插入：

```text
├── meme_no_text/             # OCR 无文字图片存放目录（不进索引，Docker 卷挂载）
```

- [ ] **Step 7: `CONTEXT.md` 术语表 — 新增术语并修订同步策略**

在 `CONTEXT.md` 核心概念表（第 13 行「按文件名同步的增量刷新」术语的 Definition 末尾）追加一句：

```markdown
| **按文件名同步的增量刷新** | 启动和 `/refresh` 时使用的 v1.0 同步策略：对新增图片先按格式执行图片无损压缩，再追加索引记录；删除已不存在图片的索引记录；文件名仍存在的图片不重新 OCR，不检测同名覆盖；删除记录后保持其他已有 id 稳定，允许临时编号空洞；新增图片按文件名升序处理，并优先复用最小空洞 id；新增图片 OCR 后按「去除所有空白字符的去重键」去重，与已有条目或其他新图同键时保留已有/靠前者、删除重复新图；OCR 无文字的新图移至 `meme_no_text/` 不进索引 |
```

在核心概念表末尾（第 22 行「群聊消息」之后）新增两行：

```markdown
| **去重键** | OCR 文本去除所有空白字符（含半角/全角空格、制表符、换行）后的纯文本；用于在 `/add` 和 `sync_with_filesystem` 新增阶段判定「是否完全相同的图片」，实时计算不落盘 |
| **无文字目录** | `memes/` 同级的 `meme_no_text/` 目录；OCR 去除所有空白后为空的图片在此场景下不进入索引，被移动到该目录并由日志 warning 提示，本项目不处理该类表情包 |
```

- [ ] **Step 8: `docs/api/API.md` §1.4 — `SyncResult` 新增字段**

把 `docs/api/API.md` 第 98-103 行 `SyncResult` 代码块替换为：

```python
@dataclass
class SyncResult:
    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)
```

并把其下字段表（第 105-110 行）替换为：

```markdown
| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `added` | `int` | `0` | 本次同步新增的图片数量 |
| `deleted` | `int` | `0` | 本次同步删除的图片数量（memes/ 已不存在的旧图） |
| `deduped` | `int` | `0` | 新图因去重键命中已有条目/其他新图而被删除的数量 |
| `no_text_moved` | `int` | `0` | OCR 无文字被移到 meme_no_text/ 的数量 |
| `failed` | `list[str]` | `[]` | 处理失败的文件名列表（含新增失败与 embedding 重建失败） |
```

并把该表下方的说明行（第 111 行）替换为：

```markdown
是 `sync_with_filesystem()` 的返回类型。重建 embedding 的数量不单独计入字段，仅在日志中输出。去重与无文字移动不计入 `added`/`deleted`，各自独立计数。
```

- [ ] **Step 9: `docs/api/API.md` §1.5 — 新增 `AddResult` 章节 + 工具函数 + 更新 `add_entry`/`__init__`/`sync_with_filesystem`**

在 `docs/api/API.md` §1.4 `SyncResult` 章节之后、§1.5 `IndexManager 类` 之前，插入 `AddResult` 章节：

```markdown
#### `AddResult`

```python
@dataclass
class AddResult:
    entry_id: str | None
    reason: str
    replaced_filename: str | None = None
    moved_to: str | None = None
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `entry_id` | `str \| None` | 必填 | 分配/复用的索引 ID；无文字移图场景为 `None` |
| `reason` | `str` | 必填 | 结果类别：`"added"`（正常新增）、`"replaced"`（去重覆盖）、`"no_text"`（无文字移图） |
| `replaced_filename` | `str \| None` | `None` | `reason="replaced"` 时为被删旧图文件名，否则 `None` |
| `moved_to` | `str \| None` | `None` | `reason="no_text"` 时为移入 meme_no_text/ 的完整路径，否则 `None` |

是 `add_entry()` 的返回类型。
```

在 §1.1 模块级函数区（`compute_text_hash` 之后，约第 45 行）新增两个工具函数章节：

```markdown
#### `dedup_key(text: str) -> str`

计算 OCR 文本的去重键。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | 原始 OCR 文本 |
| **返回** | `str` | 去除所有空白字符（含半角/全角空格、制表符、换行）后的文本，可能为空字符串 |

比 `normalize_text` 更严格：`normalize_text` 保留单词间单空格，`dedup_key` 完全去除空格，用于判定「是否完全相同的图片」。实时计算，不落盘。

```python
dedup_key("加班 好累")   # → "加班好累"
dedup_key("加班好累")    # → "加班好累"  # 与上行同键
dedup_key("   ")         # → ""
```

---

#### `is_blank_text(text: str) -> bool`

判断 OCR 文本是否为「无文字」。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | OCR 文本 |
| **返回** | `bool` | `True` 表示去所有空白后为空（无文字，需移到 `meme_no_text/` 不进索引） |

等价于 `dedup_key(text) == ""`。
```

更新 §1.5 `__init__` 参数表（第 132-142 行），在 `sync_concurrency` 行之后追加：

```markdown
| `no_text_dir` | `str \| None` | `None` | 无文字图存放目录；`None` 时取 `memes_dir` 同级的 `meme_no_text/`（即 `Path(memes_dir).parent / "meme_no_text"`）。插件层无需显式传入 |
```

更新 §1.5 `add_entry` 章节（第 245-258 行），把返回类型与说明替换为：

```markdown
#### `add_entry(filename: str, text: str, embedding: list[float]) -> AddResult`

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 识别文本 |
| `embedding` | `list[float]` | embedding 向量 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `AddResult` | 描述本次结果的 `AddResult`：`reason="added"` 正常新增、`reason="replaced"` 去重覆盖（复用旧 ID、删旧图）、`reason="no_text"` 无文字移图（不进索引） |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

三分支处理：
1. 无文字（`is_blank_text(text)` 为真）→ 调用 `_move_to_no_text(filename)` 移图到 `meme_no_text/`，不写索引，返回 `AddResult(entry_id=None, reason="no_text", moved_to=...)`。
2. 去重键命中已有条目（`_find_entry_by_dedup_key(dedup_key(text))` 非 `None`）→ 删除旧图文件（`missing_ok`），复用旧 ID 覆盖 `_entries[old_id]` 与 `_embeddings[old_id]`（用新 `text_hash` 与新 `embedding`），原子写入两文件，返回 `AddResult(entry_id=old_id, reason="replaced", replaced_filename=旧文件名)`。
3. 正常新增 → `_find_next_id()` 分配 ID，写入 `_entries`/`_embeddings`，原子写入两文件，返回 `AddResult(entry_id, reason="added")`。
```

更新 §1.5 `sync_with_filesystem` 阶段说明（第 319 行第 5 条），替换为：

```markdown
5. **新增阶段**：新增图片**并行处理**——对按文件名升序排序后的新增文件，通过 `asyncio.gather` 同时发布多个 task；每个 task 内部串行执行 `OcrProvider.ocr()` → `EmbeddingProvider.embed()`，task 之间受 `_sync_semaphore` 约束并发执行。结果收集后**按文件名升序串行三分类**（基于 `winner_keys` 赢家集合增量判定）：(a) 无文字（`is_blank_text`）→ `_move_to_no_text` 移图、`no_text_moved++`；(b) 去重键 `dedup_key(text)` 命中 `winner_keys`（已有条目或本轮更靠前的保留新图）→ 删新图文件、`deduped++`（现有条目/靠前图赢）；(c) 正常新增 → 分配 ID 写入、该键加入 `winner_keys`、`added++`。`winner_keys` 初始为已有条目的去重键集合。
```

并把 §1.5 `sync_with_filesystem` 的返回说明（第 330 行）替换为：

```markdown
**返回**：`SyncResult(added, deleted, deduped, no_text_moved, failed)`。重建数量仅在日志中输出（`新增=X, 删除=Y, 去重=D, 无文字移走=T, 重建=Z, 失败=W`），不计入 `SyncResult`。去重与无文字移动不计入 `added`/`deleted`，各自独立计数。
```

- [ ] **Step 10: `docs/process.md` — 追加 index_manager 条目**

把 `docs/process.md` 第 5 行（`index_manager.py` 条目）末尾的「`_embeddings_stale` 标志由重建阶段消费清除）」之后追加一句：

```markdown
- [x] `bot/engine/index_manager.py` — 索引增删改查模块（ujson 解析、原子写入、空洞 ID 复用、text_hash 一致性校验、文件系统同步、asyncio 锁管理；`sync_with_filesystem` 已改造为三阶段并行：①删除已不存在的图片 ②重建阶段——对 text_hash 不一致或 embedding 缺失的已有条目用当前 text 并行重建 embedding（统一覆盖「用户改 text」增量重建与「embeddings.json 损坏」全量重建），不重新 OCR ③新增图片并行 OCR+embed 后按文件名升序串行三分类——无文字移至 `meme_no_text/`、去重键命中已有条目或靠前新图时删除重复新图、正常新增；去重基于「去除所有空白字符的去重键」实时计算不落盘，`winner_keys` 赢家集合增量判定；并发上限由 `sync_concurrency`/`SYNC_CONCURRENCY` 控制，默认 5；`add_entry` 返回 `AddResult`，内联无文字移图与去重覆盖（复用旧 ID、删旧图）；`SyncResult` 新增 `deduped`/`no_text_moved` 字段；`_embeddings_stale` 标志由重建阶段消费清除）
```

- [ ] **Step 11: `README.md` — 目录结构与部署说明补充**

在 `README.md` 项目结构树（第 166 行 `├── memes/` 之后）插入：

```text
├── meme_no_text/            # OCR 无文字图片（不进索引，Docker 卷挂载）
```

并在 `README.md` `/add` 小节（第 51 行「不支持的扩展名不会作为表情包处理。」之后）插入一段：

```markdown

`/add` 在写入索引前会做两项检查：若新图 OCR 文本去除所有空白后与已有表情包完全相同，则用新图替换旧图（删除旧图片文件、复用旧索引 ID）；若 OCR 结果去除所有空白后为空（无文字图片），则将该图移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引并提示「未识别到文字」。
```

并在 `README.md` `/refresh` 小节（第 58 行之后）插入：

```markdown

`/refresh` 扫描时同样会对新增图片做去重与无文字排除：OCR 文本去重键命中已有条目或其他新图的新增图片会被删除（保留已有或文件名靠前者），无文字图片移至 `meme_no_text/`；完成回复包含新增、删除、去重、无文字移走、失败五项数量。
```

- [ ] **Step 12: `CLAUDE.md` 索引格式要点 — 补充去重键与无文字说明**

在 `CLAUDE.md`「索引格式要点」小节（`text_hash` 规则段落之后、`index.json` 损坏处理段落之前，约第 60 行处）插入一段：

```markdown
去重键规则：以「去除所有空白字符（含半角/全角空格、制表符、换行）后的 OCR 文本」作为去重键，实时计算不落盘。`add_entry` 与 `sync_with_filesystem` 新增阶段据此判定完全相同图片：`/add` 新图命中已有条目时用新图替换旧图（复用旧 ID、删旧图文件）；`/refresh` 保留已有或文件名靠前者、删除重复新图。OCR 去所有空白后为空的无文字图片不进入索引，移动到 `memes/` 同级的 `meme_no_text/` 目录（Docker 卷 `./meme_no_text:/app/meme_no_text` 挂载）。
```

- [ ] **Step 13: 文档编译/语法自查**

Run: `uv run python -m compileall bot` （确认未误改代码）

Expected: 无输出。

人工目检各文档 Markdown 表格与代码块格式完整。

- [ ] **Step 14: 用户审核检查点 — 请审阅所有文档 diff 并提交**

暂停。改动摘要：7 个文件文档同步——`docker-compose.yml`（新增卷）、`docs/PRD.md`（§3.3/§3.5/§5/§6）、`CONTEXT.md`（新增术语 + 修订同步策略）、`docs/api/API.md`（`AddResult`/`SyncResult`/工具函数/`add_entry`/`__init__`/`sync_with_filesystem`）、`docs/process.md`、`README.md`、`CLAUDE.md`。

请用户审阅 diff 后自行执行：
```bash
git add docker-compose.yml docs/PRD.md CONTEXT.md docs/api/API.md docs/process.md README.md CLAUDE.md
git commit -m "docs: 同步 OCR 去重与无文字排除的设计变更"
```

---

## 实现完成标准

全部 Task 1-8 完成且：
1. `uv run pytest -v` 全绿（含所有新增与改写测试）。
2. `uv run python -m compileall bot` 无输出。
3. `docs/api/API.md` 与 `index_manager.py` 实际接口一致（`AddResult`、`SyncResult`、`add_entry`、`sync_with_filesystem`、`dedup_key`、`is_blank_text`、`__init__` 的 `no_text_dir`）。
4. `docker-compose.yml` 含 `./meme_no_text:/app/meme_no_text` 卷映射。
5. PRD/CONTEXT/README/CLAUDE.md 中「OCR 无文字」「去重」表述与新行为一致，不再出现旧的"text 写'未识别到文字'"占位描述。
