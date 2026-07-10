# /query 随机排序 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/query` 命令增加随机性——无关键词时结果随机排序，有关键词时同相似度组内随机排序（组间仍按相似度降序）。

**Architecture:** 仅修改 `bot/engine/combined_searcher.py` 的 `CombinedSearcher.search`。无关键词分支把 `results.sort(key=entry_id)` 换成 `random.shuffle(results)`；有关键词分支在 `KeywordSearcher.search_in` 返回后用新增模块级私有函数 `_shuffle_within_similarity_groups`（`itertools.groupby` 按 `similarity` 分组、组内 `random.shuffle`）做组内随机。`/search` 与纯文本兜底走 `KeywordSearcher.search`，不经过 `CombinedSearcher`，故不受影响。翻页稳定性由现有 `dispatch_search_results` 冻结 `state["all_results"]` 天然保证——一次 `/query` 洗牌一次，翻页切片复用。

**Tech Stack:** Python 3.12、NoneBot2、pytest（含 `monkeypatch` fixture）、标准库 `random` + `itertools.groupby`。

> **分支约束（CLAUDE.md）：** 禁止在 `main` 分支自行 `git add`/`git commit`/`git merge`。本计划所有提交都在 Task 1 创建的 `feat/query-random-sort` 分支上进行；最终合并回 `main` 须由用户审核（PR 或手动）。

**Spec：** `docs/superpowers/specs/2026-07-10-query-random-sort-design.md`

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `bot/engine/combined_searcher.py` | 组合检索器，过滤 + 关键词匹配 + 排序 | 修改：加 `import random`/`from itertools import groupby`；无关键词分支改 `random.shuffle`；有关键词分支调 `_shuffle_within_similarity_groups`；新增该模块级私有函数 |
| `tests/unit/engine/test_combined_searcher.py` | CombinedSearcher 单元测试 | 修改：替换 `test_sorted_by_entry_id_ascending` 为两个新测试；新增 `TestShuffleWithinSimilarityGroups` 类与一个有关键词集成测试 |
| `docs/PRD.md` | 产品需求 | 修改：§3.2 交互约束加排序说明；§5 边界 `/query` 纯过滤行 |
| `CONTEXT.md` | 术语表 | 修改：`CombinedSearcher` 条目、`/query` 条目 |
| `docs/api/API.md` | 接口文档 | 修改：`combined_searcher.md`、`index_manager.md`、`meme_query.md` |

不改动：`keyword_searcher.py`、`_search_utils.py`、`meme_query.py`、`test_index_manager.py`、`test_meme_query.py`、`test_search_utils.py`（已核查安全）。

---

## Task 1: 创建特性分支并确认基线绿色

**Files:**
- 无文件改动（仅分支与基线验证）

- [ ] **Step 1: 确认当前在 main 且工作区干净**

Run:
```bash
git status --short && git branch --show-current
```
Expected: 无输出（干净）且 `main`。若有未提交改动，先与用户确认处理方式。

- [ ] **Step 2: 创建并切换到特性分支**

Run:
```bash
git checkout -b feat/query-random-sort
```
Expected: `Switched to a new branch 'feat/query-random-sort'`

- [ ] **Step 3: 运行 combined_searcher 与 index_manager 单元测试，确认基线绿色**

Run:
```bash
uv run pytest tests/unit/engine/test_combined_searcher.py tests/unit/engine/test_index_manager.py -q
```
Expected: 全部通过（`test_sorted_by_entry_id_ascending` 此刻仍应通过——它在 Task 2 才被改写）。

---

## Task 2: 无关键词分支随机排序（TDD）

**Files:**
- Modify: `bot/engine/combined_searcher.py`（导入 + 无关键词分支）
- Test: `tests/unit/engine/test_combined_searcher.py`

- [ ] **Step 1: 改写失败测试**

在 `tests/unit/engine/test_combined_searcher.py` 的 `TestNoKeywordBranch` 类中，把：

```python
    def test_sorted_by_entry_id_ascending(self, combined: CombinedSearcher) -> None:
        results = combined.search(None, ["小明", "小红"], [])
        assert [r.entry_id for r in results] == [1, 2, 3]
```

替换为以下两个测试（保留类内其余方法不变）：

