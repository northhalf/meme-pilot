# /sim 相似度展示 + /info 前 10 + 搜索分页 设计

> 日期：2026-07-08
> 状态：待实现（spec 待用户审阅）
> 关联文档：`docs/PRD.md`、`CONTEXT.md`、`README.md`、`docs/api/API.md`

## 1. 概述

本次实现三个增强：

1. **`/sim` 回复信息中包含相似度**（百分比）。
2. **`/info` 的 speaker 排行从"前 3"改为"前 10"**。
3. **`/search`、`/sim`、兜底搜索（普通文本）的多结果列表支持分页**，用户回复 `n` 查看下一页。

`/rand` 保持现状（1 页 10 条 + "0 换一批"），不参与分页与相似度展示。

## 2. 决策摘要

| # | 决策 | 选定 |
|---|------|------|
| 1 | `/sim` 相似度展示位置与格式 | 仅列表行，百分比 |
| 2 | 相似度展示范围 | `/sim` + `/search` + 兜底；`/rand` 不展示 |
| 3 | 下一页触发词 | `n` |
| 4 | 每页条数与结果集上限 | 10 条/页，结果集无上限 |
| 5 | 末页行为 | 提示"没有更多结果了"，保持当前页 |
| 6 | 触发词与页大小配置化 | 硬编码常量 |
| 7 | 分页架构 | 方案 A：一次预取 + 页内切片 |

## 3. 方案选型（分页架构）

- **方案 A（选定）**：搜索时一次取回完整结果集（关键词全量匹配、语义全库召回），存入 `matcher.state`，展示层按页切片。翻页零 IO、结果稳定、展示层简单。
- **方案 B（否决）**：首搜取 10 条，翻页时带 `offset` 再取。需给 searcher / vector_store 加 offset 参数；翻页有 IO 延迟；跨次翻页难保持快照一致；复杂度高。

选 A 的理由：简单、快、结果稳定，与"结果集无上限"决策契合。

## 4. 改动范围

| 文件 | 改动 |
|------|------|
| `bot/plugins/_search_utils.py` | 核心：分页 + 相似度渲染；新增 `PresentOptions`、常量、`_similarity_percent` |
| `bot/plugins/meme_search.py`、`meme_sim.py`、`meme_plain_text.py` | 传入对应 `PresentOptions` |
| `bot/plugins/meme_rand.py` | 适配新签名，行为不变（默认 `PresentOptions`） |
| `bot/plugins/meme_info.py`、`bot/engine/index_manager.py` | speaker 排行前 3 -> 前 10 |
| `bot/engine/keyword_searcher.py` | `search` 返回全量匹配 |
| `bot/engine/semantic_searcher.py` | `limit=None` 全库召回；协议依赖改 `MetadataStoreProvider` |
| `bot/engine/index_manager.py` | `semantic_search` `limit` 默认 `None` |
| `bot/engine/vector_store.py` | `query` `n_results=None` 取 `count` |
| 文档 | PRD、CONTEXT、README、docs/api/API.md |

## 5. 展示层设计

### 5.1 常量（硬编码在 `_search_utils.py`）

```python
PAGE_SIZE = 10
NEXT_PAGE_TRIGGER = "n"
```

### 5.2 PresentOptions

```python
@dataclass(frozen=True)
class PresentOptions:
    show_similarity: bool = False
    similarity_scale: Literal["ratio", "score"] = "score"  # ratio=0–1，score=0–100
    next_trigger: str | None = None  # None = 不支持翻页（/rand）
    page_size: int = PAGE_SIZE
```

默认值对应 `/rand` 行为（不展示相似度、不翻页）。

### 5.3 量纲归一

```python
def _similarity_percent(similarity: float, scale: str) -> int:
    raw = similarity * 100 if scale == "ratio" else similarity
    return max(0, min(100, round(raw)))
```

仅展示层归一，不改 `SearchResult.similarity` 存储语义。`ratio` 0.82->82、1.0->100；`score` 82->82、100->100。统一渲染 `82%`。`clamp` 到 `[0,100]` 防浮点越界（chroma cosine distance 浮点可能导致 similarity 略 >1）。

### 5.4 共享函数签名

- `format_metadata_line(...)` **不改**（选中后元数据行不含相似度，符合决策 1）。
- `present_candidates(..., *, options=PresentOptions(), page_index=0, total_pages=1, prompt_suffix="")`：接收"当前页切片"；列表行末尾按 `options` 追加相似度；仅当 `page_index+1 < total_pages` 追加"回复 n 看下一页"。
- `dispatch_search_results(..., *, options=PresentOptions(), prompt_suffix="")`：空结果 / 单结果不变；多结果存 `state["all_results"]` + `state["page_index"]`，切第 1 页调 `present_candidates`。
- `handle_got_selection(..., *, options=PresentOptions())`：`next_trigger` 命中翻页；选中走 `format_metadata_line`。
- `execute_search(..., *, options=PresentOptions())`：透传 `options`。
- `resolve_selection`、`got_intercept_bypass` 不变。

