# bot/session.py — 共享会话管理模块

> 管理用户的聊天会话（ChatSession）和选择会话（SelectionSession），
> 支持 /cancel 和 /help 在任何状态下旁路触发。

## 数据类

### ChatSession

```python
@dataclass
class ChatSession:
    session_id: str                    # UUID，首次创建时永久固定
    active: bool = False               # True 表示有命令正在处理
    command_type: str | None = None    # "add" / "search" / "ai" / "refresh"
    matcher: Matcher | None = None     # 当前命令的 NoneBot2 Matcher
    current_task: asyncio.Task | None = None  # 异步任务引用
```

### SelectionSession

```python
@dataclass
class SelectionSession:
    selection_id: str                  # UUID，每次创建选择时生成
    timeout_task: asyncio.Task | None = None  # 超时监控任务引用
```

## 模块级变量

```python
chat_sessions: dict[str, ChatSession]          # user_id → ChatSession
selection_sessions: dict[str, SelectionSession]  # user_id → SelectionSession
```

## 函数

### `get_or_create_chat(user_id) -> ChatSession`

首次访问时创建并存储 ChatSession，之后复用。

- `user_id` — 用户 ID

### `activate_chat(user_id, command_type, matcher) -> bool`

激活聊天会话。

- 设置 `active=True`, `matcher`, `command_type`, `current_task=asyncio.current_task()`
- 返回 `True` 表示成功激活
- 返回 `False` 表示已有活跃会话（调用方应拒绝新命令）
- 注意：NoneBot2 的 `handle()` 和 `got()` 运行在不同 asyncio task 中，
  各自的 handler 入口都需要调用 `activate_chat` 更新 `current_task`

### `deactivate_chat(user_id) -> None`

重置聊天会话为空闲。`active=False`, `command_type=None`, `matcher=None`, `current_task=None`。

### `create_selection(user_id, selection_id, timeout_task) -> None`

创建选择会话。覆盖同一用户的旧选择会话。

- `selection_id` — UUID 字符串
- `timeout_task` — 超时监控任务引用

### `remove_selection(user_id) -> SelectionSession | None`

移除选择会话，返回旧会话（用于取消 timeout_task）。不存在时返回 None。

### `get_selection(user_id) -> SelectionSession | None`

查询用户的选择会话。不存在时返回 None。

### `execute_cancel(user_id) -> str | None`

执行取消逻辑。

1. 检查是否有活跃会话，无则返回 None
2. `current_task.cancel()`（非当前 task 且未完成时）
3. `remove_selection()` + 取消 `timeout_task`
4. 在旧 matcher 上 `finish()`（发送"会话已取消"到原上下文）
5. `deactivate_chat(user_id)`

**返回：** 成功时返回 `"已取消 ✅"`，无活跃会话返回 `None`（调用方处理提示）。

**自取消保护：** 当 `current_task is asyncio.current_task()`（同频道 /cancel）时跳过 `cancel()` 调用，避免自取消。

### `got_intercept_bypass(user_id, matcher, text, HELP_TEXT) -> bool`

Got handler 入口统一拦截 /help 和 /cancel。

- `/cancel` 分支委托给 `execute_cancel`
- `/help` 分支执行 `matcher.send(HELP_TEXT)` 后 `reject()` 继续等待
- 匹配规则：`text.startswith("/cmd ") or text == "/cmd"`
- 返回 `True` 表示已拦截（调用方应 return），`False` 表示正常流程继续

### `timeout_session(bot, event, user_id, selection_id, message, *, on_cleanup=None, timeout=None) -> None`

会话超时检查任务。

超时后按 `user_id + selection_id` 双重校验：
- 匹配 → 发送超时提示 + `remove_selection` + 可选 `on_cleanup`
- 不匹配（被新选择或 /cancel 覆盖）→ 静默退出

支持 `CancelledError` 捕获，超时任务可被外部取消。
