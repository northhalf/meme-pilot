# 设计文档：新增 /addtag、/del、/info 命令

> 日期：2026-07-06
> 状态：待实现
> 关联需求：
> 1. `/addtag`：按图片 id 添加多个标签
> 2. `/del`：按图片 id 删除一个或多个表情包
> 3. `/info`：显示机器人统计与状态信息

---

## 1. 目标

- 新增三个用户命令：`/addtag`、`/del`、`/info`。
- `/addtag` 与 `/del` 作为索引写入操作，复用现有 `IndexManager` 写入管道（Write Worker + 读写锁），保证与 `/refresh`、`/add`、`/edittext`、`/setspeaker` 的并发安全。
- `/info` 为只读查询，支持私聊与群聊 @bot 触发。
- 所有新增命令遵循现有权限白名单与命令分组约定。

---

## 2. 命令接口与权限分组

### 2.1 新增命令

| 命令 | 语法 | 权限组 | 说明 |
|------|------|--------|------|
| `/addtag` | `/addtag <entry_id> <tag>...` | A（仅私聊） | 单 id，可追加多个 tag |
| `/del` | `/del <entry_id>...` | A（仅私聊） | 一个或多个 id，摘要确认后删除 |
| `/info` | `/info` | B（私聊 + 群聊 @bot） | 只读统计信息 |

### 2.2 权限行为

- `/addtag`、`/del`：
  - 非授权用户私聊/群聊 @bot 静默忽略，仅记录日志。
  - 授权用户在群聊 @bot 调用时回复"此命令仅限私聊使用"。
- `/info`：
  - 非授权用户私聊/群聊 @bot 静默忽略，仅记录日志。
  - 授权用户在群聊 @bot 调用时正常返回。

### 2.3 帮助文本更新

`bot/plugins/_help_text.py` 在 `HELP_TEXT` 中增加：

```text
/addtag <id> <tag>...：为指定表情包添加标签
/del <id>...：删除指定表情包（需确认）
/info：查看机器人状态与统计信息
```

---

## 3. `IndexManager` API 扩展

### 3.1 新增结果类型

```python
@dataclass
class AddTagResult:
    """add_tags() 的返回结果。"""

    entry_id: int
    added_tags: list[str]  # 实际新增的标签（去重后）
    all_tags: list[str]    # 添加后的完整标签列表


@dataclass
class DeleteResult:
    """delete() 的返回结果。"""

    deleted_ids: list[int]                      # 成功删除的 id
    not_found_ids: list[int]                    # 不存在的 id
    failed_ids: list[tuple[int, str]]           # 删除失败的 (id, reason)


@dataclass
class IndexInfo:
    """IndexManager 返回的内部统计信息（不含硬件）。"""

    entry_count: int
    speaker_ranking: list[tuple[str | None, int]]  # 前 3
    status: str  # "空闲" / "正在处理命令" / "正在刷新索引"
```

### 3.2 新增公共方法

```python
async def add_tags(self, entry_id: int, tags: list[str]) -> AddTagResult
async def delete(self, entry_ids: list[int]) -> DeleteResult
async def info(self) -> IndexInfo
```

`IndexManager` 的 `__init__` 增加 `deleted_dir: str | None = None` 参数，默认取 `memes/` 同级的 `memes_deleted/`。

### 3.3 写入管道扩展

- `WriteOp` 枚举增加 `ADD_TAG`、`DELETE`。
- `_WriteRequest` dataclass 增加 `tags: list[str] | None` 与 `entry_ids: list[int] | None` 字段。
- Write Worker 的 `_write_worker_loop()` 增加对 `ADD_TAG`、`DELETE` 的分发。
- 新增 `_execute_add_tags(req)` 与 `_execute_delete(req)` 私有方法。

### 3.4 写入流程

`add_tags` 与 `delete` 和 `edit_text` / `set_speaker` 一致：

1. 检查 `self._shutting_down`，为真则抛 `IndexAddCancelledError`。
2. 检查 `self._refresh_active`，为真则抛 `RefreshInProgressError`。
3. 调用 `self._ensure_write_worker()` 启动 Write Worker。
4. 入队 `_WriteRequest`，等待 future 结果。

---

## 4. `MetadataStore` 变更

无需新增方法。`/addtag` 通过现有 `update(entry_id, tags=...)` 整体替换标签列表，`IndexManager` 在 Write Worker 内完成标签合并与 diff 计算。

`remove()` 已满足 `/del` 需求：`delete()` 批量删除时逐条调用 `MetadataStore.remove(entry_id)`，已处理 `ON DELETE CASCADE` 与 `_text_to_id` 维护。

---

## 5. 写入执行细节

### 5.1 `_execute_add_tags(req)`

在写锁内执行：

