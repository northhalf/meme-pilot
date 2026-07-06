# `/addtag`、`/del`、`/info` 命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MemePilot 新增三个命令：`/addtag`（为指定表情包添加标签）、`/del`（删除指定表情包并移动到 `memes_deleted/`）、`/info`（显示机器人统计与状态）。

**Architecture:** 在 `IndexManager` 层新增 `add_tags()`、`delete()`、`info()` 三个方法；`add_tags` 与 `delete` 走 Write Worker 串行写入管道（同 `edit_text`/`set_speaker` 模式）；`/del` 删除图片时移动到 `memes_deleted/` 目录而非永久删除；`/info` 由插件层通过 `psutil` 读取硬件信息后组装回复。

**Tech Stack:** Python 3.12, NoneBot2, sqlite3, chromadb, psutil, pytest

---

⚠️ **执行提醒：本计划的每个 Task 由独立的 subagent 执行。每个 subagent 启动后需先调用 `sequential-thinking` 工具做结构化思考再动手。每个 Task 完成后必须 commit。**

---

### Task 0: 创建功能分支

- [ ] **Step 1: 从 main 创建功能分支**

```bash
git checkout main
git pull origin main
git checkout -b feat/addtag-delete-info
```

---

### Task 1: 新增 `psutil` 依赖与 `memes_deleted/` 基础设施

**Files:**
- Modify: `pyproject.toml`
- Modify: `bot/config.py`
- Modify: `docker-compose.yml`

#### 步骤

- [ ] **Step 1: 添加 `psutil` 依赖**

在项目根目录执行：

```bash
uv add psutil
```

Expected: `pyproject.toml` 的 `[project] dependencies` 列表中新增 `"psutil>=..."` 条目，`uv.lock` 被更新。

- [ ] **Step 2: 在 `bot/config.py` 新增 `MEMES_DELETED_DIR`**

找到 `bot/config.py` 中 `MEMES_DIR` 的定义（约第 20-30 行），在其后新增：

```python
MEMES_DELETED_DIR: Path = PROJECT_ROOT / "memes_deleted"
"""被删除表情包的备份目录（可从该目录手动恢复）。"""
```

- [ ] **Step 3: 更新 `docker-compose.yml` 挂载 `memes_deleted/` 卷**

在 `docker-compose.yml` 的 `bot` service `volumes` 段中（约第 62-66 行）新增一行：

```yaml
    volumes:
      - ./memes:/app/memes
      - ./data:/app/data
      - ./log:/app/log
      - ./meme_no_text:/app/meme_no_text
      - ./memes_deleted:/app/memes_deleted
```

- [ ] **Step 4: 运行语法检查**

```bash
uv run python -m compileall bot/config.py
```

Expected: Compiled OK.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock bot/config.py docker-compose.yml
git commit -m "chore(deps): 新增 psutil 依赖与 memes_deleted 目录配置"
```

---

### Task 2: `IndexManager` 基础类型扩展

**Files:**
- Modify: `bot/engine/index_manager.py:75-104` — WriteOp + _WriteRequest
- Modify: `bot/engine/index_manager.py:110-140` — 新增结果 dataclass
- Modify: `bot/engine/index_manager.py:256-331` — IndexManager.__init__
- Modify: `bot/engine/index_manager.py:8-25` — imports

#### 步骤

- [ ] **Step 1: 新增 `session_manager` 导入**

在 `bot/engine/index_manager.py` 的 import 段中，找到 `from bot.config import read_add_command_timeout, read_read_lock_timeout`，在其下方新增：

```python
from bot.session import session_manager
```

- [ ] **Step 2: 修改 `WriteOp` 枚举，新增 `ADD_TAG` 与 `DELETE`**

在 `bot/engine/index_manager.py` 中找到 `class WriteOp(Enum)`（约第 75 行），改为：

```python
class WriteOp(Enum):
    """Write Worker 操作类型枚举。"""

    ADD = auto()
    EDIT_TEXT = auto()
    SET_SPEAKER = auto()
    ADD_TAG = auto()
    DELETE = auto()
```

- [ ] **Step 3: 修改 `_WriteRequest`，新增 `tags` 与 `entry_ids` 字段**

在 `_WriteRequest` dataclass（约第 83 行）中，在 `old_text: str = ""` 前新增两行：

```python
@dataclass
class _WriteRequest:
    """写入任务单元，由 Write Worker 串行处理。"""

    op: WriteOp
    future: "asyncio.Future[AddResult | EditTextResult | SetSpeakerResult | AddTagResult | DeleteResult]"
    entry_id: int = 0
    filename: str = ""
    text: str = ""
    speaker: str | None = None
    tags: list[str] | None = None
    entry_ids: list[int] | None = None
    embedding: list[float] | None = None
    old_text: str = ""
```

- [ ] **Step 4: 在 `SetSpeakerResult` 后新增 `AddTagResult`、`DeleteResult`、`IndexInfo`**

在 `class SetSpeakerResult`（约第 125 行）之后、`class DuplicateTextError` 之前插入：

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

    deleted_ids: list[int]               # 成功删除的 id
    not_found_ids: list[int]             # 不存在的 id
    failed_ids: list[tuple[int, str]]    # 删除失败的 (id, reason)


@dataclass
class IndexInfo:
    """IndexManager 返回的内部统计信息（不含硬件）。"""

    entry_count: int
    speaker_ranking: list[tuple[str | None, int]]  # 前 3
    status: str  # "空闲" / "正在处理命令" / "正在刷新索引"
```

- [ ] **Step 5: 修改 `IndexManager.__init__` 增加 `deleted_dir` 参数**

在 `IndexManager.__init__`（约第 282 行）中：

1. 签名增加 `deleted_dir: str | None = None`：

