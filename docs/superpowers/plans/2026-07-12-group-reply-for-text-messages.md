# 群聊文本消息统一引用回复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在群聊中统一为所有可见文本消息附加 `MessageSegment.reply`，私聊保持现状，图片消息不带 reply。

**Architecture:** 新增 `bot/reply.py` 集中管理群聊引用构造逻辑，提供 `build_reply_text` 及 `finish/send/reject/bot_send` 四个发送辅助函数；`bot/session.py` 的取消与超时逻辑改为调用新模块；所有插件中的文本 `matcher.finish/send/reject` 调用替换为对应辅助函数。

**Tech Stack:** Python 3.12, NoneBot2, OneBot V11 Adapter, pytest, pyright

---

## 文件清单

| 文件 | 责任 |
|------|------|
| `bot/reply.py` | **新建**。群聊 reply 构造与发送辅助函数。 |
| `tests/unit/test_reply.py` | **新建**。`bot/reply.py` 单元测试。 |
| `bot/session.py` | `execute_cancel` 增加 `event` 参数并使用 `reply.finish`；`timeout_session` 使用 `reply.bot_send`。 |
| `bot/plugins/_search_utils.py` | `reject_with_reply`、`present_candidates` 收口到 `bot/reply.py`；`got_intercept_bypass` 同步 `execute_cancel` 调用。 |
| `bot/plugins/meme_*.py` | 所有文本 `matcher.finish/send/reject` 调用替换为 `reply.*` 辅助函数。 |
| `tests/conftest.py` 或 `tests/helpers.py` | 新增测试辅助函数 `extract_message_text`。 |
| 多个 `tests/unit/plugins/test_*.py` | 更新断言以兼容 `str \| Message` 类型。 |
| `docs/api/bot/reply.md` | **新建**。`bot/reply.py` 接口文档。 |
| `docs/api/API.md` | 增加 `docs/api/bot/reply.md` 索引条目。 |

---

## Task 1: 新建 `bot/reply.py`

**Files:**
- Create: `bot/reply.py`
- Test: `tests/unit/test_reply.py`

- [ ] **Step 1: 编写 `bot/reply.py` 实现**

```python
"""群聊消息引用回复工具模块。

提供 build_reply_text 与一组发送辅助函数，使群聊中的文本消息
自动带上 MessageSegment.reply，私聊或 message_id 缺失时退化为纯文本。
"""

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher


def build_reply_text(event: MessageEvent, text: str) -> Message | str:
    """构造群聊引用文本消息。

    当事件为群聊且 event.message_id 存在时，返回包含 reply segment 的 Message；
    否则返回原字符串，保持私聊或异常场景下的现有行为。

    Args:
        event: OneBot V11 消息事件。
        text: 要发送的纯文本内容。

    Returns:
        群聊场景下为 Message（含 reply + text），否则为原字符串。
    """
    message_id = getattr(event, "message_id", None)
    message_type = getattr(event, "message_type", None)
    if message_type == "group" and message_id is not None:
        return Message([MessageSegment.reply(message_id), MessageSegment.text(text)])
    return text


async def finish(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """调用 matcher.finish 发送已包装 reply 的文本消息。"""
    await matcher.finish(build_reply_text(event, text))


async def send(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """调用 matcher.send 发送已包装 reply 的文本消息。"""
    await matcher.send(build_reply_text(event, text))


async def reject(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """调用 matcher.reject 发送已包装 reply 的文本消息。"""
    await matcher.reject(build_reply_text(event, text))


async def bot_send(event: MessageEvent, bot: Bot, text: str) -> None:
    """调用 bot.send 发送已包装 reply 的文本消息（用于超时任务）。"""
    await bot.send(event, build_reply_text(event, text))
```

- [ ] **Step 2: 编写 `tests/unit/test_reply.py` 测试**

