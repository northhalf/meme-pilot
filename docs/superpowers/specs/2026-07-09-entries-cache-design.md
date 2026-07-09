# MetadataStore entries 缓存与 sync 去重设计

> 日期：2026-07-09
> 状态：待实现
> 关联模块：`bot/engine/metadata_store.py`、`bot/engine/index_manager.py`

## 1. 背景与问题

当前 `MetadataStore` 仅维护 `text -> id` 的内存反向索引 `_text_to_id`，全量条目每次都走 SQL。性能上存在三个重复工作点：

1. **`_scan_meme_files` 在一次 sync 内被重复调用**：`_run_sync_internal` 顺序执行 phase0 -> phase1 -> phase2，其中 phase1（`_sync_phase1_delete`，index_manager.py:1261）与 phase2（`_sync_phase2_add`，index_manager.py:1283）各扫一次 `memes/` 目录。同一次 sync 内目录内容不变（写锁独占），第二次扫描是纯重复。

2. **`get_all_entries` 被频繁调用且每次全表 SQL**：生产调用者包括 `keyword_searcher.search`、`random_searcher.search_random`、`semantic_searcher.search_semantic`（每次搜索请求一次），以及 `IndexManager` 内 sync 三阶段 phase0(1195)/phase1(1262)/phase2(1284) 各一次 + `info()`(788)。每次执行两条 SQL（`meme` 全表 + `meme_tag` 全表）并组装全部 `MemeEntry`（含 tags list）。其中 sync 三阶段顺序执行且写锁独占，期间无写入，这 3 次结果完全相同。

3. **`get_entry` 单点查询未利用全量数据**：生产调用者包括读路径 `ai_matcher._build_candidates`（构 Top10 候选）和写路径 `IndexManager` 的 `edit_text`/`set_speaker`/`add_tags`/`delete` 校验、`_execute_*` 写锁内 TOCTOU 复查、`_write_entry` 去重替换取旧 entry、`get_by_filename`。每次都执行单行 `meme` 查询 + `meme_tag` 子查询。若已有全量条目缓存，`get_entry` 可 O(1) 命中。

## 2. 目标与非目标

### 目标

- 在 `MetadataStore` 层引入全量 entries 缓存，`load` 时重建，写操作时增量维护，使 `get_all_entries`/`get_entry` 透明命中缓存。
- `MemeEntry` 改为 frozen dataclass，返回共享引用，实现 `get_entry` 零拷贝 O(1)。
- 消除 `_scan_meme_files` 在一次 sync 内的重复扫描。

### 非目标（YAGNI）

- 不引入 TTL 或主动失效机制：缓存由写操作增量维护，无需过期。
- 不改变 `MetadataStore` 对外公开方法签名（参数与返回类型不变），仅改语义（数据源从 SQL 变为缓存）。
- 不改 `keyword_searcher`/`random_searcher`/`semantic_searcher`/`ai_matcher`：它们透明受益，无需改动。
- 不改 `VectorStore` 或 chroma 相关逻辑。
- `tags` 不改为 `tuple`：保持 `list[str]`，靠约定保证不可变（见 §6.3）。

## 3. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 缓存生命周期 | 持久缓存 + 写时增量维护 | 与现有 `_text_to_id` 模式一致；搜索/AI/sync 全程透明受益；线程安全由现有 `_lock` 保证 |
| 缓存放置层级 | `MetadataStore` 层 | 所有调用方（含 searcher、ai_matcher、IndexManager）透明受益；与 `_text_to_id` 同层同生命周期 |
| 返回语义 | `MemeEntry` 改 `@dataclass(frozen=True)` + 共享引用 | `get_entry` 零拷贝 O(1)；frozen 保证返回后字段不可被调用方修改 |
| `tags` 字段类型 | 保持 `list[str]` | 生产代码已确认无任何对 `MemeEntry` 字段赋值或 tags 可变操作；波及面最小（生产 0 改动）|

