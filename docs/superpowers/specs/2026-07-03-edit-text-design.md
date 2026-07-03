# OCR 文本编辑功能设计文档

> 版本：v1.0
> 日期：2026-07-03
> 状态：待实现

---

## 1. 需求概述

授权用户可以通过 `/edittext <entry_id> <新文本>` 命令修改指定表情包的 OCR 文本，修改后同步更新 sqlite 元数据与 chroma 向量库。

### 交互流程

```
用户私聊: /edittext 5 加班到崩溃
  │
  ├── 参数解析 → 校验 entry_id 存在性
  │
  ▼
Bot 发送对应图片 + 确认消息：
「当前 OCR 文本：加班心累
 修改后文本：加班到崩溃
 回复「确认」或「yes」确认修改，回复其他内容取消」
  │
  ▼
用户回复「确认」或「yes」
  │
  ▼
IndexManager.edit_text(5, "加班到崩溃")
  1. 锁外生成新 embedding
  2. Write Worker 写锁内更新 sqlite + chroma
  │
  ▼
Bot: 「OCR 文本已修改 ✅
      旧：加班心累
      新：加班到崩溃」
```

### 边界情况

| 场景 | 行为 |
|------|------|
| entry_id 不存在 | 回复「未找到 id 为 {id} 的表情包」 |
| 新文本与自身当前文本相同 | 直接返回成功（无实际修改） |
| 新文本已被其他条目使用 | 回复「该 OCR 文本已被其他表情包使用，请换一个」 |
| refresh 进行中 | 回复「索引正在刷新，请稍后再试」 |
| refresh pending 中 | 同 refresh 进行中，拒绝修改 |
| Bot 正在关闭 | 回复「服务正在关闭，请稍后再试」 |
| Embedding API 异常 | 回复「修改失败（Embedding 异常），请稍后重试」 |
| chroma upsert 失败 | 回滚 sqlite text 后回复「修改失败（Embedding 异常），请稍后重试」（与 Embedding API 异常复用同一文案） |
| 用户输入非确认内容 | 回复「已取消修改」 |
| 等待确认超时 | 回复「修改已取消（超时）」 |
| 等待确认时 /cancel | 取消等待，回复「取消修改 ✅」 |
| 等待确认时 /help | 旁路发送帮助文本，等待继续 |
| 已有活跃会话（/add 或 /search） | 回复「已有命令在处理中，请先 /cancel」 |
| 群聊中 @bot 调用 | 回复「此命令仅限私聊使用」 |
| 非授权用户 | 静默忽略，仅记录日志 |

---

## 2. 架构变更

### 2.1 Write Worker 模式

新增全局唯一的 **Write Worker**（单 `asyncio.Task`），所有单条写入操作（`add` 的写入阶段、`edit_text`）统一通过 `asyncio.Queue` 串行化处理。

```
add_worker_loop ──→  OCR/embed  ──→  put WriteRequest ──→┐
                                                           ▼
edit_text()    ──→  embed      ──→  put WriteRequest ──→  Write Worker
                                                           │
                                                      (串行处理队列,
                                                       获取写锁,
                                                       先sqlite后chroma)
                                                           │
refresh()      ──→ 等待write_queue为空 → 获取写锁，
                    直接操作Store(批量写入，不走queue)
```

### 2.2 竞态防护矩阵

| 时序 | 防护机制 |
|------|---------|
| edit_text 启动时 refresh 已激活/pending | 入口双检 `_refresh_active \| _refresh_pending` |
| embed 期间 refresh 激活 | embed 完成后 put 前二次检查 |
| 两个 edit_text 修改同一 entry | Write Worker 串行，后发覆盖 |
| edit_text 与 add 的 text 冲突 | Write Worker 写锁内重检 `get_id_by_text` + sqlite UNIQUE 兜底 |
| chroma upsert 失败 | 回滚 sqlite text 到旧值 |
| search/ai_match 读脏数据 | Write Worker 持写锁，读者等读锁释放 |

