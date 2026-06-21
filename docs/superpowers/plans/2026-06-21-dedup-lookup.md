# 去重键反向索引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `IndexManager` 实例上维护常驻 `dedup_key → entry_id` 反向哈希索引，将 `_find_entry_by_dedup_key` 从 O(n) 线性扫描降为 O(1) 查表，并保证索引与 `_entries` 在所有写入路径上的一致性。

**Architecture:** 在 `__init__` 初始化 `self._dedup_index: dict[str, str]`；新增三个私有维护方法 `_rebuild_dedup_index` / `_dedup_index_add` / `_dedup_index_remove`，在 5 个写 `_entries` 的入口（`_load_index` 两分支、`add_entry` 正常新增与去重覆盖、`remove_entry`、`_sync_deletions`、`_sync_additions` 正常新增）显式调用；`_find_entry_by_dedup_key` 改为 `self._dedup_index.get(key)`。严格遵循「remove 在 del 前、add 在赋值后、覆盖分支先 remove 旧 key 再 add 新 key」的时序。

**Tech Stack:** Python 3.12，无新增依赖；测试用 `pytest`（项目已有 `tests/unit/engine/test_index_manager.py`）；语法检查用 `python -m compileall`。

**关联设计文档：** `docs/superpowers/specs/2026-06-21-dedup-lookup-design.md`

---

## File Structure

- **Modify:** `bot/engine/index_manager.py` — 新增 `_dedup_index` 属性与三个维护方法，改造 `_find_entry_by_dedup_key`，在 5 个写点接入维护调用。
- **Modify:** `tests/unit/engine/test_index_manager.py` — 适配 `TestFindEntryByDedupKey` 中直接赋值 `_entries` 的用例（使其同步维护 `_dedup_index`）。
- **Modify:** `docs/API.md` — 补充 `_dedup_index` 属性与维护方法接口说明，更新 `_find_entry_by_dedup_key` 返回说明。
- **Modify:** `docs/process.md` — 记录「去重键反向索引」模块落地。

单文件核心改动，无新建文件，无新依赖。

---

## Task 1: 新增 `_dedup_index` 属性与三个维护方法

本任务只新增属性与维护方法，不接入任何写点、不改查找——保证测试可在下一步以 TDD 方式驱动查找改造。

**Files:**
- Modify: `bot/engine/index_manager.py`（`__init__` 内 `:283` 附近、`_find_entry_by_dedup_key` 上方 `:676` 附近）

- [ ] **Step 1: 在 `__init__` 初始化 `_dedup_index`**

定位 `bot/engine/index_manager.py:283`，在 `self._entries` 与 `self._embeddings` 之间插入 `_dedup_index` 初始化。修改后该段应为：

```python
        self._entries: dict[str, dict[str, str]] = {}
        self._dedup_index: dict[str, str] = {}
        self._embeddings: dict[str, dict[str, object]] = {}
```

同时在类 docstring 的 `Attributes` 段（`:225` 附近）`_entries` 行后补充一行：

```
        _dedup_index: 去重键到 entry_id 的反向索引，加速 _find_entry_by_dedup_key。
```

- [ ] **Step 2: 在 `_find_entry_by_dedup_key` 上方新增三个维护方法**

定位 `bot/engine/index_manager.py:676`（`_find_entry_by_dedup_key` 定义行），在其**正上方**插入：

```python
    def _rebuild_dedup_index(self) -> None:
        """根据当前 _entries 全量重建去重键反向索引。

        在 _load_index 加载完成后调用一次，建立 dedup_key → entry_id 映射。
        空 key（无文字）条目不会进入 _entries，故不会出现空字符串键。

        Returns:
            无返回值，就地重建 self._dedup_index。
        """
        self._dedup_index = {
            dedup_key(entry.get("text", "")): entry_id
            for entry_id, entry in self._entries.items()
        }

    def _dedup_index_add(self, entry_id: str) -> None:
        """将单条条目的去重键加入反向索引。

        在 _entries[entry_id] 赋值之后调用（需读取其 text 计算 key）。
        空 key 跳过（无文字条目不入索引）。信任去重键唯一不变式，
        直接赋值不做冲突检查。

        Args:
            entry_id: 新增/覆盖条目的 ID。

        Returns:
            无返回值，就地更新 self._dedup_index。
        """
        key = dedup_key(self._entries[entry_id].get("text", ""))
        if key:
            self._dedup_index[key] = entry_id

    def _dedup_index_remove(self, entry_id: str) -> None:
        """从反向索引移除单条条目的去重键。

        在 del _entries[entry_id] 之前调用（需读取其 text 计算 key）。
        空 key 跳过。key 不存在时 pop 默认值兜底，避免异常。

        Args:
            entry_id: 待删除条目的 ID。

        Returns:
            无返回值，就地更新 self._dedup_index。
        """
        key = dedup_key(self._entries[entry_id].get("text", ""))
        if key:
            self._dedup_index.pop(key, None)

```

