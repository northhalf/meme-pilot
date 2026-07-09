# MetadataStore entries 缓存与 sync 去重 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MetadataStore 层引入全量 entries 缓存（持久 + 写时维护），`MemeEntry` 改 frozen 共享引用，`get_all_entries`/`get_entry`/`entry_count` 命中缓存，并消除 sync 内 `_scan_meme_files` 的重复扫描。

**Architecture:** 缓存放 MetadataStore 层，与现有 `_text_to_id` 同层同生命周期；`load` 合并重建 `_entries` + `_text_to_id`；写方法（add/add_with_id/update/remove）在 `_lock` 内增量维护 `_entries`；读方法返回共享 frozen 引用；`_run_sync_internal` 开头扫一次 `memes/` 传给 phase1/phase2。先做写路径维护、再启用读路径，保证中间状态一致。

**Tech Stack:** Python 3.12、sqlite3、dataclasses(frozen)、pytest、uv

**关联 spec:** `docs/superpowers/specs/2026-07-09-entries-cache-design.md`

---

## 提交策略（重要）

本项目 `CLAUDE.md` 规定：**禁止自行在 main 分支 `git add/commit/merge`，所有提交需用户审核**。每个 Task 末尾的 commit 步骤保留命令，但执行时**必须暂停等待用户审核后由用户提交**（或由用户明确授权在 feature 分支/worktree 上执行）。`uv run pytest` 与 `uv run python -m compileall` 验证步骤可自行运行。

---

## File Structure

| 文件 | 责任 | 改动 |
|------|------|------|
| `bot/engine/metadata_store.py` | sqlite 元数据存储 + `_entries` 缓存 | `MemeEntry` frozen；新增 `_entries`；`load`/`get_all_entries`/`get_entry`/`entry_count`/`add`/`add_with_id`/`update`/`remove` 改造 |
| `bot/engine/index_manager.py` | 索引薄编排 | `_run_sync_internal` 扫一次传参；`_sync_phase1_delete`/`_sync_phase2_add` 签名加 `existing` |
| `tests/unit/engine/test_metadata_store.py` | MetadataStore 单元测试 | 新增缓存行为测试 |
| `tests/unit/engine/test_index_manager.py` | IndexManager 单元测试 | 新增 `_scan_meme_files` 单次扫描测试 |
| `docs/api/bot/engine/metadata_store.md` | API 文档 | 同步缓存语义 |

---

## Task 1: MemeEntry 改 frozen + load 合并重建 _entries

**Files:**
- Modify: `bot/engine/metadata_store.py`（`MemeEntry` line 66-82、`__init__` line 95-104、`load` line 106-133）
- Test: `tests/unit/engine/test_metadata_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_metadata_store.py` 末尾追加：

```python
class TestFrozenAndEntriesCache:
    """MemeEntry frozen 与 _entries 缓存重建测试。"""

    def test_meme_entry_is_frozen(self, store: MetadataStore) -> None:
        """MemeEntry frozen 后字段不可赋值，抛 FrozenInstanceError。"""
        import dataclasses

        store.add("a.jpg", "甲")
        entry = store.get_entry(1)
        assert entry is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.image_path = "b.jpg"  # type: ignore[misc]

    def test_load_rebuilds_entries_and_text_to_id(
        self, tmp_sqlite_path: Path
    ) -> None:
        """load 后 _entries 与 _text_to_id 同步填充，含 tags。"""
        s1 = MetadataStore(str(tmp_sqlite_path))
        s1.load()
        s1.add("a.jpg", "甲", tags=["搞笑", "猫"])
        s1.close()

        s2 = MetadataStore(str(tmp_sqlite_path))
        s2.load()
        assert 1 in s2._entries
        assert s2._entries[1].text == "甲"
        assert s2._entries[1].tags == ["搞笑", "猫"]
        assert s2._text_to_id == {"甲": 1}
        s2.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestFrozenAndEntriesCache -v`
Expected: FAIL（`MemeEntry` 非 frozen，`entry.image_path = "b.jpg"` 不抛异常；`_entries` 属性不存在）

- [ ] **Step 3: 改 MemeEntry 为 frozen**

`bot/engine/metadata_store.py` line 66-67，将：

```python
@dataclass
class MemeEntry:
```

改为：

```python
@dataclass(frozen=True)
class MemeEntry:
```

- [ ] **Step 4: `__init__` 增加 `_entries` 属性**

