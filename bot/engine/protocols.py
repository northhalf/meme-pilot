"""共享 provider 协议。

IndexManager、SemanticSearcher 等模块通过这些协议调用外部服务，
便于测试时注入 mock。
"""

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Embedding 服务提供者协议。

    IndexManager、SemanticSearcher 等模块通过此协议调用 Embedding API，
    由启动入口注入具体实现。
    """

    async def embed(self, text: str) -> list[float]:
        """对文本生成 embedding 向量。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量（浮点数列表）。
        """
        ...

    async def close(self) -> None:
        """关闭 provider 占用的资源。"""
        ...


class OcrProvider(Protocol):
    """OCR 服务提供者协议。ocr() 返回去除所有空白后的文本。"""

    async def ocr(self, image_path: str) -> str: ...

    async def close(self) -> None:
        """关闭 provider 占用的资源。"""
        ...
