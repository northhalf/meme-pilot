# OCR 文本去重与无文字图排除 — 设计文档

> 日期：2026-06-21
> 状态：设计完成，待实现
> 关联模块：`bot/engine/index_manager.py`

## 1. 概述

在 `sync_with_filesystem`（启动同步与 `/refresh`）以及 `add_entry`（`/add`）处理图片时，新增对 OCR 文本的完全相同检测与无文字图排除。

核心行为：

- **去重**：以「去除所有空白字符后的 OCR 文本」为去重键，检测是否已存在完全相同的图片。命中时删除被替换方的索引记录与图片文件。
- **无文字排除**：OCR 文本去除所有空白后为空的图片，不进入索引，移动到 `memes/` 同级的 `meme_no_text/` 目录并发出警告。

### 1.1 两个场景的赢家规则

| 场景 | 触发 | 赢家 | 被替换方处理 |
|------|------|------|--------------|
| `add_entry`（单图，`/add`） | 新图去重键命中已有条目 | **新图赢** | 删旧记录 + 删旧图文件，复用旧 ID 写入新图 |
| `sync_with_filesystem` 新增阶段 | 新图去重键命中已有条目 | **现有条目赢** | 删新图文件，不新增 |
| `sync_with_filesystem` 新增阶段 | 两张新图互重 | **文件名升序靠前的赢** | 删靠后的新图文件，不新增 |

`add` 与 `sync` 的赢家方向相反：`add` 是用户主动添加意图，新图优先；`sync` 是批量扫描，保留已有/靠前者避免重复 OCR 浪费 API。

## 2. 去重键定义

**去重键 = 去除所有空白字符后的 OCR 文本。**

```python
def dedup_key(text: str) -> str:
    return "".join(text.split())
```

`str.split()` 无参形式按任意空白（含半角空格、全角空格 `　`、制表符 `\t`、换行 `\n`/`\r`、`\f`、`\v`）分割，`"".join` 去除所有空格。

**与现有 `normalize_text` 的区别**：`normalize_text` 用 `" ".join(text.split())` 保留单词间**单个**空格，"加班 好累" 与 "加班好累" 得到不同结果；`dedup_key` 用 `"".join(...)` 完全去空格，二者相同。去重键比 `normalize_text` 更严格，符合「去除所有空格后相同」的判定需求。

**不落盘**：去重键实时计算，不写入 `index.json`。`index.json` 的 entry 仍只存 `filename`、`text`、`text_hash`（PRD §3.5 约定 v1.0 不保存额外元数据）。

验证用例：

```
dedup_key("一只猫 抓蝴蝶")    → "一只猫抓蝴蝶"
dedup_key("一只猫  抓蝴蝶")   → "一只猫抓蝴蝶"   # 与上行同键
dedup_key("加班　好累")       → "加班好累"        # 全角空格去除
dedup_key("  ")               → ""
dedup_key("")                 → ""
```

## 3. 工具函数与目录配置

### 3.1 模块级工具函数（新增，与 `normalize_text`/`compute_text_hash` 并列）

```python
def dedup_key(text: str) -> str:
    """计算 OCR 文本的去重键。

    去除所有空白字符（含半角/全角空格、制表符、换行等）后的纯文本。
    比 normalize_text 更严格：normalize_text 保留单词间单空格，
    dedup_key 完全去除空格，用于判定「是否完全相同的图片」。

    Args:
        text: 原始 OCR 文本。

    Returns:
        去除所有空白字符后的文本（可能为空字符串）。
    """
    return "".join(text.split())


def is_blank_text(text: str) -> bool:
    """判断 OCR 文本是否为「无文字」。

    去除所有空白后为空即判定无文字。

    Args:
        text: OCR 文本。

    Returns:
        True 表示无文字（需移到 meme_no_text/ 不进索引）。
    """
    return dedup_key(text) == ""


def _resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    与 /add 文件名冲突策略一致：若 filename 已存在，
    在基名后追加 _2、_3... 直到不冲突。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。

    Returns:
        目标目录下不冲突的完整路径。
    """
```

