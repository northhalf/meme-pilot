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
    command_type: str | None = None    # "add" / "addtag" / "del" / "search" / "ai" / "refresh" / "info"
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

## Module-Level Singleton

```python
session_manager: SessionManager  # 模块级单例，供外部导入
```

## SessionManager 类

### `get_or_create_chat(user_id) -> ChatSession`

首次访问时创建并存储 ChatSession，之后复用。

- `user_id` — 用户 ID

### `activate_chat(user_id, command_type, matcher) -> bool`

激活聊天会话（handle 入口使用）。

- 设置 `active=True`, `matcher`, `command_type`, `current_task=asyncio.current_task()`
- 返回 `True` 表示成功激活
- 返回 `False` 表示已有活跃会话（调用方应拒绝新命令）
- 注意：chat.active 为 True 时直接返回 False，不会更新任何字段。got 入口应使用 `handler_context`（with 语句）而非 `activate_chat`。

### `deactivate_chat(user_id) -> None`

重置聊天会话为空闲，同时删除与之关联的选择会话。`active=False`, `command_type=None`, `matcher=None`, `current_task=None`，并调用 `remove_selection(user_id)` 清理选择会话。

### `create_selection(user_id, selection_id, timeout_task) -> None`

创建选择会话。覆盖同一用户的旧选择会话。

- `selection_id` — UUID 字符串
- `timeout_task` — 超时监控任务引用

### `remove_selection(user_id) -> SelectionSession | None`

移除选择会话，返回旧会话（用于取消 timeout_task）。不存在时返回 None。

### `get_selection(user_id) -> SelectionSession | None`

查询用户的选择会话。不存在时返回 None。

### `set_current_task(user_id, task) -> None`

显式设置用户的 current_task。

- `task` — `asyncio.Task | None`，要设置的异步任务

### `reset_current_task(user_id) -> None`

快速将 current_task 设为 None。在 `create_selection` 后调用，表示 handle task 即将结束。

### `handler_context(user_id, matcher)`

上下文管理器，got handler 入口使用（with 语句）。进入时自动更新 current_task 和 matcher，离开时自动 reset。

```python
with session_manager.handler_context(user_id, matcher):
    ...
```

### `execute_cancel(user_id, message="当前会话已取消") -> bool`

执行取消逻辑。

1. 检查是否有活跃会话，无则返回 `False`
2. `current_task.cancel()`（非当前 task 且未完成时）
3. `remove_selection()` + 取消 `timeout_task`
4. 在旧 matcher 上 `finish()`（发送 `message` 到原上下文）
5. `deactivate_chat(user_id)`

**返回：** `True` 表示成功重置对话，`False` 表示无活跃会话（调用方处理提示）。

**自取消保护：** 当 `current_task is asyncio.current_task()`（同频道 /cancel）时跳过 `cancel()` 调用，避免自取消。

## 模块级工具函数

### `timeout_session(bot, event, user_id, selection_id, message, *, on_cleanup=None, timeout=None) -> None`

会话超时检查任务。

超时后按 `user_id + selection_id` 双重校验：
- 匹配 → `remove_selection` + `deactivate_chat` 清理会话 + 可选 `on_cleanup` + 发送超时提示
- 不匹配（被新选择或 /cancel 覆盖）→ 静默退出

支持 `CancelledError` 捕获，超时任务可被外部取消。内部通过 `session_manager.get_selection()` 公共方法访问会话状态。