---

## 3. 数据类型

### `WriteOp` 枚举

```python
from enum import Enum, auto

class WriteOp(Enum):
    ADD = auto()
    EDIT_TEXT = auto()
```

### `_WriteRequest`

```python
@dataclass
class _WriteRequest:
    """写入任务单元，由 Write Worker 串行处理。

    语义统一：text/embedding 均为"要写入的最终值"，
    不分 ADD/EDIT_TEXT。ADD 时 entry_id=0（由 store 自动分配），
    EDIT_TEXT 时 entry_id 指向目标 id。
    """
    op: WriteOp
    future: asyncio.Future

    entry_id: int = 0           # EDIT_TEXT: 目标 id；ADD: 0（store 自动分配）
    filename: str = ""          # ADD: memes/ 下文件名
    text: str = ""              # 写入的 text（ADD=OCR text，EDIT_TEXT=新文本）
    embedding: list[float] | None = None  # 对应的 embedding
    old_text: str = ""          # EDIT_TEXT: 旧 text（回滚用）
```

### `EditTextResult`

```python
@dataclass
class EditTextResult:
    """edit_text() 的返回结果。"""
    entry_id: int
    old_text: str
    new_text: str
```

### `DuplicateTextError`

```python
class DuplicateTextError(RuntimeError):
    """edit_text 要修改的文本已被其他条目使用。"""
```

---

## 4. IndexManager 变更

### 4.1 新增属性

```python
self._write_queue: asyncio.Queue[_WriteRequest] = asyncio.Queue()
self._write_worker_task: asyncio.Task | None = None
```

### 4.2 `edit_text()` 方法

```python
async def edit_text(self, entry_id: int, new_text: str) -> EditTextResult:
    """修改指定条目的 OCR 文本。

    流程：校验 → embed(锁外) → 二次检查 refresh → put WriteRequest → await future

    Args:
        entry_id: 要修改的索引 id。
        new_text: 新的 OCR 文本（调用方已去空白）。

    Returns:
        EditTextResult 描述修改结果。

    Raises:
        IndexAddCancelledError: Bot 正在关闭。
        RefreshInProgressError: 刷新进行中或 pending 中。
        ValueError: entry_id 不存在。
        DuplicateTextError: new_text 已被其他条目使用。
        EmbeddingError: Embedding 生成失败。
    """
    # 检查①：shutting_down（最高优先级，避免浪费 embed API）
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")

    # 检查②：refresh 状态
    if self._refresh_active or self._refresh_pending:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    # 确保 Write Worker 已启动
    self._ensure_write_worker()

    # 校验 entry 存在 + 获取旧 text（用于回滚）
    entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
    if entry is None:
        raise ValueError(f"entry_id={entry_id} 不存在")
    old_text = entry.text
    if old_text == new_text:
        return EditTextResult(entry_id=entry_id, old_text=old_text, new_text=new_text)

    # 锁外生成新 embedding
    new_embedding = await self._embedding_provider.embed(new_text)

    # 检查③：TOCTOU 防护（embed 期间 shutting_down 或 refresh 可能已激活）
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active or self._refresh_pending:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    # 提交写入任务
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    req = _WriteRequest(
        op=WriteOp.EDIT_TEXT,
        future=future,
        entry_id=entry_id,
        text=new_text,
        embedding=new_embedding,
        old_text=old_text,
    )
    await self._write_queue.put(req)
    return await future
```

### 4.3 `_write_worker_loop()`

