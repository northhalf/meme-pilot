# bot/engine/index_manager.py — 索引管理 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 模块级函数

### `normalize_text(text: str) -> str`

规范化 OCR 文本。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | 原始 OCR 文本，可能含多余空白 |
| **返回** | `str` | 去除首尾空白、合并连续空白为单个空格后的文本 |
| **异常** | 无 | |

```python
normalize_text("  一只猫  抓蝴蝶  ")  # → "一只猫 抓蝴蝶"
normalize_text("a\t\tb\n\nc")         # → "a b c"
normalize_text("")                    # → ""
```

---

### `compute_text_hash(text: str) -> str`

计算规范化文本的 SHA-256 哈希。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | 待哈希的文本，内部先调用 `normalize_text` |
| **返回** | `str` | 格式 `"sha256:<64位十六进制>"` |
| **异常** | 无 | |

```python
compute_text_hash("hello")  # → "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
```

---

### `dedup_key(text: str) -> str`

计算 OCR 文本的去重键。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | 原始 OCR 文本 |
| **返回** | `str` | 去除所有空白字符后的文本，可能为空字符串 |
| **异常** | 无 | |

比 `normalize_text` 更严格：`normalize_text` 保留单词间单空格，`dedup_key` 完全去除空格，用于判定「是否完全相同的图片」。实时计算，不落盘。

```python
dedup_key("加班 好累")   # → "加班好累"
dedup_key("加班好累")    # → "加班好累"
dedup_key("   ")         # → ""
```

---

### `is_blank_text(text: str) -> bool`

判断 OCR 文本是否为「无文字」。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | OCR 文本 |
| **返回** | `bool` | `True` 表示去除所有空白后为空，需移到 `meme_no_text/` 且不进索引 |
| **异常** | 无 | |

等价于 `dedup_key(text) == ""`。

---

### `resolve_unique_filename(target_dir: Path, filename: str) -> Path`

在目标目录中生成不冲突的文件名。若文件已存在则追加数字后缀（如 `cat(1).jpg`）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_dir` | `Path` | 目标目录路径 |
| `filename` | `str` | 原始文件名 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `Path` | 不冲突的完整文件路径 |
| **异常** | 无 | |

---

### `encode_embedding(embedding: list[float]) -> str`

将 float32 向量编码为 base64 字符串（big-endian），用于 embeddings.json 压缩存储。

| 参数 | 类型 | 说明 |
|------|------|------|
| `embedding` | `list[float]` | float32 向量值列表 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `str` | base64 编码字符串（5464 字符/1024 维）|
| **异常** | `struct.error` | 空列表 |

---

### `decode_embedding(data: str) -> list[float]`

将 base64 字符串解码为 float32 向量。

| 参数 | 类型 | 说明 |
|------|------|------|
| `data` | `str` | base64 编码的 float32 二进制数据 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `list[float]` | float32 值列表 |
| **异常** | `binascii.Error` | base64 格式错误 |

---

## 异常

### `IndexCorruptedError(Exception)`

`index.json` 结构损坏或缺少必要字段时抛出。

无额外属性，使用 `str(exc)` 获取错误消息。

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
| `ocr` | `image_path: str` — 图片文件路径 | `str` — 识别到的文字 | 异步，对图片执行 OCR 文字识别 |

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
| `failed` | `list[str]` | `[]` | 处理失败的文件名列表，含新增失败与 embedding 重建失败 |

---

### `AddResult`

```python
@dataclass
class AddResult:
    entry_id: str | None
    reason: str
    text: str = ""
    replaced_filename: str | None = None
    moved_to: str | None = None
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `entry_id` | `str \| None` | 必填 | 分配或复用的索引 ID；无文字移图场景为 `None` |
| `reason` | `str` | 必填 | 结果类别：`"added"`、`"replaced"`、`"no_text"` |
| `text` | `str` | `""` | OCR 识别文本；无文字时为空字符串 |
| `replaced_filename` | `str \| None` | `None` | `reason="replaced"` 时为被删旧图文件名，否则为 `None` |
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

