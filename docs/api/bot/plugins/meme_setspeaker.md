# /setspeaker 命令 — API 参考

## 依赖

- `app_state.get_index_manager()`
- `app_state.get_metadata_store()`
- `bot.auth.is_authorized()`
- `bot.session.session_manager`
- `bot.session.timeout_session`
- `bot.plugins._search_utils.got_intercept_bypass`
- `bot.config.read_session_timeout()`

## 命令

`on_command("setspeaker", rule=to_me(), priority=5, block=True)`

### handle_setspeaker

入口处理器。授权校验 → 私聊检查 → 会话激活 → 参数解析 → 发送图片与确认消息 → 注册超时。

命令格式：`/setspeaker <id> [说话人]`。`[说话人]` 缺省时清空字段。

### got_confirm

`got("confirm")` 处理器。处理用户确认/取消：
- 确认（确认/yes/y）→ `IndexManager.set_speaker()` → 回复修改结果
- 其他 → 回复"已取消"

## 错误处理

| 异常 | 用户消息 |
|------|----------|
| `IndexAddCancelledError` | 服务正在关闭，请稍后再试 |
| `RefreshInProgressError` | 索引正在刷新，请稍后再试 |
| `ValueError`（id 不存在） | 未找到 id 为 {entry_id} 的表情包 |
| `asyncio.TimeoutError` | 修改处理超时，请稍后再试 |

## 群聊

授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"。