`bot/engine/metadata_store.py` `__init__`（line 95-104），在 `self._text_to_id: dict[str, int] = {}` 后追加：

```python
        self._text_to_id: dict[str, int] = {}
        self._entries: dict[int, MemeEntry] = {}
```

- [ ] **Step 5: `load` 合并重建 `_entries` 与 `_text_to_id`**

`bot/engine/metadata_store.py` `load` 方法（line 106-133），将 `with self._lock:` 块内重建部分：

```python
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()
            self._text_to_id = {
                row["text"]: row["id"]
                for row in self._conn.execute("SELECT id, text FROM meme")
            }
        logger.info(
            "MetadataStore 加载完成: %s, 共 %d 条记录",
            self._db_path,
            len(self._text_to_id),
        )
```

替换为：

```python
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._conn.commit()
            rows = list(
                self._conn.execute(
                    "SELECT id, image_path, text, speaker FROM meme ORDER BY id"
                )
            )
            tags_by_id: dict[int, list[str]] = {}
            for row in self._conn.execute(
                "SELECT meme_id, tag FROM meme_tag ORDER BY meme_id, tag"
            ):
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
            self._text_to_id = {
                entry.text: eid for eid, entry in self._entries.items()
            }
        logger.info(
            "MetadataStore 加载完成: %s, 共 %d 条记录",
            self._db_path,
            len(self._entries),
        )
```

- [ ] **Step 6: 运行新测试确认通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestFrozenAndEntriesCache -v`
Expected: PASS

- [ ] **Step 7: 运行全量测试确认无回归**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py -v`
Expected: PASS（现有测试不受 frozen 影响：`MemeEntry(...)` 构造与 `==` 比较仍可用）

- [ ] **Step 8: 提交（⚠ 需用户审核）**

```bash
git add bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
git commit -m "refactor(engine): MemeEntry 改 frozen 并在 load 合并重建 _entries 缓存"
```

---

## Task 2: add / add_with_id 维护 _entries

**Files:**
- Modify: `bot/engine/metadata_store.py`（`add` line 291-327、`add_with_id` line 329-368）
- Test: `tests/unit/engine/test_metadata_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_metadata_store.py` 追加新 class：

```python
class TestWriteMaintainsEntriesCache:
    """写操作同步维护 _entries 缓存。"""

    def test_add_updates_entries_cache(self, store: MetadataStore) -> None:
        """add 后 _entries 同步含新条目，_text_to_id 同步。"""
        eid = store.add("a.jpg", "甲", tags=["搞笑", "猫"])
        assert eid in store._entries
        assert store._entries[eid].text == "甲"
        assert store._entries[eid].tags == ["搞笑", "猫"]
        assert store._text_to_id["甲"] == eid

    def test_add_dedup_sort_tags_in_cache(self, store: MetadataStore) -> None:
        """缓存 tags 去重 + 字典序排序，与 SQL 存储一致。"""
        eid = store.add("b.jpg", "乙", tags=["x", "x", "a"])
        assert store._entries[eid].tags == ["a", "x"]
        # 与 get_entry（当前仍走 SQL）返回的 tags 一致
        entry = store.get_entry(eid)
        assert entry is not None
        assert entry.tags == store._entries[eid].tags

    def test_add_with_id_updates_entries_cache(self, store: MetadataStore) -> None:
        """add_with_id 同步维护 _entries。"""
        store.add_with_id(5, "e.jpg", "戊", tags=["t"])
        assert 5 in store._entries
        assert store._entries[5].tags == ["t"]
        assert store._text_to_id["戊"] == 5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestWriteMaintainsEntriesCache -v`
Expected: FAIL（`add` 后 `_entries` 未含新条目，因为 Task 1 的 `load` 填充了 `_entries` 但 `add` 尚未维护）

- [ ] **Step 3: `add` 维护 `_entries`**

`bot/engine/metadata_store.py` `add` 方法末尾（line 312-327），将：

```python
            self._write_tags(entry_id, tags)
            conn.commit()
            self._text_to_id[text] = entry_id
        return entry_id
```

替换为：

```python
            self._write_tags(entry_id, tags)
            conn.commit()
            self._text_to_id[text] = entry_id
            self._entries[entry_id] = MemeEntry(
                id=entry_id,
                image_path=image_path,
                text=text,
                speaker=speaker,
                tags=sorted(set(tags or [])),
            )
        return entry_id
```