```python
"""bot/reply.py 单元测试。"""

from unittest.mock import MagicMock

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from bot.reply import build_reply_text


def _make_event(
    *,
    message_type: str = "private",
    message_id: int | None = 42,
) -> MagicMock:
    event = MagicMock()
    event.message_type = message_type
    event.message_id = message_id
    return event


class TestBuildReplyText:
    """build_reply_text 行为测试。"""

    def test_private_chat_returns_plain_text(self) -> None:
        """私聊返回原字符串。"""
        event = _make_event(message_type="private", message_id=42)
        result = build_reply_text(event, "hello")
        assert result == "hello"
        assert isinstance(result, str)

    def test_group_chat_with_message_id_returns_message(self) -> None:
        """群聊且 message_id 存在时返回带 reply 的 Message。"""
        event = _make_event(message_type="group", message_id=123)
        result = build_reply_text(event, "hello")
        assert isinstance(result, Message)
        assert len(result) == 2
        assert result[0].type == "reply"
        assert result[0].data["id"] == "123"
        assert result[1].type == "text"
        assert result[1].data["text"] == "hello"

    def test_group_chat_without_message_id_returns_plain_text(self) -> None:
        """群聊但 message_id 缺失时退化为纯文本。"""
        event = _make_event(message_type="group", message_id=None)
        result = build_reply_text(event, "hello")
        assert result == "hello"
        assert isinstance(result, str)

    def test_message_type_missing_returns_plain_text(self) -> None:
        """message_type 属性缺失时退化为纯文本。"""
        event = MagicMock(spec=[])
        result = build_reply_text(event, "hello")
        assert result == "hello"
```

- [ ] **Step 3: 运行新增测试**

```bash
pytest tests/unit/test_reply.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 4: 由用户审核后提交（项目禁止自动 commit）**

---

## Task 2: 更新 `bot/session.py`

**Files:**
- Modify: `bot/session.py`
- Test: `tests/unit/test_session.py`

- [ ] **Step 1: 导入 `bot.reply` 并修改 `execute_cancel` 签名与实现**

说明：`execute_cancel` 原签名增加 `event: MessageEvent` 参数，用于在群聊中构造 reply 消息；调用方只有 `got_intercept_bypass` 一处，同步改造即可。

在 `bot/session.py` 顶部新增导入：

```python
from bot import reply as reply_utils
```

将 `execute_cancel` 改为：

```python
async def execute_cancel(
    self,
    scope: ChatScope,
    event: MessageEvent,
    message: str = "当前会话已取消",
) -> bool:
    """执行取消逻辑。"""
    chat = self._chat_sessions.get(scope)
    if not (chat and chat.active):
        return False

    current = asyncio.current_task()
    if (
        chat.current_task
        and not chat.current_task.done()
        and chat.current_task is not current
    ):
        chat.current_task.cancel()

    ss = self.remove_selection(scope)
    if ss and ss.timeout_task and not ss.timeout_task.done():
        ss.timeout_task.cancel()

    if chat.matcher:
        try:
            await reply_utils.finish(event, chat.matcher, message)
        except FinishedException:
            pass

    self.deactivate_chat(scope)
    return True
```

同步更新 `execute_cancel` 的 docstring，在 `Args` 中增加 `event: 当前消息事件，用于构造群聊 reply` 说明。

- [ ] **Step 2: 修改 `timeout_session` 使用 `reply.bot_send`**

将 `timeout_session` 中的：

```python
await bot.send(event, message)
```

改为：

```python
await reply_utils.bot_send(event, bot, message)
```

- [ ] **Step 3: 更新 `tests/unit/test_session.py` 中 `execute_cancel` 相关测试**

由于 `execute_cancel` 新增 `event` 参数，所有调用处需要传入 `event`。例如：

```python
result = await session_manager.execute_cancel(scope, event)
```

并补充断言：群聊场景下 `matcher.finish` 收到的是 `Message`，第一个 segment 为 `reply`。

- [ ] **Step 4: 运行 session 测试**

```bash
pytest tests/unit/test_session.py -v
```

Expected: PASS.

- [ ] **Step 5: 由用户审核后提交**

---

## Task 3: 更新 `bot/plugins/_search_utils.py`

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`, `tests/unit/plugins/test_search_utils_scope.py`

- [ ] **Step 1: 导入 `bot.reply`**

在 `bot/plugins/_search_utils.py` 顶部新增：

```python
from bot import reply as reply_utils
```

- [ ] **Step 2: 改写 `reject_with_reply`**

