# `/setspeaker` 命令 — 设计规格

> 日期：2026-07-03
> 状态：已批准

## 概述

为 MemePilot 添加 `/setspeaker` 命令，允许授权用户在私聊中设置或清空表情包的 `speaker`（说话人）字段。该字段在 sqlite `meme` 表中已预留（`speaker TEXT`），v1.0 至今未填充。

## 命令格式

```
/setspeaker <entry_id> [说话人]
```

- `<entry_id>` — 必填，数字，表情包的索引 id
- `[说话人]` — 选填；提供时设置为该值（去除首尾空白），不提供时清空为 `None`
- 示例：
  - `/setspeaker 3 张三` — 设置 id=3 的 speaker 为「张三」
  - `/setspeaker 5` — 清空 id=5 的 speaker

## 交互流程

```
用户: /setspeaker 3 张三
Bot: (发送图片)
     当前说话人：(无)
     新说话人：张三
     回复「确认」或「yes」确认修改，回复其他内容取消
用户: 确认
Bot: 说话人已设置 ✅
```

清空场景：
```
用户: /setspeaker 3
Bot: (发送图片)
     当前说话人：张三
     新说话人：无
     回复「确认」或「yes」确认修改，回复其他内容取消
用户: 确认
Bot: 说话人已设置 ✅
```

全过程完全参照 `/edittext` 的确认模式。

## 权限

- 组 A（仅私聊），授权用户使用
- 授权用户在群聊中 @bot 调用时回复“此命令仅限私聊使用”

## 架构变更

### 1. WriteOp 枚举 — `bot/engine/index_manager.py`

```python
class WriteOp(Enum):
    ADD = auto()
    EDIT_TEXT = auto()
    SET_SPEAKER = auto()   # 新增
```

### 2. _WriteRequest — `bot/engine/index_manager.py`

新增 `speaker` 字段（仅 SET_SPEAKER 操作使用）：

```python
@dataclass
class _WriteRequest:
    op: WriteOp
    future: "asyncio.Future[...]"
    entry_id: int = 0
    filename: str = ""
    text: str = ""
    speaker: str | None = None      # 新增
    embedding: list[float] | None = None
    old_text: str = ""
```

### 3. SetSpeakerResult — `bot/engine/index_manager.py`

新增 dataclass：

```python
@dataclass
class SetSpeakerResult:
    entry_id: int
    old_speaker: str | None
    new_speaker: str | None
```

### 4. IndexManager.set_speaker() — 公有方法

```python
async def set_speaker(self, entry_id: int, speaker: str | None) -> SetSpeakerResult
```

流程：

1. 检查 `_shutting_down` → 抛 `IndexAddCancelledError`
2. 检查 `_refresh_active / _refresh_pending` → 抛 `RefreshInProgressError`
3. `_ensure_write_worker()`
4. 读 entry 检验存在 + 获取 `old_speaker`
   - entry 不存在 → 抛 `ValueError`
   - `old_speaker == speaker`（无变更）→ 直接返回 `SetSpeakerResult(...)`，不进队列
5. Create future → build `_WriteRequest(op=SET_SPEAKER, speaker=speaker)` → put queue → await future

**与 edit_text 的关键区别**：不调用 `embedding_provider.embed()`，因为 speaker 不写入 chroma，不需要生成新 embedding。因此也没有 embed 锁外→二次检查 TOCTOU 的阶段。

### 5. _write_worker_loop — 新增分支

```python
elif req.op is WriteOp.SET_SPEAKER:
    result = await self._execute_set_speaker(req)
```

### 6. _execute_set_speaker() — 写锁内执行

```python
async def _execute_set_speaker(self, req: _WriteRequest) -> SetSpeakerResult
```

写锁内：

1. TOCTOU 防护：重新检查 `get_entry(req.entry_id)` 是否仍存在
   - 不存在 → 抛 `ValueError`（极少发生：关闭/refresh 期间被其他写操作删除）
2. `MetadataStore.update(entry_id, speaker=req.speaker)`
   - speaker 没有 UNIQUE 约束，不会抛 `DuplicateEntryError`
   - 无 chroma 操作，无需回滚
3. 返回 `SetSpeakerResult(entry_id, old_speaker, new_speaker)`

### 7. 插件层 — `bot/plugins/meme_setspeaker.py`

新建文件，完全参照 `meme_edit.py` 的结构：

```
注册: on_command("setspeaker", rule=to_me(), priority=5, block=True)

handle_setspeaker():
├── 授权校验 → 私聊检查 → 会话激活
├── 参数解析
│   ├── raw.removeprefix("/setspeaker").removeprefix("setspeaker").strip()
│   ├── parts = text_part.split(maxsplit=1)
│   ├── entry_id = int(parts[0])  # 抛 ValueError → "entry_id 必须为数字"
│   └── speaker = parts[1].strip() if len > 1 else None  # None 表示清空
├── get_metadata_store().get_entry(entry_id) 验证存在
├── 发送图片（MEMES_DIR / entry.image_path）
├── 发送确认消息
│   └── 显示 old_speaker（或无）和 new_speaker（或无）
├── matcher.state["entry_id"] / ["speaker"] / ["old_speaker"] 存入
└── 注册超时（timeout_session）

got("confirm"):
├── 旁路 /help（got_intercept_bypass）、/cancel
├── text in ("确认", "yes", "y")
│   ├── asyncio.wait_for(index_manager.set_speaker(), timeout=add_user_timeout)
│   ├── 异常处理（同 edit_text 模式，不包含 DuplicateTextError / EmbeddingError）
│   └── 成功 → deactivate → "说话人已设置 ✅"
│         [旧：{old_speaker or "无"}]
│         [新：{new_speaker or "无"}]
└── else
    └── deactivate → "已取消"
```

