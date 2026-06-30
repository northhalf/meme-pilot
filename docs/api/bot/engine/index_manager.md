# bot/engine/index_manager.py — 索引管理 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

索引管理薄编排层。持有 `MetadataStore` + `VectorStore` + providers，负责压缩→OCR→Embed 管道编排、sync 四阶段（含阶段0跨库一致性修复）、跨库写入一致性、全局锁、并发上限、去重/无文字移图。**不直接写 SQL/Chroma，全部委托两个 Store。**

写入顺序统一「先 sqlite 后 chroma」，`VectorStore.upsert` 失败时回滚 sqlite 写入。OCR 文本在管道内统一去除所有空白字符。去重键 = 去空白后的 `text`，通过 `MetadataStore.get_id_by_text` 判定。

## 模块级函数

### `resolve_unique_filename(target_dir: Path, filename: str) -> Path`

在目标目录中生成不冲突的文件名。若文件已存在则追加数字后缀（如 `cat_2.jpg`）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_dir` | `Path` | 目标目录路径 |
| `filename` | `str` | 原始文件名 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `Path` | 不冲突的完整文件路径 |

---

## 异常

### `IndexCorruptedError(Exception)`

索引数据库结构损坏时抛出。

### `CompressionError(RuntimeError)`

图片压缩失败时抛出。

### `OcrError(RuntimeError)`

OCR 识别失败时抛出。

### `EmbeddingError(RuntimeError)`

Embedding 生成失败时抛出。

---

## Protocol

### `OcrProvider`

```python
class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `ocr` | `image_path: str` — 图片文件路径 | `str` — 识别到的文字 | 异步，对图片执行 OCR 文字识别；返回去除所有空白后的文本 |

---

### `MetadataStoreProtocol`

```python
class MetadataStoreProtocol(Protocol):
    def load(self) -> None: ...
    def entry_count(self) -> int: ...
    def get_all_entries(self) -> dict[int, MemeEntry]: ...
    def get_entry(self, entry_id: int) -> MemeEntry | None: ...
    def get_id_by_text(self, text: str) -> int | None: ...
    def add(self, image_path: str, text: str, speaker: str | None = None, tags: list[str] | None = None) -> int: ...
    def update(self, entry_id: int, *, image_path: str | None = None, text: str | None = None, speaker: str | None = None, tags: list[str] | None = None) -> bool: ...
    def remove(self, entry_id: int) -> bool: ...
```

`IndexManager` 依赖此协议而非具体 `MetadataStore` 实现，便于测试用 Fake 替换。仅声明 `IndexManager` 实际调用的方法子集（load/entry_count/get_all_entries/get_entry/get_id_by_text/add/update/remove）。

---

### `VectorStoreProtocol`

```python
class VectorStoreProtocol(Protocol):
    def load(self) -> None: ...
    def count(self) -> int: ...
    async def upsert(self, entry_id: int, embedding: list[float]) -> None: ...
    async def remove(self, entry_id: int) -> None: ...
    async def remove_many(self, entry_ids: list[int]) -> None: ...
    async def query(self, query_embedding: list[float], n_results: int = 10) -> list[VectorHit]: ...
    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None: ...
```

`IndexManager` 依赖此协议而非具体 `VectorStore` 实现，便于测试用 Fake 替换。仅声明 `IndexManager` 实际调用的方法子集。

---

### `ImageOptimizerProtocol`

```python
class ImageOptimizerProtocol(Protocol):
    async def optimize(self, image_path: str) -> OptimizeResult: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `optimize` | `image_path: str` — 图片文件路径 | `OptimizeResult` | 异步无损压缩，成功后覆盖原文件 |

`IndexManager` 仅调用 `optimize`，依赖此协议而非具体 `ImageOptimizer` 实现。

---

## 数据类

### `SyncResult`

```python
@dataclass
class SyncResult:
    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `added` | `int` | `0` | 本次同步新增的图片数量 |
| `deleted` | `int` | `0` | 本次同步删除的图片数量，指 `memes/` 已不存在的旧图 |
| `deduped` | `int` | `0` | 新图因去重键命中已有条目或其他新图而被删除的数量 |
| `no_text_moved` | `int` | `0` | OCR 无文字被移到 `meme_no_text/` 的数量 |
| `failed` | `list[str]` | `[]` | 处理失败的文件名列表，含新增失败与阶段0重 embed 失败 |

---

### `AddResult`

