# /add & /search 超时竞态修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 `/add` 和 `/search` 命令中超时任务在 `got` 处理函数执行期间仍存活并发送矛盾消息的竞态问题。

**Architecture:** 在 `PendingSession` 中保存 `asyncio.Task` 引用（`timeout_task`），在 `got_image` / `got_selection` 确认用户有效响应后，通过 `task.cancel()` 直接取消超时任务，防止其在耗时处理（下载图片、OCR 等）期间触发。

**Tech Stack:** Python 3.12, asyncio, NoneBot2

**涉及文件:**
- `bot/session.py` — `PendingSession` 加 `timeout_task` 字段 + 新增 `cancel_timeout_task()` 函数
- `bot/plugins/meme_add.py` — `handle_add` 存 task；`got_image` 两处取消
- `bot/plugins/_search_utils.py` — `execute_search` 多结果分支存 task
- `bot/plugins/meme_search.py` — `got_selection` 两处取消
- `bot/plugins/meme_plain_text.py` — `got_selection` 两处取消

---

### Task 1: `session.py` — PendingSession 增加 timeout_task 字段 + cancel_timeout_task 函数

**Files:**
- Modify: `bot/session.py:23-39` (PendingSession dataclass)
- Modify: `bot/session.py:104-139` (新增 cancel_timeout_task 函数)

- [ ] **Step 1: 给 PendingSession 增加 timeout_task 字段**

`bot/session.py:23-39`：
```python
@dataclass
class PendingSession:
    """待处理会话。

    Attributes:
        matcher: NoneBot2 Matcher 实例。
        cancelled: 是否已被新命令取消。
        type: 命令类型，如 "add" 或 "search"。
        timeout_task: 超时 asyncio.Task 引用，用于在 got 中取消。
    """

    matcher: Matcher
    cancelled: bool = False
    type: str = "add"
    timeout_task: asyncio.Task | None = None
```

- [ ] **Step 2: 新增 cancel_timeout_task 函数**

在 `cancel()` 函数（约第85行）后、`is_cancelled()` 之前，新增：

```python
def cancel_timeout_task(user_id: str) -> None:
    """取消用户会话的超时 asyncio Task。

    在 got 处理函数确认用户已有效响应后调用，
    防止 timeout_session 在后台继续计时并发送超时消息。

    Args:
        user_id: 用户 ID。
    """
    session = pending_sessions.get(user_id)
    if session is not None and session.timeout_task is not None:
        session.timeout_task.cancel()
        session.timeout_task = None
```

- [ ] **Step 3: 语法检查**

Run: `uv run python -m compileall bot/session.py`
Expected: 编译成功无错误

- [ ] **Step 4: Commit**

```bash
git add bot/session.py
git commit -m "fix(session): PendingSession 增加 timeout_task 字段 + cancel_timeout_task 函数"
```

---

### Task 2: `meme_add.py` — handle_add 保存 timeout_task 引用

**Files:**
- Modify: `bot/plugins/meme_add.py:106-114` (handle_add 超时任务部分)

- [ ] **Step 1: 保存 timeout_task 引用**

`bot/plugins/meme_add.py:104-114` 原代码：
```python
    # 启动超时任务
    asyncio.create_task(
        timeout_session(
            bot,
            event,
            user_id,
            "添加已取消，请重新 /add",
            on_cleanup=lambda: _release_lock_safe(index_manager),
        )
    )
```

改为：
```python
    # 启动超时任务
    task = asyncio.create_task(
        timeout_session(
            bot,
            event,
            user_id,
            "添加已取消，请重新 /add",
            on_cleanup=lambda: _release_lock_safe(index_manager),
        )
    )
    # 保存 task 引用到 session，供 got_image 取消
    session = pending_sessions.get(user_id)
    if session is not None:
        session.timeout_task = task
```

需要在文件顶部 import 中添加 `pending_sessions`：
```python
from bot.session import (
    cancel,
    cancel_timeout_task,
    check_and_cancel,
    is_cancelled,
    pending_sessions,
    register,
    timeout_session,
)
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/plugins/meme_add.py`
Expected: 编译成功无错误

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/meme_add.py
git commit -m "fix(add): handle_add 保存 timeout_task 引用到 PendingSession"
```

---

### Task 3: `meme_add.py` — got_image 中取消超时任务

**Files:**
- Modify: `bot/plugins/meme_add.py:137-154` (got_image 开头部分)

- [ ] **Step 1: is_cancelled 分支中取消超时任务 + 正常路径取消超时任务**

`bot/plugins/meme_add.py:152-164` 原代码：
```python
    # 会话有效性检查
    if is_cancelled(user_id):
        return

    index_manager: IndexManager | None = None

    try:
        # 获取 IndexManager
        try:
            index_manager = get_index_manager()
        except RuntimeError:
            return

        # ── 阶段 2：处理流程（finally 统一释放锁）──
