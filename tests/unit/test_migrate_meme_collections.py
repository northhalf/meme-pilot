"""migrate_meme_collections 子命令单元测试。"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bot.engine.metadata_store import MetadataStore
from bot.engine.types import MemePublicId
from bot.engine.vector_store import VectorStore
from scripts.migrate_meme_collections import (
    InvalidCollectionNameError,
    MigrationError,
    UpgradeResult,
    build_parser,
    detect_schema,
    run_move_root,
    run_move_root_paths,
    run_upgrade_schema,
    validate_collection_name,
)


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """创建一条旧 Schema 记录并返回数据库路径。"""
    db_path = tmp_path / "index.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE meme ("
            "id INTEGER PRIMARY KEY, image_path TEXT, text TEXT, speaker TEXT"
            ")"
        )
        conn.execute(
            "CREATE TABLE meme_tag ("
            "meme_id INTEGER NOT NULL, tag TEXT NOT NULL, "
            "PRIMARY KEY (meme_id, tag)"
            ")"
        )
        conn.execute(
            "INSERT INTO meme (id, image_path, text, speaker) "
            "VALUES (42, 'a.webp', '文本', '曹操')"
        )
        conn.execute("INSERT INTO meme_tag (meme_id, tag) VALUES (42, '吐槽')")
        conn.commit()
    return db_path


@pytest.fixture
def legacy_chroma(tmp_path: Path) -> Path:
    """创建无 per-record metadata 的旧 Chroma 记录并返回目录路径。"""
    chroma_dir = tmp_path / "chroma"
    vector_store = VectorStore(str(chroma_dir))
    vector_store.load()
    try:
        collection = vector_store._require_collection()
        collection.add(ids=["42"], embeddings=[[1.0, 0.0]])
    finally:
        vector_store.close()
    return chroma_dir


@pytest.fixture
def current_db(tmp_path: Path) -> Path:
    """创建当前 Schema 的空数据库并返回路径。"""
    db_path = tmp_path / "index.db"
    from bot.engine.metadata_store import create_current_schema

    with sqlite3.connect(db_path) as conn:
        create_current_schema(conn)
        conn.commit()
    return db_path


@pytest.fixture
def current_chroma(tmp_path: Path) -> Path:
    """创建并关闭一个空 Chroma 目录后返回路径。"""
    chroma_dir = tmp_path / "chroma"
    vector_store = VectorStore(str(chroma_dir))
    vector_store.load()
    vector_store.close()
    return chroma_dir


@pytest.fixture
def memes_dir(tmp_path: Path) -> Path:
    """返回 memes 根目录。"""
    path = tmp_path / "memes"
    path.mkdir()
    return path


@pytest.fixture
def current_store(tmp_sqlite_path: Path) -> Iterator[MetadataStore]:
    """返回已加载当前 Schema 的 MetadataStore。"""
    store = MetadataStore(str(tmp_sqlite_path))
    store.load()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def vector_store(tmp_chroma_dir: Path) -> Iterator[VectorStore]:
    """返回已加载的 VectorStore。"""
    vs = VectorStore(str(tmp_chroma_dir))
    vs.load()
    try:
        yield vs
    finally:
        vs.close()


class TestDetectSchema:
    """测试 Schema 检测。"""

    def test_detect_schema_unknown_when_no_meme_table(self, tmp_path: Path) -> None:
        """没有 meme 表时返回 unknown。"""
        db_path = tmp_path / "index.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
            conn.commit()

        with sqlite3.connect(db_path) as conn:
            assert detect_schema(conn) == "unknown"

    def test_detect_schema_current(self, current_db: Path) -> None:
        """当前 Schema 返回 current。"""
        with sqlite3.connect(current_db) as conn:
            assert detect_schema(conn) == "current"

    def test_detect_schema_legacy(self, legacy_db: Path) -> None:
        """旧 Schema 返回 legacy。"""
        with sqlite3.connect(legacy_db) as conn:
            assert detect_schema(conn) == "legacy"


class TestUpgradeSchemaDryRun:
    """测试 dry-run 不修改数据。"""

    @pytest.mark.asyncio
    async def test_upgrade_schema_dry_run_has_no_side_effects(
        self, legacy_db: Path, tmp_path: Path
    ) -> None:
        """dry-run 只统计，不备份、不改表、不写 Chroma。"""
        chroma_dir = tmp_path / "chroma"

        result = await run_upgrade_schema(legacy_db, chroma_dir, dry_run=True)

        assert result == UpgradeResult(
            upgraded_entries=1,
            updated_vectors=0,
            backup_path=None,
            already_current=False,
        )
        assert not list(tmp_path.glob("index.db.*.bak"))
        with sqlite3.connect(legacy_db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(meme)")}
            assert "collection_id" not in columns


class TestUpgradeSchemaMigration:
    """测试旧库迁移映射。"""

    @pytest.mark.asyncio
    async def test_upgrade_schema_maps_old_id_to_global_public_id(
        self, legacy_db: Path, legacy_chroma: Path
    ) -> None:
        """旧记录映射到全局合集，标签保留，Chroma 补 collection_id=0。"""
        result = await run_upgrade_schema(legacy_db, legacy_chroma, dry_run=False)

        assert result.upgraded_entries == 1
        assert result.updated_vectors == 1
        assert result.backup_path is not None
        assert result.already_current is False

        with sqlite3.connect(legacy_db) as conn:
            row = conn.execute(
                "SELECT id, collection_id, local_id, image_path, text, speaker "
                "FROM meme"
            ).fetchone()
            tags = conn.execute("SELECT meme_id, tag FROM meme_tag").fetchall()
            version = conn.execute("SELECT version FROM schema_version").fetchone()

        assert row == (42, 0, 42, "a.webp", "文本", "曹操")
        assert tags == [(42, "吐槽")]
        assert version == (2,)

        vector_store = VectorStore(str(legacy_chroma))
        vector_store.load()
        try:
            assert await vector_store.get_collection_ids() == {42: 0}
        finally:
            vector_store.close()

    @pytest.mark.asyncio
    async def test_upgrade_schema_already_current_with_complete_metadata(
        self, current_db: Path, tmp_path: Path
    ) -> None:
        """当前 Schema 且 Chroma metadata 完整时返回 already_current。"""
        chroma_dir = tmp_path / "chroma"
        vector_store = VectorStore(str(chroma_dir))
        vector_store.load()
        try:
            await vector_store.upsert(1, [1.0, 0.0], collection_id=0)
        finally:
            vector_store.close()

        with sqlite3.connect(current_db) as conn:
            conn.execute(
                "INSERT INTO meme (id, collection_id, local_id, image_path, text) "
                "VALUES (1, 0, 1, 'a.webp', '文本')"
            )
            conn.commit()

        result = await run_upgrade_schema(current_db, chroma_dir, dry_run=False)

        assert result.already_current is True
        assert result.upgraded_entries == 0
        assert result.updated_vectors == 0

    @pytest.mark.asyncio
    async def test_upgrade_schema_repairs_missing_metadata(
        self, current_db: Path, legacy_chroma: Path
    ) -> None:
        """当前 Schema 但 Chroma 缺 metadata 时只补 Chroma。"""
        with sqlite3.connect(current_db) as conn:
            conn.execute(
                "INSERT INTO meme (id, collection_id, local_id, image_path, text) "
                "VALUES (42, 0, 42, 'a.webp', '文本')"
            )
            conn.commit()

        result = await run_upgrade_schema(current_db, legacy_chroma, dry_run=False)

        assert result.already_current is False
        assert result.upgraded_entries == 0
        assert result.updated_vectors == 1

        vector_store = VectorStore(str(legacy_chroma))
        vector_store.load()
        try:
            assert await vector_store.get_collection_ids() == {42: 0}
        finally:
            vector_store.close()


class TestUpgradeSchemaRollback:
    """测试 Chroma 失败时的回滚。"""

    @pytest.mark.asyncio
    async def test_upgrade_schema_rolls_back_sqlite_when_chroma_fails(
        self,
        legacy_db: Path,
        legacy_chroma: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Chroma update 失败时 SQLite 应保持旧 Schema。"""
        monkeypatch.setattr(
            VectorStore,
            "update_collection_id",
            AsyncMock(side_effect=RuntimeError("chroma failed")),
        )

        with pytest.raises(MigrationError):
            await run_upgrade_schema(legacy_db, legacy_chroma, dry_run=False)

        with sqlite3.connect(legacy_db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(meme)")}
            assert "collection_id" not in columns
            row = conn.execute(
                "SELECT id, image_path, text, speaker FROM meme"
            ).fetchone()
            assert row == (42, "a.webp", "文本", "曹操")


class TestCli:
    """测试命令行解析。"""

    def test_upgrade_schema_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """upgrade-schema --help 能正常打印并退出。"""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["upgrade-schema", "--help"])
        assert exc_info.value.code == 0

    def test_move_root_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """move-root --help 能正常打印并退出。"""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["move-root", "--help"])
        assert exc_info.value.code == 0


