# 会话管理重构设计文档

> 版本：v1.0
> 日期：2026-06-28
> 状态：待审阅

---

## 1. 问题概述

当前会话管理（`bot/session.py`）存在以下设计缺陷：

1. **会话职责混淆**：`PendingSession` 既充当聊天会话又充当选择会话，`timeout_task` 附着于同一个数据结构
2. **锁粒度过粗**：`/add` 在插件层获取索引锁，等待图片期间整个索引不可用，阻塞 `/search`/`/ai`/`/help`
3. **无持久会话**：命令结束后 session 被删除，无法跟踪用户状态
4. **无取消命令**：无法主动终止正在进行的操作
5. **新命令覆盖策略**：通过 `check_and_cancel` 覆盖旧命令而非拒绝，导致用户不知情的状态切换

## 2. 设计目标

1. 一个用户同一时间仅能处于一个聊天会话，至多一个选择会话
2. 聊天会话与选择会话分离，数据结构各自独立
3. 每个会话有唯一 ID，超时判断基于 `user_id + selection_id` 双重校验
4. 活跃会话中拒绝新命令，明确提示用户使用 `/cancel`
5. 支持 `/cancel` 和 `/help` 在任何状态下触发（绕过会话检查）
6. 锁范围最小化，`/add` 仅在 `add_entry` 时持锁

## 3. 数据模型

### 3.1 ChatSession（聊天会话）

```python
@dataclass
class ChatSession:
    """每个用户一个，持久存在，首次访问时懒创建。"""
    session_id: str              # UUID，首次创建时永久固定
    active: bool = False         # True=有命令正在处理
    command_type: str | None = None  # "add"/"search"/"ai"/"refresh"
    matcher: Matcher | None = None   # 当前命令的 NoneBot2 Matcher
    current_task: asyncio.Task | None = None  # 异步任务引用
```

- `session_id` 永久不变，用于标识该用户的聊天会话身份
- `active=False` 且 `matcher=None` 表示空闲
- `current_task` 在 handler 入口设当前 `asyncio.current_task()`，在 finally/finish 前清空

### 3.2 SelectionSession（选择会话）

```python
@dataclass
class SelectionSession:
    """选择会话，至多一个，是 ChatSession 的子集。"""
    selection_id: str            # UUID，每次创建选择时生成
    timeout_task: asyncio.Task | None = None  # 超时监控任务
```

- 无 `candidates` 字段，候选列表通过 `matcher.state["candidates"]` 传递
- `selection_id` 用于 `timeout_session` 闭包捕获，超时后按 `user_id + selection_id` 双重校验

### 3.3 模块级字典

```python
chat_sessions: dict[str, ChatSession] = {}       # user_id → ChatSession
selection_sessions: dict[str, SelectionSession] = {}  # user_id → SelectionSession
```

- 两个字典使用相同 key（user_id），values 可以独立存在
- `selection_id` 可以和 `ChatSession.session_id` 重复（概率极低，不作保证）

---

## 4. 核心 API

### 4.1 聊天会话操作

```python
def get_or_create_chat(user_id: str) -> ChatSession
    """首次访问时创建并存储 ChatSession，之后复用。"""
```

```python
def activate_chat(
    user_id: str,
    command_type: str,
    matcher: Matcher,
) -> bool
    """激活聊天会话。
    - 设置 active=True, matcher, command_type, current_task = asyncio.current_task()
    - Returns True=成功, False=已在活跃
    - 注意：NoneBot2 的 handle() 和 got() 运行在不同 asyncio task 中，
      各自的 handler 入口都需要调用 activate_chat 更新 current_task。
    - handler 的 finally 块中调用 deactivate_chat 清空。
    """
```

```python
def deactivate_chat(user_id: str) -> None
    """重置聊天会话为空闲。active=False, matcher=None, command_type=None, current_task=None。"""
```

### 4.2 选择会话操作

```python
def create_selection(
    user_id: str,
    selection_id: str,
    timeout_task: asyncio.Task,
) -> None
    """创建选择会话。覆盖同一用户的旧选择会话。"""
```

```python
def remove_selection(user_id: str) -> SelectionSession | None
    """移除选择会话，返回旧会话（用于取消 timeout_task）。"""
```

```python
def get_selection(user_id: str) -> SelectionSession | None
    """查询用户的选择会话。"""
```

### 4.3 超时管理

```python
async def timeout_session(
    bot: Bot,
    event: Event,
    user_id: str,
    selection_id: str,           # 闭包捕获该值
    message: str,
    *,
    on_cleanup: Callable | None = None,
    timeout: int | None = None,
) -> None:
    """超时后按 user_id + selection_id 双重校验。
    - 超时后检查 selection_sessions.get(user_id).selection_id == selection_id
    - 匹配 → 发送超时提示 + remove_selection + on_cleanup
    - 不匹配（被新选择覆盖）→ 静默退出
    """
```

