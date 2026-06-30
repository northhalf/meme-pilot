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

先对 keyword 做分词 + 助词过滤，再用过滤后的文本做 LCS 匹配。
如果存在分数为 100 的结果，只返回分数为 100 的结果。

**搜索逻辑：**
1. 对 `keyword` 做 `jieba.posseg` 分词 + 词性标注，过滤助词类标签（`uj`/`ul`/`uz`/`us`/`y`/`e`）。
2. 去助词后若为空字符串，直接返回空列表。
3. 使用去助词后的文本与每条 OCR 文本做 LCS 匹配，过滤 `score < threshold` 的结果（全程统一使用 `threshold` 参数值，无特殊降阈逻辑）。
4. 若 `keyword`（去助词后）是 `text` 的子串，直接返回 100（精确命中）。
5. 否则使用 `pylcs.lcs_sequence_length(cleaned, text)` 计算最长公共子序列长度，相似度 = `(lcs_len / len(cleaned)) * 100`。
6. 如果存在分数为 100 的结果，过滤掉低于 100 的结果。

**依赖：** `jieba`（分词 + 词性标注）、`pylcs`（LCS 算法）。
