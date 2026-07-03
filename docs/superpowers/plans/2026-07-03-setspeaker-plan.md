# `/setspeaker` 命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 添加 `/setspeaker` 命令，允许授权用户在私聊中设置或清空表情包的 speaker（说话人）字段。

**Architecture:** IndexManager 层新增 `set_speaker()` 方法，通过 Write Worker 串行队列写入（同 `edit_text` 模式），但 speaker 不涉及 chroma/embedding，只需 sqlite update。插件层新建 `meme_setspeaker.py`，完全参照 `meme_edit.py` 的确认交互模式。

**Tech Stack:** Python 3.12, NoneBot2, sqlite3, pytest, pylcs

---

⚠️ **执行提醒：本计划的每个 Task 由独立的 subagent 执行。每个 subagent 启动后需先调用 `sequential-thinking` 工具做结构化思考再动手。每个 Task 完成后必须 commit。**

---

### Task 0: 创建功能分支

- [ ] **Step 1: 从 main 创建功能分支**

```bash
git checkout main
git pull origin main
git checkout -b feat/setspeaker-command
```

---

### Task 1: IndexManager 基础类型扩展

**Files:**
- Modify: `bot/engine/index_manager.py:76-104` — WriteOp 枚举 + _WriteRequest + 新增 SetSpeakerResult

#### 步骤

- [ ] **Step 1: 修改 WriteOp 枚举，新增 SET_SPEAKER**

在 `bot/engine/index_manager.py` 中找到 `class WriteOp(Enum)`（约第76行），在 `EDIT_TEXT = auto()` 后新增一行：

```python
class WriteOp(Enum):
    """Write Worker 操作类型枚举。"""
    ADD = auto()
    EDIT_TEXT = auto()
    SET_SPEAKER = auto()
```

- [ ] **Step 2: 修改 _WriteRequest，新增 speaker 字段**

在 `_WriteRequest` dataclass（约第83行）的 `text: str = ""` 后新增：

```python
@dataclass
class _WriteRequest:
    """写入任务单元，由 Write Worker 串行处理。"""
    op: WriteOp
    future: "asyncio.Future[AddResult | EditTextResult | SetSpeakerResult]"
    entry_id: int = 0
    filename: str = ""
    text: str = ""
    speaker: str | None = None
    embedding: list[float] | None = None
    old_text: str = ""
```

- [ ] **Step 3: 在 EditTextResult 后新增 SetSpeakerResult dataclass**

在 `class EditTextResult`（约第106行）之后，`class DuplicateTextError` 之前插入：

```python
@dataclass
class SetSpeakerResult:
    """set_speaker() 的返回结果。

    Attributes:
        entry_id: 被修改的条目 id。
        old_speaker: 修改前的 speaker 值。
        new_speaker: 修改后的 speaker 值。
    """
    entry_id: int
    old_speaker: str | None
    new_speaker: str | None
```

- [ ] **Step 4: 运行语法检查确认无错误**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

- [ ] **Step 5: Commit**

```bash
git add bot/engine/index_manager.py
git commit -m "refactor(engine): WriteOp 新增 SET_SPEAKER，_WriteRequest 新增 speaker 字段，新增 SetSpeakerResult"
```

---

### Task 2: IndexManager.set_speaker() 单元测试（TDD 红）

**Files:**
- Modify: `tests/unit/engine/test_index_manager.py` — 在 TestEditText class 后新增 TestSetSpeaker class

#### 步骤

- [ ] **Step 1: 追加 TestSetSpeaker 测试类**

在 `tests/unit/engine/test_index_manager.py` 末尾追加（在 `TestEditText` 类之后）：

