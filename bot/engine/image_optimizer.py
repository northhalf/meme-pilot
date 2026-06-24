"""图片无损压缩模块。

对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
成功后覆盖原文件。.bmp 文件跳过压缩。
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class ImageOptimizer:
    """图片无损压缩器。

    对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
    成功后覆盖原文件。.bmp 文件跳过压缩。

    Attributes:
        _jpeg_quality: JPEG 重编码质量（默认 95）。
        _webp_quality: WebP 无损压缩质量（默认 80）。
    """

    COMPRESSIBLE: frozenset[str] = frozenset(
        {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
        }
    )
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

        # 分发到各格式压缩方法
        original_size = path.stat().st_size
        try:
            if suffix in (".jpg", ".jpeg"):
                optimized_size = await asyncio.to_thread(
                    self._compress_jpeg, path, original_size
                )
            elif suffix == ".png":
                optimized_size = await asyncio.to_thread(
                    self._compress_png, path, original_size
                )
            elif suffix == ".webp":
                optimized_size = await asyncio.to_thread(
                    self._compress_webp, path, original_size
                )
            else:
                optimized_size = await asyncio.to_thread(
                    self._compress_gif, path, original_size
                )
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"图片压缩失败: {path.name}") from exc

        # 压缩后反而变大
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
            path.name,
            original_size,
            optimized_size,
            pct,
        )
        return OptimizeResult(
            original_size=original_size,
            optimized_size=optimized_size,
            saved=saved,
        )

    def _compress_jpeg(self, path: Path, original_size: int) -> int:
        """压缩 JPEG 文件。

        去除 EXIF/元数据，以高质量重新编码。
        非 RGB 模式自动转换。

        Args:
            path: JPEG 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            if img.mode != "RGB":
                img = img.convert("RGB")
            return self._atomic_save(
                img,
                path,
                original_size,
                format="JPEG",
                quality=self._jpeg_quality,
                optimize=True,
                progressive=True,
            )
        finally:
            img.close()

    def _compress_png(self, path: Path, original_size: int) -> int:
        """压缩 PNG 文件。

        以 optimize=True 重新保存，像素数据不变（真正无损）。

        Args:
            path: PNG 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            return self._atomic_save(
                img, path, original_size, format="PNG", optimize=True
            )
        finally:
            img.close()

    def _compress_webp(self, path: Path, original_size: int) -> int:
        """压缩 WebP 文件。

        以无损模式重新编码（lossless=True, method=6）。

        Args:
            path: WebP 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            return self._atomic_save(
                img,
                path,
                original_size,
                format="WEBP",
                lossless=True,
                quality=self._webp_quality,
                method=6,
            )
        finally:
            img.close()

    def _compress_gif(self, path: Path, original_size: int) -> int:
        """压缩 GIF 文件。

        保留动画属性（duration、loop、transparency），
        去除冗余元数据。多帧 GIF 使用 save_all 保存所有帧。

        Args:
            path: GIF 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            save_kwargs: dict[str, Any] = {"optimize": True}
            if "duration" in img.info:
                save_kwargs["duration"] = img.info["duration"]
            if "loop" in img.info:
                save_kwargs["loop"] = img.info["loop"]
            if "transparency" in img.info:
                save_kwargs["transparency"] = img.info["transparency"]
            if getattr(img, "n_frames", 1) > 1:
                save_kwargs["save_all"] = True
                return self._atomic_save_animated_gif(
                    img, path, save_kwargs, original_size
                )
            return self._atomic_save(
                img, path, original_size, format="GIF", **save_kwargs
            )
        finally:
            img.close()

    def _atomic_save_animated_gif(
        self,
        img: Image.Image,
        path: Path,
        save_kwargs: dict[str, Any],
        original_size: int,
    ) -> int:
        """保存多帧 GIF，提取所有帧后原子写入。

        Args:
            img: 已打开的 PIL 图片对象。
            path: 目标文件路径。
            save_kwargs: 保存参数。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        frames: list[Image.Image] = []
        try:
            n_frames: int = getattr(img, "n_frames", 1)
            for i in range(n_frames):
                img.seek(i)
                frames.append(img.copy())
            frames[0].save(
                tmp_path,
                format="GIF",
                append_images=frames[1:],
                **save_kwargs,
            )
            new_size = tmp_path.stat().st_size
            if new_size < original_size:
                os.replace(tmp_path, path)
                return new_size
            tmp_path.unlink(missing_ok=True)
            return original_size
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"图片写入失败: {path.name}") from exc
        finally:
            for f in frames:
                f.close()

    def _atomic_save(
        self,
        img: Image.Image,
        path: Path,
        original_size: int,
        **save_kwargs: Any,
    ) -> int:
        """原子保存：仅当压缩后文件更小时才覆盖原文件。

        Args:
            img: 已打开的 PIL 图片对象。
            path: 目标文件路径。
            original_size: 原始文件大小（字节）。
            **save_kwargs: 传递给 img.save() 的参数，如 format、quality、optimize 等。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            img.save(tmp_path, **save_kwargs)
            new_size = tmp_path.stat().st_size
            if new_size < original_size:
                os.replace(tmp_path, path)
                return new_size
            # 压缩后反而变大，保留原文件
            tmp_path.unlink(missing_ok=True)
            return original_size
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"图片写入失败: {path.name}") from exc
