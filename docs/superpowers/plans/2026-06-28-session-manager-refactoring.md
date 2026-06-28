# 会话管理重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 session 管理，分离 ChatSession 与 SelectionSession，优化索引锁粒度，添加 /cancel 命令，实现 /help 和 /cancel 在任意状态下的旁路拦截。

**Architecture:** `bot/session.py` 完全重写为 `chat_sessions` + `selection_sessions` 双字典；`IndexManager` 将锁与同步标识分离，`add_single_file` 不再持锁；各插件通过 `activate_chat()` 统一入口检查状态，got handler 通过 `got_intercept_bypass()` 共享拦截逻辑。

**Tech Stack:** Python 3.12, NoneBot2 2.x, pytest, asyncio

---

### Task 1: IndexManager — 锁粒度优化

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`（已有，追加测试）

**设计变更：**
1. 新增 `_add_sem`（Semaphore）限制 add pipeline 并发
2. 新增 `_is_syncing: bool` 标识同步状态
3. `acquire_lock()` 同时设 `_is_syncing=True`，`release_lock()` 设 `_is_syncing=False`
4. `add_single_file` 去掉 `_lock` 操作，改用 `_add_sem`
5. `is_locked` 返回 `_is_syncing` 而非检查 `_lock`

- [ ] **Step 1: 编写测试（新行为）**

```python
# tests/unit/engine/test_index_manager.py 追加

class TestIndexManagerLockRefactoring:
    """索引锁重构行为测试。"""

    async def test_is_locked_reflects_syncing(self):
        """is_locked 在 acquire_lock 时返回 True，release_lock 后返回 False。"""
        # 使用 mock 避免加载真实文件
        im = IndexManager(data_dir="/tmp/test_lock", memes_dir="/tmp/test_lock_memes")
        assert im.is_locked is False

        await im.acquire_lock()
        assert im.is_locked is True

        im.release_lock()
        assert im.is_locked is False

    async def test_add_single_file_no_lock_held(self, mocker):
        """add_single_file 不持有 _lock（仅用 _add_sem）。"""
        im = IndexManager(data_dir="/tmp/test_add", memes_dir="/tmp/test_add_memes")
        # mock pipeline 避免真实 API 调用
        mocker.patch.object(im, '_process_image_pipeline', return_value=("test text", [0.1, 0.2]))
        mocker.patch.object(im, 'add_entry')

        # add_single_file 期间 _lock 不应被占用
        assert im._lock.locked() is False
        await im.add_single_file("test.jpg")
        assert im._lock.locked() is False  # add_entry 是同步方法，不用锁

    async def test_acquire_lock_fails_when_locked(self):
        """acquire_lock 在锁已被占用时返回 False。"""
        im = IndexManager(data_dir="/tmp/test_lock2", memes_dir="/tmp/test_lock2_memes")
        await im.acquire_lock()
        result = await im.acquire_lock()
        assert result is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestIndexManagerLockRefactoring -v
```
Expected: FAIL — `_is_syncing` 属性不存在。

- [ ] **Step 3: 实现 IndexManager 锁变更**

修改 `bot/engine/index_manager.py`：

```python
# 在 __init__ 中新增 _add_sem 和 _is_syncing
# 在 self._lock = asyncio.Lock() 之后追加：
self._add_sem = asyncio.Semaphore(
    sync_concurrency
    if isinstance(sync_concurrency, int) and sync_concurrency > 0
    else self.DEFAULT_SYNC_CONCURRENCY
)
self._is_syncing: bool = False
```

```python
# 修改 acquire_lock(self) -> bool:
async def acquire_lock(self) -> bool:
    """仅供 sync_with_filesystem 调用。"""
    if self._lock.locked():
        return False
    await self._lock.acquire()
    self._is_syncing = True
    return True
```

```python
# 修改 release_lock(self) -> None:
def release_lock(self) -> None:
    """仅供 sync_with_filesystem 调用。"""
    self._is_syncing = False
    if self._lock.locked():
        self._lock.release()
```

```python
# 修改 is_locked property:
@property
def is_locked(self) -> bool:
    """仅 sync 时拒绝读操作，add 短时写锁不阻塞读。"""
    return self._is_syncing
```

```python
# 修改 add_single_file(self, filename) -> AddResult:
# 去掉 _lock 相关的 acquire/release，改用 _add_sem
async def add_single_file(self, filename: str) -> AddResult:
    async with self._add_sem:
        text, embedding = await self._process_image_pipeline(filename)
    return self.add_entry(filename, text, embedding)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestIndexManagerLockRefactoring -v
```
Expected: PASS

- [ ] **Step 5: 运行全部现有索引测试确认不破坏**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```
Expected: all existing tests still PASS

---

### Task 2: session.py — 完整重写（ChatSession + SelectionSession + 基础 API）

**Files:**
- Create: `tests/unit/test_session.py`（重写）
- Modify: `bot/session.py`（完全重写）

