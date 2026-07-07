# 新增 /rand 与 /sim 命令设计文档

> 日期：2026-07-07  
> 主题：新增随机表情包选择与语义相似度 Top-10 选择命令  
> 状态：待用户最终确认

---

## 1. 背景与目标

现有 MemePilot 已支持：
- `/search <关键词>`：关键词模糊匹配，多结果时供用户选择。
- `/ai <描述>`：基于 embedding 语义搜索 + LLM 精排，直接返回唯一最佳结果。

本设计新增两个命令：
- `/rand [关键词]`：随机给出 10 个表情包候选，支持「换一批」重新随机；有关键词时先在关键词匹配结果中随机，无关键词时全库随机。
- `/sim <描述文本>`：根据语义相似度给出前 10 个匹配度最高的候选，供用户选择。

两个命令均支持群聊 @bot 触发，遵循现有活跃会话互斥规则，选择后行为与 `/search` 一致。

---

## 2. 命令定义

| 命令 | 格式 | 权限 | 说明 |
|------|------|------|------|
| `/rand` | `/rand [关键词]` | 授权用户，私聊/群聊@均可 | 无关键词时全库随机；有关键词时先在关键词搜索结果中随机取 10 个。回复 `0` 换一批，每次独立抽样，不限制次数。 |
| `/sim` | `/sim <描述文本>` | 授权用户，私聊/群聊@均可 | 基于 embedding 语义搜索召回 Top 10，不调用 LLM 精排，直接展示候选。 |

两者都属于「组 B」命令（与 `/search`、`/help`、`/info`、普通文本同级）。

---

## 3. 架构设计

### 3.1 新增与变更文件

```text
bot/engine/
├── random_searcher.py      # 新增
├── semantic_searcher.py    # 新增
└── index_manager.py        # 扩展 random_search / semantic_search

bot/plugins/
├── meme_rand.py            # 新增
├── meme_sim.py             # 新增
├── _search_utils.py        # 扩展：present_candidates / dispatch_search_results
└── _help_text.py           # 更新

tests/unit/engine/
├── test_random_searcher.py     # 新增
└── test_semantic_searcher.py   # 新增

tests/unit/plugins/
├── test_meme_rand.py       # 新增
└── test_meme_sim.py        # 新增

docs/api/API.md             # 更新
CONTEXT.md                  # 更新术语与命令说明
README.md                   # 更新功能说明
```

### 3.2 调用关系

```text
/rand
  └── meme_rand.handle_rand()
        ├── 授权校验、群聊支持、会话激活
        ├── IndexManager.random_search(keyword)
        │      ├── 持读锁
        │      ├── 若 keyword 非空：keyword_searcher.search(keyword)
        │      │   （无匹配时返回空列表，不再回退全库）
        │      └── RandomSearcher.search_random(keyword, limit=10)
        └── dispatch_search_results(results, prompt_suffix="回复 0 换一批")

/sim
  └── meme_sim.handle_sim()
        ├── 授权校验、群聊支持、会话激活
        ├── IndexManager.semantic_search(description)
        │      ├── 锁外 embedding_provider.embed(description)
        │      ├── 持读锁
        │      └── SemanticSearcher.search_semantic(query_vector, limit=10)
        └── dispatch_search_results(results)
```

### 3.3 `bot/bot.py` 注入点

与 `keyword_searcher`、`ai_matcher` 等保持一致，先在 `IndexManager` 外部创建 `RandomSearcher` 与 `SemanticSearcher`，再注入：

```python
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher

keyword_searcher = KeywordSearcher(metadata_store)
ai_matcher = AIMatcher(
    metadata_store,
    vector_store,
    embedding_service,
    rerank_service,
)
random_searcher = RandomSearcher(metadata_store, keyword_searcher)
semantic_searcher = SemanticSearcher(metadata_store, vector_store)

index_manager = IndexManager(
    metadata_store=metadata_store,
    vector_store=vector_store,
    memes_dir=str(MEMES_DIR),
    no_text_dir=str(MEMES_DIR.parent / "meme_no_text"),
    deleted_dir=str(MEMES_DIR.parent / "memes_deleted"),
    replaced_dir=str(MEMES_DIR.parent / "memes_replaced"),
    ocr_provider=ocr_service,
    embedding_provider=embedding_service,
    optimizer=image_optimizer,
    keyword_searcher=keyword_searcher,
    ai_matcher=ai_matcher,
    random_searcher=random_searcher,
    semantic_searcher=semantic_searcher,
)
```