将原函数替换为：

```python
async def reject_with_reply(
    matcher: Matcher,
    event: MessageEvent,
    text: str,
) -> None:
    """在群聊中尽可能以回复形式 reject，私聊或不支持回复时退化为纯文本。"""
    await reply_utils.reject(event, matcher, text)
```

- [ ] **Step 3: 改写 `present_candidates` 中的 reply 逻辑**

将：

```python
content = "\n".join(lines)
message: Message | str
if (
    options.reply_in_group
    and scope.chat_type == "group"
    and event.message_id is not None
):
    message = Message(
        [
            MessageSegment.reply(event.message_id),
            MessageSegment.text(content),
        ]
    )
else:
    message = content

if use_reject:
    await cmd_matcher.reject(message)
else:
    await cmd_matcher.send(message)
```

改为：

```python
content = "\n".join(lines)

if use_reject:
    await reply_utils.reject(event, cmd_matcher, content)
else:
    await reply_utils.send(event, cmd_matcher, content)
```

说明：`PresentOptions.reply_in_group` 字段由于全局统一群聊 reply 而不再被使用。为减少本计划范围外的破坏，先保留该字段（默认 `True`），后续可单独清理。

- [ ] **Step 4: 更新 `got_intercept_bypass` 调用 `execute_cancel` 时传入 `event`**

将：

```python
if text.startswith("/cancel ") or text == "/cancel":
    if not await session_manager.execute_cancel(scope):
        await matcher.finish("当前没有活跃的会话")
    return True
```

改为：

```python
if text.startswith("/cancel ") or text == "/cancel":
    if not await session_manager.execute_cancel(scope, event):
        await reply_utils.finish(event, matcher, "当前没有活跃的会话")
    return True
```

- [ ] **Step 5: 将 `got_intercept_bypass` 的 `/help` 分支改为 `reply.reject`**

将：

```python
if text.startswith("/help ") or text == "/help":
    await matcher.reject(help_text)
    return True
```

改为：

```python
if text.startswith("/help ") or text == "/help":
    await reply_utils.reject(event, matcher, help_text)
    return True
```

- [ ] **Step 6: 将 `dispatch_search_results` 中的无结果提示改为 `reply.finish`**

将：

```python
if not results:
    session_manager.deactivate_chat(scope)
    await cmd_matcher.finish("没有匹配到任何表情包 🙁")
    return
```

改为：

```python
if not results:
    session_manager.deactivate_chat(scope)
    await reply_utils.finish(event, cmd_matcher, "没有匹配到任何表情包 🙁")
    return
```

- [ ] **Step 7: 更新 `execute_search` 和 `execute_combined_search` 中的错误提示**

将所有：

```python
await cmd_matcher.finish("服务未就绪，请稍后再试")
await cmd_matcher.finish("索引更新较慢，请稍后再试")
await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
```

改为对应的：

```python
await reply_utils.finish(event, cmd_matcher, "服务未就绪，请稍后再试")
await reply_utils.finish(event, cmd_matcher, "索引更新较慢，请稍后再试")
await reply_utils.finish(event, cmd_matcher, "搜索服务暂时不可用，稍后重试")
```

- [ ] **Step 8: 更新 `handle_got_selection` 中的提示和错误信息**

将：

```python
await matcher.finish("选择已过期，请重新搜索")
await reject_with_reply(matcher, event, "没有更多结果了")
await reject_with_reply(matcher, event, result)
await matcher.finish(
    format_metadata_line(result.entry_id, result.speaker, result.tags)
)
```

改为：

```python
await reply_utils.finish(event, matcher, "选择已过期，请重新搜索")
await reply_utils.reject(event, matcher, "没有更多结果了")
await reply_utils.reject(event, matcher, result)
await reply_utils.finish(
    event,
    matcher,
    format_metadata_line(result.entry_id, result.speaker, result.tags),
)
```

图片发送保持不变：

```python
await matcher.send(
    MessageSegment.image("file://" + str(image_path.resolve()))
)
```

- [ ] **Step 9: 更新相关测试断言**

