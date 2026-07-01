"""speaker.txt → data/index.db 的 meme.speaker 列填充脚本（手动运行）。

读取项目根目录下的 speaker.txt（每行格式：说话人,图片id1,图片id2,...），
按 id 将说话人写入 data/index.db 的 meme.speaker 列。

运行前会用 SQLite backup API 将原数据库备份为 data/index.db.bak，确保可回滚。

运行方式：
    uv run python -m scripts.import_speaker_to_db
    uv run python -m scripts.import_speaker_to_db --speaker speaker.txt --db data/index.db

规则：
- 重复说话人（如「庞统」）后者覆盖前者；
- 跳过非数据行（以 # 或 TODO 开头、或首个字段为空的行）；
- 忽略末尾多余逗号产生的空字段；
- 仅更新 meme 表中已存在的 id，越界 id 记入校验报告。
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from bot.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


def _backup_database(db_path: Path, bak_path: Path) -> None:
    """用 SQLite online backup API 将 db_path 备份为 bak_path（可回滚）。"""
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(bak_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    logger.info("已备份数据库：%s → %s", db_path, bak_path)


def _parse_speaker_file(
    speaker_path: Path,
) -> tuple[dict[int, str], list[str], list[str]]:
    """解析 speaker.txt。

    Args:
        speaker_path: speaker.txt 路径。

    Returns:
        元组 (id_to_speaker, skipped_lines, invalid_ids)：
        - id_to_speaker: 图片 id → 说话人（重复行后者覆盖前者）；
        - skipped_lines: 被跳过的非数据行原文；
        - invalid_ids: 无法解析为整数的 id 字段原文。
    """
    id_to_speaker: dict[int, str] = {}
    skipped_lines: list[str] = []
    invalid_ids: list[str] = []

    with speaker_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            # 跳过空行、注释行、TODO 等
            if not stripped or stripped.startswith("#") or stripped.upper().startswith("TODO"):
                if stripped:
                    skipped_lines.append(line)
                continue

            parts = line.split(",")
            speaker = parts[0].strip()
            if not speaker:
                skipped_lines.append(line)
                continue

            # 从第二个字段开始解析图片 id（忽略末尾空字段）
            for field in parts[1:]:
                field = field.strip()
                if not field:
                    continue
                try:
                    image_id = int(field)
                except ValueError:
                    invalid_ids.append(field)
                    continue
                # 后者覆盖前者
                id_to_speaker[image_id] = speaker

    return id_to_speaker, skipped_lines, invalid_ids


def _apply_speakers(
    db_path: Path, id_to_speaker: dict[int, str]
) -> tuple[int, int, list[int]]:
    """将说话人写入 meme.speaker 列。

    Args:
        db_path: 数据库路径。
        id_to_speaker: 图片 id → 说话人。

    Returns:
        元组 (updated, missing, missing_ids)：
        - updated: 实际更新的行数；
        - missing: speaker.txt 中存在但 meme 表中不存在的 id 数；
        - missing_ids: 缺失的具体 id 列表。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        existing_ids: set[int] = {
            row[0] for row in conn.execute("SELECT id FROM meme").fetchall()
        }

        missing_ids = [i for i in id_to_speaker if i not in existing_ids]
        present = {i: s for i, s in id_to_speaker.items() if i in existing_ids}

        conn.executemany(
            "UPDATE meme SET speaker = ? WHERE id = ?",
            [(speaker, image_id) for image_id, speaker in present.items()],
        )
        conn.commit()
        updated = conn.total_changes
    finally:
        conn.close()

    return updated, len(missing_ids), missing_ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将 speaker.txt 内容写入 data/index.db 的 meme.speaker 列。"
    )
    parser.add_argument(
        "--speaker",
        default=str(PROJECT_ROOT / "speaker.txt"),
        help="speaker.txt 路径（默认：项目根目录下 speaker.txt）。",
    )
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT / "data" / "index.db"),
        help="index.db 路径（默认：data/index.db）。",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="跳过备份步骤（默认会生成 index.db.bak）。",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    speaker_path = Path(args.speaker)
    db_path = Path(args.db)

    if not speaker_path.exists():
        logger.error("未找到 speaker.txt：%s", speaker_path)
        return 1
    if not db_path.exists():
        logger.error("未找到数据库：%s", db_path)
        return 1

    # 备份
    if not args.no_backup:
        bak_path = db_path.with_suffix(db_path.suffix + ".bak")
        _backup_database(db_path, bak_path)

    # 解析
    id_to_speaker, skipped_lines, invalid_ids = _parse_speaker_file(speaker_path)
    logger.info(
        "解析完成：说话人映射 %d 条，跳过非数据行 %d 行，非法 id %d 个",
        len(id_to_speaker),
        len(skipped_lines),
        len(invalid_ids),
    )

    # 写入
    updated, missing_count, missing_ids = _apply_speakers(db_path, id_to_speaker)
    logger.info("写入完成：实际更新 %d 行，越界 id %d 个", updated, missing_count)

    # 报告
    if skipped_lines:
        print("\n跳过的非数据行：")
        for line in skipped_lines:
            print(f"  {line}")
    if invalid_ids:
        print("\n无法解析为整数的 id 字段：")
        for field in invalid_ids:
            print(f"  {field}")
    if missing_ids:
        print("\nspeaker.txt 中存在但 meme 表中不存在的 id：")
        for image_id in missing_ids:
            print(f"  {image_id} → {id_to_speaker[image_id]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