**注意**：`RandomSearcher` 需要引用已有的 `keyword_searcher` 实例，避免重复创建；`SemanticSearcher` 需要 `metadata_store` 与 `vector_store` 实例。

---

## 4. Engine 层设计

### 4.1 `bot/engine/random_searcher.py`

```python
@dataclass
class RandomSearcher:
    """随机取样搜索器。"""

    metadata_store: MetadataStoreProvider
    keyword_searcher: KeywordSearcher

    def search_random(
        self,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """随机返回指定数量的表情包候选。

        流程：
        1. keyword 非空时，先用 KeywordSearcher 搜索得到候选池；
           若 KeywordSearcher 返回空，则整体返回空列表（不再回退全库）。
        2. keyword 为空时，候选池为全库条目。
        3. 从候选池中随机取样 limit 条。

        Args:
            keyword: 可选关键词；None/空串表示全库随机。
            limit: 返回数量上限，默认 10。

        Returns:
            随机取样后的 SearchResult 列表；候选不足时返回全部。
        """
```

**关键行为**
- `keyword` 为空/None：从 `metadata_store.get_all_entries()` 的全部条目中取样。
- `keyword` 非空：先调用 `keyword_searcher.search(keyword)`；
  - 若搜索结果非空，从中随机取样。
  - 若搜索结果为空，返回空列表（不再回退全库随机）。
- 候选池 ≤ `limit` 时返回全部候选。
- 使用 `random.sample`（候选 ≥ limit）或返回全部（候选 < limit），**每次独立抽样，不避免重复、不限制次数**。
- `SearchResult.similarity` 统一填 `0.0`（随机无相似度语义）。

### 4.2 `bot/engine/semantic_searcher.py`

```python
@dataclass
class SemanticSearcher:
    """语义搜索器。基于 embedding 向量召回 Top-N 候选。"""

    metadata_store: MetadataEntryProvider
    vector_store: VectorQueryProvider

    async def search_semantic(
        self,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[SearchResult]:
        """根据 embedding 向量召回最相似的 N 个表情包。

        流程：
        1. 调用 vector_store.query(query_vector, n_results=limit)。
        2. 用 metadata_store.get_entry(hit.entry_id) 取 metadata。
        3. 组装 SearchResult，similarity = hit.similarity。
        """
```

**关键行为**
- 不调用 LLM 精排。
- 与 `AIMatcher.match_with_vector` 的召回阶段一致，但返回列表而非单一结果。
- metadata 缺失的 hit 跳过，与 `AIMatcher` 保持一致。

### 4.3 `IndexManager` 扩展

```python
class IndexManager:
    def __init__(
        self,
        ...,
        random_searcher: RandomSearcher | None = None,
        semantic_searcher: SemanticSearcher | None = None,
    ) -> None:
        ...
        self._random_searcher = random_searcher
        self._semantic_searcher = semantic_searcher

    async def random_search(self, keyword: str | None = None) -> list[SearchResult]:
        """随机搜索入口。持读锁调用 RandomSearcher.search_random。"""
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._metadata_store.entry_count() == 0:
                return []
            if self._random_searcher is None:
                raise RuntimeError("RandomSearcher 未注入")
            return self._random_searcher.search_random(keyword)

    async def semantic_search(self, description: str) -> list[SearchResult]:
        """语义搜索入口。锁外 embed，持读锁查询。

        Raises:
            ValueError: embedding 返回零向量。
            RuntimeError: SemanticSearcher 或 EmbeddingProvider 未注入。
            asyncio.TimeoutError: 等待读锁超时。
        """
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

**设计要点**
- `random_search` 完全在读锁内执行。
- `semantic_search` 锁外 embed，锁内只做向量查询和 metadata 组装。
- 对零向量抛出 `ValueError`，与 `AIMatcher.match_with_vector` 保持一致。
- `_vector_norm` 当前位于 `ai_matcher.py`，实现时应将其抽到 `bot/engine/utils.py`（或类似公共模块），供 `ai_matcher.py` 和 `index_manager.py` 共用。
- 空库直接返回 `[]`，由插件层决定提示语。

---

## 5. 插件层设计

### 5.1 `bot/plugins/_search_utils.py` 扩展

保持 `execute_search` 语义不变，抽离通用逻辑：

- 新增 `present_candidates()`：展示候选列表并创建选择会话。
- 新增 `dispatch_search_results()`：统一处理无结果/单结果/多结果的分支。
- **重命名**：原 `handle_selection()` 改名为 `resolve_selection()`，避免与 `handle_rand_selection`、`got_sim_selection` 等选择阶段函数混淆；其职责是解析编号并返回对应候选或错误消息，属于检查/解析行为。

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
```

