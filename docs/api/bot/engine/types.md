# bot/engine/types.py - 引擎层共用数据类型

> 本文档只记录模块对外接口。engine 包各模块共用的数据类型集中在此，避免重复定义。

## 数据类

### `SearchResult`

```python
@dataclass
class SearchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `entry_id` | `int` | 索引 id |
| `image_path` | `str` | `memes/` 目录下相对路径 |
| `text` | `str` | OCR 文本（无空格） |
| `similarity` | `float` | 相似度分数，范围 0–100 |
| `speaker` | `str \| None` | 说话人，可能为 `None` |
| `tags` | `list[str]` | 标记词列表 |

单条搜索结果。原定义于 `keyword_searcher.py`，R3 重构后移到本模块统一维护，供各搜索器共用。

被以下模块使用：

- `keyword_searcher.KeywordSearcher.search()` - 关键词匹配返回值
- `random_searcher.RandomSearcher.search_random()` - 随机取样返回值
- `semantic_searcher.SemanticSearcher.search_semantic()` - 语义召回返回值
