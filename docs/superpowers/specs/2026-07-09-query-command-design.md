# /query 组合检索命令 - 设计文档

> 日期：2026-07-09
> 状态：设计已认可，待用户审阅 spec
> 流程：brainstorming（设计） -> 用户审阅 -> writing-plans（实现计划） -> 实现

## 1. 背景与目标

MemePilot 现有检索能力为单维度：`/search`（关键词）、`/rand`（随机）、`/sim`（语义）、`/ai`（AI 描述匹配）。用户无法按「说话人」或「标签」筛选表情包，也无法将关键词与这两类元数据组合检索。

本设计新增一条命令 `/query`（短命令 `/q`），用 `#tag` / `@speaker` 前缀区分维度，剩余 token 作为关键词，支持关键词、speaker、tags 的**单独或组合**检索。

## 2. 已确认决策

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 参数语法 | `#tag` 标记 tag（可多个），`@speaker` 标记 speaker（可多个），其余 token 为关键词 |
| 2 | 多 tag 语义 | AND（图片必须同时拥有全部指定 tag） |
| 3 | speaker 匹配 | 精确相等（`==`，区分大小写） |
| 4 | 多 speaker 语义 | OR（任一命中即返回） |
| 5 | 命令名/短命令 | `/query` / `/q` |
| 6 | 权限属组 | 组 B（私聊 + 群聊 @bot），与 `/search` `/rand` `/sim` 一致 |
| 7 | 纯过滤排序 | entry_id 升序（稳定可预期，翻页一致） |
| 8 | 纯过滤相似度展示 | 不展示（同 `/rand`）；有关键词时展示关键词相似度（score 0–100，同 `/search`） |
| 9 | tag 大小写 | 区分大小写（与 speaker 一致） |
| 10 | 检索无 speaker 条目 | 不支持（`@` 后必须非空） |
| 11 | 普通文本兜底 | 不改动，仍走纯关键词 `/search` |
| 12 | 分页 | 复用每页 10 条 + 回复 `n` 翻页 |
| 13 | 实现方案 | 方案 A：新建 `CombinedSearcher`（engine 层），与 `RandomSearcher`/`SemanticSearcher` 同构 |

## 3. 命令语法与参数解析

### 语法示例
```
/query 加班心累 @小明 #吐槽 #加班     # 关键词 + speaker + 多 tag
/query @小明 @小红                    # 多 speaker（OR）
/query #吐槽 #深夜                     # 多 tag（AND）
/query 加班                            # 仅关键词（等同 /search）
/query 加班 #吐槽                      # 关键词 + 单 tag
```

### 解析规则（插件层 `meme_query.py`）
- 取 `CommandArg()` 纯文本，按空白切分为 token。
- token 以 `#` 开头且 `len > 1`：`tag = tok[1:]`，追加到 `tags`。
- token 以 `@` 开头且 `len > 1`：`speaker = tok[1:]`，追加到 `speakers`（收集所有，OR）。
- 其余 token：追加到 `kw_tokens`，最后 `" ".join(kw_tokens)` 作为 `keyword`（保留原始空格，交给 `KeywordSearcher` 内部去空白）。
- `#` / `@` 单独成 token（前缀后为空）：忽略，不作为关键词也不作为筛选条件。
- 解析后若 `keyword` 为空、`speakers` 为空、`tags` 为空（三者皆空）：回复用法提示并 `finish`。

## 4. 架构

### 4.1 engine 层

#### `bot/engine/keyword_searcher.py`（向后兼容小改）
新增公开方法，把现有 `search()` 的两层匹配核心抽出：
```python
def search_in(self, entries: dict[int, MemeEntry], keyword: str) -> list[SearchResult]:
    """在给定 entries 子集上执行关键词搜索（两层匹配，逻辑同 search）。"""
```
- 复用现有 `_strip_all_whitespace` / `_remove_particles` / `_search_exact_substring` / `_search_fuzzy_lcs`（后两者已接收 `entries` 参数）。
- 返回前按 `self._limit` 截断（`results[: self._limit]`），与 `search()` 行为一致；默认 `_limit=None` 即全量。
- 原 `search(keyword)` 改为：`entries = self._metadata_store.get_all_entries(); return self.search_in(entries, keyword)`。
- **现有 `/search`、`/rand` 行为完全不变。**

