# /info 增强与 /search 删除设计

> 日期：2026-07-11
> 状态：待用户审阅
> 范围：`/info` 命令增强、`/search` 命令删除

## 1. 背景与目标

### 1.1 /info 增强

当前 `/info` 只返回索引统计、系统内存/CPU。用户希望：

1. 增加**当前进程内存占用**输出。
2. 支持 `/info [id]` 查看指定表情包详情，包含：id、文本、文件名、大小、说话人、标签。

### 1.2 /search 删除

用户希望删除显式的 `/search` 斜杠命令，但保留**普通文本兜底搜索**（即用户私聊发送不以 `/` 开头的文字时，仍按关键词搜索返回表情包）。

## 2. 关键决策（已与用户确认）

| 决策项 | 选择 | 理由 |
|---|---|---|
| 进程内存指标 | psutil 取当前进程 RSS，以 humanize 格式显示 | 项目新增依赖 humanize，读取简单直观 |
| /info 语法 | `/info [id]`，id 可选 | 与现有无参 `/info` 兼容 |
| id 不存在/无效 | 返回总体信息（不回错误） | 用户明确要求「保持现有状态，返回总体信息」 |
| /info <id> 输出字段 | id、文本、文件名、大小、说话人、标签 | 用户原要求四项，后补充增加 speaker 和 tags |
| 大小含义 | 文件在磁盘上的实际大小 | 用户原话「大小」即文件尺寸 |
| 大小格式化 | 自动在 B/KB/MB 间切换 | 避免大文件出现过大数字 |
| 删除范围 | 删除 `/search`、`/s` 注册、帮助文本、插件文件、对应测试 | 彻底移除；兜底普通文本搜索保留 |
| `/info <id>` 一致性 | 持 `IndexRwLock` 读锁查询 | 用户要求刷新期间更强一致；读锁超时后提示稍后再试 |

## 3. 架构与落点

### 3.1 总体架构

```
用户 -> /info [id]
         │
         ▼
   bot/plugins/meme_info.py
         │
         ├── 无 id / 无效 id ──► IndexManager.info() ──► 总体信息
         │
         └── 有效 id ──► IndexManager.get_entry(id) [持读锁]
                            │
                            ▼
                       读取 MEMES_DIR / entry.image_path 大小
                            │
                            ▼
                       组装详情文本
```

### 3.2 文件落点

| 文件 | 改动 |
|---|---|
| `bot/engine/index_manager.py` | 新增 `get_entry(entry_id)` 公共方法 |
| `bot/plugins/meme_info.py` | 解析可选 id；新增进程内存读取；新增详情格式化 |
| `bot/plugins/_help_text.py` | 移除 `/search` 与 `/s` 帮助行 |
| `bot/plugins/meme_search.py` | 删除 |
| `tests/unit/plugins/test_meme_search.py` | 删除 |
| `tests/unit/plugins/test_meme_info.py` | 补充进程内存、id 详情、无效 id 回退等用例 |
| `tests/unit/engine/test_index_manager_info.py` | 补充 `get_entry` 用例 |
| `docs/api/bot/plugins/meme_info.md` | 更新命令说明 |
| `docs/api/bot/plugins/meme_search.md` | 改为「已删除」或删除 |
| `docs/api/API.md` | 更新目录/条目 |

## 4. 组件改动

### 4.1 Engine：`IndexManager.get_entry`

在 `bot/engine/index_manager.py` 中新增：

```python
async def get_entry(self, entry_id: int) -> MemeEntry | None:
    """按 id 查询单条表情包元数据。

    持读锁调用 MetadataStore，保证与刷新期间的写入互斥，读取视图一致。

    Args:
        entry_id: 索引 id。

    Returns:
        对应 MemeEntry；id 不存在时返回 None。

    Raises:
        asyncio.TimeoutError: 等待读锁超时（刷新长时间占用写锁）。
        RuntimeError: MetadataStore 未注入。
    """
    async with self._rwlock.read(timeout=self.read_timeout):
        return await run_sync_with_request_id(self._metadata_store.get_entry, entry_id)
```

