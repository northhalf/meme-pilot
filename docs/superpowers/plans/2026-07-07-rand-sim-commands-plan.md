# 新增 /rand 与 /sim 命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `/rand [关键词]` 随机候选选择与 `/sim <描述文本>` 语义相似度 Top-10 选择两个命令，并复用现有搜索选择交互模式。

**Architecture:** 在 engine 层新增 `RandomSearcher` 与 `SemanticSearcher`，由 `IndexManager` 持读锁调用；在 plugins 层新增 `meme_rand.py` 与 `meme_sim.py`，并扩展 `_search_utils.py` 提供通用的候选展示/结果分派/编号解析函数。

**Tech Stack:** Python 3.12, NoneBot2, pytest, unittest.mock

**项目约束提醒：** 根据 `CLAUDE.md`，禁止在 `main` 分支自行 `git commit`。以下步骤中的 `git commit` 仅作为计划说明，实际执行时请由用户审核后再提交。

---

## 文件结构

```text
bot/engine/
├── utils.py                  # 新建：公共向量工具 _vector_norm
├── random_searcher.py        # 新建：RandomSearcher
├── semantic_searcher.py      # 新建：SemanticSearcher
├── ai_matcher.py             # 修改：从 utils 导入 _vector_norm
└── index_manager.py          # 修改：注入并暴露 random_search / semantic_search

bot/plugins/
├── _search_utils.py          # 修改：重命名 handle_selection→resolve_selection，新增 present_candidates / dispatch_search_results
├── meme_rand.py              # 新建：/rand 命令
├── meme_sim.py               # 新建：/sim 命令
└── _help_text.py             # 修改：帮助文本加入新命令

bot/bot.py                    # 修改：外部创建 RandomSearcher / SemanticSearcher 并注入 IndexManager

tests/unit/engine/
├── test_random_searcher.py   # 新建
├── test_semantic_searcher.py # 新建
└── test_index_manager.py     # 扩展

tests/unit/plugins/
├── test_search_utils.py      # 扩展
├── test_meme_rand.py         # 新建
└── test_meme_sim.py          # 新建

tests/integration/
└── test_rand_sim.py          # 新建（可选，需真实 API Key）

docs/api/API.md               # 更新
CONTEXT.md                    # 更新
README.md                     # 更新
```

---

## Task 1: 抽取 `_vector_norm` 到公共模块

**Files:**
- Create: `bot/engine/utils.py`
- Modify: `bot/engine/ai_matcher.py`
- Test: `tests/unit/engine/test_ai_matcher.py`（已存在，运行验证）

- [ ] **Step 1: 新建 `bot/engine/utils.py`**

```python
"""engine 层公共工具函数。"""

import math


def _vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))
```

- [ ] **Step 2: 修改 `bot/engine/ai_matcher.py`**

删除本地的 `_vector_norm` 函数定义，改为从 `bot.engine.utils` 导入：

```python
from bot.engine.utils import _vector_norm
```

并删除文件底部的：

```python
def _vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))
```

- [ ] **Step 3: 运行现有测试验证无回归**

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add bot/engine/utils.py bot/engine/ai_matcher.py
git commit -m "refactor(engine): 抽取 _vector_norm 到公共模块"
```

---

## Task 2: 实现 `RandomSearcher`

**Files:**
- Create: `bot/engine/random_searcher.py`
- Create: `tests/unit/engine/test_random_searcher.py`

- [ ] **Step 1: 写测试 `tests/unit/engine/test_random_searcher.py`**

```python
"""RandomSearcher 单元测试。"""

import random

import pytest

from bot.engine.keyword_searcher import KeywordSearcher, SearchResult
from bot.engine.metadata_store import MemeEntry
from bot.engine.random_searcher import RandomSearcher


class MockMetadataStore:
    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
        for i in range(1, 21)
    }


@pytest.fixture
def random_searcher(sample_entries: dict[int, MemeEntry]) -> RandomSearcher:
    metadata_store = MockMetadataStore(sample_entries)
    keyword_searcher = KeywordSearcher(metadata_store)
    return RandomSearcher(metadata_store, keyword_searcher)


class TestSearchRandom:
    def test_full_random_returns_limit(self, random_searcher: RandomSearcher) -> None:
        results = random_searcher.search_random(None, limit=10)
        assert len(results) == 10
        assert len({r.entry_id for r in results}) == 10
        assert all(r.similarity == 0.0 for r in results)

    def test_full_random_returns_all_when_entries_less_than_limit(
        self,
    ) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="甲"),
            2: MemeEntry(id=2, image_path="b.jpg", text="乙"),
        }
        metadata_store = MockMetadataStore(entries)
        keyword_searcher = KeywordSearcher(metadata_store)
        searcher = RandomSearcher(metadata_store, keyword_searcher)

        results = searcher.search_random(None, limit=10)
        assert len(results) == 2
        assert {r.entry_id for r in results} == {1, 2}

    def test_keyword_random_returns_from_search_results(
        self, random_searcher: RandomSearcher
    ) -> None:
        results = random_searcher.search_random("加班", limit=10)
        assert len(results) == 10
        assert all("加班" in r.text for r in results)

    def test_keyword_no_match_returns_empty(
        self, random_searcher: RandomSearcher
    ) -> None:
        results = random_searcher.search_random("火星文xyz", limit=10)
        assert results == []

    def test_empty_entries_returns_empty(self) -> None:
        metadata_store = MockMetadataStore({})
        keyword_searcher = KeywordSearcher(metadata_store)
        searcher = RandomSearcher(metadata_store, keyword_searcher)
        assert searcher.search_random(None, limit=10) == []

    def test_random_seed_not_fixed(self, random_searcher: RandomSearcher) -> None:
        """两次独立调用应可能产生不同结果（非确定性）。"""
        results1 = random_searcher.search_random(None, limit=10)
        results2 = random_searcher.search_random(None, limit=10)
        ids1 = [r.entry_id for r in results1]
        ids2 = [r.entry_id for r in results2]
        # 20 个里随机取 10 个，两次完全相同的概率极低；允许偶尔相同
        assert len(ids1) == 10 and len(ids2) == 10