```python
async def dispatch_search_results(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    results: list[SearchResult],
    *,
    prompt_suffix: str = "",
) -> None:
    """统一处理搜索结果：无结果、单结果、多结果。

    /search、/rand、/sim 都调用此函数。
    """
```

`execute_search` 内部在拿到 `results` 后调用 `dispatch_search_results`，保持原有单结果直接发送、多结果展示的行为。

### 5.2 `bot/plugins/meme_rand.py`

```python
rand_cmd = on_command("rand", rule=to_me(), priority=5, block=True)


@rand_cmd.handle()
async def handle_rand(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/rand 命令入口。"""
    user_id = event.get_user_id()
    # 授权校验（与 /search 一致）
    # 群聊支持
    # 会话激活：command_type="rand"

    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/rand").removeprefix("rand").strip()
    keyword = keyword or None

    try:
        index_manager = get_index_manager()
    except RuntimeError:
        ...
        return

    try:
        results = await index_manager.random_search(keyword)
    except asyncio.TimeoutError:
        ...
        return
    except Exception:
        ...
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


@rand_cmd.got("selection")
async def got_rand_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """处理 /rand 的选择：支持回复 0 换一批。"""
    await handle_rand_selection(bot, event, matcher, selection_msg)
```

### 5.3 `bot/plugins/meme_sim.py`

```python
sim_cmd = on_command("sim", rule=to_me(), priority=5, block=True)


@sim_cmd.handle()
async def handle_sim(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/sim 命令入口。"""
    user_id = event.get_user_id()
    # 授权校验、群聊支持、会话激活：command_type="sim"

    raw_text = event.get_plaintext().strip()
    description = raw_text.removeprefix("/sim").removeprefix("sim").strip()
    if not description:
        session_manager.deactivate_chat(user_id)
        await matcher.finish("/sim <描述文本>")
        return

    try:
        index_manager = get_index_manager()
    except RuntimeError:
        ...
        return

    try:
        results = await index_manager.semantic_search(description)
    except asyncio.TimeoutError:
        ...
        return
    except ValueError:
        await matcher.finish("AI 服务暂时不可用，稍后重试")
        return
    except Exception:
        await matcher.finish("AI 服务暂时不可用，稍后重试")
        return

    if not results:
        session_manager.deactivate_chat(user_id)
        await matcher.finish("没有找到匹配的表情包 🙁")
        return

    await dispatch_search_results(bot, event, matcher, results)


@sim_cmd.got("selection")
async def got_sim_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    await handle_got_selection(bot, event, matcher, selection_msg, "/sim")
```

### 5.4 `handle_rand_selection` 换一批逻辑

```python
async def handle_rand_selection(...):
    """/rand 专用选择处理器。"""
    user_id = event.get_user_id()
    with session_manager.handler_context(user_id, matcher):
        # /cancel /help 旁路
        # 选择会话有效性检查

        selection_text = selection_msg.extract_plain_text().strip()

        if selection_text == "0":
            keyword = matcher.state.get("keyword")
            try:
                index_manager = get_index_manager()
                new_results = await index_manager.random_search(keyword)
            except asyncio.TimeoutError:
                await matcher.reject("索引更新较慢，请稍后再试")
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

        # 普通选择：复用 resolve_selection
        candidates = matcher.state.get("candidates", [])
        result = resolve_selection(matcher, candidates, selection_text)
        if isinstance(result, str):
            await matcher.reject(result + "\n回复 0 换一批")
            return

        session_manager.remove_selection(user_id)
        image_path = MEMES_DIR / result.image_path
        await matcher.send(MessageSegment.image("file://" + str(image_path.resolve())))
        await matcher.finish(format_metadata_line(result.entry_id, result.speaker, result.tags))
```

