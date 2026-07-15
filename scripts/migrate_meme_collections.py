"""表情包合集显式迁移脚本。

提供两个子命令：

- ``upgrade-schema``：将旧版 index.db（无 ``collection_id``/``local_id`` 字段）
  迁移到当前合集 Schema，同时把旧向量记录的 Chroma metadata 补为
  ``collection_id=0``；若已是当前 Schema 但 Chroma metadata 缺失或不同步，
  则仅补齐向量侧 metadata，不改 SQLite 结构。
- ``move-root``：将 memes 根目录（顶层平铺、未归入合集子目录）下已索引的
  表情包迁移到指定合集。目标不存在时自动新建合集与子目录。

运行方式：
    uv run python -m scripts.migrate_meme_collections upgrade-schema
    uv run python -m scripts.migrate_meme_collections upgrade-schema --dry-run
    uv run python -m scripts.migrate_meme_collections move-root 默认合集
    uv run python -m scripts.migrate_meme_collections move-root 3 --dry-run
    uv run python -m scripts.migrate_meme_collections \
        --db-path ./data/index.db --chroma-dir ./data/chroma move-root 搞笑

规则：
- 两个子命令均支持 ``--dry-run`` 预演，不修改任何数据、不创建备份。
- ``upgrade-schema`` 在迁移旧 Schema（会改写 SQLite 表结构）时用 SQLite Backup
  API 生成时间戳备份；当前 Schema 分支只补 Chroma metadata、不改 SQLite，
  故不生成备份。旧 Schema 迁移失败会回滚 Chroma 记录，并在外键检查失败时从
  备份恢复数据库。
- ``move-root`` 仅迁移 ``image_path`` 为 basename（即根目录平铺）且扩展名受
  支持的已索引条目；目标合集内已有相同文本时记为冲突并跳过；根目录存在但
  SQLite 无记录的受支持文件记为未索引跳过；单条迁移失败会回滚文件、SQLite
  与 Chroma 三处变更，并在新建合集但全部失败时撤销空合集与空目录。
- ``move-root`` 的 ``target`` 为纯数字时先按合集 ID 解析，ID 不存在再按名称
  匹配；非数字按名称精确匹配；名称须通过校验（非空、非 ``.``/``..``、不以
  ``.`` 开头、不含路径分隔符与空字符）。

注意：
- 运行前必须停止 Bot；脚本不会检测 Bot 进程是否存活。
- ``upgrade-schema`` 会直接操作 Chroma 目录，``move-root`` 会移动文件并改写
  SQLite，建议执行前确认无其他进程正在访问索引或 memes 目录。
"""

import argparse
import asyncio
import datetime
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from bot.config import CHROMA_DIR, INDEX_DB_PATH, MEMES_DIR
from bot.engine.metadata_store import MemeEntry, MetadataStore, create_current_schema
from bot.engine.types import MemeCollection, MemePublicId
from bot.engine.utils import resolve_unique_filename
from bot.engine.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    """upgrade-schema 子命令的执行结果。

    Attributes:
        upgraded_entries: 已完成 Schema 迁移的 SQLite 记录数。
        updated_vectors: 已更新 Chroma metadata 的向量数。
        backup_path: SQLite 备份路径；dry-run 或无备份场景为 None。
        already_current: 数据库已是当前版本且 Chroma metadata 完整。
    """

    upgraded_entries: int
    updated_vectors: int
    backup_path: Path | None
    already_current: bool = False


class MigrationError(RuntimeError):
    """显式迁移失败。"""


_NUMERIC_RE = re.compile(r"^[0-9]+$", re.ASCII)
_SUPPORTED_EXTENSIONS = {
    ".webp",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
}