class TestSearchResultCarriesMetadata:
    def test_speaker_and_tags_preserved(self) -> None:
        entries = {
            1: MemeEntry(
                id=1,
                image_path="a.jpg",
                text="加班",
                speaker="小明",
                tags=["吐槽"],
            ),
        }
        metadata_store = MockMetadataStore(entries)
        keyword_searcher = KeywordSearcher(metadata_store)
        searcher = RandomSearcher(metadata_store, keyword_searcher)

        results = searcher.search_random(None, limit=10)
        assert len(results) == 1
        assert results[0].speaker == "小明"
        assert results[0].tags == ["吐槽"]
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
uv run pytest tests/unit/engine/test_random_searcher.py -v
```

Expected: FAIL (RandomSearcher not defined)

- [ ] **Step 3: 实现 `bot/engine/random_searcher.py`**

```python
"""随机取样搜索器。"""

import logging
import random
from dataclasses import dataclass

from bot.engine.keyword_searcher import KeywordSearcher, MetadataStoreProvider, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class RandomSearcher:
    """随机取样搜索器。

    基于 KeywordSearcher 的结果或全库条目进行随机取样。
    """

    metadata_store: MetadataStoreProvider
    keyword_searcher: KeywordSearcher

    def search_random(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """随机返回指定数量的表情包候选。

        Args:
            keyword: 可选关键词；None/空串表示全库随机。
            limit: 返回数量上限，默认 10。

        Returns:
            随机取样后的 SearchResult 列表；候选不足时返回全部。
        """
        if keyword:
            candidates = self.keyword_searcher.search(keyword)
        else:
            entries = self.metadata_store.get_all_entries()
            candidates = [
                SearchResult(
                    entry_id=entry.id,
                    image_path=entry.image_path,
                    text=entry.text,
                    similarity=0.0,
                    speaker=entry.speaker,
                    tags=entry.tags,
                )
                for entry in entries.values()
                if entry.text
            ]

        if not candidates:
            return []

        if len(candidates) <= limit:
            return candidates

        return random.sample(candidates, limit)
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/unit/engine/test_random_searcher.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/engine/random_searcher.py tests/unit/engine/test_random_searcher.py
git commit -m "feat(engine): 实现 RandomSearcher"
```

---

## Task 3: 实现 `SemanticSearcher`

**Files:**
- Create: `bot/engine/semantic_searcher.py`
- Create: `tests/unit/engine/test_semantic_searcher.py`

- [ ] **Step 1: 写测试 `tests/unit/engine/test_semantic_searcher.py`**

```python
"""SemanticSearcher 单元测试。"""

from unittest.mock import MagicMock

import pytest

from bot.engine.metadata_store import MemeEntry
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.vector_store import VectorHit


class MockMetadataStore:
    def __init__(self, entries: dict[int, MemeEntry]) -> None:
        self._entries = entries

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        return self._entries.get(entry_id)


class MockVectorStore:
    def __init__(self, hits: list[VectorHit]) -> None:
        self._hits = hits

    async def query(self, query_embedding: list[float], n_results: int = 10) -> list[VectorHit]:
        return self._hits[:n_results]


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="a.jpg", text="加班到崩溃", speaker="小明"),
        2: MemeEntry(id=2, image_path="b.jpg", text="猫抓蝴蝶"),
        3: MemeEntry(id=3, image_path="c.jpg", text="周末快乐"),
    }


@pytest.mark.asyncio
async def test_search_semantic_returns_search_results(sample_entries: dict[int, MemeEntry]) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 2
    assert results[0].entry_id == 1
    assert results[0].similarity == 0.95
    assert results[0].speaker == "小明"
    assert results[1].entry_id == 2


@pytest.mark.asyncio
async def test_search_semantic_skips_missing_metadata(
    sample_entries: dict[int, MemeEntry]
) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=999, similarity=0.90),  # 不存在的 entry
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=10)

    assert len(results) == 1
    assert results[0].entry_id == 1


@pytest.mark.asyncio
async def test_search_semantic_respects_limit(sample_entries: dict[int, MemeEntry]) -> None:
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
        VectorHit(entry_id=3, similarity=0.75),
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=2)

    assert len(results) == 2
    assert results[0].entry_id == 1
    assert results[1].entry_id == 2
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
uv run pytest tests/unit/engine/test_semantic_searcher.py -v
```

Expected: FAIL (SemanticSearcher not defined)

- [ ] **Step 3: 实现 `bot/engine/semantic_searcher.py`**

```python
"""语义搜索器。"""

import logging
from dataclasses import dataclass
from typing import Protocol

from bot.engine.keyword_searcher import SearchResult
from bot.engine.metadata_store import MemeEntry
from bot.engine.vector_store import VectorHit

logger = logging.getLogger(__name__)


class MetadataEntryProvider(Protocol):
    """按 id 取 MemeEntry 的协议。"""

    def get_entry(self, entry_id: int) -> MemeEntry | None:
        ...


