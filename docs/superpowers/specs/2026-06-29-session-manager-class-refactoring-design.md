# SessionManager 类重构 + current_task 管理修复设计

> 日期：2026-06-29
> 状态：待审阅

---

## 1. 问题概述

代码中存在 3 个 `# n:` / FIXME 标记，涵盖 5 处代码：

| 编号 | 位置 | 标记内容 |
|------|------|---------|
| **A** | `bot/session.py` | n: 将会话管理包装为一个类 |
| **B1** | `bot/plugins/meme_add.py:111` | FIXME: 应将chat的asyncio task设置为None |
| **B2** | `bot/plugins/_search_utils.py:154` | FIXME: 应将chat的asyncio task设置为None |
| **C1** | `bot/plugins/meme_add.py:136` | FIXME: 重新设置asyncio task，而不是激活chat |
| **C2** | `bot/plugins/_search_utils.py:180` | FIXME: 重新设置asyncio task，而不是激活chat |

---

## 2. 设计目标

1. 将 session 模块级函数 + 全局 dict 封装为 `SessionManager` 类，状态内聚
2. 解决 handle() 结束后 `current_task` 残留指向已结束 task 的问题
3. 解决 got 入口无法通过 `activate_chat()` 更新 `current_task` 的问题（因为 chat 仍为 active）
4. 提供 `with` 语句上下文管理器简化 got handler 的 task 生命周期管理
5. `got_intercept_bypass` 移出 session 层，归入调用方更合理的模块

---

## 3. 模块结构变更

### 3.1 `bot/session.py` — SessionManager 类

将当前模块级函数和全局 dict 封装为 `SessionManager` 类：

```python
class SessionManager:
    """统一的会话管理器。"""

    def __init__(self) -> None:
        self._chat_sessions: dict[str, ChatSession] = {}
        self._selection_sessions: dict[str, SelectionSession] = {}

    # ── 核心会话状态管理 ──
    def get_or_create_chat(self, user_id: str) -> ChatSession: ...
    def activate_chat(self, user_id: str, command_type: str, matcher: Matcher) -> bool: ...
    def deactivate_chat(self, user_id: str) -> None: ...

    # ── 选择会话管理 ──
    def create_selection(self, user_id: str, selection_id: str, timeout_task: asyncio.Task) -> None: ...
    def remove_selection(self, user_id: str) -> SelectionSession | None: ...
    def get_selection(self, user_id: str) -> SelectionSession | None: ...

    # ── Task 生命周期管理（新增）──
    def set_current_task(self, user_id: str, task: asyncio.Task | None) -> None:
        """显式设置用户的 current_task。"""
        chat = self.get_or_create_chat(user_id)
        chat.current_task = task

    def reset_current_task(self, user_id: str) -> None:
        """快速将 current_task 设为 None。"""
        chat = self._chat_sessions.get(user_id)
        if chat:
            chat.current_task = None

    @contextmanager
    def handler_context(self, user_id: str, matcher: Matcher):
        """进入 got handler 时更新 current_task 和 matcher，离开时自动 reset。

        用法：
            with session_manager.handler_context(user_id, matcher):
                ...
        """
        chat = self.get_or_create_chat(user_id)
        chat.current_task = asyncio.current_task()
        chat.matcher = matcher
        try:
            yield
        finally:
            # 只清理没有被 deactivate_chat 重置过的情况
            if chat.current_task is asyncio.current_task():
                chat.current_task = None

    # ── 取消 ──
    async def execute_cancel(self, user_id: str, message: str = "当前会话已取消") -> bool: ...


# 模块级单例
session_manager = SessionManager()
```

`execute_cancel` 内部逻辑不变（自取消保护、跨 task 取消、选择会话清理、matcher finish）。

### 3.2 `bot/session.py` — timeout_session 作为模块级函数

`timeout_session` 作为模块级独立函数保留在 `session.py`，内部引用 `session_manager` 单例：

```python
async def timeout_session(
    bot: Bot, event: Event, user_id: str, selection_id: str,
    message: str, *, on_cleanup=None, timeout=None,
) -> None:
    ...
    # 通过公共方法 get_selection() 访问选择会话
    ss = session_manager.get_selection(user_id)
    if ss is not None and ss.selection_id == selection_id:
        session_manager.remove_selection(user_id)
        session_manager.deactivate_chat(user_id)
        ...
```

### 3.3 `bot/plugins/_search_utils.py` — 移入 got_intercept_bypass

`got_intercept_bypass` 从 `session.py` 移出，放入 `_search_utils.py`。它作为 got handler 共享的旁路拦截工具，语义上属于搜索/插件层工具，不属于会话管理层。

```python
# bot/plugins/_search_utils.py
from bot.session import session_manager

async def got_intercept_bypass(
    user_id: str, matcher: Matcher, text: str, HELP_TEXT: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。

    内部 /cancel 分支委托给 session_manager.execute_cancel。
    """
    if text.startswith("/cancel ") or text == "/cancel":
        succeed = await session_manager.execute_cancel(user_id)
        if not succeed:
            await matcher.finish("当前没有活跃的会话")
        return True
    if text.startswith("/help ") or text == "/help":
        await matcher.reject(HELP_TEXT)  # reject 发送 HELP_TEXT 作为 prompt，继续等待
        return True
    return False
```