```python
def __init__(
    self,
    metadata_store: MetadataStoreProtocol,
    vector_store: VectorStoreProtocol,
    memes_dir: str,
    no_text_dir: str | None = None,
    deleted_dir: str | None = None,
    ocr_provider: OcrProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    optimizer: ImageOptimizerProtocol | None = None,
    keyword_searcher: KeywordSearcher | None = None,
    ai_matcher: AIMatcher | None = None,
) -> None:
```

2. 在 `self._no_text_dir = ...` 之后新增：

```python
if deleted_dir is not None:
    self._deleted_dir = Path(deleted_dir)
else:
    self._deleted_dir = Path(memes_dir).parent / "memes_deleted"
```

- [ ] **Step 6: 运行语法检查**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

Expected: Compiled OK.

- [ ] **Step 7: Commit**

```bash
git add bot/engine/index_manager.py
git commit -m "refactor(engine): WriteOp 新增 ADD_TAG/DELETE，新增 AddTagResult/DeleteResult/IndexInfo，IndexManager 支持 deleted_dir"
```

---

### Task 3: `IndexManager.add_tags()` 实现与单元测试

**Files:**
- Modify: `bot/engine/index_manager.py:446-568` 附近 — 新增 `add_tags()` 方法
- Modify: `bot/engine/index_manager.py:599-650` — Write Worker 分发
- Create: `tests/unit/engine/test_index_manager_add_tags.py`

#### 步骤

- [ ] **Step 1: 新增 `add_tags()` 公共方法**

在 `IndexManager.set_speaker()` 方法之后（约第 568 行），`refresh()` 之前插入：

```python
async def add_tags(self, entry_id: int, tags: list[str]) -> AddTagResult:
    """为指定条目追加标签。

    流程：校验 → put WriteRequest → await future。

    Args:
        entry_id: 要修改的索引 id。
        tags: 要追加的标签列表。

    Returns:
        AddTagResult 描述添加结果。

    Raises:
        IndexAddCancelledError: Bot 正在关闭。
        RefreshInProgressError: 刷新进行中或 pending 中。
        ValueError: entry_id 不存在。
    """
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    self._ensure_write_worker()

    entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
    if entry is None:
        raise ValueError(f"entry_id={entry_id} 不存在")

    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    loop = asyncio.get_running_loop()
    future: "asyncio.Future[AddTagResult]" = loop.create_future()
    req = _WriteRequest(
        op=WriteOp.ADD_TAG,
        future=future,  # type: ignore[arg-type]
        entry_id=entry_id,
        tags=list(tags),
    )
    await self._write_queue.put(req)
    return await future
```

- [ ] **Step 2: 在 Write Worker 中分发 `ADD_TAG`**

在 `_write_worker_loop()` 的分支判断中（约第 630 行），在 `elif req.op is WriteOp.SET_SPEAKER:` 后新增：

```python
elif req.op is WriteOp.ADD_TAG:
    result = await self._execute_add_tags(req)
elif req.op is WriteOp.DELETE:
    result = await self._execute_delete(req)
```

- [ ] **Step 3: 新增 `_execute_add_tags()` 私有方法**

在 `_execute_set_speaker()` 方法之后（约第 748 行），`close()` 之前插入：

```python
async def _execute_add_tags(self, req: _WriteRequest) -> AddTagResult:
    """写锁内执行 add_tags 写入（仅 sqlite，无 chroma 操作）。

    Args:
        req: 写入任务单元。

    Returns:
        AddTagResult 描述添加结果。

    Raises:
        ValueError: entry_id 不存在。
    """
    entry = await self._run_sync(self._metadata_store.get_entry, req.entry_id)
    if entry is None:
        raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")

    current_tags = set(entry.tags)
    new_tags = set(req.tags or [])
    added_tags = list(new_tags - current_tags)
    merged_tags = list(current_tags | new_tags)

    if not added_tags:
        return AddTagResult(
            entry_id=req.entry_id,
            added_tags=[],
            all_tags=list(current_tags),
        )

    success = await self._run_sync(
        self._metadata_store.update,
        req.entry_id,
        tags=merged_tags,
    )
    if not success:
        raise ValueError(f"entry_id={req.entry_id} 不存在（update 返回 False）")

    return AddTagResult(
        entry_id=req.entry_id,
        added_tags=added_tags,
        all_tags=merged_tags,
    )
```

- [ ] **Step 4: 编写 `add_tags` 单元测试**

创建 `tests/unit/engine/test_index_manager_add_tags.py`：

```python
import pytest

from bot.engine.index_manager import IndexManager


@pytest.fixture
def index_manager(tmp_path):
    from bot.engine.metadata_store import MetadataStore
    from bot.engine.vector_store import VectorStore

    db_path = tmp_path / "index.db"
    chroma_path = tmp_path / "chroma"
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()

    metadata_store = MetadataStore(str(db_path))
    metadata_store.load()
    vector_store = VectorStore(str(chroma_path))
    vector_store.load()

    im = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    return im


@pytest.mark.asyncio
async def test_add_tags_appends_and_returns_diff(index_manager):
    # 准备：先添加一条记录
    entry_id = index_manager._metadata_store.add("test.jpg", "ocr text", tags=["旧标签"])

    result = await index_manager.add_tags(entry_id, ["新标签", "旧标签"])

    assert result.entry_id == entry_id
    assert result.added_tags == ["新标签"]
    assert sorted(result.all_tags) == sorted(["旧标签", "新标签"])


@pytest.mark.asyncio
async def test_add_tags_id_not_found(index_manager):
    with pytest.raises(ValueError, match="entry_id=999 不存在"):
        await index_manager.add_tags(999, ["tag"])


@pytest.mark.asyncio
async def test_add_tags_all_existing_returns_empty(index_manager):
    entry_id = index_manager._metadata_store.add("test.jpg", "ocr text", tags=["tag"])

    result = await index_manager.add_tags(entry_id, ["tag"])

    assert result.added_tags == []
    assert result.all_tags == ["tag"]
```