说明：

- `MetadataStore.get_entry` 本身受内部锁保护且命中内存缓存，是 O(1) 操作。
- 通过 `IndexManager` 包装一层，与现有 `info()` 等查询入口风格一致，避免插件直接访问私有属性 `_metadata_store`。
- `/info` 总体信息仍保持不持 `IndexRwLock`（PRD 约束），但 **`/info <id>` 详情查询持读锁**，避免刷新期间读到不一致的条目（例如正在删除的 entry 或尚未完全写入的替换条目）。

### 4.2 Plugin：`meme_info.py`

#### 4.2.1 命令注册保持

```python
info_cmd = on_command("info", rule=to_me(), priority=5, block=True)
```

不改动注册，仅让 handler 接收 `CommandArg`。

#### 4.2.2 处理器签名

```python
@info_cmd.handle()
async def handle_info(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    ...
```

#### 4.2.3 进程内存读取

```python
try:
    process = psutil.Process()
    rss_mb = process.memory_info().rss // (1024 * 1024)
    process_mem_text = f"{rss_mb} MB"
except Exception:
    logger.warning("获取进程内存失败", exc_info=True)
    process_mem_text = "获取失败"
```

在总体信息输出中新增一行：

```
进程内存：{process_mem_text}
```

#### 4.2.4 id 解析与分支

```python
raw = args.extract_plain_text().strip()
if raw:
    try:
        entry_id = int(raw.split()[0])
    except ValueError:
        entry_id = None
else:
    entry_id = None
```

- 如果 `entry_id` 为 `None`：返回总体信息。
- 如果 `entry_id` 解析失败：返回总体信息。
- 如果 `entry_id` 有效但 `get_entry` 返回 `None`：返回总体信息。
- 如果有效且存在：返回详情。

#### 4.2.5 详情输出格式

```
id: 42
文本：加班心累
文件名：meme_20260711120000_abc12345.webp
大小：123.45 KiB
说话人：小明
标签：吐槽, 加班
```

格式化规则：

- `大小`：使用 `Path.stat().st_size`；按 B/KB/MB 自动切换，保留两位小数（B 为整数）。
- `说话人`：`entry.speaker` 为 `None` 或空串时显示 `无`。
- `标签`：`entry.tags` 为空时显示 `无`；否则 `", ".join(tags)`。
- 文件不存在时，`大小` 显示 `文件不存在`。

### 4.3 帮助文本

`bot/plugins/_help_text.py` 中移除：

```
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
```

其余行不变。

### 4.4 删除 /search 插件

- 删除 `bot/plugins/meme_search.py`。
- 删除 `tests/unit/plugins/test_meme_search.py`。
- `docs/api/bot/plugins/meme_search.md` 改为「已删除」说明，或从目录中删除；同步更新 `docs/api/API.md`。

## 5. 数据流

### 5.1 /info（无参）

```
用户: /info
        │
        ▼
   handle_info()
        │
        ├── 授权校验
        ├── IndexManager.info() → IndexInfo
        ├── psutil.Process().memory_info().rss
        ├── psutil.virtual_memory()
        ├── psutil.cpu_percent()
        └── 组装文本 → 返回
```

### 5.2 /info <id>

```
用户: /info 42
        │
        ▼
   handle_info()
        │
        ├── 授权校验
        ├── 解析 id = 42
        ├── IndexManager.get_entry(42)
        │       ├── 持 IndexRwLock 读锁
        │       └── MetadataStore.get_entry(42) → MemeEntry
        ├── Path(MEMES_DIR / entry.image_path).stat().st_size
        └── 组装详情文本 → 返回
```

如果刷新正在占用写锁，`get_entry` 等待读锁超时后会抛出 `asyncio.TimeoutError`，插件捕获后回复 `索引更新较慢，请稍后再试`。

### 5.3 /info 无效 id

