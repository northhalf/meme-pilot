# /refresh 命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `/refresh` 命令插件，授权用户私聊触发增量索引刷新

**Architecture:** 模块级单例（app_state.py）管理 IndexManager 等共享实例；NoneBot2 on_command 注册 /refresh 插件，通过 app_state getter 获取依赖

**Tech Stack:** Python 3.12, NoneBot2, nonebot-adapter-onebot v11

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `bot/app_state.py` | 共享实例管理：init_app() 初始化，get_*() 获取 |
| `bot/plugins/__init__.py` | 插件包标识（空文件） |
| `bot/plugins/meme_refresh.py` | /refresh 命令：授权校验、锁管理、同步调用、摘要回复 |
| `docs/process.md` | 追加完成记录 |
| `docs/api/API.md` | 追加接口说明 |

---

### Task 1: 创建 `bot/app_state.py`

**Files:**
- Create: `bot/app_state.py`

- [ ] **Step 1: 创建 app_state.py**

```python
"""共享实例管理模块。

模块级单例模式，供各插件获取 IndexManager、OcrService、EmbeddingService。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from bot.engine.embedding_service import EmbeddingService
from bot.engine.index_manager import IndexManager
from bot.engine.ocr_service import DeepSeekOcrService

_index_manager: IndexManager | None = None
_ocr_service: DeepSeekOcrService | None = None
_embedding_service: EmbeddingService | None = None


def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
    """
    global _index_manager, _ocr_service, _embedding_service
    _index_manager = index_manager
    _ocr_service = ocr_service
    _embedding_service = embedding_service


def get_index_manager() -> IndexManager:
    """获取 IndexManager 单例。

    Returns:
        已初始化的 IndexManager 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _index_manager is None:
        raise RuntimeError("IndexManager 尚未初始化，请先调用 init_app()")
    return _index_manager


def get_ocr_service() -> DeepSeekOcrService:
    """获取 DeepSeekOcrService 单例。

    Returns:
        已初始化的 DeepSeekOcrService 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _ocr_service is None:
        raise RuntimeError("DeepSeekOcrService 尚未初始化，请先调用 init_app()")
    return _ocr_service


def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例。

    Returns:
        已初始化的 EmbeddingService 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _embedding_service is None:
        raise RuntimeError("EmbeddingService 尚未初始化，请先调用 init_app()")
    return _embedding_service
```

- [ ] **Step 2: 语法检查**

```bash
uv run python -m compileall bot/app_state.py
```

Expected: `Syntax OK`

---

### Task 2: 创建 `bot/plugins/__init__.py`

**Files:**
- Create: `bot/plugins/__init__.py`

- [ ] **Step 1: 创建空文件**

```bash
touch bot/plugins/__init__.py
```

---

### Task 3: 创建 `bot/plugins/meme_refresh.py`

**Files:**
- Create: `bot/plugins/meme_refresh.py`

- [ ] **Step 1: 创建 meme_refresh.py**

```python
"""/refresh 命令插件 — 增量更新表情包索引。

授权用户在私聊中发送 /refresh，触发 IndexManager.sync_with_filesystem()
执行按文件名同步的增量刷新。使用全局索引更新锁，锁占用期间拒绝服务。
"""

import logging
import os

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.rule import to_me

from bot.app_state import get_index_manager

logger = logging.getLogger(__name__)

# 授权用户白名单（逗号分隔的 QQ 号）
_AUTHORIZED_USER_IDS: frozenset[str] = frozenset(
    uid.strip()
    for uid in os.environ.get("AUTHORIZED_USER_IDS", "").split(",")
    if uid.strip()
)

refresh_cmd = on_command("refresh", rule=to_me(), priority=5, block=True)


@refresh_cmd.handle()
async def handle_refresh(bot: Bot, event: PrivateMessageEvent) -> None:
    """/refresh 命令处理入口。

    流程：授权校验 → 获取锁 → 执行同步 → 释放锁 → 回复摘要。
    """
    user_id = event.get_user_id()

    # 授权校验
    if user_id not in _AUTHORIZED_USER_IDS:
        logger.debug("非授权用户 %s 的 /refresh 请求，静默忽略", user_id)
        return

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await refresh_cmd.finish("服务未就绪，请稍后再试")
        return

    # 尝试获取全局索引更新锁
    if not index_manager.acquire_lock():
        logger.info("用户 %s 的 /refresh 被拒绝：索引正在更新", user_id)
        await refresh_cmd.finish("索引正在更新，请稍后再试")
        return

    try:
        await bot.send(event, "正在刷新索引，请稍候...")
        result = await index_manager.sync_with_filesystem()
    finally:
        index_manager.release_lock()

    # 无任何条目且无新增 → memes/ 为空
    if index_manager.entry_count == 0 and result.added == 0:
        await refresh_cmd.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 格式化摘要
    lines = [
        "索引刷新完成 ✅",
        f"新增: {result.added} | 删除: {result.deleted} "
        f"| 去重: {result.deduped} | 无文字移走: {result.no_text_moved} "
        f"| 失败: {len(result.failed)}",
    ]

    if result.failed:
        shown = result.failed[:10]
        lines.append(f"失败文件: {', '.join(shown)}")

    await refresh_cmd.finish("\n".join(lines))
```

