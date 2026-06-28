# /add 与 /search 超时竞态修复方案

> 日期：2026-06-28
> 状态：待审阅

---

## 问题描述

`/add` 和 `/search` 命令存在超时竞态：当用户已有效响应（发送图片或选择编号）、`got_image` / `got_selection` 已经开始执行后，后台的 `timeout_session` 异步任务仍然存活并继续计时，可能在 `got` 函数处理期间（如下载图片、OCR 识别等耗时操作）触发，发送矛盾的"操作超时"消息，之后 `got` 函数又发送"操作成功"消息。

## 根因

1. `handle_add` / `execute_search` 中用 `asyncio.create_task` 启动了 `timeout_session` 超时任务，但**没有保存 Task 引用**，导致无法后续取消。
2. `got_image` / `got_selection` 中**没有任何代码**去取消或停止该超时任务，超时任务在整个 `got` 函数执行期间持续运行。
3. `timeout_session` 仅通过 `is_cancelled()` 和 `user_id in pending_sessions` 判断会话是否已结束，但 `got` 函数直到 `finally` 块才标记会话结束，存在时间窗口。

## 方案选择

**方案 B**（已审批通过）：在 `PendingSession` 中跟踪 `asyncio.Task` 引用，`got` 函数确认用户有效响应后直接 `task.cancel()`。

## 设计

### 数据模型变更

**`bot/session.py` — `PendingSession` 增加字段：**

```python
@dataclass
class PendingSession:
    matcher: Matcher
    cancelled: bool = False
    type: str = "add"
    timeout_task: asyncio.Task | None = None
```

### 新增函数

**`bot/session.py` — `cancel_timeout_task()`：**

```python
def cancel_timeout_task(user_id: str) -> None:
```

从 `pending_sessions` 中取出用户的 `PendingSession`，若 `timeout_task` 不为 `None` 则调用 `task.cancel()` 并置空。

### 修改点

#### 1. `bot/plugins/meme_add.py`

- **`handle_add()`**：调用 `asyncio.create_task(timeout_session(...))` 后，将返回的 task 存入 `pending_sessions[user_id].timeout_task`
- **`got_image()`**：两处新增 `cancel_timeout_task(user_id)`：
  - `is_cancelled` 分支中（被新命令覆盖时清理超时任务）
  - 会话有效性检查通过后、耗时处理开始前（防止处理期间超时触发）

#### 2. `bot/plugins/_search_utils.py`

- **`execute_search()`**：多结果分支创建 `timeout_session` 后，将 task 存入 `pending_sessions[user_id].timeout_task`

#### 3. `bot/plugins/meme_search.py`

- **`got_selection()`**：两处新增 `cancel_timeout_task(user_id)`：
  - `is_cancelled` 分支中
  - `handle_selection` 返回有效 `SearchResult` 后、发送图片前

#### 4. `bot/plugins/meme_plain_text.py`

- **`got_selection()`**：与 `meme_search.py` 相同，两处新增 `cancel_timeout_task(user_id)`：
  - `is_cancelled` 分支中
  - `handle_selection` 返回有效 `SearchResult` 后、发送图片前

### 不受影响的路径

| 场景 | 说明 |
|------|------|
| 用户不回复（真正超时） | `timeout_session` 正常触发 |
| 非图片消息（reject） | `got_image` 中不走到 `cancel_timeout_task`，超时保留 |
| 无效编号（reject） | `got_selection` 中不取消超时，会话保留 |
| 锁管理 | 锁释放仍由 `got_image` 的 finally 块管理 |
| 会话取消 | `cancel(user_id)` 保持不变 |

### 边界情况

| 场景 | 预期 |
|------|------|
| 正常流程：用户按时发图/选择 | `cancel_timeout_task` → task 被 cancel → 正常处理 |
| 超时：用户始终不回复 | `timeout_session` 正常触发 |
| 被新命令覆盖 → `is_cancelled` | `cancel_timeout_task` → return |
| `task.cancel()` 对已完成 task | 安全，asyncio 定义为 no-op |
| `handle_selection` 返回错误消息 | reject → 不 cancel task → 会话保留 |
