"""向量存储模块 — 基于 chromadb PersistentClient。

存储与 sqlite 一一对应的内部 id、embedding 和合集 metadata。使用 cosine
距离的 HNSW 索引，query 返回 Top-N (entry_id, similarity)。

设计要点：
- chroma 为同步库；公开异步方法用 asyncio.to_thread 包装，避免阻塞事件循环。
- chroma 并发访问由内部 threading.Lock 串行化。
- id 在内部与 chroma 交互时转 str，对外保持 int。
- similarity = 1 - distance（cosine distance ∈ [0, 2]）。
- metadata 更新是完整覆盖；更新单个字段前必须读取并合并完整 metadata。
"""

import asyncio
import logging
import math
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from numbers import Real
from typing import Any, overload

import chromadb

from bot.log_context import timed

logger = logging.getLogger(__name__)

VectorMetadata = dict[str, str | int | float | bool]


@dataclass(slots=True)
class VectorHit:
    """单条向量召回结果。

    Attributes:
        entry_id: 索引 id（int，与 sqlite 一一对应）。
        similarity: 余弦相似度，= 1 - distance。
    """

    entry_id: int
    similarity: float


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """迁移补偿使用的完整 Chroma 记录。

    Attributes:
        entry_id: 索引 id。
        embedding: 独立复制的向量值列表。
        metadata: 独立复制的完整 metadata。
    """

    entry_id: int
    embedding: list[float]
    metadata: VectorMetadata


def _validate_entry_id(entry_id: object) -> int:
    """校验内部向量 ID 是正整数。

    Args:
        entry_id: 待校验的内部向量 ID。

    Returns:
        校验后的内部向量 ID。

    Raises:
        ValueError: ID 是 bool、非整数、零或负数。
    """
    if type(entry_id) is not int or entry_id <= 0:
        raise ValueError("entry_id 必须是正整数")
    return entry_id


def _validate_collection_id(collection_id: object) -> int:
    """校验合集编号是非负的真正整数。

    Args:
        collection_id: 待校验的合集编号。

    Returns:
        校验后的合集编号。

    Raises:
        ValueError: 编号是 bool、非整数或负数。
    """
    if type(collection_id) is not int or collection_id < 0:
        raise ValueError("collection_id 必须是大于等于 0 的整数")
    return collection_id


def _validate_n_results(n_results: object | None) -> int | None:
    """校验查询结果上限是正整数或 None。

    Args:
        n_results: 待校验的查询结果上限。

    Returns:
        校验后的查询结果上限。

    Raises:
        ValueError: 上限是 bool、非整数、零或负数。
    """
    if n_results is None:
        return None
    if type(n_results) is not int or n_results <= 0:
        raise ValueError("n_results 必须是正整数或 None")
    return n_results


def _copy_metadata(metadata: object) -> VectorMetadata:
    """校验并复制一份 Chroma metadata。

    Args:
        metadata: 待校验的 metadata。

    Returns:
        与输入不共享别名的 metadata。

    Raises:
        ValueError: metadata 不是字典，或键值类型不受支持。
    """
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须是字典")
    copied: VectorMetadata = {}
    for key, value in metadata.items():
        if type(key) is not str:
            raise ValueError("metadata 的键必须是 str")
        if isinstance(value, bool):
            copied[key] = value
        elif isinstance(value, (str, int, float)):
            copied[key] = value
        else:
            raise ValueError("metadata 的值必须是 str、int、float 或 bool")
    return copied


def _copy_stored_metadata(metadata: object | None) -> VectorMetadata:
    """复制 Chroma 返回的 metadata，并把缺失值转换为空字典。

    Args:
        metadata: Chroma 返回的 metadata 或 None。

    Returns:
        独立复制的 metadata；None 转换为 `{}`。

    Raises:
        RuntimeError: Chroma 返回了不合法的 metadata。
    """
    if metadata is None:
        return {}
    try:
        return _copy_metadata(metadata)
    except ValueError as exc:
        raise RuntimeError("Chroma 返回了不合法的 metadata") from exc


