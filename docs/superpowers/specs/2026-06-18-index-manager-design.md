# IndexManager 索引增删改查模块 — 设计文档

> 日期：2026-06-18
> 状态：设计完成，待实现

## 1. 概述

`index_manager.py` 是 MemePilot 表情包机器人的索引增删改查核心模块，负责管理 `data/index.json` 和 `data/embeddings.json` 两个索引文件。

核心职责：
- 加载、校验、原子写入索引文件
- 启动时自动同步文件系统（新增/删除图片）
- 支持增量刷新（`/refresh`）
- 支持单条添加（`/add`）
- 支持查询（供 `keyword_searcher`、`ai_matcher` 使用）
- `text_hash` 一致性校验与自动修复
- ID 空洞复用（新增图片优先使用最小空洞 ID）

## 2. 架构

```
index_manager.py
├── 工具函数（模块级）
│   ├── normalize_text(text) -> str
│   └── compute_text_hash(text) -> str
│
├── 自定义异常
│   ├── IndexCorruptedError
│   └── IndexLockedError
│
├── Protocol 接口（依赖注入）
│   ├── OcrProvider       — async ocr(image_path) -> str
│   └── EmbeddingProvider  — async embed(text) -> list[float]
│
└── IndexManager 类
    ├── 加载/校验 → load(), validate_index()
    ├── 查询       → get_entries(), get_entry(), get_by_filename()
    ├── 写入       → save_index(), save_embeddings(), _atomic_write()
    ├── 同步       → sync_with_filesystem() (启动 & /refresh)
    ├── 增删       → add_entry(), remove_entry()
    ├── ID 管理   → _find_next_id()
    ├── 锁管理     → acquire_lock(), release_lock(), is_locked
    └── hash 维护  → _check_text_hash_consistency()
```

## 3. 组件设计

### 3.1 工具函数

```python
def normalize_text(text: str) -> str:
    """规范化 OCR 文本：去除首尾空白，合并连续空白。"""

def compute_text_hash(text: str) -> str:
    """对规范化后的文本计算 SHA-256，返回 "sha256:<hex>"。"""
```

### 3.2 自定义异常

```python
class IndexCorruptedError(Exception):
    """index.json 结构损坏或缺少必要字段。"""

class IndexLockedError(Exception):
    """索引更新锁被占用时尝试写入。"""
```

### 3.3 Protocol 接口

