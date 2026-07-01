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

两层短路匹配：先用「原始输入去所有空白、保留助词」的关键词做精确子串匹配，命中则只返回这些条目（similarity=100.0）；未命中才回退到「去助词 + 去空白」的关键词走 LCS 模糊匹配。

**搜索逻辑：**
1. 对 `keyword` 做 `strip`；为空则返回 `[]`。
2. 第一层「精确子串」：`raw = _strip_all_whitespace(keyword)`（去除所有空白字符，保留助词）。遍历 `entry.text`，若 `raw in text` 则命中，`similarity = 100.0`。命中集非空则只返回这些条目（按 `entries` 读出顺序，截断至 `limit`）。
3. 第二层「LCS 模糊回退」（仅当第一层未命中时启用）：`cleaned = _strip_all_whitespace(_remove_particles(keyword))`（`jieba.posseg` 分词过滤助词 `uj`/`ul`/`uz`/`us`/`y`/`e` + 去所有空白）；为空则返回 `[]`。
4. 对每条 `entry.text` 计算 `_compute_similarity(cleaned, text)`：若 `cleaned in text` 返回 100；否则 `pylcs.lcs_sequence_length(cleaned, text)` 计算相似度 `(lcs_len / len(cleaned)) * 100`。
5. 过滤 `score < threshold` 的结果，按 `similarity` 降序排列。
6. 若存在分数为 100 的结果，只保留 100 分结果。
7. 截断至 `limit` 返回。

**依赖：** `jieba`（分词 + 词性标注）、`pylcs`（LCS 算法）。
