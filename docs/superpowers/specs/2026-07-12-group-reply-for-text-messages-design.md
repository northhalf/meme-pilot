# 群聊文本消息统一引用回复设计文档

## 1. 背景与目标

### 1.1 背景

meme-pilot 是一个基于 NoneBot2 + OneBot V11 的 QQ 表情包机器人。当前代码中：

- `bot/plugins/_search_utils.py` 里的 `reject_with_reply` 和 `present_candidates` 在群聊中会显式使用 `MessageSegment.reply(event.message_id)` 引用原消息；
- 其余大量 `matcher.finish("...")`、`matcher.send("...")`、`matcher.reject("...")` 文本消息，以及 `bot/session.py` 超时后的 `bot.send(event, message)`，在群聊中都没有引用原消息；
- 图片消息通过 `matcher.send(MessageSegment.image(...))` 发送，本身不带 reply。

### 1.2 目标

在群聊中，除了图片之外的所有可见文本消息都带上 `reply` 字段（引用用户原消息），私聊保持现状。

具体范围：

- 群聊中所有可见文本消息：错误提示、用法帮助、搜索列表、确认/取消提示、超时提示等；
- 私聊保持现状，不带 reply；
- `matcher.finish(None)` 不发送可见消息，无需处理；
- 图片消息继续不带 reply。

## 2. 设计方案

采用**集中式辅助函数**方案。

### 2.1 新增模块 `bot/reply.py`

新建 `bot/reply.py`，作为所有群聊引用回复逻辑的唯一定义点。该模块位于 `bot/` 根目录，可被 `bot/session.py` 和 `bot/plugins/*` 共同导入，避免循环依赖。

核心函数：

```python
# bot/reply.py（示意）

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher


def build_reply_text(event: MessageEvent, text: str) -> Message | str:
    """构造群聊引用文本消息；私聊或 message_id 缺失时退化为纯文本。"""
    message_id = getattr(event, "message_id", None)
    message_type = getattr(event, "message_type", None)
    if message_type == "group" and message_id is not None:
        return Message([MessageSegment.reply(message_id), MessageSegment.text(text)])
    return text


async def finish(event: MessageEvent, matcher: Matcher, text: str) -> None:
    await matcher.finish(build_reply_text(event, text))


async def send(event: MessageEvent, matcher: Matcher, text: str) -> None:
    await matcher.send(build_reply_text(event, text))


async def reject(event: MessageEvent, matcher: Matcher, text: str) -> None:
    await matcher.reject(build_reply_text(event, text))


async def bot_send(event: MessageEvent, bot: Bot, text: str) -> None:
    await bot.send(event, build_reply_text(event, text))
```

设计要点：

- `build_reply_text` 同时被内部发送函数和外部调用方使用；
- 所有发送函数只接收纯文本字符串，不处理 `Message` 或图片消息；
- 私聊或 `message_id` 缺失时退化为纯文本，保持现有行为。

### 2.2 插件层统一替换

将所有形如 `matcher.finish("...")` / `matcher.send("...")` / `matcher.reject("...")` 的**文本消息**调用替换为 `bot/reply.py` 的对应函数。

需要改造的插件文件：

- `bot/plugins/meme_add.py`
- `bot/plugins/meme_addtag.py`
- `bot/plugins/meme_ai.py`
- `bot/plugins/meme_cancel.py`
- `bot/plugins/meme_delete.py`
- `bot/plugins/meme_edit.py`
- `bot/plugins/meme_help.py`
- `bot/plugins/meme_info.py`
- `bot/plugins/meme_plain_text.py`
- `bot/plugins/meme_query.py`
- `bot/plugins/meme_rand.py`
- `bot/plugins/meme_refresh.py`
- `bot/plugins/meme_setspeaker.py`
- `bot/plugins/meme_sim.py`
- `bot/plugins/_search_utils.py`

替换时跳过：

- `matcher.finish(None)`（不发送可见消息）；
- `matcher.send(MessageSegment.image(...))`（图片消息）；
- 已经包含 `MessageSegment.reply` 的现有逻辑（统一收口到 `build_reply_text`）。

### 2.3 `bot/session.py` 改造

