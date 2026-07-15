"""AI 语义匹配模块。

先用 embedding 做语义召回（ChromaDB），再可选调用精排 provider 选出最终表情包。
"""

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from bot.log_context import timed

from .metadata_store import MemeEntry, MetadataStore
from .protocols import EmbeddingProvider
from .types import GLOBAL_COLLECTION_NAME, MemePublicId
from .utils import vector_norm
from .vector_store import VectorHit, VectorStore

if TYPE_CHECKING:
    from .rerank_service import RerankService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AIMatchCandidate:
    """Embedding 阶段的候选表情包。

    Attributes:
        rank: 临时候选序号，1-based。
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本。
        similarity: 余弦相似度。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
        collection_id: 所属合集编号，0 表示全局根目录。
        local_id: 合集内正整数编号。
        collection_name: 所属合集名称。
    """

    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
    collection_id: int = 0
    local_id: int = 1
    collection_name: str = GLOBAL_COLLECTION_NAME

    @property
    def public_id(self) -> MemePublicId:
        """返回用户可见的复合 ID。

        Returns:
            当前候选所属合集编号和合集内编号。
        """
        return MemePublicId(self.collection_id, self.local_id)


@dataclass(frozen=True, slots=True)
class AIMatchResult:
    """AI 匹配最终结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本。
        similarity: embedding 余弦相似度。
        source: 结果来源，取值为 "embedding" 或 "rerank"。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
        collection_id: 所属合集编号，0 表示全局根目录。
        local_id: 合集内正整数编号。
        collection_name: 所属合集名称。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
    collection_id: int = 0
    local_id: int = 1
    collection_name: str = GLOBAL_COLLECTION_NAME

    @property
    def public_id(self) -> MemePublicId:
        """返回用户可见的复合 ID。

        Returns:
            当前结果所属合集编号和合集内编号。
        """
        return MemePublicId(self.collection_id, self.local_id)


class AIMatcher:
    """AI 表情包匹配器。

    先对用户描述生成 embedding，再用 VectorStore.query 从 ChromaDB 召回 Top N，
    从 MetadataStore 取 metadata 构候选。可选 reranker 精排。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        rerank_provider: "RerankService | None" = None,
        limit: int = 10,
    ) -> None:
        """初始化 AI 匹配器。

        Args:
            metadata_store: 元数据提供者，按 id 取 MemeEntry。
            vector_store: 向量提供者，query 召回 Top-N。
            embedding_provider: 文本向量化服务提供者。
            rerank_provider: 可选的精排服务提供者。
            limit: 候选召回上限。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._rerank_provider = rerank_provider
        self._limit = limit

    @timed(logger, "AI 语义匹配")
    async def match_with_vector(
        self,
        description: str,
        query_vector: list[float],
        *,
        collection_id: int | None = None,
    ) -> AIMatchResult | None:
        """根据已生成的 embedding 向量匹配表情包。

        Args:
            description: 用户输入的自然语言描述（已 strip）。
            query_vector: 用户描述对应的 embedding 向量。
            collection_id: 只召回该合集的向量；None 表示全库召回。

        Returns:
            匹配结果；空描述、零向量、向量库为空或无有效候选时返回 None。

        Raises:
            ValueError: query_vector 为零向量。
        """
        description = description.strip()
        if not description:
            logger.debug("AI 匹配描述为空，返回空结果")
            return None

        logger.info(
            "AI 匹配描述: %r, top_k=%d, collection_id=%s",
            description,
            self._limit,
            collection_id,
        )

        if vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")

        if self._vector_store.count() == 0:
            logger.debug("向量库为空，返回空结果")
            return None

        hits = await self._vector_store.query(
            query_vector,
            n_results=self._limit,
            collection_id=collection_id,
        )
        candidates = self._build_candidates(hits)
        logger.info("向量召回 %d 个候选", len(candidates))
        if not candidates:
            logger.info("AI 召回无候选：description=%r", description)
            return None

        if self._rerank_provider is None:
            return _candidate_to_result(candidates[0], source="embedding")

        rank = await self._rerank(description, candidates)
        if rank is None:
            return _candidate_to_result(candidates[0], source="embedding")

        reranked = _candidate_to_result(candidates[rank - 1], source="rerank")
        logger.info("rerank 后返回 %d 个结果", 1 if reranked else 0)
        return reranked

    async def _rerank(
        self, description: str, candidates: list[AIMatchCandidate]
    ) -> int | None:
        """调用 reranker 精排，失败或返回不可用时返回 None。"""
        try:
            rank = await self._rerank_provider.rerank(description, candidates)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        except Exception:
            logger.warning("AI 精排调用失败，回退 embedding Top 1", exc_info=True)
            return None

        if type(rank) is not int:
            logger.warning("AI 精排返回非整数：rank=%r，回退 embedding Top 1", rank)
            return None

        if rank == 0:
            logger.info("AI 精排返回 0，回退 embedding Top 1")
            return None

        if rank < 1 or rank > len(candidates):
            logger.warning(
                "AI 精排返回越界序号：rank=%s, candidates=%d，回退 embedding Top 1",
                rank,
                len(candidates),
            )
            return None

        return rank

    def _build_candidates(self, hits: list[VectorHit]) -> list[AIMatchCandidate]:
        """将 VectorHit 转为候选，跳过 metadata 缺失的 hit。

        Args:
            hits: VectorStore.query 返回的召回结果（已按相似度降序）。

        Returns:
            带 rank 的候选列表（1-based，顺序与 hits 一致）。
        """
        candidates: list[AIMatchCandidate] = []
        for hit in hits:
            entry: MemeEntry | None = self._metadata_store.get_entry(hit.entry_id)
            if entry is None:
                logger.warning(
                    "召回 hit 的 metadata 缺失，跳过：entry_id=%s", hit.entry_id
                )
                continue
            candidates.append(
                AIMatchCandidate(
                    rank=0,
                    entry_id=entry.id,
                    image_path=entry.image_path,
                    text=entry.text,
                    similarity=hit.similarity,
                    speaker=entry.speaker,
                    tags=entry.tags,
                    collection_id=entry.collection_id,
                    local_id=entry.local_id,
                    collection_name=entry.collection_name,
                )
            )
        return [
            replace(candidate, rank=rank)
            for rank, candidate in enumerate(candidates, start=1)
        ]


def _candidate_to_result(candidate: AIMatchCandidate, source: str) -> AIMatchResult:
    """将候选转换为最终结果。"""
    return AIMatchResult(
        entry_id=candidate.entry_id,
        image_path=candidate.image_path,
        text=candidate.text,
        similarity=candidate.similarity,
        source=source,
        speaker=candidate.speaker,
        tags=candidate.tags,
        collection_id=candidate.collection_id,
        local_id=candidate.local_id,
        collection_name=candidate.collection_name,
    )