```python
@dataclass
class AddResult:
    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    moved_to: str | None = None
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `entry_id` | `int \| None` | 必填 | 分配或复用的索引 id；无文字移图场景为 `None` |
| `reason` | `str` | 必填 | 结果类别：`"added"`、`"replaced"`、`"no_text"` |
| `text` | `str` | `""` | OCR 识别文本（无空格）；无文字时为空字符串 |
| `replaced_image_path` | `str \| None` | `None` | `reason="replaced"` 时为被删旧图路径，否则为 `None` |
| `moved_to` | `str \| None` | `None` | `reason="no_text"` 时为移入 `meme_no_text/` 的完整路径，否则为 `None` |

---

## `IndexManager` 类

```python
class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
    DEFAULT_SYNC_CONCURRENCY: int
```

### 类属性

| 属性 | 类型 | 值 | 说明 |
|------|------|------|------|
| `SUPPORTED_EXTENSIONS` | `frozenset[str]` | `{ ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp" }` | 支持的图片扩展名集合 |
| `DEFAULT_SYNC_CONCURRENCY` | `int` | `5` | 并行同步默认并发上限 |

---

### `__init__(metadata_store, vector_store, memes_dir, no_text_dir=None, ocr_provider=None, embedding_provider=None, optimizer=None, sync_concurrency=None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `metadata_store` | `MetadataStoreProtocol` | 必填 | 元数据存储，如 `MetadataStore` 实例 |
| `vector_store` | `VectorStoreProtocol` | 必填 | 向量存储，如 `VectorStore` 实例 |
| `memes_dir` | `str` | 必填 | 表情包图片目录路径 |
| `no_text_dir` | `str \| None` | `None` | 无文字图存放目录；`None` 时取 `memes_dir` 同级的 `meme_no_text/` |
| `ocr_provider` | `OcrProvider \| None` | `None` | OCR 服务注入 |
| `embedding_provider` | `EmbeddingProvider \| None` | `None` | Embedding 服务注入 |
| `optimizer` | `ImageOptimizerProtocol \| None` | `None` | 图片压缩优化器注入，如 `ImageOptimizer` 实例；`None` 时不压缩 |
| `sync_concurrency` | `int \| None` | `None` | `sync_with_filesystem()` 并行处理新增图片时的最大并发数；`None` 或非正数时使用 `DEFAULT_SYNC_CONCURRENCY` |

初始化后需调用 `load()` 加载两个 Store。

---

### `load() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

委托 `MetadataStore.load()` 和 `VectorStore.load()`。启动时必须调用此方法后再使用其他查询或写入方法。

---

### `acquire_lock() -> bool`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 成功获取锁，`False` 锁已被占用 |

非阻塞尝试获取索引更新锁。调用方获取失败时应回复"索引正在更新，请稍后再试"。

---

### `release_lock() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

释放更新锁。未锁定时调用安全。

---

### `is_locked` *(property)*

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 锁被持有，`False` 未锁定 |

---

### `entry_count` *(property)*

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 当前索引中的条目总数（取自 `MetadataStore.entry_count()`） |

---

### `async sync_with_filesystem() -> SyncResult`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `SyncResult` | 新增、删除、去重、无文字移走和失败统计 |

按文件名同步索引与 `memes/` 目录，共四阶段：

- **阶段0 跨库一致性修复**：对齐 sqlite ↔ chroma 的 id 集合。
  - chroma 为空且 sqlite 有数据 → 全量重 embed 后 `VectorStore.rebuild_all`。
  - sqlite 有、chroma 无的 id → 逐条重 embed 并 `upsert`。
  - chroma 有、sqlite 无的 id → 删孤儿向量（`remove_many`）。
- **阶段1 删除**：`memes/` 已不存在的图片，先 sqlite 后 chroma 删除。
- **阶段2 新增**：新图并行 OCR→embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）；正常新增统一「先 sqlite 后 chroma」，`upsert` 失败回滚 sqlite。

新增图片依赖注入的 OCR 与 Embedding provider。

---

### `async add_single_file(filename: str) -> AddResult`

单张图片添加：执行压缩→OCR→Embedding 管道，然后三分类写入（无文字移图 / 去重替换 / 正常新增）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | `memes/` 下的图片文件名 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `AddResult` | 添加/替换/无文字移图结果 |
| **异常** | `CompressionError` | 图片压缩失败 |
| **异常** | `OcrError` | OCR 识别失败 |
| **异常** | `EmbeddingError` | Embedding 生成失败（含 `upsert` 失败回滚后重抛） |