```

改为：
```python
    # 会话有效性检查
    if is_cancelled(user_id):
        cancel_timeout_task(user_id)
        return

    # 用户已发送有效图片，处理开始前取消超时任务
    cancel_timeout_task(user_id)

    index_manager: IndexManager | None = None

    try:
        # 获取 IndexManager
        try:
            index_manager = get_index_manager()
        except RuntimeError:
            return

        # ── 阶段 2：处理流程（finally 统一释放锁）──
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/plugins/meme_add.py`
Expected: 编译成功无错误

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/meme_add.py
git commit -m "fix(add): got_image 中取消超时任务防止竞态触发"
```

---

### Task 4: `_search_utils.py` — execute_search 保存 timeout_task 引用

**Files:**
- Modify: `bot/plugins/_search_utils.py:128-133` (execute_search 多结果分支)

- [ ] **Step 1: 保存 timeout_task 引用**

在文件顶部 import 中增加：
```python
from bot.session import cancel, pending_sessions, register, timeout_session
```

`bot/plugins/_search_utils.py:128-133` 原代码：
```python
    await cmd_matcher.send("\n".join(lines))

    # 启动超时任务
    asyncio.create_task(timeout_session(bot, event, user_id, "选择已过期，请重新搜索"))
```

改为：
```python
    await cmd_matcher.send("\n".join(lines))

    # 启动超时任务
    task = asyncio.create_task(
        timeout_session(bot, event, user_id, "选择已过期，请重新搜索")
    )
    # 保存 task 引用到 session，供 got_selection 取消
    session = pending_sessions.get(user_id)
    if session is not None:
        session.timeout_task = task
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: 编译成功无错误

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/_search_utils.py
git commit -m "fix(search): execute_search 保存 timeout_task 引用到 PendingSession"
```

---

### Task 5: `meme_search.py` — got_selection 中取消超时任务

**Files:**
- Modify: `bot/plugins/meme_search.py:82-96` (got_selection)

- [ ] **Step 1: got_selection 中增加 cancel_timeout_task**

import 中增加：
```python
from bot.session import cancel, cancel_timeout_task, check_and_cancel, is_cancelled
```

`bot/plugins/meme_search.py:84-100` 原代码：
```python
    try:
        if is_cancelled(user_id):
            return

        candidates = matcher.state.get("candidates", [])
        text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, text)
        if isinstance(result, str):
            await matcher.reject(result)
            return

        cancel(user_id)
        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
```

改为：
```python
    try:
        if is_cancelled(user_id):
            cancel_timeout_task(user_id)
            return

        candidates = matcher.state.get("candidates", [])
        text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, text)
        if isinstance(result, str):
            await matcher.reject(result)
            return

        # 用户已有效选择，取消超时任务
        cancel_timeout_task(user_id)

        cancel(user_id)
        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/plugins/meme_search.py`
Expected: 编译成功无错误

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/meme_search.py
git commit -m "fix(search): got_selection 中取消超时任务防止竞态触发"
```

---

### Task 6: `meme_plain_text.py` — got_selection 中取消超时任务

**Files:**
- Modify: `bot/plugins/meme_plain_text.py:69-96` (got_selection)

- [ ] **Step 1: got_selection 中增加 cancel_timeout_task**

import 中增加：
```python
from bot.session import cancel, cancel_timeout_task, check_and_cancel, is_cancelled
```

`bot/plugins/meme_plain_text.py:82-96` 原代码：
```python
    if is_cancelled(user_id):
        return

    candidates = matcher.state.get("candidates", [])

    text = selection_msg.extract_plain_text().strip()
    result = handle_selection(matcher, candidates, text)

    if isinstance(result, str):
        await catch_all.reject(result)
        return

    cancel(user_id)
    image_path = MEMES_DIR / result.filename
    await catch_all.finish(MessageSegment.image("file://" + str(image_path.resolve())))
```

改为：
```python
    if is_cancelled(user_id):
        cancel_timeout_task(user_id)
        return

    candidates = matcher.state.get("candidates", [])

    text = selection_msg.extract_plain_text().strip()
    result = handle_selection(matcher, candidates, text)

    if isinstance(result, str):
        await catch_all.reject(result)
        return

    # 用户已有效选择，取消超时任务
    cancel_timeout_task(user_id)

    cancel(user_id)
    image_path = MEMES_DIR / result.filename
    await catch_all.finish(MessageSegment.image("file://" + str(image_path.resolve())))
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/plugins/meme_plain_text.py`
Expected: 编译成功无错误

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/meme_plain_text.py
git commit -m "fix(plain_text): got_selection 中取消超时任务防止竞态触发"
```

---

### Task 7: 全量语法检查 + 运行测试

- [ ] **Step 1: 全量编译检查**

Run: `uv run python -m compileall bot`
Expected: 全部文件编译成功

- [ ] **Step 2: 运行现有测试**

Run: `uv run pytest -v`
Expected: 已有测试全部通过（或与变更无关的预期失败）

- [ ] **Step 3: 提交最终调整（如有）**

```bash
git add -A
git commit -m "fix: 全量语法检查通过"
```
