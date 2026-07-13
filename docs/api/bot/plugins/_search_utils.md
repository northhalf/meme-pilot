# bot/plugins/_search_utils.py — 搜索核心逻辑模块

> 以下划线开头避免 NoneBot2 自动加载为插件。提供 `format_metadata_line`、`resolve_selection`、`present_candidates`、`dispatch_search_results`、`execute_search`、`handle_got_selection` 和 `got_intercept_bypass` 供各插件复用。

## 常量与类型

### `PAGE_SIZE: int = 10`

每页展示的候选条数。

### `NEXT_PAGE_TRIGGER: str = "n"`

用户回复该词触发"下一页"。

### `PresentOptions`（`@dataclass(frozen=True)`）

候选展示选项，控制列表行相似度展示与翻页。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `show_similarity` | `bool` | `False` | 是否在列表行末尾展示相似度百分比 |
| `similarity_scale` | `Literal["ratio", "score"]` | `"score"` | 相似度量纲；`ratio`=0–1，`score`=0–100 |
| `next_trigger` | `str \| None` | `None` | 下一页触发词；`None` 表示不支持翻页（如 `/rand`） |
| `page_size` | `int` | `PAGE_SIZE` | 每页条数 |

群聊 reply 处理已统一收敛到 `bot.reply`（`reply_utils.send/reject/finish`），`PresentOptions` 不再包含 `reply_in_group` 字段。

### `_similarity_percent(similarity, scale) -> int`

把相似度归一为 0–100 整数百分比；`ratio` 乘 100，`score` 直接取整；clamp 到 `[0, 100]`。仅展示层归一，不改 `SearchResult.similarity` 存储语义。

## 函数

### `format_metadata_line(entry_id, speaker, tags) -> str`

格式化表情包的元数据行。

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id |
| `speaker` | `str | None` | 说话人，可能为 None |
| `tags` | `list[str]` | 标记词列表 |

| 返回 | 说明 |
|------|------|
| `str` | `tags` 为空时为 `id, 无/说话人`；否则为 `id, 无/说话人, tag1, tag2, ...`（speaker 缺失时显示为 `"无"`） |

### `execute_search(bot, event, cmd_matcher, keyword, *, options=PresentOptions()) -> None`

核心关键词搜索逻辑。流程：获取 IndexManager → 执行关键词搜索 → 通过 `dispatch_search_results` 统一分发结果（空/单/多结果，透传 `options`）。单结果或用户完成选择时，先发送图片再发送 `format_metadata_line()` 元数据文本消息；多结果时注册 session 并启动超时任务。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件（兼容私聊和群聊@） |
| `cmd_matcher` | `Matcher` | 调用方的 Matcher（用于 send/finish） |
| `keyword` | `str` | 搜索关键词 |
| `options` | `PresentOptions` | 展示选项（相似度与翻页），默认 `PresentOptions()` |

| | 说明 |
|--|------|
| **返回** | `None`（通过 `cmd_matcher.finish()` 直接回复） |

多结果分支中，create_selection 后调用 `session_manager.reset_current_task()` 清除已结束的 handle task 引用。所有文本回复通过 `reply_utils.finish` 发送，群聊自动带 reply。

### `present_candidates(bot, event, cmd_matcher, candidates, *, options=PresentOptions(), has_next_page=False, prompt_suffix="", use_reject=False) -> None`

展示候选列表并创建选择会话（仅处理多结果）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件 |
| `cmd_matcher` | `Matcher` | 调用方的 Matcher（用于 send/reject） |
| `candidates` | `list[SearchResult]` | 当前页候选结果切片 |
| `options` | `PresentOptions` | 展示选项（相似度与翻页） |
| `has_next_page` | `bool` | 是否还有下一页；为 True 时追加翻页提示 |
| `prompt_suffix` | `str` | 附加在提示末尾的可选文本（如 `"回复 0 换一批"`） |
| `use_reject` | `bool` | `True` 时用 `matcher.reject` 发送列表并继续等待下一次输入；`False` 时用 `matcher.send`（首次展示） |

流程：格式化候选列表（`format_metadata_line`，按 `options` 追加相似度百分比）-> 存储 `candidates` 与 `selection_id` 到 `matcher.state` -> 通过 `reply_utils.send` 或 `reply_utils.reject` 发送列表（群聊自动带 reply；仅当 `has_next_page=True` 追加"回复 n 看下一页"）-> 创建 `timeout_session` 超时任务 -> `session_manager.create_selection` 注册选择会话 -> `reset_current_task` 清理已结束的 handle task。每次调用重置 `SESSION_EXPIRE_TIMEOUT`。

