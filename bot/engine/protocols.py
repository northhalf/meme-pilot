"""engine 包共享协议定义。

各模块共用的 Protocol 集中在此，避免重复定义。
"""

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Embedding 服务提供者协议。

    IndexManager、AIMatcher 等模块通过此协议调用 Embedding API，
    由插件层注入具体实现（如 EmbeddingService）。
    """

    async def embed(self, text: str) -> list[float]:
        """对文本生成 embedding 向量。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量（浮点数列表）。
        """
        ...
