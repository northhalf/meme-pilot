# 设计文档 — 索引管理与向量搜索重构（ChromaDB + SQLite3）

> 日期：2026-06-30
> 状态：待用户审阅
> 关联文档：`docs/PRD.md`、`CONTEXT.md`、`README.md`、`docs/api/API.md`

---

## 1. 背景与目标

### 1.1 现状

当前索引与向量搜索基于两份 JSON 文件：

- `data/index.json`：`{ version, entries: { id: { filename, text, text_hash } } }`
- `data/embeddings.json`（v2）：`{ version, entries: { id: { text_hash, embedding(base64) } } }`

`IndexManager` 管理这两份文件，承担按文件名同步、OCR 文本去重、无文字移图、原子写入、全局锁等职责。`AIMatcher` 用纯 Python 遍历全部 embedding 计算余弦相似度做 Top10 召回，再经 DeepSeek 精排。`text_hash`（SHA-256）用于检测"用户手动编辑 text 后自动重建 embedding"。

### 1.2 目标

- 用 **ChromaDB** 建立向量索引库，用 **sqlite3** 保存元数据索引。
- 去掉 `text_hash`（SHA-256）。
- SQLite 只保存：`id`、图片路径、文字、说话人、标记词。
- 向量库只保存：`id`、向量。
- 向量库与 SQLite 的 id 完全对应。
- OCR 返回文本去除所有空白，存储文本无空格。

### 1.3 非目标

- 不实现"说话人/标记词"的抽取流程（本次只建表，来源待定）。
- 不保留"用户手动编辑 text 后自动重建 embedding"能力（全量重建能力保留）。
- 启动逻辑不含旧数据迁移（由手动脚本完成）。

---

## 2. 关键决策（用户确认）

| 决策点 | 选择 | 说明 |
|---|---|---|
| 说话人/标记词来源 | 先建表、来源待定 | 可空字段，本次不填充，embedding 仍用 OCR text 生成 |
| text_hash | 完全去掉 | 放弃"改 text 自动重建 embedding"；全量重建保留（ChromaDB 损坏时按 sqlite text 重 embed） |
| 旧数据迁移 | 手动脚本迁移 | `scripts/migrate_json_to_db.py`，启动逻辑不含迁移 |
| 图片路径存储 | 仅文件名（语义为 `memes/` 下相对路径） | 扁平结构下即文件名，运行时拼接 `MEMES_DIR / image_path` |
| tags 多值存储 | 关联表 `meme_tag` | 支持按词精确查询，走索引 |
| 最小空洞 ID | 纯 SQL 单条查询 | `UNION ALL SELECT 0` 注入虚拟行，覆盖表头空洞 |
| 架构方案 | 拆为两个公开类 | `MetadataStore` + `VectorStore` 公开，`IndexManager` 薄编排 |
| 对外数据类字段名 | 统一 `image_path` | `filename` → `image_path`；`replaced_filename` → `replaced_image_path` |
| entry_id 类型 | 全栈 `int` | `dict[int, MemeEntry]`；`VectorStore` 内部 `str↔int` 转换 |
| 内部反向索引命名 | `_text_to_id` | text → id，与 `get_id_by_text` 一致 |
| OCR 文本 | 去除所有空白 | OCR 服务返回前去所有空白；存储/搜索文本均无空格 |
| 迁移 embedding | 复用旧向量 | 零 API 消耗；提示如需严格一致可手动重建 |
| 跨库一致性 | 增量写入 + 阶段0修复 | 取代 JSON 时代"全部成功才原子替换" |

---

## 3. 总体架构

存储层拆为两个公开类，`IndexManager` 退化为薄编排层。