**设计：** 替换 `PendingSession` + 旧 API 为 `ChatSession` + `SelectionSession` + 新 API。

- [ ] **Step 1: 编写 session.py 重写代码**

```python
"""共享会话管理模块。

管理用户的聊天会话（ChatSession）和选择会话（SelectionSession），
支持 /cancel 和 /help 在任何状态下旁路触发。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher

from bot.config import read_session_timeout

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """每个用户一个，持久存在，首次访问时懒创建。

    Attributes:
        session_id: UUID，首次创建时永久固定。
        active: True 表示有命令正在处理。
        command_type: 命令类型，"add"/"search"/"ai"/"refresh"。
        matcher: 当前命令的 NoneBot2 Matcher。
        current_task: 异步任务引用，handle/got 入口通过 activate_chat 设置。
    """

    session_id: str
    active: bool = False
    command_type: str | None = None
    matcher: Matcher | None = None
    current_task: asyncio.Task | None = None


@dataclass
class SelectionSession:
    """选择会话，至多一个，是 ChatSession 的子集。

    Attributes:
        selection_id: UUID，每次创建选择时生成，用于超时双重校验。
        timeout_task: 超时监控任务引用。
    """

    selection_id: str
    timeout_task: asyncio.Task | None = None


# 模块级字典
chat_sessions: dict[str, ChatSession] = {}
selection_sessions: dict[str, SelectionSession] = {}


def get_or_create_chat(user_id: str) -> ChatSession:
    """首次访问时创建并存储 ChatSession，之后复用。

    Args:
        user_id: 用户 ID。

    Returns:
        该用户的 ChatSession 实例。
    """
    if user_id not in chat_sessions:
        chat_sessions[user_id] = ChatSession(session_id=str(uuid.uuid4()))
    return chat_sessions[user_id]


def activate_chat(
    user_id: str,
    command_type: str,
    matcher: Matcher,
) -> bool:
    """激活聊天会话。

    - 设置 active=True, matcher, command_type, current_task=asyncio.current_task()
    - 返回 True=成功, False=已在活跃（调用方应拒绝新命令）
    - 注意：NoneBot2 的 handle() 和 got() 运行在不同 asyncio task 中，
      各自的 handler 入口都需要调用 activate_chat 更新 current_task。
    - handler 的 finally 块中调用 deactivate_chat 清空。

    Args:
        user_id: 用户 ID。
        command_type: 命令类型。
        matcher: NoneBot2 Matcher。

    Returns:
        True 表示成功激活，False 表示已有活跃会话。
    """
    chat = get_or_create_chat(user_id)
    if chat.active:
        return False
    chat.active = True
    chat.command_type = command_type
    chat.matcher = matcher
    chat.current_task = asyncio.current_task()
    return True


def deactivate_chat(user_id: str) -> None:
    """重置聊天会话为空闲状态。

    Args:
        user_id: 用户 ID。
    """
    chat = chat_sessions.get(user_id)
    if chat is None:
        return
    chat.active = False
    chat.command_type = None
    chat.matcher = None
    chat.current_task = None


def create_selection(
    user_id: str,
    selection_id: str,
    timeout_task: asyncio.Task,
) -> None:
    """创建选择会话。覆盖同一用户的旧选择会话。

    Args:
        user_id: 用户 ID。
        selection_id: 选择会话 ID（UUID 字符串）。
        timeout_task: 超时监控任务。
    """
    selection_sessions[user_id] = SelectionSession(
        selection_id=selection_id,
        timeout_task=timeout_task,
    )


def remove_selection(user_id: str) -> SelectionSession | None:
    """移除选择会话，返回旧会话（用于取消 timeout_task）。

    Args:
        user_id: 用户 ID。

    Returns:
        被移除的选择会话，不存在时返回 None。
    """
    return selection_sessions.pop(user_id, None)


def get_selection(user_id: str) -> SelectionSession | None:
    """查询用户的选择会话。

    Args:
        user_id: 用户 ID。

    Returns:
        该用户的选择会话，不存在时返回 None。
    """
    return selection_sessions.get(user_id)


async def execute_cancel(user_id: str) -> str | None:
    """执行取消逻辑。

    1. 检查是否有活跃会话，无则返回 None
    2. current_task.cancel()（非当前 task 且未完成时）
    3. remove_selection() + 取消 timeout_task（若有）
    4. 在旧 matcher 上 finish()（发送"会话已取消"到原上下文）
    5. deactivate_chat(user_id)

    Args:
        user_id: 用户 ID。

    Returns:
        str: 成功提示 "已取消 ✅"
        None: 无活跃会话，调用方自行发送提示
    """
    chat = chat_sessions.get(user_id)
    if not (chat and chat.active):
        return None

    # 防止自取消：同频道 /cancel 时 current_task 等于当前 task，跳过
    current = asyncio.current_task()
    if chat.current_task and not chat.current_task.done() and chat.current_task is not current:
        chat.current_task.cancel()

    # 移除选择会话 + 取消超时任务
    ss = selection_sessions.pop(user_id, None)
    if ss and ss.timeout_task and not ss.timeout_task.done():
        ss.timeout_task.cancel()

    # finish 老 matcher（发送取消消息到原上下文）
    if chat.matcher:
        try:
            await chat.matcher.finish("当前会话已取消")
        except FinishedException:
            pass

    deactivate_chat(user_id)
    return "已取消 ✅"


async def got_intercept_bypass(
    user_id: str,
    matcher: Matcher,
    text: str,
    HELP_TEXT: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。

    内部 /cancel 分支委托给 execute_cancel。

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
        result = await execute_cancel(user_id)
        if result is None:
            await matcher.finish("当前没有活跃的会话")
        else:
            await matcher.finish(result)
        return True

    if text.startswith("/help ") or text == "/help":
        await matcher.send(HELP_TEXT)
        await matcher.reject("")
        return True

    return False


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
    """会话超时检查任务。

    超时后按 user_id + selection_id 双重校验。
    匹配则发送超时提示 + remove_selection + on_cleanup。
    不匹配（被新选择或 /cancel 覆盖）则静默退出。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 原始消息事件（用于确定回复目标）。
        user_id: 用户 ID。
        selection_id: 闭包捕获的选择会话 ID。
        message: 超时提示消息。
        on_cleanup: 可选的清理回调，支持同步和异步。
        timeout: 超时秒数，为 None 时从 SESSION_EXPIRE_TIMEOUT 读取。
    """
    if timeout is None:
        timeout = read_session_timeout()
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return  # 被外部取消，静默退出

    # 双重校验：仅当 selection_id 仍然匹配时才发送超时提示
    ss = selection_sessions.get(user_id)
    if ss is not None and ss.selection_id == selection_id:
        logger.info("用户 %s 的选择会话超时（%d 秒）", user_id, timeout)
        selection_sessions.pop(user_id, None)
        if on_cleanup is not None:
            result = on_cleanup()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
        try:
            await bot.send(event, message)
        except Exception:
            logger.debug("发送超时消息失败", exc_info=True)
```

