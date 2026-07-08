# /sim 相似度展示 + /info 前 10 + 搜索分页 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `/sim` 列表加相似度百分比、`/info` speaker 排行改前 10、`/search`/`/sim`/兜底多结果支持回复 `n` 翻下一页。

**Architecture:** 展示层新增 `PresentOptions` 控制相似度与分页；搜索层改为全量返回（关键词全量匹配、语义全库召回）；分页用方案 A（一次预取 + 页内切片），状态存 `matcher.state`。`/rand` 行为零回归。

**Tech Stack:** Python 3.12、NoneBot2、chromadb、pylcs、pytest（anyio/asyncio 混用，按各测试文件既有风格）。

**项目规则（最高优先级）：**
- ⚠️ **禁止在 main 分支自行 `git add`/`git commit`/`git merge`**。每个任务末尾的"验证"步骤只运行测试与语法检查，**不提交**；提交由用户审核。
- 每实现一个模块后同步 `docs/api/API.md`。
- Python 函数用 Google 风格中文 docstring，参数/返回值类型标注，保持现有中文注释与用户提示风格。

**关联 spec：** `docs/superpowers/specs/2026-07-08-search-pagination-similarity-info-design.md`

---

## 文件结构

| 文件 | 责任 |
|------|------|
| `bot/engine/keyword_searcher.py` | `search` 全量返回（`limit` 默认 `None`） |
| `bot/engine/vector_store.py` | `query` `n_results=None` 取全库 |
| `bot/engine/semantic_searcher.py` | `limit=None` 全库召回；协议依赖切 `MetadataStoreProvider`，批量映射 |
| `bot/engine/index_manager.py` | `semantic_search` `limit` 默认 `None`；`info` 排行 `[:10]` |
| `bot/plugins/_search_utils.py` | 常量 + `PresentOptions` + `_similarity_percent`；`present_candidates`/`dispatch_search_results`/`handle_got_selection`/`execute_search` 加分页与相似度 |
| `bot/plugins/meme_sim.py` | 传 `PresentOptions(ratio, "n")` |
| `bot/plugins/meme_search.py`、`meme_plain_text.py` | 传 `PresentOptions(score, "n")` |
| `bot/plugins/meme_rand.py` | 适配新签名（默认 `PresentOptions`，行为不变） |
| `bot/plugins/meme_info.py` | "前 3" -> "前 10" |
| 文档 | PRD、CONTEXT、README、docs/api/API.md |

**类型契约（全程一致）：**
```python
PAGE_SIZE: int = 10
NEXT_PAGE_TRIGGER: str = "n"

@dataclass(frozen=True)
class PresentOptions:
    show_similarity: bool = False
    similarity_scale: Literal["ratio", "score"] = "score"
    next_trigger: str | None = None
    page_size: int = PAGE_SIZE

def _similarity_percent(similarity: float, scale: str) -> int: ...

# 签名
present_candidates(bot, event, cmd_matcher, candidates, *, options=PresentOptions(), page_index=0, total_pages=1, prompt_suffix="")
dispatch_search_results(bot, event, cmd_matcher, results, *, options=PresentOptions(), prompt_suffix="")
handle_got_selection(bot, event, matcher, selection_msg, error_label="搜索", *, options=PresentOptions())
execute_search(bot, event, cmd_matcher, keyword, *, options=PresentOptions())
# state 键：all_results, page_index, total_pages, candidates, selection_id
```

**各命令 options：**
- `/sim`：`PresentOptions(show_similarity=True, similarity_scale="ratio", next_trigger=NEXT_PAGE_TRIGGER)`
- `/search`、兜底：`PresentOptions(show_similarity=True, similarity_scale="score", next_trigger=NEXT_PAGE_TRIGGER)`
- `/rand`：`PresentOptions()`（默认，不展示不翻页）

---

## Task 1: KeywordSearcher 全量返回

**Files:**
- Modify: `bot/engine/keyword_searcher.py`（`__init__` 的 `limit` 默认值、`search` 末尾切片）
- Test: `tests/unit/engine/test_keyword_searcher.py`

- [ ] **Step 1: 更新现有 `test_default_limit` 并新增全量返回测试**

在 `tests/unit/engine/test_keyword_searcher.py` 的 `TestInit` 中改：

```python
    def test_default_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._limit is None
```

在 `TestSearchResultOrder` 中新增：

```python
    def test_default_limit_returns_all(self) -> None:
        """limit 默认 None 时返回全部匹配（不截断到 10）。"""
        entries = {
            i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)  # 15 条全部命中"加班"
        }
        s = KeywordSearcher(MockMetadataStore(entries))  # 默认 limit=None
        results = s.search("加班")
        assert len(results) == 15
        assert all(r.similarity == 100.0 for r in results)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestInit::test_default_limit tests/unit/engine/test_keyword_searcher.py::TestSearchResultOrder::test_default_limit_returns_all -v`
Expected: FAIL（`_limit == 10` 断言失败 / 默认截断到 10 条）

- [ ] **Step 3: 改 `KeywordSearcher`**

`bot/engine/keyword_searcher.py` 的 `__init__`：

```python
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        threshold: float = 60.0,
        limit: int | None = None,
    ) -> None:
        """初始化关键词搜索引擎。

        Args:
            metadata_store: 元数据存储，需实现 get_all_entries() 方法。
            threshold: 最低相似度阈值，默认 60。
            limit: 最大返回结果数；None 表示返回全部匹配，默认 None。
        """
        self._metadata_store = metadata_store
        self._threshold = threshold
        self._limit = limit
```

`search` 末尾两处 `[: self._limit]` 不变（`list[:None]` 返回全量），但 docstring 更新返回说明：

