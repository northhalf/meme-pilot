"""图片无损压缩模块。

对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
成功后覆盖原文件。.bmp 文件跳过压缩。
"""

import asyncio
import logging
import os
from collections.abc import Callable
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
        output_path: 最终文件路径（同格式压缩=原路径；转 WebP=新 .webp 路径）。
    """

    original_size: int
    optimized_size: int
    saved: int
    skipped: bool = False
    output_path: str = ""


class ImageOptimizer:
    """图片无损压缩器。

    对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
    成功后覆盖原文件。.bmp 文件跳过压缩。

    Attributes:
        _lossy_quality: 有损编码质量（用于 JPEG 与有损 WebP，默认 85）。
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
    CONVERTIBLE_TO_WEBP: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
    )

    def __init__(
        self,
        lossy_quality: int = 85,
        webp_quality: int = 80,
        concurrency: int | None = None,
        should_convert_to_webp: bool = False,
    ) -> None:
        """初始化 ImageOptimizer。

        Args:
            lossy_quality: 有损编码质量（1-100，用于 JPEG 与有损 WebP），默认 85。
            webp_quality: WebP 质量（0-100），默认 80。
            concurrency: 并发数，默认从 COMPRESS_CONCURRENCY 环境变量读取，
                         回退为 5。
            should_convert_to_webp: 是否将图片转为 WebP（默认 False，维持现状同格式压缩）。
        """
        self._lossy_quality = lossy_quality
        self._webp_quality = webp_quality
        self._should_convert_to_webp = should_convert_to_webp

        c = concurrency or int(os.environ.get("COMPRESS_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    async def optimize(self, image_path: str | Path) -> OptimizeResult:
        """尝试压缩/转换图片，成功后覆盖原文件或生成新 WebP。

        路由优先级：
        1. ``.webp`` 源：开关开 -> 有损重编码；关 -> 无损重编码。均变小才覆盖。
        2. 开关开 + 可转换格式（jpg/jpeg/png/gif/bmp）-> 强制转 WebP（不比较体积）。
        3. 开关关或 .bmp -> 同格式压缩 / PASS_THROUGH。

        Args:
            image_path: 图片文件路径。

        Returns:
            OptimizeResult 包含压缩前后大小与最终路径。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 不支持的文件格式。
            RuntimeError: 压缩/转换过程失败。
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        suffix = path.suffix.lower()

        # 优先级 1：.webp 源
        if suffix == ".webp":
            return await self._optimize_webp_source(path)

        # 优先级 2：开关开 + 可转换格式 -> 强制转 WebP
        if self._should_convert_to_webp and suffix in self.CONVERTIBLE_TO_WEBP:
            return await self._convert_to_webp_branch(path)

        # 优先级 3：BMP 跳过
        if suffix in self.PASS_THROUGH:
            size = path.stat().st_size
            logger.debug("跳过压缩: %s (节省 0 字节)", path.name)
            return OptimizeResult(
                original_size=size,
                optimized_size=size,
                saved=0,
                skipped=True,
                output_path=str(path),
            )

        # 不支持的格式
        if suffix not in self.COMPRESSIBLE:
            raise ValueError(f"不支持的图片格式: {suffix}")

        # 优先级 3：同格式压缩
        return await self._compress_same_format(path, suffix)

    async def _optimize_webp_source(self, path: Path) -> OptimizeResult:
        """优先级 1：压缩 .webp 源，变小才覆盖，不改名。

        开关开时用有损重编码（``_compress_webp_lossy``），关时用无损重编码
        （``_compress_webp``）。压缩后变大则跳过保留原文件。

        Args:
            path: WebP 文件路径。

        Returns:
            OptimizeResult（output_path 为原路径）。

        Raises:
            RuntimeError: 压缩失败。
        """
        compress_fn = (
            self._compress_webp_lossy if self._should_convert_to_webp else self._compress_webp
        )
        async with self._semaphore:
            original_size = path.stat().st_size
            try:
                optimized_size = await asyncio.to_thread(
                    compress_fn, path, original_size
                )
            except (ValueError, RuntimeError):
                raise
            except Exception as exc:
                raise RuntimeError(f"图片压缩失败: {path.name}") from exc
            saved = original_size - optimized_size
            if saved <= 0:
                return OptimizeResult(
                    original_size=original_size,
                    optimized_size=optimized_size,
                    saved=0,
                    skipped=True,
                    output_path=str(path),
                )
            return OptimizeResult(
                original_size=original_size,
                optimized_size=optimized_size,
                saved=saved,
                output_path=str(path),
            )

    async def _convert_to_webp_branch(self, path: Path) -> OptimizeResult:
        """优先级 2：强制将图片转为有损 WebP，不比较体积。

        转换后原文件由 ``_convert_image_to_webp`` 删除，output_path 为新 .webp 路径。

        Args:
            path: 源图片路径。

        Returns:
            OptimizeResult（output_path 为新 WebP 路径）。

        Raises:
            RuntimeError: 转换失败。
        """
        async with self._semaphore:
            original_size = path.stat().st_size
            try:
                new_path = await asyncio.to_thread(
                    self._convert_image_to_webp, path
                )
            except (ValueError, RuntimeError):
                raise
            except Exception as exc:
                raise RuntimeError(f"图片转换失败: {path.name}") from exc
            optimized_size = new_path.stat().st_size
            return OptimizeResult(
                original_size=original_size,
                optimized_size=optimized_size,
                saved=original_size - optimized_size,
                output_path=str(new_path),
            )

    async def _compress_same_format(self, path: Path, suffix: str) -> OptimizeResult:
        """优先级 3：同格式无损压缩（jpg/jpeg/png/gif），变小才覆盖。

        压缩后变大则跳过保留原文件并记录 debug 日志。

        Args:
            path: 图片文件路径。
            suffix: 小写扩展名（.jpg/.jpeg/.png/.gif）。

        Returns:
            OptimizeResult（output_path 为原路径）。

        Raises:
            RuntimeError: 压缩失败。
        """
        if suffix in (".jpg", ".jpeg"):
            compress_fn = self._compress_jpeg
        elif suffix == ".png":
            compress_fn = self._compress_png
        else:
            compress_fn = self._compress_gif
        async with self._semaphore:
            original_size = path.stat().st_size
            try:
                optimized_size = await asyncio.to_thread(
                    compress_fn, path, original_size
                )
            except (ValueError, RuntimeError):
                raise
            except Exception as exc:
                raise RuntimeError(f"图片压缩失败: {path.name}") from exc
            saved = original_size - optimized_size
            if saved <= 0:
                logger.debug("跳过压缩: %s (压缩后反而变大)", path.name)
                return OptimizeResult(
                    original_size=original_size,
                    optimized_size=optimized_size,
                    saved=0,
                    skipped=True,
                    output_path=str(path),
                )
            pct = saved / original_size * 100
            logger.debug(
                "压缩完成: %s (%d -> %d, 节省 %.1f%%)",
                path.name,
                original_size,
                optimized_size,
                pct,
            )
            return OptimizeResult(
                original_size=original_size,
                optimized_size=optimized_size,
                saved=saved,
                output_path=str(path),
            )

    def _ensure_rgb(self, img: Image.Image) -> Image.Image:
        """非 RGB 模式转 RGB，否则原样返回。

        Args:
            img: 已打开的 PIL 图片对象。

        Returns:
            RGB 模式的 Image（若需转换则返回新对象）。
        """
        if img.mode != "RGB":
            return img.convert("RGB")
        return img

    def _compress_simple(
        self,
        path: Path,
        original_size: int,
        *,
        format: str,
        preprocess: Callable[[Image.Image], Image.Image] | None = None,
        **save_kwargs: Any,
    ) -> int:
        """通用压缩骨架：打开 -> 可选预处理 -> 原子保存 -> 关闭。

        Args:
            path: 图片文件路径。
            original_size: 原始文件大小（字节）。
            format: PIL 保存格式（如 "JPEG"/"PNG"/"WEBP"）。
            preprocess: 可选预处理函数，接收 Image 返回 Image（如模式转换）。
            **save_kwargs: 传递给 _atomic_save 的保存参数。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            if preprocess is not None:
                img = preprocess(img)
            return self._atomic_save(
                img, path, original_size, format=format, **save_kwargs
            )
        finally:
            img.close()

    def _compress_jpeg(self, path: Path, original_size: int) -> int:
        """压缩 JPEG：去 EXIF/元数据，非 RGB 转 RGB，高质量重编码。

        Args:
            path: JPEG 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        return self._compress_simple(
            path,
            original_size,
            format="JPEG",
            preprocess=self._ensure_rgb,
            quality=self._lossy_quality,
            optimize=True,
            progressive=True,
        )

    def _compress_png(self, path: Path, original_size: int) -> int:
        """压缩 PNG：以 optimize=True 重新保存，像素数据不变（真正无损）。

        Args:
            path: PNG 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        return self._compress_simple(path, original_size, format="PNG", optimize=True)

    def _compress_webp(self, path: Path, original_size: int) -> int:
        """压缩 WebP：无损模式重编码（lossless=True, method=6）。

        Args:
            path: WebP 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若压缩后更大则保留原文件并返回原始大小。
        """
        return self._compress_simple(
            path,
            original_size,
            format="WEBP",
            lossless=True,
            quality=self._webp_quality,
            method=6,
        )

    def _compress_webp_lossy(self, path: Path, original_size: int) -> int:
        """有损重编码 WebP（开关开启时用于 .webp 源）。

        Args:
            path: WebP 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若重编码后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            save_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
            return self._atomic_save(
                save_img,
                path,
                original_size,
                format="WEBP",
                quality=self._lossy_quality,
                method=6,
            )
        finally:
            img.close()

    def _convert_image_to_webp(self, path: Path) -> Path:
        """将图片转换为有损 WebP，返回新路径，成功后删除原文件。

        强制转换不比较体积。透明通道保留（P/RGBA 保持 RGBA）。
        GIF 动图保留 duration/loop/transparency 转 animated WebP。
        失败时清理临时文件与已生成 .webp，原文件保留。

        Args:
            path: 源图片路径。

        Returns:
            生成的 WebP 文件路径。

        Raises:
            RuntimeError: 转换失败。
        """
        from .utils import resolve_unique_filename

        target_dir = path.parent
        target = resolve_unique_filename(target_dir, f"{path.stem}.webp")
        tmp_path = target.with_suffix(".webp.tmp")
        try:
            img = Image.open(path)
            try:
                save_kwargs: dict[str, Any] = {
                    "format": "WEBP",
                    "quality": self._lossy_quality,
                    "method": 6,
                }
                n_frames: int = getattr(img, "n_frames", 1)
                if n_frames > 1:
                    # 动图：提取所有帧，保留 duration/loop。
                    # transparency 保留依赖 Pillow 对 P/RGBA 帧的 WEBP 转换，若丢失需调整。
                    frames: list[Image.Image] = []
                    for i in range(n_frames):
                        img.seek(i)
                        frames.append(img.copy())
                    if "duration" in img.info:
                        save_kwargs["duration"] = img.info["duration"]
                    if "loop" in img.info:
                        save_kwargs["loop"] = img.info["loop"]
                    frames[0].save(
                        tmp_path, append_images=frames[1:], save_all=True, **save_kwargs
                    )
                    for f in frames:
                        f.close()
                else:
                    # 静态图：保留透明（P/RGBA 保持），否则转 RGB
                    save_img = (
                        img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
                    )
                    save_img.save(tmp_path, **save_kwargs)
            finally:
                img.close()
            os.replace(tmp_path, target)
            # 转换成功后删除原文件（若与目标不同）
            if path.resolve() != target.resolve():
                path.unlink(missing_ok=True)
            return target
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            # 若 .webp 已生成但后续失败，清理孤儿
            if target.exists() and path.exists():
                target.unlink(missing_ok=True)
            raise RuntimeError(f"WebP 转换失败: {path.name}") from exc

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
