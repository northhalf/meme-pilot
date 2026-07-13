"""PaddleOCR 云 API 客户端服务模块。

通过 paddleocr 库的 AsyncPaddleOCRClient 调用
PaddleOCR 官方云 API 进行图片文字识别。

实现 index_manager.OcrProvider 协议。
"""

import asyncio
import logging
import os
from typing import cast

from paddleocr import (
    AsyncPaddleOCRClient,
    Model,
    NetworkError,
    OCROptions,
    PaddleOCRAPIError,
    PollTimeoutError,
    RateLimitError,
    RequestTimeoutError,
    ServiceUnavailableError,
)

from bot.log_context import timed

from .retry_config import api_retry

logger = logging.getLogger(__name__)

# PaddleOCR 云 API 返回的 pruned_result 中，承载识别文本的常见结构。
#
# 当前支持的格式规律（基于 paddleocr*.jsonc 实测）：
# 1. 通用 OCR（PP-OCR v6 等）：dict 直接包含 "rec_texts": list[str] 与可选 "rec_scores": list[float]
# 2. 文档解析（PP-Structure v3）：dict 包含 "overall_ocr_res": dict，
#    其下再包含 "rec_texts" / "rec_scores"
#
# 不支持的格式：PaddleOCR-VL-1.6 等视觉语言模型返回的对话/多模态格式。


def _extract_rec_texts(
    data: dict[str, object], rec_score_threshold: float
) -> tuple[list[str], bool]:
    """从包含 rec_texts/rec_scores 的字典中提取文本行。

    Args:
        data: 可能包含 rec_texts 与 rec_scores 的字典。
        rec_score_threshold: rec_scores 置信度阈值（0~1），低于此值的文本行被过滤；
                             0 表示不过滤。

    Returns:
        (过滤后的文本行列表, 是否存在 rec_texts 字段)。
        当存在 rec_texts 但全部被过滤时，返回空列表和 True，调用方应整体返回空字符串，
        避免回退到 str() 兜底。
    """
    rec_texts = data.get("rec_texts")
    rec_scores = data.get("rec_scores")
    if not isinstance(rec_texts, list):
        return [], False

    has_scores = isinstance(rec_scores, list)
    parts: list[str] = []
    for i, item in enumerate(rec_texts):
        if not item:
            continue
        if has_scores and i < len(rec_scores):
            score = rec_scores[i]
            if isinstance(score, (int, float)) and score < rec_score_threshold:
                logger.debug("过滤低置信度文本: text=%s, score=%s", item, score)
                continue
        parts.append(str(item))
    return parts, True


def _extract_text(pruned_result: object, rec_score_threshold: float = 0.0) -> str:
    """从 pruned_result 中提取文本字符串。

    仅支持 PaddleOCR 云 API 实测返回的两种结构（见模块顶部注释）。
    PaddleOCR-VL-1.6 返回格式不在支持范围内。

    Args:
        pruned_result: OCRResult.pages[i].pruned_result，类型任意。
        rec_score_threshold: rec_scores 置信度阈值（0~1），低于此值的文本行被过滤；
                             0 表示不过滤。

    Returns:
        提取到的文本字符串，多行之间以空格分隔。
    """
    if pruned_result is None:
        return ""

    if not isinstance(pruned_result, dict):
        # 实测中 pruned_result 只可能出现 dict，其余类型按无文本处理
        return ""

    # 1) 通用 OCR：dict 直接包含 rec_texts
    pruned_result = cast(dict[str, object], pruned_result)
    parts, had_rec_texts = _extract_rec_texts(pruned_result, rec_score_threshold)
    if had_rec_texts:
        return " ".join(parts)

    # 2) 文档解析（PP-Structure v3）：文本嵌套在 overall_ocr_res 下
    overall_ocr_res = pruned_result.get("overall_ocr_res")
    overall_ocr_res = cast(dict[str, object], overall_ocr_res)
    if isinstance(overall_ocr_res, dict):
        parts, had_rec_texts = _extract_rec_texts(overall_ocr_res, rec_score_threshold)
        if had_rec_texts:
            return " ".join(parts)

    # 未识别到支持的文本结构
    return ""


