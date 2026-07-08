"""engine 包共享协议定义。

各模块共用的 Protocol 集中在此，避免重复定义。
"""

from typing import Protocol

from .metadata_store import MemeEntry


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

    async def close(self) -> None:
        """关闭 provider 占用的资源。"""
        ...


class MetadataEntryProvider(Protocol):
    """元数据条目提供者协议。

    消费者（如 AIMatcher、SemanticSearcher）通过此协议按 id 取 MemeEntry 构建候选，
    而非直接依赖具体的 MetadataStore 实现，便于测试用 mock 替换。
    与 keyword_searcher.MetadataStoreProvider（get_all_entries）接口不同，
    此协议只暴露消费者实际使用的 get_entry。
    """

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按 id 取条目。

        Args:
            entry_id: 索引 id。

        Returns:
            匹配的 MemeEntry；不存在时返回 None。
        """
        ...