### 5.5 各命令传参

| 命令 | show_similarity | similarity_scale | next_trigger |
|------|-----------------|------------------|--------------|
| `/sim` | True | ratio | "n" |
| `/search`、兜底 | True | score | "n" |
| `/rand` | False（默认） | - | None |

## 6. 需求1：相似度展示

### 6.1 渲染样例

`/sim`（ratio，语义 0–1）：
```
找到多个匹配的表情包，请选择：
  1. 加班到凌晨三点的我 -- 23, 小明, 82%
  2. 心累的打工人 -- 45, 无, 76%
  回复编号即可 (1-10)
  回复 n 看下一页
你: 1
Bot: (图片)
Bot: 23, 小明          ← 选中后不含相似度
```

`/search`、兜底（score，关键词 0–100）：精确子串命中 `100.0` -> `100%`，模糊 LCS 命中按分显示如 `76%`。

`/rand`：列表行无相似度，末尾"回复 0 换一批"，无"回复 n 看下一页"。

### 6.2 边界

- `score` `100.0` -> `100%`、`ratio` `1.0` -> `100%`。
- `/rand` 的 `similarity=0.0` 不展示（`show_similarity=False`）。
- 关键词精确子串命中全为 `100%`，列表均显示 `100%`（符合事实）。

## 7. 需求2：/info 前 10

- `index_manager.py` `info()`：`speaker_ranking` 切片 `[:3]` -> `[:10]`。
- `meme_info.py`：文本 `"排行（前 3）："` -> `"排行（前 10）："`。
- `IndexInfo.speaker_ranking` 类型不变（`list[tuple[str | None, int]]`），仅数量上限变 10；speaker 不足 10 个时显示全部（`[:10]` 自然成立）。

## 8. 需求3：分页机制

### 8.1 搜索层改造（全量返回）

- **`KeywordSearcher.search`**：`_search_exact_substring` 与 `_search_fuzzy_lcs` 返回全量匹配后，`search` 末尾仍执行 `results[:self._limit]`；`limit` 默认改为 `None`（`list[:None]` 等价全量切片），即默认返回全部匹配，与"结果集无上限"决策一致；`limit` 仍可作为可选上限。契约：返回 `limit` 条以内的全部匹配，`None` = 全量。
- **`SemanticSearcher.search_semantic`**：`limit` 语义改为"召回上限"，`None` = 全库。协议依赖从 `MetadataEntryProvider`（`get_entry`）切到 `MetadataStoreProvider`（`get_all_entries`），一次取全库 dict 按 `hit.entry_id` 映射，**避免逐条 `get_entry`**（全库召回时几百次 sqlite 查询）。
- **`VectorStore.query`**：`n_results = min(limit, count)`，`limit=None` 时取 `count`（全库召回）。
- **`IndexManager.search` / `semantic_search`**：`semantic_search(description, limit=None)` 默认全库；`search` 透传全量。

### 8.2 分页状态流（方案 A）

1. **首搜**：`dispatch_search_results(results, options)` 收到全量 `results`。
2. **多结果**：`state["all_results"]=results`、`state["page_index"]=0`、`state["total_pages"]=ceil(len(results)/page_size)`；切第 1 页 `results[0:page_size]`；调 `present_candidates(第1页, options, page_index=0, total_pages=state["total_pages"])`。
3. **`present_candidates`**：渲染当前页列表行（按 `options` 追加相似度）+ `回复编号即可 (1-N)`（N=当前页条数）+ **仅当 `page_index+1 < total_pages`** 追加 `回复 n 看下一页` + `prompt_suffix`；`state["candidates"]=当前页切片`；创建/重置 selection 会话（重置 `SESSION_EXPIRE_TIMEOUT`）。
4. **`got` -> `handle_got_selection(options)`**：
   - `got_intercept_bypass`（`/help`、`/cancel` 旁路）
   - 检查 selection 有效
   - 若 `options.next_trigger` 且输入 == `"n"`：
     - `(page_index+1)*page_size < len(all_results)` -> `page_index+=1`、`state["page_index"]` 更新、切新页、`remove_selection` + `present_candidates(新页, options, page_index, total_pages=state["total_pages"])`（重置超时）
     - 否则 -> `matcher.reject("没有更多结果了")`（保持当前页、selection 不变、超时继续）
   - 否则：`resolve_selection(当前页 candidates, 输入)` -> 发图 + `format_metadata_line`（不含相似度）

