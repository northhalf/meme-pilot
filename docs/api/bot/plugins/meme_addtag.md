# /addtag 命令 — API 参考

## 依赖

- `app_state.get_index_manager()`
- `app_state.get_metadata_store()`
- `bot.auth.is_authorized()`
- `bot.session.session_manager`
- `bot.session.timeout_session`
- `bot.plugins._search_utils.got_intercept_bypass`

## 命令

`on_command("addtag", rule=to_me(), priority=5, block=True)`

### handle_addtag

入口处理器。授权校验 → 私聊检查 → 会话激活 → 参数解析 → 校验条目存在 → 发送确认消息 → 注册超时。

命令格式：`/addtag <id> <tag> [<tag>...]`。支持为一个表情包追加多个标签，标签间以空格分隔。

确认消息包含当前 OCR 文本、当前标签列表和本次新增标签列表；不发送图片。

### got_confirm

`got("confirm")` 处理器。处理用户确认/取消：

- 确认（确认/yes/y）→ `IndexManager.add_tags()` → 回复「标签已添加」及本次新增/全部标签
- 其他 → 回复「已取消」

## 错误处理

| 异常 | 用户消息 |
|------|----------|
| `IndexAddCancelledError` | 服务正在关闭，请稍后再试 |
| `RefreshInProgressError` | 索引正在刷新，请稍后再试 |
| `ValueError`（id 不存在） | 未找到 id 为 {entry_id} 的表情包 |
| `asyncio.TimeoutError` | 添加处理超时，请稍后再试 |

## 群聊

授权用户群聊 @bot 调用时回复「此命令仅限私聊使用」。