- `execute_cancel(scope, message)` 增加 `event: MessageEvent` 参数，内部使用 `reply.finish(event, chat.matcher, message)`；
- `timeout_session` 中的 `bot.send(event, message)` 改为 `reply.bot_send(event, bot, message)`。

### 2.4 现有 reply 逻辑收口

- `reject_with_reply` 改为直接调用 `reply.reject(event, matcher, text)`；
- `present_candidates` 中的群聊 reply 构造逻辑改为调用 `build_reply_text(event, content)`，保持 `use_reject` 分支分别走 `reply.reject` / `reply.send`。

## 3. 边界情况处理

| 场景 | 处理方式 |
|------|----------|
| `event.message_id` 为 `None` | 退化为纯文本，不附加 reply |
| 私聊事件 | 退化为纯文本，不附加 reply |
| `matcher.finish(None)` | 不经过 `bot/reply.py`，保持原样 |
| 图片消息 | 直接 `matcher.send(MessageSegment.image(...))`，不经过 `bot/reply.py` |
| `execute_cancel` 时 `chat.matcher` 为 `None` | 保持现有 guard：先判断 `if chat.matcher`，再发送 |
| `timeout_session` 发送失败 | 保持现有 `try/except` 静默失败逻辑 |
| 发送内容不是纯文本（如已有 `Message` 对象） | 不调用 `build_reply_text`，保持调用方原有逻辑 |

## 4. 测试策略

### 4.1 新增 `bot/reply.py` 单元测试

覆盖 `build_reply_text`：

- 群聊 + `message_id` 存在 → 返回 `Message`，包含 `reply` 和 `text` segment；
- 私聊 → 返回原字符串；
- 群聊但 `message_id` 为 `None` → 返回原字符串。

### 4.2 现有插件测试更新

当前大量测试断言形如：

```python
matcher.finish.assert_awaited_once_with("当前没有活跃的会话")
```

改造后，群聊场景下 `matcher.finish` 会收到 `Message` 对象，需要改为检查消息内容：

```python
msg = matcher.finish.await_args[0][0]
assert extract_plain_text(msg) == "当前没有活跃的会话"
```

策略：

- 在 `tests/conftest.py` 或新建 `tests/helpers.py` 中新增辅助函数 `extract_message_text(msg)`，统一从 `str | Message` 中提取文本；
- 更新所有受影响的断言；
- 对关键路径（搜索、选择、超时）补充断言：群聊下消息第一个 segment 是 `reply`。

### 4.3 文档同步

根据项目规范，实施完成后需更新 `docs/api/API.md`，记录 `bot/reply.py` 中对外暴露的辅助函数签名与用途。

### 4.4 回归测试

- 运行 `pytest tests/unit` 确保没有断言断裂；
- 运行 `pyright` 保证类型标注正确。

## 5. 不采纳的方案

### 5.1 包装 Matcher 对象

提供一个 `ReplyAwareMatcher(event, matcher)` 包装类，重载 `finish` / `send` / `reject`，当参数为字符串且处于群聊时自动追加 `MessageSegment.reply`。

未采纳原因：侵入 NoneBot2 调用链，容易与框架内部行为冲突；对 `Bot.send`（超时场景）需要额外处理；测试 mock 更复杂。

### 5.2 分散到各插件原地修改

每个插件在发送文本前自行判断 `chat_type == "group"`，手动构造 `Message([MessageSegment.reply(...), ...])`。

未采纳原因：重复代码大量增加，后续新增插件或调整策略极易遗漏，维护成本高。

## 6. 验收标准

- [ ] 群聊中所有可见文本消息（错误提示、用法帮助、搜索列表、确认/取消提示、超时提示）都包含 `MessageSegment.reply`；
- [ ] 私聊中所有文本消息保持原样，不包含 `MessageSegment.reply`；
- [ ] 图片消息继续不带 `MessageSegment.reply`；
- [ ] `matcher.finish(None)` 行为不变；
- [ ] 新增 `bot/reply.py` 单元测试覆盖群聊/私聊/缺失 message_id 三种场景；
- [ ] 现有单元测试全部通过；
- [ ] `pyright` 无新增类型错误。
