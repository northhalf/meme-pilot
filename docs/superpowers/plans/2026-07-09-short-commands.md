# 命令短别名（Short Command Aliases）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **提交策略：** 用户要求直接在 main 分支修改但**不要提交**。所有 task 末尾的「Commit」步骤一律替换为「不提交，保留工作区改动」。全程禁止 `git add` / `git commit`。

**Goal:** 为 9 个现有命令各增加一个等价短命令别名（如 `/search` 与 `/s` 等价），长短命令行为完全一致。

**Architecture:** NoneBot2 原生 `on_command(aliases=...)` 注册别名，别名与主命令共享同一 matcher/handler；6 个带参数命令把脆弱的 `removeprefix` 参数提取改为 `CommandArg()` 依赖注入（与命令名解耦，是短命令能正确取参的必要条件）。

**Tech Stack:** NoneBot2（on_command aliases、CommandArg 依赖注入）、pytest、Python 3.12。

**设计依据:** `docs/superpowers/specs/2026-07-09-short-commands-design.md`

---

## 命名映射（全文统一）

| 长命令 | 短命令 | 插件文件 | 测试文件 |
|--------|--------|----------|----------|
| /search | /s | bot/plugins/meme_search.py | tests/unit/plugins/test_meme_search.py |
| /help | /h | bot/plugins/meme_help.py | tests/unit/plugins/test_meme_help.py |
| /add | /a | bot/plugins/meme_add.py | tests/unit/plugins/test_meme_add.py |
| /addtag | /at | bot/plugins/meme_addtag.py | tests/unit/plugins/test_meme_addtag.py |
| /del | /d | bot/plugins/meme_delete.py | tests/unit/plugins/test_meme_delete.py |
| /edittext | /e | bot/plugins/meme_edit.py | tests/unit/plugins/test_meme_edit.py |
| /refresh | /r | bot/plugins/meme_refresh.py | tests/unit/plugins/test_meme_refresh.py |
| /setspeaker | /sp | bot/plugins/meme_setspeaker.py | tests/unit/plugins/test_meme_setspeaker.py |
| /cancel | /c | bot/plugins/meme_cancel.py | tests/unit/plugins/test_meme_cancel.py |

## 通用改造模式（适用于 T2–T7 带参数命令）

**注册行**：`on_command("<name>", rule=to_me(), priority=5, block=True)` → 追加 `, aliases={"<short>"}`。

**import**：`from nonebot.params import Arg` → `from nonebot.params import Arg, CommandArg`。

**handler 签名**：在 `matcher: Matcher` 之后追加参数 `args: Message = CommandArg()`。

**提取行**：删除 `raw... = event.get_plaintext().strip()` 与 `...removeprefix("/<name>").removeprefix("<name>").strip()` 两行，合并为 `... = args.extract_plain_text().strip()`（变量名保持原样）。

**测试改造规则**：
1. 在测试文件辅助区新增 `_make_message`（代码见各 task）。
2. 把该文件**所有** `handle_<name>(bot, event, matcher)` 调用补上第 4 个参数 `args=_make_message("<参数文本>")`。`<参数文本>` = 该调用对应 `_make_event(text=...)` 中命令名之后的部分；默认 text（仅命令名）时为 `""`。`got_*` 函数调用**不改**（其签名不变）。
3. `got_*` 测试中若构造了 `event.get_plaintext.return_value`，保持不变（got 流程仍用 event 文本做旁路拦截判断）。

**运行单个测试文件**：`uv run pytest tests/unit/plugins/test_meme_<name>.py -v`

---

## Task 1: 三个无参数命令加 aliases（/help /refresh /cancel）

**Files:**
- Modify: `bot/plugins/meme_help.py:17`
- Modify: `bot/plugins/meme_refresh.py:23`
- Modify: `bot/plugins/meme_cancel.py:19`

这三个命令 handler 不提取参数，仅加 `aliases`，测试无需改动。