```python
# ---------------------------------------------------------------------------
# IndexManager.set_speaker()
# ---------------------------------------------------------------------------


class TestSetSpeaker:
    """IndexManager.set_speaker() 单元测试。"""

    @pytest.mark.anyio
    async def test_set_speaker_normal(self, index_manager: IndexManager) -> None:
        """正常设置 speaker。"""
        (Path(index_manager._memes_dir) / "cat.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("cat.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        result = await index_manager.set_speaker(eid, "张三")
        assert result.entry_id == eid
        assert result.old_speaker is None
        assert result.new_speaker == "张三"

        # 验证 sqlite 已更新
        entry = index_manager._metadata_store.get_entry(eid)
        assert entry is not None
        assert entry.speaker == "张三"

    @pytest.mark.anyio
    async def test_set_speaker_clear(self, index_manager: IndexManager) -> None:
        """清空 speaker（设为 None）。"""
        (Path(index_manager._memes_dir) / "dog.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("dog.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        # 先设置
        await index_manager.set_speaker(eid, "李四")
        # 再清空
        result = await index_manager.set_speaker(eid, None)
        assert result.entry_id == eid
        assert result.old_speaker == "李四"
        assert result.new_speaker is None

        entry = index_manager._metadata_store.get_entry(eid)
        assert entry is not None
        assert entry.speaker is None

    @pytest.mark.anyio
    async def test_set_speaker_no_change(self, index_manager: IndexManager) -> None:
        """speaker 无变化 → 直接返回，不进队列。"""
        (Path(index_manager._memes_dir) / "nochange.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("nochange.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        result = await index_manager.set_speaker(eid, "王五")
        assert result.new_speaker == "王五"

        # 再次设置相同值
        result2 = await index_manager.set_speaker(eid, "王五")
        assert result2.entry_id == eid
        assert result2.old_speaker == "王五"
        assert result2.new_speaker == "王五"

    @pytest.mark.anyio
    async def test_set_speaker_entry_not_found(
        self, index_manager: IndexManager
    ) -> None:
        """entry_id 不存在 → ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await index_manager.set_speaker(999, "张三")

    @pytest.mark.anyio
    async def test_set_speaker_refresh_active(
        self, index_manager: IndexManager
    ) -> None:
        """refresh 进行中 → RefreshInProgressError。"""
        index_manager._refresh_active = True
        with pytest.raises(RefreshInProgressError):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.anyio
    async def test_set_speaker_refresh_pending(
        self, index_manager: IndexManager
    ) -> None:
        """refresh pending 中 → RefreshInProgressError。"""
        index_manager._refresh_pending = True
        with pytest.raises(RefreshInProgressError):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.anyio
    async def test_set_speaker_shutting_down(
        self, index_manager: IndexManager
    ) -> None:
        """shutting_down → IndexAddCancelledError。"""
        index_manager._shutting_down = True
        with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
            await index_manager.set_speaker(1, "张三")

    @pytest.mark.anyio
    async def test_set_speaker_entry_deleted_concurrently(
        self, index_manager: IndexManager
    ) -> None:
        """_execute_set_speaker 内 entry 被并发删除 → ValueError。"""
        (Path(index_manager._memes_dir) / "race.jpg").write_bytes(b"fake")
        add_result = await index_manager.add("race.jpg")
        assert add_result.entry_id is not None
        eid = add_result.entry_id

        # 启用手动模式：让 _execute_set_speaker 的 TOCTOU 检查命中不存在
        store = index_manager._metadata_store
        original_get_entry = store.get_entry

        def get_entry_and_delete(eid2: int) -> MemeEntry | None:
            entry = original_get_entry(eid2)
            if entry is not None and eid2 == eid:
                store.remove(eid2)  # 在 TOCTOU 窗口内删除
            return entry

        store.get_entry = get_entry_and_delete  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="不存在"):
            await index_manager.set_speaker(eid, "张三")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestSetSpeaker -v
```

预期：全部 FAIL，因为 `IndexManager.set_speaker()` 尚未实现。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/engine/test_index_manager.py
git commit -m "test(engine): 添加 set_speaker 单元测试（TDD 红）"
```

---

### Task 3: 实现 IndexManager.set_speaker() + _execute_set_speaker()

**Files:**
- Modify: `bot/engine/index_manager.py` — 在 edit_text() 后新增 set_speaker()、在 _write_worker_loop 新增分支、新增 _execute_set_speaker()

#### 步骤

- [ ] **Step 1: 实现 IndexManager.set_speaker() 公有方法**

在 `edit_text()` 方法（约第488行）之后、`refresh()` 方法之前插入：

```python
    async def set_speaker(self, entry_id: int, speaker: str | None) -> SetSpeakerResult:
        """设置或清空指定条目的 speaker。

        流程：校验 → 读 entry → 无变更直接返回 → put WriteRequest → await future。

        Args:
            entry_id: 要修改的索引 id。
            speaker: 新说话人值；None 表示清空。

        Returns:
            SetSpeakerResult 描述修改结果。

        Raises:
            IndexAddCancelledError: Bot 正在关闭。
            RefreshInProgressError: 刷新进行中或 pending 中。
            ValueError: entry_id 不存在。
        """
        # 检查①：shutting_down
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")

        # 检查②：refresh 状态
        if self._refresh_active or self._refresh_pending:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # 确保 Write Worker 已启动
        self._ensure_write_worker()

        # 校验 entry 存在 + 获取 old_speaker
        entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")
        old_speaker = entry.speaker
        if old_speaker == speaker:
            return SetSpeakerResult(
                entry_id=entry_id,
                old_speaker=old_speaker,
                new_speaker=speaker,
            )

        # 提交写入任务（不需要 embed，直接入队）
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[SetSpeakerResult]" = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.SET_SPEAKER,
            future=future,  # type: ignore[arg-type]
            entry_id=entry_id,
            speaker=speaker,
        )
        await self._write_queue.put(req)
        return await future