`_resolve_unique_filename` 实现：用 `itertools.count(2)` 试探 `stem_{n}.suffix`，第一个不存在的即为结果。

### 3.2 目录配置

`IndexManager.__init__` 新增可选参数：

```python
def __init__(
    self,
    data_dir: str = "data",
    memes_dir: str = "memes",
    ocr_provider: OcrProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    sync_concurrency: int | None = None,
    no_text_dir: str | None = None,
) -> None:
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `no_text_dir` | `str \| None` | `None` | 无文字图存放目录；`None` 时取 `memes_dir` 同级的 `meme_no_text/`（即 `Path(memes_dir).parent / "meme_no_text"`）。插件层无需显式传入 |

### 3.3 import 变更

`index_manager.py` 顶部新增：

```python
import itertools
import shutil
```

（`os`/`pathlib`/`hashlib`/`ujson`/`logging`/`dataclasses`/`typing` 已有）

## 4. 数据类

### 4.1 `AddResult`（新增）

`add_entry` 的返回类型，替代当前的 `str`：

```python
@dataclass
class AddResult:
    """add_entry() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID；无文字移图场景为 None。
        reason: 结果类别，取值：
            "added"   - 正常新增；
            "replaced"- 去重命中已有条目，已复用旧 ID 覆盖；
            "no_text" - OCR 无文字，已移至 meme_no_text/ 不进索引。
        replaced_filename: reason="replaced" 时为被删旧图文件名，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
    """

    entry_id: str | None
    reason: str
    replaced_filename: str | None = None
    moved_to: str | None = None
```

三种场景对应：

| 场景 | entry_id | reason | replaced_filename | moved_to |
|------|----------|--------|-------------------|----------|
| 正常新增 | 新 id | `"added"` | None | None |
| 去重覆盖 | 旧 id（复用） | `"replaced"` | 旧图文件名 | None |
| 无文字移图 | None | `"no_text"` | None | `meme_no_text/...` |

插件层据此回复用户：
- `"replaced"` → "检测到重复表情包，已用新图替换并删除原图 {replaced_filename}"
- `"no_text"` → "未识别到文字，已移至 meme_no_text/ 不入索引"

### 4.2 `SyncResult`（扩展，加 2 字段）

```python
@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因去重键命中已有条目/其他新图而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)
```

**计数语义严格区分，不重叠**：

| 字段 | 含义 |
|------|------|
| `deleted` | memes/ 里已不存在的旧图片（删除阶段，不变） |
| `deduped` | 新增阶段新图被判重后**删除新图文件**的数量（含「新图 vs 已有条目」和「新图 vs 新图」） |
| `no_text_moved` | 新图 OCR 无文字被移到 `meme_no_text/` 的数量 |
| `added` | 真正写入索引的新图数量 |
| `failed` | OCR/embedding 调用失败的文件名（不变） |

一次「去重」只让 `deduped++`，不计入 `added`/`deleted`；一次「无文字」只让 `no_text_moved++`，不计入 `added`。被去重删除的新图文件不计入 `deleted`（`deleted` 专指 memes/ 已不存在的旧图清理）。

`/refresh` 回复摘要扩为：「新增 N 张，删除 M 张，去重 D 张，无文字移走 T 张，失败 F 张」。

## 5. `add_entry` 改造

### 5.1 签名变更

```python
def add_entry(
    self,
    filename: str,
    text: str,
    embedding: list[float],
) -> AddResult:
```

返回类型 `str` → `AddResult`。参数不变，插件层调用方式不变，只改读返回值。

### 5.2 执行流程（三选一）

```
add_entry(filename, text, embedding)
  │
  ├─ is_blank_text(text) == True
  │    → _move_to_no_text(filename) → moved_to 路径
  │    → 不写索引，不写 embeddings
  │    → return AddResult(entry_id=None, reason="no_text", moved_to=...)
  │
  ├─ key = dedup_key(text); _find_entry_by_dedup_key(key) 命中 old_id
  │    → 旧记录 old = _entries[old_id]; old_filename = old["filename"]
  │    → 删除旧图文件 memes/<old_filename>（missing_ok）
  │    → 原地覆盖旧记录：
  │        _entries[old_id] = {"filename": filename, "text": text, "text_hash": new_hash}
  │        _embeddings[old_id] = {"text_hash": new_hash, "embedding": embedding}
  │    → save_index() + save_embeddings()
  │    → return AddResult(entry_id=old_id, reason="replaced",
  │                       replaced_filename=old_filename)
  │
  └─ 正常新增
       → entry_id = _find_next_id()
       → _entries[entry_id] = {...}; _embeddings[entry_id] = {...}
       → save_index() + save_embeddings()
       → return AddResult(entry_id=entry_id, reason="added")
