"""OCR 服务模块 — 基于硅基流动 DeepSeek-OCR。

通过 OpenAI 兼容的 chat completions API 调用
deepseek-ai/DeepSeek-OCR 视觉模型进行图片文字识别。

实现 index_manager.OcrProvider 协议。
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# 匹配 DeepSeek-OCR 定位格式：<|ref|>text<|/ref|><|det|>[[coords]]<|/det|>
_TEXT_CLEAN_PATTERN = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>")


def _clean_ocr_result(raw: str) -> str:
    """清洗 DeepSeek-OCR 原始输出，提取纯文本。

    将 <|ref|>text<|/ref|><|det|>[[...]]<|/det|> 格式
    中的 text 部分提取出来，多段文本用空格连接。

    Args:
        raw: DeepSeek-OCR 原始 API 输出。

    Returns:
        清洗后的纯文本字符串。
    """
    matches = _TEXT_CLEAN_PATTERN.findall(raw)
    if not matches:
        # 没有匹配到 ref 标记，直接返回原始文本（可能已经是纯文本）
        return raw.strip()
    return " ".join(m.strip() for m in matches if m.strip())


class DeepSeekOcrService:
    """DeepSeek-OCR 服务，通过硅基流动 API 进行图片文字识别。

    实现 index_manager.OcrProvider 协议，
    可直接注入给 IndexManager 使用。

    Attributes:
        _client: AsyncOpenAI 客户端。
        _model: OCR 模型名称。
    """

    # 支持转为 base64 的图片 MIME 类型
    MIME_MAP: dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }

    # 通用 OCR prompt（DeepSeek-OCR 专用格式）
    OCR_PROMPT = "<image>\n<|grounding|>OCR this image."

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        """初始化 DeepSeekOcrService。

        Args:
            api_key: 硅基流动 API Key，默认从 SILICONFLOW_API_KEY 环境变量读取。
            base_url: API 地址，默认从 SILICONFLOW_BASE_URL 环境变量读取，
                      回退为 https://api.siliconflow.cn/v1。
            model: OCR 模型名，默认从 SILICONFLOW_OCR_MODEL 环境变量读取，
                   回退为 deepseek-ai/DeepSeek-OCR。
        """
        self._api_key = api_key or os.environ.get("SILICONFLOW_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
        )
        self._model = model or os.environ.get(
            "SILICONFLOW_OCR_MODEL", "deepseek-ai/DeepSeek-OCR"
        )

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        将图片转为 base64 后通过硅基流动 chat completions API
        调用 DeepSeek-OCR 视觉模型进行文字识别。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（已清洗定位标记，仅保留纯文本）。

        Raises:
            FileNotFoundError: 图片文件不存在。
            ValueError: 不支持的图片格式（不在 MIME_MAP 中）。
            RuntimeError: API 调用失败或返回为空。
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        suffix = path.suffix.lower()
        mime_type = self.MIME_MAP.get(suffix)
        if mime_type is None:
            raise ValueError(f"不支持的图片格式: {suffix}")

        # 读取并编码图片
        image_data = path.read_bytes()
        base64_data = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{base64_data}"

        # 调用 vision API
        logger.debug("调用 DeepSeek-OCR: %s", path.name)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                            {
                                "type": "text",
                                "text": self.OCR_PROMPT,
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"DeepSeek-OCR API 调用失败: {exc}") from exc

        raw = response.choices[0].message.content or ""
        text = _clean_ocr_result(raw)
        logger.debug("OCR 完成: %s → %d 字符", path.name, len(text))
        return text