- [ ] **Step 5: 运行测试**

```bash
uv run pytest tests/unit/engine/test_index_manager_add_tags.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager_add_tags.py
git commit -m "feat(engine): IndexManager 新增 add_tags() 及单元测试"
```

---

### Task 4: `IndexManager.delete()` 实现与单元测试

**Files:**
- Modify: `bot/engine/index_manager.py`
- Create: `tests/unit/engine/test_index_manager_delete.py`

#### 步骤

- [ ] **Step 1: 新增 `delete()` 公共方法**

在 `add_tags()` 方法之后（即 Task 3 新增位置之后），`refresh()` 之前插入：

```python
async def delete(self, entry_ids: list[int]) -> DeleteResult:
    """删除一个或多个表情包条目。

    流程：校验 → put WriteRequest → await future。

    Args:
        entry_ids: 要删除的索引 id 列表。

    Returns:
        DeleteResult 描述删除结果。

    Raises:
        IndexAddCancelledError: Bot 正在关闭。
        RefreshInProgressError: 刷新进行中或 pending 中。
    """
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    self._ensure_write_worker()

    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    loop = asyncio.get_running_loop()
    future: "asyncio.Future[DeleteResult]" = loop.create_future()
    req = _WriteRequest(
        op=WriteOp.DELETE,
        future=future,  # type: ignore[arg-type]
        entry_ids=list(entry_ids),
    )
    await self._write_queue.put(req)
    return await future
```

- [ ] **Step 2: 新增 `_execute_delete()` 私有方法**

在 `_execute_add_tags()` 方法之后插入：

```python
async def _execute_delete(self, req: _WriteRequest) -> DeleteResult:
    """写锁内执行 delete（先 sqlite 后 chroma，再移动文件到 memes_deleted/）。

    Args:
        req: 写入任务单元。

    Returns:
        DeleteResult 描述删除结果。
    """
    self._deleted_dir.mkdir(parents=True, exist_ok=True)

    deleted_ids: list[int] = []
    not_found_ids: list[int] = []
    failed_ids: list[tuple[int, str]] = []

    for entry_id in req.entry_ids or []:
        entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
        if entry is None:
            not_found_ids.append(entry_id)
            continue

        try:
            await self._run_sync(self._metadata_store.remove, entry_id)
            await self._vector_store.remove(entry_id)

            src = self._memes_dir / entry.image_path
            if src.exists():
                dst = self._resolve_unique_deleted_path(entry.image_path)
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)

            deleted_ids.append(entry_id)
        except Exception as exc:
            logger.error("删除条目失败: id=%s, error=%s", entry_id, exc)
            failed_ids.append((entry_id, str(exc)))

    return DeleteResult(
        deleted_ids=deleted_ids,
        not_found_ids=not_found_ids,
        failed_ids=failed_ids,
    )


def _resolve_unique_deleted_path(self, image_path: str) -> Path:
    """生成 memes_deleted/ 下的唯一目标路径（冲突时追加 _n）。"""
    dst = self._deleted_dir / Path(image_path).name
    if not dst.exists():
        return dst

    stem = dst.stem
    suffix = dst.suffix
    parent = dst.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
```

- [ ] **Step 3: 编写 `delete` 单元测试**

创建 `tests/unit/engine/test_index_manager_delete.py`：

```python
import pytest

from bot.engine.index_manager import IndexManager


@pytest.fixture
def index_manager(tmp_path):
    from bot.engine.metadata_store import MetadataStore
    from bot.engine.vector_store import VectorStore

    db_path = tmp_path / "index.db"
    chroma_path = tmp_path / "chroma"
    memes_dir = tmp_path / "memes"
    deleted_dir = tmp_path / "memes_deleted"
    memes_dir.mkdir()

    metadata_store = MetadataStore(str(db_path))
    metadata_store.load()
    vector_store = VectorStore(str(chroma_path))
    vector_store.load()

    im = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
        deleted_dir=str(deleted_dir),
    )
    return im


@pytest.mark.asyncio
async def test_delete_moves_file_to_deleted_dir(index_manager):
    # 创建图片文件
    image_path = index_manager._memes_dir / "test.jpg"
    image_path.write_text("fake image")

    entry_id = index_manager._metadata_store.add("test.jpg", "ocr text")
    await index_manager._vector_store.upsert(entry_id, [0.1] * 1024)

    result = await index_manager.delete([entry_id])

    assert result.deleted_ids == [entry_id]
    assert result.not_found_ids == []
    assert result.failed_ids == []
    assert not image_path.exists()
    assert (index_manager._deleted_dir / "test.jpg").exists()


@pytest.mark.asyncio
async def test_delete_not_found_id(index_manager):
    result = await index_manager.delete([999])

    assert result.deleted_ids == []
    assert result.not_found_ids == [999]
    assert result.failed_ids == []


@pytest.mark.asyncio
async def test_delete_unique_filename_on_conflict(index_manager):
    # 先在 deleted 目录放一个同名文件
    index_manager._deleted_dir.mkdir(parents=True, exist_ok=True)
    (index_manager._deleted_dir / "test.jpg").write_text("old")

    image_path = index_manager._memes_dir / "test.jpg"
    image_path.write_text("fake image")
    entry_id = index_manager._metadata_store.add("test.jpg", "ocr text")

    result = await index_manager.delete([entry_id])

    assert result.deleted_ids == [entry_id]
    assert (index_manager._deleted_dir / "test_1.jpg").exists()
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/unit/engine/test_index_manager_delete.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager_delete.py
git commit -m "feat(engine): IndexManager 新增 delete()，删除文件移至 memes_deleted/"
```

---

### Task 5: `IndexManager.info()` 实现与单元测试

**Files:**
- Modify: `bot/engine/index_manager.py`
- Create: `tests/unit/engine/test_index_manager_info.py`

#### 步骤