- [ ] **Step 3: 语法编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`
Expected: 无输出，退出码 0（编译通过）。

- [ ] **Step 4: 暂不提交**

本任务仅新增未启用的代码，无行为变化，与 Task 2（接入写点 + 改造查找 + 测试）合并提交更清晰。

---

## Task 2: 改造 `_find_entry_by_dedup_key` 为 O(1) 查表，并适配测试

TDD：先改测试使其通过新路径验证，再改实现，最后跑全量测试确认 5 个写点尚未接入时仍需手动维护 `_dedup_index` 的用例通过。

**Files:**
- Modify: `bot/engine/index_manager.py:676`（`_find_entry_by_dedup_key` 方法体）
- Test: `tests/unit/engine/test_index_manager.py:1874`（`TestFindEntryByDedupKey` 类）

- [ ] **Step 1: 适配 `TestFindEntryByDedupKey` 三个用例**

这三个用例直接 `mgr._entries = {...}` 构造索引，绕过维护方法，改造后 `_dedup_index` 为空会导致 `test_match_found`、`test_no_match_returns_none` 失效。在每次直接赋值 `_entries` 后补一行 `mgr._rebuild_dedup_index()` 显式重建。`test_empty_entries_returns_none` 无需改动（空 `_entries` 查空 `_dedup_index` 仍返回 None）。

定位 `tests/unit/engine/test_index_manager.py:1877`，将 `test_match_found` 改为：

```python
    def test_match_found(self) -> None:
        """去重键命中已有条目时返回其 ID。"""
        mgr = IndexManager()
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "加班 好累", "text_hash": "x"},
            "2": {"filename": "b.jpg", "text": "狗在跑", "text_hash": "y"},
        }
        mgr._rebuild_dedup_index()
        # "加班 好累" 去空格 == "加班好累"
        assert mgr._find_entry_by_dedup_key("加班好累") == "1"
```

将 `test_no_match_returns_none`（`:1887`）改为：

```python
    def test_no_match_returns_none(self) -> None:
        """无命中返回 None。"""
        mgr = IndexManager()
        mgr._entries = {
            "1": {"filename": "a.jpg", "text": "猫", "text_hash": "x"},
        }
        mgr._rebuild_dedup_index()
        assert mgr._find_entry_by_dedup_key("狗") is None
```

`test_empty_entries_returns_none`（`:1895`）保持不变。

- [ ] **Step 2: 改造 `_find_entry_by_dedup_key` 方法体**

定位 `bot/engine/index_manager.py:676`，将整个方法替换为：

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

- [ ] **Step 3: 跑专项测试确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestFindEntryByDedupKey -v`
Expected: 3 passed。

- [ ] **Step 4: 跑全量 index_manager 测试，预期大量失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: `TestFindEntryByDedupKey` 3 项通过；`add_entry` / `sync_with_filesystem` 相关测试因 5 个写点尚未接入 `_dedup_index` 维护而**大量失败**（去重覆盖、去重删除等用例的 `_find_entry_by_dedup_key` 查不到命中）。这是预期的——Task 3 接入写点后这些会转绿。

- [ ] **Step 5: 暂不提交**

待 Task 3 接入全部写点、全量测试转绿后统一提交。

---

## Task 3: 接入 5 个写点的 `_dedup_index` 维护

按「加载 → 增 → 覆盖 → 删 → 同步删 → 同步增」顺序接入。每接入一处后跑相关测试确认转绿，最后跑全量测试。

