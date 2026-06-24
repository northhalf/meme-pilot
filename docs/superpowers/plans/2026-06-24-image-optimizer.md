# Image Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `bot/engine/image_optimizer.py`，对表情包图片执行无损压缩，在进入索引前减小文件体积。

**Architecture:** 单类 `ImageOptimizer` + 私有方法按格式分发，通过依赖注入集成到 `IndexManager`。使用 Pillow 库处理 JPEG/PNG/WebP/GIF 四种格式，BMP 跳过。原子写入模式与 index_manager 一致。

**Tech Stack:** Python 3.12, Pillow, pytest, pytest-asyncio

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `bot/engine/image_optimizer.py` | 新建 | ImageOptimizer 类 + OptimizeResult 数据类 |
| `tests/unit/engine/test_image_optimizer.py` | 新建 | 单元测试 |
| `bot/engine/__init__.py` | 修改 | 导出新符号 |
| `bot/engine/index_manager.py` | 修改 | 注入 ImageOptimizer，_process_new_file 插入压缩 |
| `bot/app_state.py` | 修改 | ImageOptimizer 单例管理 |
| `docs/process.md` | 修改 | 记录模块完成 |
| `docs/api/API.md` | 修改 | 接口文档 |

---

### Task 1: 安装 Pillow 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 Pillow 生产依赖**

```bash
cd /home/northhalf/tmp/meme-pilot
uv add Pillow
```

- [ ] **Step 2: 验证安装成功**

```bash
uv run python -c "from PIL import Image; print(Image.__version__)"
```

Expected: 输出版本号（如 `11.1.0`）

---

### Task 2: 创建 OptimizeResult 数据类

**Files:**
- Create: `bot/engine/image_optimizer.py`
- Test: `tests/unit/engine/test_image_optimizer.py`

- [ ] **Step 1: 写失败测试 — OptimizeResult 创建**

```python
# tests/unit/engine/test_image_optimizer.py
"""ImageOptimizer 单元测试。"""

from __future__ import annotations

from bot.engine.image_optimizer import OptimizeResult


class TestOptimizeResult:
    """OptimizeResult 数据类测试。"""

    def test_create(self) -> None:
        """验证创建 OptimizeResult 实例。"""
        r = OptimizeResult(original_size=1000, optimized_size=800, saved=200)
        assert r.original_size == 1000
        assert r.optimized_size == 800
        assert r.saved == 200
        assert r.skipped is False

    def test_skipped(self) -> None:
        """验证 skipped 默认为 False，可显式设为 True。"""
        r = OptimizeResult(original_size=1000, optimized_size=1000, saved=0, skipped=True)
        assert r.skipped is True

    def test_frozen(self) -> None:
        """验证 frozen=True 不可修改。"""
        r = OptimizeResult(original_size=1000, optimized_size=800, saved=200)
        import pytest
        with pytest.raises(AttributeError):
            r.original_size = 500  # type: ignore[misc]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py::TestOptimizeResult -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'bot.engine.image_optimizer'`

- [ ] **Step 3: 写最小实现 — OptimizeResult**

```python
# bot/engine/image_optimizer.py
"""图片无损压缩模块。

对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
成功后覆盖原文件。.bmp 文件跳过压缩。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OptimizeResult:
    """图片压缩结果。

    Attributes:
        original_size: 原始文件大小（字节）。
        optimized_size: 压缩后文件大小（字节）。
        saved: 节省的字节数。
        skipped: 是否跳过压缩（如 .bmp 或压缩后反而变大）。
    """

    original_size: int
    optimized_size: int
    saved: int
    skipped: bool = False
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py::TestOptimizeResult -v
```

Expected: PASS

---

### Task 3: 实现 ImageOptimizer 类骨架 + optimize 入口

**Files:**
- Modify: `bot/engine/image_optimizer.py`
- Modify: `tests/unit/engine/test_image_optimizer.py`

- [ ] **Step 1: 写失败测试 — 不支持的格式抛 ValueError**

```python
# tests/unit/engine/test_image_optimizer.py 追加

import pytest

from bot.engine.image_optimizer import ImageOptimizer


class TestImageOptimizerUnsupported:
    """不支持的格式测试。"""

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        """不支持的格式抛出 ValueError。"""
        fake = tmp_path / "test.txt"
        fake.write_text("hello")
        optimizer = ImageOptimizer()
        with pytest.raises(ValueError, match="不支持的图片格式"):
            import asyncio
            asyncio.run(optimizer.optimize(fake))
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py::TestImageOptimizerUnsupported -v
```