```
插件层  meme_search / meme_ai / meme_add / meme_refresh / _search_utils
   │  (调用方式基本不变: AIMatcher.match / KeywordSearcher.search /
   │   IndexManager.add_single_file / sync_with_filesystem / acquire_lock)
   ▼
┌─────────────────────────────────────────────────────────────┐
│ IndexManager  (薄编排)                                       │
│  持有: MetadataStore + VectorStore + OcrProvider             │
│        + EmbeddingProvider + ImageOptimizer + _lock + 信号量 │
│  职责: 压缩→OCR→Embed 管道编排、sync 四阶段、跨库写入一致性、│
│        全局锁、并发上限、去重/无文字移图                      │
└──────────┬───────────────────┬──────────────────────────────┘
           ▼                   ▼
┌────────────────────┐  ┌────────────────────────────────────┐
│ MetadataStore      │  │ VectorStore                        │
│  sqlite3           │  │  chromadb.PersistentClient         │
│  data/index.db     │  │  collection "memes" (cosine)       │
│  meme + meme_tag   │  │  仅存: id(int) + embedding(1024)   │
│  CRUD + _text_to_id│  │  id 与 sqlite 完全对应             │
└─────────┬──────────┘  └──────────────┬─────────────────────┘
          ▲                            ▲
          │ 依赖                       │ 依赖
   ┌──────┴───────┐             ┌──────┴───────┐
│ KeywordSearcher│             │   AIMatcher  │── 也依赖 MetadataStore
│  (LCS on text) │             │ (Chroma 召回 │    (拿 image_path/text 构候选)
└────────────────┘             │  +DeepSeek精排)│
                               └──────────────┘
```

**组件职责边界**

| 组件 | 职责 | 不负责 |
|---|---|---|
| `MetadataStore` | sqlite 表 CRUD、按文件名/text/id 查询、id 分配（复用最小空洞）、事务、`_text_to_id` 维护 | OCR/embedding、锁、文件系统扫描 |
| `VectorStore` | ChromaDB collection 的 upsert/remove/query/rebuild_all、向量持久化 | metadata、业务逻辑 |
| `IndexManager` | 管道编排、sync 四阶段、跨库一致性、全局锁、并发信号量、去重/无文字移图 | 直接写 SQL/Chroma 调用（委托给两个 Store） |
| `KeywordSearcher` | 从 `MetadataStore` 取全部 text，做 jieba+pylcs LCS 匹配 | 向量、sqlite 细节 |
| `AIMatcher` | 用 `VectorStore.query` 召回 Top10 → 从 `MetadataStore` 查 metadata 构候选 → DeepSeek 精排 | 余弦计算（交给 ChromaDB） |

**纯函数**：`dedup_key` / `normalize_text` / `is_blank_text` / `compute_text_hash` / `encode_embedding` / `decode_embedding` 全部删除（运行时）。`decode_embedding` 逻辑仅内联在迁移脚本。不创建 `text_utils.py`。无文字判定用 `not text`；`winner_keys` 为已有条目 `text` 集合。

---

## 4. 数据存储结构

### 4.1 SQLite（`data/index.db`）

```sql
CREATE TABLE IF NOT EXISTS meme (
    id         INTEGER PRIMARY KEY,   -- 手动分配，复用最小空洞（纯 SQL 查询，不用 AUTOINCREMENT）
    image_path TEXT    NOT NULL,      -- memes/ 目录下相对路径（扁平结构下即文件名）
    text       TEXT    NOT NULL,      -- OCR 去除所有空白后的文本（无空格）
    speaker    TEXT                   -- 可空，单值，预留（本次不填充）
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_meme_image_path ON meme(image_path);

CREATE TABLE IF NOT EXISTS meme_tag (
    meme_id INTEGER NOT NULL,
    tag     TEXT    NOT NULL,
    PRIMARY KEY (meme_id, tag),
    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meme_tag_tag ON meme_tag(tag);
```

- `meme` 表 4 列；`tags` 移到 `meme_tag` 关联表，支持按词精确查询。
- `meme_tag` 用 `ON DELETE CASCADE`：删 `meme` 行自动清其全部 tag 行。
- 不存 `text_hash`、不存 `dedup_key` 列；去重靠 `MetadataStore` 内存反向索引 `_text_to_id`（`text → id`），`load()` 时从 sqlite 全量重建，增删同步。
- `speaker` / `tags` 本次一律 `NULL` / 不写行。

### 4.2 最小空洞 ID（纯 SQL）

```sql
SELECT MIN(t.id) + 1 AS next_id
FROM (SELECT 0 AS id UNION ALL SELECT id FROM meme) t
WHERE NOT EXISTS(SELECT 1 FROM meme t2 WHERE t2.id = t.id + 1);
```

`UNION ALL SELECT 0` 注入虚拟行，把"表头空洞"统一为普通空洞判断，无需 `CASE`。覆盖表空、表头缺、中间空洞、连续、多空洞五种情况。

### 4.3 ChromaDB（`data/chroma/`，`PersistentClient`）

| 项 | 值 |
|---|---|
| collection 名 | `memes` |
| distance | `cosine` |
| 每条存 | `id=str(int)` + `embedding=1024 维 float32` |
| metadata | 不存 |
| id 对应 | `str(sqlite.id)`，与 sqlite 完全一一对应 |