@dataclass(frozen=True, slots=True)
class MoveRootResult:
    """move-root 子命令的执行结果。

    Attributes:
        moved: 成功迁移的文件数。
        conflicts: 因目标合集内文本冲突跳过的 (源文件名, 目标公开 ID) 列表。
        unindexed_skipped: 根目录有文件但 SQLite 无记录的受支持文件名列表。
        failed: 迁移失败并回滚的 (源文件名, 错误信息) 列表。
        backup_path: SQLite 备份路径；dry-run 或无备份场景为 None。
    """

    moved: int
    conflicts: list[tuple[str, MemePublicId]]
    unindexed_skipped: list[str]
    failed: list[tuple[str, str]]
    backup_path: Path | None = None


class InvalidCollectionNameError(ValueError):
    """合集名称不能映射为安全的单层目录名。"""


def validate_collection_name(raw: str) -> str:
    """校验并规范化合集名称。

    Args:
        raw: 用户输入的目标合集名称。

    Returns:
        去除首尾空格的合法名称。

    Raises:
        InvalidCollectionNameError: 名称为空、当前目录、父目录、隐藏名、含路径
            分隔符或空字符。
    """
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


def _resolve_target_collection(store: MetadataStore, raw: str) -> MemeCollection | None:
    """按 ID 或名称解析目标合集。

    纯数字（ASCII）先按 ID 解析，ID 不存在再按名称；非数字按名称精确匹配。

    Args:
        store: 已加载的 MetadataStore。
        raw: 用户输入的目标。

    Returns:
        已存在的合集；不存在时返回 None。
    """
    text = raw.strip()
    if _NUMERIC_RE.fullmatch(text):
        collection = store.get_collection(int(text))
        if collection is not None:
            return collection
    return store.get_collection_by_name(text)


def _is_supported_extension(path: Path) -> bool:
    """判断路径扩展名是否为受支持的图片格式。

    Args:
        path: 待判断路径。

    Returns:
        扩展名在支持集合中时返回 True。
    """
    return path.suffix.lower() in _SUPPORTED_EXTENSIONS


def _collect_root_candidates(store: MetadataStore, memes_dir: Path) -> list[MemeEntry]:
    """收集根目录下待迁移的已索引条目。

    候选条件：image_path 为 basename，且扩展名受支持。

    Args:
        store: 已加载的 MetadataStore。
        memes_dir: memes 根目录。

    Returns:
        按 image_path 升序排列的候选条目列表。
    """
    candidates: list[MemeEntry] = []
    for entry in store.get_all_entries().values():
        image_path = Path(entry.image_path)
        if image_path.name != entry.image_path:
            continue
        if not _is_supported_extension(image_path):
            continue
        candidates.append(entry)
    candidates.sort(key=lambda e: e.image_path)
    return candidates


def _collect_unindexed_root_files(store: MetadataStore, memes_dir: Path) -> list[str]:
    """收集根目录下受支持但 SQLite 无记录的文件名。

    Args:
        store: 已加载的 MetadataStore。
        memes_dir: memes 根目录。

    Returns:
        按字典序排列的未索引文件名列表。
    """
    indexed_names = {
        Path(entry.image_path).name
        for entry in store.get_all_entries().values()
        if Path(entry.image_path).name == entry.image_path
    }
    unindexed: list[str] = []
    if memes_dir.exists():
        for path in memes_dir.iterdir():
            if path.is_file() and _is_supported_extension(path):
                if path.name not in indexed_names:
                    unindexed.append(path.name)
    unindexed.sort()
    return unindexed


