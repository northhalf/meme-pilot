# `got_selection` 重复 Handler Refactoring 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 `meme_search.py` 和 `meme_plain_text.py` 之间 50 行重复的 `got("selection")` handler，提取共享函数到 `_search_utils.py`。

**Architecture:** 全量提取整个 handler body（含 try/except 异常处理），两个插件各保留 ~4 行包装器。函数体通过 `error_label: str` 参数区分调用来源的日志消息。

**Tech Stack:** Python 3.12, NoneBot2, OneBot V11

---

### Task 1: `_search_utils.py` — 新增共享函数与 import

**Files:**
- Modify: `bot/plugins/_search_utils.py`

- [ ] **Step 1: 在 `_search_utils.py` 追加 import**

在现有 `from bot.session import ...` 块中添加缺失的导入：

```python
from nonebot.exception import FinishedException, RejectedException
from nonebot.adapters.onebot.v11 import Message

from bot.plugins._help_text import HELP_TEXT
from bot.session import (
    activate_chat,
    create_selection,
    deactivate_chat,
    get_selection,          # 新增
    got_intercept_bypass,   # 新增
    remove_selection,       # 新增
    timeout_session,
)
```

- [ ] **Step 2: 追加 `handle_got_selection` 共享函数**

在 `_search_utils.py` 文件末尾（`execute_search` 之后）追加：

```python
async def handle_got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message,
    error_label: str = "搜索",
) -> None:
    """处理 got 选择编号的共享逻辑。

    供 meme_search.py 和 meme_plain_text.py 的 got("selection") 包装器调用，
    消除两个插件间的重复代码。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
        error_label: 异常日志中的操作标签，用于区分调用方。
    """
    user_id = event.get_user_id()

    # got 入口重新激活 chat session（不同 asyncio task）
    activate_chat(user_id, "search", matcher)

    try:
        # /help 和 /cancel 旁路拦截
        text = event.get_plaintext().strip()
        if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
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
        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        deactivate_chat(user_id)

    except (FinishedException, RejectedException):
        deactivate_chat(user_id)
        raise
    except Exception:
        logger.exception("用户 %s 的 %s 处理异常", user_id, error_label)
        deactivate_chat(user_id)
        raise
```

- [ ] **Step 3: 校验语法**

```bash
uv run python -m compileall bot/plugins/_search_utils.py
```

---

### Task 2: `meme_search.py` — 替换 `got_selection` handler

**Files:**
- Modify: `bot/plugins/meme_search.py`

- [ ] **Step 1: 删除不再需要的 import**

从 `meme_search.py` 的 import 块中删除：
- `MessageSegment` — 不再直接使用
- `from bot.config import MEMES_DIR` — 不再直接使用
- `from bot.plugins._help_text import HELP_TEXT` — 不再直接使用
- `from bot.session import activate_chat, deactivate_chat, get_selection, got_intercept_bypass, remove_selection` — 改为 `from bot.plugins._search_utils import handle_got_selection`
- `from nonebot.exception import FinishedException, RejectedException` — 不再直接使用（共享函数内部处理）

保留：
- `from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent` — `Message` 在包装器参数中
- `from nonebot.params import Arg` — 包装器需要
- `search_cmd` — matcher 变量
- `from bot.auth import is_authorized, log_unauthorized`
- `from bot.plugins._search_utils import execute_search, handle_selection`
- `from bot.session import (activate_chat, deactivate_chat, ...)` — 删除整块

改为导入共享函数：
```python
from bot.plugins._search_utils import execute_search, handle_got_selection, handle_selection
```

- [ ] **Step 2: 替换 `got_selection` 函数体**

将 `meme_search.py` 的行 75-134（整个 `got_selection` 函数）替换为：

```python
@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    await handle_got_selection(bot, event, matcher, selection_msg, "/search")
```

- [ ] **Step 3: 校验语法**

```bash
uv run python -m compileall bot/plugins/meme_search.py
```

---

### Task 3: `meme_plain_text.py` — 替换 `got_selection` handler

**Files:**
- Modify: `bot/plugins/meme_plain_text.py`

- [ ] **Step 1: 删除不再需要的 import**

同 Task 2 Step 1 的模式：
- 删除 `from nonebot.exception import FinishedException, RejectedException`
- 删除 `from bot.config import MEMES_DIR`
- 删除 `from bot.plugins._help_text import HELP_TEXT`
- 删除 `from bot.session import (activate_chat, deactivate_chat, get_selection, got_intercept_bypass, remove_selection)`

改为导入：
```python
from bot.plugins._search_utils import execute_search, handle_got_selection, handle_selection
```

- [ ] **Step 2: 替换 `got_selection` 函数体**

将 `meme_plain_text.py` 的行 76-128（整个 `got_selection` 函数）替换为：

```python
@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    await handle_got_selection(bot, event, matcher, selection_msg, "兜底搜索")
```

- [ ] **Step 3: 校验语法**

```bash
uv run python -m compileall bot/plugins/meme_plain_text.py
```

---

### Task 4: 运行测试 & 全量语法检查

**Files:**
- N/A

- [ ] **Step 1: 运行全量编译检查**

```bash
uv run python -m compileall bot plugins
```

- [ ] **Step 2: 运行 pytest**

```bash
uv run pytest
```

- [ ] **Step 3: 确认无测试失败**

---

### Task 5: 更新 API 文档

**Files:**
- Modify: `docs/api/bot/plugins/_search_utils.md`

- [ ] **Step 1: 在 `_search_utils.md` 中追加 `handle_got_selection` 的文档条目**

在 `execute_search` 和 `handle_selection` 之后增加：

```markdown
### `handle_got_selection(bot, event, matcher, selection_msg, error_label) -> None`

处理 got 选择编号的共享逻辑。供 `meme_search.py` 和 `meme_plain_text.py` 的 `got("selection")` 包装器调用。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bot` | `Bot` | OneBot V11 Bot 实例 |
| `event` | `MessageEvent` | 消息事件 |
| `matcher` | `Matcher` | NoneBot2 Matcher 实例 |
| `selection_msg` | `Message` | 用户回复的选择编号消息 |
| `error_label` | `str` | 异常日志中的操作标签，默认"搜索" |

| 返回 | 说明 |
|------|------|
| `None` | 通过 `matcher.finish()` 直接回复 |

逻辑：got 入口激活 chat → `/help`/`/cancel` 旁路拦截 → 选择会话检查 → `handle_selection` → 发送图片 → 清理会话。
```

并在依赖表中追加：

```markdown
| `handle_got_selection()` | `bot.plugins._search_utils` | got 选择编号共享逻辑 |
```

- [ ] **Step 2: 确认 API.md 主索引不需要更新**

API.md 已有 `_search_utils.md` 的索引条目，只加了函数不用改索引本身。