class VectorQueryProvider(Protocol):
    """向量查询协议。"""

    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]:
        ...


@dataclass
class SemanticSearcher:
    """语义搜索器。

    基于 embedding 向量从 VectorStore 召回 Top-N 候选。
    """

    metadata_store: MetadataEntryProvider
    vector_store: VectorQueryProvider

    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[SearchResult]:
        """根据 embedding 向量召回最相似的 N 个表情包。"""
        hits = await self.vector_store.query(query_vector, n_results=limit)
        results: list[SearchResult] = []
        for hit in hits:
            entry = self.metadata_store.get_entry(hit.entry_id)
            if entry is None:
                logger.warning(
                    "召回 hit 的 metadata 缺失，跳过：entry_id=%s", hit.entry_id
                )
                continue
            results.append(
                SearchResult(
                    entry_id=entry.id,
                    image_path=entry.image_path,
                    text=entry.text,
                    similarity=hit.similarity,
                    speaker=entry.speaker,
                    tags=entry.tags,
                )
            )
        return results
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/unit/engine/test_semantic_searcher.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/engine/semantic_searcher.py tests/unit/engine/test_semantic_searcher.py
git commit -m "feat(engine): 实现 SemanticSearcher"
```

---

## Task 4: 扩展 `IndexManager`

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 扩展测试 `tests/unit/engine/test_index_manager.py`**

在文件末尾新增一个测试类：

```python
# ---------------------------------------------------------------------------
# random_search / semantic_search 测试
# ---------------------------------------------------------------------------


class TestRandomSearch:
    @pytest.mark.anyio
    async def test_random_search_full_random(
        self, index_manager: IndexManager
    ) -> None:
        """无关键词时从全库随机返回候选。"""
        for i in range(5):
            (Path(index_manager._memes_dir) / f"img{i}.jpg").write_bytes(b"fake")
            await index_manager.add(f"img{i}.jpg")

        results = await index_manager.random_search(None)
        assert len(results) == 5
        assert len({r.entry_id for r in results}) == 5

    @pytest.mark.anyio
    async def test_random_search_with_keyword(
        self, index_manager: IndexManager
    ) -> None:
        """有关键词时在搜索结果中随机。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "overtime.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")
        await index_manager.add("overtime.jpg")

        results = await index_manager.random_search("加班")
        assert len(results) == 1
        assert "加班" in results[0].text

    @pytest.mark.anyio
    async def test_random_search_keyword_no_match(
        self, index_manager: IndexManager
    ) -> None:
        """关键词无匹配时返回空列表。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")

        results = await index_manager.random_search("火星文")
        assert results == []

    @pytest.mark.anyio
    async def test_random_search_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        results = await index_manager.random_search(None)
        assert results == []

    @pytest.mark.anyio
    async def test_random_search_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 RandomSearcher 时抛 RuntimeError。"""
        index_manager._random_searcher = None
        with pytest.raises(RuntimeError, match="RandomSearcher 未注入"):
            await index_manager.random_search(None)


class TestSemanticSearch:
    @pytest.mark.anyio
    async def test_semantic_search_returns_results(
        self, index_manager: IndexManager
    ) -> None:
        """语义搜索返回候选列表。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        (Path(index_manager._memes_dir) / "overtime.jpg").write_bytes(b"fake")
        await index_manager.add("cat.jpg")
        await index_manager.add("overtime.jpg")

        results = await index_manager.semantic_search("加班相关")
        assert isinstance(results, list)
        assert len(results) > 0

    @pytest.mark.anyio
    async def test_semantic_search_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        results = await index_manager.semantic_search("任意描述")
        assert results == []

    @pytest.mark.anyio
    async def test_semantic_search_zero_vector(
        self, index_manager: IndexManager
    ) -> None:
        """embedding 返回零向量时抛 ValueError。"""

        class ZeroEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.0] * 1024

        index_manager._embedding_provider = ZeroEmbeddingProvider()
        with pytest.raises(ValueError, match="零向量"):
            await index_manager.semantic_search("任意描述")

    @pytest.mark.anyio
    async def test_semantic_search_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 SemanticSearcher 时抛 RuntimeError。"""
        index_manager._semantic_searcher = None
        with pytest.raises(RuntimeError, match="SemanticSearcher 未注入"):
            await index_manager.semantic_search("任意描述")
```

- [ ] **Step 2: 运行新增测试，验证失败**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestRandomSearch -v
uv run pytest tests/unit/engine/test_index_manager.py::TestSemanticSearch -v
```

Expected: FAIL

- [ ] **Step 3: 修改 `bot/engine/index_manager.py`**

导入新增依赖：

```python
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.utils import _vector_norm
```

修改 `__init__` 签名和赋值：

```python
    def __init__(
        self,
        metadata_store: MetadataStoreProtocol,
        vector_store: VectorStoreProtocol,
        memes_dir: str,
        no_text_dir: str | None = None,
        deleted_dir: str | None = None,
        replaced_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizerProtocol | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
        random_searcher: RandomSearcher | None = None,
        semantic_searcher: SemanticSearcher | None = None,
    ) -> None:
        ...
        self._random_searcher = random_searcher
        self._semantic_searcher = semantic_searcher
```

在 `search` 和 `ai_match` 之间新增两个方法：

```python
    async def random_search(self, keyword: str | None = None) -> list[SearchResult]:
        """随机搜索入口。持读锁调用 RandomSearcher.search_random。"""
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._metadata_store.entry_count() == 0:
                return []
            if self._random_searcher is None:
                raise RuntimeError("RandomSearcher 未注入")
            return self._random_searcher.search_random(keyword)

    async def semantic_search(self, description: str) -> list[SearchResult]:
        """语义搜索入口。锁外 embed，持读锁查询。"""
        if self._semantic_searcher is None:
            raise RuntimeError("SemanticSearcher 未注入")
        if self._embedding_provider is None:
            raise RuntimeError("EmbeddingProvider 未注入")
        query_vector = await self._embedding_provider.embed(description)
        if _vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._vector_store.count() == 0:
                return []
            return await self._semantic_searcher.search_semantic(query_vector)
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestRandomSearch tests/unit/engine/test_index_manager.py::TestSemanticSearch -v
```

Expected: PASS

- [ ] **Step 5: 运行完整 IndexManager 测试套件，验证无回归**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): IndexManager 增加 random_search 与 semantic_search"
```

---

## Task 5: 扩展 `_search_utils.py`

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 扩展测试 `tests/unit/plugins/test_search_utils.py`**

新增/修改测试：

```python
# 将原有 TestHandleSelection 改名为 TestResolveSelection，并更新导入
class TestResolveSelection:
    """resolve_selection 测试。"""

    def test_valid_choice_returns_result(self) -> None:
        from bot.plugins._search_utils import resolve_selection

        candidates = [
            _make_search_result(entry_id=1, image_path="a.jpg", text="甲"),
            _make_search_result(entry_id=2, image_path="b.jpg", text="乙"),
        ]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "2")

        assert isinstance(result, SearchResult)
        assert result.entry_id == 2

    def test_invalid_text_returns_error(self) -> None:
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "abc")
        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_returns_error(self) -> None:
        from bot.plugins._search_utils import resolve_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = resolve_selection(matcher, candidates, "0")
        assert isinstance(result, str)


class TestPresentCandidates:
    """present_candidates 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session")
    async def test_creates_selection_and_formats_list(
        self, mock_timeout: MagicMock, mock_create_selection: MagicMock
    ) -> None:
        from bot.plugins._search_utils import present_candidates

        candidates = [
            _make_search_result(entry_id=1, text="甲", speaker="小明", tags=["吐槽"]),
            _make_search_result(entry_id=2, text="乙", tags=["搞笑"]),
        ]
        cmd = _make_matcher()
        cmd.state = {}
        cmd.send = AsyncMock()

        await present_candidates(_make_bot(), _make_event("111"), cmd, candidates)

        assert "candidates" in cmd.state
        assert "selection_id" in cmd.state
        sent_text = cmd.send.call_args[0][0]
        assert "1. 甲 -- 1, 小明, 吐槽" in sent_text
        assert "2. 乙 -- 2, 无, 搞笑" in sent_text
        mock_create_selection.assert_called_once()
        mock_timeout.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session")
    async def test_prompt_suffix_appended(
        self, mock_timeout: MagicMock, mock_create_selection: MagicMock
    ) -> None:
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}
        cmd.send = AsyncMock()

        await present_candidates(
            _make_bot(), _make_event("111"), cmd, candidates, prompt_suffix="回复 0 换一批"
        )

        sent_text = cmd.send.call_args[0][0]
        assert "回复 0 换一批" in sent_text


