# 设计文档：启动时索引同步非阻塞化

> 日期：2026-06-27
> 状态：待实现

## 问题描述

当前 `_on_startup()` 中 `sync_with_filesystem()` 是同步阻塞的。100 张图片首次同步约需 10 分钟（OCR API 调用约 3s/张），期间 Bot 完全无法接受 WebSocket 连接或响应消息。

## 设计目标

1. Bot 启动后立即可接受 WebSocket 连接
2. 已有索引时，`/search`、`/ai` 可立即响应（用旧数据）
3. 同步期间，命令自动回复"索引正在更新，请稍后再试"
4. 同步失败时，Bot 继续运行（用已有索引），记录错误日志
5. 改动范围最小，复用现有 `is_locked` 机制

## 启动流程

**当前流程（阻塞）：**
```
_on_startup()
  ├─ setup_logging
  ├─ 创建 engine 服务
  ├─ IndexManager.load()          ← 快
  ├─ await sync_with_filesystem() ← 慢，阻塞 10 分钟
  ├─ 创建 AIMatcher/KeywordSearcher
  └─ init_app()                   ← Bot 此时才可用
```

**新流程（非阻塞）：**
```
_on_startup()
  ├─ setup_logging
  ├─ 创建 engine 服务
  ├─ IndexManager.load()          ← 快，加载已有索引
  ├─ 创建 AIMatcher/KeywordSearcher
  ├─ init_app()                   ← Bot 立即可用（用已有索引）
  └─ create_task(_background_sync())  ← 后台运行，不阻塞
```

## 后台同步任务

新增 `_background_sync()` 函数：

```python
async def _background_sync(index_manager: IndexManager) -> None:
    """后台索引同步任务，不阻塞启动。

    同步期间 is_locked = True（sync 内部获取锁），
    插件层自动回复"索引正在更新"。
    同步失败时记录错误日志，Bot 继续运行。
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

## 同步期间的行为

| 命令 | 行为 |
|------|------|
| `/help` | 正常响应（不检查锁） |
| 普通文本 | 正常响应（等同 /help） |
| `/search` | 检查 `is_locked` → 回复"索引正在更新，请稍后再试" |
| `/ai` | 检查 `is_locked` → 回复"索引正在更新，请稍后再试" |
| `/add` | `acquire_lock()` 返回 False → 回复"索引正在更新，请稍后再试" |
| `/refresh` | `acquire_lock()` 返回 False → 回复"索引正在更新，请稍后再试" |

## 边界情况

| 场景 | 当前行为 | 新行为 |
|------|---------|--------|
| 首次启动（无 index.json） | 阻塞同步完成才可用 | load() 初始化空索引 → Bot 可用 → 后台同步 |
| 已有索引 + 新增图片 | 阻塞同步完成才可用 | Bot 立即可用（旧索引）→ 后台同步更新 |
| 同步失败 | 阻止 Bot 启动 | 记录错误日志，Bot 继续运行 |
| 同步期间用户发 /search | 不会发生（Bot 未启动） | 检查 is_locked → 回复"正在更新" |
| 索引文件损坏 | 阻止启动 | 阻止启动（load() 抛 IndexCorruptedError） |

## 错误处理

1. **同步失败**（API 不可用、网络异常等）
   - 记录 `logger.exception()` 错误日志
   - Bot 继续运行（用已有索引）
   - 用户可手动 `/refresh` 重试

2. **部分图片失败**（单张图片 OCR/Embedding 失败）
   - `sync_with_filesystem()` 内部已处理：跳过失败图片，记入 `result.failed`
   - `_background_sync()` 记录失败文件列表
   - 其他图片正常同步

3. **索引文件损坏**
   - `load()` 抛 `IndexCorruptedError` → 阻止启动（这是合理的）
   - `embeddings.json` 损坏 → 置空，由 sync 重建

## 实现变更

**改动范围：仅 `bot/bot.py`**

1. 添加 `import asyncio`
2. 新增 `_background_sync()` 函数
3. 修改 `_on_startup()` 函数：
   - 将步骤 4（sync）移到最后，改为 `asyncio.create_task()`
   - 将步骤 5-6（创建服务、init_app）移到 sync 之前

**不需要改动的文件：**
- `bot/engine/index_manager.py` — 无需修改
- `bot/plugins/*.py` — 无需修改（已有 is_locked 检查）
- `bot/app_state.py` — 无需修改

## 运维观察

```bash
docker compose logs -f bot
# 输出示例：
# MemePilot 正在启动...
# index.json 加载成功，共 50 条记录
# MemePilot 启动完成，后台索引同步进行中...
# 开始后台索引同步...
# 开始并行处理 3 张新增图片，并发上限 5
# 新增图片已加入索引: id=51, filename=新图1.jpg
# 后台索引同步完成: 新增=2, 删除=0, 去重=0, 无文字移走=0, 失败=0
```