召回：`collection.query(query_embeddings=[vec], n_results=10)` → `(id, distance)`，`similarity = 1 - distance`。

### 4.4 文件布局

```
data/
├── index.db          ← 新：sqlite
├── chroma/           ← 新：ChromaDB PersistentClient 目录
├── index.json        ← 旧：迁移后可手动删除，bot 不再读写
└── embeddings.json   ← 旧：同上
```

`docker-compose.yml` 已挂载 `./data:/app/data`，`index.db` 与 `chroma/` 均在其中，无需改 volume。`Dockerfile` 新增 `chromadb` 依赖。

---

## 5. 接口签名

### 5.1 数据类

```python
# bot/engine/metadata_store.py
@dataclass
class MemeEntry:
    id: int
    image_path: str        # memes/ 目录下相对路径
    text: str              # OCR 去除所有空白后的文本（无空格）
    speaker: str | None
    tags: list[str]        # 从 meme_tag 组装；本次为空 []

# bot/engine/vector_store.py
@dataclass
class VectorHit:
    entry_id: int          # int；VectorStore 内部与 chroma 交互时转 str
    similarity: float      # = 1 - distance (cosine)
```

对外结果类统一改名：`SearchResult` / `AIMatchCandidate` / `AIMatchResult` 的 `filename → image_path`、`entry_id: str → int`；`AddResult.replaced_filename → replaced_image_path`、`entry_id: str | None → int | None`。`AIMatchResult.source` 仍为 `"embedding"` / `"rerank"`。

### 5.2 OcrProvider 协议（签名不变，返回值约定变更）

```python
class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str: ...   # 返回去除所有空白后的文本
```

`deepseek_ocr.py` / `paddle_ocr.py` 在返回前做 `"".join(result.split())`。

### 5.3 MetadataStore

```python
class MetadataStore:
    def __init__(self, db_path: str) -> None
    def load(self) -> None                              # 打开连接、建表/索引、重建 _text_to_id(text→id)
    def close(self) -> None

    def get_all_entries(self) -> dict[int, MemeEntry]   # key=int(id)，tags 从 meme_tag 组装
    def get_entry(self, entry_id: int) -> MemeEntry | None
    def get_by_filename(self, image_path: str) -> MemeEntry | None
    def get_id_by_text(self, text: str) -> int | None   # _text_to_id[text]
    def find_next_id(self) -> int                       # 纯 SQL
    def entry_count(self) -> int
    def get_all_text(self) -> list[tuple[int, str]]     # 全量重建 embedding 用

    def add(self, image_path: str, text: str,
             speaker: str | None = None,
             tags: list[str] | None = None) -> int
    def add_with_id(self, entry_id: int, image_path: str, text: str,
                    speaker: str | None = None,
                    tags: list[str] | None = None) -> int   # 迁移专用
    def update(self, entry_id: int, *,
               image_path: str | None = None,
               text: str | None = None,
               speaker: str | None = None,
               tags: list[str] | None = None) -> bool
    def remove(self, entry_id: int) -> bool             # CASCADE 删 meme_tag，更新 _text_to_id
```

### 5.4 VectorStore

```python
class VectorStore:
    def __init__(self, chroma_path: str, collection_name: str = "memes") -> None
    def load(self) -> None                              # PersistentClient + get_or_create_collection(cosine)
    def close(self) -> None

    def upsert(self, entry_id: int, embedding: list[float]) -> None          # 内部 str(entry_id)
    def remove(self, entry_id: int) -> None             # 不存在静默
    def remove_many(self, entry_ids: list[int]) -> None
    def query(self, query_embedding: list[float],
              n_results: int = 10) -> list[VectorHit]   # entry_id 转 int 返回
    def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None
    def count(self) -> int
```

### 5.5 IndexManager（薄编排）

```python
class IndexManager:
    def __init__(self, metadata_store: MetadataStore, vector_store: VectorStore,
                 memes_dir: str, no_text_dir: str | None = None,
                 ocr_provider: OcrProvider | None = None,
                 embedding_provider: EmbeddingProvider | None = None,
                 optimizer: ImageOptimizer | None = None,
                 sync_concurrency: int | None = None) -> None
    def load(self) -> None                              # 委托两 store.load()
    async def sync_with_filesystem(self) -> SyncResult  # 四阶段编排
    async def add_single_file(self, filename: str) -> AddResult
    async def acquire_lock(self) -> bool
    def release_lock(self) -> None
    @property
    def is_locked(self) -> bool
    @property
    def entry_count(self) -> int
```