- [ ] **Step 1: 修改 meme_help.py 注册行**

```python
help_cmd = on_command("help", rule=to_me(), priority=5, block=True, aliases={"h"})
```

- [ ] **Step 2: 修改 meme_refresh.py 注册行**

```python
refresh_cmd = on_command("refresh", rule=to_me(), priority=5, block=True, aliases={"r"})
```

- [ ] **Step 3: 修改 meme_cancel.py 注册行**

```python
cancel_cmd = on_command("cancel", rule=to_me(), priority=5, block=True, aliases={"c"})
```

- [ ] **Step 4: 运行这三个命令的测试确认未破坏**

Run: `uv run pytest tests/unit/plugins/test_meme_help.py tests/unit/plugins/test_meme_refresh.py tests/unit/plugins/test_meme_cancel.py -v`
Expected: PASS（handler 逻辑未变）

- [ ] **Step 5: 不提交**（保留工作区改动）

---

## Task 2: /search 加 aliases + CommandArg 提取

**Files:**
- Modify: `bot/plugins/meme_search.py:18,36,40,66-67`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 更新 test_meme_search.py——补 args 参数 + 新增短命令提取测试**

test_meme_search.py 已有 `_make_message`（约第 76 行），无需新增。

在文件末尾新增测试类：

```python
# ===========================================================================
# 短命令 /s 参数提取测试
# ===========================================================================


class TestShortCommandSearch:
    """短命令 /s 通过 CommandArg 提取关键词测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_search.session_manager, "activate_chat", return_value=True)
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_short_command_extracts_keyword(
        self,
        mock_auth: MagicMock,
        mock_activate: MagicMock,
        mock_exec: AsyncMock,
    ) -> None:
        """短命令 /s 的参数经 CommandArg 提取后应与 /search 一致。"""
        bot = _make_bot()
        event = _make_event(text="/s 加班")
        matcher = _make_matcher()

        await handle_search(bot, event, matcher, args=_make_message("加班"))

        assert mock_exec.call_args[0] == (bot, event, matcher, "加班")
```

把以下现有 `handle_search(_make_bot(), _make_event(...), _make_matcher())` 调用补 `args=_make_message("<参数文本>")`：
- `TestHandleSearchAuth.test_authorized_user_proceeds`：`_make_event()` 默认 text=`/search 加班` → `args=_make_message("加班")`
- `TestHandleSearchSessionRejection.test_inactive_session_proceeds`：默认 text → `args=_make_message("加班")`
- `TestHandleSearchEmptyKeyword.test_empty_keyword_replies_usage`：`_make_event(text="/search")` → `args=_make_message("")`
- `TestHandleSearchDelegation.test_execute_search_called_with_correct_args`：`_make_event(text="/search 测试关键词")` → `args=_make_message("测试关键词")`
- `TestHandleSearchDelegation.test_search_passes_score_options`：`_make_event(text="/search 测试")` → `args=_make_message("测试")`
- `TestHandleSearchErrorDeactivation.test_dispatch_exception_deactivates_session`：`_make_event(user_id=..., text="/search 测试")` → `args=_make_message("测试")`
- `TestHandleSearchAuth.test_unauthorized_user_ignored`：提前 return，统一补 `args=_make_message("")`
- `TestHandleSearchSessionRejection.test_active_session_rejected`：提前 return，统一补 `args=_make_message("")`

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: FAIL（handler 仍是 `handle_search(bot, event, matcher)`，新增测试传 args 会因签名不匹配报错；现有授权+激活测试因 `args.extract_plain_text` 不存在而 AttributeError）

- [ ] **Step 3: 修改 meme_search.py**

import（第 18 行附近）：
```python
from nonebot.params import Arg, CommandArg
```

注册行（第 36 行）：
```python
search_cmd = on_command("search", rule=to_me(), priority=5, block=True, aliases={"s"})
```

