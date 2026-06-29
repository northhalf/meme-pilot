# SessionManager 类重构 + current_task 修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `bot/session.py` 的模块级函数+全局 dict 封装为 `SessionManager` 类，修复 handle/got 之间 `current_task` 管理问题。

**Architecture:** SessionManager 类封装会话状态（_chat_sessions、_selection_sessions）和操作方法；模块级 `session_manager` 单例供外部导入；`got_intercept_bypass` 移入 `_search_utils.py`；`timeout_session` 保留为模块级函数引用单例；新增 `handler_context` 上下文管理器简化 got handler 的 task 生命周期。

**Tech Stack:** Python 3.12, asyncio, NoneBot2, pytest

**Spec:** `docs/superpowers/specs/2026-06-29-session-manager-class-refactoring-design.md`

---

### Task 1: session.py — 创建 SessionManager 类

**Files:**
- Modify: `bot/session.py`（整文件重写结构）
- Test: `tests/unit/test_session.py`
- Docs: 无（接口签名不变，仅封装方式变化）

- [ ] **Step 1: 在 session.py 中创建 SessionManager 类**

将 ChatSession、SelectionSession dataclass 保留。创建 SessionManager 类，把以下函数移入作为方法：
- `get_or_create_chat`
- `activate_chat`
- `deactivate_chat`
- `create_selection`
- `remove_selection`
- `get_selection`
- `execute_cancel`
- `set_current_task`（新增）
- `reset_current_task`（新增）
- `handler_context`（新增，`@contextmanager`）

类实现：

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable
import uuid

from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher

from bot.config import read_session_timeout

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """每个用户一个，持久存在，首次访问时懒创建。"""
    session_id: str
    active: bool = False
    command_type: str | None = None
    matcher: Matcher | None = None
    current_task: asyncio.Task | None = None


@dataclass
class SelectionSession:
    """选择会话，至多一个，是 ChatSession 的子集。"""
    selection_id: str
    timeout_task: asyncio.Task | None = None


class SessionManager:
    """统一的会话管理器。"""

    def __init__(self) -> None:
        self._chat_sessions: dict[str, ChatSession] = {}
        self._selection_sessions: dict[str, SelectionSession] = {}

    # ── 核心会话状态管理 ──

    def get_or_create_chat(self, user_id: str) -> ChatSession:
        if user_id not in self._chat_sessions:
            self._chat_sessions[user_id] = ChatSession(session_id=str(uuid.uuid4()))
        return self._chat_sessions[user_id]

    def activate_chat(self, user_id: str, command_type: str, matcher: Matcher) -> bool:
        chat = self.get_or_create_chat(user_id)
        if chat.active:
            return False
        chat.active = True
        chat.command_type = command_type
        chat.matcher = matcher
        chat.current_task = asyncio.current_task()
        return True

    def deactivate_chat(self, user_id: str) -> None:
        self.remove_selection(user_id)
        chat = self._chat_sessions.get(user_id)
        if chat is None:
            return
        chat.active = False
        chat.command_type = None
        chat.matcher = None
        chat.current_task = None

    # ── 选择会话管理 ──

    def create_selection(self, user_id: str, selection_id: str, timeout_task: asyncio.Task) -> None:
        self._selection_sessions[user_id] = SelectionSession(
            selection_id=selection_id,
            timeout_task=timeout_task,
        )

    def remove_selection(self, user_id: str) -> SelectionSession | None:
        return self._selection_sessions.pop(user_id, None)

    def get_selection(self, user_id: str) -> SelectionSession | None:
        return self._selection_sessions.get(user_id)

    # ── Task 生命周期管理 ──

    def set_current_task(self, user_id: str, task: asyncio.Task | None) -> None:
        chat = self.get_or_create_chat(user_id)
        chat.current_task = task

    def reset_current_task(self, user_id: str) -> None:
        chat = self._chat_sessions.get(user_id)
        if chat:
            chat.current_task = None

    @contextmanager
    def handler_context(self, user_id: str, matcher: Matcher):
        """进入 got handler 时更新 current_task 和 matcher，离开时自动 reset。"""
        chat = self.get_or_create_chat(user_id)
        chat.current_task = asyncio.current_task()
        chat.matcher = matcher
        try:
            yield
        finally:
            if chat.current_task is asyncio.current_task():
                chat.current_task = None

    # ── 取消 ──

    async def execute_cancel(self, user_id: str, message: str = "当前会话已取消") -> bool:
        chat = self._chat_sessions.get(user_id)
        if not (chat and chat.active):
            return False

        current = asyncio.current_task()
        if (
            chat.current_task
            and not chat.current_task.done()
            and chat.current_task is not current
        ):
            chat.current_task.cancel()

        ss = self._selection_sessions.pop(user_id, None)
        if ss and ss.timeout_task and not ss.timeout_task.done():
            ss.timeout_task.cancel()

        if chat.matcher:
            try:
                await chat.matcher.finish(message)
            except FinishedException:
                pass

        self.deactivate_chat(user_id)
        return True