```python
async def _write_worker_loop(self) -> None:
    """串行处理所有写入任务（写锁保护）。"""
    while True:
        try:
            req = await self._write_queue.get()
        except asyncio.CancelledError:
            # 取消所有 pending future
            while not self._write_queue.empty():
                try:
                    pending = self._write_queue.get_nowait()
                    if not pending.future.done():
                        pending.future.set_exception(
                            IndexAddCancelledError("写入工作线程已停止")
                        )
                except asyncio.QueueEmpty:
                    break
            raise

        async with self._rwlock.write():
            try:
                if req.op is WriteOp.ADD:
                    result = await self._write_entry(
                        req.filename, req.text, req.embedding,
                    )
                elif req.op is WriteOp.EDIT_TEXT:
                    result = await self._execute_edit_text(req)
                else:
                    raise ValueError(f"未知写入操作: {req.op}")

                if not req.future.done():
                    req.future.set_result(result)
            except Exception as exc:
                if not req.future.done():
                    req.future.set_exception(exc)
            finally:
                # 通知 _state_cond（refresh 可能在等待 write_queue 为空）
                async with self._state_cond:
                    self._state_cond.notify_all()
```

### 4.4 `_execute_edit_text()`

```python
async def _execute_edit_text(self, req: _WriteRequest) -> EditTextResult:
    """写锁内执行 edit_text 写入（先 sqlite 后 chroma，失败回滚）。"""
    # 写锁内 TOCTOU 检查 text 冲突
    existing_id = await self._run_sync(
        self._metadata_store.get_id_by_text, req.text,
    )
    if existing_id is not None and existing_id != req.entry_id:
        raise DuplicateTextError(
            f"OCR 文本「{req.text}」已被 entry_id={existing_id} 使用",
        )

    # 先 sqlite
    success = await self._run_sync(
        self._metadata_store.update, req.entry_id, text=req.text,
    )
    if not success:
        raise ValueError(f"entry_id={req.entry_id} 不存在")

    # 后 chroma，失败回滚 sqlite
    try:
        await self._vector_store.upsert(req.entry_id, req.embedding)
    except Exception as exc:
        await self._run_sync(
            self._metadata_store.update, req.entry_id, text=req.old_text,
        )
        raise EmbeddingError(
            f"edit_text upsert 失败，已回滚: entry_id={req.entry_id}",
        ) from exc

    return EditTextResult(
        entry_id=req.entry_id,
        old_text=req.old_text,
        new_text=req.new_text,
    )
```

### 4.5 `_add_worker_loop()` 改造

原 `_add_worker_loop` 中 OCR/embed 后获取写锁并调用 `_write_entry` 的部分，改为：

```python
# OCR/embed 完成后（原写锁 + _write_entry 部分替换为：）
loop = asyncio.get_running_loop()
future = loop.create_future()
await self._write_queue.put(_WriteRequest(
    op=WriteOp.ADD,
    future=future,
    filename=request.filename,
    text=text,
    embedding=embedding,
))
result = await future
request.future.set_result(result)
```

### 4.6 `close()` 改造

在 `close()` 中增加取消 `_write_worker_task`：

```python
if self._write_worker_task is not None and not self._write_worker_task.done():
    self._write_worker_task.cancel()
    tasks_to_wait.append(self._write_worker_task)
```

### 4.7 延迟启动 Write Worker

`_write_queue` 在 `__init__()` 中创建；Write Worker Task 采用延迟启动（与 `_add_workers` 模式一致），在 `edit_text()` 或 `add()` 首次调用时创建。

```python
# __init__()
self._write_queue: asyncio.Queue[_WriteRequest] = asyncio.Queue()
self._write_worker_task: asyncio.Task | None = None

# 延迟启动辅助方法
def _ensure_write_worker(self) -> None:
    if self._write_worker_task is None or self._write_worker_task.done():
        self._write_worker_task = asyncio.create_task(self._write_worker_loop())
```

### 4.8 refresh 增加 write_queue 等待

```python
# _refresh_pending 状态下等待 in_flight add 和 write_queue 均为空
await self._state_cond.wait_for(
    lambda: (
        (self._add_in_flight == 0 and self._write_queue.empty())
        or self._shutting_down
    ),
)
```

---

## 5. 插件端（`bot/plugins/meme_edit.py`）

### 5.1 完整代码结构

