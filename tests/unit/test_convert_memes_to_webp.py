"""迁移脚本 convert_memes_to_webp.py 单元测试。

测试用 memes_dir = tmp_path/memes（memes 在 tmp_path 子目录），
使默认 backup 目录 tmp_path/memes_migrated_backup 落在 memes 外，
避免被 _collect_files.rglob 误扫。
"""

import importlib
from pathlib import Path

from PIL import Image

from bot.engine.metadata_store import MetadataStore


def _make_img(
    path: Path, mode: str = "RGB", color=(128, 64, 32), fmt: str = "JPEG"
) -> None:
    Image.new(mode, (50, 50), color=color).save(path, fmt)


def _run(memes_dir: Path, db_path: Path, dry_run: bool = False) -> tuple[int, int, int]:
    mod = importlib.import_module("scripts.convert_memes_to_webp")
    importlib.reload(mod)
    return mod.run_conversion(
        memes_dir=memes_dir, db_path=db_path, quality=85, dry_run=dry_run
    )


class TestConvertToWebp:
    def test_converts_jpg_and_updates_db(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
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

    def test_target_exists_appends_n(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        (memes / "a.webp").write_bytes(b"existing")
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        assert (memes / "a_1.webp").exists()

    def test_no_db_record_only_convert(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        assert (memes / "a.webp").exists()

    def test_backup_dir_holds_original(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        backup = tmp_path / "backup"
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        mod.run_conversion(
            memes_dir=memes,
            db_path=tmp_sqlite_path,
            quality=85,
            dry_run=False,
            backup_dir=backup,
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

    def test_gif_animated_converted(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        frames = [
            Image.new("RGB", (50, 50), color=(i * 80, 0, 0)).quantize(colors=256)
            for i in range(3)
        ]
        gif = memes / "a.gif"
        frames[0].save(
            gif, save_all=True, append_images=frames[1:], duration=100, loop=0
        )
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        with Image.open(memes / "a.webp") as w:
            assert w.format == "WEBP"
            assert getattr(w, "n_frames", 1) == 3

    def test_nested_collection_path_updates_relative_path(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
        """嵌套合集路径转换后 image_path 保持相对路径。"""
        memes = tmp_path / "memes"
        memes.mkdir()
        nested = memes / "新三国"
        nested.mkdir()
        png = nested / "a.png"
        _make_img(png, fmt="PNG")

        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        collection = md.create_collection("新三国")
        entry_id = md.add("新三国/a.png", "丞相", collection_id=collection.id)
        md.close()

        success, skipped, failed = _run(memes, tmp_sqlite_path)

        assert success == 1 and failed == 0
        assert not png.exists()
        assert (nested / "a.webp").exists()
        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        entry = md.get_entry(entry_id)
        assert entry is not None
        assert entry.image_path == "新三国/a.webp"
        md.close()

    def test_root_file_and_nested_collection_together(
        self, tmp_path: Path, tmp_sqlite_path: Path
    ) -> None:
        """同时转换根目录文件与嵌套合集文件。"""
        memes = tmp_path / "memes"
        memes.mkdir()
        root_jpg = memes / "root.jpg"
        _make_img(root_jpg)
        nested = memes / "新三国"
        nested.mkdir()
        nested_png = nested / "a.png"
        _make_img(nested_png, fmt="PNG")

        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        collection = md.create_collection("新三国")
        root_id = md.add("root.jpg", "根文本")
        nested_id = md.add("新三国/a.png", "合集文本", collection_id=collection.id)
        md.close()

        success, skipped, failed = _run(memes, tmp_sqlite_path)

        assert success == 2 and failed == 0
        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        root_entry = md.get_entry(root_id)
        nested_entry = md.get_entry(nested_id)
        assert root_entry is not None
        assert nested_entry is not None
        assert root_entry.image_path == "root.webp"
        assert nested_entry.image_path == "新三国/a.webp"
        md.close()
