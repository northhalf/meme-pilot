# /refresh 命令设计文档

> 日期：2026-06-23
> 状态：已批准

## 概述

实现 `meme_refresh.py` NoneBot2 插件，提供 `/refresh` 命令用于增量更新表情包索引。
同时建立 `app_state.py` 共享实例管理模式，供后续 `/search`、`/ai`、`/add` 插件复用。

## 组件

### 1. `bot/app_state.py` — 共享实例管理

模块级单例模式。`bot.py` 启动时调用 `init_app()` 初始化，插件通过 `get_*()` 函数获取实例。

```python
_index_manager: IndexManager | None = None
_ocr_service: DeepSeekOcrService | None = None
_embedding_service: EmbeddingService | None = None

def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None: ...

def get_index_manager() -> IndexManager: ...      # 未初始化 raise RuntimeError
def get_ocr_service() -> DeepSeekOcrService: ...  # 未初始化 raise RuntimeError
def get_embedding_service() -> EmbeddingService: ... # 未初始化 raise RuntimeError
```

- 单元测试时可 mock `get_*()` 函数
- 未调用 `init_app()` 时 `get_*()` 抛出明确的 `RuntimeError` 提示

### 2. `bot/plugins/__init__.py` — 插件包初始化

空文件，使 `plugins/` 成为 Python 包。

### 3. `bot/plugins/meme_refresh.py` — `/refresh` 命令插件

#### 注册

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

refresh_cmd = on_command("refresh", priority=5, block=True)
```

#### 处理流程

```python
@refresh_cmd.handle()
async def handle_refresh(bot: Bot, event: PrivateMessageEvent):
    # 1. 授权校验：event.get_user_id() in AUTHORIZED_USER_IDS
    # 2. 获取 IndexManager 单例
    # 3. acquire_lock()，失败 → "索引正在更新，请稍后再试" → return
    # 4. try:
    #      await bot.send(event, "正在刷新索引，请稍候...")
    #      result = await index_manager.sync_with_filesystem()
    #    finally:
    #      index_manager.release_lock()
    # 5. 格式化 SyncResult 回复
```

#### 授权校验

- 从环境变量 `AUTHORIZED_USER_IDS` 读取（逗号分隔的 QQ 号）
- `event.get_user_id()` 不在白名单中时静默忽略（仅记录日志）
- 群聊消息不处理（通过 `PrivateMessageEvent` 类型过滤）

#### 回复格式

**正常完成：**
```
索引刷新完成 ✅
新增: X | 删除: X | 去重: X | 无文字移走: X | 失败: X
失败文件: file1.jpg, file2.png（最多前 10 个，仅失败数 > 0 时显示）
```

**锁占用：**
```
索引正在更新，请稍后再试
```

**memes/ 为空（无任何条目且无新增）：**
```
表情包目录为空，请先添加图片并执行 /refresh
```

#### 错误处理

| 场景 | 行为 |
|------|------|
| 锁占用 | 回复提示，不执行同步 |
| memes/ 为空 | SyncResult 正常返回（added=0, deleted=0），回复空目录提示 |
| OCR/Embedding API 异常 | sync_with_filesystem 内部跳过失败文件，记入 failed 列表 |
| 全部文件失败 | 正常返回摘要，失败数 > 0 |

#### 依赖导入

```python
from bot.app_state import get_index_manager
```

## PRD 对齐

| PRD 条目 | 实现位置 |
|----------|----------|
| 3.5 增量更新 1-14 | `sync_with_filesystem()` 内部已实现 |
| 全局索引更新锁 | `acquire_lock()` / `release_lock()` |
| 锁占用拒绝服务 | 插件层检查 `is_locked` |
| 回复摘要（新增/删除/去重/无文字移走/失败） | 插件层格式化 `SyncResult` |
| 失败文件最多列出前 10 个 | 插件层 `failed[:10]` |
| AUTHORIZED_USER_IDS 白名单 | 插件层环境变量校验 |

## 文件清单

| 文件 | 操作 |
|------|------|
| `bot/app_state.py` | 新建 |
| `bot/plugins/__init__.py` | 新建 |
| `bot/plugins/meme_refresh.py` | 新建 |
| `docs/process.md` | 追加 app_state 和 meme_refresh 完成记录 |
| `docs/api/API.md` | 追加 app_state 和 meme_refresh 接口说明 |

## 文件树更新

新增 `app_state.py` 和 `bot/plugins/` 目录，需同步更新以下文件中的项目结构树：

- `docs/PRD.md` 第 6 节项目结构 — 在 `bot/` 下追加 `app_state.py` 和 `plugins/` 目录
- `docs/api/API.md` 目录结构 — 追加 `app_state.md` 和 `plugins/` 条目

## 文档同步

按 CLAUDE.md 要求，每实现一个模块后更新以下文档：

### `docs/process.md`

追加两条完成记录：
- `bot/app_state.py` — 共享实例管理模块（模块级单例，init_app 初始化，get_* 获取）
- `bot/plugins/meme_refresh.py` — /refresh 命令插件（授权校验、索引锁、sync_with_filesystem 调用、SyncResult 摘要回复）

### `docs/api/API.md`

追加两个模块的接口速查：

**`docs/api/bot/app_state.md`**：
```python
def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None

def get_index_manager() -> IndexManager
def get_ocr_service() -> DeepSeekOcrService
def get_embedding_service() -> EmbeddingService
```

**`docs/api/bot/plugins/meme_refresh.md`**：
```python
# NoneBot2 命令插件，无对外 API
# 注册 /refresh 命令，依赖 app_state.get_index_manager()
```

## 不在范围内

- `bot.py` 入口文件（后续单独实现）
- `config.py` 配置模块（后续单独实现）
- 其他插件（`/search`、`/ai`、`/add`、`/help`）
- 单元测试（后续补充）
