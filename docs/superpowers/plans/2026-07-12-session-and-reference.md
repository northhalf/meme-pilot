# 会话作用域拆分与群聊列表引用实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `SessionManager` 的会话键从单一 `user_id` 改造为 `ChatScope`（用户 + 聊天窗口），并在群聊候选列表及错误提示中通过 OneBot V11 `reply` 消息段引用相关消息。

**Architecture：** 新增不可变可哈希的 `ChatScope` 值对象，统一从 `MessageEvent` 提取作用域；`SessionManager` 内部字典直接使用 `ChatScope` 作键；所有插件统一先构造 `ChatScope` 再调用会话方法；`_search_utils.py` 中新增 `reject_with_reply` 辅助函数，统一处理群聊中的消息引用。

**Tech Stack：** Python 3.12、NoneBot2、nonebot-adapter-onebot v11、pytest、pytest-asyncio。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `bot/session.py` | 新增 `ChatScope`；改造 `SessionManager` 与 `timeout_session` 使用 `ChatScope` 作键。 |
| `tests/unit/test_session.py` | 更新为使用 `ChatScope`；新增群聊/私聊隔离用例。 |
| `tests/unit/test_session_manager.py` | 更新为使用 `ChatScope`。 |
| `bot/plugins/_search_utils.py` | `present_candidates` 增加 `scope` 与 `reply_message_id`；新增 `reject_with_reply`；所有函数传递 `scope`。 |
| `tests/unit/plugins/` | 更新涉及 `_search_utils` 的测试，补充 `scope` 与 reply 断言。 |
| `bot/plugins/meme_plain_text.py` | 使用 `ChatScope.from_event` 构造 `scope` 并传递给会话方法和 `_search_utils`。 |
| `bot/plugins/meme_rand.py` | 同上；换一批时传入用户 "0" 消息 ID。 |
| `bot/plugins/meme_query.py` | 同上。 |
| `bot/plugins/meme_sim.py` | 同上。 |
| `bot/plugins/meme_add.py` | 同上；图片等待阶段使用 `scope`。 |
| `bot/plugins/meme_addtag.py` | 同上。 |
| `bot/plugins/meme_delete.py` | 同上。 |
| `bot/plugins/meme_edit.py` | 同上。 |
| `bot/plugins/meme_setspeaker.py` | 同上。 |
| `bot/plugins/meme_cancel.py` | 使用 `ChatScope.from_event` 构造 `scope` 后执行取消。 |
| `docs/api/bot/session.md` | 更新 `ChatScope` 与 `SessionManager` 签名。 |
| `docs/PRD.md` | 更新会话互斥描述。 |

---

## Task 1: 核心改造 — `ChatScope` 与 `SessionManager`

**Files:**
- Modify: `bot/session.py`
- Modify: `tests/unit/test_session.py`
- Modify: `tests/unit/test_session_manager.py`

- [ ] **Step 1: 编写失败的 `ChatScope` 与 `SessionManager` 测试**

在 `tests/unit/test_session.py` 顶部新增：

```python
from bot.session import ChatScope


def _private_scope(user_id: int = 10001) -> ChatScope:
    return ChatScope(user_id=user_id, chat_type="private", chat_id=user_id)


def _group_scope(user_id: int = 10001, group_id: int = 20001) -> ChatScope:
    return ChatScope(user_id=user_id, chat_type="group", chat_id=group_id)
```

将 `test_session.py` 中所有字符串 `"user1"` 替换为 `_private_scope()`，所有 `session_manager.get_or_create_chat("user1")` 等调用替换为 `session_manager.get_or_create_chat(_private_scope())`。

新增隔离测试：

