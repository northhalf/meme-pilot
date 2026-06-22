# AI Matcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the engine-layer AI matcher that uses embedding recall plus optional reranking to return one meme candidate for `/ai`.

**Architecture:** `AIMatcher` depends on protocols for index data, embedding generation, and optional reranking. `IndexManager` exposes a read-only `get_embeddings()` boundary so matcher code does not touch `_embeddings`. The module stays service-agnostic: it does not call DeepSeek or OpenAI SDK.

**Tech Stack:** Python 3.12, standard library `dataclasses`/`logging`/`math`/`typing`, existing `pytest` test setup, existing JSON index structures.

---

## File Structure

- Modify `bot/engine/index_manager.py`
  - Add public `get_embeddings()` near `get_entries()`.
- Create `bot/engine/ai_matcher.py`
  - Define protocols, result dataclasses, vector validation, cosine scoring, Top N recall, optional rerank fallback.
- Modify `tests/unit/engine/test_index_manager.py`
  - Add tests for `get_embeddings()` shallow-copy behavior.
- Create `tests/unit/engine/test_ai_matcher.py`
  - Cover empty input, no candidates, vector search, bad vectors, embedding provider errors, rerank success and fallback.
- Modify `docs/api/API.md`
  - Add `ai_matcher.md` to the API index and add `IndexManager.get_embeddings()` to the summary.
- Modify `docs/api/bot/engine/index_manager.md`
  - Document `get_embeddings()`.
- Create `docs/api/bot/engine/ai_matcher.md`
  - Document protocols, dataclasses, constructor, and `match()` behavior.
- Modify `docs/process.md`
  - Mark `bot/engine/ai_matcher.py` as implemented.

Project rule: do not run `git add` or `git commit`. Each task ends with a review checkpoint instead of a commit.

---

### Task 1: Expose embeddings through `IndexManager`

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: Add failing tests for `get_embeddings()`**

Append this class after existing query-related `IndexManager` tests in `tests/unit/engine/test_index_manager.py`:

```python
class TestGetEmbeddings:
    """get_embeddings() 测试。"""

    def test_returns_embeddings(self, tmp_path: Path) -> None:
        """返回当前内存中的 embedding 索引。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._embeddings = {
            "1": {"text_hash": "sha256:a", "embedding": [0.1, 0.2]}
        }

        result = mgr.get_embeddings()

        assert result == {
            "1": {"text_hash": "sha256:a", "embedding": [0.1, 0.2]}
        }

    def test_returns_outer_copy(self, tmp_path: Path) -> None:
        """返回外层浅拷贝，避免调用方替换整个条目。"""
        mgr = IndexManager(data_dir=str(tmp_path))
        mgr._embeddings = {
            "1": {"text_hash": "sha256:a", "embedding": [0.1, 0.2]}
        }

        result = mgr.get_embeddings()
        result["2"] = {"text_hash": "sha256:b", "embedding": [0.3, 0.4]}

        assert "2" not in mgr._embeddings
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestGetEmbeddings -v
```

Expected: FAIL with `AttributeError: 'IndexManager' object has no attribute 'get_embeddings'`.

- [ ] **Step 3: Add `get_embeddings()` to `IndexManager`**

In `bot/engine/index_manager.py`, add this method directly after `get_entries()`:

```python
    def get_embeddings(self) -> dict[str, dict[str, object]]:
        """返回全部 embedding 条目。

        Returns:
            key 为索引 id，value 为包含 text_hash、embedding 的字典。
        """
        return self._embeddings.copy()
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestGetEmbeddings -v
```

Expected: 2 passed.

- [ ] **Step 5: Review checkpoint**

Run:

```bash
git diff -- bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
```

Expected: diff only adds `get_embeddings()` and its tests. Do not stage or commit.

---

### Task 2: Add `ai_matcher.py` base types and empty-input behavior

**Files:**
- Create: `bot/engine/ai_matcher.py`
- Test: `tests/unit/engine/test_ai_matcher.py`

- [ ] **Step 1: Create failing base tests**

Create `tests/unit/engine/test_ai_matcher.py` with this content:

