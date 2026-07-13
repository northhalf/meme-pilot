"""RapidOCR 本地 OCR 服务模块。

使用本地 ONNX 模型进行图片文字识别，实现 index_manager.OcrProvider 协议。
"""

import asyncio
import logging
import os

from rapidocr import RapidOCR

from bot.config import read_int_env
from bot.log_context import timed

logger = logging.getLogger(__name__)


class RapidOcrService:
    """RapidOCR 本地 OCR 服务。

    使用本地 ONNX 模型进行图片文字识别，实现 index_manager.OcrProvider 协议。

    Attributes:
        _engine: RapidOCR 推理实例。
        _semaphore: 并发控制信号量。
        _text_score: 文本置信度阈值。
    """

    def __init__(
        self,
        text_score: float = 0.9,
        concurrency: int | None = None,
    ) -> None:
        """初始化 RapidOcrService。

        Args:
            text_score: 文本置信度阈值，默认 0.9。
            concurrency: 并发数，默认从 OCR_CONCURRENCY 环境变量读取，
                         回退为 5。
        """
        self._text_score = text_score
        if concurrency is not None:
            c = concurrency
        else:
            c = read_int_env("OCR_CONCURRENCY") or 5
        self._semaphore = asyncio.Semaphore(c)
        self._engine = RapidOCR(params={"Global.text_score": text_score})

    @timed(logger, "RapidOCR")
    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        调用 RapidOCR 本地引擎，默认启用检测+识别，关闭方向分类。
        识别结果按行置信度过滤后拼接，并去除所有空白字符。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（已去除所有空白字符，可能为空字符串）。

        Raises:
            FileNotFoundError: 图片文件不存在。
            RuntimeError: 推理异常。
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        async with self._semaphore:
            logger.debug("调用 RapidOCR: %s", image_path)
            try:
                result = await asyncio.to_thread(
                    self._engine,
                    image_path,
                    use_det=True,
                    use_cls=False,
                    use_rec=True,
                )
            except Exception as exc:
                raise RuntimeError(f"RapidOCR 推理失败: {exc}") from exc

            if result is None:
                return ""

            txts = getattr(result, "txts", None)
            scores = getattr(result, "scores", None)
            if not isinstance(txts, (tuple, list)):
                logger.debug("RapidOCR 返回结果无识别文本: %s", image_path)
                return ""

            scores_seq: tuple[object, ...] | list[object] | None = (
                scores if isinstance(scores, (tuple, list)) else None
            )

            lines: list[str] = []
            for i, text in enumerate(txts):
                if not text:
                    continue
                if scores_seq is not None and i < len(scores_seq):
                    score = scores_seq[i]
                    if isinstance(score, (int, float)) and score < self._text_score:
                        logger.debug("过滤低置信度文本: text=%s, score=%s", text, score)
                        continue
                lines.append(str(text))

            full_text = "".join(" ".join(lines).split())
            logger.debug("RapidOCR 完成: %s -> %s", image_path, full_text)
            return full_text

    async def close(self) -> None:
        """本地引擎无需释放网络会话。"""
        pass


def create_rapidocr_service() -> RapidOcrService:
    """从环境变量创建 RapidOCR 服务。"""
    from bot.config import read_ocr_text_score

    return RapidOcrService(
        text_score=read_ocr_text_score(),
        concurrency=read_int_env("OCR_CONCURRENCY"),
    )