```

### 5.3 新增私有方法

```python
def _find_entry_by_dedup_key(self, key: str) -> str | None:
    """按去重键查找已有条目 ID。

    线性扫描 _entries，返回第一个 dedup_key(text) == key 的条目 ID。
    正常情况下去重键唯一（add/sync 已保证不引入重复键），
    返回第一个匹配即可。

    Args:
        key: dedup_key 计算结果。

    Returns:
        匹配的条目 ID，无匹配返回 None。
    """


def _move_to_no_text(self, filename: str) -> str:
    """将无文字图片移动到 meme_no_text/ 目录。

    自动创建 meme_no_text/ 目录；目标同名时追加序号。
    移动失败（如跨设备）时 shutil.move 内部自动回退为复制+删除。

    Args:
        filename: memes/ 下的源文件名。

    Returns:
        移入 meme_no_text/ 后的完整路径字符串。
    """
```

`_move_to_no_text` 实现：

```python
def _move_to_no_text(self, filename: str) -> str:
    src = self._memes_dir / filename
    self._no_text_dir.mkdir(parents=True, exist_ok=True)
    dst = _resolve_unique_filename(self._no_text_dir, filename)
    shutil.move(str(src), str(dst))
    logger.warning("OCR 未识别到文字，已移至无文字目录: %s -> %s", filename, dst)
    return str(dst)
```

### 5.4 关键决策

- **去重覆盖用新 embedding**：去重键只保证"去空格后相同"，`normalize_text` 后的文本可能不同（空格数量不同），`text_hash` 与 embedding 都可能不同。新图是新 OCR 结果，embedding 是对新 text 生成的，覆盖时必须用新 text_hash + 新 embedding，否则 `text_hash` 与 `text` 不一致会触发下次 sync 重建阶段无谓重算。
- **删旧图 `missing_ok=True`**：旧图文件可能已被用户手动删除但索引还在（PRD §5 边界），此时仍应完成索引替换，不因旧图文件不存在而失败。
- **save 时机**：只有「替换」和「新增」分支写盘，各调用 `save_index()` + `save_embeddings()`（与现有 `add_entry`/`remove_entry` 的"单条操作即落盘"风格一致）；「无文字」分支不写盘（索引未变）。

## 6. `sync_with_filesystem` 新增阶段改造

只改新增阶段 `_sync_additions`；删除阶段、重建阶段不变。`_process_new_file`（OCR→embed）不变，仍返回 `(filename, text, embedding)`，无文字/去重判定都在 `_sync_additions` 串行分类阶段做。

### 6.1 改造后流程

核心变化：新增阶段在拿到 OCR 文本后，逐图做三分类（无文字 / 去重 / 正常），而非无脑全部写入。**去重判定基于已稳定的「赢家集合」增量推进**——已有条目 + 已确定保留的新图共同构成判定基准。

```python
async def _sync_additions(
    self,
    existing_files: set[str],
    filename_to_id: dict[str, str],
    failed: list[str],
) -> tuple[int, int, int]:
    """新增阶段：并行 OCR→embed，再按文件名升序串行三分类。

    Returns:
        (added, deduped, no_text_moved) 三元组。
    """
    import asyncio

    new_files = sorted(f for f in existing_files if f not in filename_to_id)
    if not new_files:
        return (0, 0, 0)

    raw_results = await asyncio.gather(
        *(self._process_new_file(fn) for fn in new_files),
        return_exceptions=True,
    )

    # 成功项以 filename 为 key 收集
    success_by_name: dict[str, tuple[str, list[float]]] = {}
    for filename, result in zip(new_files, raw_results):
        if isinstance(result, BaseException):
            logger.error("处理图片失败: filename=%s, error=%s", filename, result)
            failed.append(filename)
        else:
            _, text, embedding = result
            success_by_name[filename] = (text, embedding)

    # 赢家集合：初始 = 已有条目的去重键（现有条目天然是赢家）
    winner_keys: set[str] = {
        dedup_key(entry.get("text", "")) for entry in self._entries.values()
    }

    added = deduped = no_text_moved = 0

    # 按文件名升序串行分类，决定新图互重时的赢家
    for filename in sorted(success_by_name.keys()):
        text, embedding = success_by_name[filename]

        if is_blank_text(text):
            self._move_to_no_text(filename)
            no_text_moved += 1
            continue

        key = dedup_key(text)
        if key in winner_keys:
            # 命中已有条目 或 命中本轮更靠前的保留新图 → 删新图
            new_path = self._memes_dir / filename
            new_path.unlink(missing_ok=True)
            logger.info("新图与已有索引去重，删除新图: filename=%s", filename)
            deduped += 1
            continue

        # 正常新增
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
        winner_keys.add(key)  # 本图成为赢家，后续同键新图判重
        added += 1
        logger.info("新增图片已加入索引: id=%s, filename=%s", entry_id, filename)

    return (added, deduped, no_text_moved)