## 4. 详细设计

### 4.1 MetadataStore 缓存结构

新增实例属性 `_entries: dict[int, MemeEntry]`，与 `_text_to_id` 同层、同生命周期。

`MemeEntry` 改为 frozen：

```python
@dataclass(frozen=True)
class MemeEntry:
    id: int
    image_path: str
    text: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

`load()` 合并重建 `_entries` 与 `_text_to_id`，消除当前 `_text_to_id` 的单独全表查询：

```python
# 伪代码：在 _lock 内
rows = list(conn.execute("SELECT id, image_path, text, speaker FROM meme ORDER BY id"))
tags_by_id: dict[int, list[str]] = {}
for row in conn.execute("SELECT meme_id, tag FROM meme_tag ORDER BY meme_id, tag"):
    tags_by_id.setdefault(row["meme_id"], []).append(row["tag"])
self._entries = {
    row["id"]: MemeEntry(
        id=row["id"],
        image_path=row["image_path"],
        text=row["text"],
        speaker=row["speaker"],
        tags=tags_by_id.get(row["id"], []),
    )
    for row in rows
}
self._text_to_id = {entry.text: eid for eid, entry in self._entries.items()}
```

### 4.2 读路径（命中缓存）

- `get_all_entries`：返回 `self._entries.copy()`--浅拷贝出新 dict，value 共享 frozen `MemeEntry`。保留快照语义（调用方增删 key 不污染缓存），O(N) 纯内存，省掉 2 条 SQL 与 DB 往返。

```python
def get_all_entries(self) -> dict[int, MemeEntry]:
    with self._lock:
        return self._entries.copy()
```

- `get_entry`：返回 `self._entries.get(entry_id)`--O(1) 共享引用，省掉单行查询 + `meme_tag` 子查询。

```python
def get_entry(self, entry_id: int) -> MemeEntry | None:
    with self._lock:
        return self._entries.get(entry_id)
```

### 4.3 写路径（`_lock` 内增量维护）

所有写方法已在 `with self._lock` 内，维护 `_entries` 时与 `_text_to_id` 同步：

- `add` / `add_with_id`：构造 frozen `MemeEntry`，`self._entries[entry_id] = entry`，同步 `self._text_to_id[text] = entry_id`。
- `update`：用 `dataclasses.replace(...)` 生成新 `MemeEntry` 替换 `self._entries[entry_id]`（frozen 不能就地改字段）；`text` 变更时同步 `_text_to_id`（删旧键加新键）；`tags` 非 None 时整体替换为去重排序后的 list。返回 `False` 时（id 不存在）不动缓存。

**tags 一致性**：构造或替换 `MemeEntry` 时，tags 须用 `sorted(set(tags or []))`，与 SQL 的 `INSERT OR IGNORE`（PRIMARY KEY 去重）+ `ORDER BY tag`（字典序排序）对齐，保证「写入后立即 `get_entry`」与「下次 `load` 重建」返回的 tags 完全一致（顺序与去重）。`set` 去重 + `sorted` 字典序与 sqlite 默认 BINARY 排序在 Unicode 码点层面一致。
- `remove`：`self._entries.pop(entry_id, None)`，同步从 `_text_to_id` 删除对应 text（仅当 `_text_to_id[text] == entry_id` 时）。

`update` 维护示例：

```python
from dataclasses import replace as _replace
# ... 在写库成功、commit 后 ...
old_entry = self._entries.get(entry_id)
if old_entry is not None:
    self._entries[entry_id] = _replace(
        old_entry,
        image_path=image_path if image_path is not _UNSET else old_entry.image_path,
        text=text if text is not _UNSET else old_entry.text,
        speaker=speaker if speaker is not _UNSET else old_entry.speaker,
        tags=sorted(set(tags)) if tags is not None else old_entry.tags,
    )
