# API 参考 — MemePilot

> 本文档记录各模块的对外接口和所需的外部接口，作为开发参考。
> 每个函数/方法均说明参数与返回值。
> 版本：v1.0，最后更新：2026-06-18

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
    failed: list[str] = field(default_factory=list)
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `added` | `int` | `0` | 本次同步新增的图片数量 |
| `deleted` | `int` | `0` | 本次同步删除的图片数量 |
| `failed` | `list[str]` | `[]` | 处理失败的文件名列表 |

是 `sync_with_filesystem()` 的返回类型。

---

### 1.5 IndexManager 类

```python
class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
```

#### 类属性

| 属性 | 类型 | 值 | 说明 |
|------|------|------|------|
| `SUPPORTED_EXTENSIONS` | `frozenset[str]` | `{".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}` | 支持的图片扩展名集合 |

---

#### `__init__(data_dir="data", memes_dir="memes", ocr_provider=None, embedding_provider=None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `data_dir` | `str` | `"data"` | 索引文件目录路径（`index.json`、`embeddings.json`） |
| `memes_dir` | `str` | `"memes"` | 表情包图片目录路径 |
| `ocr_provider` | `OcrProvider \| None` | `None` | OCR 服务注入，未注入时无法执行 OCR |
| `embedding_provider` | `EmbeddingProvider \| None` | `None` | Embedding 服务注入，未注入时无法生成 embedding |

初始化后 `_entries` 和 `_embeddings` 均为空，需调用 `load()` 加载磁盘数据。

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
4. 自动校验 `text_hash` 一致性，不一致时修复并标记 `_embeddings_stale`
5. `embeddings.json` 不存在或损坏 → 标记 `_embeddings_stale = True`

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

#### `add_entry(filename: str, text: str, embedding: list[float]) -> str`

| 参数 | 类型 | 说明 |
|------|------|------|
| `filename` | `str` | 表情包文件名 |
| `text` | `str` | OCR 识别文本 |
| `embedding` | `list[float]` | embedding 向量 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `str` | 分配的索引 ID（空洞复用） |
| **异常** | `OSError` | 磁盘写入失败时抛出 |

内部自动调用 `_find_next_id()` 分配 ID、`compute_text_hash(text)` 计算 `text_hash`，同时写入 `_entries` 和 `_embeddings`，并原子写入两个磁盘文件。

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
| **返回** | `SyncResult` | `added` 新增数、`deleted` 删除数、`failed` 失败文件名列表 |

异步方法。按文件名同步内存索引与 `memes/` 目录：

1. 确保 `memes_dir` 存在
2. 扫描 `memes/` 下 `SUPPORTED_EXTENSIONS` 中的文件
3. 已删除的图片 → 从 `_entries` / `_embeddings` 移除
4. 新增图片（按文件名升序）→ 调用 `OcrProvider.ocr()` 和 `EmbeddingProvider.embed()`，写入索引
5. 单个图片失败不影响其他图片
6. 有变更时原子写入磁盘

**依赖**：需先注入 `ocr_provider` 和 `embedding_provider`，否则新增图片会记录到 `failed` 列表。

---

### 1.6 模块依赖

| 依赖类型 | 名称 | 来源 | 状态 |
|---------|------|------|------|
| 第三方库 | `ujson` | PyPI | 已安装 |
| Protocol 注入 | `OcrProvider` | `bot/engine/ocr_service.py` | 待实现 |
| Protocol 注入 | `EmbeddingProvider` | `bot/engine/ai_matcher.py` | 待实现 |
| 标准库 | `hashlib`, `pathlib`, `os`, `asyncio`, `logging` | CPython | 内置 |

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

## 4. 尚未实现的计划模块

| 模块 | 预计对外接口 | 预计依赖 |
|------|------------|---------|
| `engine/ocr_service.py` | 实现 `OcrProvider` 协议：`async ocr(path: str) -> str` | PaddleOCR |
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

## 5. 跨模块依赖关系图

```
logging_config.py ──(无依赖)──

index_manager.py
  ├── 依赖注入: OcrProvider (ocr_service.py, 待实现)
  ├── 依赖注入: EmbeddingProvider (ai_matcher.py, 待实现)
  ├── 第三方: ujson
  └── 标准库: hashlib, pathlib, os, asyncio, logging

keyword_searcher.py
  ├── 依赖注入: IndexProvider ← IndexManager.get_entries()
  └── 第三方: rapidfuzz

IndexProvider 协议:
  keyword_searcher.py (定义) ── IndexManager (实现)
```