```

### 6.2 赢家规则统一性

两类去重用同一 `winner_keys` 集合处理，规则天然统一：

- **新图 vs 已有条目**：`winner_keys` 初始含已有条目键 → 新图命中即删新图（**现有条目赢**）。
- **新图 vs 新图**：靠前新图先入 `winner_keys` → 靠后新图命中即删靠后新图（**文件名升序靠前的赢**）。

两种情况都是"删新图文件、不写索引、`deduped++`"，代码路径完全一致，靠 `winner_keys` 的初始化与增量自然区分。

### 6.3 `sync_with_filesystem` 主方法适配

```python
async def sync_with_filesystem(self) -> SyncResult:
    self._memes_dir.mkdir(parents=True, exist_ok=True)

    existing_files = self._scan_meme_files()
    filename_to_id = self._build_filename_to_id()

    deleted_count = self._sync_deletions(existing_files, filename_to_id)

    failed: list[str] = []
    rebuild_count = await self._sync_rebuilds(failed)

    added_count, deduped_count, no_text_count = await self._sync_additions(
        existing_files, filename_to_id, failed
    )

    self._persist_sync_results(added_count, deleted_count, rebuild_count)

    logger.info(
        "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 重建=%d, 失败=%d",
        added_count, deleted_count, deduped_count, no_text_count,
        rebuild_count, len(failed),
    )
    return SyncResult(
        added=added_count,
        deleted=deleted_count,
        deduped=deduped_count,
        no_text_moved=no_text_count,
        failed=failed,
    )