`IndexManager` 不再对外暴露 `get_entries` / `get_embeddings` / `get_entry` / `get_by_filename` / `add_entry` / `remove_entry` / `save_index` / `save_embeddings`——职责移交两个 Store。

### 5.6 依赖关系

```
KeywordSearcher ──► MetadataStore          (get_all_entries→dict[int,MemeEntry]，对 text 做 LCS)
AIMatcher ───────► VectorStore             (query 召回 Top-N，entry_id 为 int)
                 └─► MetadataStore         (按 int id 查 image_path/text 构候选)
                 └─► EmbeddingProvider     (向量化用户描述)
                 └─► RerankProvider        (DeepSeek 精排，不变)
IndexManager ────► MetadataStore + VectorStore + OcrProvider + EmbeddingProvider + ImageOptimizer
```

### 5.7 AIMatcher.match 新流程

```
描述 → EmbeddingProvider.embed → VectorStore.query(vec, n=10) → [VectorHit(entry_id:int, sim)]
     → 对每个 hit: MetadataStore.get_entry(hit.entry_id) 取 image_path/text
     → 构建 AIMatchCandidate(rank, entry_id, image_path, text, similarity)
     → RerankProvider.rerank (失败/0/越界 → fallback Top-1)
     → AIMatchResult(entry_id, image_path, text, similarity, source)
```

### 5.8 app_state / bot.py 注入

- `app_state` 新增 `get_metadata_store()` / `get_vector_store()`，`init_app` 多收两个参数。
- `bot.py` startup：创建 `MetadataStore` + `VectorStore` → `IndexManager(注入两者)` → `AIMatcher(metadata_store, vector_store, embedding, rerank)` → `KeywordSearcher(metadata_store)` → 注册 `app_state`。
- 插件层调用方式不变，但消费字段 `.filename → .image_path`（`_search_utils.py:156,228`、`meme_ai.py:137` 三处拼接改为 `MEMES_DIR / result.image_path`，局部变量避开同名冲突）。

---

## 6. 同步与写入策略

### 6.1 sync_with_filesystem 四阶段（在 `_lock` 独占下）

```
0. 一致性修复   对齐 sqlite ↔ chroma
1. 删除阶段     memes/ 已不存在的图片
2. 新增阶段     新图并行 OCR→embed，串行三分类
   (原"重建阶段"基于 text_hash，已随 text_hash 去除而取消)
```

### 6.2 阶段 0 · 一致性修复

放弃 text_hash 后，不再检测"用户改 text"。本阶段对齐 sqlite↔chroma：

| 差异 | 修复动作 |
|---|---|
| sqlite 有 id、chroma 无 | 按 sqlite `text` 重 embed → `VectorStore.upsert(id, vec)` |
| chroma 有 id、sqlite 无（孤儿向量） | `VectorStore.remove(id)` |
| chroma collection 损坏/为空、sqlite 有数据 | `MetadataStore.get_all_text()` 全量重 embed → `VectorStore.rebuild_all(items)` |

保证 sync 后 `sqlite.id 集合 == chroma.id 集合`。

### 6.3 阶段 1 · 删除

```
existing_files = 扫描 memes/ (SUPPORTED_EXTENSIONS)
entries = MetadataStore.get_all_entries()
对 (id, entry) where entry.image_path ∉ existing_files:
    MetadataStore.remove(id)        # 先删 sqlite（CASCADE 删 meme_tag，同步 _text_to_id）
    VectorStore.remove(id)          # 再删 chroma；失败则留孤儿向量，由下次阶段0清理
```

### 6.4 阶段 2 · 新增

```
new_files = sorted(existing_files - {entry.image_path for entry in entries.values()})
并行 _process_new_file（压缩→OCR→embed，_sync_semaphore 限并发）
  OCR 返回无空格 text；返回 (image_path, text, embedding)
串行三分类（按 image_path 升序，winner_keys 初始 = 已有条目 text 集合）:
  1. not text → _move_to_no_text(image_path)，no_text_moved++
  2. text ∈ winner_keys → 删新图文件，deduped++
  3. 正常新增:
       id = MetadataStore.find_next_id()
       MetadataStore.add(image_path=image_path, text=text)
       VectorStore.upsert(id, embedding)
       winner_keys.add(text); added++
```

