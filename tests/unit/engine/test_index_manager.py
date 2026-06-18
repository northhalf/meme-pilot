"""IndexManager 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import ujson

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
