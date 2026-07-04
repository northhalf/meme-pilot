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

### `RefreshInProgressError(RuntimeError)`

索引刷新进行中，新的写入请求应被拒绝。

### `IndexAddCancelledError(RuntimeError)`

/add 任务因刷新或关闭而被取消。

### `DuplicateTextError(RuntimeError)`

edit_text 要修改的文本已被其他条目使用。

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
    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = None,
        text: str | None = None,
        speaker: str | None = None,  # None means "clear speaker"; _UNSET=no-change internally
        tags: list[str] | None = None,
    ) -> bool: ...
    def remove(self, entry_id: int) -> bool: ...
```

`IndexManager` 依赖此协议而非具体 `MetadataStore` 实现，便于测试用 Fake 替换。仅声明 `IndexManager` 实际调用的方法子集（load/entry_count/get_all_entries/get_entry/get_id_by_text/add/update/remove）。

实现层面，`MetadataStore.update()` 使用内部哨兵 `_UNSET` 作为 `image_path`/`text`/`speaker` 的默认值以区分「不修改」与「清空为 NULL」；协议层保持 `None` 默认并以上述注释说明语义。

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

### `EditTextResult`

```python
@dataclass
class EditTextResult:
    entry_id: int
    old_text: str
    new_text: str
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 被修改的条目 id |
| `old_text` | `str` | 修改前的 OCR 文本 |
| `new_text` | `str` | 修改后的 OCR 文本 |

---

### `SetSpeakerResult`

```python
@dataclass
class SetSpeakerResult:
    entry_id: int
    old_speaker: str | None
    new_speaker: str | None
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 被修改的条目 id |
| `old_speaker` | `str \| None` | 修改前的说话人；为空时为 `None` |
| `new_speaker` | `str \| None` | 修改后的说话人；为空时为 `None` |

---

## `IndexManager` 类

```python
class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
    read_timeout: float
    add_user_timeout: float

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: str,
        no_text_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizer | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
    ) -> None

    def load(self) -> None

    async def search(self, keyword: str) -> list[SearchResult]
    # 持读锁调用 KeywordSearcher；空库返回 []；超时抛 asyncio.TimeoutError

    async def ai_match(self, description: str) -> AIMatchResult | None
    # 锁外 embed，持读锁调用 AIMatcher.match_with_vector()；超时抛 asyncio.TimeoutError

    async def add(self, filename: str) -> AddResult
    # FIFO 入队；refresh 期间抛 RefreshInProgressError；关闭时抛 IndexAddCancelledError；
    # 内部由 Add Worker 串行处理（压缩 → OCR → embed → Write Worker 写库）

    async def edit_text(self, entry_id: int, new_text: str) -> EditTextResult
    # 修改指定条目的 OCR 文本；锁外 embed，Write Worker 串行写入；
    # raises RefreshInProgressError, DuplicateTextError, ValueError, EmbeddingError, IndexAddCancelledError

    async def set_speaker(self, entry_id: int, speaker: str | None) -> SetSpeakerResult
    # 设置或清空指定条目的 speaker；仅更新 sqlite 元数据，无需 embed；
    # raises RefreshInProgressError, ValueError, IndexAddCancelledError

    async def refresh(self) -> SyncResult
    # 独占写锁执行同步；运行期间新的 add/refresh 被拒绝

    async def close(self) -> None
    # 取消 workers，清空 pending，关闭两个 Store
```
