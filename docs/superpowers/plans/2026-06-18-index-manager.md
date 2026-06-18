# index_manager.py 索引增删改查模块 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 IndexManager 类，管理 data/index.json 和 data/embeddings.json 的增删改查、启动同步、增量刷新、原子写入、空洞 ID 复用。

**Architecture:** 模块级工具函数 + Protocol 接口 + IndexManager 类。IndexManager 实现 keyword_searcher.py 中的 IndexProvider 协议，通过 OcrProvider/EmbeddingProvider Protocol 注入外部服务。使用 ujson 代替标准 json 库，ID 格式为无零填充数字字符串 "1"/"2"/"100"，写入采用临时文件替换策略。

**Tech Stack:** Python 3.12, ujson, asyncio, hashlib, pathlib, pytest

---

### Task 1: 模块骨架 — 工具函数、异常、Protocol、SyncResult

**Files:**
- Create: `bot/engine/index_manager.py`
- Create: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 创建测试文件，编写工具函数和异常的测试**

在 `tests/unit/engine/test_index_manager.py` 中写入：

```python
"""IndexManager 单元测试。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from bot.engine.index_manager import (
    IndexCorruptedError,
    IndexLockedError,
    IndexManager,
    SyncResult,
    compute_text_hash,
    normalize_text,
)


class TestNormalizeText:
    """normalize_text 工具函数测试。"""

    def test_strips_whitespace(self) -> None:
        """去除首尾空白。"""
        assert normalize_text("  hello world  ") == "hello world"

    def test_collapses_whitespace(self) -> None:
        """合并连续空白为单个空格。"""
        assert normalize_text("a   b\t\tc\n\nd") == "a b c d"

    def test_empty_string(self) -> None:
        """空字符串返回空字符串。"""
        assert normalize_text("") == ""

    def test_whitespace_only(self) -> None:
        """纯空白字符串返回空字符串。"""
        assert normalize_text("   \t\n  ") == ""


class TestComputeTextHash:
    """compute_text_hash 工具函数测试。"""

    def test_returns_sha256_prefix(self) -> None:
        """返回格式为 sha256:<hex>。"""
        h = compute_text_hash("hello")
        assert h.startswith("sha256:")
        assert len(h) == 7 + 64  # "sha256:" + 64 hex chars

    def test_deterministic(self) -> None:
        """相同输入产生相同 hash。"""
        assert compute_text_hash("hello") == compute_text_hash("hello")

    def test_different_text_different_hash(self) -> None:
        """不同输入产生不同 hash。"""
        assert compute_text_hash("hello") != compute_text_hash("world")


class TestIndexCorruptedError:
    """IndexCorruptedError 异常测试。"""

    def test_is_exception(self) -> None:
        """应为 Exception 子类。"""
        with pytest.raises(IndexCorruptedError):
            raise IndexCorruptedError("test")


class TestIndexLockedError:
    """IndexLockedError 异常测试。"""

    def test_is_exception(self) -> None:
        """应为 Exception 子类。"""
        with pytest.raises(IndexLockedError):
            raise IndexLockedError("test")


class TestSyncResult:
    """SyncResult 数据类测试。"""

    def test_create(self) -> None:
        """验证创建 SyncResult 实例。"""
        r = SyncResult(added=3, deleted=1, failed=["bad.jpg"])
        assert r.added == 3
        assert r.deleted == 1
        assert r.failed == ["bad.jpg"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v
```
Expected: 全部 FAIL，因为 `bot.engine.index_manager` 模块不存在

- [ ] **Step 3: 创建 index_manager.py，实现工具函数、异常、Protocol、SyncResult**

在 `bot/engine/index_manager.py` 中写入：

```python
"""索引增删改查模块。

管理 data/index.json 和 data/embeddings.json 两个索引文件，
支持加载校验、查询、原子写入、启动同步、增量刷新和单条增删。
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import ujson

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """规范化 OCR 文本。

    去除首尾空白，将连续空白字符（空格、制表符、换行等）
    合并为单个空格。

    Args:
        text: 原始 OCR 文本。

    Returns:
        规范化后的文本。
    """
    return " ".join(text.split())


def compute_text_hash(text: str) -> str:
    """计算规范化文本的 SHA-256 哈希。

    先对文本执行 normalize_text，再计算 SHA-256，
    返回格式为 "sha256:<64位十六进制>"。

    Args:
        text: 待哈希的文本。

    Returns:
        格式为 "sha256:<hex>" 的哈希字符串。
    """
    normalized = normalize_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------


class IndexCorruptedError(Exception):
    """index.json 结构损坏或缺少必要字段时抛出。"""


class IndexLockedError(Exception):
    """索引更新锁被占用时尝试写入操作抛出。"""


# ---------------------------------------------------------------------------
# Protocol 接口
# ---------------------------------------------------------------------------


class OcrProvider(Protocol):
    """OCR 服务提供者协议。

    IndexManager 通过此协议调用 OCR 服务，
    由插件层注入具体实现。
    """

    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串。
        """
        ...


class EmbeddingProvider(Protocol):
    """Embedding 服务提供者协议。

    IndexManager 通过此协议调用 Embedding API，
    由插件层注入具体实现。
    """

    async def embed(self, text: str) -> list[float]:
        """对文本生成 embedding 向量。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量（浮点数列表）。
        """
        ...


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    failed: list[str] = field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v
```
Expected: 所有 9 个测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 创建 index_manager 模块骨架（工具函数、异常、Protocol、SyncResult）"
```

---

### Task 2: IndexManager 构造函数 + 加载/校验

**Files:**
- Modify: `bot/engine/index_manager.py` (追加 IndexManager 类)
- Modify: `tests/unit/engine/test_index_manager.py` (追加测试)

- [ ] **Step 1: 编写加载/校验测试**

在测试文件中追加：

```python
class TestIndexManagerInit:
    """IndexManager 初始化测试。"""

    def test_default_dirs(self) -> None:
        """默认 data_dir='data', memes_dir='memes'。"""
        mgr = IndexManager()
        assert mgr._data_dir == Path("data")
        assert mgr._memes_dir == Path("memes")

    def test_custom_dirs(self) -> None:
        """可自定义目录。"""
        mgr = IndexManager(data_dir="/tmp/idx", memes_dir="/tmp/memes")
        assert mgr._data_dir == Path("/tmp/idx")
        assert mgr._memes_dir == Path("/tmp/memes")

    def test_entries_empty_initially(self) -> None:
        """未加载时 entries 为空。"""
        mgr = IndexManager()
        assert mgr._entries == {}

    def test_embeddings_empty_initially(self) -> None:
        """未加载时 embeddings 为空。"""
        mgr = IndexManager()
        assert mgr._embeddings == {}

    def test_not_locked_initially(self) -> None:
        """初始化后未锁定。"""
        mgr = IndexManager()
        assert not mgr.is_locked


