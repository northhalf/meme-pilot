"""迁移脚本 convert_memes_to_webp.py 单元测试。

测试用 memes_dir = tmp_path/memes（memes 在 tmp_path 子目录），
使默认 backup 目录 tmp_path/memes_migrated_backup 落在 memes 外，
避免被 _collect_files.rglob 误扫。
"""

import importlib
from pathlib import Path

from PIL import Image

from bot.engine.metadata_store import MetadataStore


def _make_img(path: Path, mode: str = "RGB", color=(128, 64, 32), fmt: str = "JPEG") -> None:
    Image.new(mode, (50, 50), color=color).save(path, fmt)


def _run(memes_dir: Path, db_path: Path, dry_run: bool = False) -> tuple[int, int, int]:
    mod = importlib.import_module("scripts.convert_memes_to_webp")
    importlib.reload(mod)
    return mod.run_conversion(
        memes_dir=memes_dir, db_path=db_path, quality=85, dry_run=dry_run
    )


class TestConvertToWebp:
    def test_converts_jpg_and_updates_db(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        md.add("a.jpg", "加班")
        md.close()

        success, skipped, failed = _run(memes, tmp_sqlite_path)

        assert success == 1 and failed == 0
        assert not jpg.exists()
        assert (memes / "a.webp").exists()
        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        assert md.get_by_filename("a.webp") is not None
        assert md.get_by_filename("a.jpg") is None
        md.close()

    def test_dry_run_no_change(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        success, _, _ = mod.run_conversion(memes, tmp_sqlite_path, 85, True)
        assert success == 1
        assert jpg.exists()
        assert not (memes / "a.webp").exists()

    def test_target_exists_appends_n(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        (memes / "a.webp").write_bytes(b"existing")
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        assert (memes / "a_1.webp").exists()

    def test_no_db_record_only_convert(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        assert (memes / "a.webp").exists()

    def test_backup_dir_holds_original(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        backup = tmp_path / "backup"
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        mod.run_conversion(
            memes_dir=memes, db_path=tmp_sqlite_path, quality=85,
            dry_run=False, backup_dir=backup,
        )
        assert (backup / "a.jpg").exists()

    def test_idempotent_second_run(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        _run(memes, tmp_sqlite_path)
        success, _, _ = _run(memes, tmp_sqlite_path)
        assert success == 0

    def test_gif_animated_converted(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        frames = [
            Image.new("RGB", (50, 50), color=(i * 80, 0, 0)).quantize(colors=256)
            for i in range(3)
        ]
        gif = memes / "a.gif"
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        with Image.open(memes / "a.webp") as w:
            assert w.format == "WEBP"
            assert getattr(w, "n_frames", 1) == 3

    def test_include_archives_no_sqlite_update(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        """归档目录图仅转文件+备份，不更新 sqlite。"""
        memes = tmp_path / "memes"
        memes.mkdir()
        deleted = tmp_path / "memes_deleted"
        deleted.mkdir()
        jpg = deleted / "arch.jpg"
        _make_img(jpg)
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        success, _, failed = mod.run_conversion(
            memes, tmp_sqlite_path, 85, False, include_archives=True
        )
        assert success == 1 and failed == 0
        assert (deleted / "arch.webp").exists()
        assert not jpg.exists()