def _copy_input_embedding(embedding: object) -> list[float]:
    """校验并复制待写入 Chroma 的 embedding。

    Args:
        embedding: 待校验的向量。

    Returns:
        由有限浮点数组成的独立列表。

    Raises:
        ValueError: 向量不是非空列表，或包含 bool、非数值或非有限值。
    """
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("embedding 必须是非空列表")
    copied: list[float] = []
    for value in embedding:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("embedding 必须只包含数值")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("embedding 必须只包含有限数值")
        copied.append(number)
    return copied


def _copy_chroma_embedding(embedding: object) -> list[float]:
    """严格复制 Chroma 返回的 embedding。

    Args:
        embedding: Chroma 返回的单条向量。

    Returns:
        独立的浮点数列表。

    Raises:
        RuntimeError: 向量缺失、不可迭代、为空或包含非法数值。
    """
    if (
        embedding is None
        or isinstance(embedding, (str, bytes, dict))
        or not isinstance(embedding, Iterable)
    ):
        raise RuntimeError("Chroma 返回的 embedding 不可迭代")
    values = list(embedding)
    if not values:
        raise RuntimeError("Chroma 返回了空 embedding")
    copied: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise RuntimeError("Chroma 返回的 embedding 包含非法数值")
        number = float(value)
        if not math.isfinite(number):
            raise RuntimeError("Chroma 返回的 embedding 包含非有限数值")
        copied.append(number)
    return copied


def _prepare_restore_records(records: list[VectorRecord]) -> list[VectorRecord]:
    """在修改 Chroma 前校验并深复制全部恢复记录。

    Args:
        records: 待恢复的完整记录。

    Returns:
        与输入列表、embedding 和 metadata 均不共享别名的记录列表。

    Raises:
        ValueError: 记录类型、ID、embedding 或 metadata 不合法，或 ID 重复。
    """
    prepared: list[VectorRecord] = []
    seen_ids: set[int] = set()
    for record in records:
        if not isinstance(record, VectorRecord):
            raise ValueError("records 必须只包含 VectorRecord")
        if type(record.entry_id) is not int or record.entry_id <= 0:
            raise ValueError("VectorRecord.entry_id 必须是正整数")
        if record.entry_id in seen_ids:
            raise ValueError(f"VectorRecord.entry_id 重复: {record.entry_id}")
        seen_ids.add(record.entry_id)
        prepared.append(
            VectorRecord(
                entry_id=record.entry_id,
                embedding=_copy_input_embedding(record.embedding),
                metadata=_copy_metadata(record.metadata),
            )
        )
    return prepared