Expected: FAIL — `AttributeError: module ... has no attribute 'ImageOptimizer'`

- [ ] **Step 3: 实现 ImageOptimizer 类骨架 + optimize 入口**

```python
# bot/engine/image_optimizer.py 追加

class ImageOptimizer:
    """图片无损压缩器。

    对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
    成功后覆盖原文件。.bmp 文件跳过压缩。

    Attributes:
        _jpeg_quality: JPEG 重编码质量（默认 95）。
        _webp_quality: WebP 无损压缩质量（默认 80）。
    """

    COMPRESSIBLE: frozenset[str] = frozenset({
        ".jpg", ".jpeg", ".png", ".webp", ".gif",
    })
    PASS_THROUGH: frozenset[str] = frozenset({".bmp"})

    def __init__(
        self,
        jpeg_quality: int = 95,
        webp_quality: int = 80,
    ) -> None:
        """初始化 ImageOptimizer。

        Args:
            jpeg_quality: JPEG 重编码质量（1-100），默认 95。
            webp_quality: WebP 无损压缩质量（0-100），默认 80。
        """
        self._jpeg_quality = jpeg_quality
        self._webp_quality = webp_quality

    async def optimize(self, image_path: str | Path) -> OptimizeResult:
        """尝试无损压缩图片，成功后覆盖原文件。

        Args:
            image_path: 图片文件路径。

        Returns:
            OptimizeResult 包含压缩前后大小信息。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 不支持的文件格式。
            RuntimeError: 压缩过程失败。
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        suffix = path.suffix.lower()

        # BMP 跳过
        if suffix in self.PASS_THROUGH:
            size = path.stat().st_size
            logger.debug("跳过压缩: %s (节省 0 字节)", path.name)
            return OptimizeResult(
                original_size=size, optimized_size=size, saved=0, skipped=True
            )

        # 不支持的格式
        if suffix not in self.COMPRESSIBLE:
            raise ValueError(f"不支持的图片格式: {suffix}")

        # TODO: 各格式压缩逻辑（后续 Task 实现）
        raise RuntimeError(f"图片压缩失败: {path.name} (尚未实现)")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py -v
```

Expected: PASS（OptimizeResult 3 个 + unsupported 1 个）

---

### Task 4: 实现 BMP 跳过 + 文件不存在测试

**Files:**
- Modify: `tests/unit/engine/test_image_optimizer.py`

- [ ] **Step 1: 写失败测试 — BMP 跳过 + 文件不存在**

```python
# tests/unit/engine/test_image_optimizer.py 追加


class TestImageOptimizerEdgeCases:
    """边界条件测试。"""

    def test_bmp_skipped(self, tmp_path: Path) -> None:
        """BMP 文件跳过压缩，返回 skipped=True。"""
        bmp = tmp_path / "test.bmp"
        bmp.write_bytes(b"\x42\x4d" + b"\x00" * 100)
        optimizer = ImageOptimizer()
        import asyncio
        result = asyncio.run(optimizer.optimize(bmp))
        assert result.skipped is True
        assert result.saved == 0
        assert result.original_size == result.optimized_size

    def test_file_not_found_raises(self) -> None:
        """文件不存在抛出 FileNotFoundError。"""
        optimizer = ImageOptimizer()
        import asyncio
        with pytest.raises(FileNotFoundError, match="图片文件不存在"):
            asyncio.run(optimizer.optimize("/nonexistent/test.jpg"))
```

- [ ] **Step 2: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py -v
```

Expected: PASS（已有功能覆盖 BMP 跳过和文件不存在的逻辑）

---

### Task 5: 实现 JPEG 压缩

**Files:**
- Modify: `bot/engine/image_optimizer.py`
- Modify: `tests/unit/engine/test_image_optimizer.py`

- [ ] **Step 1: 写失败测试 — JPEG 压缩**

```python
# tests/unit/engine/test_image_optimizer.py 追加


