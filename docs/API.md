# API 参考 — MemePilot

> 本文档记录各模块的对外接口和所需的外部接口，作为开发参考。
> 每个函数/方法均说明参数与返回值。
> 版本：v1.0，最后更新：2026-06-20

---

## 1. bot/engine/index_manager.py — 索引增删改查模块

### 1.1 模块级函数

#### `normalize_text(text: str) -> str`

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

#### `compute_text_hash(text: str) -> str`

计算规范化文本的 SHA-256 哈希。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | 待哈希的文本（内部先调用 `normalize_text`） |
| **返回** | `str` | 格式 `"sha256:<64位十六进制>"` |
| **异常** | 无 | |

```python
compute_text_hash("hello")  # → "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
```

---

#### `dedup_key(text: str) -> str`

计算 OCR 文本的去重键。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | 原始 OCR 文本 |
| **返回** | `str` | 去除所有空白字符（含半角/全角空格、制表符、换行）后的文本，可能为空字符串 |

比 `normalize_text` 更严格：`normalize_text` 保留单词间单空格，`dedup_key` 完全去除空格，用于判定「是否完全相同的图片」。实时计算，不落盘。

```python
dedup_key("加班 好累")   # → "加班好累"
dedup_key("加班好累")    # → "加班好累"  # 与上行同键
dedup_key("   ")         # → ""
```

---

#### `is_blank_text(text: str) -> bool`

判断 OCR 文本是否为「无文字」。

| | 类型 | 说明 |
|--|------|------|
| **参数** `text` | `str` | OCR 文本 |
| **返回** | `bool` | `True` 表示去所有空白后为空（无文字，需移到 `meme_no_text/` 不进索引） |

等价于 `dedup_key(text) == ""`。

---

### 1.2 异常

#### `IndexCorruptedError(Exception)`

`index.json` 结构损坏或缺少必要字段时抛出。

无额外属性，使用 `str(exc)` 获取错误消息。

#### `IndexLockedError(Exception)`

索引更新锁被占用时抛出（预留，当前锁管理使用 `bool` 返回值而非异常）。

---

### 1.3 Protocol（依赖注入接口）

#### `OcrProvider`

```python
class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `ocr` | `image_path: str` — 图片文件绝对路径 | `str` — 识别到的文字 | 异步，对图片执行 OCR 文字识别 |

由插件层注入具体实现（待实现于 `ocr_service.py`）。

---

#### `EmbeddingProvider`

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `embed` | `text: str` — 待向量化的文本 | `list[float]` — embedding 浮点数向量 | 异步，调用 SiliconFlow API 生成向量 |

由插件层注入具体实现（待实现于 `ai_matcher.py`）。

---

### 1.4 数据类

#### `SyncResult`

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
| `deleted` | `int` | `0` | 本次同步删除的图片数量（memes/ 已不存在的旧图） |
| `deduped` | `int` | `0` | 新图因去重键命中已有条目/其他新图而被删除的数量 |
| `no_text_moved` | `int` | `0` | OCR 无文字被移到 meme_no_text/ 的数量 |
| `failed` | `list[str]` | `[]` | 处理失败的文件名列表（含新增失败与 embedding 重建失败） |

是 `sync_with_filesystem()` 的返回类型。重建 embedding 的数量不单独计入字段，仅在日志中输出。去重与无文字移动不计入 `added`/`deleted`，各自独立计数。

---

#### `AddResult`

```python
@dataclass
class AddResult:
    entry_id: str | None
    reason: str
    replaced_filename: str | None = None
    moved_to: str | None = None
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `entry_id` | `str \| None` | 必填 | 分配/复用的索引 ID；无文字移图场景为 `None` |
| `reason` | `str` | 必填 | 结果类别：`"added"`（正常新增）、`"replaced"`（去重覆盖）、`"no_text"`（无文字移图） |
| `replaced_filename` | `str \| None` | `None` | `reason="replaced"` 时为被删旧图文件名，否则 `None` |
| `moved_to` | `str \| None` | `None` | `reason="no_text"` 时为移入 meme_no_text/ 的完整路径，否则 `None` |

