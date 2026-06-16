# KeywordSearcher 设计文档

> 日期：2026-06-16
> 模块：`bot/engine/keyword_searcher.py`
> 对应 PRD：第 3.1 节 功能一：关键词搜索

## 1. 概述

`KeywordSearcher` 负责对 `index.json` 中的 OCR 文本执行关键词模糊搜索，返回匹配的表情包列表。

## 2. 数据结构

### SearchResult

```python
@dataclass
class SearchResult:
    entry_id: str      # 索引 id，如 "001"
    filename: str      # 文件名，如 "cat_jump.jpg"
    text: str          # OCR 文本
    similarity: float  # 相似度 0-100
```

## 3. 匹配算法

使用 `fuzz.partial_ratio` 单阶段匹配：

- 关键词是 OCR 文本的连续子串 → similarity = 100（精确命中）
- 关键词与 OCR 文本部分重叠 → 按最长公共子串比例计算 similarity
- 阈值 >= 60（可配置）
- 按 similarity 降序排列，Top 10 截断

## 4. 类设计

```python
class KeywordSearcher:
    def __init__(self, index_provider, threshold=60.0, limit=10)
    def search(self, keyword: str) -> list[SearchResult]
```

- `index_provider`：需实现 `IndexProvider` Protocol（提供 `get_entries() -> dict[str, dict]` 方法）
- `threshold`：最低相似度阈值
- `limit`：最大返回数

## 5. 依赖

- `rapidfuzz`（fuzz.partial_ratio）
- `dataclasses`（标准库）
- `logging`（标准库）

## 6. 边界情况

| 场景 | 行为 |
|------|------|
| 关键词为空或纯空白 | 返回 [] |
| entries 为空 | 返回 [] |
| text 为空或纯空白的条目 | 跳过不参与匹配 |
| 相似度低于阈值 | 过滤不返回 |
| 结果超过 limit | Top N 截断 |
| 无任何匹配 | 返回 [] |