```
用户: /info abc
        │
        ▼
   handle_info()
        │
        ├── 解析 id 失败
        └── 回退到总体信息分支
```

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| `IndexManager` 未初始化 | 回复 `服务未就绪，请稍后再试` |
| `IndexManager.info()` 失败 | 回复 `索引信息获取失败，请稍后再试` |
| `/info <id>` 等待读锁超时 | 回复 `索引更新较慢，请稍后再试` |
| 进程内存读取失败 | 字段显示 `获取失败`，其他字段正常输出 |
| 系统内存/CPU 读取失败 | 保持现有行为：字段显示 `获取失败` |
| id 非数字 | 回退到总体信息 |
| id 不存在 | 回退到总体信息 |
| 文件大小读取失败（如文件被删除） | 详情中 `大小` 显示 `文件不存在`，其他字段正常输出 |

## 7. 测试

### 7.1 Engine 测试

文件：`tests/unit/engine/test_index_manager_info.py`（或新建）

新增用例：

- `test_get_entry_existing`：返回正确 `MemeEntry`。
- `test_get_entry_not_found`：不存在 id 返回 `None`。
- `test_get_entry_waits_for_read_lock`：模拟写锁占用时，`get_entry` 等待读锁；写锁释放后成功返回。

### 7.2 Plugin 测试

文件：`tests/unit/plugins/test_meme_info.py`

新增/调整用例：

- `test_info_overall_includes_process_memory`：断言回复文本包含 `进程内存：` 且为 humanize 格式。
- `test_info_with_valid_id_shows_detail`：mock `get_index_manager` 返回含指定 entry 的 manager；断言输出包含 id、文本、文件名、大小、说话人、标签。
- `test_info_with_invalid_id_falls_back_to_overall`：输入非数字 id，断言返回总体信息（含 `表情包数量：`）。
- `test_info_with_nonexistent_id_falls_back_to_overall`：输入存在解析但 entry 为 `None` 的 id，断言返回总体信息。
- `test_info_detail_missing_file`：entry 存在但对应文件不存在，断言 `大小` 显示 `文件不存在`。
- `test_info_detail_lock_timeout`：mock `get_entry` 抛 `asyncio.TimeoutError`；断言回复 `索引更新较慢，请稍后再试`。

### 7.3 删除的测试

- 删除 `tests/unit/plugins/test_meme_search.py`。
- 检查 `tests/unit/plugins/test_meme_plain_text.py` 与 `tests/unit/plugins/test_search_utils.py`，确认不依赖 `meme_search` 模块；如有引用则同步调整。

## 8. 文档同步

实现后按 `CLAUDE.md` 同步：

- `docs/api/bot/plugins/meme_info.md`
  - 更新命令说明为 `/info [id]`。
  - 补充进程内存字段。
  - 补充 id 详情输出字段与示例。
  - 补充 id 无效/不存在时回退总体信息的行为。
- `docs/api/bot/plugins/meme_search.md`
  - 改为「已删除」说明或删除文件；同步 `docs/api/API.md` 目录。
- `docs/PRD.md`
  - §3.1 `/search` 触发方式与流程：标记为已删除，说明普通文本兜底搜索保留。
  - §3.13 `/info`：更新触发方式、输出示例、字段说明。
  - §5 边界情况：移除 `/search` 相关行或说明由 `/query`/普通文本兜底替代。
- `CONTEXT.md`
  - 更新 `/search` 与 `/info` 条目说明。
- `README.md`
  - 更新命令列表（移除 `/search`，更新 `/info` 说明）。

## 9. 不做的事（YAGNI）

- 不修改 `KeywordSearcher`、`_search_utils`、普通文本兜底搜索。
- 不为 `/info <id>` 发送表情包图片，仅返回文本详情。
- 不添加 `/search` 的隐藏开关或别名保留。
- 不在 `IndexInfo` 中新增字段（进程内存属于应用层观测，不进入 engine 统计）。
- 不把文件大小计算下沉到 engine（文件系统属于插件层展示细节）。

## 10. 提交策略

当前在 `main` 分支，按 `CLAUDE.md` 禁止自行 `git add` / `git commit`。spec 写入后由用户审阅；实现完成、测试与文档同步后，提交由用户审核执行。