## 异常/错误汇总

| 场景 | 异常 | 用户消息 |
|------|------|----------|
| Bot 关闭中 | `IndexAddCancelledError` | 服务正在关闭，请稍后再试 |
| 索引刷新中 | `RefreshInProgressError` | 索引正在刷新，请稍后再试 |
| entry_id 不存在 | `ValueError`（参数解析前/写锁内） | 未找到 id 为 {entry_id} 的表情包 |
| entry_id 非数字 | `ValueError`（参数解析） | entry_id 必须为数字 |
| 调用超时 | `asyncio.TimeoutError` | 修改处理超时，请稍后再试 |
| 写锁内 entry 被删 | `ValueError` | 未找到 id 为 {entry_id} 的表情包 |

**不适用** edit_text 特有的异常：`DuplicateTextError`（speaker 无 UNIQUE 约束）、`EmbeddingError`（不涉及 embedding/chroma）。

## 影响范围

### 需新增

| 文件 | 说明 |
|------|------|
| `bot/plugins/meme_setspeaker.py` | 新建插件文件 |

### 需修改

| 文件 | 变更 |
|------|------|
| `bot/engine/index_manager.py` | `SetSpeakerResult` dataclass、`WriteOp.SET_SPEAKER`、`_WriteRequest.speaker`、`set_speaker()`、`_execute_set_speaker()`、`_write_worker_loop` 新增分支 |
| `bot/plugins/_help_text.py` | 新增 `/setspeaker <id> [说话人]：设置表情包的说话人` |
| `docs/api/API.md` | 新增 `meme_setspeaker.md` 索引 |
| `docs/api/bot/plugins/meme_setspeaker.md` | 新建 API 文档 |
| `CONTEXT.md` | 更新 speaker 字段状态（v1.0 已实现填充） |
| `README.md` | 新增命令说明 |

### 不涉及

- `MetadataStore`（`update()` 已有 speaker 参数）
- `VectorStore`（speaker 不写入 chroma）
- `protocols.py`
- `bot/bot.py`（NoneBot2 自动发现插件，无需注册）
- `bot/__init__.py` / `plugins/__init__.py`

## 边界情况

| 场景 | 行为 |
|------|------|
| speaker 无变化 | 直接返回成功，不进队列，不走写锁 |
| speaker 含特殊字符 | 直接存入 sqlite，无校验（`speaker TEXT` 列） |
| speaker 为纯空白 | 等同清空（`strip()` 后为空 → None） |
| speaker 超长 | 无硬限制，sqlite TEXT 可存 |
| /cancel 等待确认时 | 进入 got_intercept_bypass → 取消，清理会话 |
| /help 等待确认时 | 旁路发送帮助文本，继续等待 |

## 测试

### IndexManager 单元测试（`tests/unit/engine/test_index_manager.py`）

| 用例 | 预期 |
|------|------|
| `set_speaker(3, "张三")` 正常设置 | `SetSpeakerResult` 包含旧的 `None` 和新的 `"张三"` |
| `set_speaker(5, None)` 清空 | `SetSpeakerResult(entry_id=5, old_speaker=..., new_speaker=None)` |
| `set_speaker(3, "张三")` 两次调用（无变更）| 不进队列，直接返回 `SetSpeakerResult` |
| `set_speaker(999, "张三")` id 不存在 | 抛 `ValueError` |
| shutting_down 时调用 | 抛 `IndexAddCancelledError` |
| refresh_active 时调用 | 抛 `RefreshInProgressError` |
| `_execute_set_speaker` 内 entry 被并发删除 | 抛 `ValueError` |

### 插件单元测试（`tests/unit/plugins/test_meme_setspeaker.py`）

| 用例 | 预期 |
|------|------|
| `/setspeaker 3 张三` 参数解析 | entry_id=3, speaker="张三" |
| `/setspeaker 5` 参数解析（清空） | entry_id=5, speaker=None |
| `/setspeaker abc 张三` 非数字 id | 回复"entry_id 必须为数字" |
| `/setspeaker` 无参数 | 回复用法提示 |
| 非授权用户调用 | 静默忽略 |
| 群聊中 @bot 调用 | 回复"此命令仅限私聊使用" |
| 回复「确认」确认修改 | 调用 `set_speaker`，回复含"说话人已设置" |
| 回复「yes」确认修改 | 同上 |
| 回复其他内容 | 回复"已取消" |
| 等待确认时 `/cancel` | 旁路取消，清理会话 |

### 涉及不测试

- `MetadataStore.update(..., speaker=...)` 现有测试已覆盖
- `VectorStore` / embedding / chroma 相关不涉及

## 依赖

无新增依赖。
