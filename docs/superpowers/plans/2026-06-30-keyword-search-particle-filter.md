# 关键词搜索助词过滤 + 阈值统一 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对 keyword 用 jieba.posseg 过滤助词后搜索，删除 ≤2 字特殊 50 阈值，全程统一 threshold=60。

**Architecture:** 在 keyword_searcher.py 中新增 `_remove_particles()` 模块级函数，修改 `search()` 方法在匹配前做去助词和空白清除。SearchResult、IndexProvider 等接口不变。

**Tech Stack:** Python 3.12, jieba 中文分词, pylcs LCS 模糊匹配

---

### Task 1: 安装 jieba 依赖

**Files:**
- Modify: `pyproject.toml`（`uv add` 自动管理）

- [ ] **Step 1: uv add jieba**

```bash
uv add jieba
```

Expected: `jieba` added to `[project]dependencies` in pyproject.toml

- [ ] **Step 2: 验证安装**

```bash
uv run python -c "import jieba.posseg; print(jieba.__version__)"
```

Expected: 输出 jieba 版本号（如 `0.42.1`）

---

### Task 2: 更新测试文件

**Files:**
- Modify: `tests/unit/engine/test_keyword_searcher.py`

- [ ] **Step 1: 新增去助词单元测试**

在文件末尾新增测试类。注意：`_remove_particles` 是模块级函数，直接从 module 导入。

```python
class TestParticleRemoval:
    """_remove_particles 函数行为测试。"""

    def test_removes_structural_particle(
        self,
    ) -> None:
        """结构助词"的"(uj)应被过滤。"""
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("的加班") == "加班"

    def test_removes_modal_particle(self) -> None:
        """语气词"了吗"(ul)应被过滤。"""
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("加班了吗") == "加班"

    def test_keeps_content_word_with_particle_char(
        self,
    ) -> None:
        """实词"了解"中"了"是动词(v)，不应被过滤。"""
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("了解") == "了解"

    def test_all_particles_returns_empty(self) -> None:
        """全助词返回空字符串。"""
        from bot.engine.keyword_searcher import _remove_particles

        result = _remove_particles("的呢吗")
        assert "".join(result.split()) == ""

    def test_no_particles_unchanged(self) -> None:
        """不含助词的文本原样返回。"""
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("加班") == "加班"
```

- [ ] **Step 2: 运行测试确认新增测试失败（因为 _remove_particles 还不存在）**

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py::TestParticleRemoval -v
```

Expected: ModuleNotFoundError 或 ImportError（_remove_particles 未定义）

- [ ] **Step 3: 修改短关键词阈值测试类**

将 `TestSearchFuzzyWithShortKeyword` 类重命名为 `TestSearchFuzzyEdgeCases`，删除第 157-178 行的 `test_two_char_keyword_fuzzy_score_50_is_included`（该测试验证 score=50 能过，新逻辑下 50<60 应过滤）。

保留的测试：
- `test_two_char_keyword_fuzzy_score_below_50_still_excluded`（score=0 < 60 仍被过滤）
- `test_three_plus_char_keyword_still_uses_original_threshold`（仍测试统一阈值）

删除代码（第 159-178 行）：

```python
# 整个 test_two_char_keyword_fuzzy_score_50_is_included 方法删除
def test_two_char_keyword_fuzzy_score_50_is_included(self) -> None:
    """2 字关键词模糊匹配分数=50 时应命中。"""
    ...
```

- [ ] **Step 4: 新增去助词后的搜索行为测试**

在 `TestParticleRemoval` 类中或新增类：

```python
class TestSearchWithParticleRemoval:
    """去助词后的搜索行为测试。"""

    def test_search_drops_particles_from_keyword(
        self,
        sample_entries: dict[str, dict[str, str]],
    ) -> None:
        """"
        搜索"了加班吗"→ 去助词后剩"加班" → 匹配 3 个包含加班的条目。
        """
        searcher = KeywordSearcher(MockIndex(sample_entries))
        results = searcher.search("了加班吗")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        ids = {r.entry_id for r in results}
        assert ids == {"2", "5", "6"}

    def test_search_all_particles_returns_empty(
        self,
        sample_entries: dict[str, dict[str, str]],
    ) -> None:
        """搜索纯助词应返回空结果。"""
        searcher = KeywordSearcher(MockIndex(sample_entries))
        results = searcher.search("的呢吗")
        assert len(results) == 0

    def test_search_content_word_with_embedded_particle_char(
        self,
    ) -> None:
        """"
        搜索"了解"（了为实词）应正常匹配。
        """
        entries = {
            "1": {
                "filename": "a.jpg",
                "text": "了解详情请咨询",
                "text_hash": "x",
            },
        }
        searcher = KeywordSearcher(MockIndex(entries))
        results = searcher.search("了解")
        assert len(results) == 1
        assert results[0].similarity == 100.0

    def test_two_char_fuzzy_below_60_filtered(self) -> None:
        """"
        2 字关键词模糊匹配 score=50 < 60 → 应被过滤。
        """
        entries = {
            "1": {
                "filename": "x.jpg",
                "text": "加班到凌晨",
                "text_hash": "a",
            },
        }
        searcher = KeywordSearcher(MockIndex(entries))
        # "加a": 2 字，LCS vs "加班到凌晨" = 1（加），score = 50 < 60
        results = searcher.search("加a")
        assert len(results) == 0