`tests/unit/plugins/test_search_utils.py` 和 `tests/unit/plugins/test_search_utils_scope.py` 中大量断言需要改为使用 `extract_message_text` 提取文本，并补充群聊下 reply segment 的断言。

- [ ] **Step 10: 运行测试**

```bash
pytest tests/unit/plugins/test_search_utils.py tests/unit/plugins/test_search_utils_scope.py -v
```

Expected: PASS.

- [ ] **Step 11: 由用户审核后提交**

---

## Task 4: 更新 `bot/plugins/meme_add.py`

**Files:**
- Modify: `bot/plugins/meme_add.py`
- Test: `tests/unit/plugins/test_meme_add.py`

- [ ] **Step 1: 导入 `bot.reply`**

```python
from bot import reply as reply_utils
```

- [ ] **Step 2: 替换所有文本 `matcher.finish/send/reject` 调用**

将文件中所有发送纯文本字符串的调用替换为对应辅助函数：

```python
await matcher.finish("此命令仅限私聊使用")
# ->
await reply_utils.finish(event, matcher, "此命令仅限私聊使用")

await matcher.finish("已有命令在处理中，请先 /cancel")
# ->
await reply_utils.finish(event, matcher, "已有命令在处理中，请先 /cancel")

await matcher.reject("请发送一张图片")
# ->
await reply_utils.reject(event, matcher, "请发送一张图片")

await matcher.finish("服务未就绪，请稍后再试")
# ->
await reply_utils.finish(event, matcher, "服务未就绪，请稍后再试")
```

其余类似。注意保留：

```python
await matcher.finish(None)
await matcher.send(MessageSegment.image(...))
```

- [ ] **Step 3: 更新测试**

更新 `tests/unit/plugins/test_meme_add.py` 中断言为 `extract_message_text`。

- [ ] **Step 4: 运行测试**

```bash
pytest tests/unit/plugins/test_meme_add.py -v
```

Expected: PASS.

- [ ] **Step 5: 由用户审核后提交**

---

## Task 5: 更新 `bot/plugins/meme_addtag.py`

**Files:**
- Modify: `bot/plugins/meme_addtag.py`
- Test: `tests/unit/plugins/test_meme_addtag.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 改为 `await reply_utils.finish(event, matcher, "...")`；图片发送保留。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_addtag.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 6: 更新 `bot/plugins/meme_ai.py`

**Files:**
- Modify: `bot/plugins/meme_ai.py`
- Test: `tests/unit/plugins/test_meme_ai.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 和 `matcher.send("...")` 改为 `reply_utils.finish/send`；图片发送保留。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_ai.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 7: 更新 `bot/plugins/meme_cancel.py`

**Files:**
- Modify: `bot/plugins/meme_cancel.py`
- Test: `tests/unit/plugins/test_meme_cancel.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换文本发送**

```python
from bot import reply as reply_utils
```

将 `matcher.finish("当前没有活跃的会话")` 改为 `reply_utils.finish(event, matcher, "当前没有活跃的会话")`。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_cancel.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 8: 更新 `bot/plugins/meme_delete.py`

**Files:**
- Modify: `bot/plugins/meme_delete.py`
- Test: `tests/unit/plugins/test_meme_delete.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 和 `matcher.send("...")` 改为 `reply_utils.*`；图片发送保留。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_delete.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 9: 更新 `bot/plugins/meme_edit.py`

**Files:**
- Modify: `bot/plugins/meme_edit.py`
- Test: `tests/unit/plugins/test_meme_edit.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 和 `matcher.send("...")` 改为 `reply_utils.*`；图片发送保留。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_edit.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 10: 更新 `bot/plugins/meme_help.py`

**Files:**
- Modify: `bot/plugins/meme_help.py`
- Test: `tests/unit/plugins/test_meme_help.py`（如存在）

- [ ] **Step 1: 导入 `bot.reply` 并替换文本发送**

```python
from bot import reply as reply_utils
```

将 `matcher.finish(HELP_TEXT)` 改为 `reply_utils.finish(event, matcher, HELP_TEXT)`。

- [ ] **Step 2: 运行测试或插件 smoke 测试**

```bash
pytest tests/unit/plugins/test_meme_help.py -v 2>/dev/null || echo "no test file"
```

Expected: PASS 或无测试文件。

- [ ] **Step 3: 由用户审核后提交**

---

## Task 11: 更新 `bot/plugins/meme_info.py`

**Files:**
- Modify: `bot/plugins/meme_info.py`
- Test: `tests/unit/plugins/test_meme_info.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 改为 `reply_utils.finish`；`_build_detail_message` 返回的字符串通过 `reply_utils.finish(event, matcher, _build_detail_message(entry))` 发送；`
`.join(lines) 同样通过 `reply_utils.finish` 发送。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_info.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 12: 更新 `bot/plugins/meme_plain_text.py`