```python
    def test_no_keyword_returns_all_entries(self, combined: CombinedSearcher) -> None:
        """无关键词：返回全部过滤命中条目（顺序随机，仅校验集合与数量）。"""
        results = combined.search(None, ["小明", "小红"], [])
        assert {r.entry_id for r in results} == {1, 2, 3}
        assert len(results) == 3

    def test_no_keyword_shuffles_via_random(
        self, combined: CombinedSearcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无关键词：确实调用 random.shuffle 打乱（monkeypatch 反转验证）。"""
        monkeypatch.setattr(
            "bot.engine.combined_searcher.random.shuffle",
            lambda seq: seq.reverse(),
        )
        results = combined.search(None, ["小明", "小红"], [])
        # filtered.values() 迭代序为 [1,2,3]，反转后 [3,2,1]
        assert [r.entry_id for r in results] == [3, 2, 1]
        assert all(r.similarity == 0.0 for r in results)
```

- [ ] **Step 2: 运行测试，确认新测试失败**

Run:
```bash
uv run pytest tests/unit/engine/test_combined_searcher.py::TestNoKeywordBranch -v
```
Expected: `test_no_keyword_shuffles_via_random` FAIL（当前实现按 `entry_id` 升序返回 `[1,2,3]`，monkeypatch 不影响 `sort`，断言 `[3,2,1]` 失败；`AttributeError: module 'bot.engine.combined_searcher' has no attribute 'random'` 也可能出现，因为尚未 `import random`——两种失败均可接受）。`test_no_keyword_returns_all_entries` 应通过（集合断言与顺序无关）。

- [ ] **Step 3: 实现——加 `import random`，无关键词分支改 `random.shuffle`**

在 `bot/engine/combined_searcher.py` 顶部，把：

```python
import logging

from .keyword_searcher import KeywordSearcher
```

改为：

```python
import logging
import random

from .keyword_searcher import KeywordSearcher
```

然后在 `CombinedSearcher.search` 的无关键词分支，把：

```python
        results.sort(key=lambda r: r.entry_id)
```

改为：

```python
        random.shuffle(results)
```

- [ ] **Step 4: 运行测试，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_combined_searcher.py -v
```
Expected: 全部通过，包括 `test_no_keyword_shuffles_via_random`（`random.shuffle` 被反转后返回 `[3,2,1]`）。

- [ ] **Step 5: 提交**

```bash
git add bot/engine/combined_searcher.py tests/unit/engine/test_combined_searcher.py
git commit -m "feat(engine): /query 无关键词结果随机排序"
```

---

## Task 3: 有关键词分支同相似度组内随机排序（TDD）

**Files:**
- Modify: `bot/engine/combined_searcher.py`（加 `groupby` 导入 + 新增私有函数 + 有关键词分支调用）
- Test: `tests/unit/engine/test_combined_searcher.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_combined_searcher.py` 末尾新增一个测试类（私有函数白盒单测）：

```python
class TestShuffleWithinSimilarityGroups:
    """_shuffle_within_similarity_groups 白盒测试。"""

    def test_preserves_group_order_and_membership(self) -> None:
        from bot.engine.combined_searcher import _shuffle_within_similarity_groups

        results = [
            SearchResult(entry_id=1, image_path="a", text="t1", similarity=100.0),
            SearchResult(entry_id=2, image_path="b", text="t2", similarity=100.0),
            SearchResult(entry_id=3, image_path="c", text="t3", similarity=80.0),
            SearchResult(entry_id=4, image_path="d", text="t4", similarity=80.0),
            SearchResult(entry_id=5, image_path="e", text="t5", similarity=60.0),
        ]
        out = _shuffle_within_similarity_groups(results)
        # 组间仍按 similarity 降序
        assert [r.similarity for r in out] == [100.0, 100.0, 80.0, 80.0, 60.0]
        # 每组成员集合不变
        assert {r.entry_id for r in out[:2]} == {1, 2}
        assert {r.entry_id for r in out[2:4]} == {3, 4}
        assert out[4].entry_id == 5

    def test_randomizes_within_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bot.engine.combined_searcher import _shuffle_within_similarity_groups

        monkeypatch.setattr(
            "bot.engine.combined_searcher.random.shuffle",
            lambda seq: seq.reverse(),
        )
        results = [
            SearchResult(entry_id=1, image_path="a", text="t1", similarity=100.0),
            SearchResult(entry_id=2, image_path="b", text="t2", similarity=100.0),
            SearchResult(entry_id=3, image_path="c", text="t3", similarity=80.0),
            SearchResult(entry_id=4, image_path="d", text="t4", similarity=80.0),
        ]
        out = _shuffle_within_similarity_groups(results)
        # 每组反转：[2,1] + [4,3]
        assert [r.entry_id for r in out] == [2, 1, 4, 3]