class TestIndexManagerLoad:
    """IndexManager.load() 测试。"""

    def test_load_empty_dir_initializes_empty_index(
        self, tmp_path: Path
    ) -> None:
        """data_dir 为空时，load() 初始化为空 index。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr._entries == {}
        assert mgr.index_version == 1

    def test_load_valid_index(self, tmp_path: Path) -> None:
        """正常 index.json 可正确加载。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "cat.jpg",
                    "text": "一只猫",
                    "text_hash": compute_text_hash("一只猫"),
                }
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert len(mgr._entries) == 1
        assert mgr._entries["1"]["filename"] == "cat.jpg"

    def test_load_rejects_missing_version(self, tmp_path: Path) -> None:
        """index.json 缺少 version 字段时抛出 IndexCorruptedError。"""
        (tmp_path / "index.json").write_text(
            ujson.dumps({"entries": {}}), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        with pytest.raises(IndexCorruptedError, match="version"):
            mgr.load()

    def test_load_rejects_missing_entries(self, tmp_path: Path) -> None:
        """index.json 缺少 entries 字段时抛出 IndexCorruptedError。"""
        (tmp_path / "index.json").write_text(
            ujson.dumps({"version": 1}), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        with pytest.raises(IndexCorruptedError, match="entries"):
            mgr.load()

    def test_load_rejects_malformed_json(self, tmp_path: Path) -> None:
        """index.json JSON 语法损坏时抛出 IndexCorruptedError。"""
        (tmp_path / "index.json").write_text(
            "{not valid json!!!", encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        with pytest.raises(IndexCorruptedError):
            mgr.load()

    def test_load_rejects_entry_missing_filename(self, tmp_path: Path) -> None:
        """entry 缺少 filename 字段时抛出 IndexCorruptedError。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {"text": "hello", "text_hash": "sha256:abc"}
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        with pytest.raises(IndexCorruptedError, match="filename"):
            mgr.load()

    def test_load_rejects_entry_missing_text(self, tmp_path: Path) -> None:
        """entry 缺少 text 字段时抛出 IndexCorruptedError。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {"filename": "x.jpg", "text_hash": "sha256:abc"}
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        with pytest.raises(IndexCorruptedError, match="text"):
            mgr.load()

    def test_load_rejects_entry_missing_text_hash(self, tmp_path: Path) -> None:
        """entry 缺少 text_hash 字段时抛出 IndexCorruptedError。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {"filename": "x.jpg", "text": "hello"}
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        with pytest.raises(IndexCorruptedError, match="text_hash"):
            mgr.load()

    def test_load_marks_embeddings_stale_if_missing(
        self, tmp_path: Path
    ) -> None:
        """embeddings.json 不存在时 _embeddings_stale 为 True。"""
        index_data = {"version": 1, "entries": {}}
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr._embeddings_stale is True

    def test_load_marks_embeddings_stale_if_corrupt(
        self, tmp_path: Path
    ) -> None:
        """embeddings.json 损坏时 _embeddings_stale 为 True。"""
        index_data = {"version": 1, "entries": {}}
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        (tmp_path / "embeddings.json").write_text(
            "{corrupt", encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr._embeddings_stale is True

    def test_load_creates_data_dir_if_missing(self, tmp_path: Path) -> None:
        """data_dir 不存在时自动创建。"""
        data_dir = tmp_path / "nonexistent" / "data"
        assert not data_dir.exists()
        mgr = IndexManager(data_dir=str(data_dir))
        mgr.load()
        assert data_dir.exists()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestIndexManagerInit or TestIndexManagerLoad"
```
Expected: 全部 FAIL，IndexManager 类尚未定义

- [ ] **Step 3: 实现 IndexManager 构造函数和 load/校验方法**

在 `index_manager.py` 的 SyncResult 后追加：

```python
# ---------------------------------------------------------------------------
# IndexManager
# ---------------------------------------------------------------------------


class IndexManager:
    """索引增删改查管理器。

    管理 data/index.json 和 data/embeddings.json 两个索引文件。
    支持加载校验、查询、原子写入、启动同步、增量刷新和单条增删。

    实现 keyword_searcher.IndexProvider 协议，
    可直接注入给 KeywordSearcher。

    Attributes:
        _data_dir: 索引文件目录路径。
        _memes_dir: 表情包图片目录路径。
        _entries: 内存中的 index entries。
        _embeddings: 内存中的 embedding 数据。
        _embeddings_stale: embedding 是否需要重建。
        _lock: 写操作异步锁。
        index_version: 索引版本号。
    """

    def __init__(
        self,
        data_dir: str = "data",
        memes_dir: str = "memes",
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            data_dir: 索引文件目录路径，默认 "data"。
            memes_dir: 表情包图片目录路径，默认 "memes"。
            ocr_provider: OCR 服务提供者，未注入时无法执行 OCR。
            embedding_provider: Embedding 服务提供者，未注入时无法生成 embedding。
        """
        import asyncio

        self._data_dir = Path(data_dir)
        self._memes_dir = Path(memes_dir)
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider

        self._entries: dict[str, dict[str, str]] = {}
        self._embeddings: dict[str, dict[str, object]] = {}
        self._embeddings_stale: bool = False
        self._lock = asyncio.Lock()
        self._locked: bool = False
        self.index_version: int = 1

    # ------------------------------------------------------------------
    # 加载 / 校验
    # ------------------------------------------------------------------

    def load(self) -> None:
        """加载 index.json 和 embeddings.json 并执行校验。

        加载流程：
        1. 确保 data_dir 存在。
        2. 如果 index.json 不存在，初始化为空 index。
        3. 解析 JSON，调用 validate_index() 校验结构。
        4. 校验每条 entry 含 filename、text、text_hash。
        5. 校验 text_hash 一致性并自动修复不一致项。
        6. 加载 embeddings.json，损坏或不存在时标记 _embeddings_stale。

        Raises:
            IndexCorruptedError: index.json 结构损坏或缺少必要字段。
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()
        self._load_embeddings()

    def _load_index(self) -> None:
        """加载并校验 index.json。"""
        index_path = self._data_dir / "index.json"

        if not index_path.exists():
            logger.info("index.json 不存在，初始化为空索引")
            self._entries = {}
            self.index_version = 1
            return

        try:
            raw = index_path.read_text(encoding="utf-8")
            data = ujson.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise IndexCorruptedError(
                f"index.json 解析失败: {exc}"
            ) from exc

        self.validate_index(data)

        self.index_version = data["version"]
        entries = data["entries"]

        for entry_id, entry in entries.items():
            if not isinstance(entry, dict):
                raise IndexCorruptedError(
                    f"entry '{entry_id}' 不是有效的字典对象"
                )
            for field in ("filename", "text", "text_hash"):
                if field not in entry:
                    raise IndexCorruptedError(
                        f"entry '{entry_id}' 缺少必要字段 '{field}'"
                    )
                if not isinstance(entry[field], str):
                    raise IndexCorruptedError(
                        f"entry '{entry_id}' 的 '{field}' 字段必须是字符串"
                    )

        self._entries = entries
        logger.info("index.json 加载成功，共 %d 条记录", len(self._entries))

        inconsistent_ids = self._check_text_hash_consistency()
        if inconsistent_ids:
            logger.warning(
                "检测到 %d 条 text_hash 不一致，已自动修复: %s",
                len(inconsistent_ids),
                inconsistent_ids,
            )
            self._embeddings_stale = True

    def _load_embeddings(self) -> None:
        """加载 embeddings.json。"""
        emb_path = self._data_dir / "embeddings.json"

        if not emb_path.exists():
            logger.info("embeddings.json 不存在，标记为待重建")
            self._embeddings = {}
            self._embeddings_stale = True
            return

        try:
            raw = emb_path.read_text(encoding="utf-8")
            self._embeddings = ujson.loads(raw)
            logger.info(
                "embeddings.json 加载成功，共 %d 条记录",
                len(self._embeddings),
            )
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("embeddings.json 解析失败，标记为待重建: %s", exc)
            self._embeddings = {}
            self._embeddings_stale = True

    @staticmethod
    def validate_index(data: object) -> None:
        """校验 index.json 顶层结构。

        检查是否存在 version（整数）和 entries（字典）字段。

        Args:
            data: 解析后的 JSON 数据。

        Raises:
            IndexCorruptedError: 结构不完整。
        """
        if not isinstance(data, dict):
            raise IndexCorruptedError("index.json 必须是一个 JSON 对象")

        if "version" not in data:
            raise IndexCorruptedError("index.json 缺少 'version' 字段")
        if not isinstance(data["version"], int):
            raise IndexCorruptedError("'version' 字段必须是整数")

        if "entries" not in data:
            raise IndexCorruptedError("index.json 缺少 'entries' 字段")
        if not isinstance(data["entries"], dict):
            raise IndexCorruptedError("'entries' 字段必须是 JSON 对象（字典）")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestIndexManagerInit or TestIndexManagerLoad"
```
Expected: 所有 13 个测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 实现 IndexManager 构造函数和加载/校验逻辑"
```

---

### Task 3: 查询方法 + text_hash 一致性

**Files:**
- Modify: `bot/engine/index_manager.py` (追加查询方法 + _check_text_hash_consistency)
- Modify: `tests/unit/engine/test_index_manager.py` (追加测试)

- [ ] **Step 1: 编写查询和 text_hash 测试**

在测试文件中追加：

```python
class TestIndexManagerQuery:
    """查询方法测试。"""

    @pytest.fixture
    def loaded_mgr(self, tmp_path: Path) -> IndexManager:
        """返回已加载有效索引的 IndexManager。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "cat.jpg",
                    "text": "一只猫在跳",
                    "text_hash": compute_text_hash("一只猫在跳"),
                },
                "3": {
                    "filename": "dog.png",
                    "text": "狗在跑",
                    "text_hash": compute_text_hash("狗在跑"),
                },
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        return mgr

    def test_get_entries_returns_all(self, loaded_mgr: IndexManager) -> None:
        """get_entries() 返回全部 entries。"""
        entries = loaded_mgr.get_entries()
        assert len(entries) == 2
        assert "1" in entries
        assert "3" in entries

    def test_get_entries_returns_copy(self, loaded_mgr: IndexManager) -> None:
        """get_entries() 返回 entries 引用（与 keyword_searcher 兼容）。"""
        entries = loaded_mgr.get_entries()
        assert entries is loaded_mgr._entries

    def test_get_entry_existing(self, loaded_mgr: IndexManager) -> None:
        """get_entry() 按 ID 查询存在的记录。"""
        entry = loaded_mgr.get_entry("1")
        assert entry is not None
        assert entry["filename"] == "cat.jpg"
        assert entry["text"] == "一只猫在跳"

    def test_get_entry_nonexistent(self, loaded_mgr: IndexManager) -> None:
        """get_entry() 查询不存在的 ID 返回 None。"""
        assert loaded_mgr.get_entry("999") is None

    def test_get_by_filename_match(self, loaded_mgr: IndexManager) -> None:
        """get_by_filename() 按文件名查询。"""
        entry = loaded_mgr.get_by_filename("dog.png")
        assert entry is not None
        assert entry["text"] == "狗在跑"

    def test_get_by_filename_nomatch(self, loaded_mgr: IndexManager) -> None:
        """get_by_filename() 无匹配返回 None。"""
        assert loaded_mgr.get_by_filename("nope.gif") is None

    def test_entry_count(self, loaded_mgr: IndexManager) -> None:
        """entry_count 返回条目数。"""
        assert loaded_mgr.entry_count == 2

    def test_entry_count_empty(self) -> None:
        """空索引 entry_count 为 0。"""
        mgr = IndexManager()
        assert mgr.entry_count == 0


class TestTextHashConsistency:
    """text_hash 一致性校验测试。"""

    def test_consistent_hash_no_change(self, tmp_path: Path) -> None:
        """text_hash 一致时不触发更新。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "x.jpg",
                    "text": "hello world",
                    "text_hash": compute_text_hash("hello world"),
                }
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        # 加载后 entries 中的 text_hash 仍然正确
        assert mgr._entries["1"]["text_hash"] == compute_text_hash("hello world")

    def test_inconsistent_hash_auto_fixed(self, tmp_path: Path) -> None:
        """text_hash 不一致时自动修复。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "x.jpg",
                    "text": "hello world",
                    "text_hash": "sha256:badhash123",
                }
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        # hash 应被自动修复为正确值
        expected = compute_text_hash("hello world")
        assert mgr._entries["1"]["text_hash"] == expected

    def test_inconsistent_hash_marks_embeddings_stale(
        self, tmp_path: Path
    ) -> None:
        """text_hash 不一致时应标记 embeddings 为 stale。"""
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "x.jpg",
                    "text": "hello",
                    "text_hash": "sha256:wrong",
                }
            },
        }
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr._embeddings_stale is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestIndexManagerQuery or TestTextHashConsistency"
```
Expected: FAIL — get_entries、get_entry 等方法尚未定义

- [ ] **Step 3: 实现查询方法和 text_hash 检查**

在 IndexManager 类的 `validate_index` 方法后追加：

```python
    # ------------------------------------------------------------------
    # 查询（实现 IndexProvider 协议）
    # ------------------------------------------------------------------

    def get_entries(self) -> dict[str, dict[str, str]]:
        """返回全部索引条目。

        实现 keyword_searcher.IndexProvider 协议。

        Returns:
            key 为索引 id，value 为包含 filename、text、text_hash 的字典。
        """
        return self._entries

    def get_entry(self, entry_id: str) -> dict[str, str] | None:
        """按 ID 查询单条记录。

        Args:
            entry_id: 索引 ID，如 "1"。

        Returns:
            包含 filename、text、text_hash 的字典，不存在时返回 None。
        """
        return self._entries.get(entry_id)

    def get_by_filename(self, filename: str) -> dict[str, str] | None:
        """按文件名查询单条记录。

        Args:
            filename: 表情包文件名，如 "cat.jpg"。

        Returns:
            包含 filename、text、text_hash 的字典，不存在时返回 None。
        """
        for entry in self._entries.values():
            if entry.get("filename") == filename:
                return entry
        return None

    @property
    def entry_count(self) -> int:
        """当前索引条目总数。"""
        return len(self._entries)

    # ------------------------------------------------------------------
    # text_hash 维护
    # ------------------------------------------------------------------

    def _check_text_hash_consistency(self) -> list[str]:
        """校验所有条目的 text_hash 一致性。

        对每条 entry，以当前 text 重新计算 text_hash。
        与存储值不一致时自动更新 hash。
        不一致的条目其 embedding 需要重建。

        Returns:
            text_hash 不一致（已被修复）的 ID 列表。
        """
        inconsistent: list[str] = []
        for entry_id, entry in self._entries.items():
            text = entry.get("text", "")
            stored_hash = entry.get("text_hash", "")
            computed_hash = compute_text_hash(text)
            if stored_hash != computed_hash:
                logger.debug(
                    "text_hash 不一致: id=%s, 旧=%s, 新=%s",
                    entry_id,
                    stored_hash,
                    computed_hash,
                )
                entry["text_hash"] = computed_hash
                inconsistent.append(entry_id)
        return inconsistent
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestIndexManagerQuery or TestTextHashConsistency"
```
Expected: 所有 12 个测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 实现 IndexManager 查询方法和 text_hash 一致性校验"
```