# 模块级单例
session_manager = SessionManager()


# ── 模块级工具函数 ──

async def timeout_session(
    bot: Bot,
    event: Event,
    user_id: str,
    selection_id: str,
    message: str,
    *,
    on_cleanup: Callable[[], Any | Awaitable[Any]] | None = None,
    timeout: int | None = None,
) -> None:
    """会话超时检查任务。"""
    if timeout is None:
        timeout = read_session_timeout()
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    ss = session_manager.get_selection(user_id)
    if ss is not None and ss.selection_id == selection_id:
        logger.info("用户 %s 的选择会话超时（%d 秒）", user_id, timeout)
        session_manager.remove_selection(user_id)
        session_manager.deactivate_chat(user_id)
        if on_cleanup is not None:
            result = on_cleanup()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
        try:
            await bot.send(event, message)
        except Exception:
            logger.debug("发送超时消息失败", exc_info=True)
```

注意：`__init__.py` 不更新（session 模块无需导出到 __init__）。

- [ ] **Step 2: 运行语法检查**

```bash
uv run python -m compileall bot/session.py
```

Expected: OK, no errors.

- [ ] **Step 3: Run existing tests to verify baseline**

```bash
uv run pytest tests/unit/test_session.py -v
```

Expected: Tests will fail because they import from the old module-level API.

---

### Task 2: test_session.py — 适配 SessionManager 类

**Files:**
- Modify: `tests/unit/test_session.py`

- [ ] **Step 1: 更新 fixture 和所有测试**

在 test_session.py 中做以下变更：

a) 将 fixtures 中的 `chat_sessions.clear()` → `session_manager._chat_sessions.clear()`（`selection_sessions` 同理）

b) 所有测试中调用函数改为 `session_manager.xxx()`：
- `activate_chat()` → `session_manager.activate_chat()`
- `deactivate_chat()` → `session_manager.deactivate_chat()`
- `get_or_create_chat()` → `session_manager.get_or_create_chat()`
- `create_selection()` → `session_manager.create_selection()`
- `remove_selection()` → `session_manager.remove_selection()`
- `get_selection()` → `session_manager.get_selection()`
- `execute_cancel()` → `session_manager.execute_cancel()`

c) `TestGotInterceptBypass` 整个类**删除**（移到 `test_search_utils.py`）

d) import 变更：
```python
# 旧
from bot.session import (
    ChatSession, SelectionSession, activate_chat, chat_sessions,
    create_selection, deactivate_chat, execute_cancel,
    get_or_create_chat, get_selection, got_intercept_bypass,
    remove_selection, selection_sessions,
)

