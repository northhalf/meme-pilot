# bot/plugins/meme_plain_text.py — 兜底消息插件

> NoneBot2 兜底插件，处理普通文本和未知斜杠命令。无对外 Python API。

## 注册

```python
catch_all = on_message(rule=to_me(), priority=99, block=False)
```

## 行为

### 普通文本

授权用户私聊或群聊 @bot 发送不以 `/` 开头的普通文本时，等同执行 `/search`：
1. 授权校验
2. `session_manager.activate_chat()` 激活会话，若已有活跃会话则 `matcher.finish("已有命令在处理中，请先 /cancel")`
3. 调用 `_search_utils.execute_search()` 执行搜索

### 未知斜杠命令

授权用户私聊或群聊 @bot 发送未知斜杠命令时，回复"未知命令"并附帮助摘要。

### 非授权用户

所有消息静默忽略（仅记录日志）。

### 多结果选择

`got("selection")` 薄包装，委托 `_search_utils.handle_got_selection()` 处理由本 matcher 触发的搜索多结果选择（旁路拦截、会话检查、`handle_selection`、发送图片、清理）。

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `is_authorized()` / `log_unauthorized()` | `bot.auth` | 授权校验 |
| `execute_search()` / `handle_got_selection()` | `bot.plugins._search_utils` | 搜索核心逻辑与 got 选择处理 |
| `HELP_TEXT` | `bot.plugins._help_text` | 帮助文本常量 |
| `session_manager` | `bot.session` | 会话管理（activate_chat） |

## 匹配器

| 匹配器 | 类型 | priority | block | 说明 |
|--------|------|----------|-------|------|
| `catch_all` | `on_message` | 99 | False | 普通文本 / 未知命令兜底 |
