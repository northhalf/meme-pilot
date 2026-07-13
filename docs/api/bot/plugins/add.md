# bot/plugins/add.py — /add 命令插件

> NoneBot2 命令插件，无对外 Python API。本文档记录命令行为与依赖。

## 命令

| 命令 | 格式 | 说明 |
|------|------|------|
| `/add` | `/add [speaker <tags...>]` | 通过聊天添加表情包到索引 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `IndexManager` | `app_state.get_index_manager()` | 索引增删改查、锁检查、单张图片添加 |
| `is_authorized()` | `bot.auth` | 授权用户校验 |
| `session_manager` | `bot.session` | 会话状态管理（activate/deactivate/create_selection/remove_selection/reset_current_task） |
| `timeout_session()` | `bot.session` | 会话超时检查任务 |
| `got_intercept_bypass()` | `bot.plugins._search_utils` | Got 入口旁路拦截 /help 和 /cancel |
| `format_metadata_line()` | `bot.plugins._search_utils` | 格式化成功回复后的元数据行 |
| `read_session_timeout()` | `bot.config` | 读取会话超时秒数，用于动态 prompt |
| `extract_image_urls()` | `nonebot.adapters.onebot.v11.helpers` | 从消息提取图片 URL |
| `resolve_unique_filename()` | `bot.engine.index_manager` | 文件名冲突自动编号 |

## 行为

### handle_add（命令入口）

1. 授权校验：非授权用户静默忽略（仅日志）
2. 群聊拦截：非 `"private"` 消息类型回复"此命令仅限私聊使用"
3. 激活聊天会话（`session_manager.activate_chat`），已有活跃会话则拒绝（回复"已有命令在处理中，请先 /cancel"）
4. 获取 `IndexManager`，未初始化则回复"服务未就绪"
5. `/add` 后的参数按空白切分，第一个词作为 `speaker`，剩余词作为 `tags`，存入 `matcher.state`
6. 创建选择会话（`selection_id` + `session_manager.create_selection`）并启动超时任务（`timeout_session`）
7. 调用 `session_manager.reset_current_task(user_id)` 清除已结束的 handle task 引用
8. 回复"请发送图片"并等待用户图片

### got_image（等待图片）

采用 `handler_context`（with 语句）+ `try/except/else` 结构，`deactivate_chat` 和文件清理统一在异常处理分支和成功分支。

1. **入口更新 current_task**：`with session_manager.handler_context(user_id, matcher)` 自动更新 current_task 和 matcher
2. **旁路拦截**：`got_intercept_bypass()` 拦截 `/cancel` 和 `/help`：
   - `/cancel` → `session_manager.execute_cancel()` 取消会话
   - `/help` → `matcher.reject(HELP_TEXT)` 发送帮助文本，继续等待
3. 从 `got("image")` 接收的消息提取图片 URL（`extract_image_urls`，异常时清理会话）
4. 无图片时 `reject` 提示重发（RejectedException 不清理会话，会话保持活跃）
5. **清理选择会话**：收到有效图片后调用 `session_manager.remove_selection(user_id)` 清除选择会话
6. 获取 `IndexManager`
7. 下载图片（httpx，30s 超时）
8. 确定扩展名（URL 路径 → Content-Type），不支持则回复错误
9. 构建文件名：始终由 `_auto_filename()` 自动生成，格式为 `meme_<YYYYMMDDHHMMSS>_<hash8>`
10. `resolve_unique_filename()` 处理文件名冲突
11. 保存图片到 `memes/`
12. `try/except/else`：
    - `try`：`IndexManager.add()` 执行压缩→OCR→Embedding 管道
    - `except CompressionError/OcrError/EmbeddingError`：分别回复对应错误消息，`deactivate_chat`
    - `else`：回复成功（`added`/`replaced` 分支附 OCR 文字和 `format_metadata_line()` 元数据行，`no_text` 分支不变），`deactivate_chat`
13. `except` 分支：删除刚下载的图片文件（`filepath.unlink(missing_ok=True)`），`deactivate_chat`

## 回复格式

**成功添加：** `新增表情包✅，id：{id}，识别到的文字为：\n「{OCR 文本}」\n{id}, 无/说话人, tag1, tag2, ...`

**替换旧图：** `替换旧图✅，id：{id}，识别到的文字为：\n「{OCR 文本}」\n{id}, 无/说话人, tag1, tag2, ...`

**无文字：** `未识别到文字，已移至 meme_no_text/`

**无图片：** `请发送一张图片`（reject，重新等待）

**下载失败：** `图片下载失败`

**格式不支持：** `不支持的图片格式: xxx`

**保存失败：** `图片保存失败`

**压缩失败：** `图片压缩失败`

**OCR 失败：** `OCR 服务不可用`

**Embedding 失败：** `Embedding 服务不可用`

**未知异常：** `添加失败，请查看日志`

**服务未就绪：** `服务未就绪，请稍后再试`

**锁占用：** `索引正在刷新，请稍后再试`

## 会话管理

- 使用 `bot.session` 模块的 SessionManager 管理 ChatSession + SelectionSession
- 每用户同一时间仅一个活跃会话（`session_manager.activate_chat` 互斥检查）
- 收到有效图片后立即调用 `session_manager.remove_selection` 清理选择会话，允许后续新命令覆盖
- 非活跃命令（`/help`、`/cancel`）可旁路触发（`got_intercept_bypass`）
- 会话超时由 `SESSION_EXPIRE_TIMEOUT` 环境变量控制
