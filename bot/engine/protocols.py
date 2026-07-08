"""engine 包共享协议定义。

各模块共用的 Protocol 集中在此，避免重复定义。
"""

from typing import Protocol

from .metadata_store import MemeEntry
from .vector_store import VectorHit


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

    消费者（如 AIMatcher）通过此协议按 id 取 MemeEntry 构建候选，
    而非直接依赖具体的 MetadataStore 实现，便于测试用 mock 替换。
    与 MetadataStoreProvider（get_all_entries）接口不同，
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


class MetadataStoreProvider(Protocol):
    """元数据提供者协议。"""

    def get_all_entries(self) -> dict[int, MemeEntry]:
        """返回全部条目，key=int(id)。"""
        ...


class VectorQueryProvider(Protocol):
    """向量查询提供者协议。

    AIMatcher、SemanticSearcher 依赖此协议做向量召回，
    而非直接依赖具体的 VectorStore 实现，便于测试用 mock 替换。
    """

    def count(self) -> int:
        """返回当前向量数。"""
        ...

    async def query(
        self, query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]:
        """召回 Top-N。

        Args:
            query_embedding: 查询向量。
            n_results: 召回数量上限；None 表示全库召回。

        Returns:
            按 similarity 降序排列的 VectorHit 列表。
        """
        ...