### 4.4 取消操作

本模块提供 `execute_cancel` 作为取消入口，供 `handle_cancel`（`meme_cancel.py`）和 `got_intercept_bypass`（本模块内部 `/cancel` 分支）共享：

```python
async def execute_cancel(user_id: str) -> str | None:
    """执行取消逻辑。

    1. 检查是否有活跃会话，无则返回 None
    2. current_task.cancel()（若非当前 task 且未完成）
    3. remove_selection() + 取消 timeout_task（若有）
    4. 在旧 matcher 上 finish()（发送"会话已取消"到原上下文）
    5. deactivate_chat(user_id)

    Returns:
        str: 成功提示 "已取消 ✅"
        None: 无活跃会话，调用方自行发送提示
    """
    chat = chat_sessions.get(user_id)
    if not (chat and chat.active):
        return None

    # 防止自取消：同频道 /cancel 时 current_task 等于当前 task，跳过
    current = asyncio.current_task()
    if chat.current_task and not chat.current_task.done() and chat.current_task is not current:
        chat.current_task.cancel()

    # timeout_task 已有 except asyncio.CancelledError: return，无需额外处理

    ss = selection_sessions.pop(user_id, None)
    if ss and ss.timeout_task and not ss.timeout_task.done():
        ss.timeout_task.cancel()

    if chat.matcher:
        try:
            await chat.matcher.finish("当前会话已取消")
        except FinishedException:
            pass

    deactivate_chat(user_id)
    return "已取消 ✅"
```

### 4.5 Got 入口拦截

```python
async def got_intercept_bypass(
    user_id: str,
    matcher: Matcher,
    text: str,
    HELP_TEXT: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。

    内部 /cancel 分支委托给 execute_cancel。

    Returns True 表示拦截到命令（调用方应 return），
    False 表示正常流程继续。

    /cancel: execute_cancel + matcher.finish()
    /help:   matcher.send(HELP_TEXT) + matcher.reject() 继续等待
    """
    if text.startswith("/cancel ") or text == "/cancel":
        result = await execute_cancel(user_id)
        if result is None:
            await matcher.finish("当前没有活跃的会话")
        else:
            await matcher.finish(result)
        return True

    if text.startswith("/help ") or text == "/help":
        await matcher.send(HELP_TEXT)
        await matcher.reject("")
        return True

    return False
```

---

## 5. 状态转换图

```
                     ┌─────────────────────────────┐
                     │        ChatSession          │
                     │  active=False               │
                     │  matcher=None               │
                     │  command_type=None           │
                     └──────────┬──────────────────┘
                                │
              ┌─────────────────┼──────────────────┐
              │ 新命令          │                  │
              ▼                 ▼ (active=True)    │
     ┌────────────────┐  ┌──────────────────┐      │
     │ 拒绝并提示      │  │ 命令处理中        │      │
     │ (/cancel取消)   │  │ active=True      │      │
     └────────────────┘  │ matcher=当前      │      │
                         │ command_type=...  │      │
                         └────────┬─────────┘      │
                                  │                │
                    ┌─────────────┼──────┐         │
                    ▼             ▼      ▼         │
              ┌──────────┐ ┌─────────┐ ┌──────┐   │
              │ 正常完成   │ │/cancel  │ │出错   │   │
              │ deactivate│ │取消任务  │ │清理   │   │
              └──────────┘ │finish    │ │       │   │
                           │deactivate│ │       │   │
                           └──────────┘ └──────┘   │
                                │                  │
                                └──────────────────┘
```

选择会话作为聊天会话的子集，仅在多结果搜索场景中创建。选择会话活跃时，新命令同样被拒绝（因为父聊天会话仍 `active=True`）：

```
搜索会话活跃 (active=True + command_type="search")
    │
    ├── 无结果 / 单结果 ──→ deactivate_chat
    │
    ├── 多结果 ──→ create_selection(user_id, selection_id, task)
    │                 │
    │            ┌────┼────────────┐
    │            ▼    ▼            ▼
    │        ┌────┐ ┌────┐    ┌─────────┐
    │        │选择│ │超时│    │新命令    │
    │        │完成│ │触发│    │被拒绝    │
    │        │remove│ │    │(active  │
    │        └────┘ └────┘    │ 仍 True) │
    │            │            └─────────┘
    │            ▼
    │        deactivate_chat
    │
    └── /cancel ──→ remove_selection + timeout_task.cancel
                              + finish + deactivate_chat
```

---

## 6. 索引锁变更

### 6.1 当前问题

