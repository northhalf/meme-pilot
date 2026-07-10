# /query 组合检索命令 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/query`(`/q`) 命令，用 `#tag`/`@speaker` 前缀实现关键词、说话人、标签的单独与组合检索。

**Architecture:** engine 层新建 `CombinedSearcher`（先按 speakers OR + tags AND 过滤 entries 子集，再在子集上跑 `KeywordSearcher.search_in`），`IndexManager` 加 `search_combined` 持读锁调用；插件层 `meme_query.py` 解析前缀参数后复用现有 `_search_utils` 展示/分页/选择逻辑。

**Tech Stack:** Python 3.12、NoneBot2、pytest（anyio + asyncio）、pylcs、jieba。

**提交策略:** 项目 CLAUDE.md 与 memory 禁止自行 `git add`/`commit`。每个 Task 末尾的 "Checkpoint" 步骤为向用户报告完成并等待审核，**不自行提交**。

**参考 spec:** `docs/superpowers/specs/2026-07-09-query-command-design.md`

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `bot/engine/keyword_searcher.py` | 关键词搜索；新增 `search_in` 支持子集搜索 | 修改 |
| `bot/engine/combined_searcher.py` | 组合检索（speaker/tags 过滤 + 关键词子集） | 新建 |
| `bot/engine/index_manager.py` | 加 `search_combined` 持读锁 + 注入参数 | 修改 |
| `bot/engine/__init__.py` | 导出 `CombinedSearcher` | 修改 |
| `bot/app_state.py` | 加 `get_combined_searcher` | 修改 |
| `bot/bot.py` | startup 创建并注入 `CombinedSearcher` | 修改 |
| `bot/plugins/_search_utils.py` | 加 `execute_combined_search` | 修改 |
| `bot/plugins/meme_query.py` | `/query` 命令插件 | 新建 |
| `bot/plugins/_help_text.py` | HELP_TEXT 加 `/query` 行 | 修改 |
| `tests/unit/engine/test_keyword_searcher.py` | 补 `search_in` 测试 | 修改 |
| `tests/unit/engine/test_combined_searcher.py` | CombinedSearcher 单测 | 新建 |
| `tests/unit/engine/test_index_manager.py` | 补 `search_combined` 测试 + fixture 注入 | 修改 |
| `tests/unit/plugins/test_search_utils.py` | 补 `execute_combined_search` 测试 | 修改 |
| `tests/unit/plugins/test_meme_query.py` | `/query` 插件单测 | 新建 |
| `docs/PRD.md`、`CONTEXT.md`、`README.md`、`docs/api/API.md` | 文档同步 | 修改 |

---

## Task 1: KeywordSearcher.search_in（在子集上搜索）

**Files:**
- Modify: `bot/engine/keyword_searcher.py`
- Test: `tests/unit/engine/test_keyword_searcher.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_keyword_searcher.py` 末尾追加：

```python
class TestSearchIn:
    """search_in：在给定 entries 子集上搜索。"""

    def test_search_in_respects_subset(self, sample_entries: dict[int, MemeEntry]) -> None:
        """search_in 只在传入子集上匹配，不触及全集其他条目。"""
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        subset = {5: sample_entries[5]}  # "当你的老板说今天要加班"
        results = s.search_in(subset, "加班")
        assert len(results) == 1
        assert results[0].entry_id == 5
        assert results[0].similarity == 100.0

    def test_search_in_empty_subset_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search_in({}, "加班") == []

    def test_search_in_empty_keyword_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search_in(sample_entries, "") == []

    def test_search_in_fuzzy_fallback(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        """子集上无精确命中时走 LCS 模糊回退。"""
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        subset = {1: sample_entries[1]}  # "一只猫在跳起来抓蝴蝶哈哈哈"
        results = s.search_in(subset, "猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_search_in_respects_limit(self) -> None:
        """search_in 同样按 _limit 截断。"""
        entries = {
            i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        assert len(s.search_in(entries, "加班")) == 5

    def test_search_equals_search_in_on_full_entries(
        self, sample_entries: dict[int, MemeEntry], searcher: KeywordSearcher
    ) -> None:
        """search(keyword) 在全集结果应等于 search_in(全集, keyword)。"""
        keyword = "加班"
        full = searcher._metadata_store.get_all_entries()
        assert searcher.search(keyword) == searcher.search_in(full, keyword)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestSearchIn -v`
Expected: FAIL，`AttributeError: 'KeywordSearcher' object has no attribute 'search_in'`

- [ ] **Step 3: 实现 search_in 并重构 search**

在 `bot/engine/keyword_searcher.py` 的 `search` 方法**之前**插入 `search_in`，并把 `search` 改为委托：

```python
    def search_in(
        self,
        entries: dict[int, MemeEntry],
        keyword: str,
    ) -> list[SearchResult]:
        """在给定 entries 子集上执行关键词搜索（两层匹配，逻辑同 search）。

        Args:
            entries: 索引条目子集，key=int(id)。
            keyword: 用户输入的搜索关键词。

        Note:
            调用方必须已持有读锁，保证读取期间子集快照不被并发写入修改。

        Returns:
            按相似度降序排列的搜索结果列表；limit=None 时返回全部匹配，否则最多返回 limit 条。
            无匹配时返回空列表。
        """
        keyword = keyword.strip()
        if not keyword:
            logger.debug("关键词为空，返回空结果")
            return []

        raw = _strip_all_whitespace(keyword)
        if not raw:
            logger.debug("关键词去空白后为空，返回空结果")
            return []

        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        exact_results = self._search_exact_substring(entries, raw)
        if exact_results:
            logger.info(
                "关键词精确子串命中：keyword=%r, 命中=%d, 返回=%d",
                keyword,
                len(exact_results),
                (
                    len(exact_results)
                    if self._limit is None
                    else min(len(exact_results), self._limit)
                ),
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

    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        在全部条目上执行两层匹配（逻辑同 search_in）：
        1. 精确子串层：用「原始输入去所有空白、保留助词」的关键词做子串匹配；
           命中则只返回包含该子串的条目（similarity=100.0）。
        2. LCS 模糊回退层：仅当第一层未命中时启用，用「去助词+去空白」的关键词
           走现有 LCS 模糊匹配（阈值 60，全量匹配）。

        Args:
            keyword: 用户输入的搜索关键词。

        Note:
            调用方必须已持有读锁，保证读取期间 MetadataStore 快照不被并发写入修改。
            IndexManager.search() 负责持锁。

        Returns:
            按相似度降序排列的搜索结果列表；limit=None 时返回全部匹配，否则最多返回 limit 条。
            无匹配时返回空列表。
        """
        entries = self._metadata_store.get_all_entries()
        return self.search_in(entries, keyword)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py -v`
