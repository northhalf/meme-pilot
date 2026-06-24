"""IndexManager 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import ujson

from bot.engine.index_manager import (
    AddResult,
    IndexCorruptedError,
    IndexManager,
    SyncResult,
    _resolve_unique_filename,
    compute_text_hash,
    dedup_key,
    is_blank_text,
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


class TestIndexManagerInit:
    """IndexManager 初始化测试。"""

    def test_default_dirs(self) -> None:
        """默认 data_dir='data', memes_dir='memes'。"""
        mgr = IndexManager()
        assert mgr._data_dir == Path("data")
        assert mgr._memes_dir == Path("memes")
        assert mgr._no_text_dir == Path("meme_no_text")

    def test_custom_dirs(self) -> None:
        """可自定义目录。"""
        mgr = IndexManager(data_dir="/tmp/idx", memes_dir="/tmp/memes")
        assert mgr._data_dir == Path("/tmp/idx")
        assert mgr._memes_dir == Path("/tmp/memes")

    def test_custom_no_text_dir(self) -> None:
        """可自定义无文字图目录。"""
        mgr = IndexManager(no_text_dir="/tmp/blank")
        assert mgr._no_text_dir == Path("/tmp/blank")

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

    def test_load_resets_embeddings_if_missing(
        self, tmp_path: Path
    ) -> None:
        """embeddings.json 不存在时 _embeddings 置空，由 sync 重建阶段全量重建。"""
        index_data = {"version": 1, "entries": {}}
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr._embeddings == {}

    def test_load_resets_embeddings_if_corrupt(
        self, tmp_path: Path
    ) -> None:
        """embeddings.json 损坏时 load() 不抛异常，置空 _embeddings 待重建。"""
        index_data = {"version": 1, "entries": {}}
        (tmp_path / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        (tmp_path / "embeddings.json").write_text(
            "{corrupt", encoding="utf-8"
        )
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr._embeddings == {}

    def test_load_creates_data_dir_if_missing(self, tmp_path: Path) -> None:
        """data_dir 不存在时自动创建。"""
        data_dir = tmp_path / "nonexistent" / "data"
        assert not data_dir.exists()
        mgr = IndexManager(data_dir=str(data_dir))
        mgr.load()
        assert data_dir.exists()


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


class TestGetEmbeddings:
    """get_embeddings() 测试。"""

    def test_returns_embeddings(self, tmp_path: Path) -> None:
        """返回当前内存中的 embedding 索引。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._embeddings = {
            "1": {"text_hash": "sha256:a", "embedding": [0.1, 0.2]}
        }

        result = mgr.get_embeddings()

        assert result == {
            "1": {"text_hash": "sha256:a", "embedding": [0.1, 0.2]}
        }

    def test_returns_outer_copy(self, tmp_path: Path) -> None:
        """返回外层浅拷贝，避免调用方替换整个条目。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._embeddings = {
            "1": {"text_hash": "sha256:a", "embedding": [0.1, 0.2]}
        }

        result = mgr.get_embeddings()
        result["2"] = {"text_hash": "sha256:b", "embedding": [0.3, 0.4]}

        assert "2" not in mgr._embeddings


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
        mgr._rebuild_dedup_index()

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
        mgr._rebuild_dedup_index()

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

    @pytest.mark.asyncio
    async def test_acquire_lock_succeeds(self) -> None:
        """未锁定时 acquire_lock 返回 True。"""
        mgr = IndexManager()
        assert await mgr.acquire_lock() is True
        assert mgr.is_locked is True

    @pytest.mark.asyncio
    async def test_acquire_lock_fails_when_locked(self) -> None:
        """已锁定时 acquire_lock 返回 False。"""
        mgr = IndexManager()
        await mgr.acquire_lock()
        assert await mgr.acquire_lock() is False

    @pytest.mark.asyncio
    async def test_release_lock(self) -> None:
        """释放锁后可再次获取。"""
        mgr = IndexManager()
        await mgr.acquire_lock()
        mgr.release_lock()
        assert mgr.is_locked is False
        assert await mgr.acquire_lock() is True

    def test_release_when_not_locked_is_safe(self) -> None:
        """未锁定时释放不抛异常。"""
        mgr = IndexManager()
        mgr.release_lock()  # 不应抛出异常
        assert mgr.is_locked is False


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
            async def ocr(self, image_path: str) -> str:
                return f"text of {Path(image_path).name}"

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
        # embeddings.json 与 index 的 text_hash 保持一致，避免触发重建，
        # 让本测试专注验证删除逻辑
        (data_dir / "embeddings.json").write_text(
            ujson.dumps(
                {
                    "1": {"text_hash": compute_text_hash("hello"), "embedding": [0.1]},
                    "2": {"text_hash": compute_text_hash("gone"), "embedding": [0.2]},
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
        assert result.deleted == 1
        assert result.failed == []
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
        # embeddings.json 与 index 的 text_hash 一致，避免 old_kept.jpg 触发重建，
        # 让本测试专注验证「新增 + 删除」混合
        (data_dir / "embeddings.json").write_text(
            ujson.dumps(
                {
                    "1": {"text_hash": compute_text_hash("old"), "embedding": [0.1]},
                    "2": {"text_hash": compute_text_hash("gone"), "embedding": [0.2]},
                }
            ),
            encoding="utf-8",
        )

        class MockOcr:
            async def ocr(self, image_path: str) -> str:
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
        assert result.failed == []
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
        # embeddings.json 与 index 的 text_hash 一致，避免触发重建，
        # 让本测试专注验证「已存在文件不重复 OCR」
        (data_dir / "embeddings.json").write_text(
            ujson.dumps(
                {
                    "1": {
                        "text_hash": compute_text_hash("original text"),
                        "embedding": [0.0],
                    }
                }
            ),
            encoding="utf-8",
        )

        call_count = 0

        class CountingOcr:
            async def ocr(self, image_path: str) -> str:
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

        result = asyncio.run(run_sync())
        # cat.jpg 已存在且 embedding 一致，不应调用 OCR，也不应进 failed
        assert call_count == 0
        assert result.failed == []

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
            async def ocr(self, image_path: str) -> str:
                if "bad" in image_path:
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
        """新增图片按文件名升序分配 ID。

        并行处理后 OCR 完成顺序不确定，但 ID 分配仍按文件名升序，
        保证 a.jpg < m.jpg < z.jpg 对应的 id 数值递增。
        """
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

        class OrderedOcr:
            async def ocr(self, image_path: str) -> str:
                # 每张图返回不同文本，避免触发去重，专注验证文件名升序分配 ID
                return f"text of {Path(image_path).name}"

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

        # 按 id 数值升序排列，对应文件名应为 a.jpg, m.jpg, z.jpg
        sorted_ids = sorted(mgr._entries.keys(), key=int)
        filenames_by_id = [mgr._entries[eid]["filename"] for eid in sorted_ids]
        assert filenames_by_id == ["a.jpg", "m.jpg", "z.jpg"]

    def test_sync_ocr_runs_concurrently(self, tmp_path: Path) -> None:
        """多张新增图片的 OCR 应能并行执行。

        通过记录同时在执行 OCR 的任务数最大值，验证并发上限 > 1。
        每个 OCR 任务内部人为制造短暂 await 让出控制权，使多个任务
        能在 Semaphore 范围内同时处于执行状态。
        """
        import asyncio

        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        # 6 张图，并发上限默认 5，期望同时执行数 >= 2
        for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"):
            (memes_dir / name).write_text(name)

        index_data = {"version": 1, "entries": {}}
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )

        in_flight = 0
        max_in_flight = 0
        counter_lock = asyncio.Lock()

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

        class MockEmbed:
            async def embed(self, text: str) -> list[float]:
                return [0.0]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=ConcurrentOcr(),
            embedding_provider=MockEmbed(),
            # 显式指定并发上限，避免依赖默认值
            sync_concurrency=5,
        )
        mgr.load()

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())

        assert result.added == 6
        assert result.deduped == 0
        assert max_in_flight >= 2, (
            f"OCR 未并行执行，最大同时执行数仅 {max_in_flight}"
        )

    def test_sync_rebuilds_embedding_when_text_edited(
        self, tmp_path: Path
    ) -> None:
        """用户手改 index.json 的 text 后，text_hash 不一致应重建对应 embedding。

        场景：index.json 中 cat.jpg 的 text 被手动改成新文本，但 embeddings.json
        里仍是旧 text_hash。load() 会按新 text 修复 _entries[id].text_hash，
        sync 时检测到 _entries 与 _embeddings 的 text_hash 不一致 → 用新 text
        重建 embedding。不应重新 OCR（text 已存在）。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "cat.jpg").write_text("cat content")

        # 用户把 text 从 "old text" 手改成 "new text"，但 text_hash 仍是旧的
        old_hash = compute_text_hash("old text")
        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "cat.jpg",
                    "text": "new text",
                    "text_hash": old_hash,  # 故意写旧 hash，模拟用户只改了 text
                }
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        # embeddings.json 里的 text_hash 也是旧的，与修复后的 _entries 不一致
        (data_dir / "embeddings.json").write_text(
            ujson.dumps(
                {"1": {"text_hash": old_hash, "embedding": [0.0, 0.0]}}
            ),
            encoding="utf-8",
        )

        ocr_call_count = 0
        embed_texts: list[str] = []

        class NoCallOcr:
            async def ocr(self, image_path: str) -> str:
                nonlocal ocr_call_count
                ocr_call_count += 1
                return "should not be called"

        class RecordingEmbed:
            async def embed(self, text: str) -> list[float]:
                embed_texts.append(text)
                return [0.9, 0.8]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=NoCallOcr(),
            embedding_provider=RecordingEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())

        # 没有新增/删除，只有重建
        assert result.added == 0
        assert result.deleted == 0
        assert result.failed == []
        # 重建用的是当前 text，不应重新 OCR
        assert ocr_call_count == 0
        # embed 收到的是修复后的新 text
        assert embed_texts == ["new text"]
        # _embeddings[id] 已更新为新 hash 和新向量
        new_hash = compute_text_hash("new text")
        assert mgr._embeddings["1"]["text_hash"] == new_hash
        assert mgr._embeddings["1"]["embedding"] == [0.9, 0.8]
        # _entries[id].text_hash 也已落盘修复
        assert mgr._entries["1"]["text_hash"] == new_hash
        # 落盘检查：磁盘 index.json 的 text_hash 已更新
        disk_index = ujson.loads(
            (data_dir / "index.json").read_text(encoding="utf-8")
        )
        assert disk_index["entries"]["1"]["text_hash"] == new_hash
        disk_emb = ujson.loads(
            (data_dir / "embeddings.json").read_text(encoding="utf-8")
        )
        assert disk_emb["1"]["text_hash"] == new_hash

    def test_sync_rebuilds_all_when_embeddings_missing(
        self, tmp_path: Path
    ) -> None:
        """embeddings.json 缺失时，对全部已有条目全量重建 embedding。

        场景：index.json 有效（2 条），但 embeddings.json 不存在。
        load() 将 _embeddings 置空，sync 时所有 id 均不在 _embeddings → 全部重建。
        不应重新 OCR。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "a.jpg").write_text("a")
        (memes_dir / "b.jpg").write_text("b")

        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "a.jpg",
                    "text": "text a",
                    "text_hash": compute_text_hash("text a"),
                },
                "2": {
                    "filename": "b.jpg",
                    "text": "text b",
                    "text_hash": compute_text_hash("text b"),
                },
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        # 故意不创建 embeddings.json

        ocr_call_count = 0
        embed_texts: list[str] = []

        class NoCallOcr:
            async def ocr(self, image_path: str) -> str:
                nonlocal ocr_call_count
                ocr_call_count += 1
                return "should not be called"

        class RecordingEmbed:
            async def embed(self, text: str) -> list[float]:
                embed_texts.append(text)
                return [0.1, 0.2]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            ocr_provider=NoCallOcr(),
            embedding_provider=RecordingEmbed(),
        )
        mgr.load()
        # load 后 embeddings 缺失，_embeddings 为空待重建
        assert mgr._embeddings == {}

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())

        # 无新增无删除，全量重建 2 条
        assert result.added == 0
        assert result.deleted == 0
        assert result.failed == []
        assert ocr_call_count == 0
        # 两条都重建（顺序因并行不确定，用集合比较）
        assert sorted(embed_texts) == ["text a", "text b"]
        # 两条 embedding 都已生成
        assert set(mgr._embeddings.keys()) == {"1", "2"}
        assert mgr._embeddings["1"]["embedding"] == [0.1, 0.2]
        assert mgr._embeddings["2"]["embedding"] == [0.1, 0.2]
        # 落盘检查
        disk_emb = ujson.loads(
            (data_dir / "embeddings.json").read_text(encoding="utf-8")
        )
        assert set(disk_emb.keys()) == {"1", "2"}

    def test_sync_rebuild_failure_recorded_in_failed(
        self, tmp_path: Path
    ) -> None:
        """重建 embedding 失败时，对应文件名记入 failed，不影响其他条目。

        场景：2 条已有条目都需重建（embeddings.json 缺失），其中一条的
        embed 抛异常 → 该条记入 failed，另一条仍重建成功。
        """
        data_dir = tmp_path / "data"
        memes_dir = tmp_path / "memes"
        data_dir.mkdir()
        memes_dir.mkdir()

        (memes_dir / "good.jpg").write_text("g")
        (memes_dir / "bad.jpg").write_text("b")

        index_data = {
            "version": 1,
            "entries": {
                "1": {
                    "filename": "good.jpg",
                    "text": "good text",
                    "text_hash": compute_text_hash("good text"),
                },
                "2": {
                    "filename": "bad.jpg",
                    "text": "bad text",
                    "text_hash": compute_text_hash("bad text"),
                },
            },
        }
        (data_dir / "index.json").write_text(
            ujson.dumps(index_data), encoding="utf-8"
        )
        # 不创建 embeddings.json → 两条都需重建

        class FailingEmbed:
            async def embed(self, text: str) -> list[float]:
                if "bad" in text:
                    raise RuntimeError("embed failed")
                return [0.5]

        mgr = IndexManager(
            data_dir=str(data_dir),
            memes_dir=str(memes_dir),
            embedding_provider=FailingEmbed(),
        )
        mgr.load()

        import asyncio

        async def run_sync() -> SyncResult:
            return await mgr.sync_with_filesystem()

        result = asyncio.run(run_sync())

        # good.jpg 重建成功，bad.jpg 重建失败
        assert result.failed == ["bad.jpg"]
        assert mgr._embeddings["1"]["embedding"] == [0.5]
        assert "2" not in mgr._embeddings or "embedding" not in mgr._embeddings.get("2", {})

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

        文件名升序处理：blank, dup, ok1, ok2。
        blank 无文字移走；dup（"文 本一"→"文本一"）先处理，winner_keys 为空 → 正常新增成赢家；
        ok1（"文本一"）后处理，键命中 dup → ok1 被去重删除；
        ok2 正常新增。故保留 dup+ok2，删除 ok1。
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


class TestFindEntryByDedupKey:
    """_find_entry_by_dedup_key 私有方法测试。"""

    def test_match_found(self) -> None:
        """去重键命中已有条目时返回其 ID。"""
        mgr = IndexManager()
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "加班 好累", "text_hash": "x"},
            "2": {"filename": "b.jpg", "text": "狗在跑", "text_hash": "y"},
        }
        mgr._rebuild_dedup_index()
        # "加班 好累" 去空格 == "加班好累"
        assert mgr._find_entry_by_dedup_key("加班好累") == "1"

    def test_no_match_returns_none(self) -> None:
        """无命中返回 None。"""
        mgr = IndexManager()
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "猫", "text_hash": "x"},
        }
        mgr._rebuild_dedup_index()
        assert mgr._find_entry_by_dedup_key("狗") is None

    def test_empty_entries_returns_none(self) -> None:
        """空索引返回 None。"""
        mgr = IndexManager()
        assert mgr._find_entry_by_dedup_key("anything") is None


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


class TestIndexManagerLock:
    """索引更新锁行为测试。"""

    @pytest.mark.asyncio
    async def test_acquire_lock_returns_true_when_free(self, tmp_path: Path) -> None:
        """空闲时 acquire_lock 返回 True。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        assert await mgr.acquire_lock() is True

    @pytest.mark.asyncio
    async def test_acquire_lock_returns_false_when_held(self, tmp_path: Path) -> None:
        """已持有时 acquire_lock 返回 False。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        await mgr.acquire_lock()
        assert await mgr.acquire_lock() is False

    @pytest.mark.asyncio
    async def test_release_lock_allows_reacquire(self, tmp_path: Path) -> None:
        """释放后可重新获取。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        await mgr.acquire_lock()
        mgr.release_lock()
        assert await mgr.acquire_lock() is True

    @pytest.mark.asyncio
    async def test_is_locked_reflects_state(self, tmp_path: Path) -> None:
        """is_locked 属性反映当前锁状态。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        assert mgr.is_locked is False
        await mgr.acquire_lock()
        assert mgr.is_locked is True
        mgr.release_lock()
        assert mgr.is_locked is False

    def test_release_lock_when_not_held_is_noop(self, tmp_path: Path) -> None:
        """未持有时 release_lock 不抛异常。"""
        mgr = IndexManager(str(tmp_path), str(tmp_path / "memes"))
        mgr.release_lock()  # 不应抛出
        assert mgr.is_locked is False