handler 签名（第 40 行）：
```python
@search_cmd.handle()
async def handle_search(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
```

提取行（第 66-67 行，删除 raw_text 行，替换 keyword 行）：
```python
        # 提取关键词
        keyword = args.extract_plain_text().strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: PASS

- [ ] **Step 5: 不提交**

---

## Task 3: /add 加 aliases + CommandArg 提取

**Files:**
- Modify: `bot/plugins/meme_add.py:20,44,47,80-82`
- Modify: `tests/unit/plugins/test_meme_add.py`

- [ ] **Step 1: 更新 test_meme_add.py——新增 _make_message + 补 args + 新增短命令测试**

在辅助区（`_make_response` 之后）新增：

```python
def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 Message 对象（CommandArg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg
```

在 `TestParseAddArgs` 类中新增短命令提取测试：

```python
    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_short_command_extracts_speaker_and_tags(
        self, mock_sm, mock_auth, mock_get_im
    ) -> None:
        """短命令 /a 的参数经 CommandArg 提取后应与 /add 一致。"""
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(
            _make_bot(), _make_event("111", "/a 小明 吐槽 加班"),
            matcher, args=_make_message("小明 吐槽 加班"),
        )
        assert matcher.state["speaker"] == "小明"
        assert matcher.state["tags"] == ["吐槽", "加班"]
