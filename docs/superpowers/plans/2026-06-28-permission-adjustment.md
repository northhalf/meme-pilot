# 权限调整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将命令分为两组——/add、/refresh、/ai 仅私聊可用；/search、/help、普通文本同时支持私聊和群聊@。

**Architecture:** 在 7 个插件文件中统一做两件事：(1) 将 `PrivateMessageEvent` 类型标注改为 `MessageEvent`；(2) 组 A 命令在授权校验后加 `event.message_type` 判断拦截群聊消息并回复"此命令仅限私聊使用"。测试文件同步更新 mock event 以设置 `message_type`。

**Tech Stack:** NoneBot2, pytest, unittest.mock

---

### 文件结构概览

| 文件 | 角色 | 改动类型 |
|------|------|----------|
| `bot/plugins/meme_refresh.py` | 组 A — 仅私聊 | 类型标注 + 行为逻辑 |
| `bot/plugins/meme_ai.py` | 组 A — 仅私聊 | 类型标注 + 行为逻辑 |
| `bot/plugins/meme_add.py` | 组 A — 仅私聊 | 行为逻辑（类型已为 `MessageEvent`） |
| `bot/plugins/meme_search.py` | 组 B — 放行群聊 | 类型标注 |
| `bot/plugins/meme_help.py` | 组 B — 放行群聊 | 类型标注 |
| `bot/plugins/meme_plain_text.py` | 组 B — 放行群聊 | 类型标注 |
| `bot/plugins/_search_utils.py` | 被组 B 调用的共享模块 | 类型标注 |

---

### Task 1: 更新 `_search_utils.py` — 放宽事件类型

**Files:**
- Modify: `bot/plugins/_search_utils.py:10-14` (import)
- No test changes needed（测试不依赖类型标注）

- [ ] **Step 1: 修改 import 和类型标注**

将 `bot/plugins/_search_utils.py` 中第 10 行的 import：

```python
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageSegment,
    PrivateMessageEvent,
)
```

改为：

```python
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    MessageSegment,
)
```

将第 57 行 `execute_search` 的签名：

```python
async def execute_search(
    bot: Bot,
    event: PrivateMessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
```

改为：

```python
async def execute_search(
    bot: Bot,
    event: MessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/plugins/_search_utils.py`
Expected: OK (no syntax errors)

- [ ] **Step 3: 运行相关测试**

Run: `uv run pytest tests/unit/plugins/test_meme_search.py tests/unit/plugins/test_meme_plain_text.py -v`
Expected: ALL PASS

---

### Task 2: 更新 `meme_refresh.py` — 组 A 仅私聊

**Files:**
- Modify: `bot/plugins/meme_refresh.py`
- Modify: `tests/unit/plugins/test_meme_refresh.py`

- [ ] **Step 1: 修改 import 和类型标注**

在 `bot/plugins/meme_refresh.py` 第 10 行：

```python
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
```

改为：

```python
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
```

修改第 22 行 `handle_refresh` 的 event 类型：

```python
async def handle_refresh(bot: Bot, event: PrivateMessageEvent) -> None:
```

改为：

```python
async def handle_refresh(bot: Bot, event: MessageEvent) -> None:
```

- [ ] **Step 2: 在 `handle_refresh` 中加群聊拦截**

在 `meme_refresh.py` 中 `handle_refresh` 函数的授权校验之后（第 37 行 `return` 之后），添加：

```python
    # 群聊拦截：/refresh 仅限私聊使用
    if event.message_type != "private":
        logger.info("用户 %s 在群聊中调用 /refresh，已拒绝", user_id)
        await refresh_cmd.finish("此命令仅限私聊使用")
        return
```

插入位置：在第 37 行 `return` 之后、第 39 行空行之前。

- [ ] **Step 3: 更新测试 — 在 `_make_event` 中添加 `message_type`**

在 `tests/unit/plugins/test_meme_refresh.py` 第 31-35 行的 `_make_event` 函数中，增加 `message_type`：

```python
def _make_event(user_id: str = "12345") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = "private"
    return event
```

- [ ] **Step 4: 添加群聊拦截测试**

在 `tests/unit/plugins/test_meme_refresh.py` 的 `TestHandleRefreshAuth` 类中，添加新测试方法：

```python
    @pytest.mark.asyncio
    @patch.object(meme_refresh, "is_authorized", return_value=True)
    @patch.object(meme_refresh, "get_index_manager")
    async def test_group_chat_rejected(
        self, mock_get_im: MagicMock, mock_auth: MagicMock
    ) -> None:
        """群聊中调用 /refresh 应回复仅限私聊提示。"""
        _reset_cmd()
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.message_type = "group"

        await handle_refresh(_make_bot(), event)

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "仅限私聊" in call_args
        mock_get_im.assert_not_called()
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/unit/plugins/test_meme_refresh.py -v`
Expected: ALL PASS (including the new group_chat_rejected test)