```python
"""AIMatcher 单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from bot.engine.ai_matcher import (
    AIMatchCandidate,
    AIMatcher,
    AIMatchResult,
)


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
    """模拟 EmbeddingProvider。"""

    def __init__(
        self,
        embedding: list[float] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.embedding = embedding or [1.0, 0.0]
        self.exc = exc
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.exc is not None:
            raise self.exc
        return self.embedding


class MockReranker:
    """模拟 RerankProvider。"""

    def __init__(self, result: Any = 1, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, list[AIMatchCandidate]]] = []

    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int:
        self.calls.append((description, candidates))
        if self.exc is not None:
            raise self.exc
        return self.result


class TestAIMatchDataClasses:
    """AI 匹配数据类测试。"""

    def test_candidate_create(self) -> None:
        """创建 AIMatchCandidate。"""
        candidate = AIMatchCandidate(
            rank=1,
            entry_id="3",
            filename="cat.jpg",
            text="一只猫",
            similarity=0.8,
        )

        assert candidate.rank == 1
        assert candidate.entry_id == "3"
        assert candidate.filename == "cat.jpg"
        assert candidate.text == "一只猫"
        assert candidate.similarity == 0.8

    def test_result_create(self) -> None:
        """创建 AIMatchResult。"""
        result = AIMatchResult(
            entry_id="3",
            filename="cat.jpg",
            text="一只猫",
            similarity=0.8,
            source="embedding",
        )

        assert result.entry_id == "3"
        assert result.filename == "cat.jpg"
        assert result.text == "一只猫"
        assert result.similarity == 0.8
        assert result.source == "embedding"


class TestAIMatcherEmptyInput:
    """空输入与空索引测试。"""

    @pytest.mark.asyncio
    async def test_empty_description_returns_none(self) -> None:
        """空描述返回 None，且不调用 embedding。"""
        embedding_provider = MockEmbeddingProvider()
        matcher = AIMatcher(MockIndex(), embedding_provider)

        result = await matcher.match("   ")

        assert result is None
        assert embedding_provider.calls == []

    @pytest.mark.asyncio
    async def test_no_entries_returns_none(self) -> None:
        """索引为空时返回 None。"""
        matcher = AIMatcher(MockIndex(entries={}, embeddings={}), MockEmbeddingProvider())

        result = await matcher.match("心累加班")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_embeddings_returns_none(self) -> None:
        """没有向量索引时返回 None。"""
        entries = {
            "1": {"filename": "a.jpg", "text": "加班好累", "text_hash": "x"}
        }
        matcher = AIMatcher(MockIndex(entries=entries, embeddings={}), MockEmbeddingProvider())

        result = await matcher.match("心累加班")

        assert result is None
```

- [ ] **Step 2: Run the base tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'bot.engine.ai_matcher'`.

- [ ] **Step 3: Create minimal `ai_matcher.py`**

Create `bot/engine/ai_matcher.py` with this content:

```python
"""AI 语义匹配模块。

先用 embedding 做语义召回，再可选调用精排 provider 选出最终表情包。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class AIIndexProvider(Protocol):
    """AI 匹配索引数据提供者协议。"""

    def get_entries(self) -> dict[str, dict[str, str]]:
        """返回 index.json 中的 entries 字典。"""
        ...

    def get_embeddings(self) -> dict[str, dict[str, object]]:
        """返回 embeddings.json 中的向量索引。"""
        ...


class EmbeddingProvider(Protocol):
    """Embedding 服务提供者协议。"""

    async def embed(self, text: str) -> list[float]:
        """对文本生成 embedding 向量。"""
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
    """AI 表情包匹配器。"""

    def __init__(
        self,
        index_provider: AIIndexProvider,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None:
        """初始化 AI 匹配器。

        Args:
            index_provider: 索引与向量数据提供者。
            embedding_provider: 用户描述 embedding 服务。
            rerank_provider: 可选候选精排服务。
            limit: embedding 阶段最大候选数量。
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
            匹配结果；无候选时返回 None。
        """
        description = description.strip()
        if not description:
            logger.debug("AI 匹配描述为空，返回空结果")
            return None

        await self._embedding_provider.embed(description)

        entries = self._index_provider.get_entries()
        embeddings = self._index_provider.get_embeddings()
        if not entries or not embeddings:
            logger.debug("AI 匹配索引为空，返回空结果")
            return None

        return None
```

- [ ] **Step 4: Run the base tests and verify they pass**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Review checkpoint**

Run:

```bash
git diff -- bot/engine/ai_matcher.py tests/unit/engine/test_ai_matcher.py
```

Expected: new matcher module has protocols/dataclasses and empty-index behavior. Do not stage or commit.

---

### Task 3: Implement embedding recall and vector validation

**Files:**
- Modify: `bot/engine/ai_matcher.py`
- Test: `tests/unit/engine/test_ai_matcher.py`

- [ ] **Step 1: Add failing vector search tests**

Append this class to `tests/unit/engine/test_ai_matcher.py`:

```python
class TestAIMatcherEmbeddingRecall:
    """Embedding 语义召回测试。"""

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
```

- [ ] **Step 2: Run recall tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py::TestAIMatcherEmbeddingRecall -v
```

Expected: FAIL because `match()` still returns `None` for valid vectors.

- [ ] **Step 3: Replace `ai_matcher.py` with vector recall implementation**

Replace the full contents of `bot/engine/ai_matcher.py` with:

```python
"""AI 语义匹配模块。