- [ ] **Step 2: 编写 session.py 新 API 测试**

```python
# tests/unit/test_session.py — 完全重写
"""bot.session 会话管理模块测试。"""

from __future__ import annotations

import asyncio
from typing import Any, Generator
from unittest.mock import MagicMock, AsyncMock

import pytest

from bot.session import (
    ChatSession,
    SelectionSession,
    activate_chat,
    chat_sessions,
    create_selection,
    deactivate_chat,
    execute_cancel,
    get_or_create_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
    selection_sessions,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    """每个测试前清空会话字典。"""
    chat_sessions.clear()
    selection_sessions.clear()
    yield
    chat_sessions.clear()
    selection_sessions.clear()


class TestGetOrCreateChat:
    """get_or_create_chat 测试。"""

    def test_creates_new(self):
        """首次调用创建新 ChatSession。"""
        chat = get_or_create_chat("user1")
        assert isinstance(chat, ChatSession)
        assert chat.active is False

    def test_reuses_existing(self):
        """重复调用返回同一实例。"""
        chat1 = get_or_create_chat("user1")
        chat2 = get_or_create_chat("user1")
        assert chat1 is chat2
        assert chat1.session_id == chat2.session_id


class TestActivateChat:
    """activate_chat 测试。"""

    def test_activate_success(self):
        """正常激活返回 True。"""
        matcher = MagicMock()
        result = activate_chat("user1", "search", matcher)
        assert result is True
        chat = get_or_create_chat("user1")
        assert chat.active is True
        assert chat.command_type == "search"
        assert chat.matcher is matcher
        assert chat.current_task is asyncio.current_task()

    def test_activate_fails_when_active(self):
        """已有活跃会话时返回 False。"""
        matcher1 = MagicMock()
        matcher2 = MagicMock()
        activate_chat("user1", "add", matcher1)
        result = activate_chat("user1", "search", matcher2)
        assert result is False
        # 状态不应被覆盖
        chat = get_or_create_chat("user1")
        assert chat.command_type == "add"
        assert chat.matcher is matcher1


class TestDeactivateChat:
    """deactivate_chat 测试。"""

    def test_deactivate_active(self):
        """重置活跃会话为空闲。"""
        matcher = MagicMock()
        activate_chat("user1", "add", matcher)
        deactivate_chat("user1")
        chat = get_or_create_chat("user1")
        assert chat.active is False
        assert chat.command_type is None
        assert chat.matcher is None
        assert chat.current_task is None

    def test_deactivate_nonexistent(self):
        """对不存在的用户调用不报错。"""
        deactivate_chat("nonexistent")  # 不抛异常即可


class TestSelectionSession:
    """create_selection / remove_selection / get_selection 测试。"""

    def test_create_and_get(self):
        """创建选择会话后可查询。"""
        task = asyncio.get_event_loop().create_task(asyncio.sleep(999))
        create_selection("user1", "sel_001", task)
        ss = get_selection("user1")
        assert ss is not None
        assert ss.selection_id == "sel_001"
        assert ss.timeout_task is task

    def test_create_overwrites(self):
        """重复创建覆盖旧选择会话。"""
        task1 = asyncio.get_event_loop().create_task(asyncio.sleep(999))
        task2 = asyncio.get_event_loop().create_task(asyncio.sleep(999))
        create_selection("user1", "sel_001", task1)
        create_selection("user1", "sel_002", task2)
        ss = get_selection("user1")
        assert ss.selection_id == "sel_002"

    def test_remove_returns_old(self):
        """remove_selection 返回旧会话且从字典中移除。"""
        task = asyncio.get_event_loop().create_task(asyncio.sleep(999))
        create_selection("user1", "sel_001", task)
        removed = remove_selection("user1")
        assert removed is not None
        assert removed.selection_id == "sel_001"
        assert get_selection("user1") is None

    def test_get_nonexistent(self):
        """不存在时返回 None。"""
        assert get_selection("no_user") is None


class TestExecuteCancel:
    """execute_cancel 测试。"""

    async def test_no_active_session(self):
        """无活跃会话时返回 None。"""
        result = await execute_cancel("user1")
        assert result is None

    async def test_cancel_active_chat(self):
        """取消活跃会话返回提示。"""
        matcher = MagicMock()
        activate_chat("user1", "add", matcher)
        result = await execute_cancel("user1")
        assert result == "已取消 ✅"
        chat = get_or_create_chat("user1")
        assert chat.active is False

    async def test_cancel_cleans_up_selection(self):
        """取消时清除选择会话。"""
        task = asyncio.get_event_loop().create_task(asyncio.sleep(999))
        matcher = MagicMock()
        activate_chat("user1", "search", matcher)
        create_selection("user1", "sel_001", task)
        await execute_cancel("user1")
        assert get_selection("user1") is None


class TestGotInterceptBypass:
    """got_intercept_bypass 测试。"""

    async def test_normal_text_returns_false(self):
        """普通文本返回 False。"""
        matcher = AsyncMock()
        result = await got_intercept_bypass(
            "user1", matcher, "hello", "帮助文本"
        )
        assert result is False

    async def test_help_returns_true(self):
        """/help 拦截后返回 True。"""
        matcher = AsyncMock()
        result = await got_intercept_bypass(
            "user1", matcher, "/help", "帮助文本"
        )
        assert result is True
        matcher.send.assert_called_once_with("帮助文本")

    async def test_cancel_returns_true(self):
        """/cancel 拦截后返回 True。"""
        matcher = MagicMock()
        activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass(
            "user1", matcher, "/cancel", "帮助文本"
        )
        assert result is True

    async def test_help_with_args_matches(self):
        """/help xxx（带参数）也匹配帮助。"""
        matcher = AsyncMock()
        result = await got_intercept_bypass(
            "user1", matcher, "/help 加班", "帮助文本"
        )
        assert result is True

    async def test_cancel_with_args_matches(self):
        """/cancel xxx（带参数）也匹配取消。"""
        matcher = MagicMock()
        activate_chat("user1", "add", matcher)
        result = await got_intercept_bypass(
            "user1", matcher, "/cancel something", "帮助文本"
        )
        assert result is True
```

