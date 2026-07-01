# 关键词搜索：精确子串优先 + LCS 模糊回退 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `KeywordSearcher.search()` 改为两层短路：第一层用「原始输入去所有空白、保留助词」的关键词做精确子串匹配，命中即只返回子串命中条目；未命中时回退到现有「去助词 + LCS 模糊」匹配。

**Architecture:** 在 `keyword_searcher.py` 新增模块级函数 `_strip_all_whitespace` 与两个私有方法 `_search_exact_substring` / `_search_fuzzy_lcs`，`search()` 退化为纯编排（预处理 → 第一层短路 → 第二层回退）。`_compute_similarity` / `_remove_particles` / `SearchResult` / `__init__` 签名全部不动。TDD：先加测试再改实现。

**Tech Stack:** Python 3.12、pytest、pylcs、jieba.posseg、uv。

**Spec:** `docs/superpowers/specs/2026-07-01-keyword-exact-substring-priority-design.md`

---

## 文件结构

| 文件 | 责任 | 操作 |
|------|------|------|
| `bot/engine/keyword_searcher.py` | 关键词搜索引擎；新增 `_strip_all_whitespace`、`_search_exact_substring`、`_search_fuzzy_lcs`，重写 `search()` | 修改 |
| `tests/unit/engine/test_keyword_searcher.py` | 单元测试；新增 `TestSearchExactSubstringLayer`、`TestStripAllWhitespace` 两个测试类 | 修改 |
| `docs/api/bot/engine/keyword_searcher.md` | API 文档；补充两层匹配说明 | 修改 |
| `CONTEXT.md` | 术语表「关键词搜索」条目 | 修改 |
| `docs/PRD.md` | 3.1 节流程与交互约束 | 修改 |

---

## Task 1: 新增 `_strip_all_whitespace` 模块级函数（TDD）