async def run_move_root(
    store: MetadataStore,
    vector_store: VectorStore,
    memes_dir: Path,
    target: str,
    dry_run: bool,
) -> MoveRootResult:
    """将根目录表情包迁移到指定合集。

    Args:
        store: 已加载的 MetadataStore。
        vector_store: 已加载的 VectorStore。
        memes_dir: memes 根目录。
        target: 目标合集名称或编号。
        dry_run: 为 True 时只预演，不修改数据。

    Returns:
        迁移结果。

    Raises:
        InvalidCollectionNameError: 目标名称非法。
    """
    name = validate_collection_name(target)
    collection = _resolve_target_collection(store, name)
    created_collection = False
    created_directory = False

    candidates = _collect_root_candidates(store, memes_dir)
    unindexed_skipped = _collect_unindexed_root_files(store, memes_dir)

    if not candidates:
        return MoveRootResult(
            moved=0,
            conflicts=[],
            unindexed_skipped=unindexed_skipped,
            failed=[],
        )

    if collection is None:
        if dry_run:
            # 预演新建空合集：无文本冲突，全部候选计入移动。
            return MoveRootResult(
                moved=len(candidates),
                conflicts=[],
                unindexed_skipped=unindexed_skipped,
                failed=[],
            )
        collection = store.create_collection(name)
        created_collection = True
        target_dir = memes_dir / collection.name
        target_dir_existed = target_dir.exists()
        target_dir.mkdir(parents=True, exist_ok=True)
        created_directory = not target_dir_existed
    else:
        target_dir = memes_dir / collection.name
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    conflicts: list[tuple[str, MemePublicId]] = []
    failed: list[tuple[str, str]] = []

    for entry in candidates:
        try:
            conflict_id = store.get_id_by_text(entry.text, collection_id=collection.id)
            if conflict_id is not None:
                conflict_entry = store.get_entry(conflict_id)
                public_id = (
                    conflict_entry.public_id
                    if conflict_entry is not None
                    else MemePublicId(collection.id, 0)
                )
                conflicts.append((Path(entry.image_path).name, public_id))
                continue

            if dry_run:
                moved_count += 1
                continue

            local_id = store.find_next_local_id(collection.id)
            target_path = resolve_unique_filename(
                target_dir, Path(entry.image_path).name, first_suffix=2
            )

            source_path = memes_dir / entry.image_path
            old_image_path = entry.image_path
            old_collection_id = entry.collection_id
            old_local_id = entry.local_id

            # 1. 移动文件
            shutil.move(str(source_path), str(target_path))
            new_relative = target_path.relative_to(memes_dir).as_posix()

            # 2. 更新 SQLite
            try:
                updated = store.update(
                    entry.id,
                    image_path=new_relative,
                    collection_id=collection.id,
                    local_id=local_id,
                )
                if not updated:
                    raise MigrationError(f"SQLite 中未找到条目 id={entry.id}")
            except Exception as exc:
                try:
                    shutil.move(str(target_path), str(source_path))
                except Exception as rollback_exc:
                    logger.error(
                        "回滚文件失败: %s -> %s: %s",
                        target_path,
                        source_path,
                        rollback_exc,
                    )
                raise MigrationError(f"更新 SQLite 失败: {exc}") from exc

            # 3. 更新 Chroma
            try:
                await vector_store.update_collection_id(entry.id, collection.id)
            except Exception as exc:
                try:
                    store.update(
                        entry.id,
                        image_path=old_image_path,
                        collection_id=old_collection_id,
                        local_id=old_local_id,
                    )
                except Exception as rollback_exc:
                    logger.error("回滚 SQLite 失败 id=%d: %s", entry.id, rollback_exc)
                try:
                    shutil.move(str(target_path), str(source_path))
                except Exception as rollback_exc:
                    logger.error(
                        "回滚文件失败: %s -> %s: %s",
                        target_path,
                        source_path,
                        rollback_exc,
                    )
                try:
                    await vector_store.update_collection_id(entry.id, old_collection_id)
                except Exception as chroma_rollback_exc:
                    logger.error(
                        "回滚 Chroma 失败 id=%d: %s",
                        entry.id,
                        chroma_rollback_exc,
                    )
                raise MigrationError(f"更新 Chroma 失败: {exc}") from exc

            moved_count += 1
        except Exception as exc:
            logger.error("迁移文件 %s 失败: %s", entry.image_path, exc)
            failed.append((Path(entry.image_path).name, str(exc)))

    if created_collection and moved_count == 0:
        store.delete_collection_and_reset_scopes(collection.id)
        if created_directory and target_dir.exists() and not any(target_dir.iterdir()):
            target_dir.rmdir()

    return MoveRootResult(
        moved=moved_count,
        conflicts=conflicts,
        unindexed_skipped=unindexed_skipped,
        failed=failed,
    )