### 6.5 跨库写入一致性（语义变化）

JSON 时代"全部成功才原子替换正式文件"。sqlite/chroma 是增量写入，无分布式原子性。策略：

- **单条 `/add`**：`MetadataStore.add`（事务提交，返回 id）→ `VectorStore.upsert(id, vec)`。若 upsert 失败 → 回滚 `MetadataStore.remove(id)` + 删除已下载图片 + 报错。
- **`/refresh` 批量**：各条即时写入；单条 upsert 失败 → 立即回滚该条 `MetadataStore.remove(id)`，记 `failed`，继续其他图片；**阶段 0 一致性修复**作为下次 sync 开头额外保险，兜底任何残留差异。
- 不再有 `_persist_sync_results` 统一写盘步骤。

### 6.6 锁与并发（保留不变）

| 机制 | 作用 |
|---|---|
| `_lock` (asyncio.Lock) | `sync_with_filesystem` 独占；`acquire_lock` 非阻塞尝试，占用时插件回复"索引正在更新" |
| `_sync_semaphore` | sync 新增阶段 OCR/embed 并发上限（`SYNC_CONCURRENCY`，默认 5） |
| `_add_sem` | `add_single_file` 管道并发上限（同值） |
| `is_locked` / `_is_syncing` | 插件层只读检查 |

### 6.7 去重（`/add` 替换旧图）

```
old_id = MetadataStore.get_id_by_text(text)
if old_id is not None:
    旧 image_path = MetadataStore.get_entry(old_id).image_path
    # 顺序保证可回滚：先改 sqlite 指向，再更新向量，最后删旧图
    MetadataStore.update(old_id, image_path=新filename)   # sqlite 指向新图（text 不变，_text_to_id 键不变）
    VectorStore.upsert(old_id, 新embedding)                # 失败 → 回滚 update(image_path=旧) + 删新图 + 报错
    删旧图文件 (memes/旧image_path)                        # 最后删，保证前序失败时旧图仍在
    AddResult(entry_id=old_id, reason="replaced", replaced_image_path=旧image_path)
```

### 6.8 无文字移图

`not text`（text 无空格，空即 `""`）→ `_move_to_no_text(image_path)` 移到 `meme_no_text/`，不进 sqlite/chroma，`AddResult(reason="no_text", moved_to=...)`。

---

## 7. 迁移脚本与启动流程

### 7.1 迁移脚本 `scripts/migrate_json_to_db.py`（手动运行）

```
读取 data/index.json + data/embeddings.json（用标准库 json，无需 ujson）
初始化 MetadataStore(data/index.db) + VectorStore(data/chroma)
幂等检查: 若 meme 表已有数据 → 提示"已迁移，跳过"并退出

对每条旧 entry (id_str, {filename, text, text_hash}):
    text_new = "".join(entry["text"].split())           # 去所有空白
    new_id = MetadataStore.add_with_id(
                 entry_id=int(id_str),                   # 保留旧 id 数值
                 image_path=entry["filename"],
                 text=text_new,
                 speaker=None, tags=[])
    VectorStore.upsert(new_id, decode_embedding(旧 embedding))   # 复用旧向量
```

- 保留旧 id 数值；`decode_embedding` 逻辑内联在脚本（`struct.unpack` + `base64`）。
- **格式兼容**：旧 `embeddings.json` 可能是 v2（`embedding` 为 base64 字符串，需 decode）或 v1（`embedding` 为 `list[float]`，直接使用）。脚本按 `version` 字段判断，v1 直接取 list，v2 走 decode。
- **非数字 id 防御**：旧 `id_str` 无法 `int()` 时跳过该条并提示，不中断迁移。
- 迁移后旧 `index.json` / `embeddings.json` 保留不删，脚本打印三行提示：
  1. `迁移完成：N 条记录写入 data/index.db 与 data/chroma/`
  2. `embedding 复用旧向量（基于含空格 text 生成）。如需与无空格 text 严格一致，可删除 data/chroma/ 后重启 Bot，后台同步会按 sqlite text 全量重建 embedding。`
  3. `旧文件 data/index.json、data/embeddings.json 已保留，可自行归档或删除。`

### 7.2 启动流程 `bot.py _on_startup`