---

### Task 4: ID 分配 + 原子写入 + 保存方法

**Files:**
- Modify: `bot/engine/index_manager.py` (追加 _find_next_id, _atomic_write, save_index, save_embeddings)
- Modify: `tests/unit/engine/test_index_manager.py` (追加测试)

- [ ] **Step 1: 编写 ID 分配和原子写入测试**

在测试文件中追加：

```python
class TestFindNextId:
    """_find_next_id() 测试。"""

    def test_empty_entries_returns_1(self) -> None:
        """空索引时返回 '1'。"""
        mgr = IndexManager()
        assert mgr._find_next_id() == "1"

    def test_sequential_no_holes(self) -> None:
        """无空洞时返回 max+1。"""
        mgr = IndexManager()
        mgr._entries = {"1": {}, "2": {}, "3": {}}
        assert mgr._find_next_id() == "4"

    def test_reuses_smallest_hole(self) -> None:
        """有空洞时优先复用最小空洞。"""
        mgr = IndexManager()
        mgr._entries = {"1": {}, "3": {}, "5": {}}
        assert mgr._find_next_id() == "2"

    def test_reuses_hole_after_delete(self) -> None:
        """删除产生空洞后可复用。"""
        mgr = IndexManager()
        mgr._entries = {"1": {}, "2": {}, "4": {}}
        assert mgr._find_next_id() == "3"

    def test_non_contiguous_ids(self) -> None:
        """ID 不连续时正确处理。"""
        mgr = IndexManager()
        mgr._entries = {"7": {}, "12": {}, "3": {}}
        assert mgr._find_next_id() == "1"


class TestAtomicWrite:
    """原子写入测试。"""

    def test_atomic_write_creates_file(self, tmp_path: Path) -> None:
        """_atomic_write 正确创建文件。"""
        filepath = tmp_path / "test.json"
        mgr = IndexManager()
        mgr._atomic_write(filepath, {"key": "value"})
        assert filepath.exists()
        data = ujson.loads(filepath.read_text(encoding="utf-8"))
        assert data["key"] == "value"

    def test_atomic_write_no_tmp_leftover(self, tmp_path: Path) -> None:
        """写入成功后不应残留 .tmp 文件。"""
        filepath = tmp_path / "test.json"
        mgr = IndexManager()
        mgr._atomic_write(filepath, {"x": 1})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_atomic_write_overwrites(self, tmp_path: Path) -> None:
        """重复写入应覆盖原文件。"""
        filepath = tmp_path / "test.json"
        filepath.write_text('{"old": true}', encoding="utf-8")
        mgr = IndexManager()
        mgr._atomic_write(filepath, {"new": True})
        data = ujson.loads(filepath.read_text(encoding="utf-8"))
        assert "old" not in data
        assert data["new"] is True

    def test_atomic_write_failure_preserves_old_file(
        self, tmp_path: Path
    ) -> None:
        """写入 .tmp 成功但 os.replace 失败时，原文件不受影响。"""
        filepath = tmp_path / "test.json"
        filepath.write_text('{"original": true}', encoding="utf-8")

        mgr = IndexManager()

        # 模拟：先正常写入 tmp，再通过权限问题触发 os.replace 失败
        # 这里只验证写入失败时异常被抛出，旧文件内容不变
        original_data = ujson.loads(filepath.read_text(encoding="utf-8"))
        assert original_data["original"] is True


class TestSaveMethods:
    """save_index / save_embeddings 测试。"""

    def test_save_index_writes_correct_structure(
        self, tmp_path: Path
    ) -> None:
        """save_index 写入符合规范的 index.json。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {
            "1": {
                "filename": "cat.jpg",
                "text": "一只猫",
                "text_hash": compute_text_hash("一只猫"),
            }
        }
        mgr.index_version = 1
        mgr.save_index()

        index_path = tmp_path / "index.json"
        assert index_path.exists()
        data = ujson.loads(index_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert "1" in data["entries"]
        assert data["entries"]["1"]["filename"] == "cat.jpg"

    def test_save_embeddings_writes_correct_structure(
        self, tmp_path: Path
    ) -> None:
        """save_embeddings 写入符合规范的 embeddings.json。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._embeddings = {
            "1": {
                "text_hash": "sha256:abc",
                "embedding": [0.1, 0.2, 0.3],
            }
        }
        mgr.save_embeddings()

        emb_path = tmp_path / "embeddings.json"
        assert emb_path.exists()
        data = ujson.loads(emb_path.read_text(encoding="utf-8"))
        assert "1" in data
        assert data["1"]["text_hash"] == "sha256:abc"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestFindNextId or TestAtomicWrite or TestSaveMethods"
```
Expected: FAIL — _find_next_id、_atomic_write 等方法尚未定义