```

- [ ] **Step 5: 运行所有 keyword_searcher 测试，确认红**

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -v
```

Expected: 部分新增测试红（`_remove_particles` 未定义，`search()` 未实现去助词）

---

### Task 3: 修改 keyword_searcher.py

**Files:**
- Modify: `bot/engine/keyword_searcher.py`

- [ ] **Step 1: 新增 jieba 导入和 POS 常量**

在文件顶部 import 区域（`import pylcs` 之后）新增：

```python
import jieba.posseg as pseg
```

在 `logger` 定义之后新增：

```python
# jieba 词性标注中与助词相关的标签
# uj=助词(的/地/得), ul=语气词(吗/呢/吧), uz=时态助词(着/了/过)
# us=结构助词(所/得以), e=叹词(嗯/哦)
_PARTICLE_POS_TAGS: frozenset[str] = frozenset({'uj', 'ul', 'uz', 'us', 'e'})
```

- [ ] **Step 2: 新增 `_remove_particles` 函数**

在 `KeywordSearcher` 类定义之前新增：

```python
def _remove_particles(text: str) -> str:
    """使用 jieba.posseg 过滤助词，返回纯文本。

    对 text 做分词 + 词性标注，移除助词类标签（uj/ul/uz/us/e）的词位，
    保留非助词部分的原始字符和顺序。

    Args:
        text: 待处理的文本（搜索关键词）。

    Returns:
        移除助词后的纯文本；如果全部为助词则返回空字符串。
    """
    return "".join(word for word, flag in pseg.cut(text)
                   if flag not in _PARTICLE_POS_TAGS)
```

- [ ] **Step 3: 修改 `search()` 方法**

将原方法 108-154 行替换为：

```python
    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        先对 keyword 做分词 + 助词过滤，再用过滤后的文本做 LCS 匹配。

        Args:
            keyword: 用户输入的搜索关键词。

        Returns:
            按相似度降序排列的搜索结果列表，最多返回 limit 条。
            无匹配时返回空列表。
        """
        keyword = keyword.strip()
        if not keyword:
            logger.debug("关键词为空，返回空结果")
            return []

        # 去助词后搜索
        cleaned = _remove_particles(keyword)
        cleaned = "".join(cleaned.split())  # 删除所有空白字符
        if not cleaned:
            logger.debug("关键词去助词后为空，返回空结果")
            return []

        entries = self._index_provider.get_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        results: list[SearchResult] = []

        for entry_id, entry in entries.items():
            text = entry.get("text", "").strip()
            if not text:
                continue

            score = self._compute_similarity(cleaned, text)
            if score >= self._threshold:
                results.append(
                    SearchResult(
                        entry_id=entry_id,
                        filename=entry.get("filename", ""),
                        text=text,
                        similarity=score,
                    )
                )

        results.sort(key=lambda r: r.similarity, reverse=True)

        # 如果存在分数为 100 的结果，只返回分数为 100 的结果
        perfect_results = [r for r in results if r.similarity == 100.0]
        if perfect_results:
            results = perfect_results

        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            min(len(results), self._limit),
        )

        return results[: self._limit]
```

- [ ] **Step 4: 运行测试确认全绿**

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -v
```

Expected: 全部测试 PASS

---

### Task 4: 更新 API 文档

**Files:**
- Modify: `docs/api/bot/engine/keyword_searcher.md`

- [ ] **Step 1: 更新文档中的搜索逻辑说明**

将原文档第 66-69 行：

```
**搜索逻辑：**
- 对每条 OCR 文本使用 LCS（最长公共子序列）计算相似度，过滤 `score < threshold` 的结果。
- 关键词 ≤ 2 字时，有效阈值降为 50（而非 `threshold` 参数的值），使短关键词更易模糊匹配。
- 如果存在分数为 100 的结果，只返回分数为 100 的结果。
```

替换为：

```
**搜索逻辑：**
- 对 keyword 使用 jieba.posseg 做分词 + 词性标注，过滤助词类（的、了、吗、呢、吧等）后再执行匹配。
- 过滤助词后的 keyword 清除所有空白字符；若全部为助词则直接返回空结果。
- 对每条 OCR 文本使用 LCS（最长公共序列）计算相似度，过滤 `score < threshold` 的结果。
- 全程使用统一的 `threshold` 参数（默认 60），不按关键词长度做特殊处理。
- 如果存在分数为 100 的结果，只返回分数为 100 的结果。
```

---

### Task 5: 全量测试验证

- [ ] **Step 1: 运行全量测试和语法检查**

```bash
uv run pytest -v
uv run python -m compileall bot tests
```

Expected: 测试全 PASS，语法检查无错误

- [ ] **Step 2: 告知用户结果，等待用户审核提交**
