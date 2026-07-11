# Plugins 日志输出增强与日志文件大小调整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `bot/logging_config.py` 的日志文件大小调整为 10MB 并保留 3 个备份，同时在 `bot/plugins/*.py` 关键流程中补充 `debug`/`info` 级别的运行日志。

**Architecture：** 保持现有"顶层 `bot` logger + 子 logger 继承"的架构不变；新增日志全部位于现有 `set_request_id()` 上下文中，自动复用 `[req:xxx]` 前缀。

**Tech Stack：** Python 3.12、标准库 `logging`、pytest

---

## File Map

| 文件 | 动作 | 职责 |
|---|---|---|
| `bot/logging_config.py` | 修改 | `RotatingFileHandler.maxBytes=10_485_760`，`backupCount=3` |
| `tests/unit/test_logging_config.py` | 新建或修改 | 验证 handler 的 `maxBytes` 与 `backupCount` |
| `bot/plugins/meme_add.py` | 修改 | 补充 `/add` 流程日志 |
| `bot/plugins/meme_delete.py` | 修改 | 补充 `/del` 流程日志 |
| `bot/plugins/meme_edit.py` | 修改 | 补充 `/edittext` 流程日志 |
| `bot/plugins/meme_addtag.py` | 修改 | 补充 `/addtag` 流程日志 |
| `bot/plugins/meme_setspeaker.py` | 修改 | 补充 `/setspeaker` 流程日志 |
| `bot/plugins/meme_refresh.py` | 修改 | 补充 `/refresh` 流程日志 |
| `bot/plugins/meme_search.py` | 修改 | 补充 `/search` 流程日志 |
| `bot/plugins/meme_query.py` | 修改 | 补充 `/query` 流程日志 |
| `bot/plugins/meme_rand.py` | 修改 | 补充 `/rand` 流程日志 |
| `bot/plugins/meme_sim.py` | 修改 | 补充 `/sim` 流程日志 |
| `bot/plugins/meme_ai.py` | 修改 | 补充 `/ai` 流程日志 |
| `bot/plugins/meme_info.py` | 修改 | 补充 `/info` 流程日志 |
| `bot/plugins/meme_cancel.py` | 修改 | 补充 `/cancel` 流程日志 |
| `bot/plugins/meme_plain_text.py` | 修改 | 补充兜底文本处理日志 |

---

### Task 1: 调整日志文件大小与备份数

**Files:**
- Modify: `bot/logging_config.py:37-42`
- Test: `tests/unit/test_logging_config.py`（新建）

- [ ] **Step 1: 写 failing 测试**

在 `tests/unit/test_logging_config.py` 写入：

```python
"""bot/logging_config.py 单元测试。"""

import logging
from logging.handlers import RotatingFileHandler

from bot.logging_config import setup_logging


def test_log_handler_size_and_backup_count():
    """RotatingFileHandler 应为 10MB 并保留 3 个备份。"""
    setup_logging(log_dir="log_test")
    bot_logger = logging.getLogger("bot")
    file_handlers = [
        h for h in bot_logger.handlers
        if isinstance(h, RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert handler.maxBytes == 10_485_760
    assert handler.backupCount == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_logging_config.py -v`
Expected: FAIL with `AssertionError`（当前 `maxBytes` 为 1_048_576，`backupCount` 为 1）

- [ ] **Step 3: 修改 `bot/logging_config.py`**

将 `bot/logging_config.py` 中的：

```python
    file_handler = RotatingFileHandler(
        _log_dir / "bot.log",
        maxBytes=1_048_576,
        backupCount=1,
        encoding="utf-8",
    )
```

改为：

```python
    file_handler = RotatingFileHandler(
        _log_dir / "bot.log",
        maxBytes=10_485_760,
        backupCount=3,
        encoding="utf-8",
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_logging_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/logging_config.py tests/unit/test_logging_config.py
git commit -m "feat(logging): 日志文件大小调整为 10MB，保留 3 个备份"
```

---

### Task 2: 补充 `/add` 流程日志

