# bot/plugins/_search_utils.py — 搜索核心逻辑模块

> 以下划线开头避免 NoneBot2 自动加载为插件。提供 `execute_search` 和 `handle_selection` 供 `meme_search.py` 和 `meme_plain_text.py` 复用。

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

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `get_index_manager()` | `bot.app_state` | 锁检查和索引空检查 |
| `get_keyword_searcher()` | `bot.app_state` | 关键词搜索 |
| `register()` / `timeout_session()` / `cancel()` | `bot.session` | 会话管理 |
| `MEMES_DIR` | `bot.config` | 图片路径 |

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
