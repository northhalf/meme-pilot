# /search 命令 LCS 算法设计

> 日期：2026-06-26
> 状态：已批准

---

## 问题背景

当前 `/search` 命令使用 `rapidfuzz.fuzz.partial_ratio` 进行模糊匹配，存在以下问题：

1. **搜索「醉了」返回不相关结果**（如「好啊生死不明那就是死了」）
2. **搜索「我子敬真的是要醉了」返回不相关结果**（如「真的吗」）

**根因：** `partial_ratio` 是非对称的，当 keyword 比 text 长时，它会把 text 当作短串去匹配 keyword 的子串，导致错误的高分。

## 设计目标

1. 优先：keyword 完整出现在 text 中 → 精确命中（score=100）
2. 次优先：无精确命中时，用最长公共子序列（LCS）匹配
3. 关键要求：匹配分数与输入长度强关联

## 算法设计

### 评分公式

```
score = (LCS长度 / keyword长度) * 100
```

### 匹配逻辑

```python
import pylcs

def compute_similarity(keyword: str, text: str) -> float:
    # 1. 优先：精确子串匹配
    if keyword in text:
        return 100.0
    
    # 2. 次优先：最长公共子序列
    lcs_len = pylcs.lcs_sequence_length(keyword, text)
    return (lcs_len / len(keyword)) * 100
```

### 验证案例

| keyword | text | 匹配方式 | LCS | score | 结果 |
|---------|------|----------|-----|-------|------|
| `醉了` (2字) | `我子敬真的是要醉了` | 子串命中 | - | 100 | ✓ 返回 |
| `醉了` (2字) | `我醉生梦死了` | LCS | 2 | 100 | ✓ 返回 |
| `醉了` (2字) | `好啊生死不明那就是死了` | LCS | 1 | 50 | ✗ 过滤 |
| `我子敬真的是要醉了` (8字) | `真的吗` | LCS | 2 | 25 | ✗ 过滤 |

## 依赖变更

- **移除：** `rapidfuzz`
- **新增：** `pylcs>=0.1.1`（C++ 实现，支持中文）

## 代码修改

### `bot/engine/keyword_searcher.py`

1. 替换导入：`from rapidfuzz import fuzz` → `import pylcs`
2. 修改 `search` 方法中的评分逻辑

### `tests/unit/engine/test_keyword_searcher.py`

更新测试用例以适应新的 LCS 逻辑：
- `TestSearchFuzzy`：调整模糊匹配测试
- `TestSearchEdgeCases`：更新边界情况测试

### `docs/api/bot/engine/keyword_searcher.md`

更新算法说明文档。

## PRD 合规性

本设计完全符合 PRD 3.1 节的要求：

> - 关键词是 OCR 文本的连续子串时，相似度为 100（精确命中）
> - 关键词与 OCR 文本部分重叠时，按最长公共子序列比例计算相似度
> - 过滤保留 similarity >= 60 的结果，按分数降序排列，最多返回 Top 10