```

`_sync_additions` 返回值从 `int` 改为 `(int, int, int)` 元组（added, deduped, no_text_moved）。

### 6.4 `_persist_sync_results` 写盘判定不变

当前依据 `added > 0 or deleted > 0 or rebuild > 0` 决定写盘。去重和无文字场景：

- 去重删新图：新图从未进索引（内存 `_entries` 未变），不产生索引记录变更。
- 无文字移图：同样不产生索引记录变更。

所以 `_persist_sync_results` **写盘判定不变**：仍按 `added/deleted/rebuild` 三个计数决定是否写索引文件。去重和无文字只动 `memes/` 文件，不动索引记录，无需触发写盘。

> 边界：去重删除的新图文件若 OCR 成功但被判重，它已不在 `memes/`，下次 sync 不会再扫到——这正是期望行为（避免重复图反复 OCR 浪费 API）。

## 7. 文件副作用与冲突处理

### 7.1 `meme_no_text/` 目录

- **位置**：`memes/` 同级，即 `Path(self._memes_dir).parent / "meme_no_text"`。部署中 `memes/` 是 Docker 卷 `./memes`，则 `meme_no_text/` 即宿主机 `./meme_no_text`，用户可直接查看。
- **创建**：`_move_to_no_text` 内 `self._no_text_dir.mkdir(parents=True, exist_ok=True)`，惰性创建。
- **挂载**：需在 `docker-compose.yml` 增加 `./meme_no_text:/app/meme_no_text` 卷映射，否则容器内移图后宿主机看不到。

### 7.2 移图与删图

- **移无文字图**（`_move_to_no_text`）：`shutil.move(str(src), str(dst))`，跨设备自动复制+删源；落点用 `_resolve_unique_filename` 处理冲突。
- **删旧图**（add 去重覆盖）：`old_path.unlink(missing_ok=True)`，旧图可能已被外部删除，仍完成索引替换。
- **删新图**（sync 去重）：`new_path.unlink(missing_ok=True)`，`missing_ok=True` 仅为防御并发外部删除。

### 7.3 事务性

`add_entry` 和 `sync_with_filesystem` 都不是严格事务——文件删除与索引写入是两步。遵循"先删图、后写索引"顺序：若写索引失败，最坏情况是图已删但索引未更新（下次 sync 会因图片缺失触发删除阶段清理索引），与现有"索引与文件可能短暂不一致"的容错模型一致，不引入新的不一致风险。

## 8. 文档更新清单

| 文件 | 改动内容 |
|------|----------|
| `docs/PRD.md` §3.3 `/add` | 增加去重与无文字处理：新图去重键命中已有表情包时删除原图并用新图替换（复用旧 ID）；OCR 无文字时移至 `meme_no_text/`、不进索引、回复"未识别到文字" |
| `docs/PRD.md` §3.5 第 12 条 | `/refresh` 回复摘要扩为「新增 N、删除 M、去重 D、无文字移走 T、失败 F」 |
| `docs/PRD.md` §3.5 增量更新 | 新增条目：新增图片 OCR 后按去重键去重——与已有条目或其他新图去重键相同的新图被删除（保留已有/文件名靠前者）；无文字图移至 `meme_no_text/` 不进索引 |
| `docs/PRD.md` §5 边界情况 | **改写**「OCR 无文字」行：原"text 写'未识别到文字'"→ 新"移至 `meme_no_text/`、不进索引、warning"；新增「新图与已有条目 OCR 去重键相同」行 |
| `docs/PRD.md` §6 项目结构 | `data/` 同级新增 `meme_no_text/` 目录说明 |
| `docs/PRD.md` §4.2 部署 / docker-compose | 新增 `./meme_no_text:/app/meme_no_text` 卷映射 |
| `CONTEXT.md` 术语表 | 新增「去重键」「无文字目录(meme_no_text)」术语；修订「按文件名同步的增量刷新」补充去重与无文字排除 |
| `docker-compose.yml` | bot 服务 `volumes` 增加 `./meme_no_text:/app/meme_no_text` |
| `docs/api/API.md` §1.4 | `SyncResult` 加 `deduped`、`no_text_moved` 字段说明 |
| `docs/api/API.md` §1.5 `add_entry` | 返回类型 `str` → `AddResult`；新增 `AddResult` 数据类章节；新增 `dedup_key`/`is_blank_text`/`_resolve_unique_filename`/`_find_entry_by_dedup_key`/`_move_to_no_text` 接口说明；`__init__` 加 `no_text_dir` 参数 |
| `docs/api/API.md` §1.5 `sync_with_filesystem` | 更新阶段说明：新增阶段三分类（无文字/去重/正常）；返回 `SyncResult` 新增两字段 |
| `docs/process.md` | `index_manager.py` 条目追加：OCR 文本去重（去空格键）、无文字图移至 `meme_no_text/` |
| `README.md` | 目录结构补 `meme_no_text/`；部署步骤补 `docker-compose.yml` 卷说明 |
| `CLAUDE.md` 索引格式要点 | 补一句：去重键实时计算不落盘；无文字图不进索引移至 `meme_no_text/` |

`.env.example` 无新增环境变量（`no_text_dir` 默认推导，不暴露）。

## 9. 测试要点

仓库尚无测试框架，先列要点，待引入框架后落实于 `tests/unit/engine/`：

1. **`dedup_key` / `is_blank_text`**：`"加班 好累"` 与 `"加班好累"` 同键；全角空格 `"　"` 去除；纯空白 → `is_blank_text=True`
2. **`add_entry` 正常新增** → `AddResult(entry_id, "added")`，索引/embeddings 写入
3. **`add_entry` 去重覆盖**：预先 `add_entry("a.jpg", "加班 好累", emb)`，再 `add_entry("b.jpg", "加班好累", emb2)` → `reason="replaced"`，`entry_id` 复用 a 的 id，`replaced_filename="a.jpg"`，磁盘 `a.jpg` 删除、`b.jpg` 存在，索引里该 id 的 filename 变为 `b.jpg`
4. **`add_entry` 无文字**：`add_entry("c.jpg", "   ", emb)` → `reason="no_text"`，`entry_id=None`，`moved_to` 指向 `meme_no_text/c.jpg`，索引无该条目
5. **`add_entry` 无文字 + `meme_no_text/` 同名冲突** → 落点 `c_2.jpg`
6. **`sync_with_filesystem` 新图 vs 已有条目去重**：索引已有 `old.jpg`("加班")，放入 `new.jpg`("加 班") → sync 后 `deduped=1, added=0`，`new.jpg` 被删，`old.jpg` 保留，索引不变
7. **`sync_with_filesystem` 新图互重**：放入 `a.jpg`/`b.jpg`（同 OCR 文本）→ `added=1, deduped=1`，文件名靠前的 `a.jpg` 保留并进索引，`b.jpg` 被删
8. **`sync_with_filesystem` 无文字新图** → `no_text_moved=1`，移至 `meme_no_text/`，不进索引
9. **`sync_with_filesystem` 计数不重叠**：一次去重只 `deduped++`，不计 `added`/`deleted`；混合场景（2 新增 + 1 去重 + 1 无文字）各计数独立
10. **旧占位条目保留**：预置 `text="未识别到文字"` 的旧条目，sync 后仍在索引中（不清理）

验证命令（仓库尚无测试框架，先用编译检查兜底）：

```bash
uv run python -m compileall bot
```

## 10. 范围与非目标

**本设计范围内**：

- `index_manager.py` 的 `add_entry`、`sync_with_filesystem` 新增阶段改造
- 新增工具函数 `dedup_key`/`is_blank_text`/`_resolve_unique_filename`
- 新增私有方法 `_find_entry_by_dedup_key`/`_move_to_no_text`
- `AddResult` 数据类、`SyncResult` 扩展两字段
- `__init__` 加 `no_text_dir` 参数
- `docker-compose.yml` 加 `meme_no_text` 卷映射
- PRD/CONTEXT/API/process/README/CLAUDE.md 文档同步

**非目标（明确不做）**：

- 不为误删风险加保护（两张不同图但 OCR 文字恰好相同会被当重复删图，符合需求）
- 不清理 `index.json` 里已有的"未识别到文字"占位条目（sync 重建阶段不重新 OCR，旧占位条目保留）
- 不把去重键落盘到 `index.json`（保持 v1.0 不保存额外元数据）
- 不改动删除阶段、重建阶段、`_process_new_file`、锁管理、`_atomic_write` 等现有逻辑
- 不实现插件层（`meme_add.py`/`meme_refresh.py` 等仍待后续单独实现，本设计只定 `add_entry` 返回接口供其消费）