**Files:**
- Modify: `bot/plugins/meme_add.py:85-244`

- [ ] **Step 1: 在参数解析后补充 debug 日志**

在 `bot/plugins/meme_add.py` 的 `handle_add` 函数中，解析 `speaker` 和 `tags` 后添加：

```python
            logger.debug("/add 参数: speaker=%r, tags=%r", speaker, tags)
```

- [ ] **Step 2: 在图片验证后补充 debug 日志**

在 `got_image` 函数中，获取 `image_url` 后、下载前添加：

```python
                logger.debug("/add 收到图片 URL: %r, 扩展名: %r", image_url, ext)
```

- [ ] **Step 3: 在保存图片后补充 info 日志**

在 `filepath.write_bytes(image_data)` 成功后添加：

```python
                logger.info("图片已保存: %s", filename)
```

- [ ] **Step 4: 在 `index_manager.add()` 成功后补充 info 日志**

在 `else` 分支中、`session_manager.deactivate_chat(user_id)` 之前添加：

```python
                    logger.info(
                        "/add 成功: entry_id=%s, reason=%s", result.entry_id, result.reason
                    )
```

- [ ] **Step 5: Commit**

```bash
git add bot/plugins/meme_add.py
git commit -m "feat(plugins): 补充 /add 流程 debug/info 日志"
```

---

### Task 3: 补充 `/del` 流程日志

**Files:**
- Modify: `bot/plugins/meme_delete.py:80-209`

- [ ] **Step 1: 在参数解析后补充 debug 日志**

在 `handle_delete` 函数中，`entry_ids = list(dict.fromkeys(entry_ids))` 之后添加：

```python
            logger.debug("/del 目标 entry_ids: %s", entry_ids)
```

- [ ] **Step 2: 在查询结果后补充 debug 日志**

在 `for eid in entry_ids:` 循环结束后添加：

```python
            logger.debug("/del 找到 %d 条，未找到 %d 条", len(found), len(not_found_ids))
```

- [ ] **Step 3: 在删除完成后补充 info 日志**

在 `got_confirm` 函数的 `else` 分支中，构造 `lines` 之前添加：

```python
                        logger.info(
                            "/del 完成: 成功=%s, 未找到=%s, 失败=%s",
                            result.deleted_ids,
                            result.not_found_ids,
                            result.failed_ids,
                        )
```

- [ ] **Step 4: 在用户取消删除时补充 info 日志**

在 `got_confirm` 函数的 `else`（取消）分支中，`session_manager.deactivate_chat(user_id)` 之前添加：

```python
                    logger.info("用户 %s 取消 /del", user_id)
```

- [ ] **Step 5: Commit**

```bash
git add bot/plugins/meme_delete.py
git commit -m "feat(plugins): 补充 /del 流程 debug/info 日志"
```

---

### Task 4: 补充 `/edittext` 流程日志

**Files:**
- Modify: `bot/plugins/meme_edit.py`

- [ ] **Step 1: 在参数解析后补充 debug 日志**

在 `handle_edit` 中，解析出 `entry_id` 和 `new_text` 后添加：

```python
            logger.debug("/edittext 参数: entry_id=%s, new_text=%r", entry_id, new_text)
```

- [ ] **Step 2: 在编辑成功后补充 info 日志**

在确认处理函数中，调用 `index_manager.edit_text()` 成功后添加：

```python
                    logger.info("/edittext 成功: entry_id=%s", entry_id)
```

- [ ] **Step 3: 在用户取消时补充 info 日志**

在确认处理的取消分支中添加：

```python
                    logger.info("用户 %s 取消 /edittext", user_id)
```

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_edit.py
git commit -m "feat(plugins): 补充 /edittext 流程 debug/info 日志"
```

---

### Task 5: 补充 `/addtag` 流程日志

**Files:**
- Modify: `bot/plugins/meme_addtag.py`

- [ ] **Step 1: 在参数解析后补充 debug 日志**

在 `handle_addtag` 中，解析出 `entry_id` 和 `tags` 后添加：

```python
            logger.debug("/addtag 参数: entry_id=%s, tags=%r", entry_id, tags)