- [ ] **Step 4: `add_with_id` 维护 `_entries`**

`bot/engine/metadata_store.py` `add_with_id` 方法末尾（line 352-368），将：

```python
            self._write_tags(entry_id, tags)
            conn.commit()
            self._text_to_id[text] = entry_id
        return entry_id
```

替换为：

```python
            self._write_tags(entry_id, tags)
            conn.commit()
            self._text_to_id[text] = entry_id
            self._entries[entry_id] = MemeEntry(
                id=entry_id,
                image_path=image_path,
                text=text,
                speaker=speaker,
                tags=sorted(set(tags or [])),
            )
        return entry_id
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestWriteMaintainsEntriesCache -v`
Expected: PASS

- [ ] **Step 6: 运行全量测试确认无回归**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py -v`
Expected: PASS

- [ ] **Step 7: 提交（⚠ 需用户审核）**

```bash
git add bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
git commit -m "perf(engine): add/add_with_id 同步维护 _entries 缓存"
```

---

## Task 3: update 用 replace 维护 _entries

**Files:**
- Modify: `bot/engine/metadata_store.py`（import line 20、`update` line 370-444）
- Test: `tests/unit/engine/test_metadata_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_metadata_store.py` 的 `TestWriteMaintainsEntriesCache` class 中追加：

```python
    def test_update_refreshes_entries_cache(self, store: MetadataStore) -> None:
        """update 后 _entries 同步更新字段，_text_to_id 同步 text。"""
        eid = store.add("a.jpg", "甲", tags=["旧"])
        store.update(eid, text="乙", tags=["新1", "新2"])
        assert store._entries[eid].text == "乙"
        assert store._entries[eid].tags == ["新1", "新2"]
        assert "甲" not in store._text_to_id
        assert store._text_to_id["乙"] == eid

    def test_update_image_path_in_cache(self, store: MetadataStore) -> None:
        """update image_path 同步到 _entries。"""
        eid = store.add("old.jpg", "甲")
        store.update(eid, image_path="new.jpg")
        assert store._entries[eid].image_path == "new.jpg"

    def test_update_nonexistent_leaves_cache(self, store: MetadataStore) -> None:
        """id 不存在时不动缓存。"""
        store.update(999, image_path="x.jpg")
        assert 999 not in store._entries
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestWriteMaintainsEntriesCache -v`
Expected: FAIL（`update` 后 `_entries` 未更新）

- [ ] **Step 3: import `replace`**

`bot/engine/metadata_store.py` line 20，将：

```python
from dataclasses import dataclass, field
```

改为：

```python
from dataclasses import dataclass, field, replace
```

- [ ] **Step 4: `update` 维护 `_entries`**

`bot/engine/metadata_store.py` `update` 方法末尾的 `_text_to_id` 维护段（line 436-444），将：

```python
            conn.commit()

            # 维护 _text_to_id
            old_text = row["text"]
            new_text: str = text if text is not _UNSET else old_text  # type: ignore[assignment]
            if old_text in self._text_to_id and self._text_to_id[old_text] == entry_id:
                del self._text_to_id[old_text]
            self._text_to_id[new_text] = entry_id
        return True
```

替换为：

```python
            conn.commit()

            # 维护 _text_to_id
            old_text = row["text"]
            new_text: str = text if text is not _UNSET else old_text  # type: ignore[assignment]
            if old_text in self._text_to_id and self._text_to_id[old_text] == entry_id:
                del self._text_to_id[old_text]
            self._text_to_id[new_text] = entry_id

            # 维护 _entries（frozen，用 replace 生成新对象替换缓存槽位）
            old_entry = self._entries.get(entry_id)
            if old_entry is not None:
                self._entries[entry_id] = replace(
                    old_entry,
                    image_path=(
                        image_path if image_path is not _UNSET else old_entry.image_path
                    ),
                    text=new_text,
                    speaker=(
                        speaker if speaker is not _UNSET else old_entry.speaker
                    ),
                    tags=sorted(set(tags)) if tags is not None else old_entry.tags,
                )
        return True
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestWriteMaintainsEntriesCache -v`
Expected: PASS

- [ ] **Step 6: 运行全量测试确认无回归**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py -v`
Expected: PASS（含 `TestDuplicateEntryError` 的 update 冲突测试，冲突时走 `raise` 不触达 `_entries` 维护）

- [ ] **Step 7: 提交（⚠ 需用户审核）**