### 3.4 变更汇总

| 文件 | 变更 |
|------|------|
| `bot/session.py` | 新增 `SessionManager` 类、模块级 `session_manager` 实例；保留 `timeout_session` 模块级函数；移除 `got_intercept_bypass` |
| `bot/plugins/_search_utils.py` | 新增 `got_intercept_bypass` 函数；改用 `session_manager.xxx()` 调用模式；修复 Task B + C |
| `bot/plugins/meme_add.py` | 改用 `session_manager.xxx()` 调用模式；import 更新；修复 Task B + C |
| `bot/plugins/meme_search.py` | 改用 `session_manager.xxx()` 调用模式 |
| `bot/plugins/meme_plain_text.py` | 改用 `session_manager.xxx()` 调用模式；import 更新 |
| `bot/plugins/meme_cancel.py` | 改用 `session_manager.execute_cancel()` |
| `tests/unit/test_session.py` | 适配 SessionManager 类；每测试清空 `session_manager._chat_sessions` / `_selection_sessions` |
| `tests/unit/test_search_utils.py` | 新增 `got_intercept_bypass` 测试（如有需要） |

---

## 4. Task B + C 修复细节

### 4.1 Task B — `create_selection` 后 reset current_task

**handle_add** (`meme_add.py`):
```python
# 修复前
create_selection(user_id, selection_id, task)
# FIXME: 应将chat的asyncio task设置为None

# 修复后
session_manager.create_selection(user_id, selection_id, task)
session_manager.reset_current_task(user_id)  # handle task 即将结束
```

**execute_search** (`_search_utils.py`):
```python
# 修复前
create_selection(user_id, selection_id, task)
# FIXME: 应将chat的asyncio task设置为None

# 修复后
session_manager.create_selection(user_id, selection_id, task)
session_manager.reset_current_task(user_id)
```

### 4.2 Task C — got 入口使用 handler_context

**got_image** (`meme_add.py`):
```python
# 修复前
# FIXME: 重新设置asyncio task，而不是激活chat(这里无法激活，因为chat还是active状态)
activate_chat(user_id, "add", matcher)

# 修复后
with session_manager.handler_context(user_id, matcher):
    # current_task 自动设为 got task，matcher 更新
    # 方法体（try/except/else）整体缩进进入 with 块
    ...
    # 离开 with 块时自动 reset current_task
```

注意：`handler_context` 不捕获异常，所有异常（`FinishedException`、`RejectedException`、其他）正常传播。`deactivate_chat` 在 `finally` / `except` 中需要时仍显式调用，`handler_context` 的 `finally` 只清理没有被 `deactivate_chat` 修改过的 `current_task`。

同样的模式应用于 **handle_got_selection** (`_search_utils.py`):
```python
with session_manager.handler_context(user_id, matcher):
    ...
```

### 4.3 安全边界

`handler_context` 的 `finally` 块中的保护逻辑：
```python
if chat.current_task is asyncio.current_task():
    chat.current_task = None
```

此条件确保：
- 如果 got handler 正常流程已调用 `deactivate_chat`（`current_task` 已为 None），不重复清理
- 如果 got handler 异常退出（尚未执行 `deactivate_chat`），自动清理
- 如果 got handler 中途被其他线程/协程修改了 `current_task`，不误清理

---

## 5. 调用方变更示例

### meme_add.py

```python
# 旧
from bot.session import activate_chat, create_selection, deactivate_chat, got_intercept_bypass, timeout_session, remove_selection

activate_chat(user_id, "add", matcher)
create_selection(user_id, selection_id, task)

# 新
from bot.session import session_manager, timeout_session
from bot.plugins._search_utils import got_intercept_bypass

session_manager.activate_chat(user_id, "add", matcher)
session_manager.create_selection(user_id, selection_id, task)
session_manager.reset_current_task(user_id)
```

### meme_cancel.py

```python
# 旧
from bot.session import execute_cancel
await execute_cancel(user_id)

# 新
from bot.session import session_manager
await session_manager.execute_cancel(user_id)
```

### meme_search.py

```python
# 旧
from bot.session import activate_chat, deactivate_chat

# 新
from bot.session import session_manager
```

### meme_plain_text.py

```python
# 旧
from bot.session import activate_chat

# 新
from bot.session import session_manager
```

---

## 6. 测试变更

### test_session.py

每个测试前清空 session_manager 内部 dict：

```python
@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
```

`got_intercept_bypass` 的测试移至 `_search_utils.py` 对应的测试文件（或保留在现有搜索测试中）。

---

## 7. 不变量

- 所有方法签名、返回值类型不变
- `ChatSession`、`SelectionSession` 数据结构不变
- `execute_cancel` 行为不变
- `timeout_session` 行为不变
- `got_intercept_bypass` /help 路径简化：`send(HELP_TEXT) + reject(None)` → `reject(HELP_TEXT)`（功能等价）
- 测试覆盖率不下降