- [ ] **Step 3: 运行测试验证旧接口不可用**

```bash
uv run pytest tests/unit/test_session.py -v
```
Expected: PASS（新测试文件内容都是对 session.py 新 API 的测试，需等 Step 1 写完才能通过）

- [ ] **Step 4: 运行完整测试套件**

```bash
uv run pytest tests/unit/test_session.py tests/unit/engine/test_index_manager.py -v
```
Expected: all PASS

---

### Task 3: meme_cancel.py — 新增 /cancel 命令

**Files:**
- Create: `bot/plugins/meme_cancel.py`
- Create: `tests/unit/plugins/test_meme_cancel.py`

- [ ] **Step 1: 编写测试**

```python
# tests/unit/plugins/test_meme_cancel.py
"""meme_cancel 插件测试。"""

from __future__ import annotations

from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.session import activate_chat, chat_sessions, selection_sessions


@pytest.fixture(autouse=True)
def _clear_sessions() -> Generator[None, Any, None]:
    chat_sessions.clear()
    selection_sessions.clear()
    yield
    chat_sessions.clear()
    selection_sessions.clear()


class TestCancelCommand:
    """/cancel 命令测试。"""

    async def test_cancel_with_active_session(self):
        """有活跃会话时取消成功。"""
        from bot.plugins.meme_cancel import handle_cancel

        matcher = MagicMock()
        activate_chat("user1", "add", matcher)

        bot = AsyncMock()
        event = MagicMock()
        event.get_user_id.return_value = "user1"
        event.get_plaintext.return_value = "/cancel"

        # 模拟授权
        with patch("bot.plugins.meme_cancel.is_authorized", return_value=True):
            await handle_cancel(bot, event, matcher)

        # 验证会话已取消
        chat = chat_sessions.get("user1")
        assert chat is None or chat.active is False

    async def test_cancel_without_active_session(self):
        """无活跃会话时提示。"""
        from bot.plugins.meme_cancel import handle_cancel

        matcher = MagicMock()
        bot = AsyncMock()
        event = MagicMock()
        event.get_user_id.return_value = "user1"

        with patch("bot.plugins.meme_cancel.is_authorized", return_value=True):
            await handle_cancel(bot, event, matcher)

        matcher.finish.assert_called_once()
        args = matcher.finish.call_args[0][0]
        assert "没有活跃" in args

    async def test_unauthorized(self):
        """未授权用户静默忽略。"""
        from bot.plugins.meme_cancel import handle_cancel

        matcher = MagicMock()
        bot = AsyncMock()
        event = MagicMock()
        event.get_user_id.return_value = "unauthorized"

        with patch("bot.plugins.meme_cancel.is_authorized", return_value=False):
            await handle_cancel(bot, event, matcher)

        matcher.finish.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_meme_cancel.py -v
```
Expected: FAIL — `bot.plugins.meme_cancel` 模块不存在