```
1. setup_logging
2. 选 OCR 引擎 (paddle/deepseek) —— ocr() 现返回无空格 text
   embedding_service / rerank_service / image_optimizer
3. metadata_store = MetadataStore(data/index.db)
   vector_store   = VectorStore(data/chroma)
   index_manager  = IndexManager(metadata_store, vector_store, memes_dir,
                                 ocr, embedding, optimizer, sync_concurrency)
   index_manager.load()        # 两 store.load(): 建表/索引/重建 _text_to_id + get_or_create_collection
                               # 不再读 index.json / embeddings.json
4. ai_matcher     = AIMatcher(metadata_store, vector_store, embedding_service, rerank_service)
   keyword_searcher = KeywordSearcher(metadata_store)
5. init_app(index_manager, metadata_store, vector_store, ocr, embedding, optimizer, ai_matcher, keyword_searcher)
6. 后台 _background_sync: acquire_lock → sync_with_filesystem → release_lock
```

### 7.3 启动时不做迁移

| 场景 | 行为 |
|---|---|
| 已运行迁移脚本，sqlite 有数据 | sync 阶段0 一致性修复 + 阶段1 删除 + 阶段2 增量新增 |
| 未运行迁移脚本，sqlite 空、memes/ 有图 | sync 阶段2 全量 OCR+embed 建库（消耗 API，等同全新部署） |
| 全新部署，memes/ 空 | 正常启动，`/search`/`/ai`/`/refresh` 回复"表情包目录为空" |

### 7.4 升级提示（写入 README/PRD）

> 从旧版升级时，先运行 `uv run python scripts/migrate_json_to_db.py` 再启动 Bot。否则旧 `index.json` 不会被读取，首次启动会全量重新 OCR/embed（消耗 API）。

---

## 8. 错误处理与边界情况

### 8.1 存储层错误

| 场景 | 行为 |
|---|---|
| `data/index.db` 不存在 | `MetadataStore.load` 建表（空库）→ sync 阶段2 全量建库 |
| `data/index.db` 损坏（非 sqlite 格式） | 打开失败抛 `IndexCorruptedError`，Bot 拒绝启动，要求用户修复 |
| `data/chroma/` 不存在 | `PersistentClient` 自动创建目录与 collection |
| chroma collection 损坏/为空、sqlite 有数据 | sync 阶段0 检测 → `get_all_text()` 全量重 embed → `rebuild_all` |
| sqlite↔chroma 不一致 | sync 阶段0 修复 |
| 迁移脚本遇旧 text 去空格后为空 | 跳过该条不写入，提示用户该条无文字（脚本不碰 `memes/` 文件） |

### 8.2 OCR / Embedding API 失败（保留 PRD 语义）

| 场景 | 行为 |
|---|---|
| 单图 OCR 异常 | `/add` 删图报错；`/refresh` 记 `failed`，继续其他图 |
| OCR API 不可用 | `/add` 回复失败原因；`/refresh` 本次不更新索引，回复"OCR 服务不可用" |
| 新图 embedding 失败 | `/add` 删图报错；`/refresh` 该条不写入，记 `failed` |
| `/ai` 用户描述 embedding 失败 | 回复"AI 服务暂时不可用，稍后重试" |
| `/ai` 精排失败/返回 0/越界 | fallback embedding Top-1 |
| 阶段0 重 embed 失败 | 记 `failed`，该条 chroma 仍缺，下次 sync 再试 |
| embedding 维度与 collection 不一致 | `upsert`/`query` 抛错；迁移脚本校验维度，不一致跳过并提示 |

### 8.3 跨库写入失败与回滚

| 路径 | 策略 |
|---|---|
| `/add`：`MetadataStore.add` 成功、`VectorStore.upsert` 失败 | 回滚 `MetadataStore.remove(id)` + 删已下载图片 + 回复失败 |
| `/add` 去重替换：`update(image_path)` 成功、`VectorStore.upsert` 失败 | 回滚 `update(image_path=旧)` + 删新图 + 回复失败（旧图未删，状态可恢复） |
| `/refresh` 阶段2：单条 upsert 失败 | 立即回滚该条 `MetadataStore.remove(id)`，记 `failed`，继续其他；阶段0 兜底 |
| `/refresh` 阶段1：`MetadataStore.remove` 成功、`VectorStore.remove` 失败 | 不回滚 sqlite（删除不可逆）；chroma 留孤儿向量，下次 sync 阶段0 检测"chroma 有 sqlite 无"清理 |
| sqlite 事务失败 | 事务自动回滚，不写 chroma，记 `failed` |

