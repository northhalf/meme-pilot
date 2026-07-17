"""ImagePipeline - 压缩 -> OCR -> Embedding 管道 + optimizer 并发锁表。

从 IndexManager 抽离，自包含持有 optimizer/ocr/embedding providers 与目标锁注册表，
负责同父目录、同 stem 图片的优化互斥、外部取消的可靠传播与管线输出清理。
门面 IndexManager 持有本类实例并薄委托 _process_image_pipeline/_move_to_no_text 等方法，
provider 与锁表属性经 property 转发以保留测试 monkeypatch rebind 能力。
"""

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, TypeVar

from bot.engine.image_optimizer import ImageOptimizer, OptimizeResult
from bot.engine.protocols import EmbeddingProvider, OcrProvider
from bot.engine.utils import resolve_unique_filename

from .index_types import EmbeddingError, OcrError, _OptimizerLockEntry

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class ImagePipeline:
    """压缩 -> OCR -> Embedding 管道 + optimizer 并发锁表。

    Args:
        optimizer: 图片压缩器（可选，None 时跳过压缩）。
        ocr_provider: OCR 服务提供者。
        embedding_provider: Embedding 服务提供者。
        memes_dir: memes/ 目录。
        no_text_dir: 无文字图目录。
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )

    def __init__(
        self,
        optimizer: ImageOptimizer | None,
        ocr_provider: OcrProvider | None,
        embedding_provider: EmbeddingProvider | None,
        memes_dir: Path,
        no_text_dir: Path,
    ) -> None:
        """初始化 ImagePipeline。

        Args:
            optimizer: 图片压缩器实例（可选，None 时跳过压缩）。
            ocr_provider: OCR 服务提供者实例。
            embedding_provider: Embedding 服务提供者实例。
            memes_dir: memes/ 目录路径。
            no_text_dir: 无文字图目录路径。
        """
        self._optimizer = optimizer
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider
        self._memes_dir = memes_dir
        self._no_text_dir = no_text_dir
        self._optimizer_target_locks: dict[tuple[str, str], _OptimizerLockEntry] = {}
        self._optimizer_registry_guard = asyncio.Lock()

    @staticmethod
    def has_supported_ext(name: str) -> bool:
        """判断文件名扩展名是否受支持（避免 Path 对象分配）。

        Args:
            name: 文件名。

        Returns:
            扩展名（含点、小写）在 SUPPORTED_EXTENSIONS 中时返回 True。
        """
        dot = name.rfind(".")
        if dot < 0:
            return False
        return name[dot:].lower() in ImagePipeline.SUPPORTED_EXTENSIONS

    @asynccontextmanager
    async def optimizer_target_lock(self, filename: str) -> AsyncIterator[None]:
        """引用计数方式持有同父目录、同 stem 图片共享的优化锁。

        waiter 在等待前计入 users，取消或完成后再递减；只有最后一个用户释放
        后才移除注册项，避免已有 waiter 与新请求取得不同锁。

        Args:
            filename: memes/ 下的 POSIX 相对路径。

        Yields:
            None；上下文期间当前任务独占目标锁。
        """
        path = self._memes_dir / filename
        key = (path.parent.as_posix().casefold(), path.stem.casefold())
        async with self._optimizer_registry_guard:
            entry = self._optimizer_target_locks.get(key)
            if entry is None:
                entry = _OptimizerLockEntry()
                self._optimizer_target_locks[key] = entry
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            release_task = asyncio.create_task(
                self.release_optimizer_lock_entry(key, entry)
            )
            _, release_error, cancelled = await self.wait_task_through_cancellation(
                release_task
            )
            if release_error is not None:
                raise release_error
            if cancelled:
                raise asyncio.CancelledError

    @staticmethod
    async def wait_task_through_cancellation(
        task: asyncio.Task[_T],
    ) -> tuple[_T | None, BaseException | None, bool]:
        """忽略调用者重复取消并等待独立 task 真正结束。

        每次收到外部取消后调用 ``uncancel()`` 清除本次注入，使下一轮 shield
        能阻塞等待而非忙循环。独立 task 的结果或异常始终被读取；调用者是否
        曾被取消通过返回值交由上层在清理完成后显式传播。

        Args:
            task: 从开始即独立运行且不得被外部取消传播的 task。

        Returns:
            task 结果、task 异常与等待期间是否收到过外部取消。
        """
        cancelled = False
        current_task = asyncio.current_task()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                external_cancel = (
                    current_task is not None and current_task.cancelling() > 0
                )
                if external_cancel:
                    cancelled = True
                    current_task.uncancel()
                if task.done():
                    break
            except BaseException:
                break
        try:
            return task.result(), None, cancelled
        except BaseException as exc:
            return None, exc, cancelled

    async def release_optimizer_lock_entry(
        self,
        key: tuple[str, str],
        entry: _OptimizerLockEntry,
    ) -> None:
        """可靠释放目标锁引用，并在最后一个用户离开时删除注册项。

        Args:
            key: 目标锁注册键。
            entry: 当前任务持有引用的注册项。
        """
        async with self._optimizer_registry_guard:
            entry.users -= 1
            if entry.users == 0 and self._optimizer_target_locks.get(key) is entry:
                del self._optimizer_target_locks[key]

    async def optimize_with_cancellation(
        self, filename: str
    ) -> tuple[OptimizeResult, set[Path]]:
        """持目标锁运行 optimizer，外部取消后等待实际操作结束再传播。

        Args:
            filename: memes/ 下的 POSIX 相对路径。

        Returns:
            optimizer 结果与调用前在目标父目录内存在的路径快照。

        Raises:
            asyncio.CancelledError: 外部取消或 optimizer 自身取消。
            Exception: optimizer 调用异常。
        """
        image_path = self._memes_dir / filename
        existing_paths: set[Path] = set()
        result: OptimizeResult | None = None
        try:
            async with self.optimizer_target_lock(filename):
                existing_paths = set(image_path.parent.iterdir())
                assert self._optimizer is not None
                optimize_task = asyncio.create_task(
                    self._optimizer.optimize(str(image_path))
                )
                (
                    result,
                    optimize_error,
                    cancelled,
                ) = await self.wait_task_through_cancellation(optimize_task)
                if optimize_error is not None:
                    if cancelled:
                        logger.error(
                            "外部取消后 optimizer 仍执行失败: filename=%s, error=%s",
                            filename,
                            optimize_error,
                        )
                        raise asyncio.CancelledError
                    raise optimize_error
                assert result is not None
                if cancelled:
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            if result is not None:
                final_path = Path(result.output_path)
                if final_path not in existing_paths:
                    self.cleanup_pipeline_output(final_path)
            raise
        assert result is not None
        return result, existing_paths

    @staticmethod
    def cleanup_pipeline_output(created_output: Path | None) -> None:
        """清理当前管线新建的输出，不让清理异常遮蔽原异常或取消。

        Args:
            created_output: 当前任务确认新建的最终输出；None 时不处理。
        """
        if created_output is None:
            return
        try:
            created_output.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("清理管线输出失败: path=%s, error=%s", created_output, exc)

    def validate_add_relative_path(self, relative_path: str) -> str:
        """校验 add 输入是 memes/ 内的规范 POSIX 相对路径。

        Args:
            relative_path: 待校验路径。

        Returns:
            校验后的原始 POSIX 相对路径。

        Raises:
            ValueError: 路径非规范相对路径、包含父目录跳转或解析到 memes/ 外。
        """
        path = Path(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or path.as_posix() != relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("relative_path 必须是 memes/ 内的规范 POSIX 相对路径")
        memes_dir = self._memes_dir.resolve()
        resolved_path = (self._memes_dir / path).resolve(strict=False)
        if not resolved_path.is_relative_to(memes_dir):
            raise ValueError("relative_path 解析后超出 memes/ 目录")
        return relative_path

    async def process(self, filename: str) -> tuple[str, str, list[float]]:
        """压缩 -> OCR -> Embedding 管道。

        optimize 后读取 result.output_path 作为最终路径；若与原 filename 不同（转 webp），
        final_filename 取 output_path 的文件名。optimize 失败时降级：清理可能已生成的
        .webp 孤儿，回退用原 filename 继续 OCR/embed，不抛错。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            (final_filename, text, embedding)：final_filename 可能与原 filename 不同
            （转 webp 后为 .webp）。

        Raises:
            OcrError: OCR 服务未注入或调用失败。
            EmbeddingError: Embedding 服务未注入或调用失败。
        """
        image_path = self._memes_dir / filename
        final_filename = filename
        created_output: Path | None = None
        if self._optimizer is not None:
            try:
                result, existing_paths = await self.optimize_with_cancellation(
                    filename
                )
                final_image_path = Path(result.output_path)
                if final_image_path not in existing_paths:
                    created_output = final_image_path
                final_filename = final_image_path.relative_to(
                    self._memes_dir
                ).as_posix()
                image_path = final_image_path
            except Exception as exc:
                # 降级：optimize 失败时 _convert_image_to_webp 内部已清理 .webp 孤儿，回退原 filename
                logger.warning(
                    "转 webp 失败，降级保留原格式: filename=%s, error=%s", filename, exc
                )
                final_filename = filename
                image_path = self._memes_dir / filename
        if self._ocr_provider is None:
            raise OcrError("OCR 服务未注入")
        try:
            text = await self._ocr_provider.ocr(str(image_path))
        except asyncio.CancelledError:
            self.cleanup_pipeline_output(created_output)
            raise
        except Exception as exc:
            self.cleanup_pipeline_output(created_output)
            raise OcrError(f"OCR 调用失败: {filename}") from exc
        text = "".join(text.split())  # 统一去除所有空白
        if not text:
            # 空文本不 embed，由下游 no_text 分支移图
            # （避免 provider 对空串抛 ValueError 导致 no_text 分支不可达）
            return final_filename, "", []
        if self._embedding_provider is None:
            raise EmbeddingError("Embedding 服务未注入")
        try:
            embedding = await self._embedding_provider.embed(text)
        except asyncio.CancelledError:
            self.cleanup_pipeline_output(created_output)
            raise
        except Exception as exc:
            self.cleanup_pipeline_output(created_output)
            raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc
        return final_filename, text, embedding

    def move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._no_text_dir, Path(filename).name)
        shutil.move(str(src), str(dst))
        logger.warning("OCR 未识别到文字，已移至无文字目录: %s -> %s", filename, dst)
        return str(dst)