class TestValidateCollectionName:
    """测试合集名称校验。"""

    @pytest.mark.parametrize(
        "name",
        ["", ".", "..", ".hidden", "a/b", "a\\b", "a\x00b"],
    )
    def test_validate_collection_name_rejects_invalid(self, name: str) -> None:
        """非法名称应抛 InvalidCollectionNameError。"""
        with pytest.raises(InvalidCollectionNameError):
            validate_collection_name(name)

    def test_validate_collection_name_strips_and_accepts(self) -> None:
        """合法名称去除首尾空格后返回。"""
        assert validate_collection_name("  新三国  ") == "新三国"

    @pytest.mark.parametrize(
        "name", ["新 三国", "新\t三国", "新　三国", "全局", "全部合集"]
    )
    def test_validate_collection_name_rejects_domain_invalid_names(
        self, name: str
    ) -> None:
        """迁移入口应拒绝领域非法名称。"""
        with pytest.raises(InvalidCollectionNameError):
            validate_collection_name(name)


class TestMoveRootPaths:
    """测试 move-root 外层路径与备份流程。"""

    @pytest.mark.asyncio
    async def test_move_root_paths_creates_sqlite_backup(
        self,
        current_db: Path,
        current_chroma: Path,
        memes_dir: Path,
    ) -> None:
        """非 dry-run 会生成 SQLite 备份。"""
        await run_move_root_paths(
            current_db,
            current_chroma,
            memes_dir,
            "新三国",
            dry_run=False,
        )

        assert list(current_db.parent.glob("index.db.*.bak"))