```bash
git add bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
git commit -m "perf(engine): update 用 replace 维护 _entries 缓存"
```

---

## Task 4: remove 维护 _entries

**Files:**
- Modify: `bot/engine/metadata_store.py`（`remove` line 446-467）
- Test: `tests/unit/engine/test_metadata_store.py`

- [ ] **Step 1: 写失败测试**

在 `TestWriteMaintainsEntriesCache` class 中追加：

```python
    def test_remove_updates_entries_cache(self, store: MetadataStore) -> None:
        """remove 后 _entries 与 _text_to_id 同步删除。"""
        eid = store.add("a.jpg", "甲")
        store.remove(eid)
        assert eid not in store._entries
        assert "甲" not in store._text_to_id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestWriteMaintainsEntriesCache::test_remove_updates_entries_cache -v`
Expected: FAIL（`remove` 后 `_entries` 仍含条目）

- [ ] **Step 3: `remove` 维护 `_entries`**

`bot/engine/metadata_store.py` `remove` 方法末尾（line 462-467），将：

```python
            conn.commit()
            text = row["text"]
            if self._text_to_id.get(text) == entry_id:
                del self._text_to_id[text]
        return True
```

替换为：

```python
            conn.commit()
            text = row["text"]
            if self._text_to_id.get(text) == entry_id:
                del self._text_to_id[text]
            self._entries.pop(entry_id, None)
        return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestWriteMaintainsEntriesCache::test_remove_updates_entries_cache -v`
Expected: PASS

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py -v`
Expected: PASS

- [ ] **Step 6: 提交（⚠ 需用户审核）**

```bash
git add bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
git commit -m "perf(engine): remove 同步维护 _entries 缓存"
```

---

## Task 5: 读路径 get_all_entries / get_entry / entry_count 读缓存

**前提：** Task 2-4 已完成写路径维护，`_entries` 在写操作后保持一致，可安全启用读路径。

**Files:**
- Modify: `bot/engine/metadata_store.py`（`get_all_entries` line 159-186、`get_entry` line 188-218、`entry_count` line 261-273）
- Test: `tests/unit/engine/test_metadata_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_metadata_store.py` 追加新 class：

```python
class TestReadPathHitsCache:
    """读路径命中缓存，返回共享 frozen 引用。"""

    def test_get_entry_returns_cached_reference(self, store: MetadataStore) -> None:
        """get_entry 返回缓存内同一对象引用（is 判定）。"""
        eid = store.add("a.jpg", "甲")
        entry = store.get_entry(eid)
        assert entry is store._entries[eid]

    def test_get_all_entries_returns_copy_sharing_values(
        self, store: MetadataStore
    ) -> None:
        """get_all_entries 返回新 dict，value 共享 frozen 引用。"""
        eid = store.add("a.jpg", "甲")
        result = store.get_all_entries()
        assert result is not store._entries  # 新 dict（浅拷贝）
        assert result[eid] is store._entries[eid]  # value 共享

    def test_get_all_entries_no_sql_after_cache(self, store: MetadataStore) -> None:
        """缓存命中后多次 get_all_entries 不触发 SQL。"""
        store.add("a.jpg", "甲")
        original_execute = store._conn.execute
        call_count = 0

        def counting_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_execute(*args, **kwargs)

        store._conn.execute = counting_execute  # type: ignore[assignment]
        for _ in range(3):
            store.get_all_entries()
        assert call_count == 0

    def test_entry_count_reads_cache_len(self, store: MetadataStore) -> None:
        """entry_count 等于 len(_entries)。"""
        store.add("a.jpg", "甲")
        store.add("b.jpg", "乙")
        assert store.entry_count() == len(store._entries) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestReadPathHitsCache -v`
Expected: FAIL（`get_entry` 走 SQL 返回新对象，`is store._entries[eid]` 为 False；`get_all_entries` 触发 SQL，`call_count != 0`）

- [ ] **Step 3: `get_all_entries` 读缓存**

`bot/engine/metadata_store.py` `get_all_entries`（line 159-186），将整个方法体替换为：

```python
    def get_all_entries(self) -> dict[int, MemeEntry]:
        """返回全部条目，key=int(id)。

        命中 _entries 缓存，返回浅拷贝 dict（value 共享 frozen MemeEntry）。

        Returns:
            id -> MemeEntry 映射（value 为缓存内 frozen 引用，不可修改）。
        """
        with self._lock:
            return self._entries.copy()
