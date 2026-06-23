"""AI 语义匹配模块。

先用 embedding 做语义召回，再可选调用精排 provider 选出最终表情包。
"""

import logging
import math
from dataclasses import dataclass, replace
from typing import Protocol

from bot.engine.protocols import EmbeddingProvider

logger = logging.getLogger(__name__)


class AIIndexProvider(Protocol):
    """AI 匹配所需的索引数据提供者协议。"""

    def get_entries(self) -> dict[str, dict[str, str]]:
        """返回全部索引条目。

        Returns:
            key 为索引 id，value 为包含 filename、text、text_hash 的字典。
        """
        ...

    def get_embeddings(self) -> dict[str, dict[str, object]]:
        """返回全部 embedding 条目。

        Returns:
            key 为索引 id，value 为包含 text_hash、embedding 的字典。
        """
        ...


@dataclass(frozen=True)
class AIMatchCandidate:
    """Embedding 阶段的候选表情包。

    Attributes:
        rank: 临时候选序号，1-based。
        entry_id: 索引 id。
        filename: 表情包文件名。
        text: OCR 文本。
        similarity: 余弦相似度。
    """

    rank: int
    entry_id: str
    filename: str
    text: str
    similarity: float


class RerankProvider(Protocol):
    """候选精排服务协议。"""

    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int:
        """从候选中选出最匹配的临时序号。

        Args:
            description: 用户自然语言描述。
            candidates: embedding 阶段 Top N 候选。

        Returns:
            1-based 临时候选序号；返回 0 表示放弃精排。
        """
        ...


@dataclass(frozen=True)
class AIMatchResult:
    """AI 匹配最终结果。

    Attributes:
        entry_id: 索引 id。
        filename: 表情包文件名。
        text: OCR 文本。
        similarity: embedding 余弦相似度。
        source: 结果来源，取值为 "embedding" 或 "rerank"。
    """

    entry_id: str
    filename: str
    text: str
    similarity: float
    source: str