是 `add_entry()` 的返回类型。

---

### 1.5 IndexManager 类

```python
class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
    DEFAULT_SYNC_CONCURRENCY: int
```

#### 类属性

| 属性 | 类型 | 值 | 说明 |
|------|------|------|------|
| `SUPPORTED_EXTENSIONS` | `frozenset[str]` | `{".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}` | 支持的图片扩展名集合 |
| `DEFAULT_SYNC_CONCURRENCY` | `int` | `5` | 并行同步默认并发上限，`sync_concurrency` 未注入或非正数时使用 |

---

#### `__init__(data_dir="data", memes_dir="memes", ocr_provider=None, embedding_provider=None, sync_concurrency=None, no_text_dir=None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `data_dir` | `str` | `"data"` | 索引文件目录路径（`index.json`、`embeddings.json`） |
| `memes_dir` | `str` | `"memes"` | 表情包图片目录路径 |
| `ocr_provider` | `OcrProvider \| None` | `None` | OCR 服务注入，未注入时无法执行 OCR |
| `embedding_provider` | `EmbeddingProvider \| None` | `None` | Embedding 服务注入，未注入时无法生成 embedding |
| `sync_concurrency` | `int \| None` | `None` | `sync_with_filesystem()` 并行处理新增图片时的最大并发数；`None` 或非正数时使用 `DEFAULT_SYNC_CONCURRENCY`(5)。建议由插件层从 `SYNC_CONCURRENCY` 环境变量读取后注入，避免一次性发起大量请求触发 SiliconFlow 限流 |
| `no_text_dir` | `str \| None` | `None` | 无文字图存放目录；`None` 时取 `memes_dir` 同级的 `meme_no_text/`（即 `Path(memes_dir).parent / "meme_no_text"`）。插件层无需显式传入 |

初始化后 `_entries` 和 `_embeddings` 均为空，需调用 `load()` 加载磁盘数据。`_sync_semaphore` 在此时根据 `sync_concurrency` 创建。

---

#### `load() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `IndexCorruptedError` | `index.json` 结构损坏或缺少必要字段 |

加载并校验 `data/index.json` 和 `data/embeddings.json`。启动时必须调用此方法后再使用其他查询/写入方法。

行为：
1. 自动创建 `data_dir`（如不存在）
2. `index.json` 不存在 → 初始化为空 `{"version": 1, "entries": {}}`
3. `index.json` 存在但损坏 → 抛出 `IndexCorruptedError`
4. 自动校验 `text_hash` 一致性：若用户手动编辑了 `text` 导致 `text_hash` 与 `text` 不符，按当前 `text` 重新计算并修复 `_entries[id].text_hash`，同时标记 `_embeddings_stale = True`。修复后的 `_entries[id].text_hash` 与 `_embeddings[id].text_hash` 不一致将由 `sync_with_filesystem()` 的重建阶段消费，触发对应 embedding 重建
5. `embeddings.json` 不存在或损坏 → 标记 `_embeddings_stale = True`（`_embeddings` 置空，由 `sync_with_filesystem()` 重建阶段全量重建）

---

#### `validate_index(data: object) -> None` *(静态方法)*

| 参数 | 类型 | 说明 |
|------|------|------|
| `data` | `object` | 解析后的 JSON 数据 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `IndexCorruptedError` | 缺少 `version`(int) 或 `entries`(dict) |

仅校验顶层结构，不校验 entry 内部字段（entry 校验在 `_load_index` 中完成）。

---

#### `get_entries() -> dict[str, dict[str, str]]`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, dict[str, str]]` | key 为索引 ID（如 `"1"`），value 为 `{"filename": str, "text": str, "text_hash": str}` |