**Files:**
- Modify: `bot/engine/index_manager.py`（`_load_index`、`add_entry`、`remove_entry`、`_sync_deletions`、`_sync_additions`）

- [ ] **Step 1: `_load_index` 两个分支末尾调用 `_rebuild_dedup_index()`**

定位 `bot/engine/index_manager.py:327`（空索引分支）。当前为：

```python
        if not index_path.exists():
            logger.info("index.json 不存在，初始化为空索引")
            self._entries = {}
            self.index_version = 1
            return
```

在 `return` 前插入重建调用：

```python
        if not index_path.exists():
            logger.info("index.json 不存在，初始化为空索引")
            self._entries = {}
            self.index_version = 1
            self._rebuild_dedup_index()
            return
```

定位 `:355`（从磁盘加载分支末尾）。当前为：

```python
        self._entries = entries
        logger.info("index.json 加载成功，共 %d 条记录", len(self._entries))

        inconsistent_ids = self._check_text_hash_consistency()
        if inconsistent_ids:
            logger.warning(
                "检测到 %d 条 text_hash 不一致，已自动修复: %s",
                len(inconsistent_ids),
                inconsistent_ids,
            )
            self._embeddings_stale = True
```

在 `self._entries = entries` 之后、`_check_text_hash_consistency` 之前插入重建（此时 `_entries` 已就绪，text_hash 修复不改变 text，不影响 dedup_key）：

```python
        self._entries = entries
        self._rebuild_dedup_index()
        logger.info("index.json 加载成功，共 %d 条记录", len(self._entries))

        inconsistent_ids = self._check_text_hash_consistency()
```

- [ ] **Step 2: 语法编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`
Expected: 无输出，退出码 0。

- [ ] **Step 3: `add_entry` 正常新增分支接入 `_dedup_index_add`**

定位 `bot/engine/index_manager.py:636`（正常新增分支）。当前为：

```python
        # 3. 正常新增
        entry_id = self._find_next_id()
        text_hash = compute_text_hash(text)
        self._entries[entry_id] = {
            "filename": filename,
            "text": text,
            "text_hash": text_hash,
        }
        self._embeddings[entry_id] = {
            "text_hash": text_hash,
            "embedding": embedding,
        }
        self.save_index()
        self.save_embeddings()
```

在 `self._entries[entry_id] = {...}` 赋值块之后、`self._embeddings[entry_id] = {...}` 之前插入维护调用（时序：add 在赋值后）：

```python
        # 3. 正常新增
        entry_id = self._find_next_id()
        text_hash = compute_text_hash(text)
        self._entries[entry_id] = {
            "filename": filename,
            "text": text,
            "text_hash": text_hash,
        }
        self._dedup_index_add(entry_id)
        self._embeddings[entry_id] = {
            "text_hash": text_hash,
            "embedding": embedding,
        }
        self.save_index()
        self.save_embeddings()