### 5.5 `_help_text.py` 更新

在 `HELP_TEXT` 中加入：

```text
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包
```

---

## 6. 会话与权限

- `/rand` 和 `/sim` 的 `command_type` 分别为 `"rand"` 和 `"sim"`。
- 均通过 `session_manager.activate_chat()` 激活；已有活跃会话时回复"已有命令在处理中，请先 /cancel"。
- 均支持群聊 @bot 触发（组 B）。
- 选择阶段 `/cancel` 和 `/help` 通过 `got_intercept_bypass` 旁路处理。
- 选择超时统一由 `timeout_session` 处理，超时提示"选择已过期，请重新搜索"。
- `/rand` 换一批时保留 `keyword` 状态，移除旧选择会话、创建新选择会话并刷新候选列表。

---

## 7. 错误处理与边界情况

| 场景 | 处理方式 |
|------|---------|
| IndexManager 未初始化 | 回复"服务未就绪，请稍后再试" |
| `/rand` / `/sim` 等待读锁超时 | 回复"索引更新较慢，请稍后再试" |
| `/sim` embedding 失败或零向量 | 回复"AI 服务暂时不可用，稍后重试" |
| `/rand` 无关键词且索引为空 | 回复"表情包目录为空，请先添加图片并执行 /refresh" |
| `/rand` 有关键词但无匹配 | 回复"没有匹配到任何表情包 🙁" |
| `/sim` 无召回候选 | 回复"没有找到匹配的表情包 🙁" |
| `/rand` 换一批后无候选 | 回复"没有更多表情包了 🙁" 并结束会话 |
| 候选不足 10 个 | 返回实际数量，按单结果/多结果分支处理 |
| 用户回复无效编号 | reject 提示"无效编号，请回复 1-N 之间的数字" |
| metadata 缺失（/sim） | 跳过该 hit，与 `AIMatcher` 一致 |

---

## 8. 测试计划

### 8.1 单元测试

- `tests/unit/engine/test_random_searcher.py`
  - 全库随机取样返回 10 条。
  - 关键词过滤后随机取样。
  - 候选不足 10 条时返回全部。
  - keyword 为空且库为空时返回空列表。
- `tests/unit/engine/test_semantic_searcher.py`
  - mock vector_store.query 与 metadata_store.get_entry，验证返回 SearchResult 列表。
  - metadata 缺失时跳过。
  - 空库时返回空列表。
- `tests/unit/engine/test_index_manager.py`
  - 补充 `random_search` 和 `semantic_search` 的读锁、空库、未注入 Searcher 的测试。
- `tests/unit/plugins/test_meme_rand.py`
  - 命令解析、授权、群聊、空结果提示、换一批交互。
- `tests/unit/plugins/test_meme_sim.py`
  - 命令解析、授权、群聊、空结果提示、embedding 失败降级。
- `tests/unit/plugins/test_search_utils.py`（扩展）
  - `resolve_selection` 对有效编号、无效编号、越界编号、非数字输入的返回行为。
  - `present_candidates` 正确创建选择会话并格式化列表。
  - `dispatch_search_results` 对无结果/单结果/多结果的分支处理。
  - `prompt_suffix` 正确附加到多结果提示。

### 8.2 集成测试

- `tests/integration/test_rand_sim.py`
  - 使用真实索引验证 `/rand` 和 `/sim` 的端到端流程。

---

## 9. 文档更新清单

- [ ] `bot/bot.py`：在 `IndexManager` 初始化时注入 `RandomSearcher` 与 `SemanticSearcher`。
- [ ] `docs/api/API.md`：新增 `RandomSearcher`、`SemanticSearcher`、`IndexManager.random_search`、`IndexManager.semantic_search`、插件接口。
- [ ] `CONTEXT.md`：新增「/rand」「/sim」术语，更新命令分组说明。
- [ ] `README.md`：在功能列表中加入 `/rand` 和 `/sim` 的说明与示例。
- [ ] `docs/superpowers/specs/2026-07-07-rand-sim-commands-design.md`：本文件。

---

## 10. 待确认事项

无。本设计已通过用户评审。
