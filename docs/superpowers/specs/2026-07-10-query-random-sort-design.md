# /query 随机排序设计

> 日期：2026-07-10
> 状态：待用户审阅
> 范围：仅 `/query` 命令

## 1. 背景与目标

当前 `/query`（`CombinedSearcher.search`）的排序行为：

- **无关键词（纯过滤）**：包装 `similarity=0.0`，按 `entry_id` 升序返回。
- **有关键词**：委托 `KeywordSearcher.search_in`，按相似度降序返回；同分之间按条目读出顺序（未打乱）。

目标：为 `/query` 增加随机性

- **无关键词**：对最终结果随机排序。
- **有关键词**：对「每组相似度相同的结果」组内随机排序，组间仍按相似度降序。

## 2. 关键决策（已与用户确认）

| 决策项 | 选择 | 理由 |
|---|---|---|
| 随机范围 | 仅 `/query` | `/search` 与纯文本兜底走 `KeywordSearcher.search`，不经过 `CombinedSearcher`，故不受影响；与用户原话「query 命令」一致 |
| 分组粒度 | 按真实 `similarity` 浮点值精确相等 | 精确子串命中全 100.0 归一组；模糊匹配 LCS 比值完全相同才同组；实现简单 |
| 随机源 | 模块级 `random`（方案 A） | 与 `RandomSearcher` 先例一致，不改构造器签名，改动最小 |
| 翻页稳定性 | 一次 `/query` 调用洗牌一次 | `dispatch_search_results` 冻结 `all_results`，翻页切片复用；每页重洗会导致跨页重复/丢失，不合理 |
| groupby 依赖 | 直接 `groupby`，不额外排序 | `search_in` 契约保证返回「降序、同分相邻」；用户确认设计正确 |

## 3. 架构与落点

仅修改 `bot/engine/combined_searcher.py` 的 `CombinedSearcher.search`。

- 不修改 `KeywordSearcher`（`/search`、纯文本兜底不受影响）
- 不修改 `_search_utils`（展示与翻页逻辑不变）

数据流：

```
/query -> execute_combined_search -> index_manager.search_combined（持读锁）
       -> CombinedSearcher.search（随机洗牌）
       -> dispatch_search_results（冻结 state["all_results"]）
       -> 翻页切片（顺序稳定）
```

## 4. 组件改动

### 4.1 导入

`bot/engine/combined_searcher.py` 顶部新增：

```python
import random
from itertools import groupby
```

### 4.2 无关键词分支

原：

```python
results.sort(key=lambda r: r.entry_id)
```

改为：

```python
random.shuffle(results)
```

（`results` 仍由 `filtered.values()` 构造，全量打乱，`similarity` 仍 `0.0`。）

### 4.3 有关键词分支

原：

```python
if keyword:
    results = self._keyword_searcher.search_in(filtered, keyword)
    logger.info(...)
    return results
```

改为在 `return` 前对结果做组内随机：

```python
if keyword:
    results = self._keyword_searcher.search_in(filtered, keyword)
    results = _shuffle_within_similarity_groups(results)
    logger.info(...)
    return results
```

### 4.4 新增模块级私有函数

```python
def _shuffle_within_similarity_groups(results: list[SearchResult]) -> list[SearchResult]:
    """对相似度相同的结果组内随机排序，组间保持相似度降序。

    依赖入参已按 similarity 降序排列（search_in 契约保证，同分相邻），
    因此 itertools.groupby 的「连续相同 key 合并」即可正确分组。
    """
    shuffled: list[SearchResult] = []
    for _sim, group in groupby(results, key=lambda r: r.similarity):
        g = list(group)
        random.shuffle(g)
        shuffled.extend(g)
    return shuffled
```

## 5. 数据流与翻页稳定性

