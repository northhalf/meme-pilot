# 设计文档：关键词搜索助词过滤 + 阈值统一

> 日期：2026-06-30
> 状态：已批准

---

## 1. 目标

1. 在关键词搜索时，用 `jieba` 分词 + 词性标注（POS tagging）过滤掉 keyword 中的助词，再执行匹配。
2. 删除原有 ≤2 字关键词降低阈值为 50 的特殊逻辑，全程统一使用 threshold=60。
3. 以上变化使 2 字关键词若只匹配 1 字（score=50）自动被 60 阈值过滤，无需单独处理。

## 2. 方案

### 2.1 新增依赖

```
uv add jieba
```

`jieba` 为纯 Python 中文分词库，支持 `jieba.posseg` 词性标注。

### 2.2 助词识别

使用 `jieba.posseg` 对 keyword 做分词 + 词性标注，过滤掉助词类标签的词位。

**过滤的 POS 标签：**

| 标签 | 类别 | 示例 |
|------|------|------|
| `uj` | 助词 | 的、地、得 |
| `ul` | 语气词 | 吗、呢、吧、啊、呀、哇、嘛、哈 |
| `uz` | 时态助词 | 着、了、过 |
| `us` | 结构助词 | 所、得以 |
| `e` | 叹词 | 嗯、哦 |

仅对 keyword 做过滤，OCR text 不做处理。

### 2.3 代码变更

**`bot/engine/keyword_searcher.py`**

新增模块级函数：

```python
def _remove_particles(text: str) -> str:
    """使用 jieba.posseg 过滤助词。"""
    return ''.join(word for word, flag in pseg.cut(text)
                   if flag not in _PARTICLE_POS_TAGS)
```

修改 `search()` 方法：

```python
def search(self, keyword: str) -> list[SearchResult]:
    keyword = keyword.strip()
    if not keyword:
        return []

    cleaned = _remove_particles(keyword)
    cleaned = ''.join(cleaned.split())  # 删除所有空白字符
    if not cleaned:
        return []

    entries = self._index_provider.get_entries()
    ...
    for entry_id, entry in entries.items():
        text = entry.get("text", "").strip()
        if not text:
            continue
        score = self._compute_similarity(cleaned, text)  # 使用 cleaned
        if score >= self._threshold:                      # 统一 threshold
            ...
```

### 2.4 阈值变化

| 场景 | 当前 | 新 |
|------|------|----|
| keyword="加班"(2字) | threshold=50 | threshold=60 |
| keyword="的了"(全助词) | threshold=50 → 模糊匹配 | 返回空结果 |
| keyword="的加班"(去助词→"加班") | threshold=50 | threshold=60 |
| keyword="今天加班"(4字) | threshold=60 | threshold=60(不变) |

2 字匹配效果：keyword="加x" vs text="加班到凌晨" → LCS=1 → score=50 < 60 → 自动过滤。

### 2.5 测试变更

**`tests/unit/engine/test_keyword_searcher.py`**

- `TestSearchFuzzyWithShortKeyword` 类中删除 `test_two_char_keyword_fuzzy_score_50_is_included`（验证 score=50 能过，新逻辑下应过滤）
- `test_two_char_keyword_fuzzy_score_below_50_still_excluded` 保留（score=0 < 60 仍不过）
- `test_three_plus_char_keyword_still_uses_original_threshold` 保留（语义改为"所有关键词使用统一阈值"）
- 新增 `TestRemoveParticles` 测试类

### 2.6 API 文档

**`docs/api/bot/engine/keyword_searcher.md`**

- 删除 ≤2 字降阈至 50 的说明
- 新增去助词逻辑说明

## 3. 边界情况

| 场景 | 行为 |
|------|------|
| keyword 全为助词/叹词 | 去助词后为空，返回空列表 |
| keyword 不含助词 | 行为与现有一致 |
| jieba 首次加载 | 自动加载词典，毫秒级初始化延迟 |
| keyword 为英文/数字 | jieba 按字符原样返回，不受影响 |
| 去助词后字符顺序不变 | 仅移除助词词位，非助词的相对顺序和原始字符保留 |

## 4. 未涵盖

- 不对 OCR text 做去助词处理
- 不修改去重键逻辑
- 不影响 `/ai` 命令
- 不影响 `/add` 命令
- 不影响 `/refresh` 和 sync_with_filesystem 逻辑
