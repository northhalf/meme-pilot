# 关键词搜索：精确子串优先 + LCS 模糊回退设计

> 日期：2026-07-01
> 状态：待批准

---

## 1. 问题背景

当前 `KeywordSearcher.search()` 对用户输入先做 `jieba.posseg` 去助词 + 去所有空白得到 `cleaned`，再用 `cleaned in text` 判定子串，最后对全集算 LCS 并在末尾用「存在 100 分只保留 100 分」规则过滤。该流程已经能"有子串命中则只返回子串命中"，但子串判定基于**去助词后的关键词**，并非用户原始输入。

用户需求：**优先用用户原始输入做完全匹配子串查找，命中则只返回包含该子串的字符串**。即把"精确子串"层从去助词版本改为原始输入版本，使其更直观地体现"输入什么就精确找什么"。

## 2. 设计目标

1. 第一层「精确子串」：用**原始输入去所有空白、保留助词**的关键词 `raw` 对 OCR 文本（已去空白）做 `raw in text` 判定；命中则只返回命中条目（`similarity=100.0`），不再走模糊匹配。
2. 第二层「LCS 模糊回退」：仅当第一层未命中时启用，沿用现有 `jieba` 去助词 + 去空白得到 `cleaned`，走 pylcs LCS 模糊匹配（阈值 60，Top 10），保留「存在 100 分只保留 100 分」规则。
3. 最小改动：不动 `_compute_similarity`、`_remove_particles`、`_PARTICLE_POS_TAGS`、`SearchResult`、`__init__` 签名、默认阈值/上限；不影响 `/ai`、`/add`、`/refresh`、sync、去重键逻辑。

## 3. 方案

采用**前置子串短路 + 现有 LCS 回退**（方案 1）。`search()` 拆分为流程编排 + 两个职责单一的私有方法。

### 3.1 算法分层

```
search(keyword):
  1. keyword = keyword.strip(); 空则返回 []
  2. raw = _strip_all_whitespace(keyword)          # 去所有空白，保留助词
     raw 为空 → 返回 []
  3. entries = get_all_entries(); 空则返回 []
  4. 第一层 _search_exact_substring(entries, raw):
     exact = [e for e in entries.values() if e.text and raw in e.text]
     非空 → 返回 exact[:limit]（每条 similarity=100.0）
  5. 第二层 _search_fuzzy_lcs(entries, cleaned):
     cleaned = _strip_all_whitespace(_remove_particles(keyword))
     cleaned 为空 → 返回 []
     遍历 entries，_compute_similarity(cleaned, text) >= threshold 过滤
     降序排序；存在 100 分只保留 100 分；返回 [:limit]
```

**职责分工**：
- 第一层：用「原始去空白」关键词做精确子串，体现"输入什么找什么"。
- 第二层：仅当第一层空时启用，用「去助词去空白」关键词做模糊兜底，保留现有宽松匹配能力。

### 3.2 新增模块级函数

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

### 3.3 新增实例方法

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

### 3.4 重写 `search()`（纯编排）

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
            keyword, len(exact_results), min(len(exact_results), self._limit),
        )
        return exact_results[: self._limit]

    cleaned = _strip_all_whitespace(_remove_particles(keyword))
    if not cleaned:
        logger.debug("关键词去助词后为空，返回空结果")
        return []

    results = self._search_fuzzy_lcs(entries, cleaned)
    logger.info(
        "关键词搜索完成：keyword=%r, 匹配=%d, 返回=%d",
        keyword, len(results), min(len(results), self._limit),
    )
    return results[: self._limit]
```

`_search_exact_substring` / `_search_fuzzy_lcs` 返回未截断列表，`search()` 统一在末尾 `[:limit]` 截断——保持单一截断点。

### 3.5 不变项

- `_compute_similarity(cleaned, text)`：内部 `if keyword in text: return 100.0` 否则 LCS，签名和行为不动。
- `_remove_particles(text)`、`_PARTICLE_POS_TAGS`：保留，仅用于第二层。
- `SearchResult` dataclass、`__init__(metadata_store, threshold=60.0, limit=10)` 签名、`MetadataStoreProvider` 协议：不变。

## 4. 数据流

```
用户输入 keyword
   │
   ▼
search(): strip → raw = _strip_all_whitespace(keyword)
   │  raw 为空? ──是──▶ 返回 []
   ▼
entries = get_all_entries()
   │  entries 为空? ──是──▶ 返回 []
   ▼
_search_exact_substring(entries, raw)
   │  raw in entry.text?
   ├──非空──▶ 截断 [:limit] 返回（每条 100.0）
   │
   ▼ 空
cleaned = _strip_all_whitespace(_remove_particles(keyword))
   │  cleaned 为空? ──是──▶ 返回 []
   ▼
_search_fuzzy_lcs(entries, cleaned)
   │  _compute_similarity(cleaned, text) >= 60?
   │  降序 + 「100 分只保留 100 分」
   ▼
