# bot/plugins/meme_cancel.py — /cancel 命令插件

> NoneBot2 命令插件，无对外 Python API。本文档记录命令行为与依赖。

## 命令

| 命令 | 格式 | 说明 |
|------|------|------|
| `/cancel` | `/cancel` | 取消当前正在执行的命令 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `is_authorized()` | `bot.auth` | 授权用户校验 |
| `session_manager` | `bot.session` | 执行取消逻辑（`execute_cancel`） |

## 行为

1. 授权校验：非授权用户静默忽略（仅日志）
2. 委托 `session_manager.execute_cancel()` 执行取消：
   - 有活跃会话→取消异步任务、清理选择会话、finish 旧 matcher、重置会话
   - 无活跃会话→回复"当前没有活跃的会话"
3. 支持同频道（got 等待中）和异频道（私聊/群聊分离）/cancel

## 匹配器

| 匹配器 | 类型 | priority | block | 说明 |
|--------|------|----------|-------|------|
| `cancel_cmd` | `on_command("cancel")` | 5 | True | `/cancel` 命令 |
