# bot/plugins/meme_add.py — /add 命令插件

> NoneBot2 命令插件，无对外 Python API。本文档记录命令行为与依赖。

## 命令

| 命令 | 格式 | 说明 |
|------|------|------|
| `/add` | `/add [目标命名]` | 通过聊天添加表情包到索引 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `IndexManager` | `app_state.get_index_manager()` | 索引增删改查、锁检查、单张图片添加 |
| `is_authorized()` | `bot.auth` | 授权用户校验 |
| `activate_chat()` / `deactivate_chat()` / `got_intercept_bypass()` | `bot.session` | 新会话管理：激活、停用、got 入口拦截 |
| `extract_image_urls()` | `nonebot.adapters.onebot.v11.helpers` | 从消息提取图片 URL |
| `resolve_unique_filename()` | `bot.engine.index_manager` | 文件名冲突自动编号 |

## 行为

### handle_add（命令入口）

1. 授权校验：非授权用户静默忽略（仅日志）
2. 群聊拦截：非 `"private"` 消息类型回复"此命令仅限私聊使用"
3. 激活聊天会话（`activate_chat`），已有活跃会话则拒绝（回复"已有命令在处理中，请先 /cancel"）
4. 获取 `IndexManager`，未初始化则回复"服务未就绪"
5. 检查索引锁（`IndexManager.is_locked`），锁占用则回复"索引正在更新"
6. 捕获目标命名（`/add` 后的文本）存入 `matcher.state`
7. 回复"请发送图片"并等待用户图片

### got_image（等待图片）

采用 `activate_chat`（更新 current_task）+ `try/except/else` 结构，`deactivate_chat` 和文件清理统一在异常处理分支和成功分支。

1. 从 `got("image")` 接收的消息提取图片 URL（`extract_image_urls`，异常时清理会话）
2. 无图片时 `reject` 提示重发（reject 在 `try` 之外，会话保持活跃）
3. 入口调用 `got_intercept_bypass()` 拦截 `/cancel` 和 `/help`：
   - `/cancel` → `execute_cancel` 取消会话，`return`
   - `/help` → 发送帮助文本，`reject()` 继续等待
4. 获取 `IndexManager`
5. 下载图片（httpx，30s 超时）
6. 确定扩展名（URL 路径 → Content-Type），不支持则回复错误
7. 构建文件名：有目标命名用 `_sanitize_filename()`，否则 `_auto_filename()`（`meme_<时间戳>_<hash8>`）
8. `resolve_unique_filename()` 处理文件名冲突
9. 保存图片到 `memes/`
10. `try/except/else`：
    - `try`：`IndexManager.add_single_file()` 执行压缩→OCR→Embedding 管道
    - `except CompressionError/OcrError/EmbeddingError`：分别回复对应错误消息，`deactivate_chat`
    - `else`：回复成功（`added`/`replaced` 分支附 OCR 文字，`no_text` 分支不变），`deactivate_chat`
11. `except` 分支：删除刚下载的图片文件（`filepath.unlink(missing_ok=True)`），`deactivate_chat`

## 回复格式

**成功添加：** `新增表情包✅，识别到的文字为：{OCR 文本}`

**替换旧图：** `替换旧图✅，识别到的文字为：{OCR 文本}`

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

**锁占用：** `索引正在更新，请稍后再试`

## 会话管理

- 使用 `bot.session` 模块的 ChatSession + SelectionSession 机制
- 每用户同一时间仅一个活跃会话（`activate_chat` 互斥检查）
- 非活跃命令（`/help`、`/cancel`）可旁路触发（`got_intercept_bypass`）
- 会话超时由 NoneBot2 全局配置 `SESSION_EXPIRE_TIMEOUT` 控制