```python
class TestChatScopeIsolation:
    def test_same_user_different_groups_are_isolated(self):
        """同一用户在不同群聊为不同会话。"""
        group_a = _group_scope(group_id=20001)
        group_b = _group_scope(group_id=20002)
        chat_a = session_manager.get_or_create_chat(group_a)
        chat_b = session_manager.get_or_create_chat(group_b)
        assert chat_a is not chat_b

    def test_same_user_private_and_group_are_isolated(self):
        """同一用户私聊和群聊为不同会话。"""
        private = _private_scope()
        group = _group_scope()
        chat_private = session_manager.get_or_create_chat(private)
        chat_group = session_manager.get_or_create_chat(group)
        assert chat_private is not chat_group

    def test_chatscope_is_hashable(self):
        """ChatScope 可作为 dict 键。"""
        scope = _private_scope()
        d = {scope: "value"}
        assert d[scope] == "value"
        same_scope = ChatScope(user_id=10001, chat_type="private", chat_id=10001)
        assert d[same_scope] == "value"
```

在 `tests/unit/test_session_manager.py` 中更新：

```python
from bot.session import ChatScope, ChatSession, session_manager


def _private_scope(user_id: int = 10001) -> ChatScope:
    return ChatScope(user_id=user_id, chat_type="private", chat_id=user_id)


class TestHasActiveSession:
    def test_has_active_session_true_after_activate(self):
        chat = ChatSession(session_id="test-id")
        chat.active = True
        session_manager._chat_sessions[_private_scope()] = chat
        assert session_manager.has_active_session() is True

    def test_has_active_session_false_after_deactivate(self):
        chat = ChatSession(session_id="test-id")
        chat.active = True
        session_manager._chat_sessions[_private_scope()] = chat
        session_manager.deactivate_chat(_private_scope())
        assert session_manager.has_active_session() is False
```

运行测试，预期大量失败：

```bash
uv run pytest tests/unit/test_session.py tests/unit/test_session_manager.py -v
```

Expected: FAIL（`ChatScope` 未定义，方法签名不匹配）。

- [ ] **Step 2: 实现 `ChatScope` 与改造 `SessionManager`**

修改 `bot/session.py`：

1. 在文件顶部新增导入：

```python
from dataclasses import dataclass
from typing import Literal
```

2. 在 `ChatSession` 之前新增 `ChatScope`：

```python
@dataclass(frozen=True, slots=True)
class ChatScope:
    """聊天作用域：一个用户在一个聊天窗口内的会话范围。"""

    user_id: int
    chat_type: Literal["private", "group"]
    chat_id: int

    def __str__(self) -> str:
        return f"{self.chat_type}:{self.chat_id}:user:{self.user_id}"

    @classmethod
    def from_event(cls, event: MessageEvent) -> "ChatScope":
        """从 NoneBot2 OneBot V11 消息事件构造作用域。"""
        user_id = int(event.get_user_id())
        message_type = getattr(event, "message_type", None)
        if message_type == "group":
            group_id = getattr(event, "group_id", None)
            if group_id is None:
                raise ValueError("群聊事件缺少 group_id")
            return cls(user_id=user_id, chat_type="group", chat_id=int(group_id))
        return cls(user_id=user_id, chat_type="private", chat_id=user_id)
```

3. 修改 `SessionManager.__init__`：

```python
def __init__(self) -> None:
    self._chat_sessions: dict[ChatScope, ChatSession] = {}
    self._selection_sessions: dict[ChatScope, SelectionSession] = {}
```

4. 修改所有公共方法签名，将 `user_id: str` 改为 `scope: ChatScope`，内部使用 `scope` 代替原来的 `user_id`：

```python
def get_or_create_chat(self, scope: ChatScope) -> ChatSession:
    if scope not in self._chat_sessions:
        self._chat_sessions[scope] = ChatSession(session_id=str(uuid.uuid4()))
    return self._chat_sessions[scope]

def activate_chat(
    self, scope: ChatScope, command_type: str, matcher: Matcher
) -> bool: ...

def deactivate_chat(self, scope: ChatScope) -> None: ...

def create_selection(
    self, scope: ChatScope, selection_id: str, timeout_task: asyncio.Task
) -> None: ...

def remove_selection(self, scope: ChatScope) -> SelectionSession | None: ...

def get_selection(self, scope: ChatScope) -> SelectionSession | None: ...

def set_current_task(
    self, scope: ChatScope, task: asyncio.Task | None
) -> None: ...

def reset_current_task(self, scope: ChatScope) -> None: ...

@contextmanager
def handler_context(self, scope: ChatScope, matcher: Matcher): ...

async def execute_cancel(
    self, scope: ChatScope, message: str = "当前会话已取消"
) -> bool: ...
```

