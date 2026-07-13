# bot/engine/keyword_searcher.py — 关键词搜索 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

对 `MetadataStore` 中的 OCR 文本（已去除所有空白）使用 LCS（最长公共子序列）进行匹配。

## 依赖类型

- `MetadataStoreProvider`（`get_all_entries`）- 定义于 `protocols.py`，详见 [protocols.md](protocols.md)
- `SearchResult` - 定义于 `types.py`，详见 [types.md](types.md)

---

## `KeywordSearcher` 类

### `__init__(metadata_store: MetadataStoreProvider, threshold: float = 60.0, limit: int | None = None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `metadata_store` | `MetadataStoreProvider` | 必填 | 元数据数据来源，如 `MetadataStore` 实例 |
| `threshold` | `float` | `60.0` | 最低相似度阈值，低于此分数不返回 |
| `limit` | `int \| None` | `None` | 最大返回结果数；`None` 表示返回全部匹配 |

---

### `warm_up() -> None`

在 Bot 启动阶段显式加载 jieba 默认词典，避免首次进入 LCS 模糊回退时承担词典惰性初始化耗时。该方法不读取元数据，也不执行关键词搜索。

| | 类型 | 说明 |
|--|------|------|
| 返回 | None | jieba 默认词典初始化完成 |
| 异常 | Exception | jieba 初始化异常保持原类型向上传播，调用方应中止启动 |

预热使用 `@timed(logger, "关键词搜索预热")` 记录耗时；成功时记录“关键词搜索预热完成”。

---

### `search(keyword: str) -> list[SearchResult]`

| 参数 | 类型 | 说明 |
|------|------|------|
| `keyword` | `str` | 用户输入的关键词 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `list[SearchResult]` | 按 `similarity` 降序排列，最多 `limit` 条（`limit=None` 时返回全部匹配）；无匹配或关键词为空时返回 `[]` |

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
