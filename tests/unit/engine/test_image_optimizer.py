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
            r.original_size = 500  # type: ignore[misc, ty:invalid-assignment]

    def test_output_path_default_empty(self) -> None:
        r = OptimizeResult(original_size=1000, optimized_size=800, saved=200)
        assert r.output_path == ""

    def test_output_path_set(self) -> None:
        r = OptimizeResult(
            original_size=1000, optimized_size=800, saved=200, output_path="/x/a.webp"
        )
        assert r.output_path == "/x/a.webp"


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

    def test_optimize_returns_output_path_original(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")
        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(jpg))
        assert result.output_path == str(jpg)

    def test_bmp_output_path_original(self, tmp_path: Path) -> None:
        bmp = tmp_path / "t.bmp"
        bmp.write_bytes(b"\x42\x4d" + b"\x00" * 100)
        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(bmp))
        assert result.output_path == str(bmp)

    def test_should_convert_to_webp_param_default_false(self) -> None:
        optimizer = ImageOptimizer()
        assert optimizer._should_convert_to_webp is False

    def test_should_convert_to_webp_param_true(self) -> None:
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        assert optimizer._should_convert_to_webp is True


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


class TestImageOptimizerSemaphore:
    """验证 ImageOptimizer 的 Semaphore 并发控制。"""

    @pytest.mark.asyncio
    async def test_default_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不传 concurrency 时使用环境变量默认值 (5)。"""
        monkeypatch.delenv("COMPRESS_CONCURRENCY", raising=False)
        service = ImageOptimizer()
        assert service._semaphore._value == 5

    @pytest.mark.asyncio
    async def test_custom_concurrency(self) -> None:
        """传 concurrency=2 时 Semaphore 值为 2。"""
        service = ImageOptimizer(concurrency=2)
        assert service._semaphore._value == 2

    @pytest.mark.asyncio
    async def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """设置 COMPRESS_CONCURRENCY 环境变量时生效。"""
        monkeypatch.setenv("COMPRESS_CONCURRENCY", "3")
        service = ImageOptimizer()
        assert service._semaphore._value == 3

    @pytest.mark.asyncio
    async def test_semaphore_blocks_concurrent(self, tmp_path: Path) -> None:
        """concurrency=1 时第二个并发调用应阻塞。"""
        import time

        service = ImageOptimizer(concurrency=1)

        img1 = tmp_path / "test1.jpg"
        img1.write_text("1234")
        img2 = tmp_path / "test2.jpg"
        img2.write_text("5678")

        def slow_compress(path: Path, original_size: int) -> int:
            time.sleep(10)
            return 100

        service._compress_jpeg = slow_compress  # type: ignore[method-assign, ty:invalid-assignment]

        task1 = asyncio.create_task(service.optimize(str(img1)))
        await asyncio.sleep(0.05)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service.optimize(str(img2)), timeout=0.1)

        task1.cancel()
        try:
            await task1
        except (asyncio.CancelledError, RuntimeError):
            pass


class TestConvertToWebp:
    """should_convert_to_webp=True 时各格式转 WebP 测试。"""

    def test_jpg_converted(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (200, 200), color=(128, 64, 32))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG", quality=100)
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(jpg))
        assert str(result.output_path).endswith(".webp")
        assert Path(result.output_path).exists()
        assert not jpg.exists()
        with Image.open(result.output_path) as w:
            assert w.format == "WEBP"

    def test_png_alpha_preserved(self, tmp_path: Path) -> None:
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        png = tmp_path / "t.png"
        img.save(png, "PNG")
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(png))
        with Image.open(result.output_path) as w:
            assert w.mode == "RGBA"
        assert not png.exists()

    def test_gif_animated_converted(self, tmp_path: Path) -> None:
        frames = [
            Image.new("RGB", (50, 50), color=(i * 80, 0, 0)).quantize(colors=256)
            for i in range(3)
        ]
        gif = tmp_path / "t.gif"
        frames[0].save(
            gif, save_all=True, append_images=frames[1:], duration=100, loop=0
        )
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(gif))
        with Image.open(result.output_path) as w:
            assert w.format == "WEBP"
            assert getattr(w, "n_frames", 1) == 3
        assert not gif.exists()

    def test_bmp_converted_when_switch_on(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        bmp = tmp_path / "t.bmp"
        img.save(bmp, "BMP")
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(bmp))
        assert str(result.output_path).endswith(".webp")
        assert not bmp.exists()

    def test_webp_source_lossy_reencode_when_switch_on(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        webp = tmp_path / "t.webp"
        img.save(webp, "WEBP", lossless=True, quality=100)
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(webp))
        assert result.output_path == str(webp)
        # 有损重编码：变大则 skipped 保留原文件，变小则覆盖
        with Image.open(webp) as w:
            assert w.format == "WEBP"

    def test_webp_source_lossless_when_switch_off(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        webp = tmp_path / "t.webp"
        img.save(webp, "WEBP")
        optimizer = ImageOptimizer(should_convert_to_webp=False)
        result = asyncio.run(optimizer.optimize(webp))
        assert result.output_path == str(webp)

    def test_switch_off_keeps_jpg(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")
        optimizer = ImageOptimizer(should_convert_to_webp=False)
        result = asyncio.run(optimizer.optimize(jpg))
        assert result.output_path == str(jpg)
        assert jpg.exists()
        with Image.open(jpg) as j:
            assert j.format == "JPEG"

    def test_convert_failure_preserves_original(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")

        def fail(_p: Path) -> Path:
            raise RuntimeError("convert fail")

        optimizer = ImageOptimizer(should_convert_to_webp=True)
        optimizer._convert_image_to_webp = fail  # type: ignore[method-assign, ty:invalid-assignment]
        with pytest.raises(RuntimeError, match="convert fail"):
            asyncio.run(optimizer.optimize(jpg))
        assert jpg.exists()

    def test_target_exists_appends_n(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")
        (tmp_path / "t.webp").write_bytes(b"existing")
        optimizer = ImageOptimizer(should_convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(jpg))
        assert str(result.output_path).endswith("t_1.webp")
        assert Path(result.output_path).exists()
        assert not jpg.exists()
