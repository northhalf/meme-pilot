# 被替换文件归档到 memes_replaced/ 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/add` 去重替换的旧图以及 `/refresh` 去重删除的重复新图移动到 `memes_replaced/` 目录，而不是直接删除。

**Architecture:** 在 `IndexManager` 中新增 `replaced_dir` 归档目录（与 `deleted_dir`、`no_text_dir` 风格一致），新增 `_move_to_replaced` 方法；在 `/add` 替换成功后将旧图归档，在 `/refresh` 去重分支将重复新图归档。保持原文件名，冲突时自动加 `_2`、`_3` 序号。不新增环境变量，不改动用户提示。

**Tech Stack:** Python 3.12, pathlib, shutil, pytest, NoneBot2

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `bot/config.py` | 修改 | 新增 `MEMES_REPLACED_DIR` 常量并导出 |
| `bot/bot.py` | 修改 | `IndexManager` 初始化时传入 `replaced_dir` |
| `bot/engine/index_manager.py` | 修改 | 新增 `replaced_dir` 参数、`_move_to_replaced` 方法、`AddResult.archived_path`；修改 `/add` 与 `/refresh` 去重逻辑 |
| `docker-compose.yml` | 修改 | `bot` 服务新增 `memes_replaced` 卷挂载 |
| `README.md` | 修改 | 项目结构图增加 `memes_replaced/` |
| `CONTEXT.md` | 修改 | 术语表增加"替换归档目录" |
| `docs/api/API.md` | 修改 | 更新 `AddResult` 与 `IndexManager.__init__` 说明 |
| `tests/unit/test_config.py` | 修改 | 新增 `MEMES_REPLACED_DIR` 路径测试 |
| `tests/unit/engine/test_index_manager.py` | 修改 | 更新 `/add` 替换测试，新增 `/refresh` 去重归档与冲突重命名测试 |

---

## Task 1: 新增 `MEMES_REPLACED_DIR` 目录常量

**Files:**
- 修改：`bot/config.py:10-11`
- 修改：`bot/config.py:161-174`
- 测试：`tests/unit/test_config.py:7-19`

- [ ] **Step 1: 在 `bot/config.py` 中新增常量**

```python
MEMES_DELETED_DIR: Path = PROJECT_ROOT / "memes_deleted"
"""被删除表情包的备份目录（可从该目录手动恢复）。"""

MEMES_REPLACED_DIR: Path = PROJECT_ROOT / "memes_replaced"
"""被替换表情包的归档目录。"""
```

- [ ] **Step 2: 将 `MEMES_REPLACED_DIR` 加入 `__all__`**

```python
__all__ = [
    "PROJECT_ROOT",
    "MEMES_DIR",
    "MEMES_DELETED_DIR",
    "MEMES_REPLACED_DIR",
    "DATA_DIR",
    "INDEX_DB_PATH",
    "CHROMA_DIR",
    ...
]
```

- [ ] **Step 3: 更新 `tests/unit/test_config.py` 导入与测试**

在顶部 `from bot.config import (...)` 中加入 `MEMES_REPLACED_DIR`。

新增测试：

```python
def test_memes_replaced_dir_under_root() -> None:
    """MEMES_REPLACED_DIR 位于 <项目根>/memes_replaced。"""
    assert MEMES_REPLACED_DIR == PROJECT_ROOT / "memes_replaced"
```

- [ ] **Step 4: 运行测试验证通过**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 暂存变更，等待用户审核后提交**

```bash
git diff bot/config.py tests/unit/test_config.py
```

（按项目规则，提交需用户审核。）

---

## Task 2: `IndexManager` 支持 `replaced_dir` 并新增归档方法

**Files:**
- 修改：`bot/engine/index_manager.py:332-385`
- 修改：`bot/engine/index_manager.py:1004-1025` 附近
- 修改：`bot/engine/index_manager.py:283-302`

- [ ] **Step 1: `__init__` 新增 `replaced_dir` 参数**

```python
def __init__(
    self,
    metadata_store: MetadataStoreProtocol,
    vector_store: VectorStoreProtocol,
    memes_dir: str,
    no_text_dir: str | None = None,
    deleted_dir: str | None = None,
    replaced_dir: str | None = None,
    ocr_provider: OcrProvider | None = None,
    ...
) -> None:
```

