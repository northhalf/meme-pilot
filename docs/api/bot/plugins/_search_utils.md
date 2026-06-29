# bot/plugins/_search_utils.py — 搜索核心逻辑模块

> 以下划线开头避免 NoneBot2 自动加载为插件。提供 `execute_search`、`handle_selection`、`handle_got_selection` 和 `got_intercept_bypass` 供各插件复用。

## 函数

### `execute_search(bot, event, cmd_matcher, keyword) -> None`

核心搜索逻辑。流程：锁检查 → 索引空检查 → 执行搜索 → 结果分支。多结果时注册 session 并启动超时任务。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件（兼容私聊和群聊@） |
| `cmd_matcher` | `Matcher` | 调用方的 Matcher（用于 send/finish） |
| `keyword` | `str` | 搜索关键词 |

| | 说明 |
|--|------|
| **返回** | `None`（通过 `cmd_matcher.finish()` 直接回复） |

多结果分支中，create_selection 后调用 `session_manager.reset_current_task()` 清除已结束的 handle task 引用。

### `handle_selection(matcher, candidates, text) -> SearchResult | str`

处理用户选择编号。

| 参数 | 类型 | 说明 |
|------|------|------|
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |
| `candidates` | `list[SearchResult]` | 搜索结果候选列表 |
| `text` | `str` | 用户输入的编号文本 |

| 返回 | 说明 |
|------|------|
| `SearchResult` | 选择成功时返回对应结果 |
| `str` | 错误消息（无效编号、candidates 为空等） |

### `handle_got_selection(bot, event, matcher, selection_msg, error_label) -> None`

处理 got 选择编号的共享逻辑。供 `meme_search.py` 和 `meme_plain_text.py` 的 `got("selection")` 包装器调用。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件 |
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |
| `selection_msg` | `Message` | 用户回复的选择编号消息 |
| `error_label` | `str` | 异常日志中的操作标签，默认"搜索" |

| 返回 | 说明 |
|------|------|
| `None` | 通过 `matcher.finish()` 直接回复 |

逻辑：got 入口通过 `handler_context` 更新 current_task → `/help`/`/cancel` 旁路拦截 → 选择会话检查 → `handle_selection` → 发送图片 → 清理会话。

### `got_intercept_bypass(user_id, matcher, text, HELP_TEXT) -> bool`

Got handler 入口统一拦截 /help 和 /cancel（从 `bot/session.py` 移入）。

- `/cancel` 分支委托给 `session_manager.execute_cancel()`
- `/help` 分支通过 `matcher.reject(HELP_TEXT)` 发送帮助文本并继续等待
- 匹配规则：`text.startswith("/cmd ") or text == "/cmd"`
- 返回 `True` 表示已拦截（调用方应 return），`False` 表示正常流程继续

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `get_index_manager()` | `bot.app_state` | 锁检查和索引空检查 |
| `get_keyword_searcher()` | `bot.app_state` | 关键词搜索 |
| `session_manager` | `bot.session` | 会话状态管理（activate/deactivate/create_selection 等） |
| `timeout_session()` | `bot.session` | 会话超时检查任务 |
| `MEMES_DIR` | `bot.config` | 图片路径 |
| `HELP_TEXT` | `bot.plugins._help_text` | 帮助文本（旁路拦截） |

## 错误处理

| 场景 | 回复 |
|------|------|
| IndexManager 未初始化 | "服务未就绪，请稍后再试" |
| 索引锁占用 | "索引正在更新，请稍后再试" |
| 索引为空 | "表情包目录为空，请先添加图片并执行 /refresh" |
| KeywordSearcher 未初始化 | "服务未就绪，请稍后再试" |
| search() 异常 | "搜索服务暂时不可用，稍后重试" |
| 无匹配 | "没有匹配到任何表情包 🙁" |
| candidates 为空（handle_selection） | "搜索状态异常，请重新搜索" |
| 无效编号（handle_selection） | "无效编号，请回复 1-{N} 之间的数字" |
| 会话超时 | `timeout_session()` 自动处理 |
| 选择会话过期（handle_got_selection） | "选择已过期，请重新搜索" |