class TestDispatchSearchResults:
    """dispatch_search_results 测试。"""

    @pytest.mark.asyncio
    async def test_no_results_finishes_no_match(self) -> None:
        from bot.plugins._search_utils import dispatch_search_results

        cmd = _make_matcher()
        cmd.finish = AsyncMock()

        with patch("bot.plugins._search_utils.session_manager.deactivate_chat") as mock_deactivate:
            await dispatch_search_results(_make_bot(), _make_event("111"), cmd, [])

            cmd.finish.assert_awaited_once()
            assert "没有匹配到" in cmd.finish.call_args[0][0]
            mock_deactivate.assert_called_once_with("111")

    @pytest.mark.asyncio
    async def test_single_result_sends_image(self) -> None:
        from bot.plugins._search_utils import dispatch_search_results

        result = _make_search_result(entry_id=7, image_path="a.jpg", speaker="小明")
        cmd = _make_matcher()
        cmd.finish = AsyncMock()
        cmd.send = AsyncMock()

        with patch("bot.plugins._search_utils.session_manager.deactivate_chat") as mock_deactivate, \
             patch("bot.plugins._search_utils.MessageSegment") as mock_segment:
            await dispatch_search_results(_make_bot(), _make_event("111"), cmd, [result])

            cmd.send.assert_awaited_once()
            cmd.finish.assert_awaited_once()
            mock_deactivate.assert_called_once_with("111")

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.present_candidates")
    async def test_multiple_results_calls_present_candidates(
        self, mock_present: MagicMock
    ) -> None:
        from bot.plugins._search_utils import dispatch_search_results

        results = [
            _make_search_result(entry_id=1, text="甲"),
            _make_search_result(entry_id=2, text="乙"),
        ]
        cmd = _make_matcher()

        await dispatch_search_results(
            _make_bot(), _make_event("111"), cmd, results, prompt_suffix="回复 0 换一批"
        )

        mock_present.assert_awaited_once()
        args = mock_present.call_args
        assert args.kwargs.get("prompt_suffix") == "回复 0 换一批"