> 写入顺序统一为"先 sqlite 后 chroma"：添加路径 sqlite 提交后 chroma upsert，失败可回滚 sqlite add；删除路径 sqlite 删除后 chroma 删除，chroma 失败靠阶段0清孤儿（删除不可回滚）。

### 8.4 边界情况（对照 PRD 第 5 节）

| 场景 | 行为 |
|---|---|
| `memes/` 为空 | 启动 warning；`/search`/`/ai`/`/refresh` 回复"表情包目录为空，请先添加图片并执行 /refresh" |
| OCR 无文字（`not text`） | 移到 `meme_no_text/`，不进 sqlite/chroma，日志 warning |
| 新图 text 命中已有条目 | `/add` 删旧图、复用旧 ID、`update(image_path)` + `upsert`；`/refresh` 保留赢家、删新图 |
| 图片文件被删、索引还在 | sync 阶段1 `VectorStore.remove` + `MetadataStore.remove` |
| 文件名含特殊字符 | sqlite TEXT 存储，无分隔符解析问题 |
| 不支持扩展名 | 不作为表情包，不写入 |
| `/add` 多图 | 仅处理第一张 |
| `/add` 非图消息 | 提示"请发送一张图片"，继续等待至超时 |
| `/add` 无法判断扩展名 | 拒绝，回复"无法识别图片格式" |
| `.bmp` | 不压缩，继续 OCR/embed |
| 压缩失败 | `/add` 删图报错；`/refresh` 跳过该文件，汇总 `failed` |
| 旧 `index.json`/`embeddings.json` | bot 不再读写；迁移后可手动删除 |

### 8.5 并发与线程安全（实现要点）

| 关注点 | 处理 |
|---|---|
| chroma 同步调用阻塞事件循环 | `VectorStore` 用 `asyncio.to_thread` 包装 `upsert`/`remove`/`query`/`rebuild_all` |
| chroma 并发写冲突 | `VectorStore` 内部 `threading.Lock` 串行化所有 chroma 访问 |
| sqlite 跨线程 | `sqlite3.connect(check_same_thread=False)` + `MetadataStore` 内部 `threading.Lock` 串行化；同步调用 `asyncio.to_thread` 包装 |
| sync 与 `/add` 并发 | `is_locked` 互斥：sync 持 `_lock` 期间，`/add` 回复"索引正在更新" |
| 多 `/add` 并发 | `_add_sem` 限并发上限；两 Store 内部 Lock 保证写串行 |

### 8.6 性能考量

`KeywordSearcher.search` 每次调用 `MetadataStore.get_all_entries()`（sqlite 全表 + LEFT JOIN `meme_tag`）。对几千条记录 sqlite 查询通常 <100ms，LCS 计算才是主要开销，PRD "<1 秒" 可满足。若未来索引规模增大，`MetadataStore` 可内部缓存 `get_all_entries` 结果，在 `add`/`update`/`remove` 后失效，避免每次搜索全量查询。本次不强制实现缓存。

---

## 9. 测试策略

### 9.1 新增单元测试

| 文件 | 覆盖点 |
|---|---|
| `tests/unit/engine/test_metadata_store.py` | `load`/`add`/`add_with_id`/`update`/`remove`/查询/`find_next_id` 五例/`entry_count`/`tags` 组装 |
| `tests/unit/engine/test_vector_store.py` | `load`/`upsert`/`remove`/`query`/`rebuild_all`/`count`/`str↔int`/`to_thread` |
| `tests/unit/test_migrate_script.py` | fixture 旧 JSON → 迁移 → 验证 sqlite+chroma 内容、text 去空格、保留旧 id、复用旧向量、幂等 |

### 9.2 改造单元测试

| 文件 | 改造点 |
|---|---|
| `test_index_manager.py` | 重写为测薄编排（mock 两 store + providers）；sync 四阶段、`add_single_file` 回滚、去重替换、`not text` 移图、锁 |
| `test_ai_matcher.py` | mock `VectorStore.query` + `MetadataStore.get_entry`；`AIMatchCandidate(entry_id:int, image_path)` + rerank fallback |
| `test_keyword_searcher.py` | mock `MetadataStore`（`dict[int, MemeEntry]`）；`SearchResult.entry_id:int`、`image_path` |
| `test_rerank_service.py` | `AIMatchCandidate.image_path` |
| `test_deepseek_ocr.py` / `test_paddle_ocr.py` | 增加"返回无空格文本"断言 |

### 9.3 删除

| 文件 | 原因 |
|---|---|
| `tests/unit/engine/test_embedding_codec.py` | `encode/decode_embedding` 已删 |