- [ ] **Step 3: 创建 meme_cancel.py**

```python
# bot/plugins/meme_cancel.py
"""/cancel 命令插件 — 取消当前活跃会话。

授权用户在任何状态（包括 got 等待中）发送 /cancel 时，
通过 execute_cancel 取消正在进行的命令会话。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.session import execute_cancel

logger = logging.getLogger(__name__)

cancel_cmd = on_command("cancel", rule=to_me(), priority=5, block=True)


@cancel_cmd.handle()
async def handle_cancel(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/cancel 命令处理入口。

    execute_cancel 内部处理自取消（同频道）和跨 task 取消的逻辑。
    此 handler 只负责授权校验和结果转发。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /cancel", user_id)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "cancel")
        return

    result = await execute_cancel(user_id)
    if result is None:
        await matcher.finish("当前没有活跃的会话")
    else:
        await matcher.finish(result)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_meme_cancel.py -v
```
Expected: PASS

---

### Task 4: _search_utils.py — 改用新 session API

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Modify: `tests/unit/plugins/test_search_utils.py`

**变更：** `execute_search` 改用 `create_selection` + `selection_id` 传递链。

- [ ] **Step 1: 修改 _search_utils.py**

关键变更点：

```python
# 旧导入
from bot.session import pending_sessions, register, timeout_session

# 新导入
import uuid
from bot.session import (
    chat_sessions,
    create_selection,
    deactivate_chat,
    get_selection,
    remove_selection,
    timeout_session,
)
```

`execute_search` 函数变更：

```python
async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
    # ... 前面部分不变：锁检查、索引空检查、搜索执行 ...

    # 是否单个结果 —— 同上
    if not results:
        deactivate_chat(user_id)  # 新增：单结果时清理会话
        await cmd_matcher.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        deactivate_chat(user_id)  # 新增：单结果时清理会话
        image_path = MEMES_DIR / results[0].filename
        await cmd_matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        return

    # 多个结果：格式化选择列表
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.text}")
    lines.append(f"回复编号即可 (1-{len(results)})")

    # 存储候选并创建选择会话
    cmd_matcher.state["candidates"] = results
    selection_id = str(uuid.uuid4())
    cmd_matcher.state["selection_id"] = selection_id

    await cmd_matcher.send("\n".join(lines))

    # 启动超时任务
    task = asyncio.create_task(
        timeout_session(bot, event, user_id, selection_id, "选择已过期，请重新搜索")
    )
    create_selection(user_id, selection_id, task)
```

- [ ] **Step 2: 更新测试文件**

```python
# tests/unit/plugins/test_search_utils.py 更新导入和 mock
# 将 mock `register` 改为 mock `create_selection`
```