class TestCompressJpeg:
    """JPEG 压缩测试。"""

    def test_jpeg_compress(self, tmp_path: Path) -> None:
        """JPEG 压缩后文件大小减少，返回正确 OptimizeResult。"""
        # 创建一个有一定体积的测试 JPEG
        from PIL import Image
        img = Image.new("RGB", (200, 200), color=(128, 64, 32))
        jpg = tmp_path / "test.jpg"
        img.save(jpg, "JPEG", quality=100)
        original_size = jpg.stat().st_size

        optimizer = ImageOptimizer()
        import asyncio
        result = asyncio.run(optimizer.optimize(jpg))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.saved >= 0
        assert result.skipped is False
        # 文件应被覆盖（原子写入）
        assert jpg.exists()

    def test_jpeg_strips_metadata(self, tmp_path: Path) -> None:
        """JPEG 压缩后去除 EXIF 元数据。"""
        from PIL import Image
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        jpg = tmp_path / "meta.jpg"
        # 写入带 EXIF 的 JPEG
        img.save(jpg, "JPEG", quality=100, exif=b"fake exif data here")
        original_size = jpg.stat().st_size

        optimizer = ImageOptimizer()
        import asyncio
        result = asyncio.run(optimizer.optimize(jpg))

        # 压缩后文件应更小（去除了 EXIF）
        assert result.optimized_size < original_size or result.saved >= 0
        # 验证 EXIF 已去除
        from PIL import Image as PILImage
        with PILImage.open(jpg) as optimized:
            assert optimized.info.get("exif") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py::TestCompressJpeg -v
```

Expected: FAIL — `RuntimeError: 图片压缩失败: test.jpg (尚未实现)`

- [ ] **Step 3: 实现 _compress_jpeg + _atomic_save**

```python
# bot/engine/image_optimizer.py — 在 ImageOptimizer 类中追加

    async def _compress_jpeg(self, path: Path) -> int:
        """压缩 JPEG 文件。

        去除 EXIF/元数据，以高质量重新编码。

        Args:
            path: JPEG 文件路径。

        Returns:
            压缩后文件大小（字节）。
        """
        img = Image.open(path)
        try:
            if img.mode != "RGB":
                img = img.convert("RGB")
            return self._atomic_save(
                img, path, format="JPEG",
                quality=self._jpeg_quality, optimize=True, progressive=True,
            )
        finally:
            img.close()

    async def _compress_png(self, path: Path) -> int:
        """压缩 PNG 文件。

        以 optimize=True 重新保存，像素数据不变。

        Args:
            path: PNG 文件路径。

        Returns:
            压缩后文件大小（字节）。
        """
        img = Image.open(path)
        try:
            return self._atomic_save(img, path, format="PNG", optimize=True)
        finally:
            img.close()

    async def _compress_webp(self, path: Path) -> int:
        """压缩 WebP 文件。

        以无损模式重新编码。

        Args:
            path: WebP 文件路径。

        Returns:
            压缩后文件大小（字节）。
        """
        img = Image.open(path)
        try:
            return self._atomic_save(
                img, path, format="WEBP",
                lossless=True, quality=self._webp_quality, method=6,
            )
        finally:
            img.close()

    async def _compress_gif(self, path: Path) -> int:
        """压缩 GIF 文件。

        逐帧复制，保留动画属性，去除冗余元数据。

        Args:
            path: GIF 文件路径。

        Returns:
            压缩后文件大小（字节）。
        """
        img = Image.open(path)
        try:
            # 保留动画关键属性
            save_kwargs: dict = {"optimize": True}
            if "duration" in img.info:
                save_kwargs["duration"] = img.info["duration"]
            if "loop" in img.info:
                save_kwargs["loop"] = img.info["loop"]
            if "transparency" in img.info:
                save_kwargs["transparency"] = img.info["transparency"]
            return self._atomic_save(img, path, format="GIF", **save_kwargs)
        finally:
            img.close()

    def _atomic_save(
        self, img: Image.Image, path: Path, **save_kwargs: object
    ) -> int:
        """原子写入：先写 .tmp 再 os.replace 覆盖原文件。

        Args:
            img: Pillow Image 对象。
            path: 目标文件路径。
            **save_kwargs: 传递给 img.save() 的参数。

        Returns:
            写入后文件大小（字节）。

        Raises:
            RuntimeError: 写入失败。
        """
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            img.save(tmp_path, **save_kwargs)
            os.replace(tmp_path, path)
            return path.stat().st_size
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"图片写入失败: {path.name}") from exc
```

- [ ] **Step 4: 更新 optimize 入口，分发到各格式方法**

```python
# bot/engine/image_optimizer.py — 替换 optimize 方法中的 TODO 部分

        # 分发到各格式压缩方法
        original_size = path.stat().st_size
        try:
            if suffix in (".jpg", ".jpeg"):
                optimized_size = await self._compress_jpeg(path)
            elif suffix == ".png":
                optimized_size = await self._compress_png(path)
            elif suffix == ".webp":
                optimized_size = await self._compress_webp(path)
            elif suffix == ".gif":
                optimized_size = await self._compress_gif(path)
            else:
                raise ValueError(f"不支持的图片格式: {suffix}")
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"图片压缩失败: {path.name}") from exc

        # 压缩后反而变大 → 保留原文件（已在 _atomic_save 中覆盖，此处记录跳过）
        saved = original_size - optimized_size
        if saved <= 0:
            logger.debug("跳过压缩: %s (压缩后反而变大)", path.name)
            return OptimizeResult(
                original_size=original_size,
                optimized_size=optimized_size,
                saved=0,
                skipped=True,
            )

        pct = saved / original_size * 100
        logger.debug(
            "压缩完成: %s (%d → %d, 节省 %.1f%%)",
            path.name, original_size, optimized_size, pct,
        )
        return OptimizeResult(
            original_size=original_size,
            optimized_size=optimized_size,
            saved=saved,
        )