async def run_move_root_paths(
    db_path: Path | str,
    chroma_dir: Path | str,
    memes_dir: Path | str,
    target: str,
    dry_run: bool,
) -> MoveRootResult:
    """打开 Store、创建备份并执行 move-root。

    Args:
        db_path: index.db 文件路径。
        chroma_dir: Chroma 数据目录。
        memes_dir: memes 根目录。
        target: 目标合集名称或编号。
        dry_run: 为 True 时只预演，不修改数据。

    Returns:
        迁移结果（含 backup_path）。
    """
    db_path = Path(db_path)
    chroma_dir = Path(chroma_dir)
    memes_dir = Path(memes_dir)

    store = MetadataStore(str(db_path))
    vector_store = VectorStore(str(chroma_dir))
    try:
        store.load()
        vector_store.load()

        backup_path: Path | None = None
        if not dry_run:
            backup_path = _make_backup_path(db_path)
            _backup_database(db_path, backup_path)
            logger.info("已创建 SQLite 备份: %s", backup_path)

        result = await run_move_root(store, vector_store, memes_dir, target, dry_run)
        if backup_path is not None:
            result = replace(result, backup_path=backup_path)
        return result
    finally:
        store.close()
        vector_store.close()


def _report_move_root(result: MoveRootResult) -> None:
    """输出 move-root 结果摘要。

    Args:
        result: move-root 执行结果。
    """
    logger.info(
        "move-root 完成: moved=%d, conflicts=%d, unindexed_skipped=%d, failed=%d",
        result.moved,
        len(result.conflicts),
        len(result.unindexed_skipped),
        len(result.failed),
    )
    if result.backup_path:
        logger.info("SQLite 备份: %s", result.backup_path)

    for label, items, formatter in (
        ("冲突跳过", result.conflicts, lambda item: f"{item[0]} -> {item[1]}"),
        ("未索引跳过", result.unindexed_skipped, lambda item: item),
        ("失败", result.failed, lambda item: f"{item[0]}: {item[1]}"),
    ):
        if not items:
            continue
        logger.warning("%s %d 项（最多显示前 10 项）:", label, len(items))
        for item in items[:10]:
            logger.warning("  %s", formatter(item))


def detect_schema(conn: sqlite3.Connection) -> Literal["legacy", "current", "unknown"]:
    """检测 index.db 的 Schema 状态。

    Args:
        conn: 已打开的 SQLite 连接。

    Returns:
        ``"current"`` 表示当前合集 Schema；``"legacy"`` 表示旧 Schema；
        ``"unknown"`` 表示无法识别。
    """
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "meme" not in tables:
        return "unknown"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(meme)")}
    if {"collection_id", "local_id"}.issubset(columns):
        return "current"
    if {"id", "image_path", "text", "speaker"}.issubset(columns):
        return "legacy"
    return "unknown"


def _backup_database(source: Path, target: Path) -> None:
    """使用 SQLite Backup API 创建数据库备份。

    Args:
        source: 源数据库路径。
        target: 备份文件路径。
    """
    with sqlite3.connect(source) as src_conn:
        with sqlite3.connect(target) as dst_conn:
            src_conn.backup(dst_conn)


def _restore_database(backup: Path, target: Path) -> None:
    """使用 SQLite Backup API 从备份恢复数据库。

    Args:
        backup: 备份文件路径。
        target: 目标数据库路径。
    """
    with sqlite3.connect(backup) as src_conn:
        with sqlite3.connect(target) as dst_conn:
            src_conn.backup(dst_conn)