```

- [ ] **Step 4: `add_entry` 去重覆盖分支接入「先 remove 再 add」**

定位 `bot/engine/index_manager.py:604`（去重覆盖分支）。当前为：

```python
        # 2. 去重键命中已有条目 → 删旧图，复用旧 ID 覆盖
        key = dedup_key(text)
        old_id = self._find_entry_by_dedup_key(key)
        if old_id is not None:
            old_filename = self._entries[old_id].get("filename", "")
            old_path = self._memes_dir / old_filename
            old_path.unlink(missing_ok=True)

            text_hash = compute_text_hash(text)
            self._entries[old_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._embeddings[old_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            self.save_index()
            self.save_embeddings()
```

改为（在 `if old_id is not None:` 进入后、覆盖前先 `remove`，覆盖后 `add`）：

```python
        # 2. 去重键命中已有条目 → 删旧图，复用旧 ID 覆盖
        key = dedup_key(text)
        old_id = self._find_entry_by_dedup_key(key)
        if old_id is not None:
            old_filename = self._entries[old_id].get("filename", "")
            old_path = self._memes_dir / old_filename
            old_path.unlink(missing_ok=True)

            # 覆盖前移除旧 key（新 text 的 dedup_key 可能与旧 text 不同）
            self._dedup_index_remove(old_id)
            text_hash = compute_text_hash(text)
            self._entries[old_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._dedup_index_add(old_id)
            self._embeddings[old_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            self.save_index()
            self.save_embeddings()
```

- [ ] **Step 5: 跑 `add_entry` 测试确认转绿**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestAddEntry -v`
Expected: 全部 passed（含 `test_add_entry_replaces_on_dedup`、`test_add_entry_replaces_when_old_image_missing`、`test_add_entry_no_text_moves_file` 等）。

- [ ] **Step 6: `remove_entry` 接入 `_dedup_index_remove`**

定位 `bot/engine/index_manager.py:662`。当前为：

```python
        if entry_id not in self._entries:
            logger.warning("尝试删除不存在的记录: id=%s", entry_id)
            return False

        filename = self._entries[entry_id].get("filename", "")
        del self._entries[entry_id]
        self._embeddings.pop(entry_id, None)
```

在 `del self._entries[entry_id]` **之前**插入 remove（时序：remove 在 del 前，需读 text）：

```python
        if entry_id not in self._entries:
            logger.warning("尝试删除不存在的记录: id=%s", entry_id)
            return False

        filename = self._entries[entry_id].get("filename", "")
        self._dedup_index_remove(entry_id)
        del self._entries[entry_id]
        self._embeddings.pop(entry_id, None)
```

- [ ] **Step 7: `_sync_deletions` 接入 `_dedup_index_remove`**

定位 `bot/engine/index_manager.py:857`。当前为：

```python
        deleted_count = 0
        for filename, eid in list(filename_to_id.items()):
            if filename not in existing_files:
                logger.info("图片已删除，移除索引: id=%s, filename=%s", eid, filename)
                del self._entries[eid]
                self._embeddings.pop(eid, None)
                del filename_to_id[filename]
                deleted_count += 1
        return deleted_count
```

在 `del self._entries[eid]` **之前**插入 remove：

```python
        deleted_count = 0
        for filename, eid in list(filename_to_id.items()):
            if filename not in existing_files:
                logger.info("图片已删除，移除索引: id=%s, filename=%s", eid, filename)
                self._dedup_index_remove(eid)
                del self._entries[eid]
                self._embeddings.pop(eid, None)
                del filename_to_id[filename]
                deleted_count += 1
        return deleted_count
```

- [ ] **Step 8: `_sync_additions` 正常新增分支接入 `_dedup_index_add`**

定位 `bot/engine/index_manager.py:1030`（正常新增分支）。当前为：

```python
            # 3. 正常新增
            entry_id = self._find_next_id()
            text_hash = compute_text_hash(text)
            self._entries[entry_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._embeddings[entry_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            winner_keys.add(key)
            added += 1
```

在 `self._entries[entry_id] = {...}` 赋值块之后、`self._embeddings[entry_id] = {...}` 之前插入维护调用：

```python
            # 3. 正常新增
            entry_id = self._find_next_id()
            text_hash = compute_text_hash(text)
            self._entries[entry_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._dedup_index_add(entry_id)
            self._embeddings[entry_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            winner_keys.add(key)
            added += 1
```

- [ ] **Step 9: 语法编译检查**

Run: `uv run python -m compileall bot/engine/index_manager.py`
Expected: 无输出，退出码 0。

- [ ] **Step 10: 跑全量 index_manager 测试确认全部转绿**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: 全部 passed（含 `TestFindEntryByDedupKey`、`TestAddEntry`、全部 `sync_with_filesystem` 测试类）。

- [ ] **Step 11: 跑全量测试确认无回归**

Run: `uv run pytest tests/ -v`
Expected: 全部 passed（含 `test_keyword_searcher.py`、`test_logging_config.py`）。

- [ ] **Step 12: 提交**

按 `CLAUDE.md` 严禁事项，**禁止自行 git add / commit**。改为准备好改动后告知用户审核：

```bash
# 不要执行；提示用户审核以下改动：
# git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
# git commit -m "perf(engine): 去重键反向索引，add_entry 去重查找 O(n)→O(1)"
```

向用户报告：核心代码与测试改动已完成、全量测试通过，请审核后由用户执行 commit。

---

## Task 4: 同步 `docs/API.md` 与 `docs/process.md`

按 `CLAUDE.md` 约定，模块实现后需更新 API 文档与 process 记录。

**Files:**
- Modify: `docs/API.md`（`IndexManager` 段落、`_find_entry_by_dedup_key` 段落）
- Modify: `docs/process.md`（追加模块记录）

- [ ] **Step 1: 在 `docs/API.md` 补充 `_dedup_index` 属性与维护方法接口**

先读 `docs/API.md` 中 `IndexManager` 与 `_find_entry_by_dedup_key` 相关段落（搜索 `_find_entry_by_dedup_key`、`IndexManager` 定位），然后在 `_find_entry_by_dedup_key` 段落附近补充三个维护方法说明，并更新 `_find_entry_by_dedup_key` 的返回说明为「通过 `_dedup_index` 反向索引 O(1) 查找」。

具体追加内容（位置：`_find_entry_by_dedup_key` 段落之后）：

```markdown
#### `_rebuild_dedup_index() -> None`

根据当前 `_entries` 全量重建去重键反向索引 `dedup_key → entry_id`。在 `_load_index` 加载完成后调用一次。空 key（无文字）条目不进入 `_entries`，故不会出现空字符串键。

#### `_dedup_index_add(entry_id: str) -> None`

将单条条目的去重键加入反向索引。在 `_entries[entry_id]` 赋值之后调用。空 key 跳过。信任去重键唯一不变式，直接赋值不做冲突检查。

#### `_dedup_index_remove(entry_id: str) -> None`

从反向索引移除单条条目的去重键。在 `del _entries[entry_id]` 之前调用。空 key 跳过。key 不存在时 `pop(key, None)` 兜底。
```

并在 `IndexManager` 的属性表（若有）中补充 `_dedup_index: dict[str, str]` 一行：去重键到 entry_id 的反向索引，加速 `_find_entry_by_dedup_key`。

- [ ] **Step 2: 在 `docs/process.md` 追加模块记录**

读 `docs/process.md` 末尾，按现有格式追加一行，简要说明本次实现的模块。例如（格式与现有条目对齐）：

```
- 去重键反向索引：在 IndexManager 维护 dedup_key → entry_id 常驻哈希索引，将 add_entry 的去重查找从 O(n) 线性扫描降为 O(1) 查表，并在 _load_index / add_entry / remove_entry / _sync_deletions / _sync_additions 全部写点同步维护。
```

- [ ] **Step 3: 提交文档改动（交由用户审核）**

```bash
# 不要执行；提示用户审核：
# git add docs/API.md docs/process.md
# git commit -m "docs(engine): 补充去重键反向索引接口与 process 记录"
```

向用户报告文档已更新，请审核后由用户执行 commit。

---

## Self-Review

**1. Spec coverage：**

- §2 数据结构（`_dedup_index` 属性）→ Task 1 Step 1。
- §3 维护方法（三个私有方法）→ Task 1 Step 2。
- §3.1 时序约束（remove 在 del 前、add 在赋值后、覆盖先 remove 再 add）→ Task 3 Step 4/6/7/8 显式注释时序。
- §4 写点接入表（5 个写点 7 行）→ Task 3 Step 1（`_load_index` 两分支）、Step 3（`add_entry` 正常新增）、Step 4（`add_entry` 去重覆盖）、Step 6（`remove_entry`）、Step 7（`_sync_deletions`）、Step 8（`_sync_additions`）。全覆盖。
- §5 查找改造 → Task 2 Step 2。
- §6 测试影响 → Task 2 Step 1 适配 `TestFindEntryByDedupKey`；Task 3 Step 10/11 全量测试兜底。
- §7 验证命令 → Task 2/3 各步骤。
- §8 文档同步 → Task 4。
- §9 不做的事（不合并 winner_keys、不加断言、不增 index.json 字段、不改调用方）→ 计划全程遵循，无对应任务。

**2. Placeholder scan：** 无 TBD/TODO/"add appropriate error handling"；每个代码步骤均含完整代码块；测试步骤含确切命令与预期输出。通过。

**3. Type consistency：** `_dedup_index: dict[str, str]`、`_rebuild_dedup_index() -> None`、`_dedup_index_add(entry_id: str) -> None`、`_dedup_index_remove(entry_id: str) -> None` 在 Task 1 定义，Task 2/3 调用签名一致；`_find_entry_by_dedup_key(key: str) -> str | None` 签名不变。通过。