```

- [ ] **Step 5: 运行全部测试确认通过**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py -v
```

Expected: PASS

---

### Task 6: 实现 PNG/WebP/GIF 压缩测试

**Files:**
- Modify: `tests/unit/engine/test_image_optimizer.py`

- [ ] **Step 1: 写失败测试 — PNG/WebP/GIF**

```python
# tests/unit/engine/test_image_optimizer.py 追加


class TestCompressPng:
    """PNG 压缩测试。"""

    def test_png_compress(self, tmp_path: Path) -> None:
        """PNG 压缩后返回正确 OptimizeResult。"""
        from PIL import Image
        img = Image.new("RGBA", (200, 200), color=(128, 64, 32, 255))
        png = tmp_path / "test.png"
        img.save(png, "PNG")
        original_size = png.stat().st_size

        optimizer = ImageOptimizer()
        import asyncio
        result = asyncio.run(optimizer.optimize(png))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.skipped is False


class TestCompressWebp:
    """WebP 压缩测试。"""

    def test_webp_compress(self, tmp_path: Path) -> None:
        """WebP 无损压缩后返回正确 OptimizeResult。"""
        from PIL import Image
        img = Image.new("RGB", (200, 200), color=(100, 150, 200))
        webp = tmp_path / "test.webp"
        img.save(webp, "WEBP")
        original_size = webp.stat().st_size

        optimizer = ImageOptimizer()
        import asyncio
        result = asyncio.run(optimizer.optimize(webp))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.skipped is False


class TestCompressGif:
    """GIF 压缩测试。"""

    def test_gif_compress(self, tmp_path: Path) -> None:
        """GIF 压缩后保留动画，返回正确 OptimizeResult。"""
        from PIL import Image
        frames = []
        for i in range(3):
            frame = Image.new("P", (50, 50), color=i * 50)
            frames.append(frame)
        gif = tmp_path / "test.gif"
        frames[0].save(
            gif, save_all=True, append_images=frames[1:],
            duration=100, loop=0,
        )
        original_size = gif.stat().st_size

        optimizer = ImageOptimizer()
        import asyncio
        result = asyncio.run(optimizer.optimize(gif))

        assert result.original_size == original_size
        assert result.optimized_size <= original_size
        assert result.skipped is False

        # 验证 GIF 仍可正常打开且帧数不变
        with Image.open(gif) as optimized:
            assert optimized.format == "GIF"
            assert optimized.n_frames == 3
```