在 `deleted_dir` 处理之后加入：

```python
if replaced_dir is not None:
    self._replaced_dir = Path(replaced_dir)
else:
    self._replaced_dir = Path(memes_dir).parent / "memes_replaced"
```

- [ ] **Step 2: `AddResult` 新增 `archived_path` 字段**

```python
@dataclass
class AddResult:
    """add() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID（int）；无文字移图场景为 None。
        reason: 结果类别：added / replaced / no_text。
        text: OCR 文本（无空格）。
        replaced_image_path: reason="replaced" 时为被替换旧图路径，否则 None。
        archived_path: reason="replaced" 时为旧图归档后的完整路径，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
        speaker: ADD 时写入的说话人（无文字移图时为 None）。
        tags: ADD 时写入的标签列表（无文字移图时为空列表）。
    """

    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    archived_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

- [ ] **Step 3: 新增 `_move_to_replaced` 方法**

在 `_resolve_unique_deleted_path` 方法之后插入：

```python
    def _move_to_replaced(self, filename: str) -> str:
        """将被替换的文件移动到 memes_replaced/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._replaced_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._replaced_dir, filename)
        shutil.move(str(src), str(dst))
        logger.info("已归档被替换文件: %s -> %s", filename, dst)
        return str(dst)
```

- [ ] **Step 4: 运行编译检查**

```bash
uv run python -m compileall bot/engine/index_manager.py
```

Expected: 无语法错误。

- [ ] **Step 5: 暂存变更，等待用户审核后提交**

---

## Task 3: `/add` 去重替换时归档旧图

**Files:**
- 修改：`bot/engine/index_manager.py:1327-1342`

- [ ] **Step 1: 替换 `unlink` 为 `_move_to_replaced`**

将 `_write_entry` 去重替换分支中的：

```python
            # 删旧图（最后删，保证前序失败时旧图仍在）
            if old_image_path and old_image_path != filename:
                (self._memes_dir / old_image_path).unlink(missing_ok=True)
            logger.info(
                "去重替换: id=%s, 旧=%s, 新=%s", old_id, old_image_path, filename
            )
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                text=text,
                replaced_image_path=old_image_path,
                speaker=speaker,
                tags=tags or [],
            )
```

改为：

```python
            # 归档旧图（最后移动，保证前序失败时旧图仍在）
            archived_path: str | None = None
            if old_image_path and old_image_path != filename:
                archived_path = await self._run_sync(
                    self._move_to_replaced, old_image_path
                )
            logger.info(
                "去重替换: id=%s, 旧=%s, 新=%s, archived=%s",
                old_id,
                old_image_path,
                filename,
                archived_path,
            )
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                text=text,
                replaced_image_path=old_image_path,
                archived_path=archived_path,
                speaker=speaker,
                tags=tags or [],
            )
```

- [ ] **Step 2: 更新 `tests/unit/engine/test_index_manager.py` 中的替换测试**

在 `TestAdd.test_add_duplicate_replaces_speaker_and_tags` 末尾追加断言：

```python
        # 验证旧图已被归档到 memes_replaced/
        replaced_dir = Path(index_manager._replaced_dir)
        assert (replaced_dir / "old.jpg").exists()
        # 验证新图仍保留在 memes/
        assert (Path(index_manager._memes_dir) / "new.jpg").exists()
        # 验证 AddResult 携带归档路径
        assert result.archived_path == str(replaced_dir / "old.jpg")
```

注意：需要把 `await index_manager.add("new.jpg", ...)` 的结果保存到 `result` 变量。

在 `TestDataclasses.test_add_result_replaced` 中可保留原断言，或新增：

```python
assert r.archived_path is None
```

在 `TestAdd.test_add_duplicate_upsert_failure_rolls_back_speaker_and_tags` 末尾追加文件存在性断言，确保 upsert 失败时旧图不会被移动：

```python
        # 验证旧图仍在 memes/（upsert 失败时不应移动旧图），新图应已被清理
        assert (Path(index_manager._memes_dir) / "old.jpg").exists()
        assert not (Path(index_manager._memes_dir) / "new.jpg").exists()
```

- [ ] **Step 3: 新增 `/add` 替换冲突重命名测试**

在 `TestAdd` 中新增：

```python
    @pytest.mark.anyio
    async def test_add_duplicate_archives_old_image_with_unique_name(
        self, index_manager: IndexManager
    ) -> None:
        """多次替换同名旧图时，memes_replaced/ 中应生成 _2、_3 等不冲突文件名。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "相同文本"

        index_manager._ocr_provider = ConstantOcrProvider()
        replaced_dir = Path(index_manager._replaced_dir)

        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"1")
        await index_manager.add("old.jpg")

        # 第一次替换
        (Path(index_manager._memes_dir) / "new1.jpg").write_bytes(b"2")
        r1 = await index_manager.add("new1.jpg")
        assert r1.archived_path == str(replaced_dir / "old.jpg")

        # 第二次替换：再次把同名 old.jpg 移入 memes_replaced/
        # 通过手动调用 _move_to_replaced 模拟同名冲突
        (Path(index_manager._memes_dir) / "old.jpg").write_bytes(b"3")
        archived = await index_manager._run_sync(
            index_manager._move_to_replaced, "old.jpg"
        )
        assert archived == str(replaced_dir / "old_2.jpg")
        assert (replaced_dir / "old_2.jpg").exists()
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestAdd -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 暂存变更，等待用户审核后提交**