```

把以下调用补 `args=_make_message("<参数文本>")`：
- `TestParseAddArgs.test_no_args`：`_make_event("111", "/add")` → `args=_make_message("")`
- `TestParseAddArgs.test_speaker_only`：`_make_event("111", "/add 小明")` → `args=_make_message("小明")`
- `TestParseAddArgs.test_speaker_and_tags`：`_make_event("111", "/add 小明 吐槽 加班")` → `args=_make_message("小明 吐槽 加班")`
- `TestHandleAdd.test_unauthorized_rejected`：`_make_event("999")` 默认 `/add` → `args=_make_message("")`
- `TestHandleAdd.test_group_chat_rejected`：event.get_plaintext=`/add 测试` → `args=_make_message("测试")`
- `TestHandleAdd.test_authorized_proceeds`：`_make_event("111")` 默认 → `args=_make_message("")`
- `TestHandleAdd.test_existing_session_rejected`：`_make_event("111")` → `args=_make_message("")`

`TestGotImage` 所有 `got_image(...)` 调用**不改**。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_add.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 meme_add.py**

import（第 20 行附近）：
```python
from nonebot.params import Arg, CommandArg
```

注册行（第 44 行）：
```python
add_cmd = on_command("add", rule=to_me(), priority=5, block=True, aliases={"a"})
```

handler 签名（第 47-48 行）：
```python
@add_cmd.handle()
async def handle_add(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
```

提取行（第 80-82 行，删除 raw_text 行，替换 args_text 行）：
```python
        # 解析 speaker 和 tags
        args_text = args.extract_plain_text().strip()
        parts = args_text.split()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_add.py -v`
Expected: PASS

- [ ] **Step 5: 不提交**

---

## Task 4: /addtag 加 aliases + CommandArg 提取

**Files:**
- Modify: `bot/plugins/meme_addtag.py:16,31,34,64-65`
- Modify: `tests/unit/plugins/test_meme_addtag.py`

- [ ] **Step 1: 更新 test_meme_addtag.py——新增 _make_message + 补 args + 新增短命令测试**

在辅助区新增 `_make_message`（同 Task 3 代码）。

在文件末尾新增测试类（需先读 test_meme_addtag.py 确认 `handle_addtag` 调用所用的 mock 模式，参考其现有 `TestHandleAddtag` 类的 patch 方式；以下测试沿用同一 mock 模式，参数取 `/at 42 心累 深夜`）：

```python
class TestShortCommandAddtag:
    """短命令 /at 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_id_and_tags(self) -> None:
        """短命令 /at 的参数经 CommandArg 提取后应与 /addtag 一致。"""
        with (
            patch("bot.plugins.meme_addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_addtag.session_manager.activate_chat", return_value=True
            ),
            patch("bot.plugins.meme_addtag.get_metadata_store") as mock_store,
            patch("bot.plugins.meme_addtag.session_manager.create_selection"),
            patch("bot.plugins.meme_addtag.session_manager.reset_current_task"),
            patch("bot.plugins.meme_addtag.timeout_session"),
            patch("bot.plugins.meme_addtag.asyncio.create_task"),
        ):
            entry = MagicMock()
            entry.tags = []
            entry.text = "旧文本"
            store = MagicMock()
            store.get_entry.return_value = entry
            mock_store.return_value = store

            matcher = _make_matcher()
            asyncio.run(
                handle_addtag(
                    _make_bot(),
                    _make_event(text="/at 42 心累 深夜"),
                    matcher,
                    args=_make_message("42 心累 深夜"),
                )
            )
            assert matcher.state["entry_id"] == 42
            assert matcher.state["tags"] == ["心累", "深夜"]
```

> 注：执行时先读 test_meme_addtag.py，按其 `_make_event`/`_make_matcher`/`_make_bot` 的实际定义与现有 `TestHandleAddtag` 的 patch 链调整上述 mock。核心断言是 `matcher.state["entry_id"] == 42` 且 `matcher.state["tags"] == ["心累", "深夜"]`。

把该文件**所有** `handle_addtag(bot, event, matcher)` 调用补 `args=_make_message("<参数文本>")`。`<参数文本>` 按各测试 `_make_event(text=...)` 命令名之后部分确定（默认 `/addtag` → `""`）。`got_confirm` 调用不改。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_addtag.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 meme_addtag.py**

import（第 16 行附近）：
```python
from nonebot.params import Arg, CommandArg
```

注册行（第 31 行）：
```python
addtag_cmd = on_command("addtag", rule=to_me(), priority=5, block=True, aliases={"at"})
```

handler 签名（第 34-35 行）：
```python
@addtag_cmd.handle()
async def handle_addtag(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
```

提取行（第 64-65 行，删除 raw 行，替换 text_part 行）：
```python
        # 解析参数
        text_part = args.extract_plain_text().strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_addtag.py -v`
Expected: PASS

- [ ] **Step 5: 不提交**

---

## Task 5: /del 加 aliases + CommandArg 提取

**Files:**
- Modify: `bot/plugins/meme_delete.py:16,27,45,75-76`
- Modify: `tests/unit/plugins/test_meme_delete.py`

- [ ] **Step 1: 更新 test_meme_delete.py——新增 _make_message + 补 args + 新增短命令测试**

在辅助区新增 `_make_message`（同 Task 3 代码）。

在文件末尾新增测试类（参考 test_meme_delete.py 现有 `TestHandleDelete` 的 patch 链；参数取 `/d 12 42`）：

```python
class TestShortCommandDelete:
    """短命令 /d 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_ids(self) -> None:
        """短命令 /d 的参数经 CommandArg 提取后应与 /del 一致。"""
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat", return_value=True
            ),
            patch("bot.plugins.meme_delete.get_metadata_store") as mock_store,
        ):
            entry = MagicMock()
            entry.text = "文本"
            store = MagicMock()
            store.get_entry.return_value = entry
            mock_store.return_value = store

            matcher = _make_matcher()
            asyncio.run(
                handle_delete(
                    _make_bot(),
                    _make_event(text="/d 12 42"),
                    matcher,
                    args=_make_message("12 42"),
                )
            )
            assert matcher.state["entry_ids"] == [12, 42]