def _make_backup_path(db_path: Path) -> Path:
    """生成基于时间戳的备份路径，若冲突则追加计数器。

    Args:
        db_path: 数据库路径。

    Returns:
        形如 ``index.db.YYYYMMDDHHMMSS.bak`` 的备份路径。
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    candidate = db_path.parent / f"{db_path.name}.{timestamp}.bak"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = db_path.parent / f"{db_path.name}.{timestamp}_{counter}.bak"
        if not candidate.exists():
            return candidate
        counter += 1


def _read_current_collection_ids(conn: sqlite3.Connection) -> dict[int, int]:
    """读取当前 Schema 下各内部 ID 所属的合集编号。

    Args:
        conn: 已打开的连接，Schema 须为当前版本。

    Returns:
        内部 ID 到合集编号的映射。
    """
    rows = conn.execute("SELECT id, collection_id FROM meme ORDER BY id")
    return {int(row["id"]): int(row["collection_id"]) for row in rows}


async def _upgrade_current_schema(
    conn: sqlite3.Connection,
    chroma_dir: Path,
    dry_run: bool,
) -> UpgradeResult:
    """处理当前 Schema 但 Chroma metadata 缺失或不同步的情况。

    Args:
        conn: 已打开的当前版本 SQLite 连接。
        chroma_dir: Chroma 数据目录。
        dry_run: 为 True 时只统计不修改。

    Returns:
        升级结果。

    Raises:
        MigrationError: Chroma 更新失败。
    """
    expected = _read_current_collection_ids(conn)
    if not expected:
        return UpgradeResult(
            upgraded_entries=0,
            updated_vectors=0,
            backup_path=None,
            already_current=True,
        )

    vector_store = VectorStore(str(chroma_dir))
    vector_store.load()
    try:
        actual = await vector_store.get_collection_ids()
        missing: list[tuple[int, int]] = []
        for entry_id, collection_id in expected.items():
            actual_id = actual.get(entry_id)
            if actual_id is None or actual_id != collection_id:
                missing.append((entry_id, collection_id))
        if not missing:
            return UpgradeResult(
                upgraded_entries=0,
                updated_vectors=0,
                backup_path=None,
                already_current=True,
            )
        if dry_run:
            return UpgradeResult(
                upgraded_entries=0,
                updated_vectors=len(missing),
                backup_path=None,
                already_current=False,
            )

        entry_ids = [entry_id for entry_id, _ in missing]
        old_records = await vector_store.snapshot_records(entry_ids)
        try:
            for entry_id, collection_id in missing:
                await vector_store.update_collection_id(entry_id, collection_id)
        except Exception as exc:
            await vector_store.restore_records(old_records)
            raise MigrationError(f"Chroma metadata 更新失败: {exc}") from exc

        return UpgradeResult(
            upgraded_entries=0,
            updated_vectors=len(missing),
            backup_path=None,
            already_current=False,
        )
    finally:
        vector_store.close()


async def _upgrade_legacy_schema(
    conn: sqlite3.Connection,
    chroma_dir: Path,
    dry_run: bool,
    db_path: Path,
) -> UpgradeResult:
    """将旧 Schema 迁移到当前 Schema 并补 Chroma metadata。

    Args:
        conn: 已打开的旧版本 SQLite 连接。
        chroma_dir: Chroma 数据目录。
        dry_run: 为 True 时只统计不修改。
        db_path: 数据库文件路径，用于生成备份。

    Returns:
        升级结果。

    Raises:
        MigrationError: Schema 迁移或 Chroma 更新失败。
    """
    legacy_rows = list(conn.execute("SELECT id, image_path, text, speaker FROM meme"))
    if dry_run:
        return UpgradeResult(
            upgraded_entries=len(legacy_rows),
            updated_vectors=0,
            backup_path=None,
            already_current=False,
        )

    backup_path = _make_backup_path(db_path)
    _backup_database(db_path, backup_path)
    logger.info("已创建 SQLite 备份: %s", backup_path)

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
        "INSERT INTO meme_tag (meme_id, tag) SELECT meme_id, tag FROM meme_tag_legacy"
    )

    vector_store = VectorStore(str(chroma_dir))
    vector_store.load()
    try:
        entry_ids = [int(row["id"]) for row in legacy_rows]
        old_records = await vector_store.snapshot_records(entry_ids)
        if {record.entry_id for record in old_records} != set(entry_ids):
            conn.rollback()
            raise MigrationError("SQLite 与 Chroma ID 不一致，拒绝迁移")

        try:
            for entry_id in entry_ids:
                await vector_store.update_collection_id(entry_id, 0)
        except Exception as exc:
            await vector_store.restore_records(old_records)
            conn.rollback()
            raise MigrationError(f"Chroma metadata 更新失败: {exc}") from exc

        try:
            conn.execute("DROP TABLE meme_tag_legacy")
            conn.execute("DROP TABLE meme_legacy")
            conn.commit()
        except Exception as exc:
            await vector_store.restore_records(old_records)
            raise MigrationError(f"SQLite 提交失败: {exc}") from exc

        conn.execute("PRAGMA foreign_keys = ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            await vector_store.restore_records(old_records)
            conn.close()
            _restore_database(backup_path, db_path)
            raise MigrationError(f"外键检查失败: {violations!r}")
    finally:
        vector_store.close()

    return UpgradeResult(
        upgraded_entries=len(legacy_rows),
        updated_vectors=len(entry_ids),
        backup_path=backup_path,
        already_current=False,
    )


async def run_upgrade_schema(
    db_path: Path | str,
    chroma_dir: Path | str,
    dry_run: bool,
) -> UpgradeResult:
    """执行 upgrade-schema 迁移核心。

    Args:
        db_path: index.db 文件路径。
        chroma_dir: Chroma 数据目录。
        dry_run: 为 True 时只统计不修改。

    Returns:
        升级结果。

    Raises:
        MigrationError: 未知 Schema 或迁移失败。
    """
    db_path = Path(db_path)
    chroma_dir = Path(chroma_dir)

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        schema = detect_schema(conn)
        if schema == "unknown":
            raise MigrationError("无法识别的 index.db Schema")
        if schema == "current":
            return await _upgrade_current_schema(conn, chroma_dir, dry_run)
        return await _upgrade_legacy_schema(conn, chroma_dir, dry_run, db_path)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="migrate_meme_collections",
        description="表情包合集显式迁移工具。",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=INDEX_DB_PATH,
        help="index.db 路径",
    )
    parser.add_argument(
        "--chroma-dir",
        type=Path,
        default=CHROMA_DIR,
        help="Chroma 数据目录",
    )
    parser.add_argument(
        "--memes-dir",
        type=Path,
        default=MEMES_DIR,
        help="memes 目录",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出调试日志",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    upgrade_parser = subparsers.add_parser(
        "upgrade-schema",
        help="升级旧 Schema 到当前版本",
    )
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预演，不修改数据",
    )
    move_root_parser = subparsers.add_parser(
        "move-root",
        help="将根目录表情包迁移到指定合集",
    )
    move_root_parser.add_argument(
        "target",
        help="目标合集名称或编号",
    )
    move_root_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预演，不修改数据",
    )
    return parser


def main() -> int:
    """命令行入口。

    Returns:
        成功返回 0，迁移失败返回 1。
    """
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    logger.warning("请确保 Bot 已停止运行；脚本不会自动检测 Bot 进程。")

    try:
        if args.command == "upgrade-schema":
            result = asyncio.run(
                run_upgrade_schema(args.db_path, args.chroma_dir, args.dry_run)
            )
            logger.info("升级结果：%s", result)
        elif args.command == "move-root":
            result = asyncio.run(
                run_move_root_paths(
                    args.db_path,
                    args.chroma_dir,
                    args.memes_dir,
                    args.target,
                    args.dry_run,
                )
            )
            _report_move_root(result)
            if result.failed:
                return 1
    except (MigrationError, InvalidCollectionNameError) as exc:
        logger.error("迁移失败：%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