```python
# 当前：/add 在 handle_add 时获取锁，got_image 的 finally 释放锁
# 等待图片期间（最长 60s）索引被锁定，所有其他命令被阻塞
@add_cmd.handle()
async def handle_add(...):
    await index_manager.acquire_lock()  # ← 持锁太久
    ...

@add_cmd.got("image")
async def got_image(...):
    ...
    finally:
        index_manager.release_lock()    # ← 才释放
```

### 6.2 变更后

```python
class IndexManager:
    def __init__(self, ...):
        self._lock = asyncio.Lock()
        # 独立的 _add_sem，用于限制 add_single_file 的并发 pipeline。
        # 值复用 sync_concurrency（默认 5），避免突发 API 调用触发限流。
        self._add_sem = asyncio.Semaphore(
            sync_concurrency if isinstance(sync_concurrency, int) and sync_concurrency > 0
            else self.DEFAULT_SYNC_CONCURRENCY
        )
        self._is_syncing: bool = False        # 供读操作检查

    async def acquire_lock(self) -> bool:
        """仅供 sync_with_filesystem 调用。"""
        if self._lock.locked():
            return False
        await self._lock.acquire()
        self._is_syncing = True
        return True

    def release_lock(self) -> None:
        """仅供 sync_with_filesystem 调用。"""
        self._is_syncing = False
        if self._lock.locked():
            self._lock.release()

    async def add_single_file(self, filename: str) -> AddResult:
        # 1. pipeline 受 _add_sem 并发限制
        async with self._add_sem:
            text, embedding = await self._process_image_pipeline(filename)
        # 2. add_entry 是纯同步方法，天然原子，无需异步锁
        return self.add_entry(filename, text, embedding)

    @property
    def is_locked(self) -> bool:
        """仅 sync 时拒绝读操作，add 短时写锁不阻塞读。"""
        return self._is_syncing
```

| 操作 | 锁持有范围 | 影响 |
|------|-----------|------|
| `/refresh` / 启动同步 | `acquire_lock()` → `sync_with_filesystem()` → `release_lock()`（插件层管理） | 读操作被 `is_locked` 阻塞 |
| `/add` | 仅在 `add_entry` 时（同步方法，无需异步锁） | 不阻塞读 |
| `/search` / `/ai` | 无锁，仅检查 `is_locked` | sync 时被拒 |

注意：`sync_with_filesystem` 的锁仍由插件层管理（`handle_refresh` 调用 `acquire_lock()`，`finally` 中调用 `release_lock()`），因为 `bot.py` 的后台同步和 `/refresh` 命令共享同一逻辑。`add_single_file` 中不再涉及 `_lock` 操作。`_lock` 与 `_is_syncing` 同时设置/清除，确保一致性。

### 6.3 多个用户并发 /add

`_add_sem` 限制同时进行 OCR + Embedding 的协程数，避免突发 API 调用触发限流。多个 `/add` 可以在 pipeline 阶段并行，最终 `add_entry` 虽然是同步串行执行，但 `add_entry` 本身只涉及内存 dict 操作 + 同步文件写入，速度极快，不会形成瓶颈。

---

## 7. /cancel 实现

### 7.1 命令注册

```python
cancel_cmd = on_command("cancel", rule=to_me(), priority=5, block=True)
```

### 7.2 入口路径

`/cancel` 的处理入口取决于发送频道与当前命令是否同频道：

- **同频道**（如私聊 `/add` 后私聊 `/cancel`）：NoneBot2 优先恢复 paused matcher（`add_cmd` 的 got handler），进入后由 `got_intercept_bypass()` 识别 `/cancel` 并执行取消逻辑
- **异频道**（如群聊中 `/cancel` 私聊的 `/add`）：不存在 paused matcher，由 `cancel_cmd.handle()`（`priority=5`）直接处理

两种路径执行相同的取消逻辑：
1. `current_task.cancel()` — 终止正在执行的异步任务
2. `remove_selection()` + `timeout_task.cancel()` — 清除选择会话
3. `chat.matcher.finish()` — 结束老 matcher（发送通知到原上下文）
4. `deactivate_chat(user_id)` — 重置会话

### 7.3 处理流程

```python
@cancel_cmd.handle()
async def handle_cancel(bot: Bot, event: MessageEvent, matcher: Matcher):
    user_id = event.get_user_id()

    if not is_authorized(user_id):
        log_unauthorized(user_id, "cancel")
        return

    # 绕过会话检查（/cancel 始终可用）
    result = await execute_cancel(user_id)
    if result is None:
        await matcher.finish("当前没有活跃的会话")
    else:
        await matcher.finish(result)
```

---

## 8. /help 绕过机制

### 8.1 当用户不在活跃会话中

`help_cmd` 正常处理，不受影响。

### 8.2 当用户在活跃会话中（有 matcher 处理中）