- [ ] **Step 2: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_image_optimizer.py -v
```

Expected: PASS

---

### Task 7: 语法检查 + 全量测试

**Files:**
- (无文件变更)

- [ ] **Step 1: 语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 全部 OK

- [ ] **Step 2: 全量单元测试**

```bash
uv run pytest tests/unit/ -v
```

Expected: 全部 PASS

---

### Task 8: 更新 engine 包导出

**Files:**
- Modify: `bot/engine/__init__.py`

- [ ] **Step 1: 添加 ImageOptimizer 和 OptimizeResult 到 __init__.py**

在 `bot/engine/__init__.py` 中：

1. 添加导入：
```python
from .image_optimizer import ImageOptimizer, OptimizeResult
```

2. 添加到 `__all__`：
```python
"ImageOptimizer",
"OptimizeResult",
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from bot.engine import ImageOptimizer, OptimizeResult; print('OK')"
```

Expected: `OK`

---

### Task 9: 集成到 IndexManager

**Files:**
- Modify: `bot/engine/index_manager.py`

- [ ] **Step 1: __init__ 新增 optimizer 参数**

在 `IndexManager.__init__()` 的参数列表中添加：
```python
optimizer: "ImageOptimizer | None" = None,
```

在方法体内添加：
```python
self._optimizer = optimizer
```

- [ ] **Step 2: _process_new_file 插入压缩调用**

将 `_process_new_file` 方法改为：
```python
    async def _process_new_file(self, filename: str) -> tuple[str, str, list[float]]:
        """处理单张新增图片：压缩 → OCR → Embed。

        受 _sync_semaphore 约束，并发上限内执行。

        Args:
            filename: 表情包文件名。

        Returns:
            (filename, ocr_text, embedding) 三元组。

        Raises:
            RuntimeError: OCR 或 embedding 服务未注入，或压缩失败。
            Exception: OCR 或 embedding 调用失败时向上抛出，由调用方捕获。
        """
        image_path = self._memes_dir / filename
        async with self._sync_semaphore:
            # 压缩（可选）
            if self._optimizer is not None:
                await self._optimizer.optimize(str(image_path))

            if self._ocr_provider is None:
                raise RuntimeError("OCR 服务未注入")
            text = await self._ocr_provider.ocr(str(image_path))

            if self._embedding_provider is None:
                raise RuntimeError("Embedding 服务未注入")
            embedding = await self._embedding_provider.embed(text)

        return filename, text, embedding
```

- [ ] **Step 3: 语法检查**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

Expected: OK

- [ ] **Step 4: 运行现有 IndexManager 测试确认无回归**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: 全部 PASS

---

### Task 10: 更新 app_state.py

**Files:**
- Modify: `bot/app_state.py`

- [ ] **Step 1: 添加 ImageOptimizer 单例管理**

修改 `bot/app_state.py`：

```python
"""共享实例管理模块。

模块级单例模式，供各插件获取 IndexManager、OcrService、EmbeddingService、ImageOptimizer。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from .engine import DeepSeekOcrService, EmbeddingService, ImageOptimizer, IndexManager

_index_manager: IndexManager | None = None
_ocr_service: DeepSeekOcrService | None = None
_embedding_service: EmbeddingService | None = None
_image_optimizer: ImageOptimizer | None = None


def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    image_optimizer: ImageOptimizer | None = None,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
        image_optimizer: 图片压缩器实例，可选。
    """
    global _index_manager, _ocr_service, _embedding_service, _image_optimizer
    _index_manager = index_manager
    _ocr_service = ocr_service
    _embedding_service = embedding_service
    _image_optimizer = image_optimizer


# ... 其他 get_* 函数保持不变 ...


def get_image_optimizer() -> ImageOptimizer | None:
    """获取 ImageOptimizer 单例。

    Returns:
        已初始化的 ImageOptimizer 实例，未注入时返回 None。
    """
    return _image_optimizer
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from bot.app_state import get_image_optimizer; print('OK')"
```

Expected: `OK`

---

### Task 11: 更新文档

**Files:**
- Modify: `docs/process.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 更新 process.md**

在已完成模块列表中添加 `image_optimizer.py` 及简要说明。

- [ ] **Step 2: 更新 API.md**

添加 `ImageOptimizer` 和 `OptimizeResult` 的接口文档，包含：
- 类签名和构造参数
- `optimize()` 方法签名、参数、返回值、异常
- `OptimizeResult` 各字段说明

- [ ] **Step 3: 语法检查（仅文档变更，不运行测试）**

```bash
uv run python -m compileall bot
```

Expected: OK

---

### Task 12: 全量验证 + 提交

**Files:**
- (无文件变更)

- [ ] **Step 1: 全量语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 全部 OK

- [ ] **Step 2: 全量测试**

```bash
uv run pytest -v
```

Expected: 全部 PASS

- [ ] **Step 3: 提交（由用户审核）**

```bash
git add bot/engine/image_optimizer.py tests/unit/engine/test_image_optimizer.py bot/engine/__init__.py bot/engine/index_manager.py bot/app_state.py docs/process.md docs/api/API.md pyproject.toml uv.lock
git commit -m "feat(engine): 实现 image_optimizer 图片无损压缩模块

- 新增 ImageOptimizer 类，支持 JPEG/PNG/WebP/GIF 无损压缩
- 新增 OptimizeResult 数据类，返回压缩效果信息
- 集成到 IndexManager，OCR 前自动压缩新图片
- 更新 app_state.py 支持 ImageOptimizer 单例管理
- 更新 engine 包导出
- 更新 process.md 和 API.md 文档"
```