5. 修改 `timeout_session` 签名：

```python
async def timeout_session(
    bot: Bot,
    event: Event,
    scope: ChatScope,
    selection_id: str,
    message: str,
    *,
    on_cleanup: Callable[[], Any | Awaitable[Any]] | None = None,
    timeout: int | None = None,
) -> None:
    ...
    ss = session_manager.get_selection(scope)
    if ss is not None and ss.selection_id == selection_id:
        ...
        session_manager.remove_selection(scope)
        session_manager.deactivate_chat(scope)
        ...
```

实现逻辑保持不变，仅替换所有 `user_id` 为 `scope`。

- [ ] **Step 3: 运行核心测试**

```bash
uv run pytest tests/unit/test_session.py tests/unit/test_session_manager.py -v
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add bot/session.py tests/unit/test_session.py tests/unit/test_session_manager.py
git commit -m "refactor(session): introduce ChatScope and use it as session key"
```

---

## Task 2: 搜索工具改造 — `present_candidates` 与 `reject_with_reply`

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Modify: `tests/unit/plugins/`（涉及 `_search_utils` 的测试）

- [ ] **Step 1: 编写失败的搜索工具测试**

在 `tests/unit/plugins/` 下创建或更新测试文件，例如 `tests/unit/plugins/test_search_utils_scope.py`：

```python
"""_search_utils 的 ChatScope 与 reply 行为测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from bot.plugins._search_utils import (
    PresentOptions,
    format_metadata_line,
    reject_with_reply,
)
from bot.session import ChatScope
from bot.engine.types import SearchResult


def _group_scope(user_id: int = 10001, group_id: int = 20001) -> ChatScope:
    return ChatScope(user_id=user_id, chat_type="group", chat_id=group_id)


def _private_scope(user_id: int = 10001) -> ChatScope:
    return ChatScope(user_id=user_id, chat_type="private", chat_id=user_id)


@pytest.mark.asyncio
async def test_reject_with_reply_in_group():
    """群聊中 reject 应附加 reply 消息段。"""
    matcher = MagicMock()
    matcher.reject = AsyncMock()
    scope = _group_scope()
    await reject_with_reply(matcher, scope, 12345, "无效编号")

    matcher.reject.assert_awaited_once()
    message = matcher.reject.await_args[0][0]
    assert isinstance(message, Message)
    assert message[0].type == "reply"
    assert message[0].data["id"] == 12345
    assert message[1].type == "text"


@pytest.mark.asyncio
async def test_reject_with_reply_in_private():
    """私聊中 reject 保持纯文本。"""
    matcher = MagicMock()
    matcher.reject = AsyncMock()
    scope = _private_scope()
    await reject_with_reply(matcher, scope, 12345, "无效编号")
    matcher.reject.assert_awaited_once_with("无效编号")


@pytest.mark.asyncio
async def test_present_candidates_in_group_has_reply():
    """群聊中展示候选列表应附加 reply 消息段。"""
    bot = MagicMock()
    event = MagicMock()
    event.get_user_id.return_value = "10001"
    event.message_type = "group"
    event.group_id = 20001
    event.message_id = 99999

    matcher = MagicMock()
    matcher.send = AsyncMock()

    scope = ChatScope.from_event(event)
    candidates = [
        SearchResult(
            entry_id=1,
            image_path="a.webp",
            text="test",
            similarity=1.0,
            speaker=None,
            tags=[],
        )
    ]

    await present_candidates(
        bot,
        event,
        matcher,
        candidates,
        scope,
        reply_message_id=event.message_id,
    )

    matcher.send.assert_awaited_once()
    message = matcher.send.await_args[0][0]
    assert isinstance(message, Message)
    assert message[0].type == "reply"
    assert message[0].data["id"] == 99999


@pytest.mark.asyncio
async def test_present_candidates_in_private_no_reply():
    """私聊中展示候选列表不包含 reply。"""
    bot = MagicMock()
    event = MagicMock()
    event.get_user_id.return_value = "10001"
    event.message_type = "private"
    event.message_id = 99999

    matcher = MagicMock()
    matcher.send = AsyncMock()

    scope = ChatScope.from_event(event)
    candidates = [
        SearchResult(
            entry_id=1,
            image_path="a.webp",
            text="test",
            similarity=1.0,
            speaker=None,
            tags=[],
        )
    ]

    await present_candidates(
        bot,
        event,
        matcher,
        candidates,
        scope,
        reply_message_id=event.message_id,
    )

    matcher.send.assert_awaited_once()
    message = matcher.send.await_args[0][0]
    assert isinstance(message, MessageSegment)
    assert message.type == "text"
```

