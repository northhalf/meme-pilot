# bot/engine/vector_store.py — 向量存储 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

基于 chromadb `PersistentClient` 的向量存储。仅存 `id`（与 sqlite 完全一一对应）+ `embedding`（1024 维 float32），HNSW cosine 索引，`query` 返回 Top-N `(entry_id, similarity)`。

设计要点：
- chroma 为同步库；`upsert`/`remove`/`remove_many`/`query`/`rebuild_all` 用 `asyncio.to_thread` 包装以避免阻塞事件循环；`load()`/`close()`/`count()` 为同步方法（仅供启动期或已在线程中调用）。
- chroma 并发写冲突 → 内部 `threading.Lock` 串行化所有访问。
- `id` 在内部与 chroma 交互时转 `str`，对外保持 `int`。
- `similarity = 1 - distance`（cosine distance ∈ [0, 2]）。
- `remove` 不存在静默：chromadb `delete` 对不存在 id 本身即静默，无需捕获。

## 数据类

### `VectorHit`

```python
@dataclass
class VectorHit:
    entry_id: int
    similarity: float
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id（int，与 sqlite 一一对应） |
| `similarity` | `float` | 余弦相似度，= `1 - distance` |

---

## `VectorStore` 类

### `__init__(chroma_path: str, collection_name: str = "memes") -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `chroma_path` | `str` | 必填 | chroma `PersistentClient` 数据目录，`load()` 时自动创建 |
| `collection_name` | `str` | `"memes"` | collection 名 |

---

### `load() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

创建 `PersistentClient` 并 `get_or_create_collection`（`metadata={"hnsw:space": "cosine"}`）。若已存在旧 client，先 `close()` 再打开。`load()` 前不可调用其他方法。

---

### `close() -> None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

调用 chromadb `PersistentClient.close()` 释放系统资源并置空引用；重复调用安全。

---

### `async upsert(entry_id: int, embedding: list[float]) -> None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id（内部转 `str` 写入 chroma） |
| `embedding` | `list[float]` | 向量值列表 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

插入或覆盖一条向量。

---

### `async remove(entry_id: int) -> None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 待删除向量 id |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

删除一条向量，不存在静默（chromadb `delete` 本身即静默）。

---

### `async remove_many(entry_ids: list[int]) -> None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `entry_ids` | `list[int]` | 待删除向量 id 列表 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

批量删除向量，不存在的静默。空列表时直接返回。

---

### `async query(query_embedding: list[float], n_results: int = 10) -> list[VectorHit]`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `query_embedding` | `list[float]` | 必填 | 查询向量 |
| `n_results` | `int` | `10` | 召回条数 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `list[VectorHit]` | 召回 Top-N，`entry_id` 转 `int`；collection 为空或无结果时返回 `[]` |

`similarity = 1 - distance`。

---

### `async rebuild_all(items: list[tuple[int, list[float]]]) -> None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `items` | `list[tuple[int, list[float]]]` | `(entry_id, embedding)` 列表 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |

全量重建：先删除 collection 后重建（清空全部向量），再批量写入 `items`。供 `IndexManager.refresh()` 阶段0 全量重 embed 使用。

---

### `count() -> int`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 当前 collection 内向量数（同步方法） |