截断 [:limit] 返回
```

## 5. 边界情况

| 场景 | 行为 |
|------|------|
| 空关键词 / 全空白 | `raw` 为空 → 返回 `[]` |
| 索引为空 | 返回 `[]` |
| `entry.text` 为空字符串 | 第一层跳过（`if entry.text`）；第二层跳过 |
| raw 子串命中集 > limit | 按 `entries` 读出顺序（sqlite `id` 升序）取前 `limit` |
| raw 子串命中集非空 | 只返回子串命中条目，不再走 LCS；带助词输入若 `raw` 恰为某 text 子串即命中（新行为） |
| raw 未命中、cleaned 为空（全助词输入如 `的吗`） | 返回 `[]`（与现状一致） |
| raw 未命中、cleaned 非空 | 回退 LCS，threshold=60，Top10，保留「100 分只返回 100 分」 |
| 关键词含内部空白（如 `加班 了`） | `raw`=`加班了`，按 `加班了 in text` 判定 |
| 关键词首尾空白 | `search()` 入口 `strip` 已处理 |
| raw 命中条目全部 `text` 非空 | `if entry.text and raw in entry.text` 双重保护，空 text 不参与 |

### 5.1 现有测试兼容性核对

| 测试 | 输入 | raw 子串命中? | 实际路径 | 结果 |
|------|------|--------------|---------|------|
| `test_short_keyword_in_long_text` | `小人之心` | 是 | 第一层 | 1 条 100 分 ✅ |
| `test_keyword_hits_multiple` | `加班` | 是(3条) | 第一层 | 3 条 100 分 ✅ |
| `test_full_text_match` | `加班到凌晨三点的我` | 是 | 第一层 | 1 条 100 分 ✅ |
| `test_partial_overlap` | `猫抓蝴蝶` | 否(不连续) | 回退 LCS，cleaned 同串 → 100 | 1 条 100 分 ✅ |
| `test_non_contiguous_match` | `加班凌晨通知` | 否 | 回退 LCS | 60–100 分 ✅ |
| `test_drops_particles` | `了加班吗` | 否 | 回退 LCS，cleaned=`加班` → 100 | 3 条 100 分 ✅ |
| `test_all_particles_returns_empty` | `的呢吗` | 否 | 回退 LCS，cleaned 为空 | `[]` ✅ |
| `test_perfect_score_filters_others` | `加班`+`加斑` | 是(2条) | 第一层 | 2 条 100 分 ✅ |
| `test_below_threshold_filtered` | `加班`vs`今天天气真好` | 否 | LCS=0<60 | `[]` ✅ |
| `test_limit_truncation` | `加班`×15 | 是 | 第一层，取前 5 | 5 条 ✅ |

**结论**：所有现有用例行为不变。唯一新行为出现在「关键词含助词且 `raw` 恰为某 text 子串」（如 text 含 `的加班`，输入 `的加班`）——这是需求要求的新增精确匹配能力。

## 6. 测试变更

修改 `tests/unit/engine/test_keyword_searcher.py`：现有用例全部保留，新增两个测试类。

### 6.1 新增 `TestSearchExactSubstringLayer`

验证第一层精确子串短路：

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
        # raw 不是任何 text 子串 → 回退 LCS（cleaned 去助词后命中）
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
```

### 6.2 新增 `TestStripAllWhitespace`

模块级函数单元测试：

```python
class TestStripAllWhitespace:
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

### 6.3 测试命令

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -v
uv run python -m compileall bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
```

## 7. 文档同步

### 7.1 `docs/api/bot/engine/keyword_searcher.md`

新增两层匹配说明与函数签名：

```python
def _strip_all_whitespace(text: str) -> str  # 去所有空白，保留助词

class KeywordSearcher:
    def search(self, keyword: str) -> list[SearchResult]
    # 两层短路：
    # 1. 精确子串层（raw = 原始输入去所有空白、保留助词）：raw in text 命中则只返回这些条目（similarity=100.0）
    # 2. LCS 模糊回退层（cleaned = 去助词+去空白）：仅第一层未命中时启用，阈值 60，Top 10，保留「100 分只返回 100 分」
```

### 7.2 `CONTEXT.md` 术语表「关键词搜索」条目

修订为：

> **关键词搜索**：功能一：用户输入关键词，先用「原始输入去所有空白、保留助词」的关键词对索引中的 OCR 文本做精确子串匹配，命中则只返回包含该子串的 Top 10 表情包；未命中时回退到 jieba.posseg 分词过滤助词后的关键词，用 pylcs LCS 模糊匹配（阈值统一 >= 60），按分数降序返回 Top 10；模糊回退阶段如果存在分数为 100 的结果，只返回分数为 100 的结果；不匹配文件名。

### 7.3 `docs/PRD.md` 3.1 节

在流程中补一层「精确子串优先」：

```
├ 使用「原始输入去所有空白、保留助词」的关键词做精确子串匹配
│   ├── 命中 → 只返回包含该子串的条情包（Top 10）
│   └── 未命中 → 回退到 jieba 去助词 + pylcs LCS 模糊匹配（>= 60）
```

在交互约束补充："关键词先做精确子串匹配（用去除所有空白、保留助词的原始输入）；命中则只返回包含该子串的结果，否则回退到 jieba 去助词后的 LCS 模糊匹配。"

### 7.4 不改动

- `README.md`：对外行为仍是"按 OCR 文本关键词搜索"，无需改动。
- `.env.example` / `docker-compose.yml`：无新增环境变量或容器变更。

## 8. 未涵盖

- 不修改 `_compute_similarity` 内部逻辑（`if keyword in text: return 100.0` 否则 LCS）。
- 不移除 `jieba` 依赖与 `_remove_particles`（第二层 LCS 回退仍需去助词）。
- 不修改去重键逻辑（`MetadataStore.get_id_by_text` 仍按去空白文本去重）。
- 不影响 `/ai`、`/add`、`/refresh`、`sync_with_filesystem` 逻辑。