```python
class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str: ...

class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

与 `keyword_searcher.py` 中的 `IndexProvider` 协议风格一致，由外部插件层注入具体实现。

### 3.4 IndexManager 类

#### 构造函数

```python
class IndexManager:
    def __init__(
        self,
        data_dir: str = "data",
        memes_dir: str = "memes",
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
```

- `data_dir` — 索引文件目录
- `memes_dir` — 表情包图片目录
- `ocr_provider` / `embedding_provider` — 可选，OCR/embedding 未注入时只能做纯 JSON 操作

#### 核心属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `_entries` | `dict[str, dict]` | 内存中的 index entries |
| `_embeddings` | `dict[str, dict]` | 内存中的 embedding 数据 |
| `_lock` | `asyncio.Lock` | 写操作异步锁 |
| `_locked` | `bool` | 锁是否被持有 |
| `index_version` | `int` | 索引版本号 |

#### 查询方法（同步，实现 IndexProvider 协议）

| 方法 | 返回 | 说明 |
|------|------|------|
| `get_entries()` | `dict[str, dict]` | 返回全部 entries，符合 `IndexProvider` 协议 |
| `get_entry(entry_id)` | `dict \| None` | 按 ID 查询单条 |
| `get_by_filename(name)` | `dict \| None` | 按文件名查询 |
| `entry_count` | `int` | property，返回条目数 |

#### 加载/校验方法

| 方法 | 说明 |
|------|------|
| `load()` | 加载 index.json + embeddings.json，执行校验 |
| `validate_index(data)` | 校验 index.json 结构完整性 |
| `_load_index()` | 读取并解析 index.json |
| `_load_embeddings()` | 读取并解析 embeddings.json |

**加载流程**：
1. 如果 `index.json` 不存在 → 初始化为空 `{"version": 1, "entries": {}}`
2. 如果 `index.json` 存在 → 解析 JSON，校验结构
3. JSON 语法错误或缺少必要字段 → 抛出 `IndexCorruptedError`
4. 遍历 entries，校验每条记录含 `filename`、`text`、`text_hash`
5. 调用 `_check_text_hash_consistency()` 检查并自动修复
6. 如果 `embeddings.json` 不存在或损坏但 index 有效 → 标记 `_embeddings_stale = True`

#### 写入方法（原子替换）

| 方法 | 说明 |
|------|------|
| `save_index()` | 原子写入 index.json |
| `save_embeddings()` | 原子写入 embeddings.json |
| `_atomic_write(path, data)` | 先写 `.tmp`，成功后再 `os.replace()` |

**原子写入流程**：
1. 序列化为 JSON，写入 `path.tmp`
2. `os.replace(path.tmp, path)` 原子替换
3. 写入失败不破坏现有文件

#### ID 管理

```python
def _find_next_id(self) -> str:
    """查找下一个可用 ID。
    
    优先复用最小空洞 ID，无空洞时返回当前最大 ID + 1。
    ID 格式为数字字符串：'1', '2', '3' ...
    """
```

#### 同步方法（async）

```python
async def sync_with_filesystem(self) -> SyncResult:
    """按文件名同步索引与 memes/ 目录。
    
    1. 扫描 memes/ 获取当前图片文件列表
    2. 对比 index entries 找出新增和已删除文件
    3. 对新增图片：OCR → embedding → 写入索引
    4. 删除已不存在的图片记录
    5. 原子写入更新后的索引文件
    
    Returns:
        SyncResult(added, deleted, failed)
    """
```

**新增图片处理顺序**：按文件名升序处理。每张新增图片优先复用最小空洞 ID。

**压缩**：由调用方（插件层或启动层）在调用 `sync_with_filesystem()` 之前对新增图片执行压缩。IndexManager 不直接调用压缩逻辑——保持职责单一。

#### 单条增删

| 方法 | 说明 |
|------|------|
| `add_entry(filename, text, embedding)` | 添加单条记录，返回分配的 ID |
| `remove_entry(entry_id)` | 按 ID 删除记录 |

`add_entry` 内部调用 `_find_next_id()` 分配 ID，然后更新内存中的 `_entries` 和 `_embeddings`，最后原子写入磁盘。

#### 锁管理

```python
def acquire_lock(self) -> bool:
    """非阻塞尝试获取更新锁。成功返回 True，已锁定返回 False。"""

def release_lock(self) -> None:
    """释放更新锁。"""

@property
def is_locked(self) -> bool:
    """锁是否被持有。"""
```

使用 `asyncio.Lock`。`acquire_lock()` 使用 `locked()` 检查状态后调用 `acquire()`（非阻塞）。插件层在调用同步/添加前尝试获取锁，失败时回复"索引正在更新，请稍后再试"。

#### text_hash 维护

```python
def _check_text_hash_consistency(self) -> list[str]:
    """校验所有条目的 text_hash。
    
    对每条 entry，重新计算 text_hash 并与存储值比较。
    不一致时自动更新 hash，并返回不一致的 ID 列表（其 embedding 需重建）。
    """
```

## 4. 数据流

### 4.1 启动同步

```
Bot 启动
  → IndexManager(data_dir, memes_dir)
  → index_mgr.load()                    # 加载/校验索引文件
  → index_mgr.sync_with_filesystem()    # 扫描 memes/，同步新增/删除
  → Bot 业务就绪
```

### 4.2 /refresh 命令

```
用户发送 /refresh
  → 插件层: index_mgr.acquire_lock()
  → 成功: index_mgr.sync_with_filesystem()
  → 插件层: index_mgr.release_lock()
  → 回复用户摘要
```

### 4.3 /add 命令

```
用户发送 /add
  → 插件层下载图片到 memes/
  → 插件层执行图片压缩
  → 插件层调用 ocr_provider.ocr(image_path)
  → 插件层调用 embedding_provider.embed(text)
  → 插件层: index_mgr.acquire_lock()
  → 成功: index_mgr.add_entry(filename, text, embedding)
  → 插件层: index_mgr.release_lock()
  → 回复用户成功
```

### 4.4 /search 查询

```
用户发送 /search
  → keyword_searcher.search(keyword)
  → keyword_searcher 调用 index_mgr.get_entries()
  → 内存查询，无磁盘 I/O
```

## 5. 错误处理

| 场景 | 行为 |
|------|------|
| `index.json` 不存在 | 初始化为空 index |
| `index.json` JSON 语法损坏 | 抛出 `IndexCorruptedError`，拒绝启动/刷新 |
| `index.json` 缺少 `version` 或 `entries` 字段 | 抛出 `IndexCorruptedError` |
| `index.json` entry 缺少 `filename`/`text`/`text_hash` | 抛出 `IndexCorruptedError` |
| `embeddings.json` 不存在 | `_embeddings_stale = True`，后续自动重建 |
| `embeddings.json` JSON 语法损坏 | `_embeddings_stale = True` |
| `text_hash` 不一致 | 自动更新 hash，标记对应 embedding 过期 |
| 原子写入中 `os.replace()` 失败 | 记录日志，保留旧文件 |
| `memes/` 目录不存在 | 自动创建 |
| `memes/` 目录为空 | 正常返回，sync 结果为 0/0/0 |
| 锁被占用时尝试写入 | `acquire_lock()` 返回 False，由调用方回复 |

## 6. 测试策略

测试目录：`tests/unit/engine/test_index_manager.py`

### 测试用例规划

| 类别 | 测试点 |
|------|--------|
| 工具函数 | `normalize_text` 空白处理；`compute_text_hash` 确定性 |
| 加载 | 空目录加载；有效 index 加载；损坏 JSON 拒绝；缺少字段拒绝；embeddings 损坏自动标记重建 |
| 校验 | 有效数据通过；各缺少字段情况拒绝 |
| 查询 | `get_entries`、`get_entry`、`get_by_filename`、`entry_count` |
| ID 分配 | 空索引从 1 开始；空洞复用；无空洞取 max+1 |
| 增删 | `add_entry` 分配 ID 并写入；`remove_entry` 删除并清理 |
| 原子写入 | 正常写入；写入失败不破坏原文件 |
| text_hash | 一致不触发更新；不一致自动修复 |
| 锁 | 获取成功；重复获取失败（跳过非阻塞）；释放后可再获取 |
| 同步 | 无变化；新增图片；删除图片；混合场景 |

## 7. 依赖

- Python 第三方库：`ujson`（高性能 JSON 解析/序列化）
- Python 标准库：`hashlib`、`pathlib`、`os`、`shutil`、`logging`、`asyncio`
- 通过 Protocol 依赖外部 OCR 和 Embedding 服务（注入）