返回内存中的 `_entries` 引用（非拷贝）。实现 `keyword_searcher.IndexProvider` 协议。

---

#### `get_entry(entry_id: str) -> dict[str, str] | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 索引 ID，如 `"1"` |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, str] \| None` | `{"filename": str, "text": str, "text_hash": str}`，不存在时返回 `None` |

---

#### `get_by_filename(filename: str) -> dict[str, str] | None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | 表情包文件名，如 `"cat.jpg"` |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `dict[str, str] \| None` | 匹配到的 entry，不存在时返回 `None` |

线性扫描 `_entries`，O(n) 复杂度。注意：如果存在同名文件，只返回第一个匹配项（正常情况不应出现同名）。

---

#### `entry_count` *(property)*

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 当前索引中的条目总数 |

---

#### `save_index() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

将 `_entries` 和 `index_version` 序列化为 `{"version": N, "entries": {...}}` 格式，通过 `_atomic_write` 原子写入 `data/index.json`。

---

#### `save_embeddings() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

将 `_embeddings` 序列化后通过 `_atomic_write` 原子写入 `data/embeddings.json`。写入成功后将 `_embeddings_stale` 设为 `False`。

---

#### `add_entry(filename: str, text: str, embedding: list[float]) -> AddResult`

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 识别文本 |
| `embedding` | `list[float]` | embedding 向量 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `AddResult` | 描述本次结果的 `AddResult`：`reason="added"` 正常新增、`reason="replaced"` 去重覆盖（复用旧 ID、删旧图）、`reason="no_text"` 无文字移图（不进索引） |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

三分支处理：
1. 无文字（`is_blank_text(text)` 为真）→ 调用 `_move_to_no_text(filename)` 移图到 `meme_no_text/`，不写索引，返回 `AddResult(entry_id=None, reason="no_text", moved_to=...)`。
2. 去重键命中已有条目（`_find_entry_by_dedup_key(dedup_key(text))` 非 `None`）→ 删除旧图文件（`missing_ok`），复用旧 ID 覆盖 `_entries[old_id]` 与 `_embeddings[old_id]`（用新 `text_hash` 与新 `embedding`），原子写入两文件，返回 `AddResult(entry_id=old_id, reason="replaced", replaced_filename=旧文件名)`。
3. 正常新增 → `_find_next_id()` 分配 ID，写入 `_entries`/`_embeddings`，原子写入两文件，返回 `AddResult(entry_id, reason="added")`。

---

#### `remove_entry(entry_id: str) -> bool`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 待删除的索引 ID |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 删除成功，`False` ID 不存在 |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

从 `_entries` 和 `_embeddings` 中删除记录，并原子写入磁盘。删除后产生 ID 空洞，可被后续 `add_entry` 复用。

---

#### `acquire_lock() -> bool`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 成功获取锁，`False` 锁已被占用 |

非阻塞尝试。同一时间只允许一个索引写入任务运行。调用方获取失败时应回复"索引正在更新，请稍后再试"。

---

#### `release_lock() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

释放更新锁。未锁定时调用也是安全的（no-op）。

---

#### `is_locked` *(property)*

| | 类型 | 说明 |
|--|------|------|
| **返回** | `bool` | `True` 锁被持有，`False` 未锁定 |

---

#### `async sync_with_filesystem() -> SyncResult`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `SyncResult` | `added` 新增数、`deleted` 删除数、`deduped` 去重数、`no_text_moved` 无文字移走数、`failed` 失败文件名列表 |

异步方法。按文件名同步内存索引与 `memes/` 目录，三阶段并行：