# _text_to_id 维持现有 old_text/new_text 逻辑不变
```

### 4.4 `_scan_meme_files` 去重

`_run_sync_internal` 开头扫一次，传给 phase1 / phase2：

```python
async def _run_sync_internal(self) -> SyncResult:
    self._memes_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    existing_files = self._scan_meme_files()  # 仅扫一次

    await self._sync_phase0_consistency(failed)
    deleted_count = await self._sync_phase1_delete(existing_files)
    added_count, deduped_count, no_text_count = await self._sync_phase2_add(
        existing_files, failed
    )
    ...
```

phase1 / phase2 签名增加 `existing: set[str]` 参数，移除各自内部的 `self._scan_meme_files()` 调用。phase0 不变（它读 `get_all_entries`，现走缓存）。

语义与现状一致：phase1 判删除、phase2 判新增用的都是 sync 开始时的目录快照；phase2 对 `memes/` 的副作用（移走无文字图、归档去重图）发生在快照之后，不影响正确性。

### 4.5 `entry_count` 读缓存

`entry_count()` 当前走 SQL `SELECT COUNT(*)`，被 `IndexManager.search`/`random_search` 在每次请求时用于判空库。改为读 `len(self._entries)`：O(1) 内存，与缓存长度天然一致，省掉搜索热路径每次的 COUNT SQL。`add`/`remove` 维护 `_entries` 时 `len` 自动正确，无需额外同步。

```python
def entry_count(self) -> int:
    with self._lock:
        return len(self._entries)