```

- [ ] **Step 4: `get_entry` 读缓存**

`bot/engine/metadata_store.py` `get_entry`（line 188-218），将整个方法体替换为：

```python
    def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按 id 查询单条记录。

        命中 _entries 缓存，返回共享 frozen 引用（O(1)）。

        Args:
            entry_id: 索引 id。

        Returns:
            对应 MemeEntry（缓存内 frozen 引用，不可修改）；id 不存在时返回 None。
        """
        with self._lock:
            return self._entries.get(entry_id)
```

- [ ] **Step 5: `entry_count` 读缓存**

`bot/engine/metadata_store.py` `entry_count`（line 261-273），将整个方法体替换为：

```python
    def entry_count(self) -> int:
        """条目总数（读缓存，O(1)）。

        Returns:
            _entries 当前长度。
        """
        with self._lock:
            return len(self._entries)
```

- [ ] **Step 6: 运行新测试确认通过**

Run: `uv run pytest tests/unit/engine/test_metadata_store.py::TestReadPathHitsCache -v`
Expected: PASS

- [ ] **Step 7: 运行全量单元测试确认无回归**

Run: `uv run pytest tests/unit/ -v`
Expected: PASS（`test_index_manager.py` 的 `FakeMetadataStore` 独立实现，不受影响；现有 `test_metadata_store.py` 的 `get_entry`/`get_all_entries`/`entry_count` 断言仍成立，因为缓存已被写路径正确维护）

- [ ] **Step 8: 提交（⚠ 需用户审核）**

```bash
git add bot/engine/metadata_store.py tests/unit/engine/test_metadata_store.py
git commit -m "perf(engine): get_all_entries/get_entry/entry_count 命中缓存"
```

---

## Task 6: sync 内 _scan_meme_files 仅扫描一次

**Files:**
- Modify: `bot/engine/index_manager.py`（`_run_sync_internal` line 1137-1168、`_sync_phase1_delete` line 1255-1272、`_sync_phase2_add` line 1274-1348）
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_index_manager.py` 的 `TestRefresh` class 之后追加：

```python
@pytest.mark.anyio
async def test_scan_meme_files_called_once_per_sync(
    index_manager: IndexManager,
) -> None:
    """sync 内 _scan_meme_files 仅调用一次（phase1/phase2 复用同一快照）。"""
    call_count = 0
    original = index_manager._scan_meme_files

    def counting_scan() -> set[str]:
        nonlocal call_count
        call_count += 1
        return original()

    index_manager._scan_meme_files = counting_scan  # type: ignore[assignment]
    await index_manager.refresh()
    assert call_count == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::test_scan_meme_files_called_once_per_sync -v`
Expected: FAIL（`call_count == 2`，phase1 和 phase2 各扫一次）

- [ ] **Step 3: `_run_sync_internal` 开头扫一次并传参**

`bot/engine/index_manager.py` `_run_sync_internal`（line 1137-1168），将方法体开头的三阶段调用：

```python
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []

        await self._sync_phase0_consistency(failed)
        deleted_count = await self._sync_phase1_delete()
        added_count, deduped_count, no_text_count = await self._sync_phase2_add(failed)
```

替换为：

```python
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []
        existing_files = self._scan_meme_files()  # 仅扫一次，phase1/phase2 复用

        await self._sync_phase0_consistency(failed)
        deleted_count = await self._sync_phase1_delete(existing_files)
        added_count, deduped_count, no_text_count = await self._sync_phase2_add(
            existing_files, failed
        )
```

- [ ] **Step 4: `_sync_phase1_delete` 接收快照参数**

`bot/engine/index_manager.py` `_sync_phase1_delete`（line 1255-1272），将：

```python
    async def _sync_phase1_delete(self) -> int:
        """阶段1：删除 memes/ 已不存在的图片对应记录。先 sqlite 后 chroma。

        Returns:
            本次删除的图片数量。
        """
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
```

替换为：

```python
    async def _sync_phase1_delete(self, existing: set[str]) -> int:
        """阶段1：删除 memes/ 已不存在的图片对应记录。先 sqlite 后 chroma。

        Args:
            existing: sync 开始时扫描的 memes/ 文件名集合（复用上游快照）。

        Returns:
            本次删除的图片数量。
        """
        entries = await self._run_sync(self._metadata_store.get_all_entries)
```

- [ ] **Step 5: `_sync_phase2_add` 接收快照参数**

`bot/engine/index_manager.py` `_sync_phase2_add`（line 1274-1288），将签名与开头的扫描：

```python
    async def _sync_phase2_add(self, failed: list[str]) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR->embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Args:
            failed: 失败文件名收集列表，处理异常或 upsert 失败回滚的文件名追加至此。

        Returns:
            (added, deduped, no_text_moved) 三元组：新增、去重删除、无文字移走数量。
        """
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
```

替换为：

```python
    async def _sync_phase2_add(
        self, existing: set[str], failed: list[str]
    ) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR->embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Args:
            existing: sync 开始时扫描的 memes/ 文件名集合（复用上游快照）。
            failed: 失败文件名收集列表，处理异常或 upsert 失败回滚的文件名追加至此。

        Returns:
            (added, deduped, no_text_moved) 三元组：新增、去重删除、无文字移走数量。
        """
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
```

- [ ] **Step 6: 运行新测试确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::test_scan_meme_files_called_once_per_sync -v`
Expected: PASS（`call_count == 1`）

- [ ] **Step 7: 运行 IndexManager 全量测试确认无回归**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: PASS（`TestRefresh` 的 dedup/no_text 测试仍通过，快照语义未变）

- [ ] **Step 8: 提交（⚠ 需用户审核）**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "perf(engine): sync 内 _scan_meme_files 仅扫描一次"
```

---

## Task 7: 文档同步 + 全量验证

**Files:**
- Modify: `docs/api/bot/engine/metadata_store.md`
- Run: 全量测试 + 语法检查

- [ ] **Step 1: 运行全量测试**

Run: `uv run pytest`
Expected: 全部 PASS

- [ ] **Step 2: 运行语法检查**

Run: `uv run python -m compileall bot tests`
Expected: 无报错

- [ ] **Step 3: 更新 API 文档**

打开 `docs/api/bot/engine/metadata_store.md`，按以下要点更新（具体措辞参照文件现有风格）：

1. `MemeEntry` 标注 `@dataclass(frozen=True)`，说明返回的实例不可修改字段。
2. `get_all_entries` 语义改为「命中 `_entries` 缓存，返回浅拷贝 dict，value 共享 frozen 引用」。
3. `get_entry` 语义改为「命中 `_entries` 缓存，O(1) 返回共享 frozen 引用」。
4. `entry_count` 语义改为「读 `len(_entries)`，O(1)」。
5. 新增「`_entries` 缓存」说明：`load` 重建，`add`/`add_with_id`/`update`/`remove` 在 `_lock` 内增量维护；`update` 用 `dataclasses.replace` 生成新对象；tags 维护用 `sorted(set(tags or []))` 与 SQL 对齐。

- [ ] **Step 4: 提交（⚠ 需用户审核）**

```bash
git add docs/api/bot/engine/metadata_store.md
git commit -m "docs(api): 同步 metadata_store 缓存语义说明"
```

> 仅文档变更，按项目惯例可注明「仅文档变更，未运行测试」--但本 Task 已运行测试。

---

## Self-Review

**1. Spec coverage:**
- §4.1 缓存结构 + load 重建 + MemeEntry frozen → Task 1 ✓
- §4.2 读路径 get_all_entries/get_entry → Task 5 ✓
- §4.3 写路径 add/add_with_id/update/remove 维护 → Task 2/3/4 ✓
- §4.3 tags 一致性（sorted(set)）→ Task 2/3 实现 + Task 2 测试 ✓
- §4.4 _scan_meme_files 去重 → Task 6 ✓
- §4.5 entry_count 读缓存 → Task 5 ✓
- §6 正确性（写先于读启用）→ Task 顺序 2-4（写）先于 5（读）✓
- §7.1 波及面 metadata_store/index_manager → Task 1-6 ✓
- §7.2 测试零破坏 → Task 5 Step 7 全量验证 ✓
- §8 测试策略 → Task 1-6 各自测试 + Task 7 全量 ✓
- §9 文档同步 → Task 7 ✓

**2. Placeholder scan:** 无 TBD/TODO；每个实现 step 均给出完整代码或精确 old/new 替换。

**3. Type consistency:** `_entries: dict[int, MemeEntry]` 全程一致；`replace` 在 Task 3 import 后使用；`sorted(set(tags or []))` 在 add/add_with_id/update 一致；`existing: set[str]` 在 phase1/phase2 签名一致。