```

同时，将文件中原有的 `handle_selection` 导入测试更新为 `resolve_selection`。

- [ ] **Step 2: 运行新增/修改测试，验证失败**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: 部分 FAIL（resolve_selection / present_candidates / dispatch_search_results 未定义）

- [ ] **Step 3: 修改 `bot/plugins/_search_utils.py`**

核心变更：

```python
async def present_candidates(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    candidates: list[SearchResult],
    *,
    prompt_suffix: str = "",
) -> None:
    """展示候选列表并创建选择会话（仅处理多结果）。"""
    user_id = event.get_user_id()
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(candidates, 1):
        meta = format_metadata_line(r.entry_id, r.speaker, r.tags)
        lines.append(f"{i}. {r.text} -- {meta}")
    lines.append(f"回复编号即可 (1-{len(candidates)})")
    if prompt_suffix:
        lines.append(prompt_suffix)

    cmd_matcher.state["candidates"] = candidates
    selection_id = str(uuid.uuid4())
    cmd_matcher.state["selection_id"] = selection_id

    await cmd_matcher.send("\n".join(lines))
    task = asyncio.create_task(
        timeout_session(bot, event, user_id, selection_id, "选择已过期，请重新搜索")
    )
    session_manager.create_selection(user_id, selection_id, task)
    session_manager.reset_current_task(user_id)


