# 设计文档：会话作用域拆分与群聊列表引用

> 日期：2026-07-12
> 状态：待实现
> 关联需求：
> 1. 一个会话 = 一个用户 + 一个聊天窗口。
> 2. 群聊中候选列表支持引用消息。

---

## 1. 背景与目标

### 1.1 当前问题

`bot/session.py` 中的 `SessionManager` 使用单一 `user_id` 作为会话键：

```python
self._chat_sessions: dict[str, ChatSession] = {}
self._selection_sessions: dict[str, SelectionSession] = {}
```

这导致：
- 同一用户在不同群聊中的命令会话互相冲突。
- 同一用户在私聊和群聊中的命令会话互相冲突。

### 1.2 目标

1. 将会话键从 `user_id` 改为「用户 + 聊天窗口」的复合作用域。
2. 群聊中发送候选列表时，通过 OneBot V11 的 `reply` 消息段引用相关消息，形成视觉上的回复链。
3. 保持私聊行为不变。
4. 保持现有会话互斥、超时、取消等生命周期逻辑不变。

---

## 2. 总体方案

采用**方案 B**：引入 `ChatScope` 值对象包装用户与聊天窗口，`SessionManager` 直接使用 `ChatScope` 作为字典键；群聊引用通过 `present_candidates` 的 `reply_message_id` 参数实现。

---

## 3. 详细设计

### 3.1 `ChatScope` 作用域对象

新增于 `bot/session.py`：

```python
from dataclasses import dataclass
from typing import Literal

from nonebot.adapters.onebot.v11 import MessageEvent


@dataclass(frozen=True, slots=True)
class ChatScope:
    """聊天作用域：一个用户在一个聊天窗口内的会话范围。

    Attributes:
        user_id: 用户 QQ 号。
        chat_type: 聊天类型，"private" 或 "group"。
        chat_id: 窗口标识；私聊为对方 QQ 号，群聊为群号。
    """

    user_id: int
    chat_type: Literal["private", "group"]
    chat_id: int

    def __str__(self) -> str:
        return f"{self.chat_type}:{self.chat_id}:user:{self.user_id}"

    @classmethod
    def from_event(cls, event: MessageEvent) -> "ChatScope":
        """从 NoneBot2 OneBot V11 消息事件构造作用域。

        目前项目仅支持私聊和群聊。若事件类型既不是 "group"
        也不是 "private"，则按私聊处理（兜底行为）。
        """
        user_id = int(event.get_user_id())
        message_type = getattr(event, "message_type", None)
        if message_type == "group":
            group_id = getattr(event, "group_id", None)
            if group_id is None:
                raise ValueError("群聊事件缺少 group_id")
            return cls(
                user_id=user_id,
                chat_type="group",
                chat_id=int(group_id),
            )
        return cls(user_id=user_id, chat_type="private", chat_id=user_id)
```

#### 设计要点

- `frozen=True` + `slots=True` 保证不可变、内存紧凑且可哈希，可直接作为 `dict` 键。
- 字段均为可哈希类型，因此 `ChatScope` 可直接作为字典键。

- `frozen=True` + `slots=True` 保证不可变且内存紧凑。
- 字段均为可哈希类型，因此 `ChatScope` 可直接作为字典键。
- `from_event` 负责统一从 `MessageEvent` 提取作用域，避免各插件重复解析。
- 私聊与群聊的 `chat_type` 不同，同一用户的私聊和群聊天然为不同作用域。
- 同一用户在不同群聊中 `chat_id`（群号）不同，天然为不同作用域。

### 3.2 `SessionManager` 改造

内部存储改为：

```python
self._chat_sessions: dict[ChatScope, ChatSession] = {}
self._selection_sessions: dict[ChatScope, SelectionSession] = {}
```

公共方法签名统一接收 `ChatScope`：

```python
def get_or_create_chat(self, scope: ChatScope) -> ChatSession: ...
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
def has_active_session(self) -> bool: ...
```

`timeout_session` 工具函数同步改造：

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
) -> None: ...
```

实现逻辑保持不变，仅键类型从 `str` 改为 `ChatScope`。

`execute_cancel(scope, ...)` 只会取消同一 `scope` 对应的会话；私聊中的 `/cancel` 不会取消该用户在群聊中的会话，反之亦然。

### 3.3 插件调用点改造

所有插件统一模式：

```python
from bot.session import ChatScope, session_manager

scope = ChatScope.from_event(event)

# 原来
if not session_manager.activate_chat(user_id, "search", matcher):
    ...

# 改为
if not session_manager.activate_chat(scope, "search", matcher):
    ...