#### 新建 `bot/engine/combined_searcher.py`
模仿 `random_searcher.py` 风格（同步方法、中文 Google 风格 docstring）：
```python
class CombinedSearcher:
    def __init__(self, metadata_store: MetadataStoreProvider, keyword_searcher: KeywordSearcher) -> None: ...

    def search(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str] | None = None,
    ) -> list[SearchResult]: ...
```
检索流程：
1. `entries = metadata_store.get_all_entries()`（命中 `_entries` 内存缓存）。
2. 若 `speakers` 非空：保留 `entry.speaker in speakers`（OR，精确相等）。
3. 若 `tags` 非空：保留 `all(t in entry.tags for t in tags)`（AND，区分大小写）。
4. 过滤后子集为空 -> 返回 `[]`。
5. 若 `keyword` 非空：委托 `KeywordSearcher.search_in(子集, keyword)`，返回带 similarity 的结果（两层匹配：精确子串 -> LCS 模糊，降序）。
6. 若 `keyword` 为空：把过滤后条目包装为 `SearchResult(similarity=0.0)`，按 `entry_id` 升序稳定排序。

> 关键：先按 speaker/tags 过滤 entries 子集，再在子集上跑关键词搜索（比「全库搜索再过滤」更省，且语义一致）。

#### `bot/engine/index_manager.py`
- `__init__` 新增可选参数 `combined_searcher: CombinedSearcher | None = None`，存 `self._combined_searcher`。
- 新增方法（持读锁，模板同 `search()`/`random_search()`）：
```python
async def search_combined(
    self, keyword: str | None, speakers: list[str], tags: list[str]
) -> list[SearchResult]:
    async with self._rwlock.read(timeout=self.read_timeout):
        if self._metadata_store.entry_count() == 0:
            return []
        if self._combined_searcher is None:
            raise RuntimeError("CombinedSearcher 未注入")
        return self._combined_searcher.search(keyword, speakers, tags)
```
- 顶部 import `CombinedSearcher`。

#### `bot/engine/__init__.py`
- 导入 `CombinedSearcher`，加入 `__all__`。

### 4.2 注入层

#### `bot/app_state.py`
- 新增 `_combined_searcher` 全局变量、`init_app` 参数 `combined_searcher`、`get_combined_searcher()` 函数（模板同 `get_random_searcher`）。
- 需相应 `from .engine.combined_searcher import CombinedSearcher`（或经 `bot/engine/__init__.py` 导出后导入）。

#### `bot/bot.py` `_on_startup`
- 需 `from bot.engine.combined_searcher import CombinedSearcher`（或经 `bot.engine` 导入）。
- 在创建 `random_searcher` 后：`combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)`。
- 注入 `IndexManager(..., combined_searcher=combined_searcher)`。
- `init_app(..., combined_searcher=combined_searcher)`。

### 4.3 插件层

#### 新建 `bot/plugins/meme_query.py`
模板参考 `meme_search.py`：
```python
query_cmd = on_command("query", rule=to_me(), priority=5, block=True, aliases={"q"})

@query_cmd.handle()
async def handle_query(bot, event, matcher, args = CommandArg()):
    # 1. 授权校验 is_authorized / log_unauthorized
    # 2. session_manager.activate_chat(user_id, "query", matcher)  # 拒绝而非覆盖
    # 3. 解析 #/@ 前缀（见 §3）
    # 4. 三者皆空 -> finish 用法提示
    # 5. 构造 PresentOptions：
    #    有关键词 -> QUERY_KW_OPTIONS(show_similarity=True, score, next_trigger="n")
    #    无关键词 -> QUERY_FILTER_OPTIONS(show_similarity=False, next_trigger="n")
    #    存入 matcher.state["query_options"] 供 got 复用
    # 6. await execute_combined_search(bot, event, matcher, keyword, speakers, tags, options=options)

@query_cmd.got("selection")
async def got_selection(bot, event, matcher, selection_msg = Arg("selection")):
    options = matcher.state.get("query_options", PresentOptions())
    await handle_got_selection(bot, event, matcher, selection_msg, "/query", options=options)
```

**列表展示规则**（由 `PresentOptions` 控制，首屏与 got 翻页共用同一 options 对象）：
- 有关键词：列表行末尾展示关键词相似度百分比（score 量纲 0–100，同 `/search`），形如 `1. OCR文本 -- id, speaker, tags, 85%`。
- 无关键词（纯过滤）：列表行**不展示**相似度（同 `/rand`），形如 `1. OCR文本 -- id, speaker, tags`。
- 单结果：直接发送图片 + 元数据行（不涉及列表与相似度，复用 `dispatch_search_results` 统一分支）。
- 两者均支持多结果分页（每页 10 条，回复 `n` 翻页）。