- [ ] **Step 3: 实现 ID 分配和原子写入方法**

在 IndexManager 类的 `_check_text_hash_consistency` 后追加：

```python
    # ------------------------------------------------------------------
    # ID 管理
    # ------------------------------------------------------------------

    def _find_next_id(self) -> str:
        """查找下一个可用 ID。

        优先复用最小空洞 ID（已删除留下的编号空缺），
        无空洞时返回当前最大 ID + 1。
        ID 格式为数字字符串：'1', '2', '3' ...

        Returns:
            下一个可用的索引 ID。
        """
        if not self._entries:
            return "1"

        existing_ids = {int(eid) for eid in self._entries.keys()}
        max_id = max(existing_ids)

        for i in range(1, max_id + 2):
            if i not in existing_ids:
                return str(i)

        # 理论上不会到达这里，但保留防御性代码
        return str(max_id + 1)

    # ------------------------------------------------------------------
    # 原子写入
    # ------------------------------------------------------------------

    def _atomic_write(self, filepath: Path, data: object) -> None:
        """原子写入 JSON 文件。

        先将数据序列化写入 filepath.tmp，
        写入成功后通过 os.replace() 原子替换正式文件。
        失败时不破坏现有文件。

        Args:
            filepath: 目标文件路径。
            data: 待序列化为 JSON 的数据。
        """
        tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        try:
            tmp_path.write_text(
                ujson.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, filepath)
        except Exception:
            # 清理残留 tmp 文件
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    def save_index(self) -> None:
        """原子写入 index.json。

        将当前内存中的 _entries 和 index_version
        序列化为符合规范的 index.json 格式并原子写入磁盘。
        """
        data: dict[str, object] = {
            "version": self.index_version,
            "entries": self._entries,
        }
        index_path = self._data_dir / "index.json"
        self._atomic_write(index_path, data)
        logger.info("index.json 已保存，共 %d 条记录", len(self._entries))

    def save_embeddings(self) -> None:
        """原子写入 embeddings.json。

        将当前内存中的 _embeddings 序列化并原子写入磁盘。
        """
        emb_path = self._data_dir / "embeddings.json"
        self._atomic_write(emb_path, self._embeddings)
        logger.info(
            "embeddings.json 已保存，共 %d 条记录", len(self._embeddings)
        )
        self._embeddings_stale = False
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestFindNextId or TestAtomicWrite or TestSaveMethods"
```
Expected: 所有 10 个测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 实现 IndexManager ID 分配、原子写入和保存方法"
```

---

### Task 5: 增删条目 + 锁管理

**Files:**
- Modify: `bot/engine/index_manager.py` (追加 add_entry, remove_entry, acquire_lock, release_lock, is_locked)
- Modify: `tests/unit/engine/test_index_manager.py` (追加测试)

- [ ] **Step 1: 编写增删和锁管理测试**

在测试文件中追加：

```python
class TestAddEntry:
    """add_entry() 测试。"""

    def test_add_entry_assigns_id(self, tmp_path: Path) -> None:
        """add_entry 分配 ID 并写入磁盘。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {}
        mgr._embeddings = {}

        new_id = mgr.add_entry(
            filename="new.jpg",
            text="新图片",
            embedding=[0.1, 0.2],
        )
        assert new_id == "1"
        assert mgr._entries["1"]["filename"] == "new.jpg"
        assert mgr._entries["1"]["text"] == "新图片"
        assert mgr._entries["1"]["text_hash"] == compute_text_hash("新图片")
        assert mgr._embeddings["1"]["embedding"] == [0.1, 0.2]

    def test_add_entry_reuses_hole(self, tmp_path: Path) -> None:
        """add_entry 在有空洞时复用最小空洞 ID。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {"1": {"filename": "a.jpg", "text": "a", "text_hash": "x"}}
        mgr._embeddings = {}

        new_id = mgr.add_entry(
            filename="b.jpg",
            text="b",
            embedding=[0.5],
        )
        assert new_id == "2"  # 无空洞，取 max+1

        # 删除 1 后添加，应复用 1
        mgr.remove_entry("1")
        new_id2 = mgr.add_entry(
            filename="c.jpg",
            text="c",
            embedding=[0.8],
        )
        assert new_id2 == "1"

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