1. 重新检查 entry 是否存在（TOCTOU），不存在则抛 `ValueError`。
2. 读取 `entry.tags` 作为 `current_tags`。
3. 对 `req.tags` 去重，计算：
   - `added_tags = set(req.tags) - set(current_tags)`
   - `merged_tags = set(current_tags) | set(req.tags)`
4. 若 `added_tags` 为空，直接返回 `AddTagResult(entry_id, [], current_tags)`。
5. 调用 `self._metadata_store.update(entry_id, tags=list(merged_tags))`。
6. 返回 `AddTagResult(entry_id, list(added_tags), list(merged_tags))`。

无需操作 chroma（标签不进入向量库）。

### 5.2 `_execute_delete(req)`

在写锁内执行：

1. 初始化 `deleted_ids`、`not_found_ids`、`failed_ids`。
2. 确保 `memes_deleted/` 目录存在（`mkdir(parents=True, exist_ok=True)`）。
3. 对每个 `entry_id`：
   - 查询 `get_entry(entry_id)`，不存在则加入 `not_found_ids`。
   - 存在则：
     - 先 sqlite `remove(entry_id)`。
     - 再 chroma `remove(entry_id)`。
     - 将该条目对应的 `memes/` 下图片文件移动到 `memes_deleted/`（使用唯一文件名，若冲突则追加 `_<n>` 序号）。
     - 任意步骤失败则记录日志，id 加入 `failed_ids`，继续处理下一个 id。
4. 返回 `DeleteResult`。

**注意**：sqlite 与 chroma 删除先行。若文件移动失败，图片仍留在 `memes/`，但索引已删除；后续 `/refresh` 会将其作为新图重新处理（自愈合）。`memes_deleted/` 中的文件可手动恢复。

---

## 6. 插件交互流程

### 6.1 `/addtag <id> <tag>...`

1. 授权校验 → 仅限私聊 → 会话检查（`session_manager.activate_chat`）。
2. 解析参数：第一个 token 为 `entry_id`，其余为 `tags`。
3. 校验 `entry_id` 存在，读取当前 entry 的 `text` 与 `tags`。
4. 对传入 tags 去重，计算实际会新增的标签。
5. 发送确认消息（不发送图片）：
   ```text
   id：42
   OCR 文本：加班心累时的表情包
   当前标签：吐槽、加班
   将新增标签：心累、躺平
   回复「确认」执行添加，回复其他内容取消。
   ```
   当前标签为空时显示为"无"。
6. 用户回复「确认」后，调用 `IndexManager.add_tags(entry_id, tags)`。
7. 成功回复：
   ```text
   标签已添加 ✅
   id：42
   新增标签：心累、躺平
   当前标签：吐槽、加班、心累、躺平
   ```
8. 异常处理：
   - `ValueError` → "未找到 id 为 {id} 的表情包"
   - `RefreshInProgressError` → "索引正在刷新，请稍后再试"
   - `IndexAddCancelledError` → "服务正在关闭，请稍后再试"
   - 所有 tag 已存在 → "这些标签已存在，无需添加"

### 6.2 `/del <id>...`

1. 授权校验 → 仅限私聊 → 会话检查。
2. 解析参数：所有 token 转为 int，去重。
3. 查询每个 id 的 entry：
   - 存在：记录 `id, OCR 文本摘要`（OCR 文本超过 30 字时截断并标注）。
   - 不存在：记录到 `not_found_ids`。
4. 如果所有 id 都不存在，回复"未找到任何表情包"并结束。
5. 发送摘要确认消息（不发送图片）：
   ```text
   确认删除以下表情包？回复「确认」执行删除，回复其他内容取消。
   42, 加班心累时的表情包...
   43, 当你的老板说今天要加班...
   ```
6. 用户回复「确认」后，调用 `IndexManager.delete(entry_ids)`。
7. 成功回复：
   ```text
   已删除表情包 ✅
   成功：42、43
   未找到：44
   ```
   如果有失败 id，追加：
   ```text
   失败：45（原因）
   ```
8. 异常处理：
   - `RefreshInProgressError` → "索引正在刷新，请稍后再试"
   - `IndexAddCancelledError` → "服务正在关闭，请稍后再试"
   - 用户回复非"确认" → "已取消删除"

### 6.3 `/info`

1. 授权校验（私聊或群聊 @bot 均可）。
2. 调用 `IndexManager.info()` 获取 `IndexInfo`。
3. 使用 `psutil` 读取内存与 CPU：
   - `psutil.virtual_memory()` → used / total / percent。
   - `await asyncio.to_thread(psutil.cpu_percent, interval=0.1)` → CPU 占用百分比。
   - 不采用 `interval=None` 模式，因为两次调用间隔可能很长，平均值失去参考价值；每次 `/info` 实时采样 0.1 秒更准确。
