"""Embedding 服务模块 — 通用 OpenAI 兼容 Embedding API 封装。

通过 OpenAI 兼容的 embeddings API 调用向量化模型，
为 AI 语义匹配提供文本 embedding 生成能力。

支持任何兼容 OpenAI embeddings API 的服务商（如 GLM、SiliconFlow、OpenAI、
DeepSeek 等），只需配置 api_key、base_url 和 model 即可。

实现 ai_matcher.EmbeddingProvider 协议。
"""

import asyncio
import logging
import os

import openai
from openai import AsyncOpenAI

from bot.log_context import timed
from .retry_config import api_retry

logger = logging.getLogger(__name__)


class OpenAIEmbeddingService:
    """通用 Embedding 服务，通过 OpenAI 兼容 API 生成文本向量。

    实现 ai_matcher.EmbeddingProvider 协议，
    可直接注入给 AIMatcher 使用。

    Attributes:
        _client: AsyncOpenAI 客户端。
        _model: Embedding 模型名称。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        """初始化 OpenAIEmbeddingService。

        Args:
            api_key: API Key，默认从 OPENAI_EMBEDDING_API_KEY 环境变量读取。
            base_url: API 地址，默认从 OPENAI_EMBEDDING_BASE_URL 环境变量读取。
                      未提供时将使用 OpenAI SDK 的默认地址。
            model: Embedding 模型名，默认从 OPENAI_EMBEDDING_MODEL 环境变量读取。
                   未提供时由调用方/服务商决定，调用 embed() 前须确保已配置。
            concurrency: 并发数，默认从 EMBEDDING_CONCURRENCY 环境变量读取，
                         回退为 5。
        """
        self._api_key = api_key or os.environ.get("OPENAI_EMBEDDING_API_KEY", "")
        self._base_url = base_url or os.environ.get("OPENAI_EMBEDDING_BASE_URL")
        model_name = model or os.environ.get("OPENAI_EMBEDDING_MODEL")
        if not model_name:
            raise ValueError(
                "必须提供 Embedding 模型名（通过 model 参数或 OPENAI_EMBEDDING_MODEL 环境变量）"
            )
        self._model = model_name

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
        )

        c = concurrency or int(os.environ.get("EMBEDDING_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    @api_retry(
        extra_exceptions=(
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
    )
    async def embed(self, text: str) -> list[float]:
        """生成文本 embedding 向量。

        通过 OpenAI 兼容的 embeddings API 将文本转换为浮点向量。
        输出维度由配置的模型决定，须与现有 ChromaDB 索引维度保持一致。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量，浮点数列表。

        Raises:
            ValueError: 文本为空。
            RuntimeError: API 调用失败或返回为空。
        """
        async with timed(logger, "OpenAI Embedding"):
            text = text.strip()
            if not text:
                raise ValueError("待向量化文本不能为空")

            async with self._semaphore:
                logger.debug(
                    "调用 Embedding API: model=%s, text_len=%d",
                    self._model,
                    len(text),
                )
                try:
                    response = await self._client.embeddings.create(
                        model=self._model, input=text, dimensions=1024
                    )
                except openai.APIError:
                    # 让 tenacity 重试可重试的 OpenAI API 异常
                    raise
                except Exception as exc:
                    logger.info("Embedding API 调用失败: %s", exc)
                    raise RuntimeError(f"Embedding API 调用失败: {exc}") from exc

                if not response.data:
                    logger.info("Embedding API 返回为空")
                    raise RuntimeError("Embedding API 返回为空")

                embedding = response.data[0].embedding
                logger.debug("Embedding 完成: %d 维", len(embedding))
                return embedding

    async def close(self) -> None:
        """释放 AsyncOpenAI HTTP 客户端会话。"""
        await self._client.close()
        logger.debug("OpenAIEmbeddingService HTTP 会话已关闭")


def create_openai_embedding_service() -> OpenAIEmbeddingService:
    """从环境变量创建 OpenAI 兼容 Embedding 服务。"""
    from bot.config import read_int_env

    return OpenAIEmbeddingService(concurrency=read_int_env("EMBEDDING_CONCURRENCY"))
