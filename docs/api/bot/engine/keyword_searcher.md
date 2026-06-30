# bot/engine/keyword_searcher.py — 关键词搜索 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

对 `MetadataStore` 中的 OCR 文本（已去除所有空白）使用 LCS（最长公共子序列）进行匹配。

## Protocol

### `MetadataStoreProvider`

```python
class MetadataStoreProvider(Protocol):
    def get_all_entries(self) -> dict[int, MemeEntry]: ...
```

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_all_entries` | 无 | `dict[int, MemeEntry]` | key 为 `int(id)`，value 为 `MemeEntry`（含 `image_path`、`text` 等） |

`KeywordSearcher` 依赖此协议获取元数据条目，而非直接依赖具体的 `MetadataStore` 实现，便于测试用 mock 替换。

---

## 数据类

### `SearchResult`

```python
@dataclass
class SearchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id |
| `image_path` | `str` | `memes/` 目录下相对路径 |
| `text` | `str` | OCR 文本（无空格） |
| `similarity` | `float` | 相似度分数，范围 0–100 |

---

## `KeywordSearcher` 类

### `__init__(metadata_store: MetadataStoreProvider, threshold: float = 60.0, limit: int = 10) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `metadata_store` | `MetadataStoreProvider` | 必填 | 元数据数据来源，如 `MetadataStore` 实例 |
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

先对 keyword 做分词 + 助词过滤 + 去所有空白，再用过滤后的文本做 LCS 匹配。
如果存在分数为 100 的结果，只返回分数为 100 的结果。

**搜索逻辑：**
1. 对 `keyword` 做 `jieba.posseg` 分词 + 词性标注，过滤助词类标签（`uj`/`ul`/`uz`/`us`/`y`/`e`）。
2. 去助词后再去除所有空白字符；结果为空字符串则直接返回空列表。
3. 使用去助词、去空白后的文本与每条 OCR 文本（`MetadataStore.get_all_entries()` 返回的 `entry.text`）做 LCS 匹配，过滤 `score < threshold` 的结果（全程统一使用 `threshold` 参数值，无特殊降阈逻辑）。
4. 若 `keyword`（清洗后）是 `text` 的子串，直接返回 100（精确命中）。
5. 否则使用 `pylcs.lcs_sequence_length(cleaned, text)` 计算最长公共子序列长度，相似度 = `(lcs_len / len(cleaned)) * 100`。
6. 如果存在分数为 100 的结果，过滤掉低于 100 的结果。

**依赖：** `jieba`（分词 + 词性标注）、`pylcs`（LCS 算法）。