---

### Task 3: 更新 `meme_ai.py` — 组 A 仅私聊

**Files:**
- Modify: `bot/plugins/meme_ai.py`
- Modify: `tests/unit/plugins/test_meme_ai.py`

- [ ] **Step 1: 修改 import 和类型标注**

在 `bot/plugins/meme_ai.py` 第 11 行：

```python
from nonebot.adapters.onebot.v11 import Bot, MessageSegment, PrivateMessageEvent
```

改为：

```python
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
```

修改第 52 行 `handle_ai` 的 event 类型：

```python
async def handle_ai(bot: Bot, event: PrivateMessageEvent) -> None:
```

改为：

```python
async def handle_ai(bot: Bot, event: MessageEvent) -> None:
```

- [ ] **Step 2: 在 `handle_ai` 中加群聊拦截**

在 `meme_ai.py` 中 `handle_ai` 的授权校验之后（第 67 行 `return` 之后），添加：

```python
    # 群聊拦截：/ai 仅限私聊使用
    if event.message_type != "private":
        logger.info("用户 %s 在群聊中调用 /ai，已拒绝", user_id)
        await ai_cmd.finish("此命令仅限私聊使用")
        return
```

插入位置：在第 67 行 `return` 之后、第 69 行 `# 获取 IndexManager` 之前。

- [ ] **Step 3: 更新测试 — 在 `_make_event` 中添加 `message_type`**

在 `tests/unit/plugins/test_meme_ai.py` 第 28-33 行的 `_make_event` 函数中：

```python
def _make_event(user_id: str = "12345", text: str = "/ai 加班心累") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event
```

- [ ] **Step 4: 添加群聊拦截测试**

在 `tests/unit/plugins/test_meme_ai.py` 的 `TestHandleAiAuth` 类中，添加：

```python
    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_group_chat_rejected(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """群聊中调用 /ai 应回复仅限私聊提示。"""
        _reset_cmd()
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.get_plaintext.return_value = "/ai 加班心累"
        event.message_type = "group"

        await handle_ai(_make_bot(), event)

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "仅限私聊" in call_args
        mock_get_im.assert_not_called()
        mock_get_ai.assert_not_called()
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/unit/plugins/test_meme_ai.py -v`
Expected: ALL PASS

---

### Task 4: 更新 `meme_add.py` — 组 A 仅私聊

**Files:**
- Modify: `bot/plugins/meme_add.py`
- Modify: `tests/unit/plugins/test_meme_add.py`

- [ ] **Step 1: 在 `handle_add` 中加群聊拦截**

`meme_add.py` 的 `handle_add` 参数**已经**是 `MessageEvent`（无需改类型标注）。在 `handle_add` 的授权校验之后（第 70 行 `return` 之后），添加：

```python
    # 群聊拦截：/add 仅限私聊使用
    if event.message_type != "private":
        logger.info("用户 %s 在群聊中调用 /add，已拒绝", user_id)
        await matcher.finish("此命令仅限私聊使用")
        return
```

插入位置：在第 70 行 `return` 之后、第 72 行 `# 会话覆盖检查` 之前。

- [ ] **Step 2: 更新测试 — 在 `_make_event` 中添加 `message_type`**

在 `tests/unit/plugins/test_meme_add.py` 第 40-45 行的 `_make_event` 函数中：

```python
def _make_event(user_id: str = "12345", text: str = "/add") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event
```

- [ ] **Step 3: 添加群聊拦截测试**

在 `tests/unit/plugins/test_meme_add.py` 中找到 `TestHandleAddAuth` 类（或类似授权测试类），添加：

```python
    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    async def test_group_chat_rejected(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """群聊中调用 /add 应回复仅限私聊提示。"""
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.get_plaintext.return_value = "/add 测试"
        event.message_type = "group"

        await handle_add(_make_bot(), event, _make_matcher())

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "仅限私聊" in call_args
        mock_get_im.assert_not_called()
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/unit/plugins/test_meme_add.py -v`
Expected: ALL PASS

---

### Task 5: 更新 `meme_search.py` — 组 B 放行群聊

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 修改 import 和类型标注**

在 `bot/plugins/meme_search.py` 第 11-16 行的 import：

```python
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
```

改为：

```python
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
```

修改第 32 行 `handle_search` 的 event 类型：

```python
async def handle_search(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None:
```

改为：