```python
        exact_results = self._search_exact_substring(entries, raw)
        if exact_results:
            logger.info(
                "关键词精确子串命中：keyword=%r, 命中=%d, 返回=%d",
                keyword,
                len(exact_results),
                len(exact_results) if self._limit is None else min(len(exact_results), self._limit),
            )
            return exact_results[: self._limit]

        cleaned = _strip_all_whitespace(_remove_particles(keyword))
        if not cleaned:
            logger.debug("关键词去助词后为空，返回空结果")
            return []

        results = self._search_fuzzy_lcs(entries, cleaned)
        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            len(results) if self._limit is None else min(len(results), self._limit),
        )
        return results[: self._limit]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py -v`
Expected: PASS（含原有 `test_raw_hit_respects_limit`、`test_limit_truncation` 用 `limit=5` 仍截断 5 条）

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/engine/keyword_searcher.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 2: VectorStore.query 支持全库召回

**Files:**
- Modify: `bot/engine/vector_store.py`（`_query_sync` 与 `query` 的 `n_results` 类型）
- Test: `tests/unit/engine/test_vector_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_vector_store.py` 的 `TestUpsertAndQuery` 中新增：

```python
    async def test_query_none_returns_all(self, store: VectorStore) -> None:
        """n_results=None 时返回全库所有向量。"""
        for i in range(5):
            await store.upsert(i, [float(i), 0.0])
        hits = await store.query([0.0, 0.0], n_results=None)
        assert len(hits) == 5
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_vector_store.py::TestUpsertAndQuery::test_query_none_returns_all -v`
Expected: FAIL（`n_results=None` 传入 chroma `collection.query` 报错或返回不符）

- [ ] **Step 3: 改 `VectorStore`**

`bot/engine/vector_store.py` 的 `_query_sync` 与 `query`：

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_vector_store.py -v`
Expected: PASS（含原有 `test_query_n_results_limits` 等）

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/engine/vector_store.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 3: SemanticSearcher 全库召回 + 批量元数据映射

**Files:**
- Modify: `bot/engine/semantic_searcher.py`（协议依赖切 `MetadataStoreProvider`，批量映射，`limit=None` 全库）
- Test: `tests/unit/engine/test_semantic_searcher.py`

- [ ] **Step 1: 更新 Mock 与现有测试，新增全库测试**

`tests/unit/engine/test_semantic_searcher.py` 顶部 `MockMetadataStore` 改为提供 `get_all_entries`：

```python
class MockMetadataStore:
    def __init__(self, entries: dict[int, MemeEntry]) -> None:
        self._entries = entries

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries
```

`MockVectorStore.query` 支持 `n_results=None`：

```python
class MockVectorStore:
    def __init__(self, hits: list[VectorHit]) -> None:
        self._hits = hits

    async def query(
        self, query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]:
        if n_results is None:
            return list(self._hits)
        return self._hits[:n_results]
```

文件末尾新增：

```python
@pytest.mark.asyncio
async def test_search_semantic_limit_none_returns_all(
    sample_entries: dict[int, MemeEntry],
) -> None:
    """limit=None 时全库召回，不截断。"""
    hits = [
        VectorHit(entry_id=1, similarity=0.95),
        VectorHit(entry_id=2, similarity=0.85),
        VectorHit(entry_id=3, similarity=0.75),
    ]
    searcher = SemanticSearcher(MockMetadataStore(sample_entries), MockVectorStore(hits))

    results = await searcher.search_semantic([0.1] * 1024, limit=None)

    assert len(results) == 3
    assert [r.entry_id for r in results] == [1, 2, 3]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_semantic_searcher.py -v`
Expected: FAIL（`SemanticSearcher` 仍调 `get_entry`，`MockMetadataStore` 无该方法）

- [ ] **Step 3: 改 `SemanticSearcher`**

`bot/engine/semantic_searcher.py`：

```python
"""语义搜索器。"""

import logging
from typing import Protocol

from bot.engine.keyword_searcher import MetadataStoreProvider, SearchResult
from bot.engine.vector_store import VectorHit

logger = logging.getLogger(__name__)


class VectorQueryProvider(Protocol):
    """向量查询协议。"""

    async def query(
        self, query_embedding: list[float], n_results: int | None = 10
    ) -> list[VectorHit]: ...


