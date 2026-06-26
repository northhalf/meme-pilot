# 启动时索引同步非阻塞化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `_on_startup()` 中的 `sync_with_filesystem()` 从阻塞调用改为后台任务，使 Bot 启动后立即可用。

**Architecture:** 仅修改 `bot/bot.py`，将 `init_app()` 移到 `sync_with_filesystem()` 之前调用，同步任务通过 `asyncio.create_task()` 在后台执行。同步期间 `is_locked = True`（sync 内部获取锁），插件层已有检查会自动回复"索引正在更新"。

**Tech Stack:** Python asyncio, NoneBot2

---

## File Structure

- Modify: `bot/bot.py` — 唯一需要修改的文件

不需要修改的文件：
- `bot/engine/index_manager.py` — sync_with_filesystem() 内部已有锁机制
- `bot/plugins/*.py` — 已有 is_locked 检查
- `bot/app_state.py` — 无需修改

---

### Task 1: 修改 bot/bot.py 实现后台同步

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: 添加 asyncio import**

在 `bot/bot.py` 顶部添加 `import asyncio`：

```python
import asyncio
import logging
import os
```

- [ ] **Step 2: 新增 _background_sync() 函数**

在 `_read_bot_port()` 之后、`_on_startup()` 之前添加：

```python
async def _background_sync(index_manager: IndexManager) -> None:
    """后台索引同步任务，不阻塞启动。

    同步期间 is_locked = True（sync 内部获取锁），
    插件层自动回复"索引正在更新"。
    同步失败时记录错误日志，Bot 继续运行。

    Args:
        index_manager: 已加载索引的 IndexManager 实例。
    """
    logger.info("开始后台索引同步...")
    try:
        result = await index_manager.sync_with_filesystem()
        logger.info(
            "后台索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
            result.added,
            result.deleted,
            result.deduped,
            result.no_text_moved,
            len(result.failed),
        )
        if result.failed:
            logger.warning("同步失败文件（前 10 个）: %s", result.failed[:10])
    except Exception:
        logger.exception("后台索引同步失败，Bot 继续运行（用已有索引）")
```

- [ ] **Step 3: 修改 _on_startup() 函数**

将 `_on_startup()` 改为以下内容（关键变化：init_app 移到 sync 之前，sync 改为后台任务）：

```python
async def _on_startup() -> None:
    """NoneBot2 启动钩子 — 初始化引擎服务，后台执行首次索引同步。

    流程：
    1. 配置日志
    2. 创建 OCR / Embedding / Rerank / ImageOptimizer 服务
    3. 创建 IndexManager 并加载现有索引
    4. 创建 AIMatcher / KeywordSearcher
    5. 注册到 app_state 供插件获取（Bot 立即可用）
    6. 后台执行 sync_with_filesystem()（不阻塞启动）

    同步期间 is_locked = True，插件层自动回复"索引正在更新"。
    同步失败时记录错误日志，Bot 继续运行（用已有索引）。
    """
    # 1. 日志
    setup_logging("log")
    logger.info("MemePilot 正在启动...")

    # 2. 创建引擎服务（各服务从环境变量读取配置）
    ocr_service = DeepSeekOcrService()
    embedding_service = EmbeddingService()
    rerank_service = RerankService()
    image_optimizer = ImageOptimizer()

    # 3. 创建 IndexManager 并加载索引
    data_dir = str(PROJECT_ROOT / "data")
    memes_dir = str(MEMES_DIR)
    sync_concurrency = _read_sync_concurrency()

    index_manager = IndexManager(
        data_dir=data_dir,
        memes_dir=memes_dir,
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        sync_concurrency=sync_concurrency,
        optimizer=image_optimizer,
    )
    index_manager.load()

    # 4. 创建搜索和匹配服务（可立即使用已有索引）
    ai_matcher = AIMatcher(
        index_provider=index_manager,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )
    keyword_searcher = KeywordSearcher(index_provider=index_manager)

    # 5. 注册到 app_state（Bot 立即可用）
    init_app(
        index_manager=index_manager,
        ocr_service=ocr_service,
        embedding_service=embedding_service,
        image_optimizer=image_optimizer,
        ai_matcher=ai_matcher,
        keyword_searcher=keyword_searcher,
    )
    logger.info("MemePilot 启动完成，后台索引同步进行中...")

    # 6. 后台执行首次索引同步（不阻塞启动）
    asyncio.create_task(_background_sync(index_manager))
```

- [ ] **Step 4: 运行 compileall 语法检查**

Run: `uv run python -m compileall bot`
Expected: 无错误输出

- [ ] **Step 5: 运行现有测试确保不破坏**

Run: `uv run pytest tests/ -v`
Expected: 所有测试通过

- [ ] **Step 6: 提交**

```bash
git add bot/bot.py
git commit -m "feat(bot): 启动时索引同步改为后台任务，Bot 立即可用"
```