```python
async def handle_search(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
```

修改第 68-70 行 `got_selection` 的 event 类型：

```python
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
```

改为：

```python
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
```

- [ ] **Step 2: 更新测试 — 在 `_make_event` 中添加 `message_type`**

在 `tests/unit/plugins/test_meme_search.py` 第 30-35 行的 `_make_event` 函数中：

```python
def _make_event(user_id: str = "12345", text: str = "/search 加班") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: ALL PASS

---

### Task 6: 更新 `meme_help.py` — 组 B 放行群聊

**Files:**
- Modify: `bot/plugins/meme_help.py`
- Modify: `tests/unit/plugins/test_meme_help.py`

- [ ] **Step 1: 修改 import 和类型标注**

在 `bot/plugins/meme_help.py` 第 9 行：

```python
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
```

改为：

```python
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
```

修改第 21 行 `handle_help` 的 event 类型：

```python
async def handle_help(bot: Bot, event: PrivateMessageEvent) -> None:
```

改为：

```python
async def handle_help(bot: Bot, event: MessageEvent) -> None:
```

- [ ] **Step 2: 更新测试 — 在 `_make_event` 中添加 `message_type`**

在 `tests/unit/plugins/test_meme_help.py` 第 27-31 行的 `_make_event` 函数中：

```python
def _make_event(user_id: str = "12345") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = "private"
    return event
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/unit/plugins/test_meme_help.py -v`
Expected: ALL PASS

---

### Task 7: 更新 `meme_plain_text.py` — 组 B 放行群聊

**Files:**
- Modify: `bot/plugins/meme_plain_text.py`
- Modify: `tests/unit/plugins/test_meme_plain_text.py`

- [ ] **Step 1: 修改 import 和类型标注**

在 `bot/plugins/meme_plain_text.py` 第 11-16 行的 import：

```python
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
```

改为：

```python
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
    MessageSegment,
)
```

修改第 39 行 `handle_plain_text` 的 event 类型：

```python
async def handle_plain_text(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
) -> None:
```

改为：

```python
async def handle_plain_text(
    bot: Bot, event: MessageEvent, matcher: Matcher
) -> None:
```

修改第 69-71 行 `got_selection` 的 event 类型（注意代码行号可能在同一个函数内）：

```python
@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
```

改为：

```python
@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
```

- [ ] **Step 2: 更新测试 — 在 `_make_event` 中添加 `message_type`**

在 `tests/unit/plugins/test_meme_plain_text.py` 第 30-35 行的 `_make_event` 函数中：

```python
def _make_event(user_id: str = "12345", text: str = "") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event
```

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/unit/plugins/test_meme_plain_text.py -v`
Expected: ALL PASS

---

### Task 8: 文档同步

**Files:**
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 PRD.md**

在 `docs/PRD.md` 中：

1. **第 1.3 节目标用户**：将"不支持群聊"改为群聊权限说明（区分组 A/组 B）
2. **第 3 节功能需求各小节**中的群聊描述：
   - 3.1 `/search`：增加"群聊中 @bot 也可触发"的说明
   - 3.4 `/help`：增加"群聊中 @bot 也可触发"的说明
   - 3.5 权限约束段落：将"群聊不在 v1.0 范围内，无论发送者是否授权，群聊消息都静默忽略"改为新的分组规则
3. **第 5 节边界情况**：更新群聊相关场景的行为描述

- [ ] **Step 2: 更新 CONTEXT.md**

1. **「群聊消息」术语**：从"v1.0 不支持的会话形态"改为分命令说明
2. **「/help」「/search」术语**：增加群聊@支持说明
3. **「/add」「/ai」「/refresh」术语**：标注仅私聊

- [ ] **Step 3: 更新 README.md**

1. 在功能概览和帮助文本中增加群聊@使用说明
2. 在 **功能** → `/search` 和 `/help` 描述中补充"群聊中 @bot 也可使用"

- [ ] **Step 4: 语法检查**

Run: `uv run python -m compileall bot tests`
Expected: OK (no syntax errors)

- [ ] **Step 5: 全量测试**

Run: `uv run pytest -v`
Expected: ALL PASS

---

### 自审与验证

- [ ] **Step 6: 自审 — spec 覆盖**

对照设计文档逐项验证：
- 组 A 仅私聊：Task 2 (refresh) + Task 3 (ai) + Task 4 (add) ✅
- 组 B 放行群聊：Task 5 (search) + Task 6 (help) + Task 7 (plain_text) ✅
- 依赖模块更新：Task 1 (_search_utils) ✅
- 文档同步：Task 8 ✅

所有 spec 需求均已覆盖，无遗漏。