class SemanticSearcher:
    """语义搜索器。

    基于 embedding 向量从 VectorStore 召回 Top-N 候选。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        vector_store: VectorQueryProvider,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store

    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int | None = 10,
    ) -> list[SearchResult]:
        """根据 embedding 向量召回最相似的 N 个表情包。

        Args:
            query_vector: 查询文本的 embedding 向量。
            limit: 召回数量上限；None 表示全库召回，默认 10。

        Returns:
            与向量最相似的 SearchResult 列表；metadata 缺失的命中会被跳过。
        """
        hits = await self.vector_store.query(query_vector, n_results=limit)
        entries = self.metadata_store.get_all_entries()
        results: list[SearchResult] = []
        for hit in hits:
            entry = entries.get(hit.entry_id)
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

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_semantic_searcher.py -v`
Expected: PASS（含 `test_search_semantic_skips_missing_metadata`、`test_search_semantic_respects_limit`、新 `test_search_semantic_limit_none_returns_all`）

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/engine/semantic_searcher.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 4: IndexManager.semantic_search 默认全库 + info 排行前 10

**Files:**
- Modify: `bot/engine/index_manager.py`（`semantic_search` 的 `limit` 默认 `None`；`info` 的 `[:3]` -> `[:10]`）
- Test: `tests/unit/engine/test_index_manager_info.py`、`tests/unit/engine/test_index_manager.py`（若有 `semantic_search` 测试则同步）

- [ ] **Step 1: 更新 `test_info_entry_count_and_ranking` 断言，新增前 10 测试**

`tests/unit/engine/test_index_manager_info.py` 的 `TestInfo.test_info_entry_count_and_ranking`，speaker 有 4 个（甲乙丙 None），前 10 = 全部 4 个：

```python
        assert info.entry_count == 10
        assert info.speaker_ranking == [("甲", 4), ("乙", 3), ("丙", 2), (None, 1)]
        assert info.status == "空闲"
```

`TestInfo` 中新增：

```python
    @pytest.mark.anyio
    async def test_info_ranking_truncates_to_ten(self, index_manager: IndexManager) -> None:
        """speaker 种类超过 10 个时，排行截断到前 10。"""
        metadata_store = index_manager._metadata_store
        for i in range(12):
            metadata_store.add(f"m{i}.jpg", f"文本{i}", speaker=f"speaker{i}")

        info = await index_manager.info()

        assert len(info.speaker_ranking) == 10
        # 每个 speaker 各 1 条，排序稳定：按 count 降序、speaker 升序
        assert info.speaker_ranking[0][1] == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_index_manager_info.py -v`
Expected: FAIL（`speaker_ranking` 仍为前 3）

- [ ] **Step 3: 改 `IndexManager`**

`bot/engine/index_manager.py` 的 `info` 方法，`[:3]` -> `[:10]`：

```python
        speaker_ranking = sorted(
            speaker_counts.items(),
            key=lambda item: (-item[1], item[0] or ""),
        )[:10]
```

`semantic_search` 签名与 docstring（`limit` 默认 `None`）：

```python
    async def semantic_search(self, description: str, limit: int | None = None) -> list[SearchResult]:
        """语义搜索入口。锁外 embed，持读锁查询。

        Args:
            description: 用户自然语言描述。
            limit: 返回结果数量上限；None 表示全库召回，默认 None。

        Returns:
            语义相似度 SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: SemanticSearcher 或 EmbeddingProvider 未注入。
            ValueError: embedding 结果为零向量。
        """
        if self._semantic_searcher is None:
            raise RuntimeError("SemanticSearcher 未注入")
        if self._embedding_provider is None:
            raise RuntimeError("EmbeddingProvider 未注入")
        query_vector = await self._embedding_provider.embed(description)
        if vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._vector_store.count() == 0:
                return []
            return await self._semantic_searcher.search_semantic(query_vector, limit=limit)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_index_manager_info.py tests/unit/engine/test_index_manager.py -v`
Expected: PASS。若 `test_index_manager.py` 有 `semantic_search` 用例因 `limit` 默认值变化而失败，按"默认全库"语义修正断言。

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/engine/index_manager.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 5: _search_utils 常量 + PresentOptions + _similarity_percent

**Files:**
- Modify: `bot/plugins/_search_utils.py`（顶部新增常量与 dataclass、辅助函数）
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/plugins/test_search_utils.py` 新增测试类：

```python
class TestSimilarityPercent:
    """_similarity_percent 量纲归一测试。"""

    def test_ratio_to_percent(self) -> None:
        from bot.plugins._search_utils import _similarity_percent
        assert _similarity_percent(0.82, "ratio") == 82
        assert _similarity_percent(1.0, "ratio") == 100
        assert _similarity_percent(0.0, "ratio") == 0

    def test_score_to_percent(self) -> None:
        from bot.plugins._search_utils import _similarity_percent
        assert _similarity_percent(82.0, "score") == 82
        assert _similarity_percent(100.0, "score") == 100
        assert _similarity_percent(60.0, "score") == 60

    def test_clamp_out_of_range(self) -> None:
        from bot.plugins._search_utils import _similarity_percent
        assert _similarity_percent(1.05, "ratio") == 100  # 浮点越界 clamp
        assert _similarity_percent(-0.1, "ratio") == 0


class TestPresentOptionsDefaults:
    """PresentOptions 默认值 = /rand 行为。"""

    def test_defaults(self) -> None:
        from bot.plugins._search_utils import PresentOptions, PAGE_SIZE, NEXT_PAGE_TRIGGER
        opts = PresentOptions()
        assert opts.show_similarity is False
        assert opts.similarity_scale == "score"
        assert opts.next_trigger is None
        assert opts.page_size == PAGE_SIZE == 10
        assert NEXT_PAGE_TRIGGER == "n"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestSimilarityPercent tests/unit/plugins/test_search_utils.py::TestPresentOptionsDefaults -v`
Expected: FAIL（`ImportError`：模块无这些符号）

- [ ] **Step 3: 在 `_search_utils.py` 顶部新增**

`bot/plugins/_search_utils.py`，在现有 import 之后、`format_metadata_line` 之前插入：

```python
from dataclasses import dataclass
from typing import Literal

PAGE_SIZE: int = 10
"""每页展示的候选条数。"""

NEXT_PAGE_TRIGGER: str = "n"
"""用户回复该词触发"下一页"。"""


@dataclass(frozen=True)
class PresentOptions:
    """候选展示选项。

    控制列表行是否展示相似度、相似度量纲、是否支持翻页。

    Attributes:
        show_similarity: 是否在列表行末尾展示相似度百分比。
        similarity_scale: 相似度量纲；ratio=0–1，score=0–100。
        next_trigger: 下一页触发词；None 表示不支持翻页（如 /rand）。
        page_size: 每页条数，默认 PAGE_SIZE。
    """

    show_similarity: bool = False
    similarity_scale: Literal["ratio", "score"] = "score"
    next_trigger: str | None = None
    page_size: int = PAGE_SIZE


def _similarity_percent(similarity: float, scale: str) -> int:
    """把相似度归一为 0–100 的整数百分比。

    Args:
        similarity: 相似度原值。
        scale: 量纲；ratio=0–1 乘 100，score=0–100 直接取整。

    Returns:
        clamp 到 [0, 100] 的整数百分比。
    """
    raw = similarity * 100 if scale == "ratio" else similarity
    return max(0, min(100, round(raw)))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestSimilarityPercent tests/unit/plugins/test_search_utils.py::TestPresentOptionsDefaults -v`
Expected: PASS

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 6: present_candidates 分页提示 + 相似度渲染

**Files:**
- Modify: `bot/plugins/_search_utils.py`（`present_candidates` 签名与渲染）
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 更新现有测试并新增分页/相似度测试**

`tests/unit/plugins/test_search_utils.py` 的 `TestPresentCandidates`，现有两个测试改为不传 `prompt_suffix` 时仍兼容新签名（默认 `options=PresentOptions()`），并新增测试：

```python
    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_show_similarity_score_appends_percent(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """score 量纲下列表行末尾追加百分比。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲", similarity=82.0)]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(show_similarity=True, similarity_scale="score", next_trigger="n")

        await present_candidates(_make_bot(), _make_event("111"), cmd, candidates, options=opts)

        sent_text = cmd.send.call_args[0][0]
        assert "1. 甲 -- 1, 无, 82%" in sent_text

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_show_similarity_ratio_appends_percent(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """ratio 量纲下 0.82 -> 82%。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲", similarity=0.82)]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(show_similarity=True, similarity_scale="ratio", next_trigger="n")

        await present_candidates(_make_bot(), _make_event("111"), cmd, candidates, options=opts)

        assert "82%" in cmd.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_next_page_hint_shown_when_has_next(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """有下一页时追加"回复 n 看下一页"。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(next_trigger="n")

        await present_candidates(
            _make_bot(), _make_event("111"), cmd, candidates,
            options=opts, page_index=0, total_pages=3,
        )

        assert "回复 n 看下一页" in cmd.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_next_page_hint_hidden_on_last_page(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """末页不追加"回复 n 看下一页"。"""
        from bot.plugins._search_utils import PresentOptions, present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲")]
        cmd = _make_matcher()
        cmd.state = {}
        opts = PresentOptions(next_trigger="n")

        await present_candidates(
            _make_bot(), _make_event("111"), cmd, candidates,
            options=opts, page_index=2, total_pages=3,
        )

        assert "回复 n 看下一页" not in cmd.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.create_selection")
    @patch("bot.plugins._search_utils.timeout_session", new_callable=AsyncMock)
    async def test_default_options_no_similarity_no_next_hint(
        self, mock_timeout: AsyncMock, mock_create_selection: MagicMock
    ) -> None:
        """/rand 默认 options：无相似度、无"回复 n"。"""
        from bot.plugins._search_utils import present_candidates

        candidates = [_make_search_result(entry_id=1, text="甲", similarity=0.0)]
        cmd = _make_matcher()
        cmd.state = {}

        await present_candidates(
            _make_bot(), _make_event("111"), cmd, candidates,
            page_index=0, total_pages=1, prompt_suffix="回复 0 换一批",
        )

        sent_text = cmd.send.call_args[0][0]
        assert "%" not in sent_text
        assert "回复 n 看下一页" not in sent_text
        assert "回复 0 换一批" in sent_text
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestPresentCandidates -v`
Expected: FAIL（新签名下相似度/翻页提示未渲染）

- [ ] **Step 3: 改 `present_candidates`**

`bot/plugins/_search_utils.py`：

```python
async def present_candidates(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    candidates: list[SearchResult],
    *,
    options: PresentOptions = PresentOptions(),
    page_index: int = 0,
    total_pages: int = 1,
    prompt_suffix: str = "",
) -> None:
    """展示候选列表并创建选择会话（仅处理多结果）。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send）。
        candidates: 当前页候选结果切片。
        options: 展示选项（相似度与翻页）。
        page_index: 当前页索引（从 0 开始）。
        total_pages: 总页数。
        prompt_suffix: 附加在提示末尾的可选文本。
    """
    user_id = event.get_user_id()
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(candidates, 1):
        meta = format_metadata_line(r.entry_id, r.speaker, r.tags)
        if options.show_similarity:
            sim_pct = _similarity_percent(r.similarity, options.similarity_scale)
            lines.append(f"{i}. {r.text} -- {meta}, {sim_pct}%")
        else:
            lines.append(f"{i}. {r.text} -- {meta}")
    lines.append(f"回复编号即可 (1-{len(candidates)})")
    if options.next_trigger and page_index + 1 < total_pages:
        lines.append(f"回复 {options.next_trigger} 看下一页")
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestPresentCandidates -v`
Expected: PASS

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 7: dispatch_search_results 分页状态

**Files:**
- Modify: `bot/plugins/_search_utils.py`（`dispatch_search_results`）
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 更新现有测试并新增分页状态测试**

`tests/unit/plugins/test_search_utils.py` 的 `TestDispatchSearchResults.test_multiple_results_calls_present_candidates` 更新为验证新签名（存 state、切第 1 页、传 options）：

```python
    @pytest.mark.asyncio
    @patch(
        "bot.plugins._search_utils.present_candidates", new_callable=AsyncMock
    )
    async def test_multiple_results_calls_present_candidates(
        self, mock_present: AsyncMock
    ) -> None:
        """多结果时应存分页状态、切第 1 页并传 options/prompt_suffix。"""
        from bot.plugins._search_utils import dispatch_search_results, PresentOptions

        results = [
            _make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 4)
        ]
        cmd = _make_matcher()
        opts = PresentOptions(show_similarity=True, similarity_scale="score", next_trigger="n")

        await dispatch_search_results(
            _make_bot(),
            _make_event("111"),
            cmd,
            results,
            options=opts,
            prompt_suffix="回复 0 换一批",
        )

        mock_present.assert_awaited_once()
        kwargs = mock_present.call_args.kwargs
        assert kwargs["options"] is opts
        assert kwargs["page_index"] == 0
        assert kwargs["total_pages"] == 1
        assert kwargs["prompt_suffix"] == "回复 0 换一批"
        # 第 1 页切片
        assert mock_present.call_args.args[3] == results[0:10]
        # 分页状态
        assert cmd.state["all_results"] == results
        assert cmd.state["page_index"] == 0
        assert cmd.state["total_pages"] == 1
```

新增多页测试：

```python
    @pytest.mark.asyncio
    @patch(
        "bot.plugins._search_utils.present_candidates", new_callable=AsyncMock
    )
    async def test_multiple_results_paginates_when_over_page_size(
        self, mock_present: AsyncMock
    ) -> None:
        """结果数 > page_size 时切第 1 页，total_pages 正确。"""
        from bot.plugins._search_utils import dispatch_search_results, PresentOptions

        results = [
            _make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 26)
        ]  # 25 条
        cmd = _make_matcher()
        opts = PresentOptions(next_trigger="n")  # page_size=10

        await dispatch_search_results(_make_bot(), _make_event("111"), cmd, results, options=opts)

        kwargs = mock_present.call_args.kwargs
        assert kwargs["page_index"] == 0
        assert kwargs["total_pages"] == 3
        assert len(mock_present.call_args.args[3]) == 10  # 第 1 页 10 条
        assert cmd.state["total_pages"] == 3
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestDispatchSearchResults -v`
Expected: FAIL（`dispatch_search_results` 未存 state、未切页）

- [ ] **Step 3: 改 `dispatch_search_results`**

`bot/plugins/_search_utils.py`：

```python
async def dispatch_search_results(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    results: list[SearchResult],
    *,
    options: PresentOptions = PresentOptions(),
    prompt_suffix: str = "",
) -> None:
    """统一处理搜索结果：无结果、单结果、多结果分页。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        results: 搜索结果全量列表。
        options: 展示选项（相似度与翻页）。
        prompt_suffix: 多结果时传给 present_candidates 的附加提示。
    """
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
        await cmd_matcher.finish(
            format_metadata_line(result.entry_id, result.speaker, result.tags)
        )
        return

    page_size = options.page_size
    total_pages = max(1, (len(results) + page_size - 1) // page_size)
    cmd_matcher.state["all_results"] = results
    cmd_matcher.state["page_index"] = 0
    cmd_matcher.state["total_pages"] = total_pages
    first_page = results[0:page_size]
    await present_candidates(
        bot,
        event,
        cmd_matcher,
        first_page,
        options=options,
        page_index=0,
        total_pages=total_pages,
        prompt_suffix=prompt_suffix,
    )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestDispatchSearchResults -v`
Expected: PASS

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 8: handle_got_selection 翻页

**Files:**
- Modify: `bot/plugins/_search_utils.py`（`handle_got_selection`）
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/plugins/test_search_utils.py` 新增 `TestHandleGotSelectionPagintion`：

```python
class TestHandleGotSelectionPagination:
    """handle_got_selection 翻页测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.handler_context")
    @patch("bot.plugins._search_utils.present_candidates", new_callable=AsyncMock)
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", new_callable=AsyncMock)
    async def test_next_trigger_advances_page(
        self,
        mock_bypass: AsyncMock,
        mock_get_selection: MagicMock,
        mock_remove_selection: MagicMock,
        mock_present: AsyncMock,
        mock_ctx: MagicMock,
    ) -> None:
        """回复 n 且有下一页时，page_index +1 并重渲染。"""
        from bot.plugins._search_utils import handle_got_selection, PresentOptions

        mock_bypass.return_value = False
        mock_get_selection.return_value = MagicMock()  # selection 有效
        all_results = [_make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 26)]
        matcher = _make_matcher()
        matcher.state = {"all_results": all_results, "page_index": 0, "total_pages": 3, "candidates": all_results[0:10]}
        event = _make_event("111")
        event.get_plaintext.return_value = "n"
        msg = MagicMock()
        msg.extract_plain_text.return_value = "n"
        opts = PresentOptions(show_similarity=True, similarity_scale="score", next_trigger="n")

        from contextlib import contextmanager
        @contextmanager
        def _ctx(uid, m):
            yield
        mock_ctx.side_effect = _ctx

        await handle_got_selection(_make_bot(), event, matcher, msg, "搜索", options=opts)

        assert matcher.state["page_index"] == 1
        mock_present.assert_awaited_once()
        assert mock_present.call_args.kwargs["page_index"] == 1
        assert len(mock_present.call_args.args[3]) == 10  # 第 2 页 10 条

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.session_manager.handler_context")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", new_callable=AsyncMock)
    async def test_next_trigger_on_last_page_rejects(
        self,
        mock_bypass: AsyncMock,
        mock_get_selection: MagicMock,
        mock_ctx: MagicMock,
    ) -> None:
        """末页回复 n 时 reject"没有更多结果了"，page_index 不变。"""
        from bot.plugins._search_utils import handle_got_selection, PresentOptions

        mock_bypass.return_value = False
        mock_get_selection.return_value = MagicMock()
        all_results = [_make_search_result(entry_id=i, text=f"甲{i}") for i in range(1, 4)]
        matcher = _make_matcher()
        matcher.state = {"all_results": all_results, "page_index": 0, "total_pages": 1, "candidates": all_results}
        event = _make_event("111")
        event.get_plaintext.return_value = "n"
        msg = MagicMock()
        msg.extract_plain_text.return_value = "n"
        opts = PresentOptions(next_trigger="n")

        from contextlib import contextmanager
        @contextmanager
        def _ctx(uid, m):
            yield
        mock_ctx.side_effect = _ctx

        await handle_got_selection(_make_bot(), event, matcher, msg, "搜索", options=opts)

        matcher.reject.assert_awaited_once()
        assert "没有更多结果了" in matcher.reject.call_args[0][0]
        assert matcher.state["page_index"] == 0

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.session_manager.remove_selection")
    @patch("bot.plugins._search_utils.session_manager.get_selection")
    @patch("bot.plugins._search_utils.got_intercept_bypass", new_callable=AsyncMock)
    async def test_valid_selection_sends_image_without_similarity(
        self,
        mock_bypass: AsyncMock,
        mock_get_selection: MagicMock,
        mock_remove_selection: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """有效编号选中后发图 + 元数据行（不含相似度）。"""
        from bot.plugins._search_utils import handle_got_selection, PresentOptions

        mock_bypass.return_value = False
        mock_get_selection.return_value = MagicMock()
        candidate = _make_search_result(entry_id=7, image_path="a.jpg", speaker="小明", similarity=82.0)
        matcher = _make_matcher()
        matcher.state = {"candidates": [candidate], "page_index": 0, "total_pages": 1, "all_results": [candidate]}
        event = _make_event("111")
        event.get_plaintext.return_value = "1"
        msg = MagicMock()
        msg.extract_plain_text.return_value = "1"
        opts = PresentOptions(show_similarity=True, similarity_scale="score", next_trigger="n")

        from contextlib import contextmanager
        from bot.session import session_manager
        real_handler_context = session_manager.handler_context
        matcher.state.setdefault("__real", True)

        await handle_got_selection(_make_bot(), event, matcher, msg, "搜索", options=opts)

        matcher.send.assert_awaited_once()
        matcher.finish.assert_awaited_once()
        finished_text = matcher.finish.call_args[0][0]
        assert "7, 小明" in finished_text
        assert "%" not in finished_text
```

> 说明：`handler_context` 是上下文管理器。测试用 `side_effect` 返回一个简单 `@contextmanager` 替身，避免触碰真实 `session_manager` 状态。第三个测试不 patch `handler_context`，走真实实现（需 `session_manager` 可用，与现有 `TestGotInterceptBypass` 一致）。

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestHandleGotSelectionPagination -v`
Expected: FAIL（`handle_got_selection` 不接受 `options`、无翻页逻辑）

- [ ] **Step 3: 改 `handle_got_selection`**

`bot/plugins/_search_utils.py`：

```python
async def handle_got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message,
    error_label: str = "搜索",
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
    """处理 got 选择编号的共享逻辑（含分页翻页）。

    供 meme_search.py、meme_sim.py、meme_plain_text.py 的 got("selection") 包装器调用。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
        error_label: 异常日志中的操作标签，用于区分调用方。
        options: 展示选项（相似度与翻页）。
    """
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):
        try:
            # /help 和 /cancel 旁路拦截
            text = event.get_plaintext().strip()
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            # 检查选择会话是否仍有效
            ss = session_manager.get_selection(user_id)
            if ss is None:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("选择已过期，请重新搜索")
                return

            selection_text = selection_msg.extract_plain_text().strip()

            # 下一页
            if options.next_trigger and selection_text == options.next_trigger:
                all_results: list[SearchResult] = matcher.state.get("all_results", [])
                page_index: int = matcher.state.get("page_index", 0)
                page_size = options.page_size
                if (page_index + 1) * page_size < len(all_results):
                    page_index += 1
                    matcher.state["page_index"] = page_index
                    start = page_index * page_size
                    current_page = all_results[start : start + page_size]
                    session_manager.remove_selection(user_id)
                    await present_candidates(
                        bot,
                        event,
                        matcher,
                        current_page,
                        options=options,
                        page_index=page_index,
                        total_pages=matcher.state.get("total_pages", 1),
                    )
                else:
                    await matcher.reject("没有更多结果了")
                return

            # 编号选择
            candidates = matcher.state.get("candidates", [])
            result = resolve_selection(matcher, candidates, selection_text)
            if isinstance(result, str):
                await matcher.reject(result)
                return

            # 有效选择：清除选择会话
            session_manager.remove_selection(user_id)
            image_path = MEMES_DIR / result.image_path
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )
            await matcher.finish(
                format_metadata_line(result.entry_id, result.speaker, result.tags)
            )

        except RejectedException:
            raise
        except asyncio.CancelledError:
            session_manager.deactivate_chat(user_id)
            raise FinishedException
        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except Exception:
            logger.exception("用户 %s 的 %s 处理异常", user_id, error_label)
            session_manager.deactivate_chat(user_id)
            raise
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py -v`
Expected: PASS（含新增翻页测试与既有 `TestResolveSelection`、`TestGotInterceptBypass`）

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 9: execute_search 透传 options

**Files:**
- Modify: `bot/plugins/_search_utils.py`（`execute_search`）
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 更新现有 `execute_search` 多结果测试**

`tests/unit/plugins/test_search_utils.py` 的 `TestExecuteSearch.test_multiple_results_delegates_to_present_candidates`，验证 `options` 透传到 `dispatch_search_results`（patch `dispatch_search_results` 而非 `present_candidates`）：

```python
    @pytest.mark.asyncio
    @patch(
        "bot.plugins._search_utils.dispatch_search_results", new_callable=AsyncMock
    )
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_multiple_results_delegates_to_dispatch(
        self, mock_get_im: MagicMock, mock_dispatch: AsyncMock
    ) -> None:
        """多个结果时应委托给 dispatch_search_results 并透传 options。"""
        from bot.plugins._search_utils import execute_search, PresentOptions

        results = [
            _make_search_result(entry_id=1, text="甲"),
            _make_search_result(entry_id=2, text="乙"),
        ]
        mock_get_im.return_value = _make_index_manager(results=results)
        bot = _make_bot()
        event = _make_event("111")
        cmd = _make_matcher()
        opts = PresentOptions(show_similarity=True, similarity_scale="score", next_trigger="n")

        await execute_search(bot, event, cmd, "加班", options=opts)

        mock_dispatch.assert_awaited_once_with(bot, event, cmd, results, options=opts)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestExecuteSearch::test_multiple_results_delegates_to_dispatch -v`
Expected: FAIL（`execute_search` 不接受 `options`、不透传）

- [ ] **Step 3: 改 `execute_search`**

`bot/plugins/_search_utils.py`，把 `dispatch_search_results(bot, event, cmd_matcher, results)` 改为透传 `options`，并加 `options` 形参：

```python
async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
    """核心关键词搜索逻辑。

    流程：获取 IndexManager 并执行关键词搜索，
    再通过 dispatch_search_results 统一处理结果分支。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        keyword: 搜索关键词。
        options: 展示选项（相似度与翻页）。
    """
    user_id = event.get_user_id()

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    # 执行搜索
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

    await dispatch_search_results(bot, event, cmd_matcher, results, options=options)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py -v`
Expected: PASS（全部 _search_utils 测试）

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 10: 各命令插件传 options

**Files:**
- Modify: `bot/plugins/meme_sim.py`、`bot/plugins/meme_search.py`、`bot/plugins/meme_plain_text.py`、`bot/plugins/meme_rand.py`
- Test: `tests/unit/plugins/test_meme_sim.py`、`test_meme_search.py`、`test_meme_plain_text.py`、`test_meme_rand.py`

- [ ] **Step 1: 写/更新插件传参测试**

在 `tests/unit/plugins/test_meme_sim.py` 新增（若已有 `dispatch_search_results` patch 用例则更新其断言）：

```python
    @pytest.mark.asyncio
    @patch("bot.plugins.meme_sim.dispatch_search_results", new_callable=AsyncMock)
    @patch("bot.plugins.meme_sim.get_index_manager")
    @patch("bot.plugins.meme_sim.is_authorized", return_value=True)
    async def test_sim_passes_ratio_options(
        self, mock_auth, mock_get_im, mock_dispatch, ...
    ) -> None:
        """/sim 传 show_similarity=True、scale=ratio、next_trigger=n。"""
        # 按 test_meme_sim.py 既有 fixture 构造 event/matcher；
        # mock_get_im.return_value.semantic_search = AsyncMock(return_value=[...])
        ...
        opts = mock_dispatch.call_args.kwargs["options"]
        assert opts.show_similarity is True
        assert opts.similarity_scale == "ratio"
        assert opts.next_trigger == "n"