---

## Task 4: `/refresh` 去重时归档重复新图

**Files:**
- 修改：`bot/engine/index_manager.py:1242-1246`
- 测试：`tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 修改 `_sync_phase2_add` 去重分支**

将：

```python
            if text in winner_keys:
                (self._memes_dir / filename).unlink(missing_ok=True)
                logger.info("新图与已有索引去重，删除新图: filename=%s", filename)
                deduped += 1
                continue
```

改为：

```python
            if text in winner_keys:
                try:
                    archived_path = await self._run_sync(
                        self._move_to_replaced, filename
                    )
                except Exception as exc:
                    logger.error(
                        "去重新图归档失败，跳过该文件: filename=%s, error=%s",
                        filename,
                        exc,
                    )
                    failed.append(filename)
                    continue
                logger.info(
                    "新图与已有索引去重，已归档新图: filename=%s, archived=%s",
                    filename,
                    archived_path,
                )
                deduped += 1
                continue
```

- [ ] **Step 2: 新增 `/refresh` 去重归档测试**

在 `tests/unit/engine/test_index_manager.py` 中新增 `TestRefresh` 类（放在文件末尾 `TestConcurrencyAndDrain` 之后即可）：

```python
class TestRefresh:
    """IndexManager.refresh() 去重归档测试。"""

    @pytest.mark.anyio
    async def test_refresh_dedup_moves_duplicate_to_replaced(
        self, index_manager: IndexManager
    ) -> None:
        """新图与已有条目 OCR 文本重复时，应归档到 memes_replaced/。"""

        class ConstantOcrProvider:
            async def ocr(self, image_path: str) -> str:
                return "重复文本"

        index_manager._ocr_provider = ConstantOcrProvider()
        memes_dir = Path(index_manager._memes_dir)
        replaced_dir = Path(index_manager._replaced_dir)

        # 先建立一条已有索引
        (memes_dir / "old.jpg").write_bytes(b"1")
        await index_manager.add("old.jpg")

        # 再放入一张 OCR 文本相同的新图
        (memes_dir / "new.jpg").write_bytes(b"2")
        result = await index_manager.refresh()

        assert result.deduped == 1
        assert result.added == 0
        assert not (memes_dir / "new.jpg").exists()
        assert (replaced_dir / "new.jpg").exists()
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestRefresh -v
```

Expected: 全部 PASS。

- [ ] **Step 4: 暂存变更，等待用户审核后提交**

---

## Task 5: `bot.py` 传入 `replaced_dir`

**Files:**
- 修改：`bot/bot.py:17-28`
- 修改：`bot/bot.py:113-123`

- [ ] **Step 1: 导入 `MEMES_REPLACED_DIR`**

```python
from bot.config import (
    CHROMA_DIR,
    INDEX_DB_PATH,
    MEMES_DELETED_DIR,
    MEMES_DIR,
    MEMES_REPLACED_DIR,
    PROJECT_ROOT,
    ...
)
```

- [ ] **Step 2: `IndexManager` 初始化传入 `replaced_dir`**

```python
    index_manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=memes_dir,
        deleted_dir=str(MEMES_DELETED_DIR),
        replaced_dir=str(MEMES_REPLACED_DIR),
        ocr_provider=ocr_service,
        ...
    )