```

- [ ] **Step 2: 在追加成功后补充 info 日志**

在确认处理成功后添加：

```python
                    logger.info("/addtag 成功: entry_id=%s, tags=%r", entry_id, tags)
```

- [ ] **Step 3: 在用户取消时补充 info 日志**

在取消分支中添加：

```python
                    logger.info("用户 %s 取消 /addtag", user_id)
```

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_addtag.py
git commit -m "feat(plugins): 补充 /addtag 流程 debug/info 日志"
```

---

### Task 6: 补充 `/setspeaker` 流程日志

**Files:**
- Modify: `bot/plugins/meme_setspeaker.py`

- [ ] **Step 1: 在参数解析后补充 debug 日志**

在 `handle_setspeaker` 中，解析出 `entry_id` 和 `speaker` 后添加：

```python
            logger.debug("/setspeaker 参数: entry_id=%s, speaker=%r", entry_id, speaker)
```

- [ ] **Step 2: 在设置成功后补充 info 日志**

在确认处理成功后添加：

```python
                    logger.info("/setspeaker 成功: entry_id=%s, speaker=%r", entry_id, speaker)
```

- [ ] **Step 3: 在用户取消时补充 info 日志**

在取消分支中添加：

```python
                    logger.info("用户 %s 取消 /setspeaker", user_id)
```

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_setspeaker.py
git commit -m "feat(plugins): 补充 /setspeaker 流程 debug/info 日志"
```

---

### Task 7: 补充 `/refresh` 流程日志

**Files:**
- Modify: `bot/plugins/meme_refresh.py`

- [ ] **Step 1: 在刷新完成后补充 info 日志**

在 `index_manager.refresh()` 调用成功后添加：

```python
                logger.info("用户 %s 的 /refresh 完成", user_id)
```

若 `SyncResult` 返回统计信息，可同时记录：

```python
                logger.info(
                    "/refresh 统计: 新增=%d, 删除=%d, 替换=%d",
                    result.added or 0,
                    result.deleted or 0,
                    result.replaced or 0,
                )
```

- [ ] **Step 2: Commit**

```bash
git add bot/plugins/meme_refresh.py
git commit -m "feat(plugins): 补充 /refresh 流程 info 日志"
```

---

### Task 8: 补充搜索/查询类命令日志

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `bot/plugins/meme_query.py`
- Modify: `bot/plugins/meme_rand.py`
- Modify: `bot/plugins/meme_sim.py`
- Modify: `bot/plugins/meme_ai.py`

- [ ] **Step 1: `/search` 日志**

在 `bot/plugins/meme_search.py` 中：

```python
# 在关键词解析后
logger.debug("/search 关键词: %r", keyword)

# 在 dispatch_search_results 调用前
logger.info("/search 结果数: %d", len(results))
```

- [ ] **Step 2: `/query` 日志**

在 `bot/plugins/meme_query.py` 中：

```python
# 在解析 keyword/speakers/tags 后
logger.debug("/query 参数: keyword=%r, speakers=%s, tags=%s", keyword, speakers, tags)

# 在 dispatch_search_results 调用前
logger.info("/query 结果数: %d", len(results))
```

- [ ] **Step 3: `/rand` 日志**

在 `bot/plugins/meme_rand.py` 中：

```python
# 在关键词解析后
logger.debug("/rand 关键词: %r", keyword)

# 在发送结果前
logger.info("/rand 发送结果数: %d", len(results))
```

- [ ] **Step 4: `/sim` 日志**

在 `bot/plugins/meme_sim.py` 中：

```python
# 在 description 解析后
logger.debug("/sim 描述: %r", description)

# 在 dispatch_search_results 调用前
logger.info("/sim 召回结果数: %d", len(results))
```

- [ ] **Step 5: `/ai` 日志**

在 `bot/plugins/meme_ai.py` 中：

```python
# 在 description 解析后
logger.debug("/ai 描述: %r", description)

