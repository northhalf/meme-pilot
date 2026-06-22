"""AIMatcher 单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from bot.engine.ai_matcher import AIMatchCandidate, AIMatchResult, AIMatcher


class MockIndex:
    """模拟 AIIndexProvider。"""

    def __init__(
        self,
        entries: dict[str, dict[str, str]] | None = None,
        embeddings: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self._entries = entries or {}
        self._embeddings = embeddings or {}

    def get_entries(self) -> dict[str, dict[str, str]]:
        return self._entries

    def get_embeddings(self) -> dict[str, dict[str, object]]:
        return self._embeddings


class MockEmbeddingProvider:
    """模拟 EmbeddingProvider，并记录调用。"""

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
    """模拟 RerankProvider，并记录调用。

    result 可传入非整数（如 "2"、99）以测试 fallback 分支；
    exc 不为 None 时抛出该异常以测试精排失败回退。
    """

    def __init__(
        self,
        result: Any = 0,
        exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[tuple[str, list[AIMatchCandidate]]] = []

    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int:
        self.calls.append((description, candidates))
        if self._exc is not None:
            raise self._exc
        return self._result


def test_ai_match_candidate_create() -> None:
    """验证 AIMatchCandidate 可正确创建。"""
    candidate = AIMatchCandidate(
        rank=1,
        entry_id="1",
        filename="cat.jpg",
        text="一只猫",
        similarity=0.95,
    )

    assert candidate.rank == 1
    assert candidate.entry_id == "1"
    assert candidate.filename == "cat.jpg"
    assert candidate.text == "一只猫"
    assert candidate.similarity == 0.95


def test_ai_match_result_create() -> None:
    """验证 AIMatchResult 可正确创建。"""
    result = AIMatchResult(
        entry_id="1",
        filename="cat.jpg",
        text="一只猫",
        similarity=0.95,
        source="vector",
    )

    assert result.entry_id == "1"
    assert result.filename == "cat.jpg"
    assert result.text == "一只猫"
    assert result.similarity == 0.95
    assert result.source == "vector"


@pytest.mark.anyio
async def test_match_empty_description_returns_none_without_embedding_call() -> None:
    """空描述应直接返回 None，且不调用 embedding。"""
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(MockIndex(), provider, MockReranker())

    result = await matcher.match("   ")

    assert result is None
    assert provider.calls == []


@pytest.mark.anyio
async def test_match_returns_none_when_no_entries() -> None:
    """索引 entries 为空时应返回 None。"""
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(
        MockIndex(
            entries={},
            embeddings={"1": {"text_hash": "sha256:abc", "embedding": [0.1, 0.2]}},
        ),
        provider,
        MockReranker(),
    )

    result = await matcher.match("找一只猫")

    assert result is None
    assert provider.calls == []


@pytest.mark.anyio
async def test_match_returns_none_when_no_embeddings() -> None:
    """索引 embeddings 为空时应返回 None。"""
    provider = MockEmbeddingProvider()
    matcher = AIMatcher(
        MockIndex(
            entries={
                "1": {
                    "filename": "cat.jpg",
                    "text": "一只猫",
                    "text_hash": "sha256:abc",
                }
            },
            embeddings={},
        ),
        provider,
        MockReranker(),
    )

    result = await matcher.match("找一只猫")

    assert result is None
    assert provider.calls == []


class TestAIMatcherEmbeddingRecall:
    """Embedding 语义召回测试。"""

    @pytest.mark.anyio
    async def test_returns_highest_cosine_similarity(self) -> None:
        """返回余弦相似度最高的候选。"""
        entries = {
            "1": {"filename": "cat.jpg", "text": "猫猫开心", "text_hash": "x"},
            "2": {"filename": "work.jpg", "text": "加班心累", "text_hash": "y"},
        }
        embeddings = {
            "1": {"text_hash": "x", "embedding": [0.0, 1.0]},
            "2": {"text_hash": "y", "embedding": [1.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("心累加班")

        assert result == AIMatchResult(
            entry_id="2",
            filename="work.jpg",
            text="加班心累",
            similarity=1.0,
            source="embedding",
        )

    @pytest.mark.anyio
    async def test_tie_breaks_by_numeric_entry_id(self) -> None:
        """相似度相同时按数字 id 升序返回。"""
        entries = {
            "10": {"filename": "b.jpg", "text": "同分 B", "text_hash": "b"},
            "2": {"filename": "a.jpg", "text": "同分 A", "text_hash": "a"},
        }
        embeddings = {
            "10": {"text_hash": "b", "embedding": [1.0, 0.0]},
            "2": {"text_hash": "a", "embedding": [1.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("同分")

        assert result is not None
        assert result.entry_id == "2"

    @pytest.mark.anyio
    async def test_skips_missing_embedding(self) -> None:
        """缺少 embedding 的条目会被跳过。"""
        entries = {
            "1": {"filename": "missing.jpg", "text": "缺向量", "text_hash": "x"},
            "2": {"filename": "ok.jpg", "text": "有向量", "text_hash": "y"},
        }
        embeddings = {
            "2": {"text_hash": "y", "embedding": [1.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("有向量")

        assert result is not None
        assert result.entry_id == "2"

    @pytest.mark.anyio
    async def test_skips_bad_index_vectors(self) -> None:
        """坏索引向量被跳过，不影响好候选。"""
        entries = {
            "1": {"filename": "bad.jpg", "text": "坏向量", "text_hash": "x"},
            "2": {"filename": "ok.jpg", "text": "好向量", "text_hash": "y"},
        }
        embeddings = {
            "1": {"text_hash": "x", "embedding": ["bad"]},
            "2": {"text_hash": "y", "embedding": [1.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("好向量")

        assert result is not None
        assert result.entry_id == "2"

    @pytest.mark.anyio
    async def test_skips_dimension_mismatch(self) -> None:
        """维度不一致的索引向量被跳过。"""
        entries = {
            "1": {"filename": "bad.jpg", "text": "维度错", "text_hash": "x"},
            "2": {"filename": "ok.jpg", "text": "维度对", "text_hash": "y"},
        }
        embeddings = {
            "1": {"text_hash": "x", "embedding": [1.0, 0.0, 0.0]},
            "2": {"text_hash": "y", "embedding": [1.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("维度对")

        assert result is not None
        assert result.entry_id == "2"

    @pytest.mark.anyio
    async def test_skips_zero_index_vector(self) -> None:
        """零向量索引条目被跳过。"""
        entries = {
            "1": {"filename": "zero.jpg", "text": "零向量", "text_hash": "x"},
            "2": {"filename": "ok.jpg", "text": "好向量", "text_hash": "y"},
        }
        embeddings = {
            "1": {"text_hash": "x", "embedding": [0.0, 0.0]},
            "2": {"text_hash": "y", "embedding": [1.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("好向量")

        assert result is not None
        assert result.entry_id == "2"

    @pytest.mark.anyio
    async def test_all_invalid_candidates_returns_none(self) -> None:
        """所有候选都无效时返回 None。"""
        entries = {
            "1": {"filename": "zero.jpg", "text": "零向量", "text_hash": "x"},
        }
        embeddings = {
            "1": {"text_hash": "x", "embedding": [0.0, 0.0]},
        }
        matcher = AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
        )

        result = await matcher.match("零向量")

        assert result is None


class TestAIMatcherEmbeddingProviderErrors:
    """用户描述 embedding 异常测试。

    这些用例使用非空索引，确保 match() 越过空索引短路、真正调用 embed，
    从而验证 embedding 阶段的错误处理。
    """

    def _index(self) -> MockIndex:
        """返回非空索引，供 embedding 异常用例触发 embed 调用。"""
        return MockIndex(
            entries={
                "1": {"filename": "cat.jpg", "text": "一只猫", "text_hash": "x"},
            },
            embeddings={
                "1": {"text_hash": "x", "embedding": [1.0, 0.0]},
            },
        )

    @pytest.mark.anyio
    async def test_embedding_provider_error_bubbles_up(self) -> None:
        """用户描述 embedding 生成失败时向外抛。"""
        matcher = AIMatcher(
            self._index(),
            MockEmbeddingProvider(error=RuntimeError("embedding down")),
        )

        with pytest.raises(RuntimeError, match="embedding down"):
            await matcher.match("心累加班")

    @pytest.mark.anyio
    async def test_empty_query_vector_raises_value_error(self) -> None:
        """用户描述 embedding 为空列表时抛出 ValueError。"""
        provider = MockEmbeddingProvider()
        provider._embedding = []
        matcher = AIMatcher(self._index(), provider)

        with pytest.raises(ValueError, match="非空列表"):
            await matcher.match("心累加班")

    @pytest.mark.anyio
    async def test_zero_query_vector_raises_value_error(self) -> None:
        """用户描述 embedding 为零向量时抛出 ValueError。"""
        matcher = AIMatcher(
            self._index(),
            MockEmbeddingProvider([0.0, 0.0]),
        )

        with pytest.raises(ValueError, match="零向量"):
            await matcher.match("心累加班")


class TestAIMatcherRerank:
    """候选精排测试。"""

    def _matcher(self, reranker: MockReranker, limit: int = 10) -> AIMatcher:
        entries = {
            "1": {"filename": "first.jpg", "text": "第一张", "text_hash": "x"},
            "2": {"filename": "second.jpg", "text": "第二张", "text_hash": "y"},
            "3": {"filename": "third.jpg", "text": "第三张", "text_hash": "z"},
        }
        embeddings = {
            "1": {"text_hash": "x", "embedding": [1.0, 0.0]},
            "2": {"text_hash": "y", "embedding": [0.8, 0.2]},
            "3": {"text_hash": "z", "embedding": [0.0, 1.0]},
        }
        return AIMatcher(
            MockIndex(entries=entries, embeddings=embeddings),
            MockEmbeddingProvider([1.0, 0.0]),
            rerank_provider=reranker,
            limit=limit,
        )

    @pytest.mark.anyio
    async def test_reranker_valid_rank_selects_candidate(self) -> None:
        """reranker 返回有效序号时使用精排结果。"""
        reranker = MockReranker(result=2)
        matcher = self._matcher(reranker)

        result = await matcher.match("选第二张")

        assert result is not None
        assert result.entry_id == "2"
        assert result.source == "rerank"
        assert reranker.calls[0][0] == "选第二张"
        assert [candidate.rank for candidate in reranker.calls[0][1]] == [1, 2, 3]

    @pytest.mark.anyio
    async def test_limit_controls_rerank_candidates(self) -> None:
        """limit 控制传给 reranker 的候选数量。"""
        reranker = MockReranker(result=1)
        matcher = self._matcher(reranker, limit=2)

        result = await matcher.match("只看两个")

        assert result is not None
        assert len(reranker.calls[0][1]) == 2

    @pytest.mark.anyio
    async def test_reranker_returns_zero_fallbacks_to_top1(self) -> None:
        """reranker 返回 0 时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(result=0))

        result = await matcher.match("放弃精排")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_reranker_out_of_range_fallbacks_to_top1(self) -> None:
        """reranker 返回越界序号时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(result=99))

        result = await matcher.match("越界")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_reranker_non_integer_fallbacks_to_top1(self) -> None:
        """reranker 返回非整数时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(result="2"))

        result = await matcher.match("非整数")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"

    @pytest.mark.anyio
    async def test_reranker_exception_fallbacks_to_top1(self) -> None:
        """reranker 抛异常时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(exc=RuntimeError("rerank down")))

        result = await matcher.match("精排失败")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"