- 群聊属组 B：支持群聊 @bot，不回复「此命令仅限私聊使用」。

#### `bot/plugins/_search_utils.py`
新增 `execute_combined_search`（模板同 `execute_search`）：
```python
async def execute_combined_search(
    bot, event, cmd_matcher, keyword: str | None, speakers: list[str], tags: list[str],
    *, options: PresentOptions = PresentOptions(),
) -> None:
    # get_index_manager（RuntimeError -> "服务未就绪，请稍后再试"）
    # results = await index_manager.search_combined(keyword, speakers, tags)
    #   except asyncio.TimeoutError -> "索引更新较慢，请稍后再试"
    #   except Exception -> "搜索服务暂时不可用，稍后重试"
    # dispatch_search_results(bot, event, cmd_matcher, results, options=options)
```
**完全复用** `dispatch_search_results` / `present_candidates` / `handle_got_selection`，无需改动它们。

#### `bot/plugins/_help_text.py`
`HELP_TEXT` 在 `/search` 行后插入：
```
/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足）
```

## 5. 数据流

```
用户: /query 加班 @小明 #吐槽
  │
  ▼
meme_query.handle_query
  ├ 授权校验 -> activate_chat("query")
  ├ 解析: keyword="加班", speakers=["小明"], tags=["吐槽"]
  ├ PresentOptions: 有关键词 -> show_similarity=True, score, next_trigger="n"
  └ execute_combined_search
        │
        ▼
  index_manager.search_combined（持读锁）
        │
        ▼
  CombinedSearcher.search
        ├ entries = get_all_entries()
        ├ 过滤: speaker ∈ ["小明"] (OR)  AND  "吐槽" ∈ entry.tags (AND)
        ├ 子集非空, keyword 非空 -> KeywordSearcher.search_in(子集, "加班")
        └ 返回带 similarity 的 SearchResult 列表（降序）
        │
        ▼
  dispatch_search_results
        ├ 空结果 -> "没有匹配到任何表情包 🙁"
        ├ 单结果 -> 发图 + 元数据行
        └ 多结果 -> present_candidates（第 1 页，10 条）+ 创建选择会话
              │
              ▼
        用户回复编号 / n（翻页）
              │
              ▼
        handle_got_selection -> 发图 + 元数据行
```

## 6. 错误处理与边界

| 场景 | 预期行为 |
|------|---------|
| `/query` 无参数 | 回复用法提示 `/query <关键词> [@说话人] [#标签...]` |
| 仅 `@speaker`，无任何条目匹配 | `没有匹配到任何表情包 🙁` |
| 仅 `#tag`，tag 不存在 | 同上 |
| 多 `#tag` AND，无同时满足者 | 同上 |
| 多 `@speaker` OR，无任一命中 | 同上 |
| `@` 或 `#` 单独 token | 忽略该 token；若致三者皆空则回复用法提示 |
| 多个 `@speaker` | 收集为 speakers 列表，OR 匹配；重复 `@` 自动去重 |
| 关键词含 `#`/`@` 字符 | 被前缀解析吞掉，无法作为关键词搜索（OCR 文本去空白后罕见，已接受该权衡，文档注明） |
| speaker/tag 区分大小写 | `"Xiao"` ≠ `"xiao"`，`"Cat"` ≠ `"cat"` |
| tag 名含空格 | 不支持（token 按空白切分，与 `/add` `/addtag` 一致） |
| 索引刷新期间 `/query` | 读锁等待超时 -> `索引更新较慢，请稍后再试` |
| 搜索异常 | `搜索服务暂时不可用，稍后重试` |
| 服务未就绪（IndexManager 未初始化） | `服务未就绪，请稍后再试` |
| 同一用户已有活跃会话再发 `/query` | `已有命令在处理中，请先 /cancel` |
| `/query` 等待选择时 `/cancel`、`/help` | 旁路生效（复用 `handle_got_selection`） |
| 群聊 @bot 调用 `/query` | 正常执行（属组 B） |
| 多结果翻页 | 回复 `n` 看下一页，末页提示「没有更多结果了」（复用现有逻辑） |
| `keyword` 为空字符串 | 等同 `None`，视为无关键词，走纯过滤分支（`if keyword` 对 `""` 为 False） |
| `speaker` 为 `None` 的条目 | 不被任何 `@` 命中（`None in [...]` 为 False，与决策 #10 一致） |
| 重复 `#tag` | 自动去重，不影响 AND 判定（实现可用 `set`） |

## 7. 测试策略

