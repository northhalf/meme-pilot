"""ImageOptimizer 单元测试。"""

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from bot.engine.image_optimizer import ImageOptimizer, OptimizeResult


class TestOptimizeResult:
    """OptimizeResult 数据类测试。"""

    def test_create(self) -> None:
        r = OptimizeResult(original_size=1000, optimized_size=800, saved=200)
        assert r.original_size == 1000
        assert r.optimized_size == 800
        assert r.saved == 200
        assert r.skipped is False

    def test_skipped(self) -> None:
        r = OptimizeResult(
            original_size=1000, optimized_size=1000, saved=0, skipped=True
        )
        assert r.skipped is True

    def test_frozen(self) -> None:
        r = OptimizeResult(original_size=1000, optimized_size=800, saved=200)
        with pytest.raises(AttributeError):
            r.original_size = 500  # type: ignore[misc]


class TestImageOptimizerUnsupported:
    """不支持的格式测试。"""

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        fake = tmp_path / "test.txt"
        fake.write_text("hello")
        optimizer = ImageOptimizer()
        with pytest.raises(ValueError, match="不支持的图片格式"):
            asyncio.run(optimizer.optimize(fake))


class TestImageOptimizerEdgeCases:
    """边界条件测试。"""

    def test_bmp_skipped(self, tmp_path: Path) -> None:
        bmp = tmp_path / "test.bmp"
        bmp.write_bytes(b"\x42\x4d" + b"\x00" * 100)
        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(bmp))
        assert result.skipped is True
        assert result.saved == 0
        assert result.original_size == result.optimized_size

    def test_file_not_found_raises(self) -> None:
        optimizer = ImageOptimizer()
        with pytest.raises(FileNotFoundError, match="图片文件不存在"):
            asyncio.run(optimizer.optimize("/nonexistent/test.jpg"))


class TestCompressJpeg:
    """JPEG 压缩测试。"""

    def test_jpeg_compress(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (200, 200), color=(128, 64, 32))
        jpg = tmp_path / "test.jpg"
        img.save(jpg, "JPEG", quality=100)
        original_size = jpg.stat().st_size

        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(jpg))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.saved >= 0
        assert result.skipped is False
        assert jpg.exists()

    def test_jpeg_strips_metadata(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        jpg = tmp_path / "meta.jpg"
        img.save(jpg, "JPEG", quality=100, exif=b"fake exif data here")
        original_size = jpg.stat().st_size

        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(jpg))

        assert result.optimized_size < original_size or result.saved >= 0
        with Image.open(jpg) as optimized:
            assert optimized.info.get("exif") is None


class TestCompressPng:
    """PNG 压缩测试。"""

    def test_png_compress(self, tmp_path: Path) -> None:
        img = Image.new("RGBA", (200, 200), color=(128, 64, 32, 255))
        png = tmp_path / "test.png"
        img.save(png, "PNG")
        original_size = png.stat().st_size

        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(png))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.skipped is False


class TestCompressWebp:
    """WebP 压缩测试。"""

    def test_webp_compress(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (200, 200), color=(100, 150, 200))
        webp = tmp_path / "test.webp"
        img.save(webp, "WEBP")
        original_size = webp.stat().st_size

        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(webp))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.skipped is False


class TestCompressGif:
    """GIF 压缩测试。"""

    def test_gif_compress(self, tmp_path: Path) -> None:
        # 需要足够大的差异帧，否则 Pillow 会优化为单帧
        frames = []
        for i in range(3):
            frame = Image.new("RGB", (100, 100), color=(i * 80, i * 40, 0))
            frames.append(frame.quantize(colors=256))
        gif = tmp_path / "test.gif"
        frames[0].save(
            gif,
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
        original_size = gif.stat().st_size

        with Image.open(gif) as check:
            assert getattr(check, "n_frames", 0) == 3, "测试 GIF 应包含 3 帧"

        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(gif))

        assert result.original_size == original_size
        # 小 GIF 压缩后可能反而变大或不变，此时 skipped=True
        if result.skipped:
            assert result.saved == 0
        else:
            assert result.optimized_size < original_size
            assert result.saved > 0

        with Image.open(gif) as optimized:
            assert optimized.format == "GIF"
            assert getattr(optimized, "n_frames", 0) == 3