Expected: PASS（含新 TestSearchIn 与全部原有用例回归）

- [ ] **Step 5: Checkpoint**

向用户报告 Task 1 完成，等待审核后再继续。不自行 commit。

---

## Task 2: CombinedSearcher（组合检索引擎）

**Files:**
- Create: `bot/engine/combined_searcher.py`
- Test: `tests/unit/engine/test_combined_searcher.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/engine/test_combined_searcher.py`：

```python
"""CombinedSearcher 单元测试。"""

import pytest

from bot.engine.combined_searcher import CombinedSearcher
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry


class MockMetadataStore:
    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    return {
        1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨", speaker="小明", tags=["吐槽", "加班"]),
        2: MemeEntry(id=2, image_path="b.jpg", text="老板又让加班", speaker="小红", tags=["加班"]),
        3: MemeEntry(id=3, image_path="c.jpg", text="周末加班通知", speaker="小明", tags=["通知", "加班"]),
        4: MemeEntry(id=4, image_path="d.jpg", text="猫在睡觉", speaker=None, tags=["萌宠"]),
        5: MemeEntry(id=5, image_path="e.jpg", text="Cat", speaker="Tom", tags=["animal"]),
    }


@pytest.fixture
def combined(sample_entries: dict[int, MemeEntry]) -> CombinedSearcher:
    md = MockMetadataStore(sample_entries)
    return CombinedSearcher(md, KeywordSearcher(md))


class TestSpeakerFilter:
    def test_single_speaker_exact(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], [])
        assert {r.entry_id for r in results} == {1, 3}
        assert all(r.speaker == "小明" for r in results)

    def test_multiple_speakers_or(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明", "小红"], [])
        assert {r.entry_id for r in results} == {1, 2, 3}

    def test_speaker_case_sensitive(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, ["tom"], []) == []  # "Tom" != "tom"

    def test_speaker_none_not_matched(self, combined: CombinedSearcher) -> None:
        """speaker=None 的条目不被 @ 命中。"""
        results = combined.search(None, ["小明"], [])
        assert 4 not in {r.entry_id for r in results}

    def test_speaker_no_match_returns_empty(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, ["不存在"], []) == []


class TestTagFilter:
    def test_single_tag(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班"])
        assert {r.entry_id for r in results} == {1, 2, 3}

    def test_multiple_tags_and(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班", "吐槽"])
        assert {r.entry_id for r in results} == {1}

    def test_tag_case_sensitive(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, [], ["Animal"]) == []  # "animal" != "Animal"

    def test_tag_not_exist_returns_empty(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, [], ["不存在的标签"]) == []

    def test_duplicate_tags_deduped(self, combined: CombinedSearcher) -> None:
        """重复 tag 不影响 AND 判定。"""
        results = combined.search(None, [], ["加班", "加班"])
        assert {r.entry_id for r in results} == {1, 2, 3}


class TestSpeakerAndTagCombined:
    def test_speaker_and_tag_intersection(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], ["通知"])
        assert {r.entry_id for r in results} == {3}


class TestWithKeyword:
    def test_keyword_on_subset(self, combined: CombinedSearcher) -> None:
        results = combined.search("凌晨", ["小明"], [])
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_keyword_subset_excludes_filtered_out(self, combined: CombinedSearcher) -> None:
        """关键词只在过滤子集上匹配，不召回被过滤掉的条目。"""
        results = combined.search("加班", ["小明"], [])
        assert {r.entry_id for r in results} == {1, 3}  # entry 2(小红) 被过滤

    def test_keyword_no_match_in_subset_returns_empty(
        self, combined: CombinedSearcher
    ) -> None:
        assert combined.search("火星文", ["小明"], []) == []

    def test_keyword_empty_string_treated_as_no_keyword(
        self, combined: CombinedSearcher
    ) -> None:
        """keyword='' 视为无关键词，走纯过滤分支，similarity=0.0。"""
        results = combined.search("", ["小明"], [])
        assert {r.entry_id for r in results} == {1, 3}
        assert all(r.similarity == 0.0 for r in results)


class TestNoKeywordBranch:
    def test_sorted_by_entry_id_ascending(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明", "小红"], [])
        assert [r.entry_id for r in results] == [1, 2, 3]

    def test_similarity_zero(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, [], ["加班"])
        assert all(r.similarity == 0.0 for r in results)

    def test_carries_metadata(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明"], [])
        r1 = next(r for r in results if r.entry_id == 1)
        assert r1.speaker == "小明"
        assert r1.tags == ["吐槽", "加班"]


class TestEmptyEntries:
    def test_empty_store_returns_empty(self) -> None:
        md = MockMetadataStore({})
        combined = CombinedSearcher(md, KeywordSearcher(md))
        assert combined.search("加班", ["小明"], ["加班"]) == []

    def test_filter_to_empty_returns_empty(self, combined: CombinedSearcher) -> None:
        assert combined.search(None, ["小明"], ["萌宠"]) == []  # 小明无萌宠 tag
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/engine/test_combined_searcher.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'bot.engine.combined_searcher'`

- [ ] **Step 3: 实现 CombinedSearcher**

创建 `bot/engine/combined_searcher.py`：