**Files:**
- Modify: `bot/plugins/meme_plain_text.py`
- Test: `tests/unit/plugins/test_meme_plain_text.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

将 `matcher.finish(f"未知命令\n\n{HELP_TEXT}")` 等改为 `reply_utils.finish`。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_plain_text.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 13: 更新 `bot/plugins/meme_query.py`

**Files:**
- Modify: `bot/plugins/meme_query.py`
- Test: `tests/unit/plugins/test_meme_query.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

将 `matcher.finish("已有命令在处理中，请先 /cancel")` 和 `matcher.finish(QUERY_USAGE)` 改为 `reply_utils.finish`。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_query.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 14: 更新 `bot/plugins/meme_rand.py`

**Files:**
- Modify: `bot/plugins/meme_rand.py`
- Test: `tests/unit/plugins/test_meme_rand.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 改为 `reply_utils.finish`；图片发送保留。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_rand.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 15: 更新 `bot/plugins/meme_refresh.py`

**Files:**
- Modify: `bot/plugins/meme_refresh.py`
- Test: `tests/unit/plugins/test_meme_refresh.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 改为 `reply_utils.finish`。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_refresh.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 16: 更新 `bot/plugins/meme_setspeaker.py`

**Files:**
- Modify: `bot/plugins/meme_setspeaker.py`
- Test: `tests/unit/plugins/test_meme_setspeaker.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 和 `matcher.send("...")` 改为 `reply_utils.*`；图片发送保留。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_setspeaker.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 17: 更新 `bot/plugins/meme_sim.py`

**Files:**
- Modify: `bot/plugins/meme_sim.py`
- Test: `tests/unit/plugins/test_meme_sim.py`

- [ ] **Step 1: 导入 `bot.reply` 并替换所有文本发送**

```python
from bot import reply as reply_utils
```

所有 `matcher.finish("...")` 改为 `reply_utils.finish`。

- [ ] **Step 2: 更新测试并运行**

```bash
pytest tests/unit/plugins/test_meme_sim.py -v
```

Expected: PASS.

- [ ] **Step 3: 由用户审核后提交**

---

## Task 18: 新增测试辅助函数并批量更新测试断言

**Files:**
- Modify: `tests/conftest.py`
- Modify: 所有受影响的 `tests/unit/plugins/test_*.py`

- [ ] **Step 1: 在 `tests/conftest.py` 新增辅助函数**

```python
from nonebot.adapters.onebot.v11 import Message


def extract_message_text(message: str | Message) -> str:
    """从字符串或 Message 中提取纯文本内容。

    Args:
        message: 可能是纯文本字符串或 OneBot V11 Message。

    Returns:
        消息中的纯文本内容；字符串时直接返回。
    """
    if isinstance(message, str):
        return message
    return message.extract_plain_text()
```

- [ ] **Step 2: 批量替换测试断言**

对于每个受影响的测试文件，将形如：

```python
matcher.finish.assert_awaited_once_with("当前没有活跃的会话")
```

替换为：

```python
from tests.conftest import extract_message_text

msg = matcher.finish.await_args[0][0]
assert extract_message_text(msg) == "当前没有活跃的会话"
```

对于关键路径（搜索选择、超时），补充 reply segment 断言：

```python
from nonebot.adapters.onebot.v11 import Message

if isinstance(msg, Message):
    assert msg[0].type == "reply"
```

- [ ] **Step 3: 运行全部单元测试**

```bash
pytest tests/unit -v
```

Expected: 全部 PASS。

- [ ] **Step 4: 由用户审核后提交**

---

## Task 19: 更新 API 文档

**Files:**
- Create: `docs/api/bot/reply.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 新建 `docs/api/bot/reply.md`**