`dispatch_search_results` 把全量 `results` 存入 `state["all_results"]`，`page_index` / `total_pages` 基于该冻结列表切片，「n」翻页取下一片。因此随机洗牌只在 `search` 时做一次，整个翻页会话内顺序稳定。每次新的 `/query` 调用重新洗牌（达到「增加随机性」目的）。

## 6. 错误处理

无新增异常路径。

- `random.shuffle` / `groupby` 对空列表安全；`search` 已在空 `entries` / 空 `filtered` 时 `return []`。
- 空结果、单结果分支不受影响（`dispatch_search_results` 的 `len==0` / `len==1` 分支不变）。

## 7. 测试

文件：`tests/unit/engine/test_combined_searcher.py`

改动：

- `TestNoKeywordBranch.test_sorted_by_entry_id_ascending`：原断言 `[r.entry_id for r in results] == [1, 2, 3]` 必然失败。改为断言「结果集合 == `{1, 2, 3}` 且为排列」（如 `{r.entry_id for r in results} == {1, 2, 3}`，且 `len(results) == 3`）。
- `TestNoKeywordBranch.test_similarity_zero`：不受影响（仍全 `0.0`）。
- `TestWithKeyword`：新增/调整
  - 组间严格 `similarity` 降序（断言相似度列表单调不增）。
  - 同分组内为原始成员的排列（断言每组 `entry_id` 集合 == 预期集合）。
  - 精确子串全 `100.0` 单组被洗牌（`monkeypatch` `random.shuffle` 强制逆序，断言顺序反转）。
- 确定性断言：`monkeypatch.setattr("bot.engine.combined_searcher.random.shuffle", ...)` 强制已知顺序（如 `lambda seq: seq.reverse()`）。
- 多相似度组场景需用模糊匹配关键词构造（精确子串命中全 `100.0` 仅单组，无法验证组间降序）。

不改动（已核查安全）：

- `test_meme_query.py`（不直接断言顺序）。
- `tests/unit/engine/test_index_manager.py::TestCombinedSearch`：`with_keyword`/`pure_filter` 单结果、`tag_filter` 集合断言 `{1}`、`empty_index` 空、`not_injected` 异常，均不被随机化打破。
- `tests/unit/plugins/test_search_utils.py` 的 `execute_combined_search` 测试用 `AsyncMock` mock 掉 `search_combined` 返回固定列表，只测 dispatch、不触发 `CombinedSearcher` 随机化。
- 其余用集合断言的用例不受影响。

## 8. 文档同步

实现后逐个更新（遵循 `CLAUDE.md` 文档同步规则）：

- `docs/PRD.md`
  - §3.2 流程：无关键词分支「按 `entry_id` 升序」->「随机排序」；有关键词分支补充「同相似度组内随机」。
  - §3.2 交互约束：更新排序说明。
  - §5 边界：`/query` 相关排序行（如「`/query` 仅 `@speaker` 或 `#tag` -> 按 `entry_id` 升序」->「随机排序」）。
- `CONTEXT.md`
  - `CombinedSearcher` 条目：无关键词随机、有关键词组内随机。
  - `/query` 条目：排序说明。
- `docs/api/API.md`
  - `combined_searcher.md`：`search()` 返回顺序说明。
  - `index_manager.md`：`search_combined` 返回顺序说明。
  - `meme_query.md`：如需补充排序行为。
- `README.md`：`/query` 示例不涉及具体顺序，无需改。

## 9. 不做的事（YAGNI）

- 不改 `KeywordSearcher`（`/search`、纯文本兜底保持确定性）。
- 不在展示层 `_search_utils` 加 `randomize` 开关。
- 不注入 seedable `rng`（用模块级 `random`，贴合 `RandomSearcher` 先例）。
- 不对 `similarity` 分桶/取整分组。
- 不每页重洗。

## 10. 提交策略

当前在 `main` 分支，按 `CLAUDE.md` 禁止自行 `git add` / `git commit`。spec 写入后由用户审阅；实现完成、测试与文档同步后，提交由用户审核执行。