class TestMoveRootMigration:
    """测试 move-root 逐文件迁移逻辑。"""

    @pytest.mark.asyncio
    async def test_move_root_creates_named_collection_and_skips_unindexed(
        self,
        current_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
    ) -> None:
        """创建新合集、迁移已索引文件、跳过未索引文件。"""
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
        assert moved is not None
        assert moved.public_id == MemePublicId(1, 1)
        assert moved.image_path == "新三国/indexed.webp"
        assert (memes_dir / "unindexed.webp").exists()
        assert (memes_dir / "新三国" / "indexed.webp").exists()
        assert result.moved == 1
        assert result.unindexed_skipped == ["unindexed.webp"]

    @pytest.mark.asyncio
    async def test_move_root_renames_file_collision(
        self,
        current_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
    ) -> None:
        """目标目录存在同名文件时自动重命名。"""
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

        entry = current_store.get_entry(entry_id)
        assert entry is not None
        assert entry.image_path == "新三国/a_2.webp"
        assert (memes_dir / "新三国" / "a_2.webp").exists()
        assert not (memes_dir / "a.webp").exists()

    @pytest.mark.asyncio
    async def test_move_root_skips_duplicate_text_without_failure(
        self,
        current_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
    ) -> None:
        """目标合集内已存在相同文本时跳过并记录冲突。"""
        target = current_store.create_collection("新三国")
        current_store.add("新三国/existing.webp", "相同", collection_id=target.id)
        source_id = current_store.add("source.webp", "相同")
        await vector_store.upsert(source_id, [1.0, 0.0], collection_id=0)

        result = await run_move_root(
            current_store, vector_store, memes_dir, "新三国", False
        )

        source_entry = current_store.get_entry(source_id)
        assert source_entry is not None
        assert source_entry.collection_id == 0
        assert source_entry.image_path == "source.webp"
        assert result.conflicts == [("source.webp", MemePublicId(target.id, 1))]
        assert result.failed == []
        assert result.moved == 0

    @pytest.mark.asyncio
    async def test_move_root_rolls_back_file_when_vector_update_fails(
        self,
        current_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Chroma 更新失败时回滚 SQLite 与文件，并清理空合集。"""
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
        entry = current_store.get_entry(entry_id)
        assert entry is not None
        assert entry.public_id == MemePublicId(0, entry_id)
        assert current_store.get_collection_by_name("新三国") is None
        assert result.moved == 0
        assert len(result.failed) == 1

    @pytest.mark.asyncio
    async def test_move_root_dry_run_does_not_modify(
        self,
        current_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
    ) -> None:
        """dry-run 不创建合集、不移动文件、不修改 SQLite 与 Chroma。"""
        (memes_dir / "a.webp").write_bytes(b"source")
        entry_id = current_store.add("a.webp", "文本")
        await vector_store.upsert(entry_id, [1.0, 0.0], collection_id=0)

        result = await run_move_root(
            current_store, vector_store, memes_dir, "新三国", dry_run=True
        )

        assert result.moved == 1
        assert result.failed == []
        assert result.conflicts == []
        entry = current_store.get_entry(entry_id)
        assert entry is not None
        assert entry.image_path == "a.webp"
        assert entry.public_id == MemePublicId(0, entry_id)
        assert (memes_dir / "a.webp").exists()
        assert current_store.get_collection_by_name("新三国") is None

    @pytest.mark.asyncio
    async def test_move_root_no_candidates_does_not_create_collection(
        self,
        current_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
    ) -> None:
        """没有可迁移的已索引文件时不创建目标合集。"""
        result = await run_move_root(
            current_store, vector_store, memes_dir, "新三国", False
        )

        assert result.moved == 0
        assert current_store.get_collection_by_name("新三国") is None
        assert not (memes_dir / "新三国").exists()
