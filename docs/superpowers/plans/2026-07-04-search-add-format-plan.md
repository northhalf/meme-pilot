# Search / Add Format 调整实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调整 MemePilot 的 `/search` 结果展示、`/ai` 发送展示与 `/add` 命令交互格式，使搜索结果与发送图片均附带 `id/speaker/tags` 元数据，`/add` 改为接收 `speaker` 和 `tags` 并自动生成文件名，成功回复包含索引 id。

**Architecture:** 在 `SearchResult`、`AIMatchCandidate`、`AIMatchResult` 中扩展 `speaker`/`tags` 字段并在构造时从 `MemeEntry` 带出；在 `IndexManager.add`/`_write_entry` 中透传 `speaker`/`tags` 到 `MetadataStore.add/update`；在 `_search_utils.py` 中新增 `format_metadata_line` 共享格式化函数，供 `execute_search`、`handle_got_selection`、`meme_ai` 使用；`meme_add.py` 改为解析第一个词为 `speaker`、剩余词为 `tags`、文件名完全自动生成的逻辑；同步更新文档与测试。

**Tech Stack:** Python 3.12, NoneBot2, pytest, pytest-asyncio, pylcs, chromadb, sqlite3.

---

## 文件变更清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `bot/engine/keyword_searcher.py` | 修改 | `SearchResult` 新增 `speaker`/`tags`；构造处带出入库字段 |
| `bot/engine/ai_matcher.py` | 修改 | `AIMatchCandidate`/`AIMatchResult` 新增 `speaker`/`tags`；`_build_candidates`/`_candidate_to_result` 透传 |
| `bot/engine/index_manager.py` | 修改 | `AddResult` 新增 `speaker`/`tags`；`_WriteRequest` 新增 `tags`；`IndexManager.add` 签名扩展；`_write_worker_loop`/`_write_entry` 透传并覆盖旧 speaker/tags |
| `bot/plugins/_search_utils.py` | 修改 | 新增 `format_metadata_line`；多结果/单结果/选择后发送分两条消息 |
| `bot/plugins/meme_ai.py` | 修改 | 命中后先发图片，再发元数据行 |
| `bot/plugins/meme_add.py` | 修改 | 解析 `speaker`/`tags`；移除目标命名；文件名自动生成；成功回复带 id |
| `bot/plugins/_help_text.py` | 修改 | `/add [speaker <tags...>]` |
| `README.md` | 修改 | `/add` 说明、示例、文件名规则同步 |
| `docs/PRD.md` | 修改 | `/add` 触发方式、流程、交互约束、帮助示例同步 |
| `docs/api/API.md` | 修改 | 更新 `SearchResult`、`AIMatchCandidate`、`AIMatchResult`、`AddResult`、`IndexManager.add` 签名 |
| `tests/unit/engine/test_keyword_searcher.py` | 修改 | 新增/更新 speaker/tags 带出断言 |
| `tests/unit/engine/test_ai_matcher.py` | 修改 | 新增/更新 speaker/tags 带出断言 |
| `tests/unit/engine/test_index_manager.py` | 修改 | 更新 `AddResult` 构造、`add` 调用断言、去重覆盖 speaker/tags 断言 |
| `tests/unit/plugins/test_search_utils.py` | 修改 | 更新单结果/多结果/选择后发送断言 |
| `tests/unit/plugins/test_meme_ai.py` | 修改 | 更新命中后发送断言 |
| `tests/unit/plugins/test_meme_add.py` | 修改 | 移除 `_sanitize_filename`/`_build_filename` 测试；新增 speaker/tags 解析、文件名自动生成、id 回复断言 |
| `tests/unit/plugins/test_meme_help.py` | 可选修改 | 帮助文本包含 `/add [speaker <tags...>]` 即可，现有断言仍通过 |

---

## Task 1: SearchResult 与 KeywordSearcher 元数据透出

