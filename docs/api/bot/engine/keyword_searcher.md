# bot/engine/keyword_searcher.py — 关键词搜索 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## Protocol

### `IndexProvider`

```python
class IndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_entries` | 无 | `dict[str, dict[str, str]]` | key 为索引 ID，value 为包含 `filename`、`text`、`text_hash` 的字典 |

---

## 数据类

### `SearchResult`

```python
@dataclass
class SearchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `str` | 索引 ID，如 `"1"` |
| `filename` | `str` | 表情包文件名，如 `"cat_jump.jpg"` |
| `text` | `str` | OCR 文本 |
| `similarity` | `float` | 相似度分数，范围 0–100 |

---

## `KeywordSearcher` 类

### `__init__(index_provider: IndexProvider, threshold: float = 60.0, limit: int = 10) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `index_provider` | `IndexProvider` | 必填 | 索引数据来源，如 `IndexManager` 实例 |
| `threshold` | `float` | `60.0` | 最低相似度阈值，低于此分数不返回 |
| `limit` | `int` | `10` | 最大返回结果数 |

---

### `search(keyword: str) -> list[SearchResult]`

| 参数 | 类型 | 说明 |
|------|------|------|
| `keyword` | `str` | 用户输入的关键词 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `list[SearchResult]` | 按 `similarity` 降序排列，最多 `limit` 条；无匹配或关键词为空时返回 `[]` |

对每条 OCR 文本使用 LCS（最长公共子序列）计算相似度，过滤 `score < threshold` 的结果。如果存在分数为 100 的结果，只返回分数为 100 的结果。

**算法逻辑：**

1. 若 `keyword` 是 `text` 的子串，直接返回 100（精确命中）。
2. 否则使用 `pylcs.lcs_sequence_length(keyword, text)` 计算最长公共子序列长度，相似度 = `(lcs_len / len(keyword)) * 100`。
3. 如果存在分数为 100 的结果，过滤掉低于 100 的结果。

**依赖：** `pylcs`（替代早期版本使用的 `rapidfuzz`）。
