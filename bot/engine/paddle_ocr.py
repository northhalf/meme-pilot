"""PaddleOCR 云 API 客户端服务模块。

通过 paddleocr 库的 AsyncPaddleOCRClient 调用
PaddleOCR 官方云 API 进行图片文字识别。

实现 index_manager.OcrProvider 协议。
"""

import logging
import os

from paddleocr import (
    AsyncPaddleOCRClient,
    Model,
    OCROptions,
    PaddleOCRAPIError,
)

logger = logging.getLogger(__name__)

# pruned_result 中可能包含文本的常见字段名
_TEXT_FIELDS = frozenset({"text", "content", "transcription", "txt"})


def _extract_text(pruned_result: object, rec_score_threshold: float = 0.0) -> str:
    """从 pruned_result 中提取文本字符串。

    兼容多种返回格式：
    - str: 直接返回
    - list[dict]: 提取每个 dict 的 text/content/transcription/txt 字段，空格拼接
    - dict: 依次尝试 text/content/transcription/txt 字段，再试 rec_texts（列表），
            可配合 rec_scores 做置信度过滤
    - None: 返回空字符串

    Args:
        pruned_result: OCRResult.pages[i].pruned_result，类型 Any。
        rec_score_threshold: rec_scores 置信度阈值（0~1），低于此值的文本行被过滤；
                             0 表示不过滤。

    Returns:
        提取到的文本字符串。
    """
    if pruned_result is None:
        return ""

    # 直接是字符串
    if isinstance(pruned_result, str):
        return pruned_result

    # 列表：可能是 list[dict] 或 list[str]
    if isinstance(pruned_result, list):
        parts: list[str] = []
        for item in pruned_result:
            if isinstance(item, dict):
                for field in _TEXT_FIELDS:
                    value = item.get(field)
                    if value and isinstance(value, str):
                        parts.append(value)
                        break
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)

    # 单个 dict
    if isinstance(pruned_result, dict):
        # 尝试简单文本字段
        for field in _TEXT_FIELDS:
            value = pruned_result.get(field)
            if value and isinstance(value, str):
                return value
        # 尝试 rec_texts（PaddleOCR API 新版返回格式，list[str]）
        rec_texts = pruned_result.get("rec_texts")
        rec_scores = pruned_result.get("rec_scores")
        logger.debug("rec_texts: %s, rec_scores: %s", rec_texts, rec_scores)
        if isinstance(rec_texts, list):
            parts: list[str] = []
            has_scores = isinstance(rec_scores, list)
            for i, t in enumerate(rec_texts):
                if not t:
                    continue
                # 置信度过滤
                if has_scores and i < len(rec_scores):
                    score = rec_scores[i]
                    if isinstance(score, (int, float)) and score < rec_score_threshold:
                        logger.debug("过滤低置信度文本: text=%s, score=%s", t, score)
                        continue
                parts.append(str(t))
            if parts:
                return " ".join(parts)
            # 有 rec_texts 但全部被过滤 → 空字符串
            if has_scores and rec_score_threshold > 0:
                return ""

    # 兜底：转字符串
    text = str(pruned_result)
    return text if text.strip() else ""


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

    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        调用 AsyncPaddleOCRClient.ocr() 提交 OCR 任务并等待完成，
        从返回结果的 pruned_result 中提取文本。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（已去除所有空白字符，可能为空字符串）。

        Raises:
            RuntimeError: API 调用失败。
        """
        logger.debug("调用 PaddleOCR API: %s", image_path)
        try:
            result = await self._client.ocr(
                file_path=image_path,
                model=self._model,
                options=self._ocr_options,
            )
        except PaddleOCRAPIError as exc:
            raise RuntimeError(f"PaddleOCR API 调用失败: {exc}") from exc
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