先用 embedding 做语义召回，再可选调用精排 provider 选出最终表情包。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Protocol

logger = logging.getLogger(__name__)


class AIIndexProvider(Protocol):
    """AI 匹配索引数据提供者协议。"""

    def get_entries(self) -> dict[str, dict[str, str]]:
        """返回 index.json 中的 entries 字典。"""
        ...

    def get_embeddings(self) -> dict[str, dict[str, object]]:
        """返回 embeddings.json 中的向量索引。"""
        ...


class EmbeddingProvider(Protocol):
    """Embedding 服务提供者协议。"""

    async def embed(self, text: str) -> list[float]:
        """对文本生成 embedding 向量。"""
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
            index_provider: 索引与向量数据提供者。
            embedding_provider: 用户描述 embedding 服务。
            rerank_provider: 可选候选精排服务。
            limit: embedding 阶段最大候选数量。
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
            匹配结果；无候选时返回 None。

        Raises:
            ValueError: 用户描述 embedding 为空、非数字或为零向量。
        """
        description = description.strip()
        if not description:
            logger.debug("AI 匹配描述为空，返回空结果")
            return None

        query_vector = _coerce_vector(
            await self._embedding_provider.embed(description),
            context="用户描述 embedding",
        )
        if _vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")

        entries = self._index_provider.get_entries()
        embeddings = self._index_provider.get_embeddings()
        if not entries or not embeddings:
            logger.debug("AI 匹配索引为空，返回空结果")
            return None

        candidates = self._build_candidates(entries, embeddings, query_vector)
        if not candidates:
            logger.info("AI embedding 召回无候选：description=%r", description)
            return None

        return _candidate_to_result(candidates[0], source="embedding")

    def _build_candidates(
        self,
        entries: dict[str, dict[str, str]],
        embeddings: dict[str, dict[str, object]],
        query_vector: list[float],
    ) -> list[AIMatchCandidate]:
        """构建 embedding Top N 候选。"""
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
                logger.warning("跳过异常 embedding：entry_id=%s, reason=%s", entry_id, exc)
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
    """将候选转换为最终结果。"""
    return AIMatchResult(
        entry_id=candidate.entry_id,
        filename=candidate.filename,
        text=candidate.text,
        similarity=candidate.similarity,
        source=source,
    )


def _coerce_vector(vector: object, *, context: str) -> list[float]:
    """将向量转换为浮点数列表。"""
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
    """计算两个等长向量的余弦相似度。"""
    left_norm = _vector_norm(left)
    right_norm = _vector_norm(right)
    if left_norm == 0 or right_norm == 0:
        return None

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


def _vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))


def _entry_id_sort_key(entry_id: str) -> tuple[int, int, str]:
    """生成稳定的 entry_id 排序键。"""
    try:
        return (0, int(entry_id), "")
    except ValueError:
        return (1, 0, entry_id)
```

- [ ] **Step 4: Run recall tests and verify they pass**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py::TestAIMatcherEmbeddingRecall -v
```

Expected: 7 passed.

- [ ] **Step 5: Run all AI matcher tests**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```

Expected: 12 passed.

---

### Task 4: Add provider error handling and rerank fallback

**Files:**
- Modify: `bot/engine/ai_matcher.py`
- Test: `tests/unit/engine/test_ai_matcher.py`

- [ ] **Step 1: Add failing error and rerank tests**

Append these classes to `tests/unit/engine/test_ai_matcher.py`:

```python
class TestAIMatcherEmbeddingProviderErrors:
    """用户描述 embedding 异常测试。"""

    @pytest.mark.asyncio
    async def test_embedding_provider_error_bubbles_up(self) -> None:
        """用户描述 embedding 生成失败时向外抛。"""
        matcher = AIMatcher(
            MockIndex(),
            MockEmbeddingProvider(exc=RuntimeError("embedding down")),
        )

        with pytest.raises(RuntimeError, match="embedding down"):
            await matcher.match("心累加班")

    @pytest.mark.asyncio
    async def test_empty_query_vector_raises_value_error(self) -> None:
        """用户描述 embedding 为空列表时抛出 ValueError。"""
        provider = MockEmbeddingProvider([1.0])
        provider.embedding = []
        matcher = AIMatcher(MockIndex(), provider)

        with pytest.raises(ValueError, match="非空列表"):
            await matcher.match("心累加班")

    @pytest.mark.asyncio
    async def test_zero_query_vector_raises_value_error(self) -> None:
        """用户描述 embedding 为零向量时抛出 ValueError。"""
        matcher = AIMatcher(MockIndex(), MockEmbeddingProvider([0.0, 0.0]))

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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_limit_controls_rerank_candidates(self) -> None:
        """limit 控制传给 reranker 的候选数量。"""
        reranker = MockReranker(result=1)
        matcher = self._matcher(reranker, limit=2)

        result = await matcher.match("只看两个")

        assert result is not None
        assert len(reranker.calls[0][1]) == 2

    @pytest.mark.asyncio
    async def test_reranker_returns_zero_fallbacks_to_top1(self) -> None:
        """reranker 返回 0 时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(result=0))

        result = await matcher.match("放弃精排")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"

    @pytest.mark.asyncio
    async def test_reranker_out_of_range_fallbacks_to_top1(self) -> None:
        """reranker 返回越界序号时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(result=99))

        result = await matcher.match("越界")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"

    @pytest.mark.asyncio
    async def test_reranker_non_integer_fallbacks_to_top1(self) -> None:
        """reranker 返回非整数时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(result="2"))

        result = await matcher.match("非整数")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"

    @pytest.mark.asyncio
    async def test_reranker_exception_fallbacks_to_top1(self) -> None:
        """reranker 抛异常时 fallback 到 embedding Top 1。"""
        matcher = self._matcher(MockReranker(exc=RuntimeError("rerank down")))

        result = await matcher.match("精排失败")

        assert result is not None
        assert result.entry_id == "1"
        assert result.source == "embedding"