```

> 说明：`...` 处沿用 `test_meme_sim.py` 既有的事件/matcher 构造方式（参考该文件已用例）。同理在 `test_meme_search.py`、`test_meme_plain_text.py` 验证 `scale == "score"`；在 `test_meme_rand.py` 验证 `dispatch_search_results` 收到默认 `PresentOptions`（`show_similarity is False`、`next_trigger is None`）且 `prompt_suffix == "回复 0 换一批"`。

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_meme_sim.py tests/unit/plugins/test_meme_search.py tests/unit/plugins/test_meme_plain_text.py tests/unit/plugins/test_meme_rand.py -v`
Expected: FAIL（插件未传 `options`）

- [ ] **Step 3: 改各插件**

`bot/plugins/meme_sim.py`，import 并在 `dispatch_search_results` 调用处传 options：

```python
from bot.plugins._search_utils import (
    PresentOptions,
    NEXT_PAGE_TRIGGER,
    dispatch_search_results,
    handle_got_selection,
)
...
SIM_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="ratio", next_trigger=NEXT_PAGE_TRIGGER
)
...
        await dispatch_search_results(bot, event, matcher, results, options=SIM_OPTIONS)
...
        await handle_got_selection(bot, event, matcher, selection_msg, "/sim", options=SIM_OPTIONS)
```

