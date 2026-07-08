# /info 命令 — API 参考

## 依赖

- `app_state.get_index_manager()`
- `bot.auth.is_authorized()`
- `psutil`

## 命令

`on_command("info", rule=to_me(), priority=5, block=True)`

### handle_info

入口处理器。授权校验 → 获取 `IndexManager` → 调用 `IndexManager.info()` → 读取本机内存/CPU 占用 → 组装并返回状态消息。

回复内容包括：

- 表情包数量
- speaker 使用频率排行（前 10）
- 当前机器人状态（空闲/正在刷新索引/正在处理命令）
- 内存占用
- CPU 占用

## 错误处理

| 场景 | 用户消息 |
|------|----------|
| `IndexManager` 尚未初始化 | 服务未就绪，请稍后再试 |
| 硬件信息获取失败 | 对应字段显示「获取失败」 |

## 群聊

授权用户群聊 @bot 调用时同样返回状态信息。