### 8.3 约束

- **编号语义**：页内 `1-N`（每页重置），`resolve_selection` 用 `state["candidates"]`（当前页切片）。
- **末页**：`reject` 提示"没有更多结果了"，当前页保留可继续选编号。
- **超时**：每次翻页 `present_candidates` 重建 selection，重置 `SESSION_EXPIRE_TIMEOUT`；用户不会因翻页累积过期。
- **会话互斥**：分页期间 chat session 仍 active，新 `/search`/`/ai`/`/add` 被拒；`/cancel` 可取消、`/help` 可旁路（与现状一致）。
- **`/rand` 边界**：`next_trigger=None`，`got_rand_selection` 仍自处理"0 换一批"、不调 `handle_got_selection`；列表无相似度、无"回复 n"。分页逻辑完全不影响 `/rand`。

### 8.4 性能与风险

- 关键词全量：LCS C++ 实现，几千条 `<50ms`（PRD 4.1 已注）。
- 语义全库召回：`n_results=count`，大库 HNSW 可能退化；批量 `get_all_entries` 一次读。表情包库量级（几百~几千）可接受；超大规模库未来可再加回上限（此处注明）。
- 内存：`all_results` 存全量 `SearchResult`，几千条 `<1MB`。
- 读锁：首搜持读锁取快照；翻页用 `state` 缓存，不重复持锁、结果稳定。

## 9. 文档同步清单（CLAUDE.md 强制）

### PRD.md
- 3.1 关键词搜索：多结果列表行含相似度百分比；新增"回复 n 看下一页"分页交互。
- 3.2 `/sim`：列表行含相似度。
- 3.5 `/info`：speaker 排行"前 3" -> "前 10"。
- 交互约束 / 边界表：分页触发词 `n`、末页"没有更多结果了"保持当前页、翻页重置超时。

### CONTEXT.md
- `/info`、`/sim`、`/search` 术语更新；`SemanticSearcher` 协议依赖改 `MetadataStoreProvider`；新增 `PAGE_SIZE`/`NEXT_PAGE_TRIGGER`/`PresentOptions` 术语。

### README.md
- `/info` 示例"前 3" -> "前 10"；`/sim`、`/search` 示例含相似度与"回复 n 看下一页"。

### docs/api/API.md
- `IndexInfo.speaker_ranking` 上限 3 -> 10。
- `_search_utils`：`present_candidates`/`dispatch_search_results`/`handle_got_selection`/`execute_search` 新增 `options` 参数；新增 `PresentOptions`、`PAGE_SIZE`、`NEXT_PAGE_TRIGGER`、`_similarity_percent`。
- `KeywordSearcher.search` 契约：返回 `limit` 条以内匹配，`limit` 默认 `None` = 全量。
- `SemanticSearcher`：`limit=None` 全库、协议依赖改 `MetadataStoreProvider`。
- `IndexManager.semantic_search` `limit` 默认 `None`；`VectorStore.query` `n_results=None` 全库。
- `meme_sim`/`meme_search`/`meme_plain_text`/`meme_info` 描述更新。

## 10. 测试计划

### `tests/unit/plugins/test_search_utils.py`（已有，扩展）
- `_similarity_percent`：`ratio` 0.82->82、1.0->100；`score` 82->82、100->100；越界 clamp。
- `present_candidates`：页大小切片、`total_pages` 控制"回复 n"提示出现/隐藏、相似度渲染（ratio/score）、`/rand` 默认无相似度无翻页提示。
- `dispatch_search_results`：多结果存 `state["all_results"]`/`state["page_index"]`、切第 1 页。
- `handle_got_selection`：`n` 翻到下一页、末页 `reject("没有更多结果了")`、选中发图 + 元数据行（不含相似度）。
- `resolve_selection`：页内 `1-N` 编号。

### `tests/unit/engine/`
- `KeywordSearcher.search` 全量返回（精确子串 + 模糊均不截断）。
- `SemanticSearcher` 批量元数据映射、`limit=None` 全库召回、metadata 缺失跳过。

### `tests/unit/plugins/test_meme_info.py`
- `speaker_ranking` 截断到 10、不足 10 全显示。

### 检查命令
- `uv run pytest`
- `uv run python -m compileall bot tests`

## 11. 实现备注

- 遵循 `CLAUDE.md`：不在 main 自行 `git add`/`commit`；每实现一个模块后同步 `docs/api/API.md`。
- Python 3.12，Google 风格中文 docstring，类型标注，保持现有中文注释与用户提示风格。
- `PresentOptions` 默认值确保 `/rand` 行为不变，降低回归风险。
- 仅文档变更时在提交说明注明"仅文档变更，未运行测试"。