1. 确保 `memes_dir` 存在
2. 扫描 `memes/` 下 `SUPPORTED_EXTENSIONS` 中的文件
3. **删除阶段**：已删除的图片 → 从 `_entries` / `_embeddings` 移除
4. **重建阶段**（embedding 过期修复 + 全量重建）：对文件仍存在的已有条目，比较 `_entries[id].text_hash` 与 `_embeddings[id].text_hash`（或 `_embeddings` 缺该 id）；不一致则用当前 `text` 调用 `EmbeddingProvider.embed()` 重建对应 embedding，覆盖 `_embeddings[id]`，**不重新 OCR**。该判定同时覆盖两类场景：
   - 用户手动编辑 `index.json` 的 `text` 导致 `text_hash` 不一致（`load()` 阶段已按新 `text` 修复 `_entries[id].text_hash`）
   - `embeddings.json` 缺失/损坏导致 `_embeddings` 为空，全部条目触发全量重建
5. **新增阶段**：新增图片**并行处理**——对按文件名升序排序后的新增文件，通过 `asyncio.gather` 同时发布多个 task；每个 task 内部串行执行 `OcrProvider.ocr()` → `EmbeddingProvider.embed()`，task 之间受 `_sync_semaphore` 约束并发执行。结果收集后**按文件名升序串行三分类**（基于 `winner_keys` 赢家集合增量判定）：(a) 无文字（`is_blank_text`）→ `_move_to_no_text` 移图、`no_text_moved++`；(b) 去重键 `dedup_key(text)` 命中 `winner_keys`（已有条目或本轮更靠前的保留新图）→ 删新图文件、`deduped++`（现有条目/靠前图赢）；(c) 正常新增 → 分配 ID（复用最小空洞 id，保证 ID 顺序与文件名升序一致）写入 `_entries` / `_embeddings`、该键加入 `winner_keys`、`added++`。`winner_keys` 初始为已有条目的去重键集合。
6. 单个图片失败（重建或新增）不影响其他图片，记入 `failed`
7. 全部处理完成后统一原子写入磁盘（新增、删除、重建任一发生时）

各阶段内部并行，阶段间串行（先完成全部重建再开始新增）。

**依赖**：需先注入 `ocr_provider` 和 `embedding_provider`。新增图片缺失 provider 会记入 `failed`；重建阶段仅需 `embedding_provider`（未注入时所有待重建条目记入 `failed`）。

**并发控制**：并发上限由 `__init__` 的 `sync_concurrency` 决定，建议从 `SYNC_CONCURRENCY` 环境变量读取。默认 `5`，用于避免一次性发起大量请求触发 SiliconFlow 限流。重建与新增共用同一 `_sync_semaphore`。

**返回**：`SyncResult(added, deleted, deduped, no_text_moved, failed)`。重建数量仅在日志中输出（`新增=X, 删除=Y, 去重=D, 无文字移走=T, 重建=Z, 失败=W`），不计入 `SyncResult`。去重与无文字移动不计入 `added`/`deleted`，各自独立计数。

---

### 1.6 模块依赖

| 依赖类型 | 名称 | 来源 | 状态 |
|---------|------|------|------|
| 第三方库 | `ujson` | PyPI | 已安装 |
| Protocol 注入 | `OcrProvider` | `bot/engine/ocr_service.py` | 待实现 |
| Protocol 注入 | `EmbeddingProvider` | `bot/engine/ai_matcher.py` | 待实现 |
| 标准库 | `hashlib`, `pathlib`, `os`, `asyncio`, `logging` | CPython | 内置 |
| 配置（注入） | `SYNC_CONCURRENCY` | `.env` | 可选，由插件层读取后通过 `sync_concurrency` 参数注入，默认 `5` |

---

## 2. bot/engine/keyword_searcher.py — 关键词模糊搜索模块

### 2.1 数据类

#### `SearchResult`

