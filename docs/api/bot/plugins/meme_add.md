# bot/plugins/meme_add.py — /add 命令插件

> NoneBot2 命令插件，无对外 Python API。本文档记录命令行为与依赖。

## 命令

| 命令 | 格式 | 说明 |
|------|------|------|
| `/add` | `/add [目标命名]` | 通过聊天添加表情包到索引 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `IndexManager` | `app_state.get_index_manager()` | 索引增删改查、锁管理、单张图片添加 |
| `is_authorized()` | `bot.auth` | 授权用户校验 |
| `check_and_cancel()` / `register()` / `cancel()` / `is_cancelled()` | `bot.session` | 共享会话管理，防重复提交 |
| `extract_image_urls()` | `nonebot.adapters.onebot.v11.helpers` | 从消息提取图片 URL |
| `resolve_unique_filename()` | `bot.engine.index_manager` | 文件名冲突自动编号 |

## 行为

### handle_add（命令入口）

1. 授权校验：非授权用户静默忽略（仅日志）
2. 会话覆盖检查：旧会话存在时标记取消并提示
3. 获取 `IndexManager`，未初始化则回复"服务未就绪"
4. 获取全局索引更新锁，失败则回复"索引正在更新"
5. 捕获目标命名（`/add` 后的文本）存入 `matcher.state`
6. 注册新会话

### got_image（等待图片）

采用阶段分离 + `try/finally` 结构，`cancel()` 和 `_release_lock_safe()` 统一在 `finally` 块中执行。

1. 从 `got("image")` 接收的消息提取图片 URL（`extract_image_urls`，异常时释放锁并清理会话）
2. 无图片时 `reject` 提示重发（reject 在 `try/finally` 之外，锁保持持有）
3. 检查会话是否已被取消，已取消则静默退出
4. 获取 `IndexManager`
5. 下载图片（httpx，30s 超时）
6. 确定扩展名（URL 路径 → Content-Type），不支持则回复错误
7. 构建文件名：有目标命名用 `_sanitize_filename()`，否则 `_auto_filename()`（`meme_<时间戳>_<hash8>`）
8. `resolve_unique_filename()` 处理文件名冲突
9. 保存图片到 `memes/`
10. 调用 `IndexManager.add_single_file()` 执行压缩→OCR→Embedding 管道
11. 调用 `_format_ocr_text()` 截断 OCR 文本（超 50 字时截断并标注总长度）
12. 回复结果（`added`/`replaced` 分支附 OCR 文字，`no_text` 分支不变）
13. `finally`：若会话未取消则 `cancel(user_id)`，若 `index_manager` 非 None 则 `_release_lock_safe(index_manager)`

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

- 使用 `bot.session` 模块的共享会话机制
- 每用户同一时间仅一个活跃会话（/add 或 /search 互斥）
- 新会话自动取消旧会话
- 会话超时由 NoneBot2 全局配置 `SESSION_EXPIRE_TIMEOUT` 控制