class PaddleOcrClientService:
    """PaddleOCR 云 API OCR 服务。

    使用 AsyncPaddleOCRClient 调用 PaddleOCR 官方云 API
    进行图片文字识别。实现 index_manager.OcrProvider 协议。

    Attributes:
        _client: AsyncPaddleOCRClient 实例。
        _model: 使用的模型枚举值。
    """

    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        model: Model | str | None = None,
        request_timeout: float = 300.0,
        poll_timeout: float = 600.0,
        text_rec_score_thresh: float = 0.9,
        concurrency: int | None = None,
    ) -> None:
        """初始化 PaddleOcrClientService。

        Args:
            access_token: AIStudio Access Token，默认从 PADDLEOCR_ACCESS_TOKEN
                          环境变量读取。
            base_url: API 地址，默认从 PADDLEOCR_BASE_URL 环境变量读取。
            model: OCR 模型，默认 Model.PP_OCRV6。
            request_timeout: 请求超时秒数，默认 300。
            poll_timeout: 轮询超时秒数，默认 600。
            text_rec_score_thresh: rec_scores 置信度阈值（0~1），低于此值的
                文本行被过滤。默认 0.9，设为 0 关闭过滤。
            concurrency: 并发数，默认从 OCR_CONCURRENCY 环境变量读取，
                         回退为 5。
        """
        token = access_token or os.environ.get("PADDLEOCR_ACCESS_TOKEN", "")
        api_base_url = base_url or os.environ.get("PADDLEOCR_BASE_URL")

        self._model = model or Model.PP_OCRV6
        self._text_rec_score_thresh = text_rec_score_thresh
        # 默认 OCR 选项：禁用文档预处理和文本行方向检测，
        # 避免预处理将多行文本合并为单行，影响多行文字识别
        self._ocr_options = OCROptions(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        self._client = AsyncPaddleOCRClient(
            token=token,
            base_url=api_base_url,
            request_timeout=request_timeout,
            poll_timeout=poll_timeout,
        )

        c = concurrency or int(os.environ.get("OCR_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    @api_retry(
        extra_exceptions=(
            NetworkError,
            RequestTimeoutError,
            PollTimeoutError,
            RateLimitError,
            ServiceUnavailableError,
        )
    )
    @timed(logger, "PaddleOCR")
    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        调用 AsyncPaddleOCRClient.ocr() 提交 OCR 任务并等待完成，
        从返回结果的 pruned_result 中提取文本。

        可重试的瞬时异常（NetworkError/RequestTimeoutError/PollTimeoutError/
        RateLimitError/ServiceUnavailableError）由 retry_config.api_retry 按指数
        退避重试，默认最多 3 次；不可重试的 API 错误（如 AuthError、
        InvalidRequestError）及重试耗尽后的异常以原始类型抛出。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（已去除所有空白字符，可能为空字符串）。

        Raises:
            PaddleOCRAPIError: 不可重试的 API 错误（如鉴权失败、参数非法），
                或可重试异常重试耗尽后以原始类型抛出。
            RuntimeError: 非 API 异常（如未预期的本地错误）。
        """
        async with self._semaphore:
            logger.debug("调用 PaddleOCR API: %s", image_path)
            try:
                result = await self._client.ocr(
                    file_path=image_path,
                    model=self._model,
                    options=self._ocr_options,
                )
            except PaddleOCRAPIError:
                # 透传给 @api_retry：可重试子类按 extra_exceptions 重试，
                # 不可重试子类（AuthError/InvalidRequestError 等）由装饰器 reraise
                raise
            except Exception as exc:
                raise RuntimeError(f"PaddleOCR 调用异常: {exc}") from exc

            # 提取文本
            if not result.pages:
                logger.debug("PaddleOCR 无识别结果: %s", image_path)
                return ""

            texts: list[str] = []
            for page in result.pages:
                text = _extract_text(page.pruned_result, self._text_rec_score_thresh)
                if text:
                    texts.append(text)

            full_text = "".join(" ".join(texts).split())
            logger.debug("PaddleOCR 完成: %s → %s", image_path, full_text)
            return full_text

    async def close(self) -> None:
        """释放 AsyncPaddleOCRClient 内部 HTTP 会话。"""
        await self._client.close()
        logger.debug("PaddleOcrClientService HTTP 会话已关闭")


def create_paddle_ocr_service() -> PaddleOcrClientService:
    """从环境变量创建 PaddleOCR 云服务。"""
    from bot.config import read_int_env, read_ocr_text_score

    return PaddleOcrClientService(
        text_rec_score_thresh=read_ocr_text_score(),
        concurrency=read_int_env("OCR_CONCURRENCY"),
    )