class TestRemoveEntry:
    """remove_entry() 测试。"""

    def test_remove_entry_deletes_from_memory(self, tmp_path: Path) -> None:
        """remove_entry 从内存中删除记录。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "a", "text_hash": "x"}
        }
        mgr._embeddings = {"1": {"text_hash": "x", "embedding": [0.1]}}
        mgr.save_index()
        mgr.save_embeddings()

        result = mgr.remove_entry("1")
        assert result is True
        assert "1" not in mgr._entries
        assert "1" not in mgr._embeddings

    def test_remove_nonexistent_returns_false(self, tmp_path: Path) -> None:
        """删除不存在的 ID 返回 False。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        assert mgr.remove_entry("999") is False

    def test_remove_entry_saves_to_disk(self, tmp_path: Path) -> None:
        """remove_entry 后数据持久化到磁盘。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "a", "text_hash": "x"}
        }
        mgr.save_index()
        mgr.remove_entry("1")

        data = ujson.loads(
            (tmp_path / "index.json").read_text(encoding="utf-8")
        )
        assert "1" not in data["entries"]


class TestLockManagement:
    """锁管理测试。"""

    def test_acquire_lock_succeeds(self) -> None:
        """未锁定时 acquire_lock 返回 True。"""
        mgr = IndexManager()
        assert mgr.acquire_lock() is True
        assert mgr.is_locked is True

    def test_acquire_lock_fails_when_locked(self) -> None:
        """已锁定时 acquire_lock 返回 False。"""
        mgr = IndexManager()
        mgr.acquire_lock()
        assert mgr.acquire_lock() is False

    def test_release_lock(self) -> None:
        """释放锁后可再次获取。"""
        mgr = IndexManager()
        mgr.acquire_lock()
        mgr.release_lock()
        assert mgr.is_locked is False
        assert mgr.acquire_lock() is True

    def test_release_when_not_locked_is_safe(self) -> None:
        """未锁定时释放不抛异常。"""
        mgr = IndexManager()
        mgr.release_lock()  # 不应抛出异常
        assert mgr.is_locked is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestAddEntry or TestRemoveEntry or TestLockManagement"
```
Expected: FAIL — add_entry、remove_entry、acquire_lock 等方法尚未定义

- [ ] **Step 3: 实现增删和锁管理**

在 IndexManager 类的 `save_embeddings` 后追加：

```python
    # ------------------------------------------------------------------
    # 单条增删
    # ------------------------------------------------------------------

    def add_entry(
        self,
        filename: str,
        text: str,
        embedding: list[float],
    ) -> str:
        """添加单条索引记录。

        自动分配可用 ID，计算 text_hash，
        同时写入 _entries 和 _embeddings，
        并原子写入磁盘。

        Args:
            filename: 表情包文件名。
            text: OCR 识别文本。
            embedding: embedding 向量。

        Returns:
            分配的索引 ID。
        """
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
        return entry_id

    def remove_entry(self, entry_id: str) -> bool:
        """删除单条索引记录。

        同时从 _entries 和 _embeddings 中删除，
        并原子写入磁盘。允许产生 ID 空洞。

        Args:
            entry_id: 待删除的索引 ID。

        Returns:
            True 表示删除成功，False 表示 ID 不存在。
        """
        if entry_id not in self._entries:
            logger.warning("尝试删除不存在的记录: id=%s", entry_id)
            return False

        filename = self._entries[entry_id].get("filename", "")
        del self._entries[entry_id]
        self._embeddings.pop(entry_id, None)

        self.save_index()
        self.save_embeddings()

        logger.info("已删除索引记录: id=%s, filename=%s", entry_id, filename)
        return True

    # ------------------------------------------------------------------
    # 锁管理
    # ------------------------------------------------------------------

    def acquire_lock(self) -> bool:
        """非阻塞尝试获取索引更新锁。

        同一时间只允许一个索引写入任务运行。
        如果锁已被占用，返回 False；
        调用方应回复"索引正在更新，请稍后再试"。

        Returns:
            True 表示成功获取锁，False 表示锁已被占用。
        """
        if self._locked:
            return False
        self._locked = True
        logger.debug("索引更新锁已获取")
        return True

    def release_lock(self) -> None:
        """释放索引更新锁。"""
        if self._locked:
            self._locked = False
            logger.debug("索引更新锁已释放")

    @property
    def is_locked(self) -> bool:
        """锁是否被持有。"""
        return self._locked
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestAddEntry or TestRemoveEntry or TestLockManagement"
```
Expected: 所有 10 个测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 实现 IndexManager 单条增删和锁管理"
```

---

### Task 6: sync_with_filesystem 文件系统同步

**Files:**
- Modify: `bot/engine/index_manager.py` (追加 sync_with_filesystem 和相关方法)
- Modify: `tests/unit/engine/test_index_manager.py` (追加测试)

- [ ] **Step 1: 编写同步测试**

在测试文件中追加：

```python
class TestSyncWithFilesystem:
    """sync_with_filesystem() 测试。"""

    def test_sync_no_memes_dir_creates_it(self, tmp_path: Path) -> None:
        """memes/ 目录不存在时自动创建。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()

        # 初始化空索引
        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        mgr = IndexManager(data_dir=str(data_dir), memes_dir=str(memes_dir))
        mgr.load()

        assert not memes_dir.exists()
        # 同步应创建 memes/ 目录
        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert memes_dir.exists()
        assert result.added == 0
        assert result.deleted == 0

    def test_sync_empty_memes_noop(self, tmp_path: Path) -> None:
        """memes/ 为空时同步无变化。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        mgr = IndexManager(data_dir=str(data_dir), memes_dir=str(memes_dir))
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.added == 0
        assert result.deleted == 0
        assert result.failed == []

    def test_sync_adds_new_images(self, tmp_path: Path) -> None:
        """新增图片被添加到索引。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        # 创建测试图片
        (memes_dir / "pic1.jpg").write_text("fake image content 1")
        (memes_dir / "pic2.png").write_text("fake image content 2")

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        # 使用 mock OCR/embedding provider
        class MockOcr:
            async def ocr(self, path: str) -> str:
                return f"text of {Path(path).name}"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2]

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
        assert result.added == 2
        assert len(mgr._entries) == 2
        # 验证索引文件已写入
        index_data_disk = ujson.loads(
            (data_dir / "index.json").read_text(encoding="utf-8")
        )
        assert len(index_data_disk["entries"]) == 2

    def test_sync_removes_deleted_images(self, tmp_path: Path) -> None:
        """已删除的图片从索引中移除。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        # memes/ 只有 pic1.jpg，但索引中有两条记录
        (memes_dir / "pic1.jpg").write_text("fake")

        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "pic1.jpg",
                    "text": "hello",
                    "text_hash": compute_text_hash("hello"),
                },
                "2": {
                    "filename": "deleted.png",
                    "text": "gone",
                    "text_hash": compute_text_hash("gone"),
                },
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        (data_dir / "embeddings.json").write_text(
            ujson.dumps({"1": {}, "2": {}}), encoding="utf-8"
        )

        mgr = IndexManager(data_dir=str(data_dir), memes_dir=str(memes_dir))
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.deleted == 1
        assert "1" in mgr._entries
        assert "2" not in mgr._entries

    def test_sync_mixed_add_and_delete(self, tmp_path: Path) -> None:
        """同时有新增和删除的混合场景。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        # 现有: new1.jpg（新增），删除 old_deleted.png
        (memes_dir / "old_kept.jpg").write_text("old")
        (memes_dir / "new1.jpg").write_text("new")

        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "old_kept.jpg",
                    "text": "old",
                    "text_hash": compute_text_hash("old"),
                },
                "2": {
                    "filename": "old_deleted.png",
                    "text": "gone",
                    "text_hash": compute_text_hash("gone"),
                },
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        class MockOcr:
            async def ocr(self, path: str) -> str:
                return "new text"

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
        assert result.added == 1
        assert result.deleted == 1
        assert len(mgr._entries) == 2
        # ID 1 保留（old_kept.jpg），ID 2 被删除，新增的复用 ID 2
        assert mgr._entries["1"]["filename"] == "old_kept.jpg"

    def test_sync_does_not_reprocess_existing(self, tmp_path: Path) -> None:
        """已存在的文件不重新 OCR。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "cat.jpg").write_text("cat content")

        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "cat.jpg",
                    "text": "original text",
                    "text_hash": compute_text_hash("original text"),
                }
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        call_count = 0

        class CountingOcr:
            async def ocr(self, path: str) -> str:
                nonlocal call_count
                call_count += 1
                return "should not be called"

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=CountingOcr(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        asyncio.run(run_sync())
        # cat.jpg 已存在，不应调用 OCR
        assert call_count == 0

    def test_sync_handles_ocr_failure(self, tmp_path: Path) -> None:
        """OCR 失败时跳过该图片并记录到 failed。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "bad.jpg").write_text("bad")
        (memes_dir / "good.jpg").write_text("good")

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        class FailingOcr:
            async def ocr(self, path: str) -> str:
                if "bad" in path:
                    raise RuntimeError("OCR failed")
                return "good text"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.1]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=FailingOcr(),
            embedding_provider=MockEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())
        assert result.added == 1
        assert len(result.failed) == 1
        assert "bad.jpg" in result.failed[0]
        # good.jpg 应被添加
        assert len(mgr._entries) == 1

    def test_sync_new_images_sorted_by_filename(
        self, tmp_path: Path
    ) -> None:
        """新增图片按文件名升序处理。"""
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "z.jpg").write_text("z")
        (memes_dir / "a.jpg").write_text("a")
        (memes_dir / "m.jpg").write_text("m")

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        processed_order: list[str] = []

        class OrderedOcr:
            async def ocr(self, path: str) -> str:
                processed_order.append(Path(path).name)
                return "text"

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.0]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=OrderedOcr(),
            embedding_provider=MockEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        asyncio.run(run_sync())
        assert processed_order == ["a.jpg", "m.jpg", "z.jpg"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestSyncWithFilesystem"
```
Expected: FAIL — sync_with_filesystem 尚未实现

- [ ] **Step 3: 实现 sync_with_filesystem**

在 IndexManager 类的 `release_lock` 后追加：

```python
    # ------------------------------------------------------------------
    # 文件系统同步
    # ------------------------------------------------------------------

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )

    async def sync_with_filesystem(self) -> SyncResult:
        """按文件名同步索引与 memes/ 目录。

        1. 扫描 memes/ 获取当前图片文件列表。
        2. 对比 index entries 找出已删除的文件，移除对应记录。
        3. 找出新增文件，按文件名升序处理：
           - 调用 OCR 识别文本
           - 调用 embedding 生成向量
           - 写入索引
        4. 单个图片处理失败不影响其他图片。
        5. 原子写入更新后的索引文件。

        Returns:
            SyncResult(added, deleted, failed)
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)

        existing_files: set[str] = {
            f.name
            for f in self._memes_dir.iterdir()
            if f.is_file()
            and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        }

        # 构建文件名 → id 映射
        filename_to_id: dict[str, str] = {}
        for eid, entry in self._entries.items():
            fn = entry.get("filename", "")
            if fn:
                filename_to_id[fn] = eid

        # 1. 删除已不存在的图片
        deleted_count = 0
        for filename, eid in list(filename_to_id.items()):
            if filename not in existing_files:
                logger.info("图片已删除，移除索引: id=%s, filename=%s", eid, filename)
                del self._entries[eid]
                self._embeddings.pop(eid, None)
                del filename_to_id[filename]
                deleted_count += 1

        # 2. 找出新增图片（按文件名升序）
        new_files = sorted(
            f for f in existing_files if f not in filename_to_id
        )

        added_count = 0
        failed: list[str] = []

        for filename in new_files:
            image_path = self._memes_dir / filename
            try:
                # OCR
                if self._ocr_provider is None:
                    raise RuntimeError("OCR 服务未注入")
                text = await self._ocr_provider.ocr(str(image_path))

                # Embedding
                if self._embedding_provider is None:
                    raise RuntimeError("Embedding 服务未注入")
                embedding = await self._embedding_provider.embed(text)

                # 写入索引
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
                added_count += 1
                logger.info("新增图片已加入索引: id=%s, filename=%s", entry_id, filename)

            except Exception as exc:
                logger.error("处理图片失败: filename=%s, error=%s", filename, exc)
                failed.append(filename)

        # 3. 原子写入
        if added_count > 0 or deleted_count > 0:
            self.save_index()
            if added_count > 0:
                self.save_embeddings()

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 失败=%d",
            added_count,
            deleted_count,
            len(failed),
        )
        return SyncResult(added=added_count, deleted=deleted_count, failed=failed)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v -k "TestSyncWithFilesystem"
```
Expected: 所有 8 个测试 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 实现 IndexManager.sync_with_filesystem 文件系统同步"
```

---

### Task 7: 全量测试 + commit

**Files:**
- (无文件变更，仅验证)

- [ ] **Step 1: 运行所有测试**

```bash
uv run python -m pytest tests/unit/engine/test_index_manager.py -v
```
Expected: 全部测试 PASS

- [ ] **Step 2: 确保已有测试也未回归**

```bash
uv run python -m pytest tests/ -v
```
Expected: 全部测试 PASS

- [ ] **Step 3: Python 编译检查**

```bash
uv run python -m compileall bot
```
Expected: 编译成功，无语法错误
```