```

- [ ] **Step 2: Run new tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py::TestAIMatcherRerank -v
```

Expected: FAIL because current matcher ignores `rerank_provider`.

- [ ] **Step 3: Update `match()` with rerank fallback**

In `bot/engine/ai_matcher.py`, replace the final line of `match()`:

```python
        return _candidate_to_result(candidates[0], source="embedding")
```

with:

```python
        if self._rerank_provider is None:
            return _candidate_to_result(candidates[0], source="embedding")

        rank = await self._rerank(description, candidates)
        if rank is None:
            return _candidate_to_result(candidates[0], source="embedding")

        return _candidate_to_result(candidates[rank - 1], source="rerank")
```

Then add this method inside `AIMatcher`, directly after `_build_candidates()`:

```python
    async def _rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int | None:
        """调用 reranker，失败时返回 None。"""
        if self._rerank_provider is None:
            return None

        try:
            rank = await self._rerank_provider.rerank(description, candidates)
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
```

- [ ] **Step 4: Run rerank tests and verify they pass**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py::TestAIMatcherRerank -v
```

Expected: 6 passed.

- [ ] **Step 5: Run all AI matcher tests**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```

Expected: 21 passed.

---

### Task 5: Update API and process documentation

**Files:**
- Modify: `docs/process.md`
- Modify: `docs/api/API.md`
- Modify: `docs/api/bot/engine/index_manager.md`
- Create: `docs/api/bot/engine/ai_matcher.md`