`bot/plugins/meme_search.py`：

```python
from bot.plugins._search_utils import (
    PresentOptions,
    NEXT_PAGE_TRIGGER,
    execute_search,
    handle_got_selection,
)
...
SEARCH_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="score", next_trigger=NEXT_PAGE_TRIGGER
)
...
        await execute_search(bot, event, matcher, keyword, options=SEARCH_OPTIONS)
...
        await handle_got_selection(bot, event, matcher, selection_msg, "/search", options=SEARCH_OPTIONS)
```

`bot/plugins/meme_plain_text.py`：

```python
from bot.plugins._search_utils import (
    PresentOptions,
    NEXT_PAGE_TRIGGER,
    execute_search,
    handle_got_selection,
)
...
SEARCH_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="score", next_trigger=NEXT_PAGE_TRIGGER
)
...
        await execute_search(bot, event, matcher, text, options=SEARCH_OPTIONS)
...
        await handle_got_selection(bot, event, matcher, selection_msg, "兜底搜索", options=SEARCH_OPTIONS)
```

`bot/plugins/meme_rand.py`：`dispatch_search_results` 与 `present_candidates` 调用保持传 `prompt_suffix="回复 0 换一批"`，`options` 用默认（不显式传或传 `PresentOptions()`）；`got_rand_selection` 不调 `handle_got_selection`，维持自处理"0"。仅需确保 `present_candidates` 新签名调用补齐默认 `page_index=0, total_pages=1`（用默认值即可，显式写出更清晰）：

