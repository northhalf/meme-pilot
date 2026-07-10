# bot/plugins/meme_edit.py — /edittext OCR 文本编辑插件

> 本文档记录 /edittext 命令插件的对外行为。模块内部 `_` 前缀函数和方法不在此列出。

授权用户在私聊中发送 `/edittext <entry_id> <新文本>`，Bot 发送确认消息，用户回复「确认」或「yes」后执行修改。

## 命令注册

| 字段 | 值 |
|------|-----|
| 命令 | `edittext` |
| 注册 | `on_command("edittext", rule=to_me(), priority=5, block=True)` |
| 形态 | 私聊仅限（组 A），群聊 @bot 时回复「此命令仅限私聊使用」 |
| 依赖 | `app_state.get_index_manager()`、`app_state.get_metadata_store()`、`auth.is_authorized()`、`bot.session.session_manager`、`bot.session.timeout_session`、`bot.plugins._search_utils.got_intercept_bypass` |

## 流程

```
/edittext <entry_id> <新文本>
        │
        ▼
  授权校验 ──非授权→ 静默忽略
        │
        ▼
  消息类型 ──群聊→ 回复"此命令仅限私聊使用"
        │
        ▼
  会话检查 ──已有活跃→ 回复"已有命令在处理中，请先 /cancel"
        │
        ▼
  参数解析
   ├─ 参数不足 → 回复"用法：/edittext <entry_id> <新文本>"
   ├─ entry_id 非数字 → 回复"entry_id 必须为数字"
   ├─ 新文本去空白后为空 → 回复"新文本不能为空"
   └─ entry_id 不存在 → 回复"未找到 id 为 X 的表情包"
        │
        ▼
  发送确认消息
  "当前 OCR 文本：{text}
  修改后文本：{new_text}
  回复「确认」或「yes」确认修改，回复其他内容取消"
        │
        ▼
  注册超时 → 进入 got("confirm") 等待
        │
        ▼
  ┌─ 旁路:/help → 查看帮助，继续等待
  ├─ 旁路:/cancel → 取消修改
  ├─ 确认 → IndexManager.edit_text() → 回复"OCR 文本已修改 ✅"
  ├─ 其他 → 回复"已取消修改"
  └─ 超时 → 回复"修改已取消（超时）"
```

## Handler

### `handle_edit(bot, event, matcher)`

入口：授权校验 → 参数解析 → 发送确认信息 → 注册超时 → got 等待。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 私聊消息事件 |
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |

**错误处理：**
- 非授权用户 → 静默忽略（`log_unauthorized` 记录日志）
- 群聊 @bot → `matcher.finish("此命令仅限私聊使用")`
- 已有活跃会话 → `matcher.finish("已有命令在处理中，请先 /cancel")`
- 参数不足 → `matcher.finish("用法：/edittext <entry_id> <新文本>")`
- entry_id 非数字 → `matcher.finish("entry_id 必须为数字")`
- 新文本为空（去空白后） → `matcher.finish("新文本不能为空")`
- entry 不存在 → `matcher.finish("未找到 id 为 X 的表情包")`

### `got_confirm(bot, event, matcher, confirm_msg)`

处理用户确认/取消。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 私聊消息事件 |
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |
| `confirm_msg` | `Message` | got("confirm") 接收到的消息 |

**确认流程：** `IndexManager.edit_text(entry_id, new_text)` → `EditTextResult`

**错误处理：**
- `asyncio.TimeoutError` → `matcher.finish("修改处理超时，请稍后再试")`
- `IndexAddCancelledError` → `matcher.finish("服务正在关闭，请稍后再试")`
- `RefreshInProgressError` → `matcher.finish("索引正在刷新，请稍后再试")`
- `ValueError` → `matcher.finish("未找到 id 为 X 的表情包")`
- `DuplicateTextError` → `matcher.finish(str(exc))`（异常消息直接回复）
- `EmbeddingError` → `matcher.finish("修改失败（Embedding 异常），请稍后重试")`

## 依赖

| 模块 | 用途 |
|------|------|
| `app_state.get_index_manager()` | 调用 `IndexManager.edit_text()` 执行修改 |
| `app_state.get_metadata_store()` | 校验 entry_id 是否存在、获取 OCR 文本 |
| `auth.is_authorized()` | 授权用户白名单校验 |
| `bot.session.session_manager` | 会话互斥（防重复提交） |
| `bot.session.timeout_session` | got 等待超时自动取消 |
| `bot.plugins._search_utils.got_intercept_bypass` | `/help` 和 `/cancel` 旁路拦截 |
| `bot.plugins._help_text.HELP_TEXT` | `/help` 旁路时发送帮助文本 |