```python
@dataclass
class SearchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float = field(compare=True)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 索引 ID，如 `"1"` |
| `filename` | `str` | 表情包文件名，如 `"cat.jpg"` |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | 相似度分数，范围 0–100 |

`compare=True` 使 `similarity` 参与排序比较。

---

### 2.2 Protocol

#### `IndexProvider`

```python
class IndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_entries` | 无 | `dict[str, dict[str, str]]` | key 为索引 ID，value 为 `{"filename": str, "text": str, "text_hash": str}` |

`KeywordSearcher` 通过此协议获取索引数据。`IndexManager` 已实现此协议，可直接注入。

---

### 2.3 KeywordSearcher 类

#### `__init__(index_provider, threshold=60.0, limit=10) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `index_provider` | `IndexProvider` | 必填 | 索引数据来源（如 `IndexManager` 实例） |
| `threshold` | `float` | `60.0` | 最低相似度阈值，低于此分数不返回 |
| `limit` | `int` | `10` | 最大返回结果数 |

---

#### `search(keyword: str) -> list[SearchResult]`

| 参数 | 类型 | 说明 |
|------|------|------|
| `keyword` | `str` | 用户输入的关键词 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `list[SearchResult]` | 按 `similarity` 降序排列，最多 `limit` 条；无匹配或关键词为空时返回 `[]` |

行为：
1. 关键词为空/纯空白 → 返回 `[]`
2. 索引为空 → 返回 `[]`
3. 对每条 OCR 文本用 `rapidfuzz.fuzz.partial_ratio(keyword, text)` 计算子串模糊匹配分数
4. 过滤 `score < threshold` 的结果
5. 按分数降序排列，截断至 `limit` 条

---

### 2.4 模块依赖

| 依赖类型 | 名称 | 来源 | 状态 |
|---------|------|------|------|
| 第三方库 | `rapidfuzz` | PyPI | 已安装 |
| Protocol 注入 | `IndexProvider` | `bot/engine/index_manager.py` | 已实现 |

---

## 3. bot/logging_config.py — 日志配置模块

### 3.1 模块级函数

#### `setup_logging(log_dir: str = "log") -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `log_dir` | `str` | `"log"` | 日志目录路径 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **副作用** | 配置全局 `logging.root` | 添加 `RotatingFileHandler` + `StreamHandler` |

配置内容：

| 处理器 | 目标 | 级别 | 格式 |
|--------|------|------|------|
| `RotatingFileHandler` | `<log_dir>/bot.log`（≤1MB，保留 1 个备份） | DEBUG | `时间 - 模块名 - 级别 - 消息` |
| `StreamHandler` | stdout | INFO | `时间 - 模块名 - 级别 - 消息` |

启动时调用一次。自动创建 `<log_dir>` 目录。

---

### 3.2 模块依赖

无外部依赖，仅使用 Python 标准库（`logging`, `logging.handlers`, `pathlib`）。

---

## 4. bot/engine/ocr_service.py — DeepSeek-OCR 模块

### 4.1 DeepSeekOcrService 类

实现 `index_manager.OcrProvider` 协议，通过硅基流动 chat completions API 调用 `deepseek-ocr` 模型进行图片文字识别。

#### `__init__(api_key=None, base_url=None, model=None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `api_key` | `str \| None` | `None` | 硅基流动 API Key，默认从 `SILICONFLOW_API_KEY` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `SILICONFLOW_BASE_URL` 环境变量读取，回退 `https://api.siliconflow.cn/v1` |
| `model` | `str \| None` | `None` | OCR 模型名，默认从 `SILICONFLOW_OCR_MODEL` 环境变量读取，回退 `deepseek-ai/DeepSeek-OCR` |

---