```python
"""组合搜索器 - 按关键词/说话人/标签组合检索。

先按 speakers(OR) + tags(AND) 过滤 entries 子集，再在子集上执行关键词搜索：
- 有关键词：委托 KeywordSearcher.search_in，返回带 similarity 的结果（降序）。
- 无关键词：包装 similarity=0.0，按 entry_id 升序返回。
"""

import logging

from .keyword_searcher import KeywordSearcher
from .metadata_store import MemeEntry
from .protocols import MetadataStoreProvider
from .types import SearchResult

logger = logging.getLogger(__name__)


class CombinedSearcher:
    """组合搜索引擎。

    Attributes:
        _metadata_store: 元数据存储提供者，需实现 get_all_entries()。
        _keyword_searcher: 关键词搜索器，用于子集上的关键词匹配。
    """

    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        keyword_searcher: KeywordSearcher,
    ) -> None:
        """初始化组合搜索引擎。

        Args:
            metadata_store: 元数据存储提供者。
            keyword_searcher: 关键词搜索器。
        """
        self._metadata_store = metadata_store
        self._keyword_searcher = keyword_searcher

    @staticmethod
    def _filter_entries(
        entries: dict[int, MemeEntry],
        speakers: list[str],
        tags: list[str],
    ) -> dict[int, MemeEntry]:
        """按 speakers(OR) + tags(AND) 过滤 entries 子集。

        Args:
            entries: 全部索引条目。
            speakers: 说话人列表（OR，精确相等）；空列表表示不过滤。
            tags: 标签列表（AND，区分大小写）；空列表表示不过滤。

        Returns:
            过滤后的 entries 子集。
        """
        speaker_set = set(speakers) if speakers else None
        tag_set = set(tags) if tags else None
        result: dict[int, MemeEntry] = {}
        for eid, entry in entries.items():
            if speaker_set is not None and entry.speaker not in speaker_set:
                continue
            if tag_set is not None and not tag_set.issubset(set(entry.tags)):
                continue
            result[eid] = entry
        return result

    def search(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str] | None = None,
    ) -> list[SearchResult]:
        """按关键词/说话人/标签组合检索。

        Args:
            keyword: 关键词；None 或空串表示纯过滤（不跑关键词匹配）。
            speakers: 说话人列表（OR，精确相等）；空列表表示不过滤。
            tags: 标签列表（AND，区分大小写）；None 或空列表表示不过滤。

        Note:
            调用方必须已持有读锁。IndexManager.search_combined() 负责持锁。

        Returns:
            有关键词时按相似度降序；无关键词时按 entry_id 升序。无匹配返回空列表。
        """
        tags = tags or []
        entries = self._metadata_store.get_all_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        filtered = self._filter_entries(entries, speakers, tags)
        if not filtered:
            logger.info(
                "组合检索过滤后子集为空: speakers=%r, tags=%r", speakers, tags
            )
            return []

        if keyword:
            results = self._keyword_searcher.search_in(filtered, keyword)
            logger.info(
                "组合检索（含关键词）: keyword=%r, speakers=%r, tags=%r, 命中=%d",
                keyword,
                speakers,
                tags,
                len(results),
            )
            return results

        results = [
            SearchResult(
                entry_id=entry.id,
                image_path=entry.image_path,
                text=entry.text,
                similarity=0.0,
                speaker=entry.speaker,
                tags=entry.tags,
            )
            for entry in filtered.values()
        ]
        results.sort(key=lambda r: r.entry_id)
        logger.info(
            "组合检索（纯过滤）: speakers=%r, tags=%r, 命中=%d",
            speakers,
            tags,
            len(results),
        )
        return results
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/engine/test_combined_searcher.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: Checkpoint**

向用户报告 Task 2 完成，等待审核。不自行 commit。

---

## Task 3: IndexManager.search_combined（持读锁委托）

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_index_manager.py` 的 `TestSemanticSearch` 类**之后**追加新测试类：

```python
class TestCombinedSearch:
    @pytest.mark.anyio
    async def test_search_combined_with_keyword(
        self, index_manager: IndexManager
    ) -> None:
        """组合检索：关键词在过滤子集上匹配。"""
        index_manager._metadata_store.add(
            "加班.jpg", "加班到凌晨", speaker="小明", tags=["吐槽"]
        )
        results = await index_manager.search_combined("加班", ["小明"], [])
        assert len(results) == 1
        assert results[0].similarity == 100.0
        assert results[0].entry_id == 1

    @pytest.mark.anyio
    async def test_search_combined_pure_filter(
        self, index_manager: IndexManager
    ) -> None:
        """纯过滤（无关键词）返回 similarity=0.0。"""
        index_manager._metadata_store.add(
            "加班.jpg", "加班到凌晨", speaker="小明", tags=["吐槽"]
        )
        results = await index_manager.search_combined(None, ["小明"], [])
        assert len(results) == 1
        assert results[0].similarity == 0.0

    @pytest.mark.anyio
    async def test_search_combined_tag_filter(
        self, index_manager: IndexManager
    ) -> None:
        """tags AND 过滤。"""
        index_manager._metadata_store.add(
            "a.jpg", "加班到凌晨", speaker="小明", tags=["吐槽", "加班"]
        )
        index_manager._metadata_store.add(
            "b.jpg", "周末加班", speaker="小明", tags=["加班"]
        )
        results = await index_manager.search_combined(None, [], ["吐槽"])
        assert {r.entry_id for r in results} == {1}

    @pytest.mark.anyio
    async def test_search_combined_empty_index(
        self, index_manager: IndexManager
    ) -> None:
        assert await index_manager.search_combined("加班", [], []) == []

    @pytest.mark.anyio
    async def test_search_combined_not_injected(
        self, index_manager: IndexManager
    ) -> None:
        """未注入 CombinedSearcher 时抛 RuntimeError。"""
        index_manager._metadata_store.add("a.jpg", "加班", speaker="小明")
        index_manager._combined_searcher = None
        with pytest.raises(RuntimeError, match="CombinedSearcher 未注入"):
            await index_manager.search_combined("加班", ["小明"], [])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestCombinedSearch -v`
Expected: FAIL，`AttributeError: ... search_combined` 或 `AttributeError: '_combined_searcher'`

- [ ] **Step 3: 修改 index_manager fixture 注入 CombinedSearcher**

在 `tests/unit/engine/test_index_manager.py` 的 `index_manager` fixture 中，定位：