```

## 5. 数据流

- **load**：SQL 全量 -> 组装 frozen `MemeEntry` -> 填充 `_entries` + `_text_to_id`。
- **读**：`get_all_entries`/`get_entry` -> 直接返回 `_entries` 中的 frozen 引用（`get_all_entries` 外加一层 dict 浅拷贝）。
- **写**：`add`/`update`/`remove` -> 在 `_lock` 内同步改 `_entries` + `_text_to_id` + SQL。
- **sync**：开头扫一次 `memes/` 传 phase1/phase2；三阶段的 `get_all_entries` 都读缓存，sync 内的 `add`/`update`/`remove` 实时同步缓存，故多次读取结果一致。

## 6. 正确性分析

### 6.1 sync 期间缓存一致性

sync 的 phase1 `remove`、phase2 `add` 与去重 `update` 全部走 `MetadataStore` 写方法，这些方法在 `_lock` 内同步更新 `_entries`。因此 sync 内多次 `get_all_entries` 读到的都是最新缓存状态，且不再重复打 SQL。phase2 内新增的条目会进入缓存，但 phase2 遍历的是 `get_all_entries` 返回的 dict 浅拷贝快照（在 phase2 开始时建立），新增条目不影响当前遍历--与现状语义一致。

### 6.2 线程安全

`_entries` 所有读写均在 `with self._lock` 内。`get_all_entries` 返回 dict 浅拷贝、`get_entry` 返回 frozen 引用后，调用方在锁外使用时：

- `update` 用 `dataclasses.replace` 创建全新 `MemeEntry` 对象替换缓存槽位，旧引用永不被修改 -> 调用方持有的旧引用始终一致。
- `add`/`remove` 改的是缓存 dict 本身，但 `get_all_entries` 返回的是浅拷贝 dict，调用方持有的 dict 不受影响。

frozen 是此安全性的关键：它保证返回后的对象不可被任何方修改字段。

### 6.3 aliasing

`MemeEntry` frozen 保证 `image_path`/`text`/`speaker` 不可赋值。`tags` 字段虽是 `list`（frozen 不冻结 list 内部），但全代码已确认无 `entry.tags.append()`、`entry.tags = ...` 之类的可变操作（rg 验证：生产代码 0 处；测试中对 `entry.xxx` 的赋值均针对 `MagicMock` 对象而非真实 `MemeEntry`，不受 frozen 影响）。靠「约定不修改 `get_entry`/`get_all_entries` 返回对象的 tags」保证。若未来出现修改 tags 的需求，须改为返回 `list(entry.tags)` 副本或将 `tags` 改 `tuple`。

## 7. 波及面

### 7.1 生产代码

| 文件 | 改动 |
|------|------|
| `bot/engine/metadata_store.py` | `MemeEntry` 加 `frozen=True`；新增 `_entries`；`load`/`get_all_entries`/`get_entry`/`entry_count`/`add`/`add_with_id`/`update`/`remove` 改造。`get_by_filename` 保持原样（生产无调用者，内部复用 `get_entry` 读缓存）；`get_id_by_text`/`find_next_id`/`get_all_text` 不变 |
| `bot/engine/index_manager.py` | `_run_sync_internal` 扫一次 `_scan_meme_files` 传参；`_sync_phase1_delete`/`_sync_phase2_add` 签名加 `existing: set[str]` |
| `keyword_searcher` / `random_searcher` / `semantic_searcher` / `ai_matcher` | **0 改动**，透明受益 |

### 7.2 测试代码

经核实，四个插件测试（`test_meme_setspeaker`/`test_meme_addtag`/`test_meme_delete`/`test_meme_edit`）的 `_make_entry` 均用 `MagicMock()` 构造 entry，其 `entry.image_path = ...` 等赋值是对 MagicMock 赋值，**frozen 不影响 MagicMock**，无需改造。`test_metadata_store.py`/`test_index_manager.py` 中的 `MemeEntry(...)` 构造点为构造时传参（非字段赋值），frozen 不影响构造。故 frozen 改动对现有测试**零破坏**，仅需新增缓存行为测试。

## 8. 测试策略

### 8.1 新增 MetadataStore 缓存测试

- `load` 后 `get_all_entries`/`get_entry` 命中缓存（返回数据与 SQL 一致）。
- `add`/`update`/`remove` 后 `_entries` 同步更新：新增可被 `get_entry` 读到；`update` 后字段变更生效；`remove` 后 `get_entry` 返回 `None`。
- `get_entry` 返回的对象与缓存内是同一引用（`is` 判定）。
- 多次 `get_all_entries` 不重复打 SQL：mock `conn.execute`，调用 N 次仅触发 0 次 SQL（缓存命中）。
- `get_all_entries` 返回的 dict 与缓存 dict 不是同一对象（浅拷贝），但 value 是同一 frozen 引用。
- `entry_count` 等于 `len(_entries)`，且 `add`/`remove` 后同步变化（不触发 COUNT SQL）。

### 8.2 现有测试适配

- 上述 4 个插件测试文件的 mock 构造方式从字段赋值改为 `replace`/构造传参。
- 现有 `test_metadata_store.py` / `test_index_manager.py` 等应大部分通过（对外语义不变）。

## 9. 文档同步

- `docs/api/bot/engine/metadata_store.md`：`get_all_entries`/`get_entry` 语义改为「读缓存返回共享 frozen 引用」；`MemeEntry` 标注 `@dataclass(frozen=True)`；补充 `_entries` 缓存与写时维护说明。
- `CONTEXT.md` / `docs/PRD.md`：无术语与用户可见行为变化，不动。

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `tags` 为 list 可能被未来代码修改导致缓存污染 | 约定 + 代码审查；`get_entry`/`get_all_entries` docstring 注明「返回对象不可修改」；未来需要时改 `tuple` |
| `update` 用 `replace` 维护缓存遗漏字段导致缓存与 SQL 不一致 | 复用现有 `update` 的 `_UNSET` 哨兵逻辑，统一在 commit 后用 `replace` 重建；单元测试覆盖每个字段变更路径 |
| 缓存 tags 与 SQL 实际存储不一致（重复/顺序） | 维护缓存时统一用 `sorted(set(tags or []))`，与 `INSERT OR IGNORE` + `ORDER BY tag` 对齐；测试覆盖「add 后立即 `get_entry` 的 tags == `load` 后 `get_entry` 的 tags」 |