运行：

```bash
uv run pytest tests/unit/plugins/test_search_utils_scope.py -v
```

Expected: FAIL（`reject_with_reply` 未定义）。

- [ ] **Step 2: 改造 `_search_utils.py`**

修改 `bot/plugins/_search_utils.py`：

1. 在导入区新增：

```python
from bot.session import ChatScope
```

2. 新增 `reject_with_reply`：

```python
async def reject_with_reply(
    matcher: Matcher,
    scope: ChatScope,
    reply_message_id: int | None,
    text: str,
) -> None:
    """在群聊中拒绝回复时引用用户消息，私聊保持纯文本。"""
    if scope.chat_type == "group" and reply_message_id is not None:
        message = Message(
            MessageSegment.reply(reply_message_id),
            MessageSegment.text(text),
        )
    else:
        message = text
    await matcher.reject(message)
```

3. 修改 `present_candidates` 签名：

```python
async def present_candidates(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    candidates: list[SearchResult],
    scope: ChatScope,
    *,
    options: PresentOptions = PresentOptions(),
    page_index: int = 0,
    total_pages: int = 1,
    prompt_suffix: str = "",
    use_reject: bool = False,
    reply_message_id: int | None = None,
) -> None:
```

4. 修改 `present_candidates` 末尾的消息构造：

```python
    content = "\n".join(lines)

    if scope.chat_type == "group" and reply_message_id is not None:
        message = Message(
            MessageSegment.reply(reply_message_id),
            MessageSegment.text(content),
        )
    else:
        message = MessageSegment.text(content)

    if use_reject:
        await cmd_matcher.reject(message)
    else:
        await cmd_matcher.send(message)
```

5. 修改 `dispatch_search_results` 签名，增加 `scope`：

```python
async def dispatch_search_results(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    scope: ChatScope,
    results: list[SearchResult],
    *,
    options: PresentOptions = PresentOptions(),
    prompt_suffix: str = "",
) -> None:
```

内部调用 `present_candidates(..., scope, ..., reply_message_id=event.message_id)`。

6. 修改 `execute_search` 签名，增加 `scope`：

```python
async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    scope: ChatScope,
    keyword: str,
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
```

内部所有 `session_manager.deactivate_chat(...)` 改为 `session_manager.deactivate_chat(scope)`，调用 `dispatch_search_results` 时传入 `scope`。

7. 修改 `execute_combined_search` 签名，增加 `scope`：

```python
async def execute_combined_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    scope: ChatScope,
    keyword: str | None,
    speakers: list[str],
    tags: list[str],
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
```

同样更新内部调用。

8. 修改 `handle_got_selection` 签名，增加 `scope`：

```python
async def handle_got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message,
    scope: ChatScope,
    error_label: str = "搜索",
    *,
    options: PresentOptions = PresentOptions(),
) -> None:
```

内部修改：
- `session_manager.handler_context(scope, matcher)`
- `got_intercept_bypass(scope, matcher, text, HELP_TEXT)`（同步改造该函数接收 `scope`）
- 所有 `session_manager.get_selection(scope)`、`session_manager.remove_selection(scope)`、`session_manager.deactivate_chat(scope)`
- 翻页调用 `present_candidates(..., scope, ..., use_reject=True, reply_message_id=event.message_id)`
- “没有更多结果了” 使用 `await reject_with_reply(matcher, scope, event.message_id, "没有更多结果了")`
- 编号选择无效时使用 `await reject_with_reply(matcher, scope, event.message_id, result)`

9. 同步修改 `got_intercept_bypass` 签名：

