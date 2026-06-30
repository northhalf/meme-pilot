"""AIMatcher 单元测试。"""

from typing import Any

import pytest

from bot.engine.ai_matcher import AIMatchCandidate, AIMatchResult, AIMatcher
from bot.engine.metadata_store import MemeEntry
from bot.engine.vector_store import VectorHit


class MockVectorStore:
    """模拟 VectorStore。"""

    def __init__(
        self,
        hits: list[VectorHit] | None = None,
        count: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._hits = hits or []
        self._count = count
        self._error = error

    async def query(self, query_embedding: list[float], n_results: int = 10) -> list[VectorHit]:
        if self._error is not None:
            raise self._error
        return self._hits[:n_results]

    def count(self) -> int:
        return self._count


class MockMetadataStore:
    """模拟 MetadataStore，按 id 返回 MemeEntry。"""

    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)


class MockEmbeddingProvider:
    def __init__(
        self,
        embedding: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._embedding = embedding or [0.1, 0.2, 0.3]
        self._error = error
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return self._embedding


class MockReranker:
    def __init__(self, result: Any = 0, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[tuple[str, list[AIMatchCandidate]]] = []

    async def rerank(
        self, description: str, candidates: list[AIMatchCandidate]
    ) -> int:
        self.calls.append((description, candidates))
        if self._exc is not None:
            raise self._exc
        return self._result


def _make_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="cat.jpg", text="猫猫开心"),
        2: MemeEntry(id=2, image_path="work.jpg", text="加班心累"),
    }


def test_candidate_create() -> None:
    c = AIMatchCandidate(
        rank=1, entry_id=1, image_path="cat.jpg", text="一只猫", similarity=0.95
    )
    assert c.entry_id == 1
    assert c.image_path == "cat.jpg"


def test_result_create() -> None:
    r = AIMatchResult(
        entry_id=1, image_path="cat.jpg", text="一只猫", similarity=0.95, source="embedding"
    )
    assert r.entry_id == 1
    assert r.image_path == "cat.jpg"
    assert r.source == "embedding"


@pytest.mark.anyio
async def test_empty_description_returns_none_without_embedding_call() -> None:
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(
        MockMetadataStore(), MockVectorStore(count=0), provider, MockReranker()
    )
    result = await matcher.match("   ")
    assert result is None
    assert provider.calls == []


@pytest.mark.anyio
async def test_empty_vector_store_returns_none() -> None:
    """VectorStore.count()==0 时返回 None，不调用 embed。"""
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(
        MockMetadataStore(_make_entries()), MockVectorStore(count=0), provider
    )
    result = await matcher.match("找猫")
    assert result is None
    assert provider.calls == []


class TestEmbeddingRecall:
    @pytest.mark.anyio
    async def test_returns_top_hit(self) -> None:
        hits = [VectorHit(entry_id=2, similarity=0.9), VectorHit(entry_id=1, similarity=0.8)]
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(hits=hits, count=2),
            MockEmbeddingProvider(),
        )
        result = await matcher.match("心累加班")
        assert result == AIMatchResult(
            entry_id=2, image_path="work.jpg", text="加班心累",
            similarity=0.9, source="embedding",
        )

    @pytest.mark.anyio
    async def test_skip_hit_with_missing_metadata(self) -> None:
        """VectorHit 对应的 metadata 不存在时跳过该候选。"""
        hits = [VectorHit(entry_id=999, similarity=0.9), VectorHit(entry_id=1, similarity=0.8)]
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(hits=hits, count=2),
            MockEmbeddingProvider(),
        )
        result = await matcher.match("找猫")
        assert result is not None
        assert result.entry_id == 1

    @pytest.mark.anyio
    async def test_all_hits_missing_metadata_returns_none(self) -> None:
        hits = [VectorHit(entry_id=999, similarity=0.9)]
        matcher = AIMatcher(
            MockMetadataStore({}),
            MockVectorStore(hits=hits, count=1),
            MockEmbeddingProvider(),
        )
        result = await matcher.match("找猫")
        assert result is None

    @pytest.mark.anyio
    async def test_limit_passed_to_query(self) -> None:
        class CountingVectorStore(MockVectorStore):
            def __init__(self) -> None:
                super().__init__(hits=[VectorHit(1, 0.9)], count=1)
                self.last_n: int = 0

            async def query(self, query_embedding, n_results=10):
                self.last_n = n_results
                return await super().query(query_embedding, n_results)

        vs = CountingVectorStore()
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()), vs, MockEmbeddingProvider(), limit=5
        )
        await matcher.match("找猫")
        assert vs.last_n == 5


class TestEmbeddingProviderErrors:
    @pytest.mark.anyio
    async def test_provider_error_bubbles_up(self) -> None:
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(count=2),
            MockEmbeddingProvider(error=RuntimeError("embedding down")),
        )
        with pytest.raises(RuntimeError, match="embedding down"):
            await matcher.match("心累加班")

    @pytest.mark.anyio
    async def test_empty_query_vector_raises_value_error(self) -> None:
        provider = MockEmbeddingProvider()
        provider._embedding = []
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()), MockVectorStore(count=2), provider
        )
        with pytest.raises(ValueError, match="非空列表"):
            await matcher.match("心累加班")

    @pytest.mark.anyio
    async def test_zero_query_vector_raises_value_error(self) -> None:
        matcher = AIMatcher(
            MockMetadataStore(_make_entries()),
            MockVectorStore(count=2),
            MockEmbeddingProvider([0.0, 0.0]),
        )
        with pytest.raises(ValueError, match="零向量"):
            await matcher.match("心累加班")


class TestRerank:
    def _matcher(self, reranker: MockReranker, limit: int = 10) -> AIMatcher:
        hits = [
            VectorHit(entry_id=1, similarity=0.9),
            VectorHit(entry_id=2, similarity=0.8),
            VectorHit(entry_id=3, similarity=0.7),
        ]
        entries = {
            1: MemeEntry(id=1, image_path="first.jpg", text="第一张"),
            2: MemeEntry(id=2, image_path="second.jpg", text="第二张"),
            3: MemeEntry(id=3, image_path="third.jpg", text="第三张"),
        }
        return AIMatcher(
            MockMetadataStore(entries),
            MockVectorStore(hits=hits, count=3),
            MockEmbeddingProvider(),
            rerank_provider=reranker,
            limit=limit,
        )

    @pytest.mark.anyio
    async def test_valid_rank_selects_candidate(self) -> None:
        reranker = MockReranker(result=2)
        result = await self._matcher(reranker).match("选第二张")
        assert result is not None
        assert result.entry_id == 2
        assert result.source == "rerank"
        assert [c.rank for c in reranker.calls[0][1]] == [1, 2, 3]

    @pytest.mark.anyio
    async def test_limit_controls_candidates(self) -> None:
        reranker = MockReranker(result=1)
        m = self._matcher(reranker, limit=2)
        await m.match("只看两个")
        assert len(reranker.calls[0][1]) == 2

    @pytest.mark.anyio
    async def test_zero_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(result=0)).match("放弃精排")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_out_of_range_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(result=99)).match("越界")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_non_integer_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(result="2")).match("非整数")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_exception_fallbacks_top1(self) -> None:
        result = await self._matcher(MockReranker(exc=RuntimeError("down"))).match("失败")
        assert result is not None
        assert result.entry_id == 1
        assert result.source == "embedding"