### `__init__(data_dir="data", memes_dir="memes", ocr_provider=None, embedding_provider=None, sync_concurrency=None, no_text_dir=None, optimizer=None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `data_dir` | `str` | `"data"` | 索引文件目录路径 |
| `memes_dir` | `str` | `"memes"` | 表情包图片目录路径 |
| `ocr_provider` | `OcrProvider \| None` | `None` | OCR 服务注入 |
| `embedding_provider` | `EmbeddingProvider \| None` | `None` | Embedding 服务注入 |
| `sync_concurrency` | `int \| None` | `None` | `sync_with_filesystem()` 并行处理新增图片时的最大并发数；`None` 或非正数时使用 `DEFAULT_SYNC_CONCURRENCY` |
| `no_text_dir` | `str \| None` | `None` | 无文字图存放目录；`None` 时取 `memes_dir` 同级的 `meme_no_text/` |
| `optimizer` | `ImageOptimizer \| None` | `None` | 图片压缩优化器注入；`None` 时不压缩 |

初始化后需调用 `load()` 加载磁盘数据。

---

### `load() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `IndexCorruptedError` | `index.json` 结构损坏或缺少必要字段 |

加载并校验 `data/index.json` 和 `data/embeddings.json`。启动时必须调用此方法后再使用其他查询或写入方法。

---

### `validate_index(data: object) -> None` *(静态方法)*

| 参数 | 类型 | 说明 |
|------|------|------|
| `data` | `object` | 解析后的 JSON 数据 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `IndexCorruptedError` | 缺少 `version`、`entries` 或类型不符 |

校验 `index.json` 顶层结构。

---

### `get_entries() -> dict[str, dict[str, str]]`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, dict[str, str]]` | key 为索引 ID，value 为 `{ "filename": str, "text": str, "text_hash": str }` |

实现 `keyword_searcher.IndexProvider` 协议。

---

### `get_embeddings() -> dict[str, dict[str, object]]`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, dict[str, object]]` | key 为索引 ID，value 为 `{ "text_hash": str, "embedding": list[float] }` |

返回当前内存中的 embedding 索引外层浅拷贝。调用方可读取向量数据，但不应修改返回值后期待写回生效。

---

### `get_entry(entry_id: str) -> dict[str, str] | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 索引 ID，如 `"1"` |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, str] \| None` | 匹配条目，格式为 `{ "filename": str, "text": str, "text_hash": str }`；不存在时返回 `None` |

---

### `get_by_filename(filename: str) -> dict[str, str] | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | 表情包文件名，如 `"cat.jpg"` |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, str] \| None` | 匹配条目；不存在时返回 `None` |

---

### `entry_count` *(property)*

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 当前索引中的条目总数 |

---

### `save_index() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

将当前索引原子写入 `data/index.json`。

---

### `save_embeddings() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

将当前 embedding 索引原子写入 `data/embeddings.json`（v2 格式：`{"version": 2, "entries": ...}`，embedding 自动编码为 base64）。

---

### `add_entry(filename: str, text: str, embedding: list[float]) -> AddResult`

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 识别文本 |
| `embedding` | `list[float]` | embedding 向量 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `AddResult` | 描述本次新增、替换或无文字移图结果 |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

---

### `remove_entry(entry_id: str) -> bool`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 待删除的索引 ID |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 删除成功，`False` ID 不存在 |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

从索引和 embedding 中删除记录，并原子写入磁盘。

---

### `acquire_lock() -> bool`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 成功获取锁，`False` 锁已被占用 |

调用方获取失败时应回复“索引正在更新，请稍后再试”。

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

### `async sync_with_filesystem() -> SyncResult`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `SyncResult` | 新增、删除、去重、无文字移走和失败统计 |

按文件名同步内存索引与 `memes/` 目录；新增图片依赖注入的 OCR 与 Embedding provider。

---

### `async add_single_file(filename: str) -> AddResult`

单张图片添加：执行压缩→OCR→Embedding 管道，然后调用 `add_entry`。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | `memes/` 下的图片文件名 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `AddResult` | 添加/替换/无文字移图结果 |
| **异常** | `CompressionError` | 图片压缩失败 |
| **异常** | `OcrError` | OCR 识别失败 |
| **异常** | `EmbeddingError` | Embedding 生成失败 |

---

### `async _process_image_pipeline(filename: str) -> tuple[str, list[float]]`

压缩→OCR→Embedding 管道。先压缩图片（若配置 optimizer），再 OCR 提取文本，最后生成 embedding 向量。

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | `memes/` 下的图片文件名 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `tuple[str, list[float]]` | `(ocr_text, embedding)` |
| **异常** | `CompressionError` | 图片压缩失败 |
| **异常** | `OcrError` | OCR 识别失败 |
| **异常** | `EmbeddingError` | Embedding 生成失败 |