```python
async def got_intercept_bypass(
    scope: ChatScope,
    matcher: Matcher,
    text: str,
    help_text: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。"""
    if text.startswith("/cancel ") or text == "/cancel":
        if not await session_manager.execute_cancel(scope):
            await matcher.finish("当前没有活跃的会话")
        return True

    if text.startswith("/help ") or text == "/help":
        await matcher.reject(help_text)
        return True

    return False
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_search_utils_scope.py -v
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add bot/plugins/_search_utils.py tests/unit/plugins/test_search_utils_scope.py
git commit -m "feat(plugins): add ChatScope and reply helper to search utils"
```

---

## Task 3: 改造 `meme_plain_text.py`

**Files:**
- Modify: `bot/plugins/meme_plain_text.py`

- [ ] **Step 1: 导入 `ChatScope` 并修改 `handle_plain_text`**

```python
from bot.session import ChatScope, session_manager
```

在 `handle_plain_text` 中：

```python
scope = ChatScope.from_event(event)
...
if not session_manager.activate_chat(scope, "search", matcher):
    await matcher.finish("已有命令在处理中，请先 /cancel")
    return

await execute_search(bot, event, matcher, scope, text, options=SEARCH_OPTIONS)
```

- [ ] **Step 2: 修改 `got_selection`**

```python
scope = ChatScope.from_event(event)
await handle_got_selection(
    bot, event, matcher, selection_msg, scope, "兜底搜索", options=SEARCH_OPTIONS
)
```

- [ ] **Step 3: 运行相关测试**

```bash
uv run pytest tests/unit/plugins/test_meme_plain_text.py -v
```

Expected: PASS（如不存在该测试文件则跳过）。

- [ ] **Step 4: 提交**

```bash
git add bot/plugins/meme_plain_text.py
git commit -m "refactor(plugins): adapt meme_plain_text to ChatScope"
```

---

## Task 4: 改造 `meme_query.py`

**Files:**
- Modify: `bot/plugins/meme_query.py`

- [ ] **Step 1: 导入 `ChatScope` 并修改 `handle_query`**

```python
from bot.session import ChatScope, session_manager
```

```python
scope = ChatScope.from_event(event)
...
if not session_manager.activate_chat(scope, "query", matcher):
    await matcher.finish("已有命令在处理中，请先 /cancel")
    return
...
await execute_combined_search(
    bot, event, matcher, scope, keyword, speakers, tags, options=options
)
```

- [ ] **Step 2: 修改 `got_selection`**

```python
scope = ChatScope.from_event(event)
options: PresentOptions = matcher.state.get("query_options", QUERY_FILTER_OPTIONS)
await handle_got_selection(
    bot, event, matcher, selection_msg, scope, "/query", options=options
)
```

- [ ] **Step 3: 运行相关测试并提交**

```bash
uv run pytest tests/unit/plugins/test_meme_query.py -v
```

```bash
git add bot/plugins/meme_query.py
git commit -m "refactor(plugins): adapt meme_query to ChatScope"
```

---

## Task 5: 改造 `meme_sim.py`

**Files:**
- Modify: `bot/plugins/meme_sim.py`

- [ ] **Step 1: 导入 `ChatScope` 并修改 `handle_sim` 与 `got_sim_selection`**

```python
from bot.session import ChatScope, session_manager
```

在 `handle_sim` 中构造 `scope` 并传入 `activate_chat` 和 `dispatch_search_results`：

```python
scope = ChatScope.from_event(event)
...
if not session_manager.activate_chat(scope, "sim", matcher):
    ...
...
await dispatch_search_results(bot, event, matcher, scope, results, options=SIM_OPTIONS)
```

在 `got_sim_selection` 中：

```python
scope = ChatScope.from_event(event)
await handle_got_selection(
    bot, event, matcher, selection_msg, scope, "/sim", options=SIM_OPTIONS
)
```

- [ ] **Step 2: 运行测试并提交**

```bash
uv run pytest tests/unit/plugins/test_meme_sim.py -v
```

```bash
git add bot/plugins/meme_sim.py
git commit -m "refactor(plugins): adapt meme_sim to ChatScope"
```

---

## Task 6: 改造 `meme_rand.py`