- [ ] **Step 1: 新增 `info()` 公共方法**

在 `delete()` 方法之后，`refresh()` 之前插入：

```python
async def info(self) -> IndexInfo:
    """返回当前索引内部统计信息（不含硬件）。

    Returns:
        IndexInfo 描述当前统计与状态。
    """
    entries = await self._run_sync(self._metadata_store.get_all_entries)

    speaker_counts: dict[str | None, int] = {}
    for entry in entries.values():
        speaker_counts[entry.speaker] = speaker_counts.get(entry.speaker, 0) + 1

    speaker_ranking = sorted(
        speaker_counts.items(),
        key=lambda item: (-item[1], item[0] or ""),
    )[:3]

    if self._refresh_active:
        status = "正在刷新索引"
    elif session_manager.has_active_session():
        status = "正在处理命令"
    else:
        status = "空闲"

    return IndexInfo(
        entry_count=len(entries),
        speaker_ranking=speaker_ranking,
        status=status,
    )
```

- [ ] **Step 2: 编写 `info` 单元测试**

创建 `tests/unit/engine/test_index_manager_info.py`：

```python
import pytest

from bot.engine.index_manager import IndexManager
from bot.session import session_manager


@pytest.fixture
def index_manager(tmp_path):
    from bot.engine.metadata_store import MetadataStore
    from bot.engine.vector_store import VectorStore

    db_path = tmp_path / "index.db"
    chroma_path = tmp_path / "chroma"
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()

    metadata_store = MetadataStore(str(db_path))
    metadata_store.load()
    vector_store = VectorStore(str(chroma_path))
    vector_store.load()

    im = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    return im


@pytest.mark.asyncio
async def test_info_entry_count_and_ranking(index_manager):
    index_manager._metadata_store.add("a.jpg", "text a", speaker="小明")
    index_manager._metadata_store.add("b.jpg", "text b", speaker="小明")
    index_manager._metadata_store.add("c.jpg", "text c", speaker="老板")

    info = await index_manager.info()

    assert info.entry_count == 3
    assert info.speaker_ranking == [("小明", 2), ("老板", 1)]
    assert info.status == "空闲"


@pytest.mark.asyncio
async def test_info_status_processing(index_manager):
    user_id = "123456"
    session_manager.activate_chat(user_id, "test", None)

    try:
        info = await index_manager.info()
        assert info.status == "正在处理命令"
    finally:
        session_manager.deactivate_chat(user_id)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/engine/test_index_manager_info.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager_info.py
git commit -m "feat(engine): IndexManager 新增 info() 及单元测试"
```

---

### Task 6: `SessionManager` 新增 `has_active_session()`

**Files:**
- Modify: `bot/session.py`
- Create: `tests/unit/test_session_manager.py`

#### 步骤

- [ ] **Step 1: 在 `SessionManager` 中新增 `has_active_session()`**

在 `bot/session.py` 的 `SessionManager` 类中找到合适位置（如 `deactivate_chat` 之后），新增：

```python
def has_active_session(self) -> bool:
    """是否存在非空闲的聊天会话。

    Returns:
        True 表示至少有一个用户处于活跃命令会话中。
    """
    return any(session.active for session in self._chat_sessions.values())
```

- [ ] **Step 2: 编写测试**

创建 `tests/unit/test_session_manager.py`（如不存在）：

```python
import pytest

from bot.session import session_manager


@pytest.fixture(autouse=True)
def reset_session_manager():
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()
    yield
    session_manager._chat_sessions.clear()
    session_manager._selection_sessions.clear()


def test_has_active_session_initially_false():
    assert session_manager.has_active_session() is False


def test_has_active_session_true_after_activate():
    session_manager.activate_chat("123", "test", None)
    assert session_manager.has_active_session() is True


def test_has_active_session_false_after_deactivate():
    session_manager.activate_chat("123", "test", None)
    session_manager.deactivate_chat("123")
    assert session_manager.has_active_session() is False
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/test_session_manager.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/session.py tests/unit/test_session_manager.py
git commit -m "feat(session): SessionManager 新增 has_active_session()"
```

---

### Task 7: `/addtag` 命令插件

**Files:**
- Create: `bot/plugins/meme_addtag.py`
- Create: `tests/unit/plugins/test_meme_addtag.py`

#### 步骤

- [ ] **Step 1: 创建 `bot/plugins/meme_addtag.py`**