- [ ] **Step 3: 运行搜索相关测试**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py tests/unit/plugins/test_meme_search.py -v
```
Expected: FAIL（因 meme_search.py 和 meme_plain_text.py 尚未更新）

---

### Task 5: meme_add.py — 去掉锁代码，加入会话检查 + got 拦截

**Files:**
- Modify: `bot/plugins/meme_add.py`
- Modify: `tests/unit/plugins/test_meme_add.py`

**变更点：**
1. 导入替换：`check_and_cancel` → `activate_chat`/`deactivate_chat`，去掉锁相关导入
2. `handle_add` 入口调用 `activate_chat`，活跃时拒绝
3. 去掉 `acquire_lock()`/`release_lock()` 全部操作
4. `got_image` 入口调用 `got_intercept_bypass`
5. `finally` 块中去掉锁释放

- [ ] **Step 1: 修改 handle_add**

```python
# meme_add.py 导入变更
from bot.session import (
    activate_chat,
    deactivate_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
)
# 删除：
#   cancel, cancel_timeout_task, check_and_cancel,
#   is_cancelled, pending_sessions, register, timeout_session
# 删除锁相关导入：IndexManager 导入保留但不用于锁
```

```python
@add_cmd.handle()
async def handle_add(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /add", user_id)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "add")
        return

    if event.message_type != "private":
        await matcher.finish("此命令仅限私聊使用")
        return

    # 会话检查：拒绝而非覆盖
    if not activate_chat(user_id, "add", matcher):
        await matcher.finish("已有命令在处理中，请先 /cancel")
        return

    # 获取 IndexManager（仅用于检查索引状态，不再获取锁）
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        deactivate_chat(user_id)
        await matcher.finish("服务未就绪，请稍后再试")
        return

    # 检查同步锁（只读检查，不持有）
    if index_manager.is_locked:
        deactivate_chat(user_id)
        await matcher.finish("索引正在更新，请稍后再试")
        return

    # 捕获目标命名
    raw_text = event.get_plaintext().strip()
    target_name = raw_text.removeprefix("/add").removeprefix("add").strip()
    matcher.state["target_name"] = target_name

    # 不再注册 session 或启动超时——后续 got 中用 got_intercept_bypass 处理
```

```python
@add_cmd.got("image", prompt="请发送图片，60 秒内有效")
async def got_image(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    image_msg: Message = Arg("image"),
) -> None:
    user_id = event.get_user_id()

    # got 入口重新激活 chat session（不同 asyncio task）
    activate_chat(user_id, "add", matcher)

    try:
        # ── 阶段 0：/help 和 /cancel 旁路拦截 ──
        text = event.get_plaintext().strip()
        if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
            if text.startswith("/cancel") or text == "/cancel":
                deactivate_chat(user_id)
            return

        # ── 阶段 1：图片验证 ──
        try:
            urls = extract_image_urls(image_msg)
        except Exception:
            logger.exception("extract_image_urls 异常")
            deactivate_chat(user_id)
            raise

        if not urls:
            await matcher.reject("请发送一张图片")
            return

        # ... 只读检查 is_locked ...
        if index_manager.is_locked:
            deactivate_chat(user_id)
            await matcher.finish("索引正在更新，请稍后再试")
            return

        # ── 阶段 2：处理流程（下载/OCR/Embedding） ──
        # ... 不变：下载图片、保存、调用 add_single_file ...

        # ── 成功/失败后清理 ──
        deactivate_chat(user_id)
        # ... finish ...

    except (FinishedException, RejectedException):
        deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的 /add 处理异常", user_id)
        deactivate_chat(user_id)
        raise
```

关键变更：
- 不再锁管理（`acquire_lock`/`release_lock`/`_release_lock_safe` 全部移除）
- `finally` 块改为 `deactivate_chat`（而非锁释放）
- 图片下载失败等 case 也可以直接 `deactivate_chat` + `finish`
- `is_cancelled` 检查替换为 `deactivate_chat` + 自然返回

- [ ] **Step 2: 更新 test_meme_add.py**

```python
# tests/unit/plugins/test_meme_add.py
# 主要更新：mock `activate_chat` 替代 `check_and_cancel`
# mock `got_intercept_bypass` 替代 `is_cancelled`
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_add.py -v
```
Expected: PASS

---

### Task 6: meme_search.py — 加入会话检查 + got 拦截

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

**变更点：** 与 meme_add.py 类似，handle_search 入口加 `activate_chat`，got_selection 入口加 `got_intercept_bypass`。

- [ ] **Step 1: 修改 meme_search.py**

```python
# 导入变更
from bot.session import (
    activate_chat,
    deactivate_chat,
    got_intercept_bypass,
)
# 删除：cancel, cancel_timeout_task, check_and_cancel, is_cancelled
```

```python
@search_cmd.handle()
async def handle_search(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /search", user_id)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "search")
        return

    # 拒绝而非覆盖
    if not activate_chat(user_id, "search", matcher):
        await matcher.finish("已有命令在处理中，请先 /cancel")
        return

    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/search").removeprefix("search").strip()
    if not keyword:
        deactivate_chat(user_id)
        await search_cmd.finish("/search <关键词>")
        return

    logger.info("用户 %s 搜索关键词: %r", user_id, keyword)
    await execute_search(bot, event, matcher, keyword)
```

```python
@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    user_id = event.get_user_id()

    # got 入口重新激活
    activate_chat(user_id, "search", matcher)

    try:
        # /help 和 /cancel 旁路拦截
        text = event.get_plaintext().strip()
        if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
            if text.startswith("/cancel") or text == "/cancel":
                deactivate_chat(user_id)
            return

        # 检查选择会话是否仍有效
        ss = get_selection(user_id)
        if ss is None:
            deactivate_chat(user_id)
            await matcher.finish("选择已过期，请重新搜索")
            return

        candidates = matcher.state.get("candidates", [])
        selection_text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, selection_text)
        if isinstance(result, str):
            await matcher.reject(result)
            return

        # 有效选择：清除选择会话
        remove_selection(user_id)
        deactivate_chat(user_id)

        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )

    except (FinishedException, RejectedException):
        deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的 /search 处理异常", user_id)
        deactivate_chat(user_id)
        raise
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_search.py -v
```
Expected: PASS

---

### Task 7: meme_plain_text.py — 加入会话检查 + got 拦截

**Files:**
- Modify: `bot/plugins/meme_plain_text.py`
- Modify: `tests/unit/plugins/test_meme_plain_text.py`

**变更点：** 与 meme_search.py 类似。

- [ ] **Step 1: 修改 meme_plain_text.py**

```python
# 导入变更
from bot.session import (
    activate_chat,
    deactivate_chat,
    get_selection,
    got_intercept_bypass,
    remove_selection,
)
# 删除：cancel, cancel_timeout_task, check_and_cancel, is_cancelled
```

```python
@catch_all.handle()
async def handle_plain_text(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    user_id = event.get_user_id()
    text = event.get_plaintext().strip()
    logger.info("兜底处理用户 %s 消息: %r", user_id, text)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "plain_text")
        return

    if text.startswith("/"):
        await catch_all.finish(f"未知命令\n\n{HELP_TEXT}")
        return

    # 会话检查：拒绝而非覆盖
    if not activate_chat(user_id, "search", matcher):
        await matcher.send("已有命令在处理中，请先 /cancel")
        return

    await execute_search(bot, event, matcher, text)