```

需要改造的调用点：

| 文件 | 改动点 |
|------|--------|
| `bot/plugins/meme_plain_text.py` | `activate_chat`, `deactivate_chat` |
| `bot/plugins/meme_rand.py` | `activate_chat`, `handler_context`, `create_selection`, `remove_selection` |
| `bot/plugins/meme_query.py` | `activate_chat`, `deactivate_chat` |
| `bot/plugins/meme_sim.py` | `activate_chat`, `deactivate_chat` |
| `bot/plugins/meme_add.py` | `activate_chat`, `deactivate_chat`, `timeout_session`, `create_selection` |
| `bot/plugins/meme_addtag.py` | `activate_chat`, `deactivate_chat`, `timeout_session` |
| `bot/plugins/meme_delete.py` | `activate_chat`, `deactivate_chat`, `timeout_session` |
| `bot/plugins/meme_edit.py` | `activate_chat`, `deactivate_chat`, `timeout_session` |
| `bot/plugins/meme_setspeaker.py` | `activate_chat`, `deactivate_chat`, `timeout_session` |
| `bot/plugins/meme_cancel.py` | `execute_cancel` |
| `bot/plugins/_search_utils.py` | `present_candidates`, `dispatch_search_results`, `execute_search`, `execute_combined_search`, `handle_got_selection` 增加并传递 `scope` |

### 3.4 群聊候选列表引用

#### 行为规则

- 只做视觉引用，不校验用户回复是否引用了列表消息。
- 私聊不添加 `reply` 消息段。
- `reply_message_id` 对应 OneBot V11 的 `message_id`，类型为 `int`。
- 用户回复无效编号或非法内容时，Bot 的拒绝/错误提示也应以 `reply` 形式引用用户该条回复消息。
- 引用目标：
  - **第一页列表**：引用用户触发命令的原始消息（`event.message_id`）。
  - **翻页**（“n”）：引用用户发送的 “n” 消息（`event.message_id`）。
  - **`/rand` 换一批**（“0”）：引用用户发送的 “0” 消息（`event.message_id`）。

#### `present_candidates` 改造

增加 `reply_message_id: int | None = None` 参数和 `scope: ChatScope` 参数：

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
    ...
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

#### 调用方传值示例

首次展示（`dispatch_search_results`）：

```python
await present_candidates(
    bot,
    event,
    cmd_matcher,
    first_page,
    scope,
    options=options,
    page_index=0,
    total_pages=total_pages,
    prompt_suffix=prompt_suffix,
    reply_message_id=event.message_id,
)
```

翻页（`handle_got_selection`）：

```python
await present_candidates(
    bot,
    event,
    matcher,
    current_page,
    scope,
    options=options,
    page_index=page_index,
    total_pages=matcher.state.get("total_pages", 1),
    use_reject=True,
    reply_message_id=event.message_id,
)
```

`/rand` 换一批（`got_rand_selection`）：

```python
await present_candidates(
    bot,
    event,
    matcher,
    new_results,
    scope,
    page_index=0,
    total_pages=1,
    prompt_suffix="回复 0 换一批",
    use_reject=True,
    reply_message_id=event.message_id,
)
```

单结果直接发图路径不发列表，无需引用。

#### 带引用的拒绝/错误提示

为统一处理群聊中错误提示的引用，新增辅助函数：

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

在 `handle_got_selection` 与 `got_rand_selection` 中，所有 `matcher.reject(...)` 调用均替换为：

```python
await reject_with_reply(matcher, scope, event.message_id, "无效编号，请回复 1-{N} 之间的数字")
```

包括：
- 无效编号。
- “没有更多结果了”。
- `/rand` 中的“回复 0 换一批”提示（在非法输入后重发列表时）。

这样可保证群聊中所有与用户交互的拒绝/提示消息都挂在对应用户消息下。

---

## 4. 测试计划

### 4.1 单元测试更新

- `tests/unit/test_session.py`：所有用例改用 `ChatScope`，覆盖私聊、群聊、不同群号。
- `tests/unit/test_session_manager.py`：更新方法调用，验证同一用户在不同 `ChatScope` 下会话独立。
- `tests/unit/plugins/` 中涉及 `_search_utils` 的测试：补充 `scope` 参数。
- 所有 `session_manager` 的 mock/stub 调用点需要同步更新方法签名。

### 4.2 新增覆盖

- 同一用户在不同群聊同时发起 `/query`，两个会话互不阻塞。
- 同一用户同时在私聊和群聊发起命令，互不阻塞。
- 群聊中 `present_candidates` 构造的消息包含 `MessageSegment.reply`。
- 私聊中 `present_candidates` 不包含 `reply`。
- 群聊中非法输入后的 `reject_with_reply` 构造的消息包含 `MessageSegment.reply`。
- 私聊中非法输入后的 `reject_with_reply` 不包含 `reply`。
- `ChatScope.from_event` 对私聊和群聊事件返回正确作用域。

---

## 5. 文档更新

- `docs/api/bot/session.md`：
  - 新增 `ChatScope` 说明。
  - 更新 `SessionManager` 和 `timeout_session` 的方法签名。
- `docs/PRD.md`：
  - 更新第 3 章中关于会话互斥的描述，明确为「同一授权用户在同一聊天窗口内」。

---

## 6. 依赖

无新增第三方依赖。

---

## 7. 风险与回滚

| 风险 | 缓解措施 |
|------|----------|
| 漏改某个插件的 `session_manager` 调用点 | 全量搜索 `session_manager\.` 调用，编译期/类型检查兜底 |
| `ChatScope.from_event` 对事件类型处理不完善 | 单元测试覆盖私聊、群聊事件构造 |
| 群聊 `reply` 消息段在某些 OneBot 实现上表现不一致 | 按 OneBot V11 标准实现，私聊不受影响 |

---

## 8. 验收标准

- [ ] 同一用户在不同群聊可同时持有独立会话。
- [ ] 同一用户私聊和群聊可同时持有独立会话。
- [ ] 群聊中候选列表消息以 `reply` 形式引用正确消息。
- [ ] 私聊行为与改造前一致。
- [ ] 所有现有单元测试通过（更新后）。
- [ ] `docs/api/bot/session.md` 和 `docs/PRD.md` 已同步更新。