```python
"""/addtag 命令插件 — 为指定表情包添加标签。

授权用户私聊中发送 /addtag <entry_id> <tag>...，
Bot 发送确认消息（含 OCR 文本，不含图片），用户回复「确认」后执行添加。
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
from bot.engine.index_manager import (
    AddTagResult,
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

addtag_cmd = on_command("addtag", rule=to_me(), priority=5, block=True)


@addtag_cmd.handle()
async def handle_addtag(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """入口：授权校验 → 参数解析 → 发送确认消息。"""
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /addtag", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "addtag")
            await matcher.finish(None)
            return

        if event.message_type != "private":
            await matcher.finish("此命令仅限私聊使用")
            return

        if not session_manager.activate_chat(user_id, "addtag", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        raw = event.get_plaintext().strip()
        text_part = raw.removeprefix("/addtag").removeprefix("addtag").strip()
        parts = text_part.split()
        if len(parts) < 2:
            await matcher.finish("用法：/addtag <id> <tag>...")
            return

        try:
            entry_id = int(parts[0])
        except ValueError:
            await matcher.finish("id 必须为数字")
            return

        tags = parts[1:]
        if not tags:
            await matcher.finish("用法：/addtag <id> <tag>...")
            return

        store = get_metadata_store()
        entry = store.get_entry(entry_id)
        if entry is None:
            await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")
            return

        current_tags_text = "、".join(entry.tags) if entry.tags else "无"
        new_tags_text = "、".join(tags)

        await matcher.send(
            f"id：{entry_id}\n"
            f"OCR 文本：{entry.text}\n"
            f"当前标签：{current_tags_text}\n"
            f"将新增标签：{new_tags_text}\n"
            "回复「确认」执行添加，回复其他内容取消。"
        )

        matcher.state["entry_id"] = entry_id
        matcher.state["tags"] = tags

        selection_id = str(uuid.uuid4())
        task = asyncio.create_task(
            timeout_session(bot, event, user_id, selection_id, "标签添加已取消（超时）")
        )
        session_manager.create_selection(user_id, selection_id, task)
        session_manager.reset_current_task(user_id)

    except asyncio.CancelledError:
        raise FinishedException


@addtag_cmd.got("confirm")
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

            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            if text.strip().lower() in ("确认", "yes", "y"):
                entry_id = matcher.state["entry_id"]
                tags = matcher.state["tags"]

                try:
                    result: AddTagResult = await asyncio.wait_for(
                        get_index_manager().add_tags(entry_id, tags),
                        timeout=get_index_manager().add_user_timeout,
                    )
                except asyncio.TimeoutError:
                    await matcher.finish("添加处理超时，请稍后再试")
                except IndexAddCancelledError:
                    await matcher.finish("服务正在关闭，请稍后再试")
                except RefreshInProgressError:
                    await matcher.finish("索引正在刷新，请稍后再试")
                except ValueError:
                    await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")
                else:
                    session_manager.deactivate_chat(user_id)
                    if not result.added_tags:
                        await matcher.finish("这些标签已存在，无需添加")
                        return

                    added_text = "、".join(result.added_tags)
                    all_text = "、".join(result.all_tags) if result.all_tags else "无"
                    await matcher.finish(
                        f"标签已添加 ✅\n"
                        f"id：{result.entry_id}\n"
                        f"新增标签：{added_text}\n"
                        f"当前标签：{all_text}"
                    )
                    return
            else:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("已取消")

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
            logger.exception("用户 %s 的 /addtag 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
```

- [ ] **Step 2: 编写 `/addtag` 插件单元测试**

创建 `tests/unit/plugins/test_meme_addtag.py`：

```python
"""/addtag 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.meme_addtag import (
        got_confirm,
        handle_addtag,
    )

from bot.engine.index_manager import AddTagResult


def _make_event(user_id: str = "12345", text: str = "/addtag") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_entry(tags: list[str] | None = None) -> MagicMock:
    entry = MagicMock()
    entry.id = 3
    entry.text = "加班心累时的表情包"
    entry.tags = tags or []
    return entry


class TestHandleAddtag:
    def test_unauthorized(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.is_authorized", return_value=False),
            patch("bot.plugins.meme_addtag.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            asyncio.run(handle_addtag(bot, event, matcher))  # type: ignore[arg-type]

            assert matcher.finish.call_count == 1
            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_addtag.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            matcher = _make_matcher()

            asyncio.run(handle_addtag(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("此命令仅限私聊使用")

    def test_missing_args(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_addtag.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/addtag")
            matcher = _make_matcher()

            asyncio.run(handle_addtag(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "用法" in msg

    def test_entry_not_found(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_addtag.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_addtag.get_metadata_store") as mock_get_store,
        ):
            store = MagicMock()
            store.get_entry.return_value = None
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/addtag 999 吐槽")
            matcher = _make_matcher()

            asyncio.run(handle_addtag(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            assert "未找到" in matcher.finish.await_args[0][0]

    def test_send_confirm(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_addtag.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_addtag.get_metadata_store") as mock_get_store,
            patch("bot.plugins.meme_addtag.get_index_manager"),
        ):
            store = MagicMock()
            store.get_entry.return_value = _make_entry(tags=["旧标签"])
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/addtag 3 新标签")
            matcher = _make_matcher()

            asyncio.run(handle_addtag(bot, event, matcher))  # type: ignore[arg-type]

            matcher.send.assert_awaited_once()
            msg = matcher.send.await_args[0][0]
            assert "OCR 文本" in msg
            assert "旧标签" in msg
            assert "新标签" in msg
            assert matcher.state["entry_id"] == 3
            assert matcher.state["tags"] == ["新标签"]


class TestGotConfirm:
    def test_confirm_yes(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.session_manager.handler_context"),
            patch("bot.plugins.meme_addtag.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_addtag.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.add_tags = AsyncMock(
                return_value=AddTagResult(
                    entry_id=3, added_tags=["新标签"], all_tags=["旧标签", "新标签"]
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(state={"entry_id": 3, "tags": ["新标签"]})

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            im.add_tags.assert_awaited_once_with(3, ["新标签"])
            matcher.finish.assert_awaited_once()
            assert "标签已添加" in matcher.finish.await_args[0][0]

    def test_cancel(self) -> None:
        with (
            patch("bot.plugins.meme_addtag.session_manager.handler_context"),
            patch("bot.plugins.meme_addtag.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(state={"entry_id": 3, "tags": ["新标签"]})

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("已取消")
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_addtag.py -v
```

Expected: tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_addtag.py tests/unit/plugins/test_meme_addtag.py
git commit -m "feat(plugins): 新增 /addtag 命令插件"
```

---

### Task 8: `/del` 命令插件

**Files:**
- Create: `bot/plugins/meme_delete.py`
- Create: `tests/unit/plugins/test_meme_delete.py`

#### 步骤

- [ ] **Step 1: 创建 `bot/plugins/meme_delete.py`**

参考 `/edittext` 的确认交互模式，实现 `/del`：

```python
"""/del 命令插件 — 删除指定表情包。

授权用户私聊中发送 /del <entry_id>...，
Bot 发送摘要确认消息，用户回复「确认」后执行删除。
删除后的图片移动到 memes_deleted/ 目录。
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
from bot.engine.index_manager import (
    DeleteResult,
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

delete_cmd = on_command("del", rule=to_me(), priority=5, block=True)


def _truncate_text(text: str, max_len: int = 30) -> str:
    """截断 OCR 文本用于摘要显示。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...（共 {len(text)} 字）"