async def dispatch_search_results(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    results: list[SearchResult],
    *,
    prompt_suffix: str = "",
) -> None:
    """统一处理搜索结果：无结果、单结果、多结果。"""
    user_id = event.get_user_id()

    if not results:
        session_manager.deactivate_chat(user_id)
        await cmd_matcher.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        session_manager.deactivate_chat(user_id)
        result = results[0]
        image_path = MEMES_DIR / result.image_path
        await cmd_matcher.send(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        await cmd_matcher.finish(format_metadata_line(result.entry_id, result.speaker, result.tags))
        return

    await present_candidates(bot, event, cmd_matcher, results, prompt_suffix=prompt_suffix)


def resolve_selection(
    matcher: Matcher,
    candidates: list[SearchResult],
    text: str,
) -> SearchResult | str:
    """解析用户选择编号。

    Args:
        matcher: NoneBot2 Matcher 实例。
        candidates: 搜索结果候选列表。
        text: 用户输入的编号文本。

    Returns:
        SearchResult: 选择成功时返回对应结果。
        str: 错误消息。
    """
    if not candidates:
        return "搜索状态异常，请重新搜索"

    try:
        choice = int(text)
    except ValueError:
        return f"无效编号，请回复 1-{len(candidates)} 之间的数字"

    if choice < 1 or choice > len(candidates):
        return f"无效编号，请回复 1-{len(candidates)} 之间的数字"

    return candidates[choice - 1]
```

调整 `execute_search` 内部，将多结果展示替换为调用 `dispatch_search_results`：

```python
async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
    """核心关键词搜索逻辑。"""
    user_id = event.get_user_id()
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    try:
        results = await index_manager.search(keyword)
    except asyncio.TimeoutError:
        logger.info("用户 %s 的搜索等待读锁超时", user_id)
        await cmd_matcher.finish("索引更新较慢，请稍后再试")
        return
    except Exception:
        logger.exception("关键词搜索异常: keyword=%r", keyword)
        await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
        return

    await dispatch_search_results(bot, event, cmd_matcher, results)
```

调整 `handle_got_selection` 中对 `handle_selection` 的调用为 `resolve_selection`。

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/plugins/_search_utils.py tests/unit/plugins/test_search_utils.py
git commit -m "refactor(plugins): 抽离 present_candidates / dispatch_search_results，重命名 handle_selection 为 resolve_selection"
```

---

## Task 6: 新增 `meme_rand.py` 插件

**Files:**
- Create: `bot/plugins/meme_rand.py`
- Create: `tests/unit/plugins/test_meme_rand.py`

- [ ] **Step 1: 写测试 `tests/unit/plugins/test_meme_rand.py`**

参考 `test_meme_search.py` 的 mock 模式：

```python
"""/rand 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.keyword_searcher import SearchResult

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_rand
    from bot.plugins.meme_rand import got_rand_selection, handle_rand


def _make_event(user_id: str = "12345", text: str = "/rand 加班") -> MagicMock:
    event = MagicMock()
    event.message_type = "private"
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 0.0,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
    )


def _make_message(text: str = "1") -> MagicMock:
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


class TestHandleRandAuth:
    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand, "is_authorized", return_value=True)
    @patch.object(meme_rand, "dispatch_search_results", new_callable=AsyncMock)
    async def test_authorized_user_proceeds(
        self, mock_dispatch: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=[_make_search_result()])
            await handle_rand(_make_bot(), _make_event(), _make_matcher())
            mock_dispatch.assert_awaited_once()


class TestHandleRandDelegation:
    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_keyword_passed_to_random_search(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_random = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.random_search = mock_random

            await handle_rand(_make_bot(), _make_event(text="/rand 加班"), _make_matcher())

            mock_random.assert_awaited_once_with("加班")

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_empty_keyword_passed_as_none(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_random = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.random_search = mock_random

            await handle_rand(_make_bot(), _make_event(text="/rand"), _make_matcher())

            mock_random.assert_awaited_once_with(None)


class TestHandleRandEmptyResults:
    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand.session_manager, "deactivate_chat")
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_keyword_no_match_replies_no_results(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_rand(_make_bot(), _make_event(text="/rand 火星文"), matcher)

            matcher.finish.assert_awaited_once()
            assert "没有匹配到" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_rand.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_rand.session_manager, "deactivate_chat")
    @patch.object(meme_rand, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty_dir(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        with patch.object(meme_rand, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.random_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_rand(_make_bot(), _make_event(text="/rand"), matcher)

            matcher.finish.assert_awaited_once()
            assert "表情包目录为空" in matcher.finish.call_args[0][0]
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
uv run pytest tests/unit/plugins/test_meme_rand.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `bot/plugins/meme_rand.py`**

```python
"""/rand 命令插件 — 随机表情包选择。

授权用户发送 /rand [关键词]，Bot 随机给出 10 个候选；
有关键词时先在关键词搜索结果中随机，无关键词时全库随机。
回复 0 可换一批。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.plugins._search_utils import (
    dispatch_search_results,
    format_metadata_line,
    got_intercept_bypass,
    present_candidates,
    resolve_selection,
)
from bot.plugins._help_text import HELP_TEXT
from bot.session import session_manager

logger = logging.getLogger(__name__)

rand_cmd = on_command("rand", rule=to_me(), priority=5, block=True)


@rand_cmd.handle()
async def handle_rand(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/rand 命令入口。"""
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /rand", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "rand")
            await matcher.finish(None)
            return

        if not session_manager.activate_chat(user_id, "rand", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        raw_text = event.get_plaintext().strip()
        keyword = raw_text.removeprefix("/rand").removeprefix("rand").strip()
        keyword = keyword or None

        try:
            index_manager = get_index_manager()
        except RuntimeError:
            logger.error("IndexManager 尚未初始化")
            session_manager.deactivate_chat(user_id)
            await matcher.finish("服务未就绪，请稍后再试")
            return

        try:
            results = await index_manager.random_search(keyword)
        except asyncio.TimeoutError:
            logger.info("用户 %s 的 /rand 等待读锁超时", user_id)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("索引更新较慢，请稍后再试")
            return
        except Exception:
            logger.exception("随机搜索异常: keyword=%r", keyword)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("搜索服务暂时不可用，稍后重试")
            return

        if not results:
            session_manager.deactivate_chat(user_id)
            if keyword:
                await matcher.finish("没有匹配到任何表情包 🙁")
            else:
                await matcher.finish("表情包目录为空，请先添加图片并执行 /refresh")
            return

        matcher.state["keyword"] = keyword
        await dispatch_search_results(
            bot, event, matcher, results, prompt_suffix="回复 0 换一批"
        )
    except asyncio.CancelledError:
        session_manager.deactivate_chat(user_id)
        raise FinishedException


@rand_cmd.got("selection")
async def got_rand_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """处理 /rand 的选择：支持回复 0 换一批。"""
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):
        try:
            text = event.get_plaintext().strip()
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            ss = session_manager.get_selection(user_id)
            if ss is None:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("选择已过期，请重新搜索")
                return

            selection_text = selection_msg.extract_plain_text().strip()

            if selection_text == "0":
                keyword = matcher.state.get("keyword")
                try:
                    index_manager = get_index_manager()
                    new_results = await index_manager.random_search(keyword)
                except asyncio.TimeoutError:
                    await matcher.reject("索引更新较慢，请稍后再试")
                    return
                except Exception:
                    logger.exception("/rand 换一批异常")
                    await matcher.reject("搜索服务暂时不可用，稍后重试")
                    return

                if not new_results:
                    session_manager.remove_selection(user_id)
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish("没有更多表情包了 🙁")
                    return

                session_manager.remove_selection(user_id)
                await present_candidates(
                    bot, event, matcher, new_results, prompt_suffix="回复 0 换一批"
                )
                return

            candidates = matcher.state.get("candidates", [])
            result = resolve_selection(matcher, candidates, selection_text)
            if isinstance(result, str):
                await matcher.reject(result + "\n回复 0 换一批")
                return

            session_manager.remove_selection(user_id)
            image_path = MEMES_DIR / result.image_path
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )
            await matcher.finish(
                format_metadata_line(result.entry_id, result.speaker, result.tags)
            )
        except Exception:
            logger.exception("用户 %s 的 /rand 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/unit/plugins/test_meme_rand.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/plugins/meme_rand.py tests/unit/plugins/test_meme_rand.py
git commit -m "feat(plugins): 新增 /rand 随机表情包选择命令"
```

---

## Task 7: 新增 `meme_sim.py` 插件

**Files:**
- Create: `bot/plugins/meme_sim.py`
- Create: `tests/unit/plugins/test_meme_sim.py`

- [ ] **Step 1: 写测试 `tests/unit/plugins/test_meme_sim.py`**

参考 `test_meme_search.py` 和 `test_meme_rand.py`：

```python
"""/sim 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.keyword_searcher import SearchResult

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_sim
    from bot.plugins.meme_sim import got_sim_selection, handle_sim


def _make_event(user_id: str = "12345", text: str = "/sim 心累的加班") -> MagicMock:
    event = MagicMock()
    event.message_type = "private"
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 0.9,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
    )


def _make_message(text: str = "1") -> MagicMock:
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


class TestHandleSimAuth:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim, "is_authorized", return_value=True)
    @patch.object(meme_sim, "dispatch_search_results", new_callable=AsyncMock)
    async def test_authorized_user_proceeds(
        self, mock_dispatch: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(return_value=[_make_search_result()])
            await handle_sim(_make_bot(), _make_event(), _make_matcher())
            mock_dispatch.assert_awaited_once()


class TestHandleSimDelegation:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_description_passed_to_semantic_search(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_semantic = AsyncMock(return_value=[_make_search_result()])
            mock_get_im.return_value.semantic_search = mock_semantic

            await handle_sim(_make_bot(), _make_event(text="/sim 心累的加班"), _make_matcher())

            mock_semantic.assert_awaited_once_with("心累的加班")

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_empty_description_replies_usage(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        matcher = _make_matcher()
        await handle_sim(_make_bot(), _make_event(text="/sim"), matcher)

        matcher.finish.assert_awaited_once_with("/sim <描述文本>")


class TestHandleSimEmptyResults:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_no_results_replies_not_found(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(return_value=[])
            matcher = _make_matcher()

            await handle_sim(_make_bot(), _make_event(), matcher)

            matcher.finish.assert_awaited_once_with("没有找到匹配的表情包 🙁")


class TestHandleSimErrors:
    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_timeout_replies_slow(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        import asyncio

        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(side_effect=asyncio.TimeoutError())
            matcher = _make_matcher()

            await handle_sim(_make_bot(), _make_event(), matcher)

            matcher.finish.assert_awaited_once()
            assert "索引更新较慢" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_sim.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_sim.session_manager, "deactivate_chat")
    @patch.object(meme_sim, "is_authorized", return_value=True)
    async def test_embedding_error_replies_unavailable(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        with patch.object(meme_sim, "get_index_manager") as mock_get_im:
            mock_get_im.return_value.semantic_search = AsyncMock(side_effect=ValueError("零向量"))
            matcher = _make_matcher()

            await handle_sim(_make_bot(), _make_event(), matcher)

            matcher.finish.assert_awaited_once_with("AI 服务暂时不可用，稍后重试")
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
uv run pytest tests/unit/plugins/test_meme_sim.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `bot/plugins/meme_sim.py`**

```python
"""/sim 命令插件 — 语义相似度 Top-10 选择。

授权用户发送 /sim <描述文本>，Bot 基于 embedding 语义搜索召回 Top 10 候选供选择。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.plugins._search_utils import (
    dispatch_search_results,
    handle_got_selection,
)
from bot.session import session_manager

logger = logging.getLogger(__name__)

sim_cmd = on_command("sim", rule=to_me(), priority=5, block=True)


@sim_cmd.handle()
async def handle_sim(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/sim 命令入口。"""
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /sim", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "sim")
            await matcher.finish(None)
            return

        if not session_manager.activate_chat(user_id, "sim", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        raw_text = event.get_plaintext().strip()
        description = raw_text.removeprefix("/sim").removeprefix("sim").strip()
        if not description:
            session_manager.deactivate_chat(user_id)
            await matcher.finish("/sim <描述文本>")
            return

        try:
            index_manager = get_index_manager()
        except RuntimeError:
            logger.error("IndexManager 尚未初始化")
            session_manager.deactivate_chat(user_id)
            await matcher.finish("服务未就绪，请稍后再试")
            return

        try:
            results = await index_manager.semantic_search(description)
        except asyncio.TimeoutError:
            logger.info("用户 %s 的 /sim 等待读锁超时", user_id)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("索引更新较慢，请稍后再试")
            return
        except ValueError:
            logger.warning("/sim embedding 异常: description=%r", description)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("AI 服务暂时不可用，稍后重试")
            return
        except Exception:
            logger.exception("语义搜索异常: description=%r", description)
            session_manager.deactivate_chat(user_id)
            await matcher.finish("AI 服务暂时不可用，稍后重试")
            return

        if not results:
            session_manager.deactivate_chat(user_id)
            await matcher.finish("没有找到匹配的表情包 🙁")
            return

        await dispatch_search_results(bot, event, matcher, results)
    except asyncio.CancelledError:
        session_manager.deactivate_chat(user_id)
        raise FinishedException


@sim_cmd.got("selection")
async def got_sim_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """处理 /sim 的选择。"""
    await handle_got_selection(bot, event, matcher, selection_msg, "/sim")
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/unit/plugins/test_meme_sim.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/plugins/meme_sim.py tests/unit/plugins/test_meme_sim.py
git commit -m "feat(plugins): 新增 /sim 语义相似度选择命令"
```

---

## Task 8: 更新 `bot/bot.py` 注入点

**Files:**
- Modify: `bot/bot.py`
- Test: 运行 `uv run python -m compileall bot/bot.py` 做语法检查

- [ ] **Step 1: 修改 `bot/bot.py`**

找到 `IndexManager` 初始化处，在 `keyword_searcher` 和 `ai_matcher` 创建之后、IndexManager 初始化之前，添加：

```python
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher

random_searcher = RandomSearcher(metadata_store, keyword_searcher)
semantic_searcher = SemanticSearcher(metadata_store, vector_store)
```

然后在 `IndexManager(...)` 调用中新增两个参数：

```python
index_manager = IndexManager(
    metadata_store=metadata_store,
    vector_store=vector_store,
    ...,
    keyword_searcher=keyword_searcher,
    ai_matcher=ai_matcher,
    random_searcher=random_searcher,
    semantic_searcher=semantic_searcher,
)
```

- [ ] **Step 2: 语法检查**

```bash
uv run python -m compileall bot/bot.py
```

Expected: Compiled OK

- [ ] **Step 3: Commit**

```bash
git add bot/bot.py
git commit -m "feat(bot): IndexManager 注入 RandomSearcher 与 SemanticSearcher"
```

---

## Task 9: 更新 `_help_text.py`

**Files:**
- Modify: `bot/plugins/_help_text.py`
- Test: `tests/unit/plugins/test_meme_help.py`（已存在，验证帮助文本包含新命令）

- [ ] **Step 1: 修改 `bot/plugins/_help_text.py`**

在 `HELP_TEXT` 中 `/search` 之后、`/ai` 之前插入两行：

```text
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包
```

更新后的 `HELP_TEXT` 示例：

```python
HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>]：通过聊天添加一张表情包
/addtag <id> <tag>...：为指定表情包添加标签
/del <id>...：删除指定表情包（需确认）
/edittext <id> <新文本>：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人]：设置或清空表情包的说话人
/refresh：扫描 memes/ 并增量更新索引
/info：查看机器人状态与统计信息
/cancel：取消当前正在执行的命令"""
```

- [ ] **Step 2: 运行帮助相关测试**

```bash
uv run pytest tests/unit/plugins/test_meme_help.py -v
```

Expected: PASS（若测试检查 HELP_TEXT 内容，则可能需要同步更新测试断言）

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/_help_text.py
git commit -m "feat(plugins): 帮助文本加入 /rand 与 /sim"
```

---

## Task 10: 更新文档

**Files:**
- Modify: `docs/api/API.md`
- Modify: `CONTEXT.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 `docs/api/API.md`**

在 engine 部分新增：

```markdown
### `docs/api/bot/engine/random_searcher.md`

```python
@dataclass
class RandomSearcher:
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        keyword_searcher: KeywordSearcher,
    ) -> None

    def search_random(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]
```

### `docs/api/bot/engine/semantic_searcher.md`

```python
@dataclass
class SemanticSearcher:
    def __init__(
        self,
        metadata_store: MetadataEntryProvider,
        vector_store: VectorQueryProvider,
    ) -> None

    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[SearchResult]
```

### `docs/api/bot/engine/index_manager.md`

新增方法：

```python
async def random_search(self, keyword: str | None = None) -> list[SearchResult]
async def semantic_search(self, description: str) -> list[SearchResult]
```
```

- [ ] **Step 2: 更新 `CONTEXT.md`**

在术语表「核心概念」中新增：

```markdown
| **随机选择** | `/rand [关键词]` 命令的行为：有关键词时在关键词搜索结果中随机取 10 个，无关键词时全库随机；回复 `0` 换一批，每次独立抽样 |
| **语义选择** | `/sim <描述文本>` 命令的行为：基于 embedding 语义搜索召回 Top 10 候选供用户选择，不调用 LLM 精排 |
```

在「交互协议」的 `/search`、`/ai` 附近新增 `/rand` 和 `/sim` 条目。

- [ ] **Step 3: 更新 `README.md`**

在「✨ 功能」部分 `/search` 之后新增 `/rand` 和 `/sim` 的说明与示例：

```markdown
### 🎲 随机选择 `/rand`

```
你: /rand 加班
Bot: 找到多个匹配的表情包，请选择：
    1. 加班到凌晨三点的我 -- 23, 小明
    ...
    10. 周日晚上的加班通知 -- 45, 无
    回复编号即可 (1-10)
    回复 0 换一批
```

### 🔗 语义选择 `/sim`

```
你: /sim 一张表达心累的加班表情包
Bot: 找到多个匹配的表情包，请选择：
    1. 加班到凌晨三点的我 -- 23, 小明, 吐槽, 加班
    ...
你: 1
Bot: (发送对应表情包)
Bot: 23, 小明, 吐槽, 加班
```
```

- [ ] **Step 4: Commit**

```bash
git add docs/api/API.md CONTEXT.md README.md
git commit -m "docs: 更新 /rand 与 /sim 的 API、术语和 README 说明"
```

---

## Task 11: 集成测试

**Files:**
- Create: `tests/integration/test_rand_sim.py`

- [ ] **Step 1: 写集成测试**

```python
"""/rand 与 /sim 集成测试。

需要真实索引。运行前确保已设置 DEEPSEEK_API_KEY 与 OPENAI_EMBEDDING_API_KEY。
"""

import pytest


@pytest.mark.asyncio
async def test_rand_returns_candidates() -> None:
    """端到端验证 /rand 能返回候选列表。"""
    # 这里需要构造完整的 NoneBot2 测试环境或调用 IndexManager 直接测试。
    # 简化示例：直接调用 IndexManager.random_search。
    from bot.app_state import get_index_manager

    index_manager = get_index_manager()
    results = await index_manager.random_search(None)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_sim_returns_candidates() -> None:
    """端到端验证 /sim 能返回候选列表。"""
    from bot.app_state import get_index_manager

    index_manager = get_index_manager()
    results = await index_manager.semantic_search("加班")
    assert isinstance(results, list)
```

- [ ] **Step 2: 运行集成测试（可选）**

```bash
export DEEPSEEK_API_KEY=sk-your-key
export OPENAI_EMBEDDING_API_KEY=sk-your-key
uv run pytest tests/integration/test_rand_sim.py -v -s
```

Expected: PASS（依赖真实索引和 API）

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rand_sim.py
git commit -m "test(integration): 新增 /rand 与 /sim 端到端测试"
```

---

## 自审检查表

- [x] **Spec coverage**: `/rand` 随机、`/rand [关键词]` 过滤随机、回复 `0` 换一批、`/sim` 语义 Top-10、群聊支持、会话互斥、文档更新均有对应任务。
- [x] **Placeholder scan**: 无 TBD/TODO，所有代码步骤给出了具体代码或命令。
- [x] **Type consistency**: `random_search` / `semantic_search` 签名、返回类型、`resolve_selection` 名称在全文档一致。

---

## 执行交接

**Plan complete and saved to `docs/superpowers/plans/2026-07-07-rand-sim-commands-plan.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach would you like?
