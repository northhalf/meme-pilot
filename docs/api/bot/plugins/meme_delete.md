# /del 命令 — API 参考

## 依赖

- `app_state.get_index_manager()`
- `app_state.get_metadata_store()`
- `bot.auth.is_authorized()`
- `bot.session.session_manager`
- `bot.session.timeout_session`
- `bot.plugins._search_utils.got_intercept_bypass`

## 命令

`on_command("del", rule=to_me(), priority=5, block=True)`

### handle_delete

入口处理器。授权校验 → 私聊检查 → 会话激活 → 参数解析 → 查询每个 id → 发送摘要确认消息 → 注册超时。

命令格式：`/del <id>...`。支持同时删除多个表情包，id 间以空格分隔；会自动去重并保持顺序。

摘要确认消息列出每个待删除 id 及其截断后的 OCR 文本；未找到的 id 也会一并提示。

### got_confirm

`got("confirm")` 处理器。处理用户确认/取消：

- 确认（确认/yes/y）→ `IndexManager.delete()` → 回复删除结果（成功/未找到/失败）
- 其他 → 回复「已取消删除」

## 错误处理

| 异常 | 用户消息 |
|------|----------|
| `IndexAddCancelledError` | 服务正在关闭，请稍后再试 |
| `RefreshInProgressError` | 索引正在刷新，请稍后再试 |
| `asyncio.TimeoutError` | 删除处理超时，请稍后再试 |
| 其他异常 | 删除过程中发生异常，请稍后重试 |

## 群聊

授权用户群聊 @bot 调用时回复「此命令仅限私聊使用」。