```

```python
@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    user_id = event.get_user_id()

    # got 入口重新激活
    activate_chat(user_id, "search", matcher)

    try:
        # /help 和 /cancel 旁路拦截
        text = event.get_plaintext().strip()
        if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
            if text.startswith("/cancel") or text == "/cancel":
                deactivate_chat(user_id)
            return

        ss = get_selection(user_id)
        if ss is None:
            deactivate_chat(user_id)
            await matcher.finish("选择已过期，请重新搜索")
            return

        candidates = matcher.state.get("candidates", [])
        selection_text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, selection_text)
        if isinstance(result, str):
            await catch_all.reject(result)
            return

        remove_selection(user_id)
        deactivate_chat(user_id)
        image_path = MEMES_DIR / result.filename
        await catch_all.finish(MessageSegment.image("file://" + str(image_path.resolve())))

    except (FinishedException, RejectedException):
        deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的兜底搜索处理异常", user_id)
        deactivate_chat(user_id)
        raise
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_plain_text.py -v
```
Expected: PASS

---

### Task 8: meme_ai.py — 加入会话检查

**Files:**
- Modify: `bot/plugins/meme_ai.py`

**变更点：** handle_ai 入口加 `activate_chat`（仅 pass-through，无 got handler）。

```python
# 导入新增
from bot.session import activate_chat

# handle_ai 开头，授权校验之后：
if not activate_chat(user_id, "ai", ai_cmd):
    await ai_cmd.finish("已有命令在处理中，请先 /cancel")
    return

# handle_ai 末尾成功/失败路径加 deactivate_chat:
deactivate_chat(user_id)
# 并在所有提前 finish 前也加上 deactivate_chat
```

- [ ] **Step 1: 修改 meme_ai.py**

```python
# 导入变更
from bot.session import activate_chat, deactivate_chat

# handle_ai 函数体：
@ai_cmd.handle()
async def handle_ai(bot: Bot, event: MessageEvent) -> None:
    user_id = event.get_user_id()

    if not is_authorized(user_id):
        log_unauthorized(user_id, "ai")
        return

    if event.message_type != "private":
        await ai_cmd.finish("此命令仅限私聊使用")
        return

    # 会话检查
    if not activate_chat(user_id, "ai", ai_cmd):
        await ai_cmd.finish("已有命令在处理中，请先 /cancel")
        return

    try:
        # ... 后续所有 finish 前加 deactivate_chat ...

        # 索引锁检查
        if index_manager.is_locked:
            deactivate_chat(user_id)
            await ai_cmd.finish("索引正在更新，请稍后再试")
            return

        # 描述为空
        if not description:
            deactivate_chat(user_id)
            await ai_cmd.finish("/ai <自然语言描述>")
            return

        # 索引空
        if index_manager.entry_count == 0:
            deactivate_chat(user_id)
            await ai_cmd.finish("表情包目录为空，请先添加图片并执行 /refresh")
            return

        # ... AI 匹配 ...

        if isinstance(match_result, str):
            deactivate_chat(user_id)
            await ai_cmd.finish(match_result)
            return

        image_path = MEMES_DIR / match_result.filename
        deactivate_chat(user_id)
        await ai_cmd.finish(MessageSegment.image("file://" + str(image_path.resolve())))
    except Exception:
        deactivate_chat(user_id)
        raise
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_ai.py -v
```
Expected: PASS

---

### Task 9: meme_refresh.py — 加入会话检查，锁管理改为内部

**Files:**
- Modify: `bot/plugins/meme_refresh.py`

**变更点：** handle_refresh 入口加 `activate_chat`。锁管理逻辑保持不变（refresh 仍使用 acquire_lock/release_lock）。

```python
# 导入新增
from bot.session import activate_chat, deactivate_chat

