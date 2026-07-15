"""将 memes/ 下的非 WebP 图片批量转为 WebP 并更新 index.db。

转换规则：
- 使用 Pillow 打开原图，保存为有损 WebP（默认 quality=85）。
- 透明通道保留（P/RGBA 保持 RGBA）；GIF 动图保留 duration/loop 转 animated WebP。
- 强制转换不比较体积。
- 目标 .webp 已存在且非当前源文件时追加 _n 序号。
- 更新 sqlite image_path；DB 无记录则仅转文件+备份。
- 原文件移到 --backup-dir（默认 memes_convert_backup/）。
- 不重新 OCR/embed，不动 chroma/meme_tag。

命令行示例：
    uv run python -m scripts.convert_memes_to_webp
    uv run python -m scripts.convert_memes_to_webp --quality 90 --dry-run
    uv run python -m scripts.convert_memes_to_webp --memes-dir ./memes --db-path ./data/index.db

注意：
    为避免 sqlite 写锁冲突，建议在 Bot 未运行时执行此脚本。
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from bot.config import INDEX_DB_PATH, MEMES_DIR
from bot.engine.metadata_store import DuplicateEntryError, MetadataStore
from bot.engine.utils import resolve_unique_filename

logger = logging.getLogger(__name__)

_CONVERTIBLE = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}


def _convert_to_webp(src: Path, quality: int) -> Path:
    """将单张图片转为 WebP，返回新路径（不改名原文件，不删原文件）。

    Args:
        src: 源图片路径。
        quality: WebP 质量。

    Returns:
        生成的 WebP 文件路径。
    """
    target = resolve_unique_filename(src.parent, f"{src.stem}.webp")
    img = Image.open(src)
    try:
        save_kwargs: dict[str, Any] = {
            "format": "WEBP",
            "quality": quality,
            "method": 6,
        }
        n_frames: int = getattr(img, "n_frames", 1)
        if n_frames > 1:
            frames: list[Image.Image] = []
            for i in range(n_frames):
                img.seek(i)
                frames.append(img.copy())
            if "duration" in img.info:
                save_kwargs["duration"] = img.info["duration"]
            if "loop" in img.info:
                save_kwargs["loop"] = img.info["loop"]
            frames[0].save(
                target, append_images=frames[1:], save_all=True, **save_kwargs
            )
            for f in frames:
                f.close()
        else:
            save_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
            save_img.save(target, **save_kwargs)
    finally:
        img.close()
    return target


def _collect_files(memes_dir: Path, include_archives: bool) -> list[Path]:
    """收集待转换文件。

    Args:
        memes_dir: 表情包目录。
        include_archives: 是否包含归档目录。

    Returns:
        待转换文件路径列表（按路径升序）。
    """
    dirs = [memes_dir]
    if include_archives:
        for name in ("memes_deleted", "memes_replaced", "meme_no_text"):
            d = memes_dir.parent / name
            if d.exists():
                dirs.append(d)
    files: list[Path] = []
    for d in dirs:
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in _CONVERTIBLE:
                files.append(p)
    return sorted(files)


def run_conversion(
    memes_dir: Path,
    db_path: Path,
    quality: int,
    dry_run: bool,
    include_archives: bool = False,
    backup_dir: Path | None = None,
) -> tuple[int, int, int]:
    """执行批量 WebP 转换。

    Args:
        memes_dir: 表情包目录。
        db_path: index.db 路径。
        quality: WebP 质量。
        dry_run: 为 True 时只打印不修改。
        include_archives: 是否处理归档目录。
        backup_dir: 原文件备份目录；None 时默认 memes_dir 同级 memes_convert_backup/。

    Returns:
        (成功数, 跳过数, 失败数)。
    """
    if dry_run:
        logger.info("DRY-RUN 模式：不会修改文件或数据库")
    if not memes_dir.exists():
        logger.error("表情包目录不存在: %s", memes_dir)
        return 0, 0, 0

    files = _collect_files(memes_dir, include_archives)
    if not files:
        logger.info("未找到待转换的非 WebP 图片")
        return 0, 0, 0

    backup = backup_dir or (memes_dir.parent / "memes_convert_backup")
    if not dry_run:
        backup.mkdir(parents=True, exist_ok=True)

    metadata_store: MetadataStore | None = None
    if not dry_run:
        metadata_store = MetadataStore(str(db_path))
        metadata_store.load()

    success = skipped = failed = 0

    try:
        for src in files:
            old_relative = (
                src.relative_to(memes_dir).as_posix()
                if not include_archives
                else src.relative_to(memes_dir.parent).as_posix()
            )
            rel = old_relative
            logger.info("转换: %s", rel)

            if dry_run:
                success += 1
                continue

            assert metadata_store is not None

            # a. 转换
            try:
                webp_path = _convert_to_webp(src, quality)
            except Exception as exc:
                logger.error("转换失败，跳过: %s - %s", rel, exc)
                failed += 1
                continue

            # 判断是否在 memes_dir 内（扁平结构下 src.parent == memes_dir）；
            # 归档目录图仅转文件+备份，不查 sqlite（避免误匹配 memes 同名记录）。
            try:
                src.relative_to(memes_dir)
                in_memes = True
            except ValueError:
                in_memes = False

            new_relative = webp_path.relative_to(memes_dir).as_posix()
            db_updated = False
            if not in_memes:
                logger.info("归档目录图仅转换备份，不更新 sqlite: %s", rel)
                db_updated = True
            else:
                try:
                    entry = metadata_store.get_by_filename(old_relative)
                    if entry is None:
                        logger.info("index.db 中无对应记录: %s", old_relative)
                        db_updated = True
                    else:
                        db_updated = metadata_store.update(
                            entry.id, image_path=new_relative
                        )
                        if not db_updated:
                            logger.warning("更新 image_path 失败，id=%s", entry.id)
                except DuplicateEntryError as exc:
                    logger.error("image_path UNIQUE 冲突，跳过: %s - %s", rel, exc)
                    webp_path.unlink(missing_ok=True)
                    failed += 1
                    continue
                except Exception as exc:
                    logger.error("数据库更新失败: %s - %s", rel, exc)
                    db_updated = False

            if not db_updated:
                webp_path.unlink(missing_ok=True)
                failed += 1
                continue

            # c. 原文件移到 backup
            try:
                dst = resolve_unique_filename(backup, src.name)
                shutil.move(str(src), str(dst))
            except Exception as exc:
                logger.warning(
                    "移备份失败（索引已一致，原文件暂留）: %s - %s", rel, exc
                )

            success += 1
    finally:
        if metadata_store is not None:
            metadata_store.close()

    return success, skipped, failed


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="将 memes/ 下的非 WebP 图片批量转为 WebP 并更新 index.db"
    )
    parser.add_argument(
        "--memes-dir",
        type=Path,
        default=MEMES_DIR,
        help=f"表情包目录（默认 {MEMES_DIR}）",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=INDEX_DB_PATH,
        help=f"sqlite 路径（默认 {INDEX_DB_PATH}）",
    )
    parser.add_argument(
        "--quality", type=int, default=85, help="WebP 质量（1-100，默认 85）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="模拟运行，不修改文件和数据库"
    )
    parser.add_argument(
        "--include-archives",
        action="store_true",
        help="同时处理 memes_deleted/memes_replaced/meme_no_text",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="原文件备份目录（默认 memes_convert_backup/）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
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
        include_archives=args.include_archives,
        backup_dir=args.backup_dir,
    )
    print(f"完成：成功 {success}，跳过 {skipped}，失败 {failed}")
    print("提示：建议在 Bot 未运行时执行，避免 sqlite 写锁冲突。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