```python
    random_searcher = RandomSearcher(metadata_store, keyword_searcher)
    semantic_searcher = SemanticSearcher(metadata_store, vector_store)

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        ocr_provider=MockOcrProvider(),
        embedding_provider=embedding_provider,
        optimizer=MockOptimizer(),
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        random_searcher=random_searcher,
        semantic_searcher=semantic_searcher,
    )
```

替换为：

```python
    random_searcher = RandomSearcher(metadata_store, keyword_searcher)
    semantic_searcher = SemanticSearcher(metadata_store, vector_store)
    from bot.engine.combined_searcher import CombinedSearcher
    combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)

    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        ocr_provider=MockOcrProvider(),
        embedding_provider=embedding_provider,
        optimizer=MockOptimizer(),
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        random_searcher=random_searcher,
        semantic_searcher=semantic_searcher,
        combined_searcher=combined_searcher,
    )
```

- [ ] **Step 4: 实现 search_combined**

在 `bot/engine/index_manager.py` 顶部 import 区，`from .random_searcher import RandomSearcher` 行下方加：

```python
from .combined_searcher import CombinedSearcher
```

在 `IndexManager.__init__` 签名中，`semantic_searcher: SemanticSearcher | None = None,` 行下方加参数：

```python
        combined_searcher: CombinedSearcher | None = None,
```

在 `__init__` body 中 `self._semantic_searcher = semantic_searcher` 行下方加：

```python
        self._combined_searcher = combined_searcher
```

在 `__init__` 的 docstring Args 中，`semantic_searcher:` 行下方加：

```
            combined_searcher: 组合搜索器，由 IndexManager 持锁后调用。
```

在 `semantic_search` 方法**之后**、`ai_match` 方法**之前**插入：

```python
    async def search_combined(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str],
    ) -> list[SearchResult]:
        """组合检索入口。持读锁调用 CombinedSearcher.search。

        Args:
            keyword: 关键词；None 或空串表示纯过滤。
            speakers: 说话人列表（OR，精确相等）；空列表不过滤。
            tags: 标签列表（AND，区分大小写）；空列表不过滤。

        Returns:
            SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: CombinedSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._metadata_store.entry_count() == 0:
                return []
            if self._combined_searcher is None:
                raise RuntimeError("CombinedSearcher 未注入")
            return self._combined_searcher.search(keyword, speakers, tags)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: PASS（含新 TestCombinedSearch 与全部原有用例回归）

- [ ] **Step 6: Checkpoint**

向用户报告 Task 3 完成，等待审核。不自行 commit。

---

## Task 4: 包导出与依赖注入接线

**Files:**
- Modify: `bot/engine/__init__.py`
- Modify: `bot/app_state.py`
- Modify: `bot/bot.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_combined_searcher.py` 顶部 import 区已有 `from bot.engine.combined_searcher import CombinedSearcher`。在文件末尾追加导入测试：

```python
class TestPackageExport:
    def test_combined_searcher_exported_from_engine(self) -> None:
        """CombinedSearcher 应可从 bot.engine 顶层导入。"""
        from bot.engine import CombinedSearcher as Exported

        assert Exported is CombinedSearcher

    def test_app_state_has_get_combined_searcher(self) -> None:
        """app_state 应提供 get_combined_searcher。"""
        from bot.app_state import get_combined_searcher

        assert callable(get_combined_searcher)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/engine/test_combined_searcher.py::TestPackageExport -v`
Expected: FAIL，`ImportError: cannot import name 'CombinedSearcher' from 'bot.engine'`

- [ ] **Step 3: 导出 CombinedSearcher**

在 `bot/engine/__init__.py` 中，`from .keyword_searcher import KeywordSearcher` 行下方加：

```python
from .combined_searcher import CombinedSearcher
```

在 `__all__` 列表中，`"KeywordSearcher",` 行下方加：

```python
    "CombinedSearcher",
```

- [ ] **Step 4: app_state 加 get_combined_searcher**

在 `bot/app_state.py` 顶部 import 区，`from .engine.semantic_searcher import SemanticSearcher` 行下方加：

```python
from .engine.combined_searcher import CombinedSearcher
```

在 `_semantic_searcher: SemanticSearcher | None = None` 行下方加：

```python
_combined_searcher: CombinedSearcher | None = None
```

在 `init_app` 签名中，`semantic_searcher: SemanticSearcher | None = None,` 行下方加：

```python
    combined_searcher: CombinedSearcher | None = None,
```

在 `init_app` 的 `global` 语句中，`global _random_searcher, _semantic_searcher` 改为：

```python
    global _random_searcher, _semantic_searcher, _combined_searcher
```

在 `init_app` body 中 `_semantic_searcher = semantic_searcher` 行下方加：

```python
    _combined_searcher = combined_searcher
```

在 `init_app` docstring Args 中，`semantic_searcher:` 描述行下方加：

```
        combined_searcher: 组合搜索器实例，可选。