# handle_refresh 开头
if not activate_chat(user_id, "refresh", refresh_cmd):
    await refresh_cmd.finish("已有命令在处理中，请先 /cancel")
    return

# try 块最后加 deactivate_chat
try:
    ...
    result = await index_manager.sync_with_filesystem()
except Exception:
    ...
finally:
    deactivate_chat(user_id)
    index_manager.release_lock()
```

- [ ] **Step 1: 修改 meme_refresh.py**

```python
# 导入变更
from bot.session import activate_chat, deactivate_chat

@refresh_cmd.handle()
async def handle_refresh(bot: Bot, event: MessageEvent) -> None:
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /refresh", user_id)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "refresh")
        return

    if event.message_type != "private":
        await refresh_cmd.finish("此命令仅限私聊使用")
        return

    if not activate_chat(user_id, "refresh", refresh_cmd):
        await refresh_cmd.finish("已有命令在处理中，请先 /cancel")
        return

    try:
        index_manager = get_index_manager()
    except RuntimeError:
        deactivate_chat(user_id)
        await refresh_cmd.finish("服务未就绪，请稍后再试")
        return

    if not await index_manager.acquire_lock():
        deactivate_chat(user_id)
        await refresh_cmd.finish("索引正在更新，请稍后再试")
        return

    try:
        await bot.send(event, "正在刷新索引，请稍候...")
        result = await index_manager.sync_with_filesystem()
    except Exception:
        logger.exception("sync_with_filesystem 执行失败")
        await refresh_cmd.finish("索引刷新失败，请查看日志")
        return
    finally:
        deactivate_chat(user_id)
        index_manager.release_lock()

    # ... 格式化结果部分不变 ...
```

- [ ] **Step 2: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_refresh.py -v
```
Expected: PASS

---

### Task 10: _help_text.py — 加入 /cancel 帮助项

**Files:**
- Modify: `bot/plugins/_help_text.py`

- [ ] **Step 1: 修改 _help_text.py**

```python
HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [目标命名]：通过聊天添加一张表情包
/refresh：扫描 memes/ 并增量更新索引
/cancel：取消当前正在执行的命令"""
```

- [ ] **Step 2: （无需测试，纯文本变更）**

---

### Task 11: 集成测试

**Files:**
- Run existing full test suite

- [ ] **Step 1: 运行全部单元测试**

```bash
uv run pytest tests/ -v 2>&1 | head -100
```

Expected: 所有测试通过（约 300+ tests）。

- [ ] **Step 2: 语法校验**

```bash
uv run python -m compileall bot tests
```
Expected: 全部通过，无语法错误。

---

### Task 12: 文档同步

**Files:**
- Modify: `docs/api/API.md`

- [ ] **Step 1: 更新 API.md**

根据各模块最终接口更新 `docs/api/API.md`，包括：
- `bot/session.py` — 完全替换为 ChatSession/SelectionSession/新函数签名
- `bot/engine/index_manager.py` — 更新锁相关 API

---

## 自审检查

### Spec 覆盖度

| Spec 要求 | 实现 Task |
|-----------|----------|
| ChatSession + SelectionSession 分离 | Task 2 |
| 每个用户一个聊天会话，至多一个选择会话 | Task 2（模块级字典） |
| session_id + selection_id 超时双重校验 | Task 2（timeout_session） |
| 活跃会话拒绝新命令 | Task 5-9（activate_chat 返回值检查） |
| /cancel 和 /help 旁路 | Task 2（got_intercept_bypass）+ Task 3（meme_cancel）|
| 锁范围最小化 | Task 1（index_manager） |
| execute_cancel 共享函数 | Task 2 |
| got_intercept_bypass 共享函数 | Task 2 |
| timeout_session 支持 selection_id | Task 2 |

### 占位符扫描

- 所有步骤包含完整代码块
- 所有命令包含预期输出
- 无 "TBD"/"TODO"/"实现细节"/"类似 Task X" 等模式
- 所有导入语句列出具体函数名

### 类型一致性

- `execute_cancel` 返回 `str | None` — 在 Task 2 定义，Task 3 使用，一致
- `got_intercept_bypass` 返回 `bool` — 在 Task 2 定义，Task 5-7 使用，一致
- `activate_chat` 返回 `bool` — 一致
- `create_selection` 接受 `(user_id, selection_id, task)` — Task 2 定义，Task 4 调用，一致
- `timeout_session` 签名增加 `selection_id` 参数 — 一致
