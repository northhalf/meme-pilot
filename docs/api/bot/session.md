# bot/session.py — 共享会话管理模块

> 管理 /add、/search 等命令的待处理会话，支持跨命令的会话覆盖（新命令取消旧命令）。

## 数据类

```python
@dataclass
class PendingSession:
    matcher: Matcher          # NoneBot2 Matcher 实例
    cancelled: bool = False   # 是否已被新命令取消
    type: str = "add"         # 命令类型，如 "add" 或 "search"
```

## 模块级变量

```python
pending_sessions: dict[str, PendingSession]  # user_id → PendingSession
```

## 函数

### `check_and_cancel(user_id, new_type) -> str | None`

检查旧会话并标记取消。

- `user_id` — 用户 ID
- `new_type` — 新命令类型
- 返回取消提示文本（`"已取消上一条未完成的操作，开始新的 /{new_type}"`），无旧会话返回 `None`

### `register(user_id, matcher, type) -> None`

注册新会话。覆盖同一用户的旧会话。

- `user_id` — 用户 ID
- `matcher` — NoneBot2 Matcher 实例
- `type` — 命令类型

### `cancel(user_id) -> None`

移除会话。不存在时不报错。

- `user_id` — 用户 ID

### `is_cancelled(user_id) -> bool`

检查会话是否已被取消。无会话返回 `False`。

- `user_id` — 用户 ID