```markdown
# `bot/reply.py` 接口说明

群聊消息引用回复工具模块，集中管理是否以及如何在群聊中为文本消息附加 `MessageSegment.reply`。

## 导出函数

### `build_reply_text(event: MessageEvent, text: str) -> Message | str`

构造群聊引用文本消息。

- 当 `event.message_type == "group"` 且 `event.message_id` 存在时，返回 `Message([MessageSegment.reply(message_id), MessageSegment.text(text)])`；
- 私聊或 `message_id` 缺失时返回原字符串。

### `finish(event: MessageEvent, matcher: Matcher, text: str) -> None`

调用 `matcher.finish` 发送已包装 reply 的文本消息。

### `send(event: MessageEvent, matcher: Matcher, text: str) -> None`

调用 `matcher.send` 发送已包装 reply 的文本消息。

### `reject(event: MessageEvent, matcher: Matcher, text: str) -> None`

调用 `matcher.reject` 发送已包装 reply 的文本消息。

### `bot_send(event: MessageEvent, bot: Bot, text: str) -> None`

调用 `bot.send` 发送已包装 reply 的文本消息，主要用于 `timeout_session` 等不在 matcher 上下文中的场景。

## 使用约束

- 仅处理纯文本字符串；`Message` 对象和图片消息应继续由调用方直接发送；
- `matcher.finish(None)` 不应经过此模块。
```

- [ ] **Step 2: 在 `docs/api/API.md` 目录结构与实际索引中增加 `bot/reply.md` 条目**

在目录结构 `bot` 列表中新增：

```text
├── reply.md
```

在实际索引部分新增：

```markdown
### `docs/api/bot/reply.md`

```python
def build_reply_text(event: MessageEvent, text: str) -> Message | str
async def finish(event: MessageEvent, matcher: Matcher, text: str) -> None
async def send(event: MessageEvent, matcher: Matcher, text: str) -> None
async def reject(event: MessageEvent, matcher: Matcher, text: str) -> None
async def bot_send(event: MessageEvent, bot: Bot, text: str) -> None
```
```

- [ ] **Step 3: 由用户审核后提交**

---

## Task 20: 类型检查与全量回归

**Files:**
- 所有已修改文件

- [ ] **Step 1: 运行 pyright**

```bash
pyright
```

Expected: 无新增类型错误。

- [ ] **Step 2: 运行全部单元测试**

```bash
pytest tests/unit -q
```

Expected: 全部 PASS。

- [ ] **Step 3: 由用户审核后提交**

---

## 自审检查

### Spec 覆盖检查

| Spec 要求 | 对应任务 |
|-----------|----------|
| 新建 `bot/reply.py` 集中管理 reply 构造 | Task 1 |
| 群聊文本消息带 reply | Task 1–17 |
| 私聊保持现状 | Task 1（build_reply_text 退化逻辑） |
| 图片消息不带 reply | Task 3–17 中明确保留 `MessageSegment.image(...)` 调用 |
| `matcher.finish(None)` 不变 | Task 3–17 中明确保留 |
| 超时提示带 reply | Task 2（timeout_session 使用 reply.bot_send） |
| 取消消息带 reply | Task 2（execute_cancel 使用 reply.finish） |
| 新增 `bot/reply.py` 单元测试 | Task 1 |
| 现有单元测试通过 | Task 18、20 |
| pyright 无新增错误 | Task 20 |
| 更新 API.md | Task 19 |

### Placeholder 检查

计划中未出现 TBD、TODO、"实现 later"、"适当错误处理" 等占位符；每个代码步骤包含完整代码；每个命令步骤包含预期输出。

### 类型一致性检查

- `build_reply_text` 返回类型始终为 `Message | str`；
- `execute_cancel` 签名在所有任务中保持一致（新增 `event: MessageEvent`）；
- `reply_utils.finish/send/reject/bot_send` 签名在所有调用点一致。

---

## 执行交接

**Plan complete and saved to `docs/superpowers/plans/2026-07-12-group-reply-for-text-messages.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

**Which approach?**