- [ ] **Step 1: Update `docs/api/bot/engine/ai_matcher.md`**

Create `docs/api/bot/engine/ai_matcher.md` with:

```markdown
# bot/engine/ai_matcher.py — AI 语义匹配 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## Protocol

### `AIIndexProvider`

```python
class AIIndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]: ...
    def get_embeddings(self) -> dict[str, dict[str, object]]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_entries` | 无 | `dict[str, dict[str, str]]` | key 为索引 ID，value 为包含 `filename`、`text`、`text_hash` 的字典 |
| `get_embeddings` | 无 | `dict[str, dict[str, object]]` | key 为索引 ID，value 为包含 `text_hash`、`embedding` 的字典 |

---

### `EmbeddingProvider`

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `embed` | `text: str` — 待向量化文本 | `list[float]` | 异步生成文本 embedding 向量 |

---

### `RerankProvider`

```python
class RerankProvider(Protocol):
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `rerank` | `description` — 用户描述；`candidates` — embedding Top N 候选 | `int` | 返回 1-based 临时候选序号；返回 `0` 表示放弃精排 |

---

## 数据类

### `AIMatchCandidate`

```python
@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: str
    filename: str
    text: str
    similarity: float
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | `int` | 临时候选序号，1-based |
| `entry_id` | `str` | 索引 ID |
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | 与用户描述 embedding 的余弦相似度 |

---

### `AIMatchResult`

```python
@dataclass(frozen=True)
class AIMatchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float
    source: str
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 索引 ID |
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | embedding 余弦相似度 |
| `source` | `str` | 结果来源：`"rerank"` 或 `"embedding"` |

---

## `AIMatcher` 类

### `__init__(index_provider, embedding_provider, rerank_provider=None, limit=10) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `index_provider` | `AIIndexProvider` | 必填 | 索引与向量数据来源，如 `IndexManager` 实例 |
| `embedding_provider` | `EmbeddingProvider` | 必填 | 用户描述向量化服务 |
| `rerank_provider` | `RerankProvider \| None` | `None` | 可选候选精排服务 |
| `limit` | `int` | `10` | embedding 阶段最大候选数量 |

---

### `async match(description: str) -> AIMatchResult | None`

| | 类型 | 说明 |
|--|------|------|
| **参数** `description` | `str` | 用户自然语言描述 |
| **返回** | `AIMatchResult \| None` | 最终匹配结果；空描述、索引为空或无有效候选时返回 `None` |
| **异常** | `ValueError` | 用户描述 embedding 为空、非数字或为零向量 |

流程：

1. 清洗用户描述，空描述直接返回 `None`。
2. 调用 `embedding_provider.embed(description)` 生成用户描述向量。
3. 与 `get_embeddings()` 中的向量计算余弦相似度，不设最低阈值，取 Top `limit`。
4. 未配置 `rerank_provider` 时返回 embedding Top 1。
5. 配置 `rerank_provider` 时使用精排结果；精排失败、返回 `0`、返回非整数或越界时 fallback 到 embedding Top 1。

单条索引 embedding 异常、维度不一致或为零向量时会被跳过，并记录 warning。
```

- [ ] **Step 2: Update `docs/api/API.md`**

Modify the directory tree so it includes `ai_matcher.md` under `bot/engine`:

```text
api
├── API.md
└── bot
    ├── engine
    │   ├── ai_matcher.md
    │   ├── index_manager.md
    │   ├── keyword_searcher.md
    │   └── ocr_service.md
    └── logging_config.md
```

Add this section before `index_manager.md` or after it:

```markdown
### `docs/api/bot/engine/ai_matcher.md`

```python
class AIIndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]
    def get_embeddings(self) -> dict[str, dict[str, object]]

class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]

@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: str
    filename: str
    text: str
    similarity: float

class RerankProvider(Protocol):
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int

@dataclass(frozen=True)
class AIMatchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float
    source: str

class AIMatcher:
    def __init__(
        self,
        index_provider: AIIndexProvider,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None

    async def match(self, description: str) -> AIMatchResult | None
```
```

In the existing `IndexManager` summary block, add:

```python
    def get_embeddings(self) -> dict[str, dict[str, object]]
