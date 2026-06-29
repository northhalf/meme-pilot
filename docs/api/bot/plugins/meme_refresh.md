# bot/plugins/meme_refresh.py — /refresh 命令插件

> NoneBot2 命令插件，无对外 Python API。本文档记录命令行为与依赖。

## 命令

| 命令 | 格式 | 说明 |
|------|------|------|
| `/refresh` | `/refresh` | 增量更新表情包索引 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `IndexManager` | `app_state.get_index_manager()` | 索引增删改查与文件系统同步 |
| `is_authorized()` | `bot.auth` | 授权用户校验 |
| `session_manager` | `bot.session` | 会话管理（activate_chat / deactivate_chat） |

## 行为

1. 授权校验：非授权用户静默忽略（仅日志）
2. `session_manager.activate_chat()` 激活会话
3. 群聊拦截：非 `"private"` 消息类型回复"此命令仅限私聊使用"
4. 获取全局索引更新锁，失败则回复"索引正在更新，请稍后再试"
5. 回复"正在刷新索引，请稍候..."
6. 调用 `IndexManager.sync_with_filesystem()` 执行增量同步
7. 释放锁（`try/finally` 保证）
8. `session_manager.deactivate_chat()` 清理会话
9. 回复摘要：新增/删除/去重/无文字移走/失败统计

## 回复格式

**正常完成：**
```
索引刷新完成 ✅
新增: X | 删除: X | 去重: X | 无文字移走: X | 失败: X
失败文件: file1.jpg, file2.png（最多前 10 个，仅失败数 > 0 时显示）
```

**群聊调用：** `此命令仅限私聊使用`

**锁占用：** `索引正在更新，请稍后再试`

**memes/ 为空：** `表情包目录为空，请先添加图片并执行 /refresh`

**同步异常：** `索引刷新失败，请查看日志`