# 新
from bot.session import (
    ChatSession, SelectionSession, session_manager,
)
```

完整 fixture 代码：
```python
@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
```

- [ ] **Step 2: 运行测试验证通过**

```bash
uv run pytest tests/unit/test_session.py -v
```

Expected: All test classes pass (except removed TestGotInterceptBypass).

---

### Task 3: _search_utils.py — got_intercept_bypass + Task B/C 修复

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Modify: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 更新 _search_utils.py 的 import 和调用方式**

将函数调用改为 `session_manager.xxx()`：

```python
# 旧 import
from bot.session import (
    activate_chat,
    create_selection,
    deactivate_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
    timeout_session,
)

# 新 import
from bot.session import (
    session_manager,
    timeout_session,
)
```

所有调用处替换：
- `activate_chat(...)` → `session_manager.activate_chat(...)`
- `create_selection(...)` → `session_manager.create_selection(...)`
- `deactivate_chat(...)` → `session_manager.deactivate_chat(...)`
- `get_selection(...)` → `session_manager.get_selection(...)`
- `remove_selection(...)` → `session_manager.remove_selection(...)`

- [ ] **Step 2: 在 _search_utils.py 中添加 got_intercept_bypass 函数**

在文件末尾（`import` 之后、`handle_selection` 之前，或文件底部）添加：

```python
async def got_intercept_bypass(
    user_id: str, matcher: Matcher, text: str, HELP_TEXT: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。

    /cancel 分支委托给 session_manager.execute_cancel。
    /help 分支通过 reject(HELP_TEXT) 发送帮助文本并继续等待。

    Args:
        user_id: 用户 ID。
        matcher: 当前 got handler 的 matcher。
        text: 用户消息文本。
        HELP_TEXT: 帮助文本常量。

    Returns:
        True 表示拦截到命令（调用方应 return），
        False 表示正常流程继续。
    """
    if text.startswith("/cancel ") or text == "/cancel":
        succeed = await session_manager.execute_cancel(user_id)
        if not succeed:
            await matcher.finish("当前没有活跃的会话")
        return True

    if text.startswith("/help ") or text == "/help":
        await matcher.reject(HELP_TEXT)
        return True

    return False
```

- [ ] **Step 3: Task B 修复 — execute_search 中 create_selection 后 reset_current_task**

在 `execute_search()` 中，修改多结果分支的末尾：

```python
# 修复前
task = asyncio.create_task(
    timeout_session(bot, event, user_id, selection_id, "选择已过期，请重新搜索")
)
create_selection(user_id, selection_id, task)
# FIXME: 应将chat的asyncio task设置为None

# 修复后
task = asyncio.create_task(
    timeout_session(bot, event, user_id, selection_id, "选择已过期，请重新搜索")
)
session_manager.create_selection(user_id, selection_id, task)
session_manager.reset_current_task(user_id)
```

- [ ] **Step 4: Task C 修复 — handle_got_selection 使用 handler_context**

在 `handle_got_selection()` 中，将函数体整体移入 `with session_manager.handler_context(user_id, matcher):` 块：

```python
async def handle_got_selection(
    bot: Bot, event: MessageEvent, matcher: Matcher,
    selection_msg: Message, error_label: str = "搜索",
) -> None:
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):  # ← 替代 activate_chat
        try:
            # /help 和 /cancel 旁路拦截
            text = event.get_plaintext().strip()
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            # 检查选择会话是否仍有效
            ss = session_manager.get_selection(user_id)
            if ss is None:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("选择已过期，请重新搜索")
                return

            candidates = matcher.state.get("candidates", [])
            selection_text = selection_msg.extract_plain_text().strip()

            result = handle_selection(matcher, candidates, selection_text)
            if isinstance(result, str):
                await matcher.reject(result)
                return

            # 有效选择：清除选择会话
            session_manager.remove_selection(user_id)
            image_path = MEMES_DIR / result.filename
            await matcher.finish(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )
            session_manager.deactivate_chat(user_id)

        except RejectedException:
            raise
        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except Exception:
            logger.exception("用户 %s 的 %s 处理异常", user_id, error_label)
            session_manager.deactivate_chat(user_id)
            raise
```

- [ ] **Step 5: 更新 test_search_utils.py — 修复 mock 路径 + 添加 got_intercept_bypass 测试**

a) 修复现有测试的 mock 路径：

```python
# 旧
@patch("bot.plugins._search_utils.deactivate_chat")

# 新
@patch("bot.plugins._search_utils.session_manager.deactivate_chat")
```

同样的模式适用于：
- `create_selection` → `bot.plugins._search_utils.session_manager.create_selection`
- `deactivate_chat` → `bot.plugins._search_utils.session_manager.deactivate_chat`

b) 添加 `got_intercept_bypass` 测试类（替代从 test_session.py 删除的 TestGotInterceptBypass）：

```python
class TestGotInterceptBypass:
    """got_intercept_bypass 测试。"""

    @pytest.mark.asyncio
    async def test_normal_text_returns_false(self):
        """普通文本返回 False。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        result = await got_intercept_bypass("user1", matcher, "hello", "帮助文本")
        assert result is False

    @pytest.mark.asyncio
    async def test_help_returns_true(self):
        """/help 拦截后返回 True（reject 抛出 RejectedException）。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        matcher.reject.return_value = None  # reject 被 mock 捕获
        result = await got_intercept_bypass("user1", matcher, "/help", "帮助文本")
        assert result is True
        matcher.reject.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_returns_true(self):
        """/cancel 拦截后返回 True。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        from bot.session import session_manager
        session_manager.activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass("user1", matcher, "/cancel", "帮助文本")
        assert result is True

    @pytest.mark.asyncio
    async def test_help_with_args_matches(self):
        """/help xxx（带参数）也匹配帮助。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        result = await got_intercept_bypass("user1", matcher, "/help 加班", "帮助文本")
        assert result is True
        matcher.reject.assert_called_once_with("帮助文本")

    @pytest.mark.asyncio
    async def test_cancel_with_args_matches(self):
        """/cancel xxx（带参数）也匹配取消。"""
        from bot.plugins._search_utils import got_intercept_bypass

        matcher = AsyncMock()
        from bot.session import session_manager
        session_manager.activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass("user1", matcher, "/cancel something", "帮助文本")
        assert result is True
```

- [ ] **Step 6: 运行测试验证**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: All tests pass.

---

### Task 4: meme_add.py — 修复 Task B/C + 改 import

**Files:**
- Modify: `bot/plugins/meme_add.py`

- [ ] **Step 1: 更新 import**

```python
# 旧
from bot.session import (
    activate_chat,
    create_selection,
    deactivate_chat,
    got_intercept_bypass,
    timeout_session,
    remove_selection,
)

# 新
from bot.session import session_manager, timeout_session
from bot.plugins._search_utils import got_intercept_bypass
```

其他 import 保持不变（`_help_text.HELP_TEXT` 等）。

- [ ] **Step 2: 替换所有模块级函数调用为 session_manager.xxx()**

```python
# 旧 → 新
activate_chat(...)              → session_manager.activate_chat(...)
create_selection(...)            → session_manager.create_selection(...)
deactivate_chat(...)             → session_manager.deactivate_chat(...)
remove_selection(...)            → session_manager.remove_selection(...)
```

- [ ] **Step 3: Task B 修复 — handle_add 中 create_selection 后 reset_current_task**

```python
# 修复前（meme_add.py:107-111）
task = asyncio.create_task(
    timeout_session(bot, event, user_id, selection_id, "发送图片超时，请重新 /add")
)
create_selection(user_id, selection_id, task)
# FIXME: 应将chat的asyncio task设置为None

# 修复后
task = asyncio.create_task(
    timeout_session(bot, event, user_id, selection_id, "发送图片超时，请重新 /add")
)
session_manager.create_selection(user_id, selection_id, task)
session_manager.reset_current_task(user_id)
```

- [ ] **Step 4: Task C 修复 — got_image 使用 handler_context**

在 `got_image()` 中，用 `with session_manager.handler_context(user_id, matcher):` 包裹方法体（替代当前的 `activate_chat(user_id, "add", matcher)` 调用）：

```python
# 修复前（meme_add.py:132-136）
user_id = event.get_user_id()

# got 入口重新激活 chat session（不同 asyncio task）
# FIXME: 重新设置asyncio task，而不是激活chat(这里无法激活，因为chat还是active状态)
activate_chat(user_id, "add", matcher)

try:
    # ── 阶段 0：/help 和 /cancel 旁路拦截 ──
    ...

# 修复后
user_id = event.get_user_id()

with session_manager.handler_context(user_id, matcher):
    try:
        # ── 阶段 0：/help 和 /cancel 旁路拦截 ──
        ...
```

注意：整个 try/except/else 结构缩进一级进入 with 块。

- [ ] **Step 5: 语法检查和测试**

```bash
uv run python -m compileall bot/plugins/meme_add.py
uv run pytest tests/unit/plugins/test_meme_add.py -v
```

---

### Task 5: meme_search.py + meme_plain_text.py + meme_cancel.py — 改 import

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `bot/plugins/meme_plain_text.py`
- Modify: `bot/plugins/meme_cancel.py`

- [ ] **Step 1: meme_search.py 更新**

```python
# 旧
from bot.session import (
    activate_chat,
    deactivate_chat,
)

# 新
from bot.session import session_manager

# 调用处
activate_chat(...)     → session_manager.activate_chat(...)
deactivate_chat(...)   → session_manager.deactivate_chat(...)
```

- [ ] **Step 2: meme_plain_text.py 更新**

```python
# 旧
from bot.session import (
    activate_chat,
)

# 新
from bot.session import session_manager

# 调用处
activate_chat(...)     → session_manager.activate_chat(...)
```

- [ ] **Step 3: meme_cancel.py 更新**

```python
# 旧
from bot.session import execute_cancel

# 新
from bot.session import session_manager

# 调用处
execute_cancel(...)    → session_manager.execute_cancel(...)
```

- [ ] **Step 4: 语法检查和测试**

```bash
uv run python -m compileall bot/plugins/meme_search.py bot/plugins/meme_plain_text.py bot/plugins/meme_cancel.py
uv run pytest tests/unit/plugins/test_meme_search.py tests/unit/plugins/test_meme_plain_text.py tests/unit/plugins/test_meme_cancel.py -v
```

---

### Task 6: 全量测试验证 + 文档更新

**Files:**
- Modify: `docs/api/API.md`（session.py 接口文档更新）

- [ ] **Step 1: 运行全量测试**

```bash
uv run pytest -v
```

所有测试必须通过。如有失败，分析原因并修复。

- [ ] **Step 2: 更新 docs/api/API.md 中 session.py 的接口文档**

API.md 第 463-478 行的 session 模块接口需更新。找到对应的 `### `bot/session.py`` 区块，更新接口描述为类方法形式：

`session.py` 模块现在导出 `session_manager`（`SessionManager` 实例）和 `timeout_session`：

```python
# 新增导入路径
from bot.session import session_manager, timeout_session

# 类名（不直接导出）
class SessionManager:
    def get_or_create_chat(user_id: str) -> ChatSession
    def activate_chat(user_id, command_type, matcher) -> bool
    def deactivate_chat(user_id) -> None
    def create_selection(user_id, selection_id, timeout_task) -> None
    def remove_selection(user_id) -> SelectionSession | None
    def get_selection(user_id) -> SelectionSession | None
    def set_current_task(user_id, task) -> None
    def reset_current_task(user_id) -> None
    @contextmanager handler_context(user_id, matcher)
    async def execute_cancel(user_id, message) -> bool

# 模块级函数
async def timeout_session(bot, event, user_id, selection_id, message, ...) -> None
```

以及移除 `got_intercept_bypass` 的引用（移至 `_search_utils.md`）。

- [ ] **Step 3: 最终确认**

```bash
uv run python -m compileall bot tests
uv run pytest -v
```

All clear.