### 9.4 插件测试改造

`test_meme_ai.py`（`AIMatchResult.image_path`）、`test_meme_search.py`/`test_meme_plain_text.py`/`test_search_utils.py`（`SearchResult.image_path`）、`test_meme_add.py`（`replaced_image_path`）、`test_meme_refresh.py`（结果类字段）、`test_app_state.py`（新增 getter）、`test_bot.py`（startup 注入）。

### 9.5 集成测试

`test_index_manager_api.py`（真实 sqlite 临时库 + chroma 临时目录端到端）、`test_ai_matcher_api.py`（`image_path` + 真实 VectorStore）、`test_rerank_service_api.py`（`AIMatchCandidate.image_path`）。

### 9.6 测试基础设施

`tests/conftest.py`（本次创建）：`tmp_sqlite_path`、`tmp_chroma_dir` fixture（基于 pytest `tmp_path`）。`pyproject.toml` 已配 `pytest`/`pytest-asyncio`，无需改。

### 9.7 验证命令

```bash
uv add chromadb                       # 新增依赖
uv run pytest                         # 全量测试
uv run python -m compileall bot tests # 语法检查
uv run python scripts/migrate_json_to_db.py   # 迁移脚本手动验证
```

---

## 10. 影响面与文档同步

### 10.1 代码影响面

新建：`bot/engine/metadata_store.py`、`bot/engine/vector_store.py`、`scripts/migrate_json_to_db.py`、`tests/unit/engine/test_metadata_store.py`、`tests/unit/engine/test_vector_store.py`、`tests/unit/test_migrate_script.py`、`tests/conftest.py`。

修改：`bot/engine/index_manager.py`（重写为薄编排）、`bot/engine/ai_matcher.py`、`bot/engine/keyword_searcher.py`、`bot/engine/deepseek_ocr.py`、`bot/engine/paddle_ocr.py`、`bot/engine/protocols.py`、`bot/engine/__init__.py`、`bot/app_state.py`、`bot/config.py`（新增 `INDEX_DB_PATH`/`CHROMA_DIR`）、`bot/bot.py`、`bot/plugins/_search_utils.py`、`bot/plugins/meme_ai.py`、`bot/plugins/meme_add.py`、`bot/Dockerfile`。

删除：`tests/unit/engine/test_embedding_codec.py`、`data/index.json`（迁移后手动）、`data/embeddings.json`（迁移后手动）。

### 10.2 依赖变更

- 新增：`chromadb`（`uv add chromadb`）
- 移除：`ujson`（仅 `index_manager.py` 用，新设计不再读写 JSON；迁移脚本用标准库 `json`）
- `sqlite3` 为标准库

### 10.3 文档同步清单

| 文档 | 同步点 |
|---|---|
| `docs/PRD.md` | 技术栈增 ChromaDB/sqlite3；OCR 文本=去除所有空白；AI 召回改 ChromaDB；索引管理删 text_hash 自动重建条款、新增阶段0一致性修复；索引文件格式改 sqlite schema + chroma collection；边界情况表更新；项目结构增删；依赖清单增 chromadb 移 ujson |
| `CONTEXT.md` | 术语：`index.json`→sqlite `index.db`、`embeddings.json`→chroma 向量库、去 `text_hash`、增 `image_path`/`speaker`/`标记词`、去重键=`text`、OCR 文本=去除所有空白 |
| `README.md` | 索引文件说明、项目结构、升级提示、依赖列表 |
| `.env.example` | 无新增环境变量 |
| `docker-compose.yml` | volume 已挂载 `./data`，无需改 |
| `docs/api/API.md` | 新增 `metadata_store.md`/`vector_store.md`；更新 `index_manager.md`/`ai_matcher.md`/`keyword_searcher.md`/`app_state.md`/`bot.md`/`config.md`；移除已删函数 |
| `CLAUDE.md` | "当前实现注意事项"更新模块清单；数据目录说明 |

### 10.4 风险与回滚

| 风险 | 缓解 |
|---|---|
| `chromadb` 依赖较重，Docker 镜像变大 | Dockerfile 用 `uv sync --no-dev`；接受体积增量 |
| chroma 版本兼容性 | `pyproject.toml` 锁版本下限 |
| 迁移误操作 | 脚本幂等 + 保留旧 JSON + 复用旧向量 |
| 回滚需求 | 旧 `index.json`/`embeddings.json` 保留，可回退旧代码 |
