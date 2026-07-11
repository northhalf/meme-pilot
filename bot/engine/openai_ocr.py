"""OCR 服务模块 — 基于 OpenAI 兼容 API。

通过 OpenAI 兼容的 chat completions API 调用视觉模型进行图片文字识别。
实现 index_manager.OcrProvider 协议。
"""

import asyncio
import base64
import logging
import os
import re
from pathlib import Path

import openai
from openai import AsyncOpenAI

from bot.log_context import timed
from .retry_config import api_retry

logger = logging.getLogger(__name__)

# 匹配 DeepSeek-OCR 定位格式：<|ref|>text<|/ref|><|det|>[[coords]]<|/det|>
_TEXT_CLEAN_PATTERN = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>")


def _clean_ocr_result(raw: str) -> str:
    """清洗 OCR 原始输出，提取纯文本。

    将 <|ref|>text<|/ref|><|det|>[[...]]<|/det|> 格式
    中的 text 部分提取出来，多段文本用空格连接。

    Args:
        raw: OCR 原始 API 输出。

    Returns:
        清洗后的纯文本字符串。
    """
    matches = _TEXT_CLEAN_PATTERN.findall(raw)
    if not matches:
        # 没有匹配到 ref 标记，直接返回原始文本（可能已经是纯文本）
        return raw.strip()
    return " ".join(m.strip() for m in matches if m.strip())


class OpenAIOcrService:
    """OpenAI 兼容 OCR 服务，通过 OpenAI 兼容 API 进行图片文字识别。

    实现 index_manager.OcrProvider 协议，可直接注入给 IndexManager 使用。

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
        concurrency: int | None = None,
    ) -> None:
        """初始化 OpenAIOcrService。

        Args:
            api_key: API Key，默认从 OPENAI_OCR_API_KEY 环境变量读取。
            base_url: API 地址，默认从 OPENAI_OCR_BASE_URL 环境变量读取。
                      未提供时将使用 OpenAI SDK 的默认地址。
            model: OCR 模型名，默认从 OPENAI_OCR_MODEL 环境变量读取。
                   调用 ocr() 前须确保已配置。
            concurrency: 并发数，默认从 OCR_CONCURRENCY 环境变量读取，
                         回退为 5。
        """
        self._api_key = api_key or os.environ.get("OPENAI_OCR_API_KEY", "")
        self._base_url = base_url or os.environ.get("OPENAI_OCR_BASE_URL")
        model_name = model or os.environ.get("OPENAI_OCR_MODEL")
        if not model_name:
            raise ValueError(
                "必须提供 OCR 模型名（通过 model 参数或 OPENAI_OCR_MODEL 环境变量）"
            )
        self._model = model_name

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
        )

        c = concurrency or int(os.environ.get("OCR_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    @api_retry(
        extra_exceptions=(
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
    )
    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        将图片转为 base64 后通过 OpenAI 兼容 chat completions API
        调用视觉模型进行文字识别。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（已清洗定位标记并去除所有空白字符）。

        Raises:
            FileNotFoundError: 图片文件不存在。
            ValueError: 不支持的图片格式（不在 MIME_MAP 中）。
            RuntimeError: API 调用失败或返回为空。
        """
        async with timed(logger, "OpenAI OCR"):
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
            async with self._semaphore:
                logger.debug("调用 OCR API: %s", path.name)
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
                except openai.APIError:
                    # 让 tenacity 重试可重试的 OpenAI API 异常
                    raise
                except Exception as exc:
                    raise RuntimeError(f"OCR API 调用失败: {exc}") from exc

                raw = response.choices[0].message.content or ""
                text = "".join(_clean_ocr_result(raw).split())
                logger.debug("OCR 完成: %s → %d 字符", path.name, len(text))
                return text

    async def close(self) -> None:
        """释放 AsyncOpenAI HTTP 客户端会话。"""
        await self._client.close()
        logger.debug("OpenAIOcrService HTTP 会话已关闭")


def create_openai_ocr_service() -> OpenAIOcrService:
    """从环境变量创建 OpenAI OCR 服务。"""
    from bot.config import read_int_env

    return OpenAIOcrService(concurrency=read_int_env("OCR_CONCURRENCY"))