**Files:**
- Modify: `bot/plugins/meme_rand.py`

- [ ] **Step 1: 导入 `ChatScope` 并修改 `handle_rand`**

```python
from bot.session import ChatScope, session_manager
```

```python
scope = ChatScope.from_event(event)
...
if not session_manager.activate_chat(scope, "rand", matcher):
    ...
...
await dispatch_search_results(
    bot, event, matcher, scope, results, prompt_suffix="回复 0 换一批"
)
```

- [ ] **Step 2: 修改 `got_rand_selection`**

```python
scope = ChatScope.from_event(event)
```

- 使用 `session_manager.handler_context(scope, matcher)`。
- `got_intercept_bypass(scope, matcher, text, HELP_TEXT)`。
- 检查 `session_manager.get_selection(scope)`。
- 换一批调用 `present_candidates(..., scope, ..., use_reject=True, reply_message_id=event.message_id)`。
- 非法编号使用 `reject_with_reply(matcher, scope, event.message_id, result + "\n回复 0 换一批")`。

- [ ] **Step 3: 运行测试并提交**

```bash
uv run pytest tests/unit/plugins/test_meme_rand.py -v
```

```bash
git add bot/plugins/meme_rand.py
git commit -m "refactor(plugins): adapt meme_rand to ChatScope and reply refs"
```

---

## Task 7: 改造 `meme_add.py`

**Files:**
- Modify: `bot/plugins/meme_add.py`

- [ ] **Step 1: 导入 `ChatScope` 并修改 `handle_add`**

```python
from bot.session import ChatScope, session_manager
```

```python
scope = ChatScope.from_event(event)
...
if not session_manager.activate_chat(scope, "add", matcher):
    ...
...
task = asyncio.create_task(
    timeout_session(bot, event, scope, selection_id, "发送图片超时，请重新 /add")
)
session_manager.create_selection(scope, selection_id, task)
```

- [ ] **Step 2: 修改 got 等待阶段**

找到 `/add` 的 `got` 处理函数（通常带有 `@add_cmd.got("image")` 或类似装饰器），在该函数顶部加入：

```python
scope = ChatScope.from_event(event)
```

然后全文件搜索 `session_manager.` 和 `timeout_session(` 的所有调用点，将 `user_id` 参数统一替换为 `scope`：
- `session_manager.handler_context(scope, matcher)`
- `session_manager.deactivate_chat(scope)`
- `got_intercept_bypass(scope, matcher, text, HELP_TEXT)`
- `timeout_session(bot, event, scope, ...)`

确保 got 处理函数退出时也调用 `session_manager.deactivate_chat(scope)`。

- [ ] **Step 3: 运行测试并提交**

```bash
uv run pytest tests/unit/plugins/test_meme_add.py -v
```

```bash
git add bot/plugins/meme_add.py
git commit -m "refactor(plugins): adapt meme_add to ChatScope"
```

---

## Task 8: 改造 `meme_addtag.py`、`meme_delete.py`、`meme_edit.py`、`meme_setspeaker.py`

**Files:**
- Modify: `bot/plugins/meme_addtag.py`
- Modify: `bot/plugins/meme_delete.py`
- Modify: `bot/plugins/meme_edit.py`
- Modify: `bot/plugins/meme_setspeaker.py`

- [ ] **Step 1: 统一修改模式**

每个文件：

```python
from bot.session import ChatScope, session_manager
```

在 `handle_*` 中：

```python
scope = ChatScope.from_event(event)
```

替换：
- `session_manager.activate_chat(user_id, ...)` → `session_manager.activate_chat(scope, ...)`
- `session_manager.deactivate_chat(user_id)` → `session_manager.deactivate_chat(scope)`
- `timeout_session(bot, event, user_id, ...)` → `timeout_session(bot, event, scope, ...)`
- got 处理函数中 `handler_context(scope, matcher)`

- [ ] **Step 2: 运行相关测试**

```bash
uv run pytest tests/unit/plugins/test_meme_addtag.py tests/unit/plugins/test_meme_delete.py tests/unit/plugins/test_meme_edit.py tests/unit/plugins/test_meme_setspeaker.py -v
```