class AIMatcher:
    """AI 表情包匹配器。

    先对用户描述生成 embedding，再与本地 embeddings.json 中的向量计算
    余弦相似度。可选 reranker 用于从 Top N 候选中精排出最终结果。
    """

    def __init__(
        self,
        index_provider: AIIndexProvider,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None:
        """初始化 AI 匹配器。

        Args:
            index_provider: 提供索引条目与 embedding 的对象。
            embedding_provider: 文本向量化服务提供者。
            rerank_provider: 可选的精排服务提供者。
            limit: 候选召回上限。
        """
        self._index_provider = index_provider
        self._embedding_provider = embedding_provider
        self._rerank_provider = rerank_provider
        self._limit = limit

    async def match(self, description: str) -> AIMatchResult | None:
        """根据自然语言描述匹配一个表情包。

        Args:
            description: 用户输入的自然语言描述。

        Returns:
            匹配结果；空描述、索引为空或无有效候选时返回 None。

        Raises:
            ValueError: 用户描述 embedding 为空、非数字或为零向量。
        """
        description = description.strip()
        if not description:
            logger.debug("AI 匹配描述为空，返回空结果")
            return None

        entries = self._index_provider.get_entries()
        embeddings = self._index_provider.get_embeddings()
        if not entries or not embeddings:
            logger.debug(
                "索引文件条目 entries 或向量库 embedding 无法从 index_provider 获取"
            )
            return None

        query_vector = _coerce_vector(
            await self._embedding_provider.embed(description),
            context="用户描述 embedding",
        )
        if _vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")

        candidates = self._build_candidates(entries, embeddings, query_vector)
        if not candidates:
            logger.info("AI embedding 召回无候选：description=%r", description)
            return None

        if self._rerank_provider is None:
            return _candidate_to_result(candidates[0], source="embedding")

        rank = await self._rerank(description, candidates)
        if rank is None:
            return _candidate_to_result(candidates[0], source="embedding")

        return _candidate_to_result(candidates[rank - 1], source="rerank")

    async def _rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int | None:
        """调用 reranker 精排，失败或返回不可用时返回 None。

        Args:
            description: 用户自然语言描述。
            candidates: embedding 阶段 Top N 候选。

        Returns:
            有效的 1-based 候选序号；reranker 失败、返回 0、非整数或越界
            时返回 None，由调用方回退到 embedding Top 1。
        """
        try:
            rank = await self._rerank_provider.rerank(description, candidates)  # type: ignore[union-attr]
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

    def _build_candidates(
        self,
        entries: dict[str, dict[str, str]],
        embeddings: dict[str, dict[str, object]],
        query_vector: list[float],
    ) -> list[AIMatchCandidate]:
        """构建 embedding Top N 候选。

        Args:
            entries: 索引条目字典。
            embeddings: 向量索引字典。
            query_vector: 用户描述向量。

        Returns:
            按相似度降序、entry_id 升序稳定排序后的 Top N 候选列表。
        """
        candidates: list[AIMatchCandidate] = []

        for entry_id, entry in entries.items():
            text = entry.get("text", "").strip()
            if not text:
                continue

            embedding_record = embeddings.get(entry_id)
            if not isinstance(embedding_record, dict):
                continue

            try:
                entry_vector = _coerce_vector(
                    embedding_record.get("embedding"),
                    context=f"索引 {entry_id} embedding",
                )
            except ValueError as exc:
                logger.warning(
                    "跳过异常 embedding：entry_id=%s, reason=%s", entry_id, exc
                )
                continue

            if len(entry_vector) != len(query_vector):
                logger.warning(
                    "跳过维度不一致的 embedding：entry_id=%s, expected=%d, actual=%d",
                    entry_id,
                    len(query_vector),
                    len(entry_vector),
                )
                continue

            similarity = _cosine_similarity(query_vector, entry_vector)
            if similarity is None:
                logger.warning("跳过零向量 embedding：entry_id=%s", entry_id)
                continue

            candidates.append(
                AIMatchCandidate(
                    rank=0,
                    entry_id=entry_id,
                    filename=entry.get("filename", ""),
                    text=text,
                    similarity=similarity,
                )
            )

        candidates.sort(key=lambda c: (-c.similarity, _entry_id_sort_key(c.entry_id)))
        return [
            replace(candidate, rank=rank)
            for rank, candidate in enumerate(candidates[: self._limit], start=1)
        ]


def _candidate_to_result(candidate: AIMatchCandidate, source: str) -> AIMatchResult:
    """将候选转换为最终结果。

    Args:
        candidate: 候选条目。
        source: 结果来源标记。

    Returns:
        最终匹配结果。
    """
    return AIMatchResult(
        entry_id=candidate.entry_id,
        filename=candidate.filename,
        text=candidate.text,
        similarity=candidate.similarity,
        source=source,
    )


def _coerce_vector(vector: object, *, context: str) -> list[float]:
    """将向量转换为浮点数列表。

    Args:
        vector: 原始向量数据。
        context: 出错时用于日志的上下文描述。

    Returns:
        浮点数列表。

    Raises:
        ValueError: 向量不是非空列表或包含非有限数字元素。
    """
    if not isinstance(vector, list) or not vector:
        raise ValueError(f"{context} 不是非空列表")

    values: list[float] = []
    for value in vector:
        if isinstance(value, bool):
            raise ValueError(f"{context} 包含非数字元素")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context} 包含非数字元素") from exc
        if not math.isfinite(number):
            raise ValueError(f"{context} 包含非有限数字")
        values.append(number)

    return values


def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
    """计算两个等长向量的余弦相似度。

    Args:
        left: 左侧向量。
        right: 右侧向量（与 left 等长）。

    Returns:
        余弦相似度；任一向量为零向量时返回 None。
    """
    left_norm = _vector_norm(left)
    right_norm = _vector_norm(right)
    if left_norm == 0 or right_norm == 0:
        return None

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


def _vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。

    Args:
        vector: 浮点数向量。

    Returns:
        向量的欧几里得范数。
    """
    return math.sqrt(sum(value * value for value in vector))


def _entry_id_sort_key(entry_id: str) -> tuple[int, int, str]:
    """生成稳定的 entry_id 排序键。

    可转为整数的 entry_id 优先按数值升序；不可转整数的按字符串排序，
    且排在所有数字 id 之后，保证两类 id 互不干扰且各自稳定。

    Args:
        entry_id: 索引 id 字符串。

    Returns:
        用于排序的元组键。
    """
    try:
        return (0, int(entry_id), "")
    except ValueError:
        return (1, 0, entry_id)