```python
"""《/edittext》命令插件 — 修改指定表情包的 OCR 文本。

授权用户私聊中发送 /edittext <entry_id> <新文本>，
Bot 发送图片和确认消息，用户回复「确认」或「yes」后执行修改。
"""

import asyncio
import logging
import uuid

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.helpers import Cooldown
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager, get_metadata_store
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.index_manager import (
    DuplicateTextError,
    EmbeddingError,
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

edit_cmd = on_command("edittext", rule=to_me(), priority=5, block=True)


@edit_cmd.handle()
async def handle_edit(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """入口：授权校验 → 参数解析 → 发图确认。"""
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /edittext", user_id)

    try:
        # 授权校验
        if not is_authorized(user_id):
            log_unauthorized(user_id, "edittext")
            return

        # 仅限私聊
        if event.message_type != "private":
            await matcher.finish("此命令仅限私聊使用")

        # 会话检查
        if not session_manager.activate_chat(user_id, "edittext", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")

        # 解析参数
        raw = event.get_plaintext().strip()
        text_part = raw.removeprefix("/edittext").removeprefix("edittext").strip()
        parts = text_part.split(maxsplit=1)
        if len(parts) < 2:
            await matcher.finish("用法：/edittext <entry_id> <新文本>")

        try:
            entry_id = int(parts[0])
        except ValueError:
            await matcher.finish("entry_id 必须为数字")

        new_text = "".join(parts[1].split())  # 统一去空白
        if not new_text:
            await matcher.finish("新文本不能为空")

        # 校验 entry 存在
        store = get_metadata_store()
        entry = store.get_entry(entry_id)
        if entry is None:
            await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")

        # 发送图片
        image_path = MEMES_DIR / entry.image_path
        if image_path.exists():
            await matcher.send(MessageSegment.image("file://" + str(image_path.resolve())))

        # 确认消息
        await matcher.send(
            f"当前 OCR 文本：{entry.text}\n"
            f"修改后文本：{new_text}\n"
            "回复「确认」或「yes」确认修改，回复其他内容取消",
        )

        # 存入 state
        matcher.state["entry_id"] = entry_id
        matcher.state["new_text"] = new_text
        matcher.state["old_text"] = entry.text

        # 注册超时
        selection_id = str(uuid.uuid4())
        task = asyncio.create_task(
            timeout_session(bot, event, user_id, selection_id, "修改已取消（超时）"),
        )
        session_manager.create_selection(user_id, selection_id, task)
        session_manager.reset_current_task(user_id)

    except asyncio.CancelledError:
        raise FinishedException


@edit_cmd.got("confirm")
async def got_confirm(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    confirm_msg: Message = Arg("confirm"),
) -> None:
    """处理用户确认/取消。"""
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):
        try:
            text = event.get_plaintext().strip()

            # 旁路拦截 /help 和 /cancel
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            if text.strip().lower() in ("确认", "yes", "y"):
                entry_id = matcher.state["entry_id"]
                new_text = str(matcher.state["new_text"])

                try:
                    result = await asyncio.wait_for(
                        get_index_manager().edit_text(entry_id, new_text),
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
                except DuplicateTextError as exc:
                    await matcher.finish(str(exc))
                except EmbeddingError:
                    await matcher.finish("修改失败（Embedding 异常），请稍后重试")
                else:
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish(
                        f"OCR 文本已修改 ✅\n"
                        f"旧：{result.old_text}\n"
                        f"新：{result.new_text}",
                    )
                    return
            else:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("已取消修改")

            # 异常统一清理
            session_manager.deactivate_chat(user_id)

        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except RejectedException:
            raise
        except asyncio.CancelledError:
            raise FinishedException
        except Exception:
            logger.exception("用户 %s 的 /edittext 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
```

---

## 6. 其他配套变更

### `_help_text.py`

```python
HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [目标命名]：通过聊天添加一张表情包
/edittext <id> <新文本>：修改指定表情包的 OCR 文本
/refresh：扫描 memes/ 并增量更新索引
/cancel：取消当前正在执行的命令\
"""
```