Expected: PASS。

- [ ] **Step 3: 提交**

```bash
git add bot/plugins/meme_addtag.py bot/plugins/meme_delete.py bot/plugins/meme_edit.py bot/plugins/meme_setspeaker.py
git commit -m "refactor(plugins): adapt admin confirmation plugins to ChatScope"
```

---

## Task 9: 改造 `meme_cancel.py`

**Files:**
- Modify: `bot/plugins/meme_cancel.py`

- [ ] **Step 1: 修改 `handle_cancel`**

```python
from bot.session import ChatScope, session_manager
```

```python
scope = ChatScope.from_event(event)
...
succeed_cancel = await session_manager.execute_cancel(scope)
```

- [ ] **Step 2: 运行测试并提交**

```bash
uv run pytest tests/unit/plugins/test_meme_cancel.py -v
```

```bash
git add bot/plugins/meme_cancel.py
git commit -m "refactor(plugins): adapt meme_cancel to ChatScope"
```

---

## Task 10: 更新文档

**Files:**
- Modify: `docs/api/bot/session.md`
- Modify: `docs/PRD.md`

- [ ] **Step 1: 更新 `docs/api/bot/session.md`**

在文件开头或合适位置新增 `ChatScope` 说明：

```markdown
### `bot/session.py`

```python
@dataclass(frozen=True, slots=True)
class ChatScope:
    user_id: int
    chat_type: Literal["private", "group"]
    chat_id: int

    @classmethod
    def from_event(cls, event: MessageEvent) -> ChatScope: ...
```

`ChatScope` 用于标识「一个用户在一个聊天窗口内」的作用域，可直接作为 `SessionManager` 内部字典的键。
```

更新 `SessionManager` 方法签名列表，将所有 `user_id: str` 替换为 `scope: ChatScope`。

更新 `timeout_session` 签名：

```python
async def timeout_session(
    bot: Bot,
    event: Event,
    scope: ChatScope,
    selection_id: str,
    message: str,
    *,
    on_cleanup: Callable[[], Any | Awaitable[Any]] | None = None,
    timeout: int | None = None,
) -> None
```

- [ ] **Step 2: 更新 `docs/PRD.md`**

全文搜索以下类似表述：

```text
同一授权用户同一时间只保留一个待处理会话
```

统一替换为：

```text
同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话
```

重点关注章节：3.1、3.2、3.4、3.7、3.10 等。若某些句子结构不同（如缺少“授权用户”或语序不同），按同样语义调整，确保所有地方都明确为“同一聊天窗口内”。

- [ ] **Step 3: 提交**

```bash
git add docs/api/bot/session.md docs/PRD.md
git commit -m "docs: update session docs for ChatScope and per-window session semantics"
```

---

## Task 11: 全量验证

**Files:**
- All modified files

- [ ] **Step 1: 运行单元测试**

```bash
uv run pytest tests/unit -v
```

Expected: PASS。

- [ ] **Step 2: 运行类型检查**

```bash
uv run pyright
```

Expected: 无新增类型错误。

- [ ] **Step 3: 静态检查（如项目配置）**

```bash
uv run ruff check bot tests
```

Expected: 无新增 lint 错误。

- [ ] **Step 4: 提交或标记完成**

如仍有零散文档或格式问题，单独提交；否则该任务结束。

---

## 自检清单

- [x] **Spec 覆盖**：每个 spec 章节都有对应任务实现。
  - `ChatScope` 设计 → Task 1
  - `SessionManager` 改造 → Task 1
  - 插件调用点改造 → Tasks 3-9
  - 群聊引用 → Tasks 2, 6
  - 错误提示引用 → Task 2
  - 测试更新 → 各 Task 中的测试步骤
  - 文档更新 → Task 10
- [x] **无占位符**：计划中没有 TBD/TODO/"稍后实现"/"类似 Task N"。
- [x] **类型一致**：所有任务中 `ChatScope` 字段、`SessionManager` 方法签名、`present_candidates` / `reject_with_reply` 参数名称保持一致。

---

## 执行交接

Plan complete and saved to `docs/superpowers/plans/2026-07-12-session-and-reference.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