#### `async ocr(image_path: str) -> str`

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str` | 图片文件绝对路径 |
| **返回** | `str` | 识别到的文本字符串 |
| **异常** | `FileNotFoundError` | 图片文件不存在 |
| | `ValueError` | 不支持的图片格式（不在 MIME_MAP 中） |
| | `RuntimeError` | API 调用失败（网络异常、认证失败等） |

行为：
1. 检查文件是否存在
2. 根据扩展名确定 MIME 类型，不支持则抛出 `ValueError`
3. 读取图片二进制 → base64 编码 → 构造 data URL
4. 通过 `AsyncOpenAI.chat.completions.create()` 调用硅基流动 vision API
5. Prompt 使用 `"<image>\n<|grounding|>OCR this image."`
6. 返回 `_clean_ocr_result(response.choices[0].message.content)` — 清洗定位标记，仅保留纯文本

---

### 4.2 模块级函数

#### `_clean_ocr_result(raw: str) -> str`

清洗 DeepSeek-OCR 原始输出。

| | 类型 | 说明 |
|--|------|------|
| **参数** `raw` | `str` | DeepSeek-OCR 原始 API 输出，含 `<|ref|>` `<|/ref|>` `<|det|>` 等定位标记 |
| **返回** | `str` | 提取纯文本，多段之间用空格连接 |

```python
_clean_ocr_result("<|ref|>不可惊扰<|/ref|><|det|>[[...]]<|/det|>")  # → "不可惊扰"
_clean_ocr_result("<|ref|>A<|/ref|>\n<|ref|>B<|/ref|>")           # → "A B"
```

---

### 4.3 类属性

| 属性 | 类型 | 值 | 说明 |
|------|------|------|------|
| `MIME_MAP` | `dict[str, str]` | `{".jpg": "image/jpeg", ...}` | 支持的图片扩展名→MIME 类型映射 |
| `OCR_PROMPT` | `str` | `"<image>\n<|grounding|>OCR this image."` | DeepSeek-OCR 通用文字识别 prompt |

---

### 4.4 模块依赖

| 依赖类型 | 名称 | 来源 | 状态 |
|---------|------|------|------|
| 第三方库 | `openai` | PyPI | 已安装 |
| 标准库 | `base64`, `re`, `os`, `logging`, `pathlib` | CPython | 内置 |
| 环境变量 | `SILICONFLOW_API_KEY` | `.env` | 必填 |
| 环境变量 | `SILICONFLOW_BASE_URL` | `.env` | 可选 |
| 环境变量 | `SILICONFLOW_OCR_MODEL` | `.env` | 可选，默认 `deepseek-ai/DeepSeek-OCR` |

---

## 5. 尚未实现的计划模块

| 模块 | 预计对外接口 | 预计依赖 |
|------|------------|---------|
| `engine/image_optimizer.py` | 无损压缩函数（接口待定） | 图片处理库 |
| `engine/ai_matcher.py` | 实现 `EmbeddingProvider` 协议 + AI 匹配逻辑（接口待定） | SiliconFlow API、DeepSeek API |
| `plugins/meme_search.py` | `/search` 命令处理 | `KeywordSearcher`、`IndexManager` |
| `plugins/meme_ai.py` | `/ai` 命令处理 | AI 匹配逻辑、`IndexManager` |
| `plugins/meme_add.py` | `/add` 命令处理 | `IndexManager`、`OcrProvider`、`EmbeddingProvider`、图片压缩 |
| `plugins/meme_help.py` | `/help` 命令处理 | 无额外依赖 |
| `plugins/meme_refresh.py` | `/refresh` 命令处理 | `IndexManager` |
| `config.py` | 环境变量读取 | `python-dotenv` |
| `bot.py` | NoneBot2 入口 | nonebot2、各插件 |

---

## 6. 跨模块依赖关系图

```
logging_config.py ──(无依赖)──

index_manager.py
  ├── 依赖注入: OcrProvider (ocr_service.py, 已实现 → SiliconFlow DeepSeek-OCR)
  ├── 依赖注入: EmbeddingProvider (ai_matcher.py, 待实现)
  ├── 第三方: ujson
  └── 标准库: hashlib, pathlib, os, asyncio, logging

keyword_searcher.py
  ├── 依赖注入: IndexProvider ← IndexManager.get_entries()
  └── 第三方: rapidfuzz

IndexProvider 协议:
  keyword_searcher.py (定义) ── IndexManager (实现)
```