`/help` 同样会被 paused got handler 先捕获（同一个 matcher 处于 waiting 状态）。
在 got handler 入口调用 `got_intercept_bypass` 识别 `/help` 和 `/cancel`：

```python
# got handler 入口
text = event.get_plaintext().strip()
if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
    return
# ... 正常流程继续
```

对 `/help`：`send` 帮助文本后 `reject` 继续等待原命令。
对 `/cancel`：执行取消逻辑后 `finish`。

所有 got handler（`got_image`、`got_selection` 等）共享此拦截函数。

### 4.6 旧 API 替换对照

`session.py` 重写后移除以下旧 API，各插件需同步替换：

| 旧 API | 替换方案 | 备注 |
|--------|---------|------|
| `pending_sessions.get()` | `get_or_create_chat()` / `get_selection()` | 分聊天和选择两个字典 |
| `register(user_id, matcher, type)` | `activate_chat()` + 可选 `create_selection()` | 注册和激活分离 |
| `cancel(user_id)` | `deactivate_chat()` / `remove_selection()` | 按场景选择 |
| `check_and_cancel(user_id, new_type)` | `activate_chat()` 的返回值判断 | 返回值 False 时直接拒绝 |
| `is_cancelled(user_id)` | `chat.active` 属性 | 直接查询 chat session |
| `cancel_timeout_task(user_id)` | `remove_selection()` + `.cancel()` | 移除选择会话时自动处理 |

---

## 9. 各插件变更汇总

| 文件 | 变更 |
|------|------|
| `bot/session.py` | 完全重写：ChatSession + SelectionSession + `execute_cancel` + `got_intercept_bypass` 等公开 API |
| `bot/engine/index_manager.py` | 缩小锁范围，新增 `_add_sem`，新增 `_is_syncing` |
| `bot/plugins/meme_cancel.py` | 新增，调用 `execute_cancel` |
| `bot/plugins/meme_add.py` | 去掉锁代码，加入会话检查 + got 拦截 |
| `bot/plugins/meme_search.py` | 加入会话检查 + got 拦截 |
| `bot/plugins/_search_utils.py` | 改用新 session API + `selection_id` 传递链 |

`selection_id` 传递链说明：
1. `_search_utils.execute_search()` 生成 `selection_id`（`uuid.uuid4()`）
2. 存入 `cmd_matcher.state["selection_id"]`，供后续 `got_selection` 读取
3. 调用 `create_selection(user_id, selection_id, task)` 注册选择会话
4. 将 `selection_id` 传入 `timeout_session(bot, event, user_id, selection_id, ...)` 供超时校验
5. `got_selection` 入口可选校验 `matcher.state["selection_id"]` 是否匹配，但非必需（NoneBot2 matcher 状态已自带隔离）
| `bot/plugins/meme_plain_text.py` | 加入会话检查 + got 拦截 |
| `bot/plugins/meme_ai.py` | 加入会话检查 |
| `bot/plugins/meme_refresh.py` | 加入会话检查，锁管理改为内部 |
| `bot/plugins/_help_text.py` | 加入 `/cancel` 帮助项 |

---

## 10. 边界情况处理

| 场景 | 预期行为 |
|------|---------|
| 用户先 `/add`，再 `/cancel`（同频道） | got 拦截捕获 `/cancel`，finish 老 matcher |
| 用户私聊 `/add`，群聊 `/cancel` | cancel handler 直接操作 chat session + 远程 finish 老 matcher |
| 用户私聊 `/add`，群聊 `/help` | help_cmd 绕过会话检查正常显示帮助 |
| got 中 `/help` 后继续发图片 | reject 后仍处于 got 状态，下一次正确图片触发正常流程 |
| `/add` 处理中（正在 OCR）时 `/cancel` | `current_task.cancel()` 中断 OCR -> CancelledError 被 handler 捕获 -> 清理跳到 finally |
| 搜索多结果时 `/cancel` | `remove_selection()` + `timeout_task.cancel()` + `chat.matcher.finish()` |
| 搜索多结果超时后用户发编号 | timeout 已 remove_selection，handler 检查到无选择会话->"选择已过期" |
| 多个用户同时 `/add` | `_add_sem` 限制并发 pipeline，add_entry 天然串行 |
| `/refresh` 中发起 `/add` | `is_locked` 拒绝，插件提示"索引正在更新" |
| `/cancel` 时用户无活跃会话 | 回复"当前没有活跃的会话" |
| `/help` 在 got 中 + 有命令处理中 | 显示帮助文本 + reject 继续等待，原命令不受影响 |

---

## 11. 未变更或不需变更的模块

- `bot/auth.py` — 不变
- `bot/app_state.py` — 不变
- `bot/config.py` — 不变
- `bot/bot.py` — 不变
- `bot/logging_config.py` — 不变
- 所有 `engine/` 子模块除 `index_manager.py` 外不变