```python
                await present_candidates(
                    bot, event, matcher, new_results,
                    page_index=0, total_pages=1, prompt_suffix="回复 0 换一批",
                )
```
`dispatch_search_results` 调用不变（默认 `options=PresentOptions()`）。

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_meme_sim.py tests/unit/plugins/test_meme_search.py tests/unit/plugins/test_meme_plain_text.py tests/unit/plugins/test_meme_rand.py -v`
Expected: PASS

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 11: meme_info 排行前 10 文本

**Files:**
- Modify: `bot/plugins/meme_info.py`（"前 3" -> "前 10"）
- Test: `tests/unit/plugins/test_meme_info.py`

- [ ] **Step 1: 写失败测试**

`tests/unit/plugins/test_meme_info.py` 的 `TestHandleInfoNormalReply`，在 `test_normal_reply_content` 断言中加：

```python
        assert "排行（前 10）：" in reply
```

并新增一个 10 项排行的测试（验证 10 行都渲染）：

```python
    @pytest.mark.asyncio
    @patch("bot.plugins.meme_info.psutil.cpu_percent", return_value=12.5)
    @patch("bot.plugins.meme_info.psutil.virtual_memory")
    @patch("bot.plugins.meme_info.get_index_manager")
    @patch.object(meme_info, "is_authorized", return_value=True)
    async def test_ranking_renders_top_ten(
        self,
        mock_auth: MagicMock,
        mock_get_index_manager: MagicMock,
        mock_virtual_memory: MagicMock,
        mock_cpu_percent: MagicMock,
    ) -> None:
        """speaker 排行 10 项时全部渲染。"""
        mock_index_manager = MagicMock()
        mock_index_manager.info = AsyncMock(
            return_value=IndexInfo(
                entry_count=100,
                speaker_ranking=[(f"s{i}", 10 - i) for i in range(10)],
                status="空闲",
            )
        )
        mock_get_index_manager.return_value = mock_index_manager
        mem_mock = MagicMock()
        mem_mock.used = 512 * 1024 * 1024
        mem_mock.total = 2048 * 1024 * 1024
        mem_mock.percent = 25.0
        mock_virtual_memory.return_value = mem_mock

        matcher = _make_matcher()
        await handle_info(_make_bot(), _make_event(), matcher)

        reply = matcher.finish.call_args[0][0]
        assert "排行（前 10）：" in reply
        assert "10. s9 1" in reply
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/plugins/test_meme_info.py -v`
Expected: FAIL（文本仍为"前 3"）

- [ ] **Step 3: 改 `meme_info.py`**

`bot/plugins/meme_info.py`：

```python
        lines = [
            f"表情包数量：{info.entry_count}",
            "排行（前 10）：",
            *ranking_lines,
            f"当前机器人状态：{info.status}",
            f"内存占用：{mem_text}",
            f"CPU占用：{cpu_text}",
        ]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/plugins/test_meme_info.py -v`
Expected: PASS

- [ ] **Step 5: 验证（提交待用户审核）**

Run: `uv run python -m compileall bot/plugins/meme_info.py`
Expected: 无语法错误。⚠️ 不自行提交。

---

## Task 12: 文档同步

**Files:**
- Modify: `docs/PRD.md`、`CONTEXT.md`、`README.md`、`docs/api/API.md`

- [ ] **Step 1: 更新 `docs/api/API.md`**

按 spec 第 9 节清单更新：
- `IndexInfo.speaker_ranking` 上限 3 -> 10。
- `_search_utils`：`present_candidates`/`dispatch_search_results`/`handle_got_selection`/`execute_search` 新增 `options` 参数；新增 `PresentOptions`、`PAGE_SIZE`、`NEXT_PAGE_TRIGGER`、`_similarity_percent` 条目。
- `KeywordSearcher.search` 契约：返回 `limit` 条以内匹配，`limit` 默认 `None` = 全量。
- `SemanticSearcher`：`limit=None` 全库、协议依赖改 `MetadataStoreProvider`。
- `IndexManager.semantic_search` `limit` 默认 `None`；`VectorStore.query` `n_results=None` 全库。
- `meme_sim`/`meme_search`/`meme_plain_text`/`meme_info` 描述更新（/sim 列表含相似度、分页、/info 前 10）。

- [ ] **Step 2: 更新 `docs/PRD.md`**

- 3.1 关键词搜索：多结果列表行含相似度百分比；新增"回复 n 看下一页"。
- 3.2 `/sim`：列表行含相似度。
- 3.5 `/info`：speaker 排行"前 3" -> "前 10"。
- 交互约束 / 边界表：分页触发词 `n`、末页"没有更多结果了"保持当前页、翻页重置超时。

- [ ] **Step 3: 更新 `CONTEXT.md`**

- `/info`、`/sim`、`/search` 术语更新；`SemanticSearcher` 协议依赖改 `MetadataStoreProvider`；新增 `PAGE_SIZE`/`NEXT_PAGE_TRIGGER`/`PresentOptions` 术语。

- [ ] **Step 4: 更新 `README.md`**

- `/info` 示例"前 3" -> "前 10"；`/sim`、`/search` 示例含相似度与"回复 n 看下一页"。

- [ ] **Step 5: 验证（提交待用户审核）**

仅文档变更，未运行测试。⚠️ 不自行提交。

---

## Task 13: 全量验证

- [ ] **Step 1: 全量测试**

Run: `uv run pytest`
Expected: 全部 PASS

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot tests`
Expected: 无语法错误

