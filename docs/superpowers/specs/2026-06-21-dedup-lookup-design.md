# 去重键反向索引 — 设计文档

> 日期：2026-06-21
> 状态：设计完成，待实现
> 关联模块：`bot/engine/index_manager.py`

## 1. 概述

`IndexManager._find_entry_by_dedup_key` 当前对 `self._entries` 做线性扫描，按 `dedup_key(text) == key` 查找首个匹配条目 ID，复杂度 O(n)。本设计在 `IndexManager` 实例上维护一份常驻的 `dedup_key → entry_id` 反向哈希索引，将该查找降为 O(1)，并保证索引与 `_entries` 在所有写入路径上保持一致。

### 1.1 优化范围

线性查找的实际触发路径：

| 调用点 | 是否经过 `_find_entry_by_dedup_key` |
|--------|------------------------------------|
| `add_entry`（用户单张 `/add`） | **是** — 每次新增前都要对当前全量 `_entries` 做一次 O(n) 扫描判断是否去重覆盖 |
| `sync_with_filesystem` 新增阶段（启动同步 / `/refresh`） | **否** — 该阶段已用局部 `winner_keys: set[str]` 集合做 O(1) 去重（`index_manager.py:1002`），不调用本方法 |

因此本次优化**仅针对 `add_entry` 路径**。`sync_with_filesystem` 的 `winner_keys` 是「已有条目 + 本轮新图」的临时赢家集合，语义与「已有条目的常驻反向索引」不同，保持现状不合并，避免引入额外复杂度。

### 1.2 不变式

在整个 `IndexManager` 对象生命周期内成立：

> 对任意 `entry_id ∈ self._entries`，令 `key = dedup_key(self._entries[entry_id]["text"])`，若 `key != ""`，则 `self._dedup_index[key] == entry_id`；反之 `self._dedup_index` 中每个 value 都仍是 `self._entries` 的合法 key。

### 1.3 信任的去重键唯一不变式

`add_entry` 与 `_sync_additions` 在新增阶段已保证不引入重复去重键（命中即走覆盖/删除分支，不新增）。因此 `_dedup_index` 仅作加速查找用，维护方法**不做「同一 key 指向不同 entry_id」的防御性检查**，`_dedup_index_add` 直接赋值（dict 赋值天然幂等）。若未来该唯一不变式被破坏，应由破坏点的逻辑修复，而非在此处加断言掩盖。

## 2. 数据结构

新增实例属性：

```python
self._dedup_index: dict[str, str] = {}
```

- key：`dedup_key(text)`，即去除所有空白字符后的 OCR 文本。
- value：对应的 `entry_id`。
- 在 `__init__` 中初始化为空 dict。

**空 key 处理**：`dedup_key(text) == ""` 即无文字图片，这类条目不会进入 `_entries`（`add_entry` 分支 1 与 `_sync_additions` 分支 1 会移图至 `meme_no_text/` 且不入索引）。因此 `_dedup_index` 中不会出现空字符串键，维护方法对空 key 跳过即可。

**内存开销**：额外一个 `dict[str, str]`，条目数与 `_entries` 相同（每条一个 str key + str value），量级与现有 `_entries` 持平，可忽略。

## 3. 维护方法

新增三个私有方法，集中维护反向索引，放在 `_find_entry_by_dedup_key` 附近。

```python
def _rebuild_dedup_index(self) -> None:
    """根据当前 _entries 全量重建去重键反向索引。"""
    self._dedup_index = {
        dedup_key(entry.get("text", "")): entry_id
        for entry_id, entry in self._entries.items()
    }

def _dedup_index_add(self, entry_id: str) -> None:
    """将单条条目的去重键加入反向索引（空键跳过）。"""
    key = dedup_key(self._entries[entry_id].get("text", ""))
    if key:
        self._dedup_index[key] = entry_id

def _dedup_index_remove(self, entry_id: str) -> None:
    """从反向索引移除单条条目的去重键（空键跳过）。"""
    key = dedup_key(self._entries[entry_id].get("text", ""))
    if key:
        self._dedup_index.pop(key, None)
```

### 3.1 时序约束（实现最易错点）

- `_dedup_index_remove(entry_id)` 必须在 `del self._entries[entry_id]` **之前**调用 — 它要读 `self._entries[entry_id]["text"]` 来计算 key。
- `_dedup_index_add(entry_id)` 必须在 `self._entries[entry_id] = {...}` **之后**调用 — 同理。
- 去重覆盖分支（`add_entry` 的 "replaced"）需「先 remove 旧 key、覆盖、再 add 新 key」三步，因为新 text 的 `dedup_key` 可能与旧 text 不同。

