"""向量存储模块 — 基于 chromadb PersistentClient。

仅存 id（与 sqlite 完全一一对应）+ embedding（1024 维 float32）。
cosine 距离的 HNSW 索引，query 返回 Top-N (entry_id, similarity)。

设计要点：
- chroma 为同步库；upsert/remove/remove_many/query/rebuild_all 用 asyncio.to_thread 包装以避免阻塞事件循环；load()/close()/count() 为同步方法（仅供启动期或已在线程中调用）。
- chroma 并发写冲突 → 内部 threading.Lock 串行化所有访问。
- id 在内部与 chroma 交互时转 str，对外保持 int。
- similarity = 1 - distance（cosine distance ∈ [0,2]）。
- remove 不存在静默：chromadb delete 对不存在 id 本身即静默，无需捕获。
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

import chromadb

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    """单条向量召回结果。

    Attributes:
        entry_id: 索引 id（int，与 sqlite 一一对应）。
        similarity: 余弦相似度，= 1 - distance。
    """

    entry_id: int
    similarity: float


class VectorStore:
    """chromadb 向量存储。

    Attributes:
        _chroma_path: PersistentClient 数据目录。
        _collection_name: collection 名，默认 "memes"。
        _client: chromadb.PersistentClient。
        _collection: chroma Collection（cosine）。
        _lock: threading.Lock，串行化所有 chroma 访问。
    """

    def __init__(self, chroma_path: str, collection_name: str = "memes") -> None:
        """初始化 VectorStore。

        Args:
            chroma_path: chroma PersistentClient 数据目录，load() 时自动创建。
            collection_name: collection 名，默认 "memes"。
        """
        self._chroma_path = chroma_path
        self._collection_name = collection_name
        self._client: Any = None
        self._collection: chromadb.Collection | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """创建 PersistentClient 并 get_or_create_collection（cosine）。

        重复 load 时先 close 旧连接再重建。自动创建数据目录。
        """
        if self._client is not None:
            self.close()
        self._client = chromadb.PersistentClient(path=self._chroma_path)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "VectorStore 加载完成: %s, collection=%s, 共 %d 条向量",
            self._chroma_path,
            self._collection_name,
            self.count(),
        )

    def close(self) -> None:
        """调用 chromadb PersistentClient.close() 释放系统资源并置空引用。"""
        if self._client is not None:
            self._client.close()
        self._collection = None
        self._client = None

    def _require_client(self) -> Any:
        """返回当前 client，未 load 时抛 RuntimeError（同时让类型检查收窄为非 None）。

        chromadb 客户端类型导出不完善（ClientAPI 为私有导入），故用 Any。

        Returns:
            当前 chromadb PersistentClient 实例。

        Raises:
            RuntimeError: 未调用 load() 即使用其他方法时抛出。
        """
        if self._client is None:
            raise RuntimeError("VectorStore 未 load，请先调用 load()")
        return self._client

    def _require_collection(self) -> chromadb.Collection:
        """返回当前 collection，未 load 时抛 RuntimeError（同时让类型检查收窄为非 None）。

        Returns:
            当前 chromadb.Collection 实例。

        Raises:
            RuntimeError: 未调用 load() 即使用其他方法时抛出。
        """
        if self._collection is None:
            raise RuntimeError("VectorStore 未 load，请先调用 load()")
        return self._collection

    def _upsert_sync(self, entry_id: int, embedding: list[float]) -> None:
        """同步插入或覆盖一条向量（内部持 _lock，id 转 str）。

        Args:
            entry_id: 索引 id（内部转 str 与 chroma 交互）。
            embedding: 与 entry_id 对应的向量。
        """
        with self._lock:
            self._require_collection().upsert(
                ids=[str(entry_id)],
                embeddings=[embedding],
            )

    async def upsert(self, entry_id: int, embedding: list[float]) -> None:
        """插入或覆盖一条向量（id 内部转 str）。

        Args:
            entry_id: 索引 id。
            embedding: 与 entry_id 对应的向量。
        """
        await asyncio.to_thread(self._upsert_sync, entry_id, embedding)

    def _remove_sync(self, entry_id: int) -> None:
        """同步删除一条向量（内部持 _lock，id 转 str，不存在静默）。

        Args:
            entry_id: 要删除的索引 id。
        """
        with self._lock:
            self._require_collection().delete(ids=[str(entry_id)])

    async def remove(self, entry_id: int) -> None:
        """删除一条向量，不存在静默（chromadb delete 本身即静默）。

        Args:
            entry_id: 要删除的索引 id。
        """
        await asyncio.to_thread(self._remove_sync, entry_id)

    def _remove_many_sync(self, entry_ids: list[int]) -> None:
        """同步批量删除向量（内部持 _lock，id 转 str，不存在的静默）。

        Args:
            entry_ids: 要删除的索引 id 列表；为空时直接返回。
        """
        if not entry_ids:
            return
        with self._lock:
            self._require_collection().delete(ids=[str(i) for i in entry_ids])

    async def remove_many(self, entry_ids: list[int]) -> None:
        """批量删除向量，不存在的静默（chromadb delete 本身即静默）。

        Args:
            entry_ids: 要删除的索引 id 列表；为空时 no-op。
        """
        await asyncio.to_thread(self._remove_many_sync, entry_ids)

    def _query_sync(
        self, query_embedding: list[float], n_results: int | None
    ) -> list[VectorHit]:
        """同步召回 Top-N（内部持 _lock，entry_id 转 int 返回）。

        Args:
            query_embedding: 查询向量。
            n_results: 召回条数上限；None 表示全库召回。

        Returns:
            按 similarity 降序排列的 VectorHit 列表；collection 为空时返回 []。
        """
        with self._lock:
            collection = self._require_collection()
            total = collection.count()
            if total == 0:
                return []
            if n_results is None or n_results > total:
                n_results = total
            result = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
            )
        ids: list[list[str]] = result.get("ids") or [[]]
        distances: list[list[float]] = result.get("distances") or [[]]
        if not ids or not ids[0]:
            return []
        hits: list[VectorHit] = []
        for raw_id, dist in zip(ids[0], distances[0]):
            hits.append(VectorHit(entry_id=int(raw_id), similarity=1.0 - float(dist)))
        return hits

    async def query(
        self, query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]:
        """召回 Top-N，entry_id 转 int 返回。

        Args:
            query_embedding: 查询向量。
            n_results: 召回条数上限，默认 10；None 表示全库召回。

        Returns:
            按 similarity 降序排列的 VectorHit 列表；collection 为空时返回 []。
        """
        return await asyncio.to_thread(self._query_sync, query_embedding, n_results)

    def _rebuild_all_sync(self, items: list[tuple[int, list[float]]]) -> None:
        """同步全量重建：删 collection 重建（清空全部向量）后批量写入。

        Args:
            items: (entry_id, embedding) 列表；为空时仅重建空 collection。
        """
        with self._lock:
            # 全量重建：删除 collection 后重建（清空全部向量），再批量写入
            client = self._require_client()
            client.delete_collection(self._collection_name)
            self._collection = client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            if items:
                self._require_collection().upsert(
                    ids=[str(i) for i, _ in items],
                    embeddings=[vec for _, vec in items],
                )

    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None:
        """全量重建：删 collection 重建并批量写入。

        Args:
            items: (entry_id, embedding) 列表；为空时仅重建空 collection。
        """
        await asyncio.to_thread(self._rebuild_all_sync, items)

    def count(self) -> int:
        """当前向量数。

        Returns:
            collection 中现存向量数量。
        """
        with self._lock:
            return int(self._require_collection().count())