```

并在 `TestWithKeyword` 类中新增一个集成测试（放在该类最后一个方法之后）：

```python
    def test_keyword_shuffles_within_same_similarity(
        self, combined: CombinedSearcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有关键词：同相似度组内随机（精确子串全 100.0 单组）。"""
        monkeypatch.setattr(
            "bot.engine.combined_searcher.random.shuffle",
            lambda seq: seq.reverse(),
        )
        results = combined.search("加班", ["小明"], [])
        # speaker 小明 -> {1,3}；精确子串「加班」命中二者，全 100.0 单组，反转后 [3,1]
        assert [r.entry_id for r in results] == [3, 1]
        assert all(r.similarity == 100.0 for r in results)
```

- [ ] **Step 2: 运行测试，确认新测试失败**

Run:
```bash
uv run pytest tests/unit/engine/test_combined_searcher.py::TestShuffleWithinSimilarityGroups tests/unit/engine/test_combined_searcher.py::TestWithKeyword::test_keyword_shuffles_within_same_similarity -v
```
Expected: FAIL——`_shuffle_within_similarity_groups` 尚未定义（`ImportError`）；集成测试因有关键词分支尚未洗牌、返回 `[1,3]` 而断言 `[3,1]` 失败。

- [ ] **Step 3: 实现——加 `groupby` 导入、新增私有函数、有关键词分支调用**

在 `bot/engine/combined_searcher.py` 顶部导入区，把：

```python
import logging
import random

from .keyword_searcher import KeywordSearcher
```

改为：

```python
import logging
import random
from itertools import groupby

from .keyword_searcher import KeywordSearcher
```

在文件末尾（`CombinedSearcher` 类定义之后）新增模块级私有函数：

```python
def _shuffle_within_similarity_groups(
    results: list[SearchResult],
) -> list[SearchResult]:
    """对相似度相同的结果组内随机排序，组间保持相似度降序。

    依赖入参已按 similarity 降序排列（search_in 契约保证，同分相邻），
    因此 itertools.groupby 的「连续相同 key 合并」即可正确分组。

    Args:
        results: 已按 similarity 降序排列的搜索结果。

    Returns:
        组间顺序不变、组内随机打乱的新列表。
    """
    shuffled: list[SearchResult] = []
    for _sim, group in groupby(results, key=lambda r: r.similarity):
        group_list = list(group)
        random.shuffle(group_list)
        shuffled.extend(group_list)
    return shuffled
```

在 `CombinedSearcher.search` 的有关键词分支，把：

```python
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
```

改为（仅插入一行 `results = _shuffle_within_similarity_groups(results)`）：

```python
        if keyword:
            results = self._keyword_searcher.search_in(filtered, keyword)
            results = _shuffle_within_similarity_groups(results)
            logger.info(
                "组合检索（含关键词）: keyword=%r, speakers=%r, tags=%r, 命中=%d",
                keyword,
                speakers,
                tags,
                len(results),
            )
            return results
```

- [ ] **Step 4: 运行测试，确认通过**

Run:
```bash
uv run pytest tests/unit/engine/test_combined_searcher.py -v
```
Expected: 全部通过，包括三个新增测试。

- [ ] **Step 5: 提交**

```bash
git add bot/engine/combined_searcher.py tests/unit/engine/test_combined_searcher.py
git commit -m "feat(engine): /query 有关键词同相似度组内随机排序"
```

---

## Task 4: 全量测试与语法检查

**Files:**
- 无文件改动（验证）

- [ ] **Step 1: 全量单元测试**

Run:
```bash
uv run pytest -q
```
Expected: 全部通过。重点关注 `test_combined_searcher.py`、`test_index_manager.py::TestCombinedSearch`、`test_meme_query.py`、`test_search_utils.py` 均绿。

- [ ] **Step 2: 语法检查**

Run:
```bash
uv run python -m compileall bot tests
```
Expected: 无 `SyntaxError`/`error`，退出码 0。

- [ ] **Step 3: 若有失败则修复后提交；全绿则无需提交**

若 Step 1/2 发现问题，修复后：
```bash
git add -A && git commit -m "fix(engine): 修复 /query 随机排序测试遗漏"
```
Expected（全绿时）：跳过本步。

---

## Task 5: 文档同步

**Files:**
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `docs/api/API.md`

> 文档提交为纯文档变更。测试已于 Task 4 通过，提交说明中注明。

- [ ] **Step 1: 更新 `docs/PRD.md` §3.2 交互约束**

在 `/query` 的 `#### 交互约束` 列表第一项之后插入一行排序说明。把：

```
- 有关键词时列表行展示关键词相似度百分比（score 0–100，同 `/search`）；无关键词纯过滤时不展示相似度（同 `/rand`）。
```

改为（在其后追加一行）：

```
- 有关键词时列表行展示关键词相似度百分比（score 0–100，同 `/search`）；无关键词纯过滤时不展示相似度（同 `/rand`）。
- 排序：无关键词时结果随机排序；有关键词时按相似度降序分组，同相似度组内随机排序（一次 `/query` 洗牌一次，翻页顺序稳定）。
```

- [ ] **Step 2: 更新 `docs/PRD.md` §5 边界表**

把：

```
| /query 仅 @speaker 或 #tag | 纯过滤，按 entry_id 升序返回，不展示相似度 |
```

改为：

```
| /query 仅 @speaker 或 #tag | 纯过滤，随机排序返回，不展示相似度 |
```

- [ ] **Step 3: 更新 `CONTEXT.md` 的 `CombinedSearcher` 条目**

把：

```
| **CombinedSearcher** | 组合搜索器，`bot/engine/combined_searcher.py`；先按 `speakers`(OR) + `tags`(AND) 过滤 `MetadataStore.get_all_entries` 子集，再委托 `KeywordSearcher.search_in` 在子集上跑关键词匹配；无关键词时包装 `similarity=0.0` 按 `entry_id` 升序；`IndexManager.search_combined` 持读锁调用；依赖 `MetadataStoreProvider` + `KeywordSearcher` |
```

改为：

```
| **CombinedSearcher** | 组合搜索器，`bot/engine/combined_searcher.py`；先按 `speakers`(OR) + `tags`(AND) 过滤 `MetadataStore.get_all_entries` 子集，再委托 `KeywordSearcher.search_in` 在子集上跑关键词匹配；无关键词时包装 `similarity=0.0` 随机排序，有关键词时同相似度组内随机排序（组间仍按相似度降序，由模块级 `_shuffle_within_similarity_groups` 用 `itertools.groupby` 实现）；`IndexManager.search_combined` 持读锁调用；依赖 `MetadataStoreProvider` + `KeywordSearcher` |
```

- [ ] **Step 4: 更新 `CONTEXT.md` 的 `/query` 条目**

把：

```
speaker 精确相等区分大小写、tag 区分大小写；有关键词时展示关键词相似度（score）、无关键词纯过滤时不展示相似度（按 entry_id 升序）；
```

改为：

```
speaker 精确相等区分大小写、tag 区分大小写；有关键词时展示关键词相似度（score，同相似度组内随机）、无关键词纯过滤时不展示相似度（随机排序）；
```

- [ ] **Step 5: 更新 `docs/api/API.md` 的 `combined_searcher.md` 段**

把：

```
    # 有关键词：在过滤子集上跑 KeywordSearcher.search_in，带 similarity 降序
    # 无关键词：包装 similarity=0.0，按 entry_id 升序
```

改为：

```
    # 有关键词：在过滤子集上跑 KeywordSearcher.search_in（相似度降序），同相似度组内随机排序
    # 无关键词：包装 similarity=0.0，随机排序
```

- [ ] **Step 6: 更新 `docs/api/API.md` 的 `index_manager.md` 中 `search_combined` 段**

把：

```
    # 持读锁调用 CombinedSearcher.search；空库返回 []；超时抛 asyncio.TimeoutError；
    # 未注入 CombinedSearcher 抛 RuntimeError
```

改为：

```
    # 持读锁调用 CombinedSearcher.search；空库返回 []；超时抛 asyncio.TimeoutError；
    # 未注入 CombinedSearcher 抛 RuntimeError
    # 排序：无关键词随机；有关键词同相似度组内随机（组间相似度降序）
```

- [ ] **Step 7: 更新 `docs/api/API.md` 的 `meme_query.md` 段**

把：

```
- 展示：有关键词用 `QUERY_KW_OPTIONS`(show_similarity=True, score, next_trigger="n")；无关键词用 `QUERY_FILTER_OPTIONS`(show_similarity=False, next_trigger="n")
```

改为：

```
- 展示：有关键词用 `QUERY_KW_OPTIONS`(show_similarity=True, score, next_trigger="n")；无关键词用 `QUERY_FILTER_OPTIONS`(show_similarity=False, next_trigger="n")
- 排序：无关键词随机；有关键词同相似度组内随机（组间相似度降序）；一次 /query 洗牌一次，翻页顺序稳定
```

- [ ] **Step 8: 提交文档**

```bash
git add docs/PRD.md CONTEXT.md docs/api/API.md
git commit -m "docs: 同步 /query 随机排序至 PRD/CONTEXT/API 文档（测试已于前置任务通过）"
```

---

## Task 6: 终检与合并准备

**Files:**
- 无文件改动（验证与汇总）

- [ ] **Step 1: 查看本分支相对 main 的全部改动**

Run:
```bash
git log main..feat/query-random-sort --oneline && git diff main...feat/query-random-sort --stat
```
Expected: 看到 Task 2/3/5 的三次提交，改动文件为 `combined_searcher.py`、`test_combined_searcher.py`、`docs/PRD.md`、`CONTEXT.md`、`docs/api/API.md`。

- [ ] **Step 2: 最终全量测试**

Run:
```bash
uv run pytest -q && uv run python -m compileall bot tests
```
Expected: 全绿，编译无错。

- [ ] **Step 3: 汇总并等待用户审核合并**

向用户汇报：分支 `feat/query-random-sort` 已完成，含 N 次提交，全量测试通过；按 CLAUDE.md 不自行合并到 `main`，等待用户审核（PR 或手动 `git merge`）。

---

## Self-Review

**1. Spec coverage：**
- 无关键词随机排序 -> Task 2 ✓
- 有关键词同相似度组内随机（组间降序） -> Task 3 ✓
- 落点仅 `CombinedSearcher.search` -> Task 2/3 ✓
- 翻页稳定性（不重排不重搜） -> 设计依赖现有 `handle_got_selection`，无需改动，Task 4 验证不破坏 ✓
- 模块级 `random`（方案 A，不注入 rng） -> Task 2/3 用 `random.shuffle` ✓
- 测试：改 `test_sorted_by_entry_id_ascending`、新增组内随机测试、monkeypatch 确定性 -> Task 2/3 ✓
- 文档同步 PRD/CONTEXT/API -> Task 5 ✓
- 分支约束（不在 main 提交） -> Task 1 + 顶部说明 ✓

**2. Placeholder scan：** 无 TBD/TODO；每个改动步骤都给了精确 old/new 代码块与运行命令。✓

**3. Type consistency：** `_shuffle_within_similarity_groups(results: list[SearchResult]) -> list[SearchResult]` 在 Task 3 定义与调用一致；`SearchResult` 已在测试文件导入（`from bot.engine.metadata_store import MemeEntry` 之外，测试文件首部已 `from bot.engine.combined_searcher import CombinedSearcher`——需确认 `SearchResult` 可用）。⚠️ 检查：测试文件当前未直接导入 `SearchResult`，Task 3 新测试用到 `SearchResult(...)`。

**修正（inline）：** Task 3 Step 1 的新测试引用了 `SearchResult`，须确保测试文件已导入。当前 `test_combined_searcher.py` 仅导入 `CombinedSearcher`、`KeywordSearcher`、`MemeEntry`。在 Task 3 Step 1 之前，需补充导入。修订如下——在 Task 3 Step 1 的代码块前追加一条导入步骤（已并入下方修订）。

> **Task 3 Step 1 修订：** 先在 `tests/unit/engine/test_combined_searcher.py` 顶部导入区，把：
> ```python
> from bot.engine.combined_searcher import CombinedSearcher
> from bot.engine.keyword_searcher import KeywordSearcher
> from bot.engine.metadata_store import MemeEntry
> ```
> 改为：
> ```python
> from bot.engine.combined_searcher import CombinedSearcher
> from bot.engine.keyword_searcher import KeywordSearcher
> from bot.engine.metadata_store import MemeEntry
> from bot.engine.types import SearchResult
> ```
> 再新增 `TestShuffleWithinSimilarityGroups` 类与 `test_keyword_shuffles_within_same_similarity` 方法（代码同上）。

✓ 修订完成。
