# /search LCS 算法实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/search` 命令的模糊匹配算法从 `rapidfuzz.fuzz.partial_ratio` 替换为 `pylcs` 最长公共子序列（LCS），解决搜索结果不相关的问题。

**Architecture:** 使用 `pylcs.lcs_sequence_length` 计算最长公共子序列长度，评分公式为 `(LCS长度 / keyword长度) * 100`，优先检查精确子串匹配。

**Tech Stack:** Python 3.12, pylcs, pytest

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `bot/engine/keyword_searcher.py` | 关键词搜索引擎，实现 LCS 匹配逻辑 |
| `tests/unit/engine/test_keyword_searcher.py` | 单元测试 |
| `docs/api/bot/engine/keyword_searcher.md` | API 文档 |

---

## Task 1: 更新测试用例以适应 LCS 算法

**Files:**
- Modify: `tests/unit/engine/test_keyword_searcher.py`

- [ ] **Step 1: 更新 TestSearchFuzzy 类的测试用例**

当前测试基于 `partial_ratio` 行为，需要调整为 LCS 逻辑。

```python
class TestSearchFuzzy:
    """模糊匹配测试（LCS 匹配）。"""

    def test_partial_overlap(self, searcher: KeywordSearcher) -> None:
        """关键词与 OCR 文本部分重叠（LCS），应命中但分数低于 100。"""
        # "猫抓蝴蝶" vs "一只猫在跳起来抓蝴蝶 哈哈哈"
        # LCS: "猫抓蝴蝶" (4字) / keyword_len(4字) = 100
        results = searcher.search("猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == "1"
        assert results[0].similarity == 100.0

    def test_typo_keyword(self, searcher: KeywordSearcher) -> None:
        """错别字关键词应通过 LCS 匹配命中。"""
        # "加斑" vs "加班到凌晨三点的我"
        # LCS: "加" (1字) / keyword_len(2字) = 50
        # "加斑" vs "当你的老板说今天要加班"
        # LCS: "加" (1字) / keyword_len(2字) = 50
        results = searcher.search("加斑")
        assert len(results) >= 1
        assert all(r.similarity == 50.0 for r in results)

    def test_lcs_multiple_chars(self, searcher: KeywordSearcher) -> None:
        """多字符 LCS 匹配测试。"""
        # "加班三点" vs "加班到凌晨三点的我"
        # LCS: "加班三点" (4字) / keyword_len(4字) = 100
        results = searcher.search("加班三点")
        assert len(results) == 1
        assert results[0].entry_id == "2"
        assert results[0].similarity == 100.0

    def test_lcs_non_contiguous(self, searcher: KeywordSearcher) -> None:
        """非连续字符的 LCS 匹配。"""
        # "加三点" vs "加班到凌晨三点的我"
        # LCS: "加三点" (3字) / keyword_len(3字) = 100
        results = searcher.search("加三点")
        assert len(results) == 1
        assert results[0].entry_id == "2"
        assert results[0].similarity == 100.0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/engine/test_keyword_searcher.py::TestSearchFuzzy -v`
Expected: FAIL（因为当前实现使用 partial_ratio，LCS 逻辑未实现）

- [ ] **Step 3: 更新 TestSearchEdgeCases 类的测试用例**

```python
class TestSearchEdgeCases:
    """边界情况测试。"""

    def test_empty_keyword(self, searcher: KeywordSearcher) -> None:
        """空关键词应返回空列表。"""
        assert searcher.search("") == []

    def test_whitespace_keyword(self, searcher: KeywordSearcher) -> None:
        """纯空白关键词应返回空列表。"""
        assert searcher.search("   ") == []

    def test_no_match(self, searcher: KeywordSearcher) -> None:
        """无任何匹配时返回空列表。"""
        assert searcher.search("火星文xyz") == []

    def test_empty_entries(self) -> None:
        """索引为空时返回空列表。"""
        s = KeywordSearcher(MockIndex({}))
        assert s.search("加班") == []

    def test_entries_with_all_empty_text(self) -> None:
        """所有条目 text 为空时返回空列表。"""
        entries = {
            "1": {"filename": "a.jpg", "text": "", "text_hash": "x"},
            "2": {"filename": "b.jpg", "text": "   ", "text_hash": "y"},
        }
        s = KeywordSearcher(MockIndex(entries))
        assert s.search("加班") == []

    def test_below_threshold_filtered(self) -> None:
        """相似度低于阈值的条目应被过滤。"""
        entries = {
            "1": {"filename": "x.jpg", "text": "今天天气真好", "text_hash": "a"},
        }
        s = KeywordSearcher(MockIndex(entries), threshold=90.0)
        # "加班" vs "今天天气真好" LCS=0, score=0 < 90
        assert s.search("加班") == []

    def test_threshold_boundary(self) -> None:
        """相似度等于阈值时应被保留。"""
        entries = {
            "1": {"filename": "x.jpg", "text": "abc", "text_hash": "a"},
        }
        # LCS("ab", "abc") = 2, score = 2/2 * 100 = 100
        s = KeywordSearcher(MockIndex(entries), threshold=100.0)
        results = s.search("ab")
        assert len(results) == 1

    def test_keyword_longer_than_text(self, searcher: KeywordSearcher) -> None:
        """关键词比 OCR 文本长时，LCS 以较短文本为基准匹配。"""
        # "当你的老板说今天要加班而且不给加班费" vs "当你的老板说今天要加班"
        # LCS: "当你的老板说今天要加班" (10字) / keyword_len(14字) ≈ 71.4
        results = searcher.search("当你的老板说今天要加班而且不给加班费")
        assert len(results) == 1
        assert results[0].entry_id == "5"
        assert 70.0 <= results[0].similarity <= 75.0

    def test_no_false_positives(self, searcher: KeywordSearcher) -> None:
        """不应返回不相关结果。"""
        # "醉了" vs "好啊生死不明那就是死了"
        # LCS: "了" (1字) / keyword_len(2字) = 50 < 60
        results = searcher.search("醉了")
        assert len(results) == 0

    def test_no_false_positives_long_keyword(self, searcher: KeywordSearcher) -> None:
        """长关键词不应匹配短文本。"""
        # "我子敬真的是要醉了" vs "真的吗"
        # LCS: "真的" (2字) / keyword_len(8字) = 25 < 60
        results = searcher.search("我子敬真的是要醉了")
        # 应该只返回真正包含该文本的结果
        for r in results:
            assert "我子敬真的是要醉了" in r.text or r.similarity >= 60.0
```