```

- [ ] **Step 3: 运行编译检查**

```bash
uv run python -m compileall bot/bot.py
```

Expected: 无语法错误。

- [ ] **Step 4: 暂存变更，等待用户审核后提交**

---

## Task 6: 部署与文档同步

**Files:**
- 修改：`docker-compose.yml:62-67`
- 修改：`README.md:283-344` 附近
- 修改：`CONTEXT.md:23-25` 附近
- 修改：`docs/api/API.md:147-191` 与 `docs/api/API.md:55-105` 附近

- [ ] **Step 1: `docker-compose.yml` 新增卷挂载**

在 `bot` 服务的 `volumes:` 列表中加入：

```yaml
      - ./memes_replaced:/app/memes_replaced
```

- [ ] **Step 2: `README.md` 项目结构同步**

在项目结构代码块中 `meme_no_text/` 之后增加：

```text
├── memes_replaced/          # 被替换表情包的归档目录
```

并在“本地表情包库”架构图下同步增加 `memes_replaced/` 说明。

- [ ] **Step 3: `CONTEXT.md` 增加术语**

在“删除备份目录”段落后新增：

```markdown
| **替换归档目录** | `memes/` 同级的 `memes_replaced/` 目录；`/add` 去重替换旧图或 `/refresh` 去重删除重复新图时，被替换的图片文件会被移动到此目录，保留原文件名（冲突时追加 `_n` 序号），可手动恢复 |
```

- [ ] **Step 4: `docs/api/API.md` 更新接口说明**

在 `AddResult` 数据类中增加 `archived_path` 字段说明：

```python
@dataclass
class AddResult:
    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    archived_path: str | None = None  # reason="replaced" 时填充
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

在 `IndexManager.__init__` 签名中增加 `replaced_dir: str | None = None`。

- [ ] **Step 5: 运行文档编译检查（仅 Python 代码块）**

```bash
uv run python -m compileall bot
```

Expected: 无语法错误。

- [ ] **Step 6: 暂存变更，等待用户审核后提交**

---

## Task 7: 全量单元测试回归

**Files:** 无新增文件

- [ ] **Step 1: 运行相关单元测试**

```bash
uv run pytest tests/unit/test_config.py tests/unit/engine/test_index_manager.py tests/unit/plugins/test_meme_add.py tests/unit/plugins/test_meme_refresh.py -v
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行全量单元测试**

```bash
uv run pytest tests/unit -v
```

Expected: 全部 PASS。

- [ ] **Step 3: 运行语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 无语法错误。

- [ ] **Step 4: 向用户展示完整 diff，等待审核**

```bash
git diff --stat
git diff bot/config.py bot/bot.py bot/engine/index_manager.py docker-compose.yml README.md CONTEXT.md docs/api/API.md tests/unit/test_config.py tests/unit/engine/test_index_manager.py
```

（按项目规则，提交需用户审核。）

---

## 自检清单

- [ ] `MEMES_REPLACED_DIR` 已定义并导出。
- [ ] `IndexManager` 支持 `replaced_dir` 参数，默认行为正确。
- [ ] `_move_to_replaced` 使用 `resolve_unique_filename` 处理冲突。
- [ ] `/add` 替换分支在 chroma upsert 成功后归档旧图。
- [ ] `/refresh` 去重分支归档重复新图。
- [ ] `AddResult.archived_path` 已填充。
- [ ] Docker 卷已挂载。
- [ ] README、CONTEXT.md、API.md 已同步。
- [ ] 单元测试覆盖替换归档与冲突重命名。
- [ ] 全量单元测试通过。
