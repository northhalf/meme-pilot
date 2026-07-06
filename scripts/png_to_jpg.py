"""将 memes/ 下的 PNG 图片批量转为 JPG 并更新 index.db。

转换规则：
- 使用 Pillow 打开 PNG，按指定质量保存为 JPG（默认 85）。
- 透明通道默认以白色背景合成。
- 转换成功后删除原 PNG。
- 在 index.db 中把对应记录的 image_path 从 .png 改为 .jpg；
  若某张 PNG 在 index.db 中无记录，仅转换文件，不报错。
- 单张图片转换失败、目标 JPG 已存在或 DB 更新失败时跳过该图片，
  并尝试清理中间文件。

命令行示例：
    uv run python scripts/png_to_jpg.py
    uv run python scripts/png_to_jpg.py --quality 90 --dry-run
    uv run python scripts/png_to_jpg.py --memes-dir ./memes --db-path ./data/index.db

注意：
    为避免 sqlite 写锁冲突，建议在 Bot 未运行时执行此脚本。
"""

import argparse
import logging
import sys
from pathlib import Path

from PIL import Image

from bot.config import INDEX_DB_PATH, MEMES_DIR
from bot.engine.metadata_store import MetadataStore

logger = logging.getLogger(__name__)


def _convert_png_to_jpg(png_path: Path, quality: int) -> Path:
    """将单张 PNG 转为 JPG，返回写入的 JPG 路径。

    Args:
        png_path: 源 PNG 文件路径。
        quality: JPG 质量（1-100）。

    Returns:
        生成的 JPG 文件路径。
    """
    jpg_path = png_path.with_suffix(".jpg")

    with Image.open(png_path) as img:
        # 处理透明通道：以白色背景合成，避免直接转 RGB 出现黑边
        if img.mode in ("RGBA", "LA", "P"):
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            if img.mode in ("RGBA", "LA"):
                rgb_img.paste(img, mask=img.split()[-1])
            img = rgb_img
        else:
            img = img.convert("RGB")

        img.save(jpg_path, format="JPEG", quality=quality, optimize=True)

    return jpg_path


def _collect_png_files(memes_dir: Path) -> list[Path]:
    """收集 memes 目录下所有 PNG 文件（大小写不敏感）。

    Args:
        memes_dir: 表情包目录。

    Returns:
        PNG 文件路径列表（按路径升序排列）。
    """
    return sorted(
        p for p in memes_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".png"
    )


def run_conversion(
    memes_dir: Path,
    db_path: Path,
    quality: int,
    dry_run: bool,
) -> tuple[int, int, int]:
    """执行 PNG → JPG 批量转换。

    Args:
        memes_dir: 表情包目录。
        db_path: index.db 路径。
        quality: JPG 质量。
        dry_run: 为 True 时只打印操作，不实际修改文件和数据库。

    Returns:
        (成功数, 跳过数, 失败数)。
    """
    if dry_run:
        logger.info("DRY-RUN 模式：不会修改文件或数据库")

    if not memes_dir.exists():
        logger.error("表情包目录不存在: %s", memes_dir)
        return 0, 0, 0

    png_files = _collect_png_files(memes_dir)
    if not png_files:
        logger.info("未找到 PNG 文件")
        return 0, 0, 0

    metadata_store: MetadataStore | None = None
    if not dry_run:
        metadata_store = MetadataStore(str(db_path))
        metadata_store.load()

    success = 0
    skipped = 0
    failed = 0

    try:
        for png_path in png_files:
            rel_path = png_path.relative_to(memes_dir).as_posix()
            jpg_path = png_path.with_suffix(".jpg")

            if jpg_path.exists():
                logger.warning(
                    "目标 JPG 已存在，跳过: %s -> %s",
                    rel_path,
                    jpg_path.name,
                )
                skipped += 1
                continue

            logger.info("转换: %s -> %s", rel_path, jpg_path.name)

            if dry_run:
                success += 1
                continue

            assert metadata_store is not None

            try:
                _convert_png_to_jpg(png_path, quality)
            except Exception as exc:
                logger.error("PNG 转换失败，跳过: %s - %s", rel_path, exc)
                failed += 1
                continue

            new_rel_path = jpg_path.relative_to(memes_dir).as_posix()
            db_updated = False
            try:
                entry = metadata_store.get_by_filename(rel_path)
                if entry is None:
                    logger.info("index.db 中无对应记录: %s", rel_path)
                    db_updated = True
                else:
                    db_updated = metadata_store.update(
                        entry.id, image_path=new_rel_path
                    )
                    if not db_updated:
                        logger.warning("更新 image_path 失败，id=%s", entry.id)
            except Exception as exc:
                logger.error("数据库更新失败: %s - %s", rel_path, exc)
                db_updated = False

            if not db_updated:
                # 回滚：删除生成的 JPG，保留原 PNG
                try:
                    jpg_path.unlink(missing_ok=True)
                except Exception as unlink_exc:
                    logger.error(
                        "清理失败 JPG 文件出错: %s - %s", jpg_path, unlink_exc
                    )
                failed += 1
                continue

            try:
                png_path.unlink()
            except Exception as exc:
                logger.error(
                    "删除原 PNG 失败: %s - %s，但 JPG 与 DB 已更新", rel_path, exc
                )

            success += 1
    finally:
        if metadata_store is not None:
            metadata_store.close()

    return success, skipped, failed


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="将 memes/ 下的 PNG 转换为 JPG 并更新 index.db"
    )
    parser.add_argument(
        "--memes-dir",
        type=Path,
        default=MEMES_DIR,
        help=f"表情包目录路径（默认 {MEMES_DIR}）",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=INDEX_DB_PATH,
        help=f"sqlite 数据库路径（默认 {INDEX_DB_PATH}）",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=85,
        help="JPG 压缩质量（1-100，默认 85）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟运行，不修改文件和数据库",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出 DEBUG 级别日志",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if not (1 <= args.quality <= 100):
        logger.error("quality 必须在 1-100 之间")
        return 1

    success, skipped, failed = run_conversion(
        memes_dir=args.memes_dir,
        db_path=args.db_path,
        quality=args.quality,
        dry_run=args.dry_run,
    )

    print(f"完成：成功 {success}，跳过 {skipped}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