- [ ] **Step 3: 验证（提交待用户审核）**

汇总所有任务改动，连同 spec 与本计划一并交用户审核后提交。⚠️ 不在 main 自行 `git add`/`commit`。

---

## Self-Review

**1. Spec 覆盖：**
- 需求1（/sim 相似度）：Task 5（`_similarity_percent`）、Task 6（列表行渲染）、Task 10（/sim 传 ratio options） ✓
- 需求2（/info 前 10）：Task 4（`info` [:10]）、Task 11（文本）、Task 12（文档） ✓
- 需求3（分页）：Task 1/2/3/4（搜索层全量）、Task 6/7/8/9（展示层分页）、Task 10（插件传 next_trigger） ✓
- 量纲归一、/rand 零回归、协议切换：Task 3/5/10 ✓

**2. Placeholder scan：** Task 10 Step 1 的测试代码用 `...` 标注"沿用既有 fixture 构造方式"，因 `test_meme_sim.py` 等已有事件/matcher 构造且各文件风格略异，执行者按既有用例补全；非占位，是显式参照。其余步骤均含完整代码。

**3. Type consistency：** `PresentOptions` 字段、`_similarity_percent(similarity, scale)`、`present_candidates/dispatch_search_results/handle_got_selection/execute_search` 签名、state 键（`all_results`/`page_index`/`total_pages`/`candidates`/`selection_id`）在各 Task 间一致。`SIM_OPTIONS`/`SEARCH_OPTIONS` 命名在 Task 10 一致。