@delete_cmd.handle()
async def handle_delete(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """入口：授权校验 → 参数解析 → 发送摘要确认。"""
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /del", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "del")
            await matcher.finish(None)
            return

        if event.message_type != "private":
            await matcher.finish("此命令仅限私聊使用")
            return

        if not session_manager.activate_chat(user_id, "del", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        raw = event.get_plaintext().strip()
        text_part = raw.removeprefix("/del").removeprefix("del").strip()
        parts = text_part.split()
        if not parts:
            await matcher.finish("用法：/del <id>...")
            return

        entry_ids: list[int] = []
        for p in parts:
            try:
                entry_ids.append(int(p))
            except ValueError:
                await matcher.finish("id 必须为数字")
                return

        entry_ids = list(dict.fromkeys(entry_ids))  # 去重并保持顺序

        store = get_metadata_store()
        found_entries: list[tuple[int, str]] = []
        not_found_ids: list[int] = []

        for eid in entry_ids:
            entry = store.get_entry(eid)
            if entry is None:
                not_found_ids.append(eid)
            else:
                found_entries.append((eid, entry.text))

        if not found_entries:
            session_manager.deactivate_chat(user_id)
            await matcher.finish("未找到任何表情包")
            return

        lines = ["确认删除以下表情包？回复「确认」执行删除，回复其他内容取消。"]
        for eid, text in found_entries:
            lines.append(f"{eid}, {_truncate_text(text)}")

        if not_found_ids:
            lines.append(f"未找到：{', '.join(str(i) for i in not_found_ids)}")

        await matcher.send("\n".join(lines))

        matcher.state["entry_ids"] = [eid for eid, _ in found_entries]

        selection_id = str(uuid.uuid4())
        task = asyncio.create_task(
            timeout_session(bot, event, user_id, selection_id, "删除已取消（超时）")
        )
        session_manager.create_selection(user_id, selection_id, task)
        session_manager.reset_current_task(user_id)

    except asyncio.CancelledError:
        raise FinishedException


@delete_cmd.got("confirm")
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

            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            if text.strip().lower() in ("确认", "yes", "y"):
                entry_ids = matcher.state["entry_ids"]

                try:
                    result: DeleteResult = await asyncio.wait_for(
                        get_index_manager().delete(entry_ids),
                        timeout=get_index_manager().add_user_timeout,
                    )
                except asyncio.TimeoutError:
                    await matcher.finish("删除处理超时，请稍后再试")
                except IndexAddCancelledError:
                    await matcher.finish("服务正在关闭，请稍后再试")
                except RefreshInProgressError:
                    await matcher.finish("索引正在刷新，请稍后再试")
                else:
                    session_manager.deactivate_chat(user_id)
                    lines = ["已删除表情包 ✅"]
                    if result.deleted_ids:
                        lines.append(f"成功：{', '.join(str(i) for i in result.deleted_ids)}")
                    if result.not_found_ids:
                        lines.append(f"未找到：{', '.join(str(i) for i in result.not_found_ids)}")
                    if result.failed_ids:
                        failed_text = ", ".join(f"{eid}（{reason}）" for eid, reason in result.failed_ids)
                        lines.append(f"失败：{failed_text}")
                    await matcher.finish("\n".join(lines))
                    return
            else:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("已取消删除")

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
            logger.exception("用户 %s 的 /del 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
```

- [ ] **Step 2: 编写 `/del` 插件单元测试**

创建 `tests/unit/plugins/test_meme_delete.py`：

```python
"""/del 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="CONFIRM_ARG_SENTINEL"),
):
    from bot.plugins.meme_delete import (
        got_confirm,
        handle_delete,
    )

from bot.engine.index_manager import DeleteResult


def _make_event(user_id: str = "12345", text: str = "/del") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_entry(text: str = "加班心累时的表情包") -> MagicMock:
    entry = MagicMock()
    entry.id = 3
    entry.text = text
    return entry


class TestHandleDelete:
    def test_unauthorized(self) -> None:
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=False),
            patch("bot.plugins.meme_delete.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher))  # type: ignore[arg-type]

            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat(self) -> None:
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event()
            event.message_type = "group"
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("此命令仅限私聊使用")

    def test_missing_args(self) -> None:
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/del")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            assert "用法" in matcher.finish.await_args[0][0]

    def test_invalid_id(self) -> None:
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
        ):
            bot = _make_bot()
            event = _make_event(text="/del abc")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("id 必须为数字")

    def test_all_not_found(self) -> None:
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_delete.get_metadata_store") as mock_get_store,
        ):
            store = MagicMock()
            store.get_entry.return_value = None
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/del 999")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("未找到任何表情包")

    def test_send_summary(self) -> None:
        with (
            patch("bot.plugins.meme_delete.is_authorized", return_value=True),
            patch(
                "bot.plugins.meme_delete.session_manager.activate_chat",
                return_value=True,
            ),
            patch("bot.plugins.meme_delete.get_metadata_store") as mock_get_store,
            patch("bot.plugins.meme_delete.get_index_manager"),
        ):
            store = MagicMock()
            store.get_entry.side_effect = lambda eid: _make_entry("测试文本") if eid == 3 else None
            mock_get_store.return_value = store

            bot = _make_bot()
            event = _make_event(text="/del 3 999")
            matcher = _make_matcher()

            asyncio.run(handle_delete(bot, event, matcher))  # type: ignore[arg-type]

            matcher.send.assert_awaited_once()
            msg = matcher.send.await_args[0][0]
            assert "确认删除" in msg
            assert "3, 测试文本" in msg
            assert "999" in msg
            assert matcher.state["entry_ids"] == [3]


class TestGotConfirm:
    def test_confirm_yes(self) -> None:
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
            patch("bot.plugins.meme_delete.get_index_manager") as mock_get_im,
        ):
            im = MagicMock()
            im.delete = AsyncMock(
                return_value=DeleteResult(
                    deleted_ids=[3], not_found_ids=[999], failed_ids=[]
                )
            )
            im.add_user_timeout = 60
            mock_get_im.return_value = im

            bot = _make_bot()
            event = _make_event(text="确认")
            matcher = _make_matcher(state={"entry_ids": [3, 999]})

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            im.delete.assert_awaited_once_with([3, 999])
            matcher.finish.assert_awaited_once()
            assert "已删除" in matcher.finish.await_args[0][0]

    def test_cancel(self) -> None:
        with (
            patch("bot.plugins.meme_delete.session_manager.handler_context"),
            patch("bot.plugins.meme_delete.session_manager.deactivate_chat"),
        ):
            bot = _make_bot()
            event = _make_event(text="不")
            matcher = _make_matcher(state={"entry_ids": [3]})

            asyncio.run(got_confirm(bot, event, matcher, "CONFIRM_ARG_SENTINEL"))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once_with("已取消删除")
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_delete.py -v
```

Expected: tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_delete.py tests/unit/plugins/test_meme_delete.py
git commit -m "feat(plugins): 新增 /del 命令插件"
```

---

### Task 9: `/info` 命令插件

**Files:**
- Create: `bot/plugins/meme_info.py`
- Create: `tests/unit/plugins/test_meme_info.py`

#### 步骤

- [ ] **Step 1: 创建 `bot/plugins/meme_info.py`**

```python
"""/info 命令插件 — 显示机器人统计与状态信息。"""

import asyncio
import logging

import psutil

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Matcher
from nonebot.exception import FinishedException
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized

logger = logging.getLogger(__name__)

info_cmd = on_command("info", rule=to_me(), priority=5, block=True)


@info_cmd.handle()
async def handle_info(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """入口：授权校验 → 读取统计 → 组装回复。"""
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /info", user_id)

    try:
        if not is_authorized(user_id):
            log_unauthorized(user_id, "info")
            await matcher.finish(None)
            return

        index_info = await get_index_manager().info()

        try:
            mem = psutil.virtual_memory()
            cpu_percent = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
            mem_text = f"{mem.used // 1024 // 1024} MB / {mem.total // 1024 // 1024} MB ({mem.percent:.1f}%)"
            cpu_text = f"{cpu_percent:.1f}%"
        except Exception as exc:
            logger.warning("读取硬件信息失败: %s", exc)
            mem_text = "获取失败"
            cpu_text = "获取失败"

        lines = [
            f"表情包数量：{index_info.entry_count}",
            "排行（前 3）：",
        ]
        for i, (speaker, count) in enumerate(index_info.speaker_ranking, 1):
            speaker_text = speaker if speaker else "无"
            lines.append(f"  {i}. {speaker_text} {count}")
        lines.append(f"当前机器人状态：{index_info.status}")
        lines.append(f"内存占用：{mem_text}")
        lines.append(f"CPU占用：{cpu_text}")

        await matcher.finish("\n".join(lines))

    except FinishedException:
        raise
    except Exception:
        logger.exception("用户 %s 的 /info 处理异常", user_id)
        raise
```

- [ ] **Step 2: 编写 `/info` 插件单元测试**

创建 `tests/unit/plugins/test_meme_info.py`：

```python
"""/info 命令插件单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins.meme_info import handle_info

from bot.engine.index_manager import IndexInfo


def _make_event(user_id: str = "12345", message_type: str = "private") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = message_type
    return event


def _make_bot() -> MagicMock:
    return MagicMock()


def _make_matcher() -> MagicMock:
    matcher = MagicMock()
    matcher.finish = AsyncMock()
    return matcher


class TestHandleInfo:
    def test_unauthorized(self) -> None:
        with (
            patch("bot.plugins.meme_info.is_authorized", return_value=False),
            patch("bot.plugins.meme_info.log_unauthorized") as mock_log,
        ):
            bot = _make_bot()
            event = _make_event()
            matcher = _make_matcher()

            asyncio.run(handle_info(bot, event, matcher))  # type: ignore[arg-type]

            assert matcher.finish.await_args[0][0] is None
            mock_log.assert_called_once()

    def test_group_chat_allowed(self) -> None:
        with (
            patch("bot.plugins.meme_info.is_authorized", return_value=True),
            patch("bot.plugins.meme_info.get_index_manager") as mock_get_im,
            patch("bot.plugins.meme_info.psutil") as mock_psutil,
        ):
            im = MagicMock()
            im.info = AsyncMock(
                return_value=IndexInfo(
                    entry_count=10,
                    speaker_ranking=[("小明", 5), (None, 3)],
                    status="空闲",
                )
            )
            mock_get_im.return_value = im

            mock_psutil.virtual_memory.return_value = MagicMock(
                used=512 * 1024 * 1024,
                total=2048 * 1024 * 1024,
                percent=25.0,
            )
            mock_psutil.cpu_percent.return_value = 12.5

            bot = _make_bot()
            event = _make_event(message_type="group")
            matcher = _make_matcher()

            asyncio.run(handle_info(bot, event, matcher))  # type: ignore[arg-type]

            matcher.finish.assert_awaited_once()
            msg = matcher.finish.await_args[0][0]
            assert "表情包数量：10" in msg
            assert "小明 5" in msg
            assert "空闲" in msg
            assert "内存占用" in msg
            assert "CPU占用" in msg
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/plugins/test_meme_info.py -v
```

Expected: tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/plugins/meme_info.py tests/unit/plugins/test_meme_info.py
git commit -m "feat(plugins): 新增 /info 命令插件"
```

---

### Task 10: 更新帮助文本

**Files:**
- Modify: `bot/plugins/_help_text.py`

#### 步骤

- [ ] **Step 1: 在 `HELP_TEXT` 中新增三条命令**

在 `bot/plugins/_help_text.py` 的 `/setspeaker` 行后新增：

```python
HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>]：通过聊天添加一张表情包
/addtag <id> <tag>...：为指定表情包添加标签
/del <id>...：删除指定表情包（需确认）
/edittext <id> <新文本>：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人]：设置或清空表情包的说话人
/refresh：扫描 memes/ 并增量更新索引
/info：查看机器人状态与统计信息
/cancel：取消当前正在执行的命令"""
```

- [ ] **Step 2: 运行相关测试**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: tests PASS.

- [ ] **Step 3: Commit**

```bash
git add bot/plugins/_help_text.py
git commit -m "feat(plugins): 帮助文本新增 /addtag、/del、/info"
```

---

### Task 11: 更新 `bot.py` 注入 `deleted_dir`

**Files:**
- Modify: `bot/bot.py`

#### 步骤

- [ ] **Step 1: 在 `bot/bot.py` 导入 `MEMES_DELETED_DIR`**

找到 `from bot.config import (...)` 块，在其中新增 `MEMES_DELETED_DIR`：

```python
from bot.config import (
    CHROMA_DIR,
    DATA_DIR,
    INDEX_DB_PATH,
    MEMES_DELETED_DIR,
    MEMES_DIR,
    NO_TEXT_DIR,
)
```

- [ ] **Step 2: 找到 `IndexManager` 实例化位置**

在 `bot/bot.py` 中找到创建 `IndexManager` 的代码（约 startup hook 中），类似：

```python
index_manager = IndexManager(
    metadata_store=metadata_store,
    vector_store=vector_store,
    memes_dir=str(MEMES_DIR),
    no_text_dir=str(NO_TEXT_DIR),
    ...
)
```

- [ ] **Step 3: 新增 `deleted_dir` 参数**

```python
index_manager = IndexManager(
    metadata_store=metadata_store,
    vector_store=vector_store,
    memes_dir=str(MEMES_DIR),
    no_text_dir=str(NO_TEXT_DIR),
    deleted_dir=str(MEMES_DELETED_DIR),
    ocr_provider=ocr_service,
    embedding_provider=embedding_service,
    optimizer=image_optimizer,
    keyword_searcher=keyword_searcher,
    ai_matcher=ai_matcher,
)
```

- [ ] **Step 4: 运行语法检查**

```bash
uv run python -m compileall bot/bot.py
```

Expected: Compiled OK.

- [ ] **Step 5: Commit**

```bash
git add bot/bot.py
git commit -m "feat(bot): IndexManager 注入 deleted_dir"
```

---

### Task 12: 更新 API 文档

**Files:**
- Modify: `docs/api/bot/engine/index_manager.md`
- Modify: `docs/api/bot/plugins/_help_text.md`
- Create: `docs/api/bot/plugins/meme_addtag.md`
- Create: `docs/api/bot/plugins/meme_delete.md`
- Create: `docs/api/bot/plugins/meme_info.md`
- Modify: `docs/api/API.md`

#### 步骤

- [ ] **Step 1: 更新 `docs/api/bot/engine/index_manager.md`**

在文档中补充：
- `AddTagResult`、`DeleteResult`、`IndexInfo` dataclass。
- `IndexManager.add_tags(entry_id, tags)`、`delete(entry_ids)`、`info()` 签名与说明。
- `IndexManager.__init__` 新增 `deleted_dir` 参数。

- [ ] **Step 2: 更新 `docs/api/bot/plugins/_help_text.md`**

反映新增的三条命令。

- [ ] **Step 3: 创建三个插件 API 文档**

分别创建 `meme_addtag.md`、`meme_delete.md`、`meme_info.md`，描述命令注册、依赖、流程。

- [ ] **Step 4: 更新 `docs/api/API.md` 目录索引**

在目录结构中新增三个插件文件条目。

- [ ] **Step 5: Commit**

```bash
git add docs/api/
git commit -m "docs(api): 补充 /addtag、/del、/info API 文档"
```

---

### Task 13: 更新 `README.md`

**Files:**
- Modify: `README.md`

#### 步骤

- [ ] **Step 1: 在功能列表中新增三条命令说明**

在 README 的 ✨ 功能部分，按照 `/add` 的格式新增 `/addtag`、 `/del`、 `/info` 小节。

- [ ] **Step 2: 在依赖列表中新增 psutil**

- `psutil` — 系统资源监控（用于 `/info`）

- [ ] **Step 3: 在项目结构中新增 `memes_deleted/` 说明**

- `memes_deleted/`：被 `/del` 删除的表情包备份目录，可手动恢复。

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): 更新 /addtag、/del、/info 命令说明与依赖"
```

---

### Task 14: 全量测试与最终检查

#### 步骤

- [ ] **Step 1: 运行全量单元测试**

```bash
uv run pytest tests/unit -v
```

Expected: all tests PASS.

- [ ] **Step 2: 运行语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: Compiled OK.

- [ ] **Step 3: 检查 pyright 类型错误**

```bash
uv run pyright bot
```

Expected: 0 errors（或仅允许与本次改动无关的既有错误）。

- [ ] **Step 4: 最终 Commit**

```bash
git commit --allow-empty -m "test: /addtag、/del、/info 全量测试通过"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: `/addtag`（Task 3/7）、`/del`（Task 4/8）、`/info`（Task 5/9）、权限分组（Task 7/8/9）、`memes_deleted/`（Task 1/4/11/13）、`psutil`（Task 1/9/13）均已覆盖。
- [x] **Placeholder scan**: 无 TBD/TODO/"实现 later"/"适当处理" 等模糊表述。
- [x] **Type consistency**: `AddTagResult`/`DeleteResult`/`IndexInfo` 在 Task 2 定义，Task 3/4/5 实现，Task 7/8/9 消费，字段命名一致。