# 在 AIMatcher.match 成功后
logger.info("/ai 命中 entry_id=%s", result.entry_id)
```

- [ ] **Step 6: Commit**

```bash
git add bot/plugins/meme_search.py bot/plugins/meme_query.py bot/plugins/meme_rand.py bot/plugins/meme_sim.py bot/plugins/meme_ai.py
git commit -m "feat(plugins): 补充搜索/查询类命令 debug/info 日志"
```

---

### Task 9: 补充 `/info`、`/cancel` 与兜底文本日志

**Files:**
- Modify: `bot/plugins/meme_info.py`
- Modify: `bot/plugins/meme_cancel.py`
- Modify: `bot/plugins/meme_plain_text.py`

- [ ] **Step 1: `/info` 日志**

在 `bot/plugins/meme_info.py` 中，组装返回信息前添加：

```python
            logger.debug("/info 条目数=%d, speakers=%s", count, speakers)
```

- [ ] **Step 2: `/cancel` 日志**

在 `bot/plugins/meme_cancel.py` 中，成功取消后添加：

```python
            logger.info("用户 %s 的 /cancel 成功", user_id)
```

- [ ] **Step 3: 普通文本兜底日志**

在 `bot/plugins/meme_plain_text.py` 中：

```python
# 在命中帮助旁路判断后
logger.debug("普通文本命中帮助旁路")

# 在兜底搜索分支
logger.debug("普通文本作为 /search 处理: %r", text)
```

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_info.py bot/plugins/meme_cancel.py bot/plugins/meme_plain_text.py
git commit -m "feat(plugins): 补充 /info、/cancel 与兜底文本日志"
```

---

### Task 10: 运行测试与静态检查

**Files:**
- 所有已修改文件

- [ ] **Step 1: 运行 pytest**

Run: `uv run pytest tests/unit -q`
Expected: 全部通过（包括 `test_logging_config.py`）

- [ ] **Step 2: 运行 pyright**

Run: `uv run pyright bot/plugins bot/logging_config.py`
Expected: 无新增类型错误

- [ ] **Step 3: 运行 ruff**

Run: `uv run ruff check bot/plugins bot/logging_config.py`
Expected: 无新增 lint 错误

- [ ] **Step 4: Commit（如仅有格式化变更）**

```bash
git commit -m "style: 日志增强代码格式化"
```

---

## 集成验证

1. 启动 Bot：`docker compose up -d bot` 或 `uv run python bot/bot.py`
2. 检查 `log/bot.log` 是否正常写入。
3. 触发 `/search`、`/add`、`/del`、`/ai` 等命令，确认日志中出现新增 `debug`/`info` 记录，并带有 `[req:xxx]` 前缀。
4. 手动生成超过 10MB 日志（或通过脚本），验证 `bot.log.1`、`bot.log.2`、`bot.log.3` 出现。

---

## Self-Review

### Spec Coverage

- `maxBytes=10MB`：Task 1 Step 3 覆盖。
- `backupCount=3`：Task 1 Step 3 覆盖。
- `/add` 日志增强：Task 2 覆盖。
- `/del` 日志增强：Task 3 覆盖。
- `/edittext` 日志增强：Task 4 覆盖。
- `/addtag` 日志增强：Task 5 覆盖。
- `/setspeaker` 日志增强：Task 6 覆盖。
- `/refresh` 日志增强：Task 7 覆盖。
- 搜索/查询类日志增强：Task 8 覆盖。
- `/info`、`/cancel`、普通文本日志增强：Task 9 覆盖。
- 测试与静态检查：Task 10 覆盖。

无遗漏。

### Placeholder Scan

- 无 `TBD`、`TODO`、"实现 later"、"添加适当的..." 等表述。
- 每个 Step 均包含具体代码或命令。

### Type Consistency

- 全部使用现有 `logger = logging.getLogger(__name__)`，签名一致。
- 全部在现有 `set_request_id()` 上下文中，无需修改 request_id 相关 API。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-11-plugins-logging-output-plan.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints

Which approach?
