"""Google Embedding API 服务模块。"""

import asyncio
import logging
import os
from typing import Any

from google import genai
from google.genai import errors as _genai_errors
from google.genai import types

from bot.log_context import timed

from .retry_config import api_retry

# Google GenAI SDK 异常类可能随版本变化，做防御性导入
try:
    _GOOGLE_API_ERROR: tuple[type[Exception], ...] = (_genai_errors.APIError,)
except AttributeError:
    _GOOGLE_API_ERROR = ()

logger = logging.getLogger(__name__)


class GoogleEmbeddingService:
    """Google Embedding 服务，通过 Google GenAI SDK 生成文本向量。

    实现 protocols.EmbeddingProvider 协议。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        """初始化 GoogleEmbeddingService。

        Args:
            api_key: API Key，默认从 GOOGLE_API_KEY 环境变量读取。
            base_url: API 地址，默认从 GOOGLE_BASE_URL 环境变量读取。
            model: Embedding 模型名，默认从 GOOGLE_EMBEDDING_MODEL 环境变量读取。
                   未提供时由 Google API 服务端决定，调用 embed() 前须确保已配置。
            concurrency: 并发数，默认从 EMBEDDING_CONCURRENCY 环境变量读取，回退为 5。
        """
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._base_url = base_url or os.environ.get("GOOGLE_BASE_URL")
        model_name = model or os.environ.get("GOOGLE_EMBEDDING_MODEL")
        if not model_name:
            raise ValueError(
                "必须提供 Google Embedding 模型名（通过 model 参数或 GOOGLE_EMBEDDING_MODEL 环境变量）"
            )
        self._model = model_name

        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["http_options"] = {"base_url": self._base_url}
        self._client = genai.Client(**client_kwargs)

        c = concurrency or int(os.environ.get("EMBEDDING_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    @api_retry(extra_exceptions=_GOOGLE_API_ERROR)
    @timed(logger, "Google Embedding")
    async def embed(self, text: str) -> list[float]:
        """生成文本 embedding 向量。

        Google GenAI SDK 当前为同步 API，通过 asyncio.to_thread 在线程池中调用，
        固定请求 1024 维输出，与现有 ChromaDB 索引维度保持一致。

        Args:
            text: 待向量化的文本。

        Returns:
            1024 维 embedding 向量。

        Raises:
            ValueError: 文本为空。
            RuntimeError: API 调用失败或返回为空。
        """
        text = text.strip()
        if not text:
            raise ValueError("待向量化文本不能为空")

        async with self._semaphore:
            logger.debug("调用 Google Embedding API: model=%s", self._model)
            try:
                response = await asyncio.to_thread(
                    self._client.models.embed_content,
                    model=self._model,
                    contents=text,
                    config=types.EmbedContentConfig(output_dimensionality=1024),
                )
            except Exception as exc:
                logger.info("Google Embedding API 调用失败: %s", exc)
                raise RuntimeError(f"Google Embedding API 调用失败: {exc}") from exc

            if not response.embeddings:
                logger.info("Google Embedding API 返回为空")
                raise RuntimeError("Google Embedding API 返回为空")

            embedding = response.embeddings[0].values
            if embedding is None:
                logger.info("Google Embedding API 返回为空")
                raise RuntimeError("Google Embedding API 返回为空")
            logger.debug("Embedding 完成: %d 维", len(embedding))
            return embedding

    async def close(self) -> None:
        """关闭服务。

        Google GenAI 同步 Client 持有 HTTP 连接池，调用其 close() 可释放底层
        网络资源。由于 close() 是同步方法，通过 asyncio.to_thread 在线程池中
        执行，避免阻塞事件循环。
        """
        await asyncio.to_thread(self._client.close)


def create_google_embedding_service() -> GoogleEmbeddingService:
    """从环境变量创建 Google Embedding 服务。"""
    from bot.config import read_int_env

    return GoogleEmbeddingService(concurrency=read_int_env("EMBEDDING_CONCURRENCY"))