```

> 注：执行时先读 test_meme_delete.py，按其现有 patch 链与 `_make_event`/`_make_matcher` 定义调整 mock（可能还需 patch `create_selection`/`reset_current_task`/`timeout_session`/`asyncio.create_task`，与现有确认摘要测试一致）。核心断言 `matcher.state["entry_ids"] == [12, 42]`。

把该文件**所有** `handle_delete(bot, event, matcher)` 调用补 `args=_make_message("<参数文本>")`（默认 `/del` → `""`）。`got_confirm` 调用不改。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_delete.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 meme_delete.py**

import（第 16 行附近）：
```python
from nonebot.params import Arg, CommandArg
```

注册行（第 27 行）：
```python
delete_cmd = on_command("del", rule=to_me(), priority=5, block=True, aliases={"d"})
```

handler 签名（第 45-46 行）：
```python
@delete_cmd.handle()
async def handle_delete(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
```

提取行（第 75-76 行，删除 raw 行，替换 text_part 行）：
```python
        # 解析参数
        text_part = args.extract_plain_text().strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_delete.py -v`
Expected: PASS

- [ ] **Step 5: 不提交**

---

## Task 6: /edittext 加 aliases + CommandArg 提取

**Files:**
- Modify: `bot/plugins/meme_edit.py:15,33,36,66-67`
- Modify: `tests/unit/plugins/test_meme_edit.py`

- [ ] **Step 1: 更新 test_meme_edit.py——新增 _make_message + 补 args + 新增短命令测试**

在辅助区新增 `_make_message`（同 Task 3 代码）。

在 `TestHandleEdit` 类中新增短命令提取测试（沿用该类现有 patch 模式）：

```python
    def test_short_command_extracts_id_and_text(self) -> None:
        """短命令 /e 的参数经 CommandArg 提取后应与 /edittext 一致。"""
        with (
            patch("bot.plugins.meme_edit.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_edit.session_manager.activate_chat", return_value=True
            ),
            patch("bot.plugins.meme_edit.get_metadata_store") as mock_store,
        ):
            store = MagicMock()
            store.get_entry.return_value = _make_entry(text="旧文本")
            mock_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/e 5 新文本")
            matcher = _make_matcher()

            asyncio.run(handle_edit(bot, event, matcher, args=_make_message("5 新文本")))

            assert matcher.state["entry_id"] == 5
            assert matcher.state["new_text"] == "新文本"
```

> 注：`_make_entry` 与 `_make_matcher` 已存在于 test_meme_edit.py；执行时按现有 `TestHandleEdit` 的 patch 链补全（如 `create_selection`/`reset_current_task`/`timeout_session`/`asyncio.create_task` 等，与 `test_entry_not_found` 等一致）。核心断言 `matcher.state["entry_id"] == 5` 且 `matcher.state["new_text"] == "新文本"`（注意 new_text 经 `"".join(parts[1].split())` 去空白，故 `"新文本"` 不含空格）。

把该文件**所有** `handle_edit(bot, event, matcher)` 调用补 `args=_make_message("<参数文本>")`：
- `test_unauthorized`：默认 `/edittext` → `args=_make_message("")`
- `test_group_chat`：默认 → `args=_make_message("")`
- `test_invalid_args_no_text`：`/edittext` → `args=_make_message("")`
- `test_invalid_args_not_number`：`/edittext abc 新文本` → `args=_make_message("abc 新文本")`
- `test_entry_not_found`：`/edittext 5 新文本` → `args=_make_message("5 新文本")`
- `test_active_session_conflict`：`/edittext 5 新文本` → `args=_make_message("5 新文本")`

`got_confirm` 调用不改。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_edit.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 meme_edit.py**

import（第 15 行附近）：
```python
from nonebot.params import Arg, CommandArg
```

注册行（第 33 行）：
```python
edit_cmd = on_command("edittext", rule=to_me(), priority=5, block=True, aliases={"e"})
```

handler 签名（第 36-37 行）：
```python
@edit_cmd.handle()
async def handle_edit(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
```

提取行（第 66-67 行，删除 raw 行，替换 text_part 行）：
```python
        # 解析参数
        text_part = args.extract_plain_text().strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_edit.py -v`
Expected: PASS

- [ ] **Step 5: 不提交**

---

## Task 7: /setspeaker 加 aliases + CommandArg 提取

**Files:**
- Modify: `bot/plugins/meme_setspeaker.py:16,32,35,65-66`
- Modify: `tests/unit/plugins/test_meme_setspeaker.py`

- [ ] **Step 1: 更新 test_meme_setspeaker.py——新增 _make_message + 补 args + 新增短命令测试**

在辅助区新增 `_make_message`（同 Task 3 代码）。

在文件末尾新增测试类（参考 test_meme_setspeaker.py 现有 `TestHandleSetspeaker` 的 patch 链；参数取 `/sp 42 小明`）：

```python
class TestShortCommandSetspeaker:
    """短命令 /sp 通过 CommandArg 提取参数测试。"""

    def test_short_command_extracts_id_and_speaker(self) -> None:
        """短命令 /sp 的参数经 CommandArg 提取后应与 /setspeaker 一致。"""
        with (
            patch("bot.plugins.meme_setspeaker.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_setspeaker.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_setspeaker.get_metadata_store") as mock_store,
        ):
            entry = MagicMock()
            entry.image_path = "test.jpg"
            entry.speaker = None
            entry.text = "旧文本"
            store = MagicMock()
            store.get_entry.return_value = entry
            mock_store.return_value = store

            matcher = _make_matcher()
            asyncio.run(
                handle_setspeaker(
                    _make_bot(),
                    _make_event(text="/sp 42 小明"),
                    matcher,
                    args=_make_message("42 小明"),
                )
            )
            assert matcher.state["entry_id"] == 42
            assert matcher.state["speaker"] == "小明"
```

> 注：执行时先读 test_meme_setspeaker.py，按其现有 patch 链（含 `create_selection`/`reset_current_task`/`timeout_session`/`asyncio.create_task`/`matcher.send` 等）补全。核心断言 `matcher.state["entry_id"] == 42` 且 `matcher.state["speaker"] == "小明"`。

把该文件**所有** `handle_setspeaker(bot, event, matcher)` 调用补 `args=_make_message("<参数文本>")`（默认 `/setspeaker` → `""`；带参数的按 `_make_event(text=...)` 命令名之后部分）。`got_confirm` 调用不改。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/plugins/test_meme_setspeaker.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 meme_setspeaker.py**

import（第 16 行附近）：
```python
from nonebot.params import Arg, CommandArg
```

注册行（第 32 行）：
```python
setspeaker_cmd = on_command("setspeaker", rule=to_me(), priority=5, block=True, aliases={"sp"})
```

handler 签名（第 35-36 行）：
```python
@setspeaker_cmd.handle()
async def handle_setspeaker(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
```

提取行（第 65-66 行，删除 raw 行，替换 text_part 行）：
```python
        # 解析参数
        text_part = args.extract_plain_text().strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/plugins/test_meme_setspeaker.py -v`
Expected: PASS

- [ ] **Step 5: 不提交**

---

## Task 8: 更新 HELP_TEXT 行内括注

**Files:**
- Modify: `bot/plugins/_help_text.py:7-20`

- [ ] **Step 1: 替换 HELP_TEXT 内容**

把 `HELP_TEXT = """\ ... """` 整体替换为：

```python
HELP_TEXT = """\
/help (/h)：查看命令帮助
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>] (/a)：通过聊天添加一张表情包
/addtag <id> <tag>... (/at)：为指定表情包添加标签
/del <id>... (/d)：删除指定表情包（需确认）
/edittext <id> <新文本> (/e)：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人] (/sp)：设置或清空表情包的说话人
/refresh (/r)：扫描 memes/ 并增量更新索引
/info：查看机器人状态与统计信息
/cancel (/c)：取消当前正在执行的命令"""
```

- [ ] **Step 2: 运行 /help 测试确认未破坏**

Run: `uv run pytest tests/unit/plugins/test_meme_help.py -v`
Expected: PASS（现有断言检查 `"/help"` `"/search"` `"/ai"` `"/add"` `"/refresh"` 仍在文本中）

- [ ] **Step 3: 不提交**

---

## Task 9: 同步文档（README / CONTEXT / API / PRD）

**Files:**
- Modify: `README.md`
- Modify: `CONTEXT.md`
- Modify: `docs/api/API.md`
- Modify: `docs/PRD.md`

- [ ] **Step 1: 更新 README.md 帮助清单**

在 `### 🧭 帮助 /help` 代码块中，为 9 个命令加 `(/短命令)` 括注，与 HELP_TEXT 一致。例如：
```
     /help (/h)：查看命令帮助
     /search <关键词> (/s)：按 OCR 文本关键词搜索表情包
     ...
     /cancel (/c)：取消当前正在执行的命令
```
（`/rand` `/sim` `/ai` `/info` 不加括注）

- [ ] **Step 2: 更新 CONTEXT.md 交互协议表**

在 `/help`、`/search`、`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/refresh`、`/cancel` 各术语定义中补充「短命令 `/<短>`」说明。例如 `/search` 定义末尾追加：「短命令 `/s`，与 `/search` 等价」。

- [ ] **Step 3: 更新 docs/api/API.md 各插件说明**

在各插件小节的「注册」行补充 `aliases`，带参数命令补充「参数经 `CommandArg()` 提取」。例如 `meme_search`：
- 注册：`on_command("search", rule=to_me(), priority=5, block=True, aliases={"s"})`
- 参数提取：`CommandArg()` 注入（短命令 `/s` 与 `/search` 等价）

- [ ] **Step 4: 更新 docs/PRD.md 各功能触发方式**

在 3.1（/search）、3.4（/help）、3.3（/add）、`/addtag`、`/del`、3.6（/edittext）、3.7（/setspeaker）、3.5（/refresh）各节「触发方式」补充短命令。例如 3.1：
> 用户在私聊中发送命令：`/search <关键词>`（短命令 `/s`）

- [ ] **Step 5: 不提交**

---

## Task 10: 全量验证

- [ ] **Step 1: 全量单元测试**

Run: `uv run pytest tests/unit -v`
Expected: PASS（所有插件与 engine 单元测试通过）

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot tests`
Expected: 无错误

- [ ] **Step 3: 工作区状态确认**

Run: `git status`
Expected: 9 个插件文件 + 9 个测试文件 + `_help_text.py` + README.md + CONTEXT.md + API.md + PRD.md + 2 个 spec/plan 文档为 modified/untracked；**无任何新 commit**。

- [ ] **Step 4: 不提交**（等待用户审核后由用户决定提交）

---

## 实现注意事项

1. **不 commit**：全程禁止 `git add` / `git commit`，改动保留在工作区供用户审核。
2. **CommandArg 仅在 handle 入口有效**：参数提取只在 `@handle` 函数内用 `args`；`got_*` 函数签名与逻辑不变。
3. **`got_*` 测试不改**：got 函数仍用 `Arg` 注入，现有 got 测试保持原样。
4. **提前 return 分支统一补 args**：非授权/群聊/会话冲突分支虽不访问 args，但为调用一致性统一补 `args=_make_message("")`。
5. **aliases 不写单测**：单元测试 mock 了 `on_command`，无法验证 aliases 注册；aliases 正确性靠 spec 规范 + compileall + 代码审查保证。
6. **行为收敛点**：改用 CommandArg 后无空格连写（如 `/del12`）不再解析为命令参数，与 PRD 示例（均带空格）一致，已在 spec 记录。
7. **读文件再改**：T4/T5/T7 的新增测试 mock 链需对照实际测试文件调整，执行前先读对应 test 文件。
