# bot/reply.py — 群聊消息引用回复工具

> 提供 `build_reply_text` 与一组发送辅助函数，使群聊中的纯文本消息自动带上 `MessageSegment.reply` 引用用户原消息；私聊或 `message_id` 缺失时退化为纯文本，保持原有行为。

## 导出函数

### `build_reply_text(event: MessageEvent, text: str) -> Message | str`

构造群聊引用文本消息。

- 当 `event.message_type == "group"` 且 `event.message_id` 存在时，返回：
  ```python
  Message([MessageSegment.reply(message_id), MessageSegment.text(text)])
  ```
- 其他情况（私聊、群聊但缺失 `message_id`）直接返回原 `text` 字符串。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `event` | `MessageEvent` | OneBot V11 消息事件 |
| `text` | `str` | 要发送的纯文本内容 |

**返回：** `Message | str`

---

### `finish(event: MessageEvent, matcher: Matcher, text: str) -> None`

调用 `matcher.finish` 发送已包装 reply 的文本消息。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `event` | `MessageEvent` | OneBot V11 消息事件 |
| `matcher` | `Matcher` | 当前 NoneBot2 Matcher 实例 |
| `text` | `str` | 要发送的纯文本内容 |

---

### `send(event: MessageEvent, matcher: Matcher, text: str) -> None`

调用 `matcher.send` 发送已包装 reply 的文本消息。

**参数：** 同 `finish`。

---

### `reject(event: MessageEvent, matcher: Matcher, text: str) -> None`

调用 `matcher.reject` 发送已包装 reply 的文本消息，用于 `got` handler 继续等待用户输入。

**参数：** 同 `finish`。

---

### `bot_send(event: MessageEvent, bot: Bot, text: str) -> None`

调用 `bot.send(event, ...)` 发送已包装 reply 的文本消息。主要用于 `timeout_session` 等没有直接 `matcher` 可用的超时任务场景。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `event` | `MessageEvent` | OneBot V11 消息事件 |
| `bot` | `Bot` | 当前 OneBot V11 Bot 实例 |
| `text` | `str` | 要发送的纯文本内容 |

## 使用约定

- **仅用于纯文本**：图片、已构造好的 `Message` 对象、`matcher.finish(None)` 等不经过本模块，保持原行为。
- **群聊自动生效**：插件层无需判断聊天类型，直接调用 `reply_utils.finish/send/reject`，本模块会根据 `event` 自动处理。
- **超时任务**：`bot/session.py` 的 `timeout_session` 与 `execute_cancel` 使用 `reply_utils.bot_send` / `reply_utils.finish`，确保群聊超时/取消提示也带 reply。

## 使用示例

```python
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher

from bot import reply as reply_utils


async def handle_example(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
) -> None:
    # 群聊中会自动附加 MessageSegment.reply；私聊保持纯文本
    await reply_utils.finish(event, matcher, "处理完成")
```