```

- [ ] **Step 3: Update `docs/api/bot/engine/index_manager.md`**

After the `get_entries()` section, add:

```markdown
### `get_embeddings() -> dict[str, dict[str, object]]`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, dict[str, object]]` | key 为索引 ID，value 为 `{ "text_hash": str, "embedding": list[float] }` |

返回当前内存中的 embedding 索引外层浅拷贝。调用方可读取向量数据，但不应修改返回值后期待写回生效。
```

- [ ] **Step 4: Update `docs/process.md`**

Add one checked item after the `index_manager.py` line or before `ocr_service.py`:

```markdown
- [x] `bot/engine/ai_matcher.py` — AI 语义匹配模块（协议注入索引、embedding 与可选 reranker；对用户描述 embedding 与本地 `embeddings.json` 向量计算余弦相似度，不设最低阈值取 Top 10；reranker 返回有效序号时使用精排结果，失败、返回 `0`、非整数或越界时 fallback 到 embedding Top 1；坏索引向量、维度不一致和零向量按条跳过并记录 warning；用户描述 embedding 失败或非法时向外抛出）
```

- [ ] **Step 5: Review documentation diff**

Run:

```bash
git diff -- docs/process.md docs/api/API.md docs/api/bot/engine/index_manager.md docs/api/bot/engine/ai_matcher.md
```

Expected: docs mention the new matcher module and `get_embeddings()` once in each relevant API index. Do not stage or commit.

---

### Task 6: Run full verification

**Files:**
- Verify: `bot/engine/ai_matcher.py`
- Verify: `bot/engine/index_manager.py`
- Verify: `tests/unit/engine/test_ai_matcher.py`
- Verify: `tests/unit/engine/test_index_manager.py`
- Verify: docs touched in Task 5

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py tests/unit/engine/test_index_manager.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 3: Run syntax check**

Run:

```bash
uv run python -m compileall bot tests
```

Expected: command exits with status 0 and reports successful compilation. Existing `__pycache__` output is acceptable.

- [ ] **Step 4: Review final diff**

Run:

```bash
git diff -- bot/engine/ai_matcher.py bot/engine/index_manager.py tests/unit/engine/test_ai_matcher.py tests/unit/engine/test_index_manager.py docs/process.md docs/api/API.md docs/api/bot/engine/index_manager.md docs/api/bot/engine/ai_matcher.md
```

Expected:

- `IndexManager` only gains `get_embeddings()`.
- `ai_matcher.py` contains no DeepSeek/OpenAI SDK call.
- Tests cover recall, rerank fallback, bad vectors, and provider errors.
- Docs match public interfaces.

- [ ] **Step 5: Report status to user**

Report:

```text
Implemented ai_matcher.py with protocol-based embedding recall and optional rerank fallback.
Verification:
- uv run pytest tests/unit/engine/test_ai_matcher.py tests/unit/engine/test_index_manager.py -v: PASS
- uv run pytest: PASS
- uv run python -m compileall bot tests: PASS
Docs updated:
- docs/process.md
- docs/api/API.md
- docs/api/bot/engine/index_manager.md
- docs/api/bot/engine/ai_matcher.md
No git add/commit run, per project rule.
```

If a command fails, include the failing command and the relevant output instead of the PASS line.

---

## Self-Review

Spec coverage:

- `AIMatcher.match(description) -> AIMatchResult | None`: Task 2, Task 3, Task 4.
- `IndexManager.get_embeddings()`: Task 1 and Task 5 docs.
- Protocol-only reranker, no DeepSeek service: Task 2 and final diff checklist.
- Embedding Top 10, no threshold, cosine similarity: Task 3.
- Rerank success and fallback on failure, `0`, non-integer, out-of-range: Task 4.
- Bad index vectors skipped with warning: Task 3.
- User embedding errors bubble or raise `ValueError`: Task 4.
- Docs updates: Task 5.
- Verification: Task 6.

Placeholder scan: no `TBD`, `TODO`, or open implementation instructions remain.

Type consistency: plan uses `AIMatchCandidate`, `AIMatchResult`, `AIIndexProvider`, `EmbeddingProvider`, `RerankProvider`, `AIMatcher.match()`, and `IndexManager.get_embeddings()` consistently.