class VectorStore:
    """chromadb 向量存储。"""

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
        """创建 PersistentClient 并 get_or_create_collection（cosine）。"""
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
        """调用 PersistentClient.close() 释放资源并置空引用。"""
        if self._client is not None:
            self._client.close()
        self._collection = None
        self._client = None

    def _require_client(self) -> Any:
        """返回当前 client，未 load 时抛 RuntimeError。

        Returns:
            当前 chromadb PersistentClient 实例。

        Raises:
            RuntimeError: 未调用 load() 即使用其他方法。
        """
        if self._client is None:
            raise RuntimeError("VectorStore 未 load，请先调用 load()")
        return self._client

    def _require_collection(self) -> chromadb.Collection:
        """返回当前 collection，未 load 时抛 RuntimeError。

        Returns:
            当前 chromadb.Collection 实例。

        Raises:
            RuntimeError: 未调用 load() 即使用其他方法。
        """
        if self._collection is None:
            raise RuntimeError("VectorStore 未 load，请先调用 load()")
        return self._collection

    @staticmethod
    def _parse_metadata_result(result: Any) -> tuple[list[str], list[object | None]]:
        """校验 Chroma get 的 ids/metadatas 扁平结果形状。

        Args:
            result: Chroma `get()` 返回值。

        Returns:
            ids 与 metadatas 列表。

        Raises:
            RuntimeError: 字段缺失、类型错误或长度不一致。
        """
        if not isinstance(result, dict):
            raise RuntimeError("Chroma get 返回值不是字典")
        ids = result.get("ids")
        metadatas = result.get("metadatas")
        if not isinstance(ids, list) or not isinstance(metadatas, list):
            raise RuntimeError("Chroma get 未返回 ids/metadatas 列表")
        if len(ids) != len(metadatas):
            raise RuntimeError("Chroma 返回的 ids 与 metadatas 数量不一致")
        if not all(isinstance(raw_id, str) for raw_id in ids):
            raise RuntimeError("Chroma 返回了非字符串 id")
        return ids, metadatas

    @staticmethod
    def _parse_query_result(result: Any) -> tuple[list[str], list[float]]:
        """校验单向量 Chroma query 的 ids/distances 结果形状。

        Args:
            result: Chroma `query()` 返回值。

        Returns:
            第一组 ids 与 distances。

        Raises:
            RuntimeError: 字段缺失、嵌套形状错误或长度不一致。
        """
        if not isinstance(result, dict):
            raise RuntimeError("Chroma query 返回值不是字典")
        ids_groups = result.get("ids")
        distance_groups = result.get("distances")
        if not isinstance(ids_groups, list) or not isinstance(distance_groups, list):
            raise RuntimeError("Chroma query 未返回 ids/distances 列表")
        if len(ids_groups) != 1 or len(distance_groups) != 1:
            raise RuntimeError("Chroma query 返回的查询组数量无效")
        ids = ids_groups[0]
        distances = distance_groups[0]
        if not isinstance(ids, list) or not isinstance(distances, list):
            raise RuntimeError("Chroma query 返回的嵌套结果形状无效")
        if len(ids) != len(distances):
            raise RuntimeError("Chroma 返回的 ids 与 distances 数量不一致")
        if not all(isinstance(raw_id, str) for raw_id in ids):
            raise RuntimeError("Chroma 返回了非字符串 id")
        parsed_distances: list[float] = []
        for distance in distances:
            try:
                parsed_distances.append(float(distance))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Chroma 返回了非法 distance") from exc
        return ids, parsed_distances

    def _upsert_sync(
        self,
        entry_id: int,
        embedding: list[float],
        collection_id: int,
    ) -> None:
        """同步插入或覆盖一条向量和合集 metadata。

        Args:
            entry_id: 索引 id。
            embedding: 与 entry_id 对应的向量副本。
            collection_id: 非负合集编号。
        """
        with self._lock:
            self._require_collection().upsert(
                ids=[str(entry_id)],
                embeddings=[embedding],
                metadatas=[{"collection_id": collection_id}],
            )

    @timed(logger, "VectorStore upsert")
    async def upsert(
        self,
        entry_id: int,
        embedding: list[float],
        *,
        collection_id: int = 0,
    ) -> None:
        """插入或覆盖一条向量及其合集 metadata。

        Args:
            entry_id: 索引 id。
            embedding: 与 entry_id 对应的向量。
            collection_id: 合集编号，必须是大于等于 0 的真正整数。

        Raises:
            ValueError: entry_id、embedding 或 collection_id 不合法。
        """
        validated_entry_id = _validate_entry_id(entry_id)
        embedding_copy = _copy_input_embedding(embedding)
        validated_collection_id = _validate_collection_id(collection_id)
        logger.debug(
            "upsert 向量 id=%d, 维度=%d",
            validated_entry_id,
            len(embedding_copy),
        )
        await asyncio.to_thread(
            self._upsert_sync,
            validated_entry_id,
            embedding_copy,
            validated_collection_id,
        )

    def _remove_sync(self, entry_id: int) -> None:
        """同步删除一条向量。

        Args:
            entry_id: 要删除的索引 id。
        """
        with self._lock:
            self._require_collection().delete(ids=[str(entry_id)])

    async def remove(self, entry_id: int) -> None:
        """删除一条向量，不存在时静默。

        Args:
            entry_id: 要删除的索引 id。
        """
        await asyncio.to_thread(self._remove_sync, entry_id)

    def _remove_many_sync(self, entry_ids: list[int]) -> None:
        """同步批量删除向量。

        Args:
            entry_ids: 要删除的索引 id 列表；为空时直接返回。
        """
        if not entry_ids:
            return
        with self._lock:
            self._require_collection().delete(ids=[str(i) for i in entry_ids])

    @timed(logger, "VectorStore 批量删除")
    async def remove_many(self, entry_ids: list[int]) -> None:
        """批量删除向量，不存在时静默。

        Args:
            entry_ids: 要删除的索引 id 列表；为空时 no-op。
        """
        entry_ids_copy = list(entry_ids)
        logger.debug("删除向量: %s", entry_ids_copy)
        await asyncio.to_thread(self._remove_many_sync, entry_ids_copy)

    def _query_sync(
        self,
        query_embedding: list[float],
        n_results: int | None,
        collection_id: int | None,
    ) -> list[VectorHit]:
        """同步召回 Top-N，可按合集 metadata 过滤。

        Args:
            query_embedding: 查询向量副本。
            n_results: 召回条数上限；None 表示全库召回。
            collection_id: None 查询全部合集；非负整数只查询该合集。

        Returns:
            按 similarity 降序排列的 VectorHit 列表。

        Raises:
            RuntimeError: Chroma 返回结果形状无效。
        """
        with self._lock:
            collection = self._require_collection()
            total = collection.count()
            if total == 0:
                return []
            actual_n_results = n_results
            if actual_n_results is None or actual_n_results > total:
                actual_n_results = total
            if collection_id is None:
                result = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=actual_n_results,
                )
            else:
                result = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=actual_n_results,
                    where={"collection_id": collection_id},
                )
        ids, distances = self._parse_query_result(result)
        return [
            VectorHit(entry_id=int(raw_id), similarity=1.0 - distance)
            for raw_id, distance in zip(ids, distances, strict=True)
        ]

    async def query(
        self,
        query_embedding: list[float],
        n_results: int | None = 10,
        *,
        collection_id: int | None = None,
    ) -> list[VectorHit]:
        """召回 Top-N，可按合集 metadata 过滤。

        Args:
            query_embedding: 查询向量。
            n_results: 召回条数上限，默认 10；None 表示全库召回。
            collection_id: None 表示不传 where、查询全部合集；非负整数
                表示只查询该合集，其中 0 表示根目录合集。

        Returns:
            按 similarity 降序排列的 VectorHit 列表。

        Raises:
            ValueError: n_results 或 collection_id 不合法。
            RuntimeError: Chroma 返回结果形状无效。
        """
        validated_n_results = _validate_n_results(n_results)
        validated_collection_id = (
            None if collection_id is None else _validate_collection_id(collection_id)
        )
        result = await asyncio.to_thread(
            self._query_sync,
            list(query_embedding),
            validated_n_results,
            validated_collection_id,
        )
        logger.debug("向量查询返回 %d 个候选", len(result))
        return result

    def _rebuild_all_sync(self, items: list[tuple[int, list[float], int]]) -> None:
        """同步全量重建，并写入每条记录的合集 metadata。

        Args:
            items: (entry_id, embedding, collection_id) 列表；为空时仅重建空
                collection。
        """
        with self._lock:
            client = self._require_client()
            client.delete_collection(self._collection_name)
            self._collection = client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            if items:
                self._require_collection().upsert(
                    ids=[str(entry_id) for entry_id, _, _ in items],
                    embeddings=[embedding for _, embedding, _ in items],
                    metadatas=[
                        {"collection_id": collection_id}
                        for _, _, collection_id in items
                    ],
                )

    @overload
    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None: ...

    @overload
    async def rebuild_all(self, items: list[tuple[int, list[float], int]]) -> None: ...

    async def rebuild_all(
        self,
        items: list[tuple[int, list[float]]] | list[tuple[int, list[float], int]],
    ) -> None:
        """全量重建 collection 并写入合集 metadata。

        三元组是正式合集感知契约。为等待 Task 6 更新现有同步路径，过渡期仍
        接受二元组，并按根目录 `collection_id=0` 规范化后写入。

        Args:
            items: `(entry_id, embedding, collection_id)` 列表，或过渡兼容的
                `(entry_id, embedding)` 列表；为空时仅重建空 collection。

        Raises:
            ValueError: 元组形式混用，或任一 ID、embedding、collection_id
                不合法。全部校验在清空前完成。
        """
        arities = {len(item) for item in items}
        if not arities.issubset({2, 3}):
            raise ValueError("rebuild_all 每条记录必须是二元组或三元组")
        if len(arities) > 1:
            raise ValueError("rebuild_all 不能混合二元组和三元组")

        prepared: list[tuple[int, list[float], int]] = []
        seen_ids: set[int] = set()
        embedding_dimension: int | None = None
        for item in items:
            if len(item) == 2:
                entry_id, embedding = item
                collection_id = 0
            else:
                entry_id, embedding, collection_id = item
            validated_entry_id = _validate_entry_id(entry_id)
            if validated_entry_id in seen_ids:
                raise ValueError(f"rebuild_all entry_id 重复: {validated_entry_id}")
            seen_ids.add(validated_entry_id)
            embedding_copy = _copy_input_embedding(embedding)
            if embedding_dimension is None:
                embedding_dimension = len(embedding_copy)
            elif len(embedding_copy) != embedding_dimension:
                raise ValueError("rebuild_all 中所有 embedding 维度必须一致")
            prepared.append(
                (
                    validated_entry_id,
                    embedding_copy,
                    _validate_collection_id(collection_id),
                )
            )
        await asyncio.to_thread(self._rebuild_all_sync, prepared)

    def count(self) -> int:
        """返回当前向量数。

        Returns:
            collection 中现存向量数量。
        """
        with self._lock:
            return int(self._require_collection().count())

    def _get_all_ids_sync(self) -> set[int]:
        """同步取 collection 全部 id。

        Returns:
            collection 中全部向量对应的 entry_id 集合。
        """
        with self._lock:
            result = self._require_collection().get(include=[])
        ids = result.get("ids") if isinstance(result, dict) else None
        if not isinstance(ids, list) or not all(
            isinstance(raw_id, str) for raw_id in ids
        ):
            raise RuntimeError("Chroma get 未返回合法 ids 列表")
        return {int(raw_id) for raw_id in ids}

    async def get_all_ids(self) -> set[int]:
        """返回 collection 中全部向量对应的 entry_id 集合。

        Returns:
            entry_id 集合。
        """
        return await asyncio.to_thread(self._get_all_ids_sync)

    def _get_metadatas_sync(self) -> dict[int, VectorMetadata]:
        """同步读取全部完整 metadata。

        Returns:
            entry_id 到独立 metadata 字典的映射；原无 metadata 映射为 `{}`。

        Raises:
            RuntimeError: Chroma 返回结果形状或 metadata 不合法。
        """
        with self._lock:
            result = self._require_collection().get(include=["metadatas"])
        ids, metadatas = self._parse_metadata_result(result)
        return {
            int(raw_id): _copy_stored_metadata(metadata)
            for raw_id, metadata in zip(ids, metadatas, strict=True)
        }

    async def get_metadatas(self) -> dict[int, VectorMetadata]:
        """读取全部完整 metadata，并返回独立副本。

        Returns:
            entry_id 到 metadata 的映射；原无 metadata 映射为 `{}`。

        Raises:
            RuntimeError: Chroma 返回结果形状或 metadata 不合法。
        """
        return await asyncio.to_thread(self._get_metadatas_sync)

    def _update_metadata_sync(self, entry_id: int, metadata: VectorMetadata) -> None:
        """同步完整覆盖一条已存在记录的 metadata。

        Args:
            entry_id: 待更新的索引 id。
            metadata: 已校验并复制的完整 metadata。

        Raises:
            ValueError: entry_id 不存在。
            RuntimeError: Chroma 返回结果形状无效。
        """
        with self._lock:
            collection = self._require_collection()
            existing = collection.get(ids=[str(entry_id)], include=["metadatas"])
            ids, metadatas = self._parse_metadata_result(existing)
            if not ids:
                raise ValueError(f"向量 id={entry_id} 不存在")
            if ids != [str(entry_id)]:
                raise RuntimeError("Chroma 返回了非请求 id")
            existing_metadata = _copy_stored_metadata(metadatas[0])
            update_payload: dict[str, str | int | float | bool | None] = {
                key: None for key in existing_metadata.keys() - metadata.keys()
            }
            update_payload.update(metadata)
            if not update_payload:
                return
            collection.update(ids=[str(entry_id)], metadatas=[update_payload])

    async def update_metadata(self, entry_id: int, metadata: VectorMetadata) -> None:
        """完整覆盖一条已存在记录的 metadata。

        Args:
            entry_id: 待更新的索引 id。
            metadata: 完整 metadata，不与存储层共享别名。

        Raises:
            ValueError: entry_id 不合法、不存在或 metadata 类型不合法。
            RuntimeError: Chroma 返回结果形状无效。
        """
        validated_entry_id = _validate_entry_id(entry_id)
        metadata_copy = _copy_metadata(metadata)
        await asyncio.to_thread(
            self._update_metadata_sync,
            validated_entry_id,
            metadata_copy,
        )

    def _get_collection_ids_sync(self) -> dict[int, int | None]:
        """同步读取全部记录的合集编号。

        Returns:
            entry_id 到 collection_id 的映射；缺 metadata 或键时为 None。

        Raises:
            RuntimeError: Chroma 结果形状无效，或已有 collection_id 不是
                非负的真正整数。
        """
        metadatas = self._get_metadatas_sync()
        collection_ids: dict[int, int | None] = {}
        for entry_id, metadata in metadatas.items():
            if "collection_id" not in metadata:
                collection_ids[entry_id] = None
                continue
            value = metadata["collection_id"]
            try:
                collection_ids[entry_id] = _validate_collection_id(value)
            except ValueError as exc:
                raise RuntimeError(
                    f"向量 id={entry_id} 的 collection_id metadata 损坏"
                ) from exc
        return collection_ids

    async def get_collection_ids(self) -> dict[int, int | None]:
        """读取全部记录的合集编号。

        Returns:
            entry_id 到 collection_id 的映射；缺 metadata 或键时为 None。

        Raises:
            RuntimeError: Chroma 结果或已有 collection_id metadata 损坏。
        """
        return await asyncio.to_thread(self._get_collection_ids_sync)

    def _update_collection_id_sync(self, entry_id: int, collection_id: int) -> None:
        """在同一锁内读取、合并并更新 collection_id。

        Args:
            entry_id: 待更新的索引 id。
            collection_id: 已校验的非负合集编号。

        Raises:
            ValueError: entry_id 不存在。
            RuntimeError: Chroma 返回结果形状或已有 metadata 不合法。
        """
        with self._lock:
            collection = self._require_collection()
            existing = collection.get(ids=[str(entry_id)], include=["metadatas"])
            ids, metadatas = self._parse_metadata_result(existing)
            if not ids:
                raise ValueError(f"向量 id={entry_id} 不存在")
            if ids != [str(entry_id)]:
                raise RuntimeError("Chroma 返回了非请求 id")
            metadata = _copy_stored_metadata(metadatas[0])
            metadata["collection_id"] = collection_id
            collection.update(ids=[str(entry_id)], metadatas=[metadata])

    async def update_collection_id(self, entry_id: int, collection_id: int) -> None:
        """仅替换一条记录的 collection_id，保留其他 metadata。

        读取、合并和完整覆盖 update 在同一个 store lock 内完成，避免两个
        独立全库读取造成 TOCTOU。

        Args:
            entry_id: 待更新的索引 id。
            collection_id: 新合集编号，必须是大于等于 0 的真正整数。

        Raises:
            ValueError: entry_id 不合法、不存在或 collection_id 不合法。
            RuntimeError: Chroma 返回结果形状或已有 metadata 不合法。
        """
        validated_entry_id = _validate_entry_id(entry_id)
        validated_collection_id = _validate_collection_id(collection_id)
        await asyncio.to_thread(
            self._update_collection_id_sync,
            validated_entry_id,
            validated_collection_id,
        )

    def _snapshot_records_sync(self, entry_ids: list[int]) -> list[VectorRecord]:
        """同步读取指定 ID 的完整快照。

        Args:
            entry_ids: 已去重且保持请求顺序的正整数 ID。

        Returns:
            按请求顺序排列的完整记录副本。

        Raises:
            ValueError: 任一请求 ID 不存在。
            RuntimeError: Chroma 返回结果形状、embedding 或 metadata 不合法。
        """
        if not entry_ids:
            return []
        requested_ids = [str(entry_id) for entry_id in entry_ids]
        with self._lock:
            result = self._require_collection().get(
                ids=requested_ids,
                include=["embeddings", "metadatas"],
            )
            ids, metadatas = self._parse_metadata_result(result)
            embeddings = result.get("embeddings")
            if not isinstance(embeddings, Iterable) or isinstance(
                embeddings, (str, bytes, dict)
            ):
                raise RuntimeError("Chroma 未返回合法 embeddings 序列")
            embeddings_list = list(embeddings)
            if len(ids) != len(embeddings_list):
                raise RuntimeError("Chroma 返回的 ids 与 embeddings 数量不一致")
            records_by_id: dict[int, VectorRecord] = {}
            for raw_id, embedding, metadata in zip(
                ids, embeddings_list, metadatas, strict=True
            ):
                entry_id = int(raw_id)
                if entry_id in records_by_id:
                    raise RuntimeError(f"Chroma 返回了重复 id={entry_id}")
                records_by_id[entry_id] = VectorRecord(
                    entry_id=entry_id,
                    embedding=_copy_chroma_embedding(embedding),
                    metadata=_copy_stored_metadata(metadata),
                )
        missing_ids = [
            entry_id for entry_id in entry_ids if entry_id not in records_by_id
        ]
        if missing_ids:
            raise ValueError(f"向量 id 不存在: {missing_ids}")
        unexpected_ids = set(records_by_id).difference(entry_ids)
        if unexpected_ids:
            raise RuntimeError(f"Chroma 返回了非请求 id: {sorted(unexpected_ids)}")
        return [records_by_id[entry_id] for entry_id in entry_ids]

    async def snapshot_records(self, entry_ids: list[int]) -> list[VectorRecord]:
        """读取指定 ID 的完整快照。

        重复 ID 按首次出现去重，返回顺序与去重后的请求顺序一致。任一 ID
        不存在时整体抛错，不返回部分快照。

        Args:
            entry_ids: 待快照的正整数 ID 列表。

        Returns:
            完整记录的独立副本列表。

        Raises:
            ValueError: ID 不是正整数，或任一请求 ID 不存在。
            RuntimeError: Chroma 返回结果形状或内容不合法。
        """
        unique_ids: list[int] = []
        seen_ids: set[int] = set()
        for entry_id in entry_ids:
            if type(entry_id) is not int or entry_id <= 0:
                raise ValueError("entry_ids 必须只包含正整数")
            if entry_id not in seen_ids:
                seen_ids.add(entry_id)
                unique_ids.append(entry_id)
        return await asyncio.to_thread(self._snapshot_records_sync, unique_ids)

    def _restore_records_sync(self, records: list[VectorRecord]) -> None:
        """同步删除并重新添加完整记录。

        Args:
            records: 已完整校验且与调用方输入不共享别名的记录。

        Raises:
            RuntimeError: Chroma 删除或任一分组添加失败；此时可能已部分恢复。
        """
        if not records:
            return
        ids = [str(record.entry_id) for record in records]
        with_metadata = [record for record in records if record.metadata]
        without_metadata = [record for record in records if not record.metadata]
        with self._lock:
            collection = self._require_collection()
            try:
                collection.delete(ids=ids)
                if with_metadata:
                    collection.add(
                        ids=[str(record.entry_id) for record in with_metadata],
                        embeddings=[list(record.embedding) for record in with_metadata],
                        metadatas=[dict(record.metadata) for record in with_metadata],
                    )
                if without_metadata:
                    collection.add(
                        ids=[str(record.entry_id) for record in without_metadata],
                        embeddings=[
                            list(record.embedding) for record in without_metadata
                        ],
                    )
            except Exception as exc:
                raise RuntimeError(
                    f"恢复向量记录失败，可能已部分写入: ids={ids}"
                ) from exc

    async def restore_records(self, records: list[VectorRecord]) -> None:
        """完整恢复记录，空 metadata 记录按无 metadata 重新添加。

        所有记录会先完成严格校验和深复制，再在同一 store lock 内删除目标
        IDs，并按 metadata 是否为空分组 add。空列表为 no-op。

        Args:
            records: 待恢复的完整记录。

        Raises:
            ValueError: 任一记录的 ID、embedding、metadata 不合法或 ID 重复。
            RuntimeError: Chroma 恢复失败，目标记录可能已部分恢复。
        """
        prepared = _prepare_restore_records(records)
        await asyncio.to_thread(self._restore_records_sync, prepared)