```

- [ ] **Step 2: 在 _write_worker_loop 新增 SET_SPEAKER 分支**

在 `_write_worker_loop` 的 `elif req.op is WriteOp.EDIT_TEXT:` 分支（约第580行）之后、`else:` 之前插入：

```python
                        elif req.op is WriteOp.SET_SPEAKER:
                            result = await self._execute_set_speaker(req)
```

- [ ] **Step 3: 新增 _execute_set_speaker() 方法**

在 `_execute_edit_text()` 方法（约第662行）之后、`close()` 方法之前插入：

```python
    async def _execute_set_speaker(self, req: _WriteRequest) -> SetSpeakerResult:
        """写锁内执行 set_speaker 写入（仅 sqlite update，无 chroma 操作）。

        Args:
            req: 写入任务单元。

        Returns:
            SetSpeakerResult 描述修改结果。

        Raises:
            ValueError: entry_id 在写锁内已不存在。
        """
        # TOCTOU 防护：写锁内重新检查 entry 是否存在
        entry = await self._run_sync(
            self._metadata_store.get_entry, req.entry_id
        )
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")
        old_speaker = entry.speaker

        # 写 sqlite（speaker 无 UNIQUE 约束，不抛 DuplicateConflict）
        success = await self._run_sync(
            self._metadata_store.update,
            req.entry_id,
            speaker=req.speaker,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在（update 返回 False）")

        return SetSpeakerResult(
            entry_id=req.entry_id,
            old_speaker=old_speaker,
            new_speaker=req.speaker,
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestSetSpeaker -v
```

预期：全部 PASS

- [ ] **Step 5: 确认未破坏现有测试**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

预期：全部 PASS（含 TestSetSpeaker + 已有 TestEditText 等）

- [ ] **Step 6: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): 实现 IndexManager.set_speaker() + _execute_set_speaker()"
```

---

### Task 4: 插件层 meme_setspeaker.py

**Files:**
- Create: `bot/plugins/meme_setspeaker.py` — 新建插件文件
- Modify: `bot/plugins/_help_text.py` — 新增帮助文本

#### 步骤

- [ ] **Step 1: 创建 meme_setspeaker.py**

```python
"""/setspeaker 命令插件 — 设置或清空表情包的说话人（speaker）字段。

授权用户私聊中发送 /setspeaker <entry_id> [说话人]，
Bot 发送图片和确认消息，用户回复「确认」或「yes」后执行修改。
无 [说话人] 参数时清空 speaker。
"""

import asyncio
import logging
import uuid

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager, get_metadata_store
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.index_manager import (
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

setspeaker_cmd = on_command("setspeaker", rule=to_me(), priority=5, block=True)


@setspeaker_cmd.handle()
async def handle_setspeaker(
    bot: Bot, event: MessageEvent, matcher: Matcher
) -> None:
    """入口：授权校验 → 参数解析 → 发图确认。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /setspeaker", user_id)

    try:
        # 授权校验
        if not is_authorized(user_id):
            log_unauthorized(user_id, "setspeaker")
            return

        # 仅限私聊
        if event.message_type != "private":
            await matcher.finish("此命令仅限私聊使用")
            return

        # 会话检查
        if not session_manager.activate_chat(user_id, "setspeaker", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        # 解析参数
        raw = event.get_plaintext().strip()
        text_part = raw.removeprefix("/setspeaker").removeprefix("setspeaker").strip()
        parts = text_part.split(maxsplit=1)
        if len(parts) < 1:
            await matcher.finish("用法：/setspeaker <entry_id> [说话人]")
            return

        try:
            entry_id = int(parts[0])
        except ValueError:
            await matcher.finish("entry_id 必须为数字")
            return

        speaker: str | None = parts[1].strip() if len(parts) > 1 else None
        if speaker is not None and not speaker:
            speaker = None

        # 校验 entry 存在
        store = get_metadata_store()
        entry = store.get_entry(entry_id)
        if entry is None:
            await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")
            return

        # 发送图片
        image_path = MEMES_DIR / entry.image_path
        if image_path.exists():
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )

        # 确认消息
        old_speaker_text = entry.speaker if entry.speaker else "(无)"
        new_speaker_text = speaker if speaker else "(无)"
        await matcher.send(
            f"当前说话人：{old_speaker_text}\n"
            f"新说话人：{new_speaker_text}\n"
            "回复「确认」或「yes」确认修改，回复其他内容取消",
        )

        # 存入 state
        matcher.state["entry_id"] = entry_id
        matcher.state["speaker"] = speaker
        matcher.state["old_speaker"] = entry.speaker

        # 注册超时
        selection_id = str(uuid.uuid4())
        task = asyncio.create_task(
            timeout_session(
                bot, event, user_id, selection_id, "说话人设置已取消（超时）",
            ),
        )
        session_manager.create_selection(user_id, selection_id, task)
        session_manager.reset_current_task(user_id)

    except asyncio.CancelledError:
        raise FinishedException


@setspeaker_cmd.got("confirm")
async def got_confirm(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    confirm_msg: Message = Arg("confirm"),
) -> None:
    """处理用户确认/取消。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        confirm_msg: got("confirm") 接收到的消息。
    """
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):
        try:
            text = event.get_plaintext().strip()

            # 旁路拦截 /help 和 /cancel
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            if text.strip().lower() in ("确认", "yes", "y"):
                entry_id = matcher.state["entry_id"]
                speaker = matcher.state.get("speaker")

                try:
                    result = await asyncio.wait_for(
                        get_index_manager().set_speaker(entry_id, speaker),
                        timeout=get_index_manager().add_user_timeout,
                    )
                except asyncio.TimeoutError:
                    await matcher.finish("修改处理超时，请稍后再试")
                except IndexAddCancelledError:
                    await matcher.finish("服务正在关闭，请稍后再试")
                except RefreshInProgressError:
                    await matcher.finish("索引正在刷新，请稍后再试")
                except ValueError:
                    await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")
                else:
                    session_manager.deactivate_chat(user_id)
                    old_text = result.old_speaker if result.old_speaker else "无"
                    new_text = result.new_speaker if result.new_speaker else "无"
                    await matcher.finish(
                        f"说话人已设置 ✅\n"
                        f"旧：{old_text}\n"
                        f"新：{new_text}",
                    )
                    return
            else:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("已取消")

            # 异常统一清理
            session_manager.deactivate_chat(user_id)

        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except RejectedException:
            raise
        except asyncio.CancelledError:
            session_manager.deactivate_chat(user_id)
            raise FinishedException
        except Exception:
            logger.exception("用户 %s 的 /setspeaker 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
```

- [ ] **Step 2: 编译检查语法**

```bash
uv run python -m compileall bot/plugins/meme_setspeaker.py
```

- [ ] **Step 3: 更新 _help_text.py**

找到 `HELP_TEXT` 字符串，在 `/edittext` 帮助行之后新增一行：

```python
/setspeaker <id> [说话人]：设置表情包的说话人
```

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_setspeaker.py bot/plugins/_help_text.py
git commit -m "feat(plugins): 实现 /setspeaker 命令插件"
```

---

### Task 5: 插件单元测试

**Files:**
- Create: `tests/unit/plugins/test_meme_setspeaker.py`

#### 步骤

- [ ] **Step 1: 创建 test_meme_setspeaker.py**

请先查看 `tests/unit/plugins/test_meme_edit.py` 的模式，然后按相同模式编写 `test_meme_setspeaker.py`，覆盖 spec 中列出的所有用例：

| 用例 | 预期 |
|------|------|
| `/setspeaker 3 张三` 参数解析 | entry_id=3, speaker="张三" |
| `/setspeaker 5` 参数解析（清空） | entry_id=5, speaker=None |
| `/setspeaker abc 张三` 非数字 id | 回复"entry_id 必须为数字" |
| `/setspeaker` 无参数 | 回复用法提示 |
| 非授权用户调用 | 静默忽略 |
| 群聊中 @bot 调用 | 回复"此命令仅限私聊使用" |
| 回复「确认」确认修改 | 调用 set_speaker，回复含"说话人已设置" |
| 回复「yes」确认修改 | 同上 |
| 回复其他内容 | 回复"已取消" |
| 等待确认时 `/cancel` | 旁路取消，清理会话 |

```bash
# 先读取 meme_edit 测试模式
cat tests/unit/plugins/test_meme_edit.py
```

- [ ] **Step 2: 运行插件测试**

```bash
uv run pytest tests/unit/plugins/test_meme_setspeaker.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/plugins/test_meme_setspeaker.py
git commit -m "test(plugins): 添加 /setspeaker 插件单元测试"
```

---

### Task 6: 文档同步

**Files:**
- Modify: `docs/api/API.md` — 新增 meme_setspeaker 索引
- Create: `docs/api/bot/plugins/meme_setspeaker.md` — 新建 API doc
- Modify: `CONTEXT.md` — speaker 字段标记已实现
- Modify: `README.md` — 新增命令说明

#### 步骤

- [ ] **Step 1: 更新 docs/api/API.md**

在 `bot/plugins/meme_edit.py` 相关条目后新增 meme_setspeaker 索引。参考 meme_edit 的条目格式。

- [ ] **Step 2: 创建 docs/api/bot/plugins/meme_setspeaker.md**

```markdown
# /setspeaker 命令 — API 参考

## 依赖

- `app_state.get_index_manager()`
- `app_state.get_metadata_store()`
- `bot.auth.is_authorized()`
- `bot.session.session_manager`
- `bot.session.timeout_session`
- `bot.plugins._search_utils.got_intercept_bypass`
- `bot.config.read_session_timeout()`

## 命令

`on_command("setspeaker", rule=to_me(), priority=5, block=True)`

### handle_setspeaker

入口处理器。授权校验 → 私聊检查 → 会话激活 → 参数解析 → 发送图片与确认消息 → 注册超时。

### got_confirm

`got("confirm")` 处理器。处理用户确认/取消：
- 确认（确认/yes/y）→ `IndexManager.set_speaker()` → 回复修改结果
- 其他 → 回复"已取消"

## 错误处理

| 异常 | 用户消息 |
|------|----------|
| `IndexAddCancelledError` | 服务正在关闭，请稍后再试 |
| `RefreshInProgressError` | 索引正在刷新，请稍后再试 |
| `ValueError`（id 不存在） | 未找到 id 为 {entry_id} 的表情包 |
| `asyncio.TimeoutError` | 修改处理超时，请稍后再试 |

## 群聊

授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"。
```

- [ ] **Step 3: 更新 CONTEXT.md**

找到 `speaker` 相关的描述条目，更新 `v1.0 预留不填充` 为 `v1.0 可通过 /setspeaker 命令设置`。同步更新 `meme` 表的 `speaker TEXT` 列描述。

- [ ] **Step 4: 更新 README.md**

在 `/edittext` 命令说明后新增 `/setspeaker` 条目，格式参照 `/edittext` 的样式。

- [ ] **Step 5: Commit**

```bash
git add docs/api/API.md docs/api/bot/plugins/meme_setspeaker.md CONTEXT.md README.md
git commit -m "docs: 同步 /setspeaker 相关文档"
```

---

### Task 7: 全量测试验证

#### 步骤

- [ ] **Step 1: 运行全量测试**

```bash
uv run pytest -v
```

预期：全部 PASS

- [ ] **Step 2: 运行编译检查**

```bash
uv run python -m compileall bot tests
```

预期：无错误

- [ ] **Step 3: 运行类型检查**

```bash
uv run pyright bot/plugins/meme_setspeaker.py bot/engine/index_manager.py
```

预期：无新增类型错误

- [ ] **Step 4: 确认分支状态**

```bash
git log --oneline
```

展示所有 commit 记录，确认功能完整。

---

⚠️ **执行提醒：本计划的每个 Task 由独立的 subagent 执行。每个 subagent 启动后需先调用 `sequential-thinking` 工具做结构化思考再动手。每个 Task 完成后必须 commit。**
