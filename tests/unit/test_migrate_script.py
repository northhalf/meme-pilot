"""迁移脚本 migrate_json_to_db.py 单元测试。"""

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
            "1": {"text_hash": "sha256:abc", "embedding": _encode_emb([0.1, 0.2, 0.3] + [0.0] * 1021)},
            "2": {"text_hash": "sha256:def", "embedding": _encode_emb([0.4, 0.5, 0.6] + [0.0] * 1021)},
            "3": {"text_hash": "sha256:ghi", "embedding": _encode_emb([0.0] * 1024)},
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
        import asyncio

        from bot.engine.vector_store import VectorStore

        _run_migration(old_data_dir)
        vs = VectorStore(str(old_data_dir / "chroma"))
        vs.load()
        # id=1 向量应为旧 [0.1,0.2,0.3]（零填充至 1024 维，方向不变）
        hits = asyncio.run(vs.query([0.1, 0.2, 0.3] + [0.0] * 1021, n_results=2))
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
                "5": {"text_hash": "h2", "embedding": _encode_emb([1.0, 0.0] + [0.0] * 1022)},
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
                "1": {"text_hash": "h", "embedding": [0.7, 0.8, 0.9] + [0.0] * 1021}
            }}), encoding="utf-8"
        )
        _run_migration(data_dir)

        from bot.engine.vector_store import VectorStore
        vs = VectorStore(str(data_dir / "chroma"))
        vs.load()
        assert vs.count() == 1
        vs.close()


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
            "3": {"filename": "c.jpg", "text": "加班", "text_hash": "h3"},
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
        assert {1, 2} == set(entries)
        assert entries[1].text == "加班"
        md.close()

        vs = VectorStore(str(dup_text_data_dir / "chroma"))
        vs.load()
        assert vs.count() == 2
        vs.close()