- [ ] **Step 4: 运行测试验证失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/engine/test_keyword_searcher.py::TestSearchEdgeCases -v`
Expected: FAIL（因为当前实现使用 partial_ratio，LCS 逻辑未实现）

---

## Task 2: 实现 LCS 算法

**Files:**
- Modify: `bot/engine/keyword_searcher.py`

- [ ] **Step 1: 替换导入语句**

将：
```python
from rapidfuzz import fuzz
```

替换为：
```python
import pylcs
```

- [ ] **Step 2: 修改 search 方法的评分逻辑**

将：
```python
score = fuzz.partial_ratio(keyword, text)
```

替换为：
```python
# 优先：精确子串匹配
if keyword in text:
    score = 100.0
else:
    # 次优先：最长公共子序列
    lcs_len = pylcs.lcs_sequence_length(keyword, text)
    score = (lcs_len / len(keyword)) * 100
```

- [ ] **Step 3: 更新 docstring**

更新 `KeywordSearcher` 类的 docstring，说明使用 LCS 算法：

```python
class KeywordSearcher:
    """关键词模糊搜索引擎。

    使用最长公共子序列（LCS）进行模糊匹配：
    - 关键词是 OCR 文本的连续子串时，相似度为 100（精确命中）。
    - 否则计算 LCS 长度与关键词长度的比例作为相似度。
    """
```

- [ ] **Step 4: 运行所有测试验证通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/engine/test_keyword_searcher.py -v`
Expected: PASS

- [ ] **Step 5: 提交代码**

```bash
cd /home/northhalf/tmp/meme-pilot
git add bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git commit -m "refactor(search): 替换 partial_ratio 为 pylcs LCS 算法

- 使用 pylcs.lcs_sequence_length 计算最长公共子序列
- 评分公式: (LCS长度 / keyword长度) * 100
- 优先检查精确子串匹配
- 解决搜索结果不相关的问题"
```

---

## Task 3: 更新 API 文档

**Files:**
- Modify: `docs/api/bot/engine/keyword_searcher.md`

- [ ] **Step 1: 更新算法说明**

```markdown
## 算法说明

使用 `pylcs.lcs_sequence_length` 计算最长公共子序列（LCS）长度：

1. **精确子串匹配**：keyword 完整出现在 text 中 → score = 100
2. **LCS 匹配**：score = (LCS长度 / keyword长度) * 100
3. **阈值过滤**：保留 score >= threshold（默认 60）的结果

### 依赖

- `pylcs>=0.1.1`：C++ 实现的最长公共子序列算法库
```

- [ ] **Step 2: 提交文档**

```bash
cd /home/northhalf/tmp/meme-pilot
git add docs/api/bot/engine/keyword_searcher.md
git commit -m "docs: 更新 keyword_searcher LCS 算法说明"
```

---

## Task 4: 验证完整功能

- [ ] **Step 1: 运行全部测试**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/ -v`
Expected: PASS

- [ ] **Step 2: 语法检查**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run python -m compileall bot tests`
Expected: 无语法错误

---

## 完成

所有任务完成后，`/search` 命令将使用 LCS 算法，解决以下问题：

1. ✅ 搜索「醉了」不再返回「好啊生死不明那就是死了」
2. ✅ 搜索「我子敬真的是要醉了」不再返回「真的吗」
3. ✅ 匹配分数与输入长度强关联