### `dispatch_search_results(bot, event, cmd_matcher, results, *, options=PresentOptions(), prompt_suffix="") -> None`

统一处理搜索结果：空结果 → 单结果 → 多结果。所有纯文本回复通过 `reply_utils.finish` 发送，群聊自动带 reply。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件 |
| `cmd_matcher` | `Matcher` | 调用方的 Matcher（用于 send/finish） |
| `results` | `list[SearchResult]` | 搜索结果全量列表 |
| `options` | `PresentOptions` | 展示选项（相似度与翻页） |
| `prompt_suffix` | `str` | 多结果时传给 `present_candidates` 的附加提示 |

### `resolve_selection(matcher, candidates, text) -> SearchResult | str`

解析用户选择编号。

| 参数 | 类型 | 说明 |
|------|------|------|
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |
| `candidates` | `list[SearchResult]` | 搜索结果候选列表 |
| `text` | `str` | 用户输入的编号文本 |

| 返回 | 说明 |
|------|------|
| `SearchResult` | 选择成功时返回对应结果 |
| `str` | 错误消息（无效编号、candidates 为空等） |

### `handle_got_selection(bot, event, matcher, selection_msg, error_label="搜索", *, options=PresentOptions()) -> None`

处理 got 选择编号的共享逻辑。供 `search.py`、`sim.py` 和 `plain_text.py` 的 `got("selection")` 包装器调用。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件 |
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |
| `selection_msg` | `Message` | 用户回复的选择编号消息 |
| `error_label` | `str` | 异常日志中的操作标签，默认"搜索" |
| `options` | `PresentOptions` | 展示选项（相似度与翻页） |

| 返回 | 说明 |
|------|------|
| `None` | 通过 `matcher.finish()` 直接回复 |

逻辑：got 入口通过 `handler_context` 更新 current_task -> `/help`/`/cancel` 旁路拦截 -> 选择会话检查 -> `next_trigger` 命中则翻页（切下一页调 `present_candidates` 重置超时；末页通过 `reply_utils.reject` 回复"没有更多结果了"）-> 否则 `resolve_selection` -> 发送图片 -> 通过 `reply_utils.finish` 发送 `format_metadata_line()` 元数据行（群聊自动带 reply）-> 清理会话。

### `got_intercept_bypass(event, matcher, text, help_text) -> bool`

Got handler 入口统一拦截 `/help` 和 `/cancel`。

- `/cancel` 分支委托给 `session_manager.execute_cancel(scope, event, ...)`
- `/help` 分支通过 `reply_utils.reject(event, matcher, help_text)` 发送帮助文本并继续等待
- 匹配规则：`text.startswith("/cancel ") or text == "/cancel"`；`/help` 同理
- 返回 `True` 表示已拦截（调用方应 return），`False` 表示正常流程继续

| 参数 | 类型 | 说明 |
|------|------|------|
| `event` | `MessageEvent` | 当前消息事件，用于构造 reply 和作用域 |
| `matcher` | `Matcher` | 当前 got handler 的 Matcher |
| `text` | `str` | 用户消息纯文本 |
| `help_text` | `str` | 帮助文本常量 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `get_index_manager()` | `bot.app_state` | 获取 IndexManager（`search()` 内部已做空库检查） |
| `session_manager` | `bot.session` | 会话状态管理（activate/deactivate/create_selection 等） |
| `timeout_session()` | `bot.session` | 会话超时检查任务 |
| `MEMES_DIR` | `bot.config` | 图片路径 |
| `HELP_TEXT` | `bot.plugins._help_text` | 帮助文本（旁路拦截） |

## 错误处理

| 场景 | 回复 |
|------|------|
| IndexManager 未初始化 | "服务未就绪，请稍后再试" |
| 读锁等待超时 | "索引更新较慢，请稍后再试" |
| search() 异常 | "搜索服务暂时不可用，稍后重试" |
| 无匹配 | "没有匹配到任何表情包 🙁" |
| candidates 为空（resolve_selection） | "搜索状态异常，请重新搜索" |
| 无效编号（resolve_selection） | "无效编号，请回复 1-{N} 之间的数字" |
| 会话超时 | `timeout_session()` 自动处理 |
| 选择会话过期（handle_got_selection） | "选择已过期，请重新搜索" |
