"""Embedding 服务模块 — 通用 OpenAI 兼容 Embedding API 封装。

通过 OpenAI 兼容的 embeddings API 调用向量化模型，
为 AI 语义匹配提供文本 embedding 生成能力。

支持任何兼容 OpenAI embeddings API 的服务商（如 SiliconFlow、OpenAI、
DeepSeek 等），只需配置 base_url 和 model 即可。

默认使用 BAAI/bge-m3 模型，输出 1024 维向量。

实现 ai_matcher.EmbeddingProvider 协议。
"""

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class EmbeddingService:
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
    ) -> None:
        """初始化 EmbeddingService。

        Args:
            api_key: API Key，默认从 EMBEDDING_API_KEY 环境变量读取。
            base_url: API 地址，默认从 EMBEDDING_BASE_URL 环境变量读取，
                      回退为 https://api.siliconflow.cn/v1。
            model: Embedding 模型名，默认从 EMBEDDING_MODEL 环境变量读取，
                   回退为 BAAI/bge-m3（1024 维向量）。
        """
        self._api_key = api_key or os.environ.get("EMBEDDING_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1"
        )
        self._model = model or os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    async def embed(self, text: str) -> list[float]:
        """生成文本 embedding 向量。

        通过 OpenAI 兼容的 embeddings API 将文本转换为浮点向量。
        默认模型 BAAI/bge-m3 输出 1024 维向量。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量，浮点数列表（BAAI/bge-m3 为 1024 维）。

        Raises:
            ValueError: 文本为空。
            RuntimeError: API 调用失败或返回为空。
        """
        text = text.strip()
        if not text:
            raise ValueError("待向量化文本不能为空")

        logger.debug(
            "调用 Embedding API: model=%s, text_len=%d",
            self._model,
            len(text),
        )
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text,
            )
        except Exception as exc:
            raise RuntimeError(f"Embedding API 调用失败: {exc}") from exc

        if not response.data:
            raise RuntimeError("Embedding API 返回为空")

        embedding = response.data[0].embedding
        logger.debug("Embedding 完成: %d 维", len(embedding))
        return embedding