### 7.1 `tests/unit/engine/test_combined_searcher.py`（新增）
用 mock `MetadataStore`（返回固定 `dict[int, MemeEntry]`）+ 真实 `KeywordSearcher`：
- 纯 speaker 过滤：单个 / 多个 OR（命中 / 未命中）
- 纯 tag 过滤：单个 / 多个 AND（含部分命中、全命中、tag 不存在）
- speaker + tags 组合（交集）
- keyword + speaker + tags 组合（子集上跑关键词，similarity 正确）
- keyword 单独（与 `/search` 结果一致）
- 过滤后子集为空 -> `[]`
- speaker 精确相等（大小写敏感）
- tag 区分大小写
- 无关键词分支：`similarity=0.0` 且按 `entry_id` 升序
- `speaker=None` 的条目不被 `@` 命中
- `keyword=""` 视为无关键词（走纯过滤分支）

### 7.2 `tests/unit/engine/test_keyword_searcher.py`（补充）
- `search_in(子集, keyword)` 在子集上两层匹配正确
- `search(keyword)` 行为不变（回归）

### 7.3 `tests/unit/plugins/test_meme_query.py`（新增）
mock `get_index_manager` + `session_manager`：
- 参数解析：`#tag`、`@speaker`、多 `#tag`、多 `@speaker`（OR 收集）、`#`/`@` 单独 token 忽略、关键词含空格
- 三者皆空 -> 用法提示
- 授权校验、会话互斥
- 群聊属组 B（不回复「仅限私聊」）
- 无结果 -> `没有匹配到任何表情包 🙁`
- 多结果分页、got 选择 options 一致性（有关键词 vs 无关键词）
- 关键词含 `#`/`@` 被前缀解析吞掉

### 7.4 检查命令
```bash
uv run python -m compileall bot tests
uv run pytest tests/unit -q
```

## 8. 文档同步

| 文件 | 改动 |
|------|------|
| `docs/PRD.md` | 新增「组合检索 /query」功能小节（触发方式、流程、交互约束）；§3 权限约束补 `/query` 属组 B；§5 边界表补 `/query` 相关行 |
| `CONTEXT.md` | 术语表新增 `/query`、`CombinedSearcher`；「依赖协议」说明 CombinedSearcher 复用 `MetadataStoreProvider` + `KeywordSearcher.search_in` |
| `README.md` | 功能列表加 `/query` 示例；帮助文本块同步；命令属组说明补 `/query` |
| `docs/api/API.md` | 新增 `combined_searcher.md` 与 `meme_query.md` 条目；更新 `index_manager.md`（`search_combined`）、`app_state.md`（`get_combined_searcher`）、`bot.md`（startup 注入）、`keyword_searcher.md`（`search_in`）；目录树补 `meme_query.py`、`combined_searcher.py` |
| `.env.example` | 无新增环境变量，不动 |

## 9. 明确不做的事（YAGNI）

- 不改 `/search` 行为，不改普通文本兜底。
- 不支持 speaker 模糊/子串匹配（用户选精确）。
- 不支持多 tag OR（用户选 AND）。
- 不支持检索「无 speaker」的条目（`@` 后必须非空）。
- 不支持带空格的 tag（与 `/add` `/addtag` 一致）。
- 不新增环境变量。
- 不改 sqlite/chroma schema（复用现有 `meme.speaker` + `meme_tag`）。

## 10. 文件改动清单

**新增（4）**
- `bot/engine/combined_searcher.py`
- `bot/plugins/meme_query.py`
- `tests/unit/engine/test_combined_searcher.py`
- `tests/unit/plugins/test_meme_query.py`

**修改（9）**
- `bot/engine/keyword_searcher.py`（加 `search_in`）
- `bot/engine/index_manager.py`（加 `search_combined` + 注入参数）
- `bot/engine/__init__.py`（导出 `CombinedSearcher`）
- `bot/app_state.py`（加 `get_combined_searcher`）
- `bot/bot.py`（startup 注入）
- `bot/plugins/_search_utils.py`（加 `execute_combined_search`）
- `bot/plugins/_help_text.py`（HELP_TEXT 加一行）
- `tests/unit/engine/test_keyword_searcher.py`（补 `search_in` 回归）
- 文档：`docs/PRD.md`、`CONTEXT.md`、`README.md`、`docs/api/API.md`

## 11. 提交策略

按项目规则（CLAUDE.md）：禁止在 main 分支自行 `git add`/`commit`。spec 文档本身是否提交由用户决定；实现完成后交用户审核。