### `docs/api/API.md`

新增以下条目：
- `bot/engine/index_manager.py`: `EditTextResult`、`DuplicateTextError`、`edit_text()`
- `bot/plugins/meme_edit.py`: `/edittext` 命令插件
- `bot/engine/index_manager.py`: `_WriteRequest`、`_write_worker_loop`（内部实现）
- `bot/app_state.py`: 导出 `EditTextResult`、`DuplicateTextError`（可选）

### `CONTEXT.md`

新增 `/edittext` 术语：授权用户在私聊中发送 `/edittext <entry_id> <新文本>` 修改指定 entry 的 OCR 文本；两步确认交互；权限属组 A（仅私聊）。

### `README.md`

功能列表新增：
```
### ✏️ OCR 文本编辑 `/edittext`
授权用户在私聊中发送 `/edittext <id> <新文本>`，Bot 发送图片和确认消息，
用户回复「确认」后执行修改。修改会同步更新文本索引和向量库。
```

---

## 7. 测试计划

### 单元测试（`tests/unit/engine/test_index_manager.py`）

| 测试 | 验证点 |
|------|--------|
| `test_edit_text_normal` | 正常修改：sqlite text 更新、chroma upsert 调用 |
| `test_edit_text_same_text` | 新文本与当前文本相同 → 直接返回，无写入 |
| `test_edit_text_entry_not_found` | entry_id 不存在 → ValueError |
| `test_edit_text_duplicate_text` | text 被其他条目使用 → DuplicateTextError |
| `test_edit_text_refresh_active` | refresh 进行中 → RefreshInProgressError |
| `test_edit_text_refresh_pending` | refresh pending 中 → RefreshInProgressError |
| `test_edit_text_upsert_failure` | chroma upsert 失败 → sqlite 回滚 |
| `test_edit_text_toctou_after_embed` | embed 期间 refresh 激活 → put 前拒绝 |
| `test_edit_text_shutting_down` | shutting_down → IndexAddCancelledError 两次检查均生效 |

### 插件测试（`tests/unit/plugins/`）

| 测试 | 验证点 |
|------|--------|
| `test_edittext_unauthorized` | 非授权用户 → 静默忽略 |
| `test_edittext_group_chat` | 群聊中 @bot → 回复仅限私聊 |
| `test_edittext_invalid_args` | 参数不足 / entry_id 非数字 → 用法提示 |
| `test_edittext_invalid_id` | entry_id 不存在 → 错误消息 |
| `test_edittext_confirm_flow` | 用户回复「确认」→ edit_text 被调用 |
| `test_edittext_cancel_flow` | 用户回复其他内容 → 回复已取消 |
| `test_edittext_help_bypass` | 等待确认时 /help → 旁路 |
| `test_edittext_cancel_bypass` | 等待确认时 /cancel → 取消 |
| `test_edittext_timeout` | 超时 → 回复超时消息 |

---

## 8. 文件变更清单

| 文件 | 变更类型 |
|------|---------|
| `bot/engine/index_manager.py` | 修改：新增 Write Worker、edit_text()、_execute_edit_text()；改造 _add_worker_loop() |
| `bot/engine/__init__.py` | 修改：导出 DuplicateTextError、EditTextResult |
| `bot/plugins/meme_edit.py` | 新增 |
| `bot/plugins/_help_text.py` | 修改：增加 /edittext |
| `docs/api/API.md` | 修改：新增接口文档 |
| `docs/superpowers/specs/2026-07-03-edit-text-design.md` | 新增（本文档） |
| `CONTEXT.md` | 修改：新增 /edittext 术语 |
| `README.md` | 修改：新增 /edittext 功能说明 |
| `tests/unit/engine/test_index_manager.py` | 修改：新增 edit_text 测试 |
| `tests/unit/plugins/test_meme_edit.py` | 新增 |