## 4. 写点接入表

所有修改 `self._entries` 的入口共 5 处，逐一接入维护方法。

| 位置 | 当前代码 | 改动 |
|------|---------|------|
| `_load_index`（从磁盘加载后，`:355`） | `self._entries = entries` | 末尾追加 `self._rebuild_dedup_index()` |
| `_load_index`（空索引分支，`:327`） | `self._entries = {}` | 末尾追加 `self._rebuild_dedup_index()`（产出空 dict，保持两分支对称） |
| `add_entry` 正常新增（`:636`） | `self._entries[entry_id] = {...}` 之后 `save_index()` | 写入后追加 `self._dedup_index_add(entry_id)` |
| `add_entry` 去重覆盖（`:610`） | `self._entries[old_id] = {...}`（新 text 覆盖旧） | 覆盖**前** `self._dedup_index_remove(old_id)`，覆盖**后** `self._dedup_index_add(old_id)` |
| `remove_entry`（`:667`） | `del self._entries[entry_id]` | 删除**前** `self._dedup_index_remove(entry_id)` |
| `_sync_deletions`（`:860`） | `del self._entries[eid]` | 删除**前** `self._dedup_index_remove(eid)` |
| `_sync_additions` 正常新增（`:1030`） | `self._entries[entry_id] = {...}` 之后 `winner_keys.add(key)` | 写入后追加 `self._dedup_index_add(entry_id)` |

### 4.1 不接入外部修改路径

`get_entries()` 返回 `self._entries` 的可变引用，供 `KeywordSearcher` 使用。已核对 `keyword_searcher.py:102` 的消费方只读不写（仅 `entry.get("text")`），因此无需在外部修改路径加防护，信任现有调用方只读。

## 5. 查找改造

`_find_entry_by_dedup_key`（`index_manager.py:676`）从 O(n) 线性扫描改为 O(1) 哈希查表：

```python
def _find_entry_by_dedup_key(self, key: str) -> str | None:
    """按去重键查找已有条目 ID。

    通过 _dedup_index 反向索引 O(1) 查找，返回该去重键对应的条目 ID。
    正常情况下去重键唯一（add/sync 已保证不引入重复键）。

    Args:
        key: dedup_key 计算结果。

    Returns:
        匹配的条目 ID，无匹配返回 None。
    """
    return self._dedup_index.get(key)
```

`add_entry` 调用方（`:603`）无需改动。

## 6. 测试影响

测试文件：`tests/unit/engine/test_index_manager.py`。

1. **`_find_entry_by_dedup_key` 专项测试（`:1875`）**：现有用例通过直接赋值 `mgr._entries = {...}` 构造索引后再调用 `_find_entry_by_dedup_key`。直接赋值绕过了维护方法，`_dedup_index` 仍为空，**这些用例会失效**。实现阶段先读该段测试，再定最小适配方式（改用 `add_entry`/`load` 构造，或在测试中手动调 `_rebuild_dedup_index()`），不新增冗余用例。

2. **`add_entry` 测试套件（`:578` 起，含 `replaces_on_dedup`、`replaces_when_old_image_missing`）**：通过正常 `add_entry` 路径构造索引，维护方法会被触发，**应继续通过**。这是兜底一致性的关键覆盖。

3. **`sync_with_filesystem` 测试套件（`:802` 起）**：走 `_sync_deletions`/`_sync_additions`，维护方法会被触发，**应继续通过**。

不新增测试：现有「增 / 删 / 覆盖 / 同步」四类写点已由既有用例覆盖。

## 7. 验证

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
uv run python -m compileall bot/engine/index_manager.py
```

## 8. 文档同步

按 `CLAUDE.md` 约定，实现后更新：

- `docs/api/API.md`：在 `IndexManager` 段落补充 `_dedup_index` 属性与三个维护方法的接口说明，并更新 `_find_entry_by_dedup_key` 的返回说明（O(1) 反向索引查找）。
- `docs/process.md`：简要记录本次「去重键反向索引」模块的落地。

## 9. 不做的事

- 不合并 `sync_with_filesystem` 的 `winner_keys` 局部集合与常驻反向索引（语义不同，强行合并增加复杂度）。
- 不对 `_dedup_index` 加「同一 key 指向不同 entry_id」的防御性断言（信任去重键唯一不变式）。
- 不给 `index.json` 增加任何新字段（去重键仍实时计算不落盘，符合 PRD §3.5）。
- 不改 `add_entry` 的调用方代码与对外接口签名。