- [ ] **Step 2: 语法检查**

```bash
uv run python -m compileall bot/plugins/meme_refresh.py
```

Expected: `Syntax OK`

---

### Task 4: 更新文件树

**Files:**
- Modify: `docs/PRD.md`（第 6 节项目结构）
- Modify: `docs/api/API.md`（目录结构）

- [ ] **Step 1: 更新 PRD.md 项目结构**

在 `docs/PRD.md` 第 6 节的项目结构树中，在 `bot/bot.py` 行之后追加：

```
│   ├── app_state.py           # 共享实例管理（模块级单例）
│   ├── plugins/
│   │   ├── __init__.py
│   │   └── meme_refresh.py    # /refresh 命令
```

- [ ] **Step 2: 更新 API.md 目录结构**

在 `docs/api/API.md` 的目录结构树中追加：

```
├── app_state.md
└── plugins
    └── meme_refresh.md
```

---

### Task 5: 更新 `docs/process.md`

**Files:**
- Modify: `docs/process.md`

- [ ] **Step 1: 追加完成记录**

在文件末尾追加：

```markdown
- [x] `bot/app_state.py` — 共享实例管理模块（模块级单例，`init_app()` 初始化 IndexManager / OcrService / EmbeddingService，`get_*()` 供插件获取，未初始化时 raise RuntimeError）
- [x] `bot/plugins/meme_refresh.py` — /refresh 命令插件（NoneBot2 on_command 注册，AUTHORIZED_USER_IDS 授权校验，全局索引更新锁 acquire_lock / release_lock，调用 sync_with_filesystem() 增量刷新，SyncResult 摘要回复含新增/删除/去重/无文字移走/失败统计，失败文件最多列出前 10 个）
```

---

### Task 6: 更新 `docs/api/API.md`

**Files:**
- Modify: `docs/api/API.md`

- [ ] **Step 1: 追加 app_state 接口说明**

在 `### docs/api/bot/logging_config.md` 之后追加：

```markdown
### `docs/api/bot/app_state.md`

\```python
def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None

def get_index_manager() -> IndexManager
def get_ocr_service() -> DeepSeekOcrService
def get_embedding_service() -> EmbeddingService
\```
```

- [ ] **Step 2: 追加 meme_refresh 说明**

在 app_state 之后追加：

```markdown
### `bot/plugins/meme_refresh.py`

NoneBot2 命令插件，注册 `/refresh` 命令。

- 依赖：`app_state.get_index_manager()`
- 授权：`AUTHORIZED_USER_IDS` 环境变量
- 锁：`IndexManager.acquire_lock()` / `release_lock()`
- 同步：`IndexManager.sync_with_filesystem() -> SyncResult`
```

- [ ] **Step 3: 更新目录结构**

在 API.md 的目录结构中追加：

```
├── app_state.md
└── plugins
    └── meme_refresh.md
```

---

### Task 7: 语法检查与提交

- [ ] **Step 1: 全量语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 全部 `Syntax OK`

- [ ] **Step 2: 提交（需用户审核）**

```bash
git add bot/app_state.py bot/plugins/__init__.py bot/plugins/meme_refresh.py docs/PRD.md docs/process.md docs/api/API.md docs/superpowers/specs/2026-06-23-meme-refresh-design.md docs/superpowers/plans/2026-06-23-meme-refresh.md
git commit -m "feat(plugins): 实现 /refresh 命令插件及 app_state 共享实例管理"
```