**Files:**
- Modify: `bot/engine/keyword_searcher.py`
- Test: `tests/unit/engine/test_keyword_searcher.py`

- [ ] **Step 1: 写失败测试**（验证 `SearchResult` 可携带 speaker/tags，且搜索返回带元数据）

在 `tests/unit/engine/test_keyword_searcher.py` 中，找到 `MockMetadataStore` 返回的 `MemeEntry`，给部分条目加上 `speaker` 和 `tags`。新增测试：

```python
def test_search_result_carries_speaker_and_tags() -> None:
    """KeywordSearcher 应把 MemeEntry 的 speaker/tags 带到 SearchResult。"""
    entries = {
        1: MemeEntry(id=1, image_path="a.jpg", text="加班", speaker="小明", tags=["吐槽", "加班"]),
    }
    searcher = KeywordSearcher(MockMetadataStore(entries))
    results = searcher.search("加班")
    assert len(results) == 1
    assert results[0].speaker == "小明"
    assert results[0].tags == ["吐槽", "加班"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py::test_search_result_carries_speaker_and_tags -v
```

Expected: `AttributeError: 'SearchResult' object has no attribute 'speaker'`

- [ ] **Step 3: 实现最小改动**

修改 `bot/engine/keyword_searcher.py`：

```python
from dataclasses import dataclass, field  # 新增 field

@dataclass
class SearchResult:
    """单条关键词搜索结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本（无空格）。
        similarity: 相似度分数，0-100。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

在 `_search_exact_substring` 和 `_search_fuzzy_lcs` 两个 `SearchResult(...)` 构造处，加上：

```python
speaker=entry.speaker,
tags=entry.tags,
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git commit -m "feat(engine): SearchResult 携带 speaker/tags"
```

---

## Task 2: AIMatchCandidate / AIMatchResult 元数据透出

**Files:**
- Modify: `bot/engine/ai_matcher.py`
- Test: `tests/unit/engine/test_ai_matcher.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_ai_matcher.py` 中，给 `TestRerank._matcher` 的 `MemeEntry` 加上 `speaker`/`tags`，并新增：

```python
@pytest.mark.anyio
async def test_candidate_carries_speaker_and_tags() -> None:
    """召回候选应携带 speaker/tags。"""
    entries = {
        1: MemeEntry(id=1, image_path="first.jpg", text="第一张", speaker="小明", tags=["搞笑"]),
    }
    matcher = AIMatcher(
        MockMetadataStore(entries),
        MockVectorStore(hits=[VectorHit(entry_id=1, similarity=0.9)], count=1),
        MockEmbeddingProvider(),
    )
    result = await matcher.match_with_vector("选第一张", _make_query_vector())
    assert result is not None
    assert result.speaker == "小明"
    assert result.tags == ["搞笑"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py::test_candidate_carries_speaker_and_tags -v
```

Expected: `AttributeError: 'AIMatchResult' object has no attribute 'speaker'`

- [ ] **Step 3: 实现最小改动**

修改 `bot/engine/ai_matcher.py`：

```python
from dataclasses import dataclass, field, replace  # 新增 field

@dataclass(frozen=True)
class AIMatchCandidate:
    """Embedding 阶段的候选表情包。

    Attributes:
        rank: 临时候选序号，1-based。
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本。
        similarity: 余弦相似度。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
    """

    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class AIMatchResult:
    """AI 匹配最终结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本。
        similarity: embedding 余弦相似度。
        source: 结果来源，取值为 "embedding" 或 "rerank"。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

在 `_build_candidates` 的 `AIMatchCandidate(...)` 构造处加：

```python
speaker=entry.speaker,
tags=entry.tags,
```

在 `_candidate_to_result` 的 `AIMatchResult(...)` 构造处加：

```python
speaker=candidate.speaker,
tags=candidate.tags,
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/ai_matcher.py tests/unit/engine/test_ai_matcher.py
git commit -m "feat(engine): AIMatchCandidate/Result 携带 speaker/tags"
```

---

## Task 3: IndexManager 写入管道透传 speaker/tags

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_index_manager.py` 中新增（可放在已有 `TestAdd` 类内）：

```python
@pytest.mark.anyio
async def test_add_passes_speaker_and_tags(manager: IndexManager) -> None:
    """/add 应把 speaker/tags 写入 sqlite。"""
    await manager.add("text.jpg", speaker="小明", tags=["吐槽"])
    entry = manager._metadata_store.get_by_filename("text.jpg")
    assert entry is not None
    assert entry.speaker == "小明"
    assert entry.tags == ["吐槽"]

@pytest.mark.anyio
async def test_add_duplicate_replaces_speaker_and_tags(manager: IndexManager) -> None:
    """去重替换时应覆盖旧 speaker/tags。"""
    await manager.add("old.jpg", speaker="旧说话人", tags=["旧标签"])
    # 用同名不同文件触发替换：这里先写入 old.jpg，然后写 new.jpg 但 OCR 文本相同
    # 测试需要准备两张文件且 OCR 文本相同；按已有 fixture 模式构造
    await manager.add("new.jpg", speaker="新说话人", tags=["新标签"])
    entry = manager._metadata_store.get_entry(1)
    assert entry is not None
    assert entry.speaker == "新说话人"
    assert entry.tags == ["新标签"]
```

如果现有 `IndexManager` 构造依赖 mock OCR，可直接 mock 返回相同文本。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_index_manager.py::TestAdd::test_add_passes_speaker_and_tags -v
```

Expected: `TypeError: IndexManager.add() got an unexpected keyword argument 'speaker'`

- [ ] **Step 3: 实现最小改动**

修改 `bot/engine/index_manager.py`：

1. 导入 `field`：

```python
from dataclasses import dataclass, field  # 新增 field
```

2. `AddResult` 新增字段：

```python
@dataclass
class AddResult:
    """add() 的返回结果。"""

    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

3. `_WriteRequest` 新增 `tags`：

```python
@dataclass
class _WriteRequest:
    """写入任务单元..."""

    op: WriteOp
    future: "asyncio.Future[AddResult | EditTextResult | SetSpeakerResult]"
    entry_id: int = 0
    filename: str = ""
    text: str = ""
    speaker: str | None = None
    tags: list[str] | None = None  # 新增
    embedding: list[float] | None = None
    old_text: str = ""
```

4. `IndexManager.add` 签名与调用：

```python
async def add(
    self,
    filename: str,
    speaker: str | None = None,
    tags: list[str] | None = None,
) -> AddResult:
    """提交 /add 任务并等待执行完成。"""
```

在 `_WriteRequest(...)` 构造处加：

```python
speaker=speaker,
tags=tags,
```

5. `_write_worker_loop` 的 ADD 分支：

```python
result = await self._write_entry(
    req.filename,
    req.text,
    req.embedding,
    req.speaker,
    req.tags,
)
```

6. `_write_entry` 签名与实现：

```python
async def _write_entry(
    self,
    filename: str,
    text: str,
    embedding: list[float],
    speaker: str | None = None,
    tags: list[str] | None = None,
) -> AddResult:
```

在 duplicate replace 分支中，把：

```python
await self._run_sync(
    self._metadata_store.update, old_id, image_path=filename
)
```

改为：

```python
await self._run_sync(
    self._metadata_store.update,
    old_id,
    image_path=filename,
    speaker=speaker,
    tags=tags,
)
```

在正常新增分支中，把：

```python
eid = await self._run_sync(self._metadata_store.add, filename, text)
```

改为：

```python
eid = await self._run_sync(self._metadata_store.add, filename, text, speaker, tags)
```

两个 `AddResult` 返回处加上 `speaker=speaker, tags=tags or []`：

```python
return AddResult(
    entry_id=old_id,
    reason="replaced",
    text=text,
    replaced_image_path=old_image_path,
    speaker=speaker,
    tags=tags or [],
)
```

```python
return AddResult(
    entry_id=eid,
    reason="added",
    text=text,
    speaker=speaker,
    tags=tags or [],
)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_index_manager.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): IndexManager.add 透传并覆盖 speaker/tags"
```

---

## Task 4: 共享格式化函数 `format_metadata_line`

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/plugins/test_search_utils.py` 末尾新增：

```python
class TestFormatMetadataLine:
    """format_metadata_line 测试。"""

    def test_with_speaker_and_tags(self) -> None:
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(3, "小明", ["吐槽", "加班"]) == "3, 小明, 吐槽, 加班"

    def test_missing_speaker(self) -> None:
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(7, None, ["吐槽"]) == "7, 无, 吐槽"

    def test_empty_tags_omitted(self) -> None:
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(7, "小明", []) == "7, 小明"

    def test_both_empty(self) -> None:
        from bot.plugins._search_utils import format_metadata_line
        assert format_metadata_line(12, None, []) == "12, 无"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py::TestFormatMetadataLine -v
```

Expected: `ImportError: cannot import name 'format_metadata_line'`

- [ ] **Step 3: 实现最小改动**

在 `bot/plugins/_search_utils.py` 中，文件顶部 `logger` 定义后添加：

```python
def format_metadata_line(entry_id: int, speaker: str | None, tags: list[str]) -> str:
    """格式化表情包的元数据行。

    输出格式：id, speaker, tag1, tag2, ...
    speaker 缺失时显示为"无"；tags 为空时省略 tags 段。

    Args:
        entry_id: 索引 id。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。

    Returns:
        格式化后的元数据行字符串。
    """
    parts = [str(entry_id), speaker if speaker else "无"]
    parts.extend(tags)
    return ", ".join(parts)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py::TestFormatMetadataLine -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/_search_utils.py tests/unit/plugins/test_search_utils.py
git commit -m "feat(plugins): 新增 format_metadata_line 共享格式化函数"
```

---

## Task 5: `_search_utils.py` 发送逻辑改为图片+元数据两行

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`, `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 写失败测试**

更新 `tests/unit/plugins/test_search_utils.py`：

1. 在 `_make_search_result` 中接受 `speaker`/`tags` 并传入：

```python
def _make_search_result(
    entry_id: int = 1,
    image_path: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
    speaker: str | None = None,
    tags: list[str] | None = None,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id,
        image_path=image_path,
        text=text,
        similarity=similarity,
        speaker=speaker,
        tags=tags or [],
    )
```

2. 新增/替换以下测试：

```python
@pytest.mark.asyncio
@patch("bot.plugins._search_utils.session_manager.deactivate_chat")
@patch("bot.plugins._search_utils.MessageSegment")
@patch("bot.plugins._search_utils.get_index_manager")
async def test_single_result_sends_image_then_metadata(
    self,
    mock_get_im: MagicMock,
    mock_segment: MagicMock,
    mock_deactivate: MagicMock,
) -> None:
    """唯一结果应先发送图片，再 finish 元数据行。"""
    from bot.plugins._search_utils import execute_search

    mock_get_im.return_value = _make_index_manager(
        results=[_make_search_result(entry_id=7, image_path="加班心累.jpg", speaker="小明")]
    )
    _cmd = MagicMock()
    _cmd.finish = AsyncMock()
    _cmd.send = AsyncMock()

    await execute_search(_make_bot(), _make_event(), _cmd, "加班")

    _cmd.send.assert_awaited_once()
    _cmd.finish.assert_awaited_once()
    finished_text = _cmd.finish.call_args[0][0]
    assert "7" in finished_text
    assert "小明" in finished_text
    mock_deactivate.assert_called_once_with("12345")

@pytest.mark.asyncio
@patch("bot.plugins._search_utils.session_manager.create_selection")
@patch("bot.plugins._search_utils.timeout_session")
@patch("bot.plugins._search_utils.get_index_manager")
async def test_multiple_results_lists_metadata(
    self,
    mock_get_im: MagicMock,
    mock_timeout: MagicMock,
    mock_create_selection: MagicMock,
) -> None:
    """多结果列表应包含 id/speaker/tags。"""
    from bot.plugins._search_utils import execute_search

    results = [
        _make_search_result(entry_id=1, text="甲", speaker="小明", tags=["吐槽"]),
        _make_search_result(entry_id=2, text="乙", tags=["搞笑"]),
    ]
    mock_get_im.return_value = _make_index_manager(results=results)
    _cmd = MagicMock()
    _cmd.state = {}
    _cmd.send = AsyncMock()

    await execute_search(_make_bot(), _make_event("111"), _cmd, "加班")

    sent_text = _cmd.send.call_args[0][0]
    assert "1, 小明, 吐槽" in sent_text
    assert "2, 无, 搞笑" in sent_text
```

3. 在 `tests/unit/plugins/test_meme_search.py` 的 `test_valid_choice_sends_image` 中，把 `matcher.finish` 断言改为 `send` + `finish` 两次调用。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py tests/unit/plugins/test_meme_search.py -v
```

Expected: 新断言失败（单结果只 finish 一次、多结果文本不含元数据）

- [ ] **Step 3: 实现最小改动**

修改 `bot/plugins/_search_utils.py`：

1. 多结果列表分支（`for i, r in enumerate(results, 1):` 处）：

```python
for i, r in enumerate(results, 1):
    meta = format_metadata_line(r.entry_id, r.speaker, r.tags)
    lines.append(f"{i}. {r.text} -- {meta}")
```

2. 单结果分支（`len(results) == 1`）：

```python
if len(results) == 1:
    session_manager.deactivate_chat(user_id)
    result = results[0]
    image_path = MEMES_DIR / result.image_path
    await cmd_matcher.send(
        MessageSegment.image("file://" + str(image_path.resolve()))
    )
    await cmd_matcher.finish(format_metadata_line(result.entry_id, result.speaker, result.tags))
    return
```

3. `handle_got_selection` 中有效选择后：

```python
session_manager.remove_selection(user_id)
image_path = MEMES_DIR / result.image_path
await matcher.send(
    MessageSegment.image("file://" + str(image_path.resolve()))
)
await matcher.finish(
    format_metadata_line(result.entry_id, result.speaker, result.tags)
)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py tests/unit/plugins/test_meme_search.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/_search_utils.py tests/unit/plugins/test_search_utils.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(plugins): 搜索结果分两条消息发送并附带元数据"
```

---

## Task 6: `meme_ai.py` 命中后分两条消息发送

**Files:**
- Modify: `bot/plugins/meme_ai.py`
- Test: `tests/unit/plugins/test_meme_ai.py`

- [ ] **Step 1: 写失败测试**

更新 `tests/unit/plugins/test_meme_ai.py` 中 `_make_index_manager` 的默认 `AIMatchResult`：

```python
AIMatchResult(
    entry_id=1,
    image_path="加班心累.jpg",
    text="加班到心累",
    similarity=0.95,
    source="rerank",
    speaker="小明",
    tags=["吐槽"],
)
```

替换 `test_match_sends_image` 为：

```python
@pytest.mark.asyncio
@patch.object(meme_ai, "MessageSegment")
@patch.object(meme_ai, "get_index_manager")
@patch.object(meme_ai, "is_authorized", return_value=True)
async def test_match_sends_image_then_metadata(
    self,
    mock_auth: MagicMock,
    mock_get_im: MagicMock,
    mock_segment: MagicMock,
) -> None:
    """匹配成功时应先发送图片，再 finish 元数据行。"""
    matcher = _make_matcher()
    mock_get_im.return_value = _make_index_manager()

    await handle_ai(_make_bot(), _make_event("12345", "/ai 加班心累"), matcher)

    matcher.send.assert_awaited_once()
    matcher.finish.assert_awaited_once()
    finished_text = matcher.finish.call_args[0][0]
    assert "1" in finished_text
    assert "小明" in finished_text
    assert "吐槽" in finished_text
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_meme_ai.py::TestHandleAiSuccess::test_match_sends_image_then_metadata -v
```

Expected: 断言失败（`matcher.send` 未调用）

- [ ] **Step 3: 实现最小改动**

修改 `bot/plugins/meme_ai.py`：

```python
from bot.plugins._search_utils import format_metadata_line
```

在命中结果发送处：

```python
image_path = MEMES_DIR / match_result.image_path
session_manager.deactivate_chat(user_id)
await matcher.send(
    MessageSegment.image("file://" + str(image_path.resolve()))
)
await matcher.finish(
    format_metadata_line(
        match_result.entry_id,
        match_result.speaker,
        match_result.tags,
    )
)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_meme_ai.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_ai.py tests/unit/plugins/test_meme_ai.py
git commit -m "feat(plugins): /ai 命中结果分两条消息发送元数据"
```

---

## Task 7: `meme_add.py` 解析 speaker/tags、自动文件名、回复带 id

**Files:**
- Modify: `bot/plugins/meme_add.py`
- Test: `tests/unit/plugins/test_meme_add.py`

- [ ] **Step 1: 写失败测试**

更新 `tests/unit/plugins/test_meme_add.py`：

1. 把 `from bot.plugins.meme_add import _build_filename, _get_extension, _sanitize_filename, got_image, handle_add` 改为 `from bot.plugins.meme_add import _auto_filename, _get_extension, got_image, handle_add`。
2. 删除 `TestSanitizeFilename` 和 `TestBuildFilename` 两个类。
3. 新增 `TestParseAddArgs`：

```python
class TestParseAddArgs:
    """/add 参数解析测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_no_args(self, mock_sm, mock_auth, mock_get_im) -> None:
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add"), matcher)
        assert matcher.state["speaker"] is None
        assert matcher.state["tags"] == []

    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_speaker_only(self, mock_sm, mock_auth, mock_get_im) -> None:
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add 小明"), matcher)
        assert matcher.state["speaker"] == "小明"
        assert matcher.state["tags"] == []

    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_speaker_and_tags(self, mock_sm, mock_auth, mock_get_im) -> None:
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add 小明 吐槽 加班"), matcher)
        assert matcher.state["speaker"] == "小明"
        assert matcher.state["tags"] == ["吐槽", "加班"]
```

4. 把已有 `test_success`/`test_success_with_target_name` 等测试中 patch `_build_filename` 改为 patch `_auto_filename` 返回固定值，例如：

```python
@patch.object(meme_add, "_auto_filename", return_value="a")
```

5. 在 `test_success` 中新增断言：

```python
im.add.assert_awaited_once_with("a.jpg", speaker=None, tags=[])
assert "id：1" in matcher.finish.call_args[0][0]
```

6. 把 `test_success_with_target_name` 改为 `test_success_with_speaker_and_tags`：

```python
@patch.object(meme_add, "_auto_filename", return_value="meme")
async def test_success_with_speaker_and_tags(...):
    ...
    matcher = _make_matcher(state={"speaker": "小明", "tags": ["吐槽"]})
    ...
    im.add.assert_awaited_once_with("meme.jpg", speaker="小明", tags=["吐槽"])
    assert "id：1" in matcher.finish.call_args[0][0]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_meme_add.py -v
```

Expected: 大量失败（无 `speaker`/`tags` state、`_build_filename` 不存在、回复无 id）

- [ ] **Step 3: 实现最小改动**

修改 `bot/plugins/meme_add.py`：

1. 在 `handle_add` 中替换目标命名逻辑：

```python
# 解析 speaker 和 tags
raw_text = event.get_plaintext().strip()
args_text = raw_text.removeprefix("/add").removeprefix("add").strip()
parts = args_text.split()
speaker = parts[0] if parts else None
tags = parts[1:] if len(parts) > 1 else []
matcher.state["speaker"] = speaker
matcher.state["tags"] = tags
```

2. 删除 `_sanitize_filename` 函数和 `_build_filename` 函数（若其他地方无引用）。
3. 在 `got_image` 中：

```python
speaker = matcher.state.get("speaker")
tags = matcher.state.get("tags", [])
```

替换文件名生成：

```python
filename = f"{_auto_filename(image_data)}{ext}"
```

调用 `index_manager.add` 时：

```python
result = await asyncio.wait_for(
    index_manager.add(filename, speaker=speaker, tags=tags),
    timeout=index_manager.add_user_timeout,
)
```

4. 成功回复中加入 id：

```python
elif result.reason == "replaced":
    ocr_display = _format_ocr_text(result.text)
    await matcher.finish(
        f"替换旧图✅，id：{result.entry_id}，识别到的文字为：\n「{ocr_display}」"
    )
else:
    ocr_display = _format_ocr_text(result.text)
    await matcher.finish(
        f"新增表情包✅，id：{result.entry_id}，识别到的文字为：\n「{ocr_display}」"
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_meme_add.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_add.py tests/unit/plugins/test_meme_add.py
git commit -m "feat(plugins): /add 解析 speaker/tags、自动文件名、回复带 id"
```

---

## Task 8: 更新帮助文本与文档

**Files:**
- Modify: `bot/plugins/_help_text.py`
- Modify: `README.md`
- Modify: `docs/PRD.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 修改 `bot/plugins/_help_text.py`**

```python
HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>]：通过聊天添加一张表情包
/edittext <id> <新文本>：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人]：设置或清空表情包的说话人
/refresh：扫描 memes/ 并增量更新索引
/cancel：取消当前正在执行的命令"""
```

- [ ] **Step 2: 修改 `README.md` 相关段落**

在 `### ➕ 聊天添加 /add` 小节中替换为：

```markdown
### ➕ 聊天添加 `/add`
```
授权用户: /add 小明 吐槽 加班
Bot: 请发送图片，60 秒内有效
授权用户: (发送一张图片)
Bot: 新增表情包✅，id：42，识别到的文字为：加班心累时的表情包
```

OCR 识别到的文字会展示给用户，超 50 字时自动截断并标注总长度。

`/add` 后的参数按空白切分，第一个词作为 `speaker`（说话人），剩余词作为 `tags`（标记词）；不填参数时 `speaker` 为空，`tags` 为空列表。文件名始终由 Bot 按 `meme_<YYYYMMDDHHMMSS>_<hash8>` 规则自动生成，不再使用用户输入作为文件名基名。
```

- [ ] **Step 3: 修改 `docs/PRD.md` 相关段落**

在 3.3 节触发方式处：

```markdown
#### 触发方式

授权用户在私聊中发送命令：`/add [speaker <tags...>]`

`speaker` 为可选说话人，不写入 OCR 文本；`tags` 为可选标记词列表。`/add` 后的参数按空白切分，第一个词作为 `speaker`，剩余词作为 `tags`。不填参数时 `speaker` 为空，`tags` 为空列表。文件名始终由 Bot 自动生成。
```

在 3.3 流程处，把示例中的 `/add 加班心累` 改为 `/add 小明 吐槽 加班`，Bot 回复改为：

```text
Bot 回复: "新增表情包✅，id：42，识别到的文字为：加班心累时的表情包"
```

删除“使用目标命名生成安全文件名”相关描述；保留自动命名规则说明。

在 3.4 节帮助命令示例中，使用 `/add [speaker <tags...>]`。

- [ ] **Step 4: 修改 `docs/api/API.md` 相关签名**

`docs/api/API.md` 中：

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

```python
@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class AIMatchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

```python
@dataclass
class AddResult:
    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

```python
async def add(self, filename: str, speaker: str | None = None, tags: list[str] | None = None) -> AddResult
```

- [ ] **Step 5: 运行帮助文本测试**

```bash
uv run pytest tests/unit/plugins/test_meme_help.py -v
```

Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add bot/plugins/_help_text.py README.md docs/PRD.md docs/api/API.md
git commit -m "docs: 同步 /add 与搜索结果元数据文档"
```

---

## Task 9: 全量验证

**Files:**
- 全部已修改文件

- [ ] **Step 1: 运行全量单元测试**

```bash
uv run pytest tests/unit -v
```

Expected: PASS（若集成测试需要真实 API Key，本次不运行）

- [ ] **Step 2: 语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 无语法错误

- [ ] **Step 3: 类型检查（如环境已配置 pyright）**

```bash
uv run pyright bot
```

Expected: 无新增类型错误（允许已存在的历史错误，但需确认与本次改动无关）

- [ ] **Step 4: 提交验证结果或标记**

若测试全部通过，可直接进入 Task 10；若失败，修复后先提交修复。

---

## Task 10: 最终提交与分支状态确认

**Files:**
- 全部变更

- [ ] **Step 1: 查看提交历史**

```bash
git log --oneline -10
```

Expected: 显示 Task 1-8 的多个提交，均位于 `feat/search-add-format` 分支。

- [ ] **Step 2: 确认未污染 main**

```bash
git branch --show-current
```

Expected: `feat/search-add-format`

- [ ] **Step 3: 汇总回复**

返回给父代理/用户：

```text
实现完成，所有单元测试通过。变更位于分支 feat/search-add-format，共 N 个提交。
主要改动：
1. SearchResult / AIMatchCandidate / AIMatchResult / AddResult 新增 speaker/tags。
2. IndexManager.add 透传 speaker/tags，去重替换时覆盖旧值。
3. _search_utils.py 新增 format_metadata_line，搜索/选择后分两条消息发送图片+元数据。
4. meme_ai.py 命中后分两条消息发送。
5. meme_add.py 解析 speaker/tags，文件名自动生成，成功回复带 id。
6. 帮助文本、README、PRD、API 文档同步更新。

请审阅后决定是否合并到 main。
```

---

## Self-Review Checklist

- [x] **Spec coverage:** 设计文档第 2 节搜索结果格式 → Task 4/5；第 3 节 `/add` 新语法 → Task 7；第 4 节数据模型 → Task 1/2/3；第 8 节文档 → Task 8；第 9 节测试 → 各 Task 测试步骤。
- [x] **Placeholder scan:** 无 TBD/TODO/"implement later"；每步均给出具体代码或命令。
- [x] **Type consistency:** `speaker: str | None = None`，`tags: list[str] | None = None` 或 `list[str] = field(default_factory=list)` 在不同层级用途一致；`_WriteRequest.tags` 为 `list[str] | None = None` 与 `IndexManager.add` 一致。
- [x] **Default values:** `SearchResult`/`AIMatchCandidate`/`AIMatchResult` 使用 `field(default_factory=list)`；`AddResult` 同样；不破坏已有构造调用。
- [x] **Two-message send:** `execute_search` 单结果、`handle_got_selection` 选择后、`meme_ai` 命中后均使用 `matcher.send(image)` + `matcher.finish(metadata_line)`。

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-04-search-add-format-plan.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — 每个 Task 派发一个子代理，逐任务审查、快速迭代。使用 `superpowers:subagent-driven-development`。
2. **Inline Execution** — 在本会话中直接使用 `superpowers:executing-plans` 批量执行任务。

请确认采用哪种方式。