```

在文件末尾（`get_semantic_searcher` 函数之后）追加：

```python
def get_combined_searcher() -> CombinedSearcher:
    """获取 CombinedSearcher 单例。

    Returns:
        已初始化的 CombinedSearcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _combined_searcher is None:
        raise RuntimeError("CombinedSearcher 尚未初始化，请先调用 init_app()")
    return _combined_searcher
```

- [ ] **Step 5: bot.py startup 创建并注入**

在 `bot/bot.py` 顶部 import 区，`from bot.engine.semantic_searcher import SemanticSearcher` 行下方加：

```python
from bot.engine.combined_searcher import CombinedSearcher
```

在 `_on_startup` 中，`semantic_searcher = SemanticSearcher(metadata_store, vector_store)` 行下方加：

```python
    combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)
```

在 `IndexManager(...)` 构造中，`semantic_searcher=semantic_searcher,` 行下方加：

```python
        combined_searcher=combined_searcher,
```

在 `init_app(...)` 调用中，`semantic_searcher=semantic_searcher,` 行下方加：

```python
        combined_searcher=combined_searcher,
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/unit/engine/test_combined_searcher.py::TestPackageExport -v`
Expected: PASS

- [ ] **Step 7: 语法检查**

Run: `uv run python -m compileall bot/app_state.py bot/bot.py bot/engine/__init__.py`
Expected: 无报错

- [ ] **Step 8: Checkpoint**

向用户报告 Task 4 完成，等待审核。不自行 commit。

---

## Task 5: _search_utils.execute_combined_search

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/plugins/test_search_utils.py` 末尾追加：

```python
class TestExecuteCombinedSearch:
    """execute_combined_search 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    @patch("bot.plugins._search_utils.dispatch_search_results", new_callable=AsyncMock)
    @patch("bot.plugins._search_utils.session_manager")
    async def test_delegates_to_search_combined(
        self,
        mock_session: MagicMock,
        mock_dispatch: AsyncMock,
        mock_get_im: MagicMock,
    ) -> None:
        """应调用 index_manager.search_combined 并分发结果。"""
        from bot.plugins._search_utils import execute_combined_search

        mock_get_im.return_value.search_combined = AsyncMock(
            return_value=[_make_search_result()]
        )
        event = MagicMock()
        event.get_user_id.return_value = "123"

        await execute_combined_search(
            MagicMock(), event, _make_matcher(),
            keyword="加班", speakers=["小明"], tags=["吐槽"],
        )

        mock_get_im.return_value.search_combined.assert_awaited_once_with(
            "加班", ["小明"], ["吐槽"]
        )
        mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager")
    @patch("bot.plugins._search_utils.session_manager")
    async def test_timeout_replies_slow(
        self,
        mock_session: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """读锁超时回复「索引更新较慢」。"""
        import asyncio
        from bot.plugins._search_utils import execute_combined_search

        mock_get_im.return_value.search_combined = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        event = MagicMock()
        event.get_user_id.return_value = "123"
        matcher = _make_matcher()

        await execute_combined_search(
            MagicMock(), event, matcher,
            keyword="加班", speakers=[], tags=[],
        )

        matcher.finish.assert_awaited_once()
        assert "索引更新较慢" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_index_manager", side_effect=RuntimeError())
    @patch("bot.plugins._search_utils.session_manager")
    async def test_not_ready_replies(
        self,
        mock_session: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """IndexManager 未就绪回复「服务未就绪」。"""
        from bot.plugins._search_utils import execute_combined_search

        event = MagicMock()
        event.get_user_id.return_value = "123"
        matcher = _make_matcher()

        await execute_combined_search(
            MagicMock(), event, matcher,
            keyword="加班", speakers=[], tags=[],
        )

        matcher.finish.assert_awaited_once_with("服务未就绪，请稍后再试")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestExecuteCombinedSearch -v`
Expected: FAIL，`ImportError: cannot import name 'execute_combined_search'`

- [ ] **Step 3: 实现 execute_combined_search**

在 `bot/plugins/_search_utils.py` 的 `execute_search` 函数**之后**插入：

```python
async def execute_combined_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str | None,
    speakers: list[str],
    tags: list[str],
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
    """组合检索核心逻辑。

    流程：获取 IndexManager 并执行组合检索，
    再通过 dispatch_search_results 统一处理结果分支。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        keyword: 关键词；None 或空串表示纯过滤。
        speakers: 说话人列表（OR）。
        tags: 标签列表（AND）。
        options: 展示选项（相似度与翻页）。
    """
    user_id = event.get_user_id()

    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        session_manager.deactivate_chat(user_id)
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    try:
        results = await index_manager.search_combined(keyword, speakers, tags)
    except asyncio.TimeoutError:
        logger.info("用户 %s 的组合检索等待读锁超时", user_id)
        session_manager.deactivate_chat(user_id)
        await cmd_matcher.finish("索引更新较慢，请稍后再试")
        return
    except Exception:
        logger.exception(
            "组合检索异常: keyword=%r, speakers=%r, tags=%r",
            keyword,
            speakers,
            tags,
        )
        session_manager.deactivate_chat(user_id)
        await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
        return

    await dispatch_search_results(bot, event, cmd_matcher, results, options=options)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_search_utils.py::TestExecuteCombinedSearch -v`
Expected: PASS

- [ ] **Step 5: Checkpoint**

向用户报告 Task 5 完成，等待审核。不自行 commit。

---

## Task 6: meme_query.py 插件

**Files:**
- Create: `bot/plugins/meme_query.py`
- Test: `tests/unit/plugins/test_meme_query.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/plugins/test_meme_query.py`：

```python
"""/query 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_query
    from bot.plugins.meme_query import _parse_args, got_selection, handle_query


def _make_event(user_id: str = "12345", text: str = "/query 加班 @小明 #吐槽") -> MagicMock:
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


def _make_args(text: str) -> MagicMock:
    args = MagicMock()
    args.extract_plain_text.return_value = text
    return args


class TestParseArgs:
    def test_keyword_speaker_tags(self) -> None:
        kw, sp, tg = _parse_args("加班心累 @小明 #吐槽 #加班")
        assert kw == "加班心累"
        assert sp == ["小明"]
        assert tg == ["吐槽", "加班"]

    def test_multiple_speakers_or(self) -> None:
        kw, sp, tg = _parse_args("@小明 @小红")
        assert kw == ""
        assert sp == ["小明", "小红"]
        assert tg == []

    def test_multiple_tags_and(self) -> None:
        kw, sp, tg = _parse_args("#吐槽 #深夜")
        assert kw == ""
        assert sp == []
        assert tg == ["吐槽", "深夜"]

    def test_keyword_only(self) -> None:
        kw, sp, tg = _parse_args("加班")
        assert kw == "加班"
        assert sp == []
        assert tg == []

    def test_lone_prefix_ignored(self) -> None:
        kw, sp, tg = _parse_args("加班 # @")
        assert kw == "加班"
        assert sp == []
        assert tg == []

    def test_keyword_with_spaces(self) -> None:
        kw, sp, tg = _parse_args("加班 心累 @小明")
        assert kw == "加班 心累"
        assert sp == ["小明"]


class TestHandleQueryAuth:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query, "is_authorized", return_value=True)
    @patch.object(meme_query, "execute_combined_search", new_callable=AsyncMock)
    async def test_authorized_proceeds(
        self, mock_exec: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        await handle_query(
            _make_bot(), _make_event(), _make_matcher(), _make_args("加班 @小明 #吐槽")
        )
        mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(meme_query, "log_unauthorized")
    @patch.object(meme_query, "is_authorized", return_value=False)
    async def test_unauthorized_silent(
        self, mock_auth: MagicMock, mock_log: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(), matcher, _make_args("加班"))
        matcher.finish.assert_awaited_once_with(None)


class TestHandleQueryEmptyArgs:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query.session_manager, "deactivate_chat")
    @patch.object(meme_query, "is_authorized", return_value=True)
    async def test_all_empty_replies_usage(
        self,
        mock_auth: MagicMock,
        mock_deactivate: MagicMock,
        mock_activate: MagicMock,
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(text="/query"), matcher, _make_args(""))
        matcher.finish.assert_awaited_once_with(
            "/query <关键词> [@说话人] [#标签...]"
        )


class TestHandleQueryOptions:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query, "is_authorized", return_value=True)
    @patch.object(meme_query, "execute_combined_search", new_callable=AsyncMock)
    async def test_keyword_uses_kw_options(
        self, mock_exec: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(), matcher, _make_args("加班"))
        opts = mock_exec.call_args.kwargs["options"]
        assert opts.show_similarity is True
        assert opts.similarity_scale == "score"
        assert matcher.state["query_options"].show_similarity is True

    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_query, "is_authorized", return_value=True)
    @patch.object(meme_query, "execute_combined_search", new_callable=AsyncMock)
    async def test_no_keyword_uses_filter_options(
        self, mock_exec: AsyncMock, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(
            _make_bot(), _make_event(text="/query @小明"), matcher, _make_args("@小明")
        )
        opts = mock_exec.call_args.kwargs["options"]
        assert opts.show_similarity is False
        assert matcher.state["query_options"].show_similarity is False


class TestHandleQuerySession:
    @pytest.mark.asyncio
    @patch.object(meme_query.session_manager, "activate_chat", return_value=False)
    @patch.object(meme_query, "is_authorized", return_value=True)
    async def test_busy_replies_cancel(
        self, mock_auth: MagicMock, mock_activate: MagicMock
    ) -> None:
        matcher = _make_matcher()
        await handle_query(_make_bot(), _make_event(), matcher, _make_args("加班"))
        matcher.finish.assert_awaited_once_with("已有命令在处理中，请先 /cancel")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_query.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'bot.plugins.meme_query'`

- [ ] **Step 3: 实现 meme_query 插件**

创建 `bot/plugins/meme_query.py`：

```python
"""/query 命令插件 - 组合检索表情包。

按关键词/说话人/标签组合检索：
- #tag 标记标签（可多个，AND）
- @speaker 标记说话人（可多个，OR）
- 其余 token 为关键词
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.plugins._search_utils import (
    NEXT_PAGE_TRIGGER,
    PresentOptions,
    execute_combined_search,
    handle_got_selection,
)
from bot.session import session_manager

logger = logging.getLogger(__name__)

QUERY_KW_OPTIONS = PresentOptions(
    show_similarity=True, similarity_scale="score", next_trigger=NEXT_PAGE_TRIGGER
)
"""有关键词时：展示关键词相似度（score 0-100）+ 翻页。"""

QUERY_FILTER_OPTIONS = PresentOptions(
    show_similarity=False, next_trigger=NEXT_PAGE_TRIGGER
)
"""无关键词纯过滤时：不展示相似度 + 翻页。"""

QUERY_USAGE = "/query <关键词> [@说话人] [#标签...]"

query_cmd = on_command("query", rule=to_me(), priority=5, block=True, aliases={"q"})


def _parse_args(text: str) -> tuple[str, list[str], list[str]]:
    """解析 /query 参数：#tag / @speaker / 关键词。

    Args:
        text: 命令参数纯文本。

    Returns:
        (keyword, speakers, tags) 三元组：
        keyword 为剩余 token 空格拼接（可能为空串）；
        speakers 为 @ 前缀 token 去前缀列表（OR）；
        tags 为 # 前缀 token 去前缀列表（AND）。
        # / @ 单独成 token（前缀后为空）忽略。
    """
    speakers: list[str] = []
    tags: list[str] = []
    kw_tokens: list[str] = []
    for tok in text.split():
        if tok.startswith("#") and len(tok) > 1:
            tags.append(tok[1:])
        elif tok.startswith("@") and len(tok) > 1:
            speakers.append(tok[1:])
        else:
            kw_tokens.append(tok)
    return " ".join(kw_tokens), speakers, tags


@query_cmd.handle()
async def handle_query(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """/query 命令入口。

    流程：授权校验 -> 会话检查 -> 解析参数 -> 调用 execute_combined_search。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入）。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /query", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "query")
            await matcher.finish(None)
            return

        if not session_manager.activate_chat(user_id, "query", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        text = args.extract_plain_text().strip()
        keyword, speakers, tags = _parse_args(text)

        if not keyword and not speakers and not tags:
            session_manager.deactivate_chat(user_id)
            logger.info("用户 %s 的 /query 缺少参数", user_id)
            await matcher.finish(QUERY_USAGE)
            return

        logger.info(
            "用户 %s 组合检索: keyword=%r, speakers=%r, tags=%r",
            user_id,
            keyword,
            speakers,
            tags,
        )
        options = QUERY_KW_OPTIONS if keyword else QUERY_FILTER_OPTIONS
        matcher.state["query_options"] = options
        await execute_combined_search(
            bot, event, matcher, keyword, speakers, tags, options=options
        )
    except asyncio.CancelledError:
        session_manager.deactivate_chat(user_id)
        raise FinishedException
    except FinishedException:
        session_manager.deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的 /query 处理异常", user_id)
        session_manager.deactivate_chat(user_id)
        raise


@query_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    options: PresentOptions = matcher.state.get("query_options", QUERY_FILTER_OPTIONS)
    await handle_got_selection(
        bot, event, matcher, selection_msg, "/query", options=options
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_query.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: Checkpoint**

向用户报告 Task 6 完成，等待审核。不自行 commit。

---

## Task 7: 帮助文本与文档同步

**Files:**
- Modify: `bot/plugins/_help_text.py`
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `README.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 写失败测试**

创建或确认 `tests/unit/plugins/test_meme_help.py` 中有 HELP_TEXT 断言。在 `tests/unit/plugins/test_meme_query.py` 末尾追加一个帮助文本断言：

```python
class TestHelpTextContainsQuery:
    def test_help_text_includes_query(self) -> None:
        from bot.plugins._help_text import HELP_TEXT

        assert "/query" in HELP_TEXT
        assert "@说话人" in HELP_TEXT
        assert "#标签" in HELP_TEXT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_query.py::TestHelpTextContainsQuery -v`
Expected: FAIL，`AssertionError: assert '/query' in '...'`

- [ ] **Step 3: 更新 _help_text.py**

在 `bot/plugins/_help_text.py` 中，定位：

```
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
```

替换为：

```
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足）
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_query.py::TestHelpTextContainsQuery -v`
Expected: PASS

- [ ] **Step 5: 同步 docs/PRD.md**

在 `docs/PRD.md` §3 功能需求中，§3.1（关键词搜索）之后插入新小节 §3.x「组合检索 /query」，内容：

```markdown
### 3.x 功能：组合检索

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/query <关键词> [@说话人] [#标签...]`（短命令 `/q`）

`#tag` 标记标签（可多个，AND 同时满足）；`@speaker` 标记说话人（可多个，OR 任一命中）；其余 token 为关键词。三者可单独或组合使用。

#### 流程

```
用户: /query 加班 @小明 #吐槽
        │
        ▼
Bot 解析: keyword="加班", speakers=["小明"], tags=["吐槽"]
        │
        ▼
IndexManager.search_combined（持读锁）
        ├── CombinedSearcher: 过滤 speaker∈["小明"](OR) AND "吐槽"∈tags(AND)
        ├── keyword 非空 -> KeywordSearcher.search_in(子集, "加班")
        └── 返回带 similarity 的结果
        │
        ▼
dispatch_search_results（复用 /search 的空/单/多结果分支）
        ├── 无结果 -> "没有匹配到任何表情包 🙁"
        ├── 单结果 -> 发图 + 元数据行
        └── 多结果 -> 每页 10 条分页，回复 n 翻页
```

#### 交互约束

- 有关键词时列表行展示关键词相似度百分比（score 0–100，同 `/search`）；无关键词纯过滤时不展示相似度（同 `/rand`）。
- speaker 精确相等、区分大小写；tags 精确匹配、区分大小写、多个为 AND；多 speaker 为 OR。
- `#`/`@` 单独成 token（前缀后为空）忽略；三者皆空时回复用法提示。
- 权限属组 B（私聊 + 群聊 @bot）；与 `/search`、`/ai`、`/add` 等共用会话互斥与读锁。
```

并在 §3.5 权限约束的「组 B」列表中加入 `/query`：

```
- 组 B（私聊 + 群聊@）：`/search`、`/query`、`/rand`、`/sim`、`/help`、普通文本 - 授权用户群聊中 @bot 时可正常触发
```

在 §5 边界情况表追加：

```markdown
| 授权用户私聊/群聊@发送 /query | 按 keyword/speaker/tags 组合检索 |
| /query 无参数 | 回复 "/query <关键词> [@说话人] [#标签...]" |
| /query 仅 @speaker 或 #tag | 纯过滤，按 entry_id 升序返回，不展示相似度 |
| /query 多 @speaker | OR 任一命中 |
| /query 多 #tag | AND 同时满足 |
| /query 关键词含 # 或 @ | 被前缀解析吞掉，不作为关键词搜索 |
```

- [ ] **Step 6: 同步 CONTEXT.md**

在 `CONTEXT.md` 术语表「交互协议」中 `/search` 行之后插入：

```markdown
| **/query** | 组合检索命令，格式 `/query <关键词> [@说话人] [#标签...]`；`#tag` 标记标签（可多个，AND）、`@speaker` 标记说话人（可多个，OR）、其余为关键词；speaker 精确相等区分大小写、tag 区分大小写；有关键词时展示关键词相似度（score）、无关键词纯过滤时不展示相似度（按 entry_id 升序）；权限属组 B（私聊+群聊@bot）；等待选择期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；短命令 `/q`，与 `/query` 等价 |
```

在「技术组件」中 `RandomSearcher` 行之后插入：

```markdown
| **CombinedSearcher** | 组合搜索器，`bot/engine/combined_searcher.py`；先按 `speakers`(OR) + `tags`(AND) 过滤 `MetadataStore.get_all_entries` 子集，再委托 `KeywordSearcher.search_in` 在子集上跑关键词匹配；无关键词时包装 `similarity=0.0` 按 `entry_id` 升序；`IndexManager.search_combined` 持读锁调用；依赖 `MetadataStoreProvider` + `KeywordSearcher` |
```

- [ ] **Step 7: 同步 README.md**

在 `README.md` 帮助命令块中，`/search` 行之后插入：

```
     /query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足）
```

在 README「功能」章节 `/search` 小节之后插入 `/query` 示例小节：

```markdown
### 🧩 组合检索 `/query`

```
你: /query 加班 @小明 #吐槽
Bot: 找到多个匹配的表情包，请选择：
    1. 加班到凌晨三点的我 -- 23, 小明, 吐槽, 加班, 100%
    回复编号即可 (1-1)
    回复 n 看下一页
你: 1
Bot: (发送对应表情包)
Bot: 23, 小明, 吐槽, 加班

你: /query @小明            # 仅按 speaker
你: /query #吐槽 #深夜       # 多 tag AND
你: /query @小明 @小红       # 多 speaker OR
```
```

在 README「群聊支持」段落中，组 B 列表加入 `/query`。

- [ ] **Step 8: 同步 docs/api/API.md**

在 `docs/api/API.md` 目录树中 `engine/` 下 `semantic_searcher.md` 之后加 `combined_searcher.md`；`plugins/` 下 `meme_search.md` 之后加 `meme_query.md`。

在「API 文件索引」中 `keyword_searcher.md` 段落之后新增：

````markdown
### `docs/api/bot/engine/combined_searcher.md`

```python
class CombinedSearcher:
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        keyword_searcher: KeywordSearcher,
    ) -> None

    def search(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str] | None = None,
    ) -> list[SearchResult]
    # 有关键词：在过滤子集上跑 KeywordSearcher.search_in，带 similarity 降序
    # 无关键词：包装 similarity=0.0，按 entry_id 升序
```
````

在 `meme_search.md` 段落之后新增 `meme_query.md` 段落：

````markdown
### `bot/plugins/meme_query.py`

NoneBot2 命令插件，注册 `/query` 命令。

- 注册：`on_command("query", rule=to_me(), priority=5, block=True, aliases={"q"})`
- 依赖：`auth.is_authorized()`、`app_state.get_index_manager()`、`bot.session.session_manager`、`_search_utils.execute_combined_search`、`_search_utils.handle_got_selection`、`_search_utils.PresentOptions`
- 参数解析：`#tag` -> tags（AND）、`@speaker` -> speakers（OR）、其余 -> 关键词；`#`/`@` 单独 token 忽略；三者皆空回复用法提示
- 管道：`IndexManager.search_combined(keyword, speakers, tags)`
- 展示：有关键词用 `QUERY_KW_OPTIONS`(show_similarity=True, score, next_trigger="n")；无关键词用 `QUERY_FILTER_OPTIONS`(show_similarity=False, next_trigger="n")
- 群聊：支持群聊 @bot 触发（属组 B）
````

更新 `index_manager.md` 段落，在 `semantic_search` 之后加 `search_combined`：

```python
    async def search_combined(
        self, keyword: str | None, speakers: list[str], tags: list[str]
    ) -> list[SearchResult]
    # 持读锁调用 CombinedSearcher.search；空库返回 []；超时抛 asyncio.TimeoutError；
    # 未注入 CombinedSearcher 抛 RuntimeError
```

并在 `IndexManager.__init__` 参数列表补 `combined_searcher: CombinedSearcher | None = None`。

更新 `app_state.md` 段落，补 `get_combined_searcher() -> CombinedSearcher` 与 `init_app` 的 `combined_searcher` 参数。

更新 `keyword_searcher.md` 段落，补 `search_in(entries, keyword)` 方法签名说明。

更新 `bot.md` 段落 startup 说明，补「创建 `CombinedSearcher(metadata_store, keyword_searcher)` 并注入 `IndexManager` 与 `app_state`」。

- [ ] **Step 9: Checkpoint**

向用户报告 Task 7 完成（含文档同步），等待审核。不自行 commit。

---

## Task 8: 全量检查

**Files:** 无（仅运行检查）

- [ ] **Step 1: 语法编译检查**

Run: `uv run python -m compileall bot tests`
Expected: 无报错（所有 `.py` 编译通过）

- [ ] **Step 2: 全量单元测试**

Run: `uv run pytest tests/unit -q`
Expected: 全部 PASS（含原有用例回归 + 新增 /query 相关用例）

- [ ] **Step 3: 针对性验证 /query 全链路**

Run: `uv run pytest tests/unit/engine/test_combined_searcher.py tests/unit/engine/test_keyword_searcher.py::TestSearchIn tests/unit/engine/test_index_manager.py::TestCombinedSearch tests/unit/plugins/test_meme_query.py tests/unit/plugins/test_search_utils.py::TestExecuteCombinedSearch -v`
Expected: 全部 PASS

- [ ] **Step 4: Checkpoint**

向用户报告全部 Task 完成、测试通过，等待用户审核与提交决策。不自行 commit。

---

## Self-Review

**1. Spec 覆盖：**
- 语法 `#tag`/`@speaker` → Task 6 `_parse_args` + 测试 TestParseArgs ✓
- 多 tag AND → Task 2 `_filter_entries` + TestTagFilter.test_multiple_tags_and ✓
- speaker 精确相等/区分大小写 → Task 2 + test_speaker_case_sensitive ✓
- 多 speaker OR → Task 2 + test_multiple_speakers_or ✓
- `/query`/`/q` 属组 B → Task 6 on_command + 插件无私聊限制 ✓
- 纯过滤 entry_id 升序 → Task 2 + test_sorted_by_entry_id_ascending ✓
- 纯过滤不展示相似度 → Task 6 QUERY_FILTER_OPTIONS + test_no_keyword_uses_filter_options ✓
- 有关键词展示相似度 score → Task 6 QUERY_KW_OPTIONS + test_keyword_uses_kw_options ✓
- tag 区分大小写 → Task 2 + test_tag_case_sensitive ✓
- 不支持检索无 speaker → Task 2 test_speaker_none_not_matched ✓
- 普通文本兜底不改 → 无任务触碰 meme_plain_text.py ✓
- 分页复用 → Task 6 复用 handle_got_selection + PresentOptions next_trigger="n" ✓
- 方案 A 新建 CombinedSearcher → Task 2 ✓
- search_in limit 截断 → Task 1 + test_search_in_respects_limit ✓
- import 链 → Task 4 ✓
- 边界（空字符串、speaker=None、重复 tag）→ Task 2 测试覆盖 ✓
- 文档同步 → Task 7 ✓

**2. 占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码；命令含 expected 输出。✓

**3. 类型一致性：**
- `CombinedSearcher.search(keyword, speakers, tags)` 签名在 Task 2 定义，Task 3 `IndexManager.search_combined(keyword, speakers, tags)` 与 Task 5 `execute_combined_search(keyword, speakers, tags)` 调用一致 ✓
- `search_in(entries, keyword)` 在 Task 1 定义，Task 2 调用一致 ✓
- `QUERY_KW_OPTIONS` / `QUERY_FILTER_OPTIONS` 在 Task 6 定义并在 got_selection 复用 ✓
- `_combined_searcher` 属性名在 Task 3/4 一致 ✓

无问题。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-query-command.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 Task 派发独立 subagent 实现，Task 间审核，快速迭代。

**2. Inline Execution** - 在当前会话按 Task 顺序执行，批量推进 + 检查点审核。

Which approach?