4. 组装并回复：
   ```text
   表情包数量：128
   排行（前 3）：
     1. 小明 45
     2. 无 38
     3. 老板 21
   当前机器人状态：空闲
   内存占用：512 MB / 2048 MB (25.0%)
   CPU占用：12.5%
   ```

---

## 7. 状态判定逻辑

在 `IndexManager.info()` 中：

```python
if self._refresh_active:
    status = "正在刷新索引"
elif session_manager.has_active_session():
    status = "正在处理命令"
else:
    status = "空闲"
```

需要在 `bot/session.py` 的 `SessionManager` 中新增方法：

```python
def has_active_session(self) -> bool:
    """是否存在非空闲的聊天会话。"""
    return any(
        session.active for session in self._chat_sessions.values()
    )
```

---

## 8. 依赖与文档更新

### 8.1 新增依赖

```bash
uv add psutil
```

`psutil` 用于 `/info` 读取内存/CPU 占用。需要同步更新：

- `pyproject.toml`
- `README.md` 依赖列表
- `bot/Dockerfile`：`uv sync` 会自动安装 `psutil`；`psutil` 含 C 扩展，builder 阶段已安装 `g++`，通常可正常编译。若构建失败，需补充 `python3-dev` 或 `build-essential`。

### 8.2 新增/修改文件

| 文件 | 变更 |
|------|------|
| `bot/engine/index_manager.py` | 新增结果类型、`WriteOp` 扩展、`add_tags()`、`delete()`、`info()`、Write Worker 处理逻辑、新增 `deleted_dir` 参数 |
| `bot/config.py` | 新增 `MEMES_DELETED_DIR` 路径常量 |
| `bot/plugins/meme_addtag.py` | 新文件，注册 `/addtag` 命令 |
| `bot/plugins/meme_delete.py` | 新文件，注册 `/del` 命令 |
| `bot/plugins/meme_info.py` | 新文件，注册 `/info` 命令 |
| `bot/plugins/_help_text.py` | 增加三条命令帮助文本 |
| `bot/session.py` | 新增 `has_active_session()` 方法 |
| `docker-compose.yml` | 新增 `./memes_deleted:/app/memes_deleted` 卷挂载 |
| `docs/api/bot/engine/index_manager.md` | 补充新 API |
| `docs/api/bot/plugins/meme_addtag.md` | 新文档 |
| `docs/api/bot/plugins/meme_delete.md` | 新文档 |
| `docs/api/bot/plugins/meme_info.md` | 新文档 |
| `README.md` | 更新命令列表、依赖、`memes_deleted/` 说明 |
| `pyproject.toml` | 新增 `psutil` |

### 8.3 测试计划

- `tests/unit/engine/test_index_manager.py`：补充 `add_tags`、`delete`、`info` 单元测试（mock store/vector store）。
- `tests/unit/plugins/`：补充三个命令插件的解析与回复测试。

---

## 9. 错误处理与边界情况

### 9.1 `/addtag`

| 场景 | 处理 |
|------|------|
| `entry_id` 不存在 | 回复"未找到 id 为 {id} 的表情包" |
| 没有 tag 参数 | 回复用法：`/addtag <id> <tag>...` |
| 所有要添加的 tag 已存在 | 回复"这些标签已存在，无需添加" |
| 索引刷新期间 | 回复"索引正在刷新，请稍后再试" |
| Bot 关闭期间 | 回复"服务正在关闭，请稍后再试" |
| 用户回复非"确认" | 回复"已取消" |

### 9.2 `/del`

| 场景 | 处理 |
|------|------|
| 所有 id 都不存在 | 回复"未找到任何表情包" |
| 部分 id 不存在 | 摘要中标注，确认时只删除存在的 id |
| 删除文件移动失败 | 记录 warning，id 计入 `failed_ids`；图片仍留在 `memes/`，后续 `/refresh` 可自愈合 |
| 索引刷新期间 | 回复"索引正在刷新，请稍后再试" |
| Bot 关闭期间 | 回复"服务正在关闭，请稍后再试" |
| 用户回复非"确认" | 回复"已取消删除" |

### 9.3 `/info`

| 场景 | 处理 |
|------|------|
| `psutil` 读取失败 | 显示"获取硬件信息失败"，不影响内部统计 |

---

## 10. 风险与后续可扩展

- **删除文件可恢复**：`/del` 将图片移动到 `memes_deleted/` 而非永久删除，用户可手动从该目录恢复。
- **批量删除部分失败**：需要清晰告知用户哪些 id 成功、哪些失败。
- **tag 去重规则**：当前按字符串完全匹配去重，大小写敏感。后续可扩展为大小写不敏感或同义词合并。