**Files:**
- Modify: `bot/engine/keyword_searcher.py`（在 `_remove_particles` 函数之后新增）
- Test: `tests/unit/engine/test_keyword_searcher.py`（在文件末尾新增 `TestStripAllWhitespace` 类）

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_keyword_searcher.py` 文件末尾追加：

```python
class TestStripAllWhitespace:
    """_strip_all_whitespace：去除所有空白字符，保留助词。"""

    def test_removes_internal_space(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace("加班 了") == "加班了"

    def test_removes_all_kinds_of_whitespace(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace(" 加\n班\t了 ") == "加班了"

    def test_preserves_particles(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace("的加班吗") == "的加班吗"

    def test_empty_string(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace("   ") == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestStripAllWhitespace -v`
Expected: FAIL — `ImportError: cannot import name '_strip_all_whitespace'`

- [ ] **Step 3: 实现函数**

在 `bot/engine/keyword_searcher.py` 的 `_remove_particles` 函数之后（`class KeywordSearcher` 之前）新增：

```python
def _strip_all_whitespace(text: str) -> str:
    """去除字符串中所有空白字符，保留其余字符（含助词）。

    Args:
        text: 待处理的文本。

    Returns:
        去除所有空白字符后的字符串。
    """
    return "".join(text.split())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestStripAllWhitespace -v`
Expected: PASS — 4 passed

- [ ] **Step 5: 提交**

```bash
git add bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git commit -m "feat(engine): 新增 _strip_all_whitespace 模块级函数"
```

---

## Task 2: 新增 `_search_exact_substring` 与 `_search_fuzzy_lcs` 私有方法（TDD）

**Files:**
- Modify: `bot/engine/keyword_searcher.py`（在 `_compute_similarity` 方法之后、`search` 方法之前新增两个方法）
- Test: `tests/unit/engine/test_keyword_searcher.py`（新增 `TestSearchExactSubstringLayer` 类）

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_keyword_searcher.py` 的 `TestSearchExactSubstring` 类之后新增：

```python
class TestSearchExactSubstringLayer:
    """第一层：原始去空白关键词的精确子串短路。"""

    def test_raw_substring_preserves_particles(self):
        # 含助词的原始输入，raw 恰为 text 子串即命中
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="的加班心累")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("的加班")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_internal_whitespace_stripped_in_raw(self):
        # 内部空白被去除后再做子串判定
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="加班了")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班 了")
        assert len(results) == 1
        assert results[0].similarity == 100.0

    def test_raw_miss_falls_back_to_lcs(self):
        # raw 不是任何 text 子串 → 回退 LCS（cleaned 去助词后是 text 子串 → 100）
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("了加班吗")  # raw="了加班吗" 不命中；cleaned="加班" 是 text 子串 → 100
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_raw_hit_excludes_non_substring_entries(self):
        # 第一层命中即短路，非子串条目不进入结果
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨"),
            2: MemeEntry(id=2, image_path="b.jpg", text="完全无关的文本"),
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班")  # raw 命中 entry 1；entry 2 不含"加班"子串
        assert {r.entry_id for r in results} == {1}
        assert all(r.similarity == 100.0 for r in results)

    def test_raw_hit_respects_limit(self):
        entries = {
            i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        results = s.search("加班")
        assert len(results) == 5
        assert all(r.similarity == 100.0 for r in results)

    def test_raw_hit_strictness_vs_cleaned(self):
        # raw="的鱼" 命中 entry1 的 "的鱼"；去助词后 cleaned="鱼" 同时命中 entry1 和 entry2
        # 现有实现（去助词）会返回 2 条；新实现第一层只返回 raw 命中的 1 条
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="这是的鱼"),
            2: MemeEntry(id=2, image_path="b.jpg", text="鱼在游"),
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("的鱼")
        assert {r.entry_id for r in results} == {1}
        assert all(r.similarity == 100.0 for r in results)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestSearchExactSubstringLayer -v`
Expected: FAIL — `test_raw_hit_strictness_vs_cleaned` 失败。

  **失败原因**：现有 `search()` 用 `cleaned = _remove_particles("的鱼")` → jieba 把 `"的"` 标为 `uj` 助词去除 → `cleaned = "鱼"`；`_compute_similarity("鱼", "这是的鱼")` → `"鱼" in "这是的鱼"` → 100，`_compute_similarity("鱼", "鱼在游")` → `"鱼" in "鱼在游"` → 100，现有「100 分只返回 100 分」返回 `{1, 2}`。新实现第一层用 `raw = "的鱼"`：`"的鱼" in "这是的鱼"` → 命中 entry 1，`"的鱼" in "鱼在游"` → 不命中，只返回 `{1}`。断言 `{1}` 在现有实现下失败。

  其余 5 个用例在现有实现下也可能通过，但 `test_raw_hit_strictness_vs_cleaned` 必失败，足以为红测试。

- [ ] **Step 3: 实现两个私有方法**

在 `bot/engine/keyword_searcher.py` 的 `_compute_similarity` 方法之后、`search` 方法之前新增：

```python
    def _search_exact_substring(
        self,
        entries: dict[int, MemeEntry],
        raw: str,
    ) -> list[SearchResult]:
        """第一层：精确子串匹配。

        用「原始输入去所有空白、保留助词」的关键词对 OCR 文本做子串判定，
        命中条目 similarity=100.0。

        Args:
            entries: 全部索引条目，key=int(id)。
            raw: 去所有空白后的关键词（保留助词）。

        Returns:
            命中结果列表（按 entries 读出顺序，未截断）；无命中返回空列表。
        """
        return [
            SearchResult(
                entry_id=entry.id,
                image_path=entry.image_path,
                text=entry.text,
                similarity=100.0,
            )
            for entry in entries.values()
            if entry.text and raw in entry.text
        ]

    def _search_fuzzy_lcs(
        self,
        entries: dict[int, MemeEntry],
        cleaned: str,
    ) -> list[SearchResult]:
        """第二层：LCS 模糊回退。

        用「去助词+去空白」的关键词走现有 LCS 模糊匹配，阈值过滤 + 降序排序
        +「存在 100 分只保留 100 分」规则。

        Args:
            entries: 全部索引条目，key=int(id)。
            cleaned: 去助词并去空白后的关键词。

        Returns:
            按相似度降序排列的结果列表（未截断）；无匹配返回空列表。
        """
        results: list[SearchResult] = []
        for entry in entries.values():
            text = entry.text
            if not text:
                continue
            score = self._compute_similarity(cleaned, text)
            if score >= self._threshold:
                results.append(
                    SearchResult(
                        entry_id=entry.id,
                        image_path=entry.image_path,
                        text=text,
                        similarity=score,
                    )
                )

        results.sort(key=lambda r: r.similarity, reverse=True)
        perfect_results = [r for r in results if r.similarity == 100.0]
        if perfect_results:
            results = perfect_results
        return results
```

注意：此时 `search()` 仍未调用这两个方法，测试继续 FAIL。

- [ ] **Step 4: 运行测试确认仍失败**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestSearchExactSubstringLayer -v`
Expected: FAIL — `test_raw_hit_strictness_vs_cleaned` 仍失败（`search()` 还未改写，仍用 `cleaned` 去助词路径）。其余 5 个用例在现有实现下已通过，属正常（红测试只需至少一个 FAIL）。

- [ ] **Step 5: 提交（方法已存在但未接线）**

```bash
git add bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git commit -m "test(engine): 新增精确子串层测试 + 私有方法骨架"
```

---

## Task 3: 重写 `search()` 接线两层短路

**Files:**
- Modify: `bot/engine/keyword_searcher.py:109-168`（替换整个 `search` 方法）

- [ ] **Step 1: 替换 `search()` 方法**

将 `bot/engine/keyword_searcher.py` 中现有的 `search` 方法（从 `def search(self, keyword: str) -> list[SearchResult]:` 到文件末尾的 `return results[: self._limit]`）整体替换为：

```python
    def search(self, keyword: str) -> list[SearchResult]:
        """根据关键词搜索表情包。

        两层匹配，短路返回：
        1. 精确子串层：用「原始输入去所有空白、保留助词」的关键词做子串匹配；
           命中则只返回包含该子串的条目（similarity=100.0）。
        2. LCS 模糊回退层：仅当第一层未命中时启用，用「去助词+去空白」的关键词
           走现有 LCS 模糊匹配（阈值 60，Top 10）。

        Args:
            keyword: 用户输入的搜索关键词。

        Returns:
            按相似度降序排列的搜索结果列表，最多返回 limit 条。无匹配时返回空列表。
        """
        keyword = keyword.strip()
        if not keyword:
            logger.debug("关键词为空，返回空结果")
            return []

        raw = _strip_all_whitespace(keyword)
        if not raw:
            logger.debug("关键词去空白后为空，返回空结果")
            return []

        entries = self._metadata_store.get_all_entries()
        if not entries:
            logger.debug("索引为空，返回空结果")
            return []

        exact_results = self._search_exact_substring(entries, raw)
        if exact_results:
            logger.info(
                "关键词精确子串命中：keyword=%r, 命中=%d, 返回=%d",
                keyword,
                len(exact_results),
                min(len(exact_results), self._limit),
            )
            return exact_results[: self._limit]

        cleaned = _strip_all_whitespace(_remove_particles(keyword))
        if not cleaned:
            logger.debug("关键词去助词后为空，返回空结果")
            return []

        results = self._search_fuzzy_lcs(entries, cleaned)
        logger.info(
            "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
            keyword,
            len(results),
            min(len(results), self._limit),
        )
        return results[: self._limit]
```

- [ ] **Step 2: 运行精确子串层测试确认通过**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py::TestSearchExactSubstringLayer -v`
Expected: PASS — 6 passed（含 `test_raw_hit_strictness_vs_cleaned`）

- [ ] **Step 3: 运行全量测试确认无回归**

Run: `uv run pytest tests/unit/engine/test_keyword_searcher.py -v`
Expected: PASS — 全部用例通过（原 24 + 新增 10 = 34 passed）
- [ ] **Step 4: 语法检查**

Run: `uv run python -m compileall bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py`
Expected: 无输出（编译通过）

- [ ] **Step 5: 提交**

```bash
git add bot/engine/keyword_searcher.py
git commit -m "feat(engine): KeywordSearcher.search 两层短路（精确子串优先 + LCS 回退）"
```

---

## Task 4: 同步 API 文档

**Files:**
- Modify: `docs/api/bot/engine/keyword_searcher.md`

- [ ] **Step 1: 更新文档**

将 `docs/api/bot/engine/keyword_searcher.md` 中 `KeywordSearcher` 的 `search` 说明替换为两层匹配语义。在 `KeywordSearcher` 类签名块之后追加算法说明（保留现有 `SearchResult`、`MetadataStoreProvider`、`__init__` 等内容不变）：

在文件中 `class KeywordSearcher:` 块的 `def search(self, keyword: str) -> list[SearchResult]` 行下方补充注释：

```python
class KeywordSearcher:
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        threshold: float = 60.0,
        limit: int = 10,
    ) -> None

    def search(self, keyword: str) -> list[SearchResult]
    # 两层短路：
    # 1. 精确子串层（raw = 原始输入去所有空白、保留助词）：raw in text 命中则只返回这些条目（similarity=100.0）
    # 2. LCS 模糊回退层（cleaned = 去助词+去空白）：仅第一层未命中时启用，阈值 60，Top 10，保留「100 分只返回 100 分」
```

并在文档末尾追加模块级函数说明：

```python
def _strip_all_whitespace(text: str) -> str  # 去所有空白，保留助词
```

- [ ] **Step 2: 提交**

```bash
git add docs/api/bot/engine/keyword_searcher.md
git commit -m "docs(api): keyword_searcher 补充两层匹配说明"
```

---

## Task 5: 同步 CONTEXT.md 术语表

**Files:**
- Modify: `CONTEXT.md`（「关键词搜索」条目）

- [ ] **Step 1: 替换术语定义**

将 `CONTEXT.md` 术语表中「关键词搜索」一行的定义单元格替换为：

> 用户输入关键词，先用「原始输入去所有空白、保留助词」的关键词对索引中的 OCR 文本做精确子串匹配，命中则只返回包含该子串的 Top 10 表情包；未命中时回退到 jieba.posseg 分词过滤助词后的关键词，用 pylcs LCS 模糊匹配（阈值统一 >= 60），按分数降序返回 Top 10；模糊回退阶段如果存在分数为 100 的结果，只返回分数为 100 的结果；不匹配文件名

- [ ] **Step 2: 提交**

```bash
git add CONTEXT.md
git commit -m "docs(context): 关键词搜索术语更新为两层匹配"
```

---

## Task 6: 同步 PRD 3.1 节

**Files:**
- Modify: `docs/PRD.md:77-127`（3.1 功能一：关键词搜索）

- [ ] **Step 1: 在流程图中补充精确子串层**

在 `docs/PRD.md` 3.1 节的流程框图中，将现有的 `Bot 接收 → 调用 KeywordSearcher.search("加班")` 之下、`使用 jieba.posseg ...` 之前，插入一层精确子串判断。把：

```
Bot 接收 → 调用 KeywordSearcher.search("加班")
        │
        ├── 使用 jieba.posseg 对关键词做分词 + 词性标注，过滤助词（的、了、吗、呢、吧等）
```

改为：

```
Bot 接收 → 调用 KeywordSearcher.search("加班")
        │
        ├── 使用「原始输入去所有空白、保留助词」的关键词做精确子串匹配
        │    ├── 命中 → 只返回包含该子串的条情包（Top 10）
        │    └── 未命中 → 回退到 jieba 去助词 + pylcs LCS 模糊匹配（>= 60）
        │
        ├── （回退路径）使用 jieba.posseg 对关键词做分词 + 词性标注，过滤助词（的、了、吗、呢、吧等）
```

- [ ] **Step 2: 在交互约束中补充一句**

在 3.1 节「交互约束」列表首项之前追加：

```
- 关键词先做精确子串匹配（用去除所有空白、保留助词的原始输入）；命中则只返回包含该子串的结果，否则回退到 jieba 去助词后的 LCS 模糊匹配。
```

- [ ] **Step 3: 提交**

```bash
git add docs/PRD.md
git commit -m "docs(prd): 3.1 关键词搜索补充精确子串优先层"
```

---

## Task 7: 全量回归与最终验证

**Files:**
- 无文件改动，仅运行验证

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: 全部通过，无失败

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot tests`
Expected: 无输出

- [ ] **Step 3: 确认 git 状态干净**

Run: `git status`
Expected: working tree clean（所有改动已分任务提交）

- [ ] **Step 4: 查看提交历史**

Run: `git log --oneline -8`
Expected: 看到本计划各任务的提交记录，按 Task 1→6 顺序排列
