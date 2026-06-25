# /search 命令插件实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `bot/plugins/meme_search.py`，授权用户通过 `/search <关键词>` 搜索表情包，支持多结果选择交互。

**Architecture:** 采用与 `/add` 一致的 `got()` 两步模式：`handle_search()` 执行搜索并展示结果，`got_selection()` 等待用户选择编号。`KeywordSearcher` 作为 app_state 单例管理。

**Tech Stack:** Python 3.12, NoneBot2, rapidfuzz, pytest

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `bot/app_state.py` | 新增 KeywordSearcher 单例管理 |
| 修改 | `tests/unit/test_app_state.py` | 新增 KeywordSearcher getter 测试 |
| 创建 | `bot/plugins/meme_search.py` | /search 命令插件 |
| 创建 | `tests/unit/plugins/test_meme_search.py` | 插件单元测试 |

---

### Task 1: 扩展 app_state 支持 KeywordSearcher

**Files:**
- Modify: `bot/app_state.py`
- Modify: `tests/unit/test_app_state.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/unit/test_app_state.py` 末尾追加：

```python
class TestGetKeywordSearcher:
    """get_keyword_searcher() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 KeywordSearcher 实例。"""
        from bot.engine import KeywordSearcher

        ks = MagicMock(spec=KeywordSearcher)
        app_state.init_app(MagicMock(), MagicMock(), MagicMock(), keyword_searcher=ks)
        assert app_state.get_keyword_searcher() is ks

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="KeywordSearcher 尚未初始化"):
            app_state.get_keyword_searcher()
```

同时更新 `_reset_globals` fixture 和 `TestInitApp.test_sets_all_globals`，加入 `_keyword_searcher` 的重置和断言。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/test_app_state.py -v`
Expected: FAIL — `get_keyword_searcher` 不存在

- [ ] **Step 3: 编写最小实现**

在 `bot/app_state.py` 中：

1. 顶部 import 区域新增：
```python
from .engine import KeywordSearcher
```

2. 模块变量区新增：
```python
_keyword_searcher: KeywordSearcher | None = None
```

3. `init_app()` 签名新增参数，函数体新增赋值：
```python
def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None:
    ...
    global _index_manager, _ocr_service, _embedding_service, _image_optimizer, _ai_matcher, _keyword_searcher
    ...
    _keyword_searcher = keyword_searcher
```

4. 新增 getter：
```python
def get_keyword_searcher() -> KeywordSearcher:
    """获取 KeywordSearcher 单例。

    Returns:
        已初始化的 KeywordSearcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _keyword_searcher is None:
        raise RuntimeError("KeywordSearcher 尚未初始化，请先调用 init_app()")
    return _keyword_searcher
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/test_app_state.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 语法检查**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run python -m compileall bot/app_state.py`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add bot/app_state.py tests/unit/test_app_state.py
git commit -m "feat(app_state): 新增 KeywordSearcher 单例管理"
```

---

### Task 2: 创建 meme_search.py 插件 — handle_search 入口

**Files:**
- Create: `bot/plugins/meme_search.py`
- Create: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 编写失败测试 — 授权校验**

创建 `tests/unit/plugins/test_meme_search.py`：

```python
"""/search 命令插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# got() 返回透传 decorator。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import meme_search
    from bot.plugins.meme_search import got_selection, handle_search


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "/search 加班") -> MagicMock:
    """创建模拟的 PrivateMessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_index_manager(
    *, is_locked: bool = False, entry_count: int = 10
) -> MagicMock:
    """创建模拟的 IndexManager。"""
    im = MagicMock()
    im.is_locked = is_locked
    im.entry_count = entry_count
    return im


def _make_keyword_searcher(
    *, results: list | None = None
) -> MagicMock:
    """创建模拟的 KeywordSearcher。"""
    from bot.engine.keyword_searcher import SearchResult

    ks = MagicMock()
    if results is not None:
        ks.search.return_value = results
    else:
        ks.search.return_value = [
            SearchResult(entry_id="1", filename="加班.jpg", text="加班到心累", similarity=95.0)
        ]
    return ks


def _reset_cmd() -> None:
    """重置 mock_cmd 的 finish/send/reject 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()
    _mock_cmd.send = AsyncMock()
    _mock_cmd.reject = AsyncMock()


# ---------------------------------------------------------------------------
# 测试：授权校验
# ---------------------------------------------------------------------------


class TestHandleSearchAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """授权用户应正常执行搜索。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher()

        await handle_search(_make_bot(), _make_event())

        mock_get_ks.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_cmd()
        bot = _make_bot()

        await handle_search(bot, _make_event("999"))

        mock_get_im.assert_not_called()
        mock_get_ks.assert_not_called()
        _mock_cmd.finish.assert_not_awaited()
        bot.send.assert_not_awaited()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py::TestHandleSearchAuth -v`
Expected: FAIL — 模块 `meme_search` 不存在

- [ ] **Step 3: 编写最小实现 — 授权校验部分**

创建 `bot/plugins/meme_search.py`：

```python
"""/search 命令插件 — 关键词搜索表情包。

授权用户在私聊中发送 /search <关键词>，Bot 通过 KeywordSearcher
对索引 OCR 文本做模糊匹配，返回搜索结果。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.app_state import get_index_manager, get_keyword_searcher
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.session import cancel, check_and_cancel, is_cancelled, register

logger = logging.getLogger(__name__)

search_cmd = on_command("search", rule=to_me(), priority=5, block=True)


@search_cmd.handle()
async def handle_search(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None:
    """/search 命令入口。

    流程：授权校验 → 会话覆盖 → 锁检查 → 搜索 → 结果分支。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "search")
        return

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await search_cmd.finish("服务未就绪，请稍后再试")
        return

    # 提取关键词
    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/search").removeprefix("search").strip()
    if not keyword:
        await search_cmd.finish("/search <关键词>")
        return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py::TestHandleSearchAuth -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_search.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(plugins): 创建 /search 插件骨架及授权校验"
```

---

### Task 3: handle_search — 会话覆盖与锁检查

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 编写失败测试**

在 `test_meme_search.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 测试：索引锁
# ---------------------------------------------------------------------------


class TestHandleSearchLock:
    """索引锁测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_lock_contention_replies(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引锁占用时应回复提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager(is_locked=True)

        await handle_search(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "索引正在更新" in _mock_cmd.finish.call_args[0][0]
        mock_get_ks.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：会话覆盖
# ---------------------------------------------------------------------------


class TestHandleSearchSession:
    """会话覆盖测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "check_and_cancel", return_value="已取消上一条未完成的操作，开始新的 /search")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_session_overlap_sends_hint(
        self,
        mock_auth: MagicMock,
        mock_cancel: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
    ) -> None:
        """旧会话存在时应发送取消提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher()

        await handle_search(_make_bot(), _make_event())

        mock_cancel.assert_called_once_with("12345", "search")
        # 提示通过 matcher.send 发送（非 finish，因为后续还要继续搜索）
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py::TestHandleSearchLock tests/unit/plugins/test_meme_search.py::TestHandleSearchSession -v`
Expected: FAIL — `is_locked` 检查和 `check_and_cancel` 调用不存在

- [ ] **Step 3: 实现锁检查与会话覆盖**

在 `handle_search()` 中 `return` 语句后追加：

```python
    # 会话覆盖检查
    hint = check_and_cancel(user_id, "search")
    if hint:
        await matcher.send(hint)

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await search_cmd.finish("服务未就绪，请稍后再试")
        return

    # 检查索引锁（只读检查，不持有锁）
    if index_manager.is_locked:
        logger.info("用户 %s 的 /search 被拒绝：索引正在更新", user_id)
        await search_cmd.finish("索引正在更新，请稍后再试")
        return

    # 提取关键词
    ...
```

注意：需要调整代码顺序 — `get_index_manager()` 应在 `check_and_cancel` 之后、`is_locked` 检查之前。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_search.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(search): 会话覆盖与索引锁检查"
```

---

### Task 4: handle_search — 空关键词与空索引

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 编写失败测试**

在 `test_meme_search.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 测试：空关键词与空索引
# ---------------------------------------------------------------------------


class TestHandleSearchValidation:
    """输入验证测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_empty_keyword_replies_usage(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """/search 无参数时应回复用法提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()

        await handle_search(_make_bot(), _make_event(text="/search"))

        _mock_cmd.finish.assert_awaited_once()
        assert "/search" in _mock_cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """索引为空时应回复表情包目录为空。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager(entry_count=0)

        await handle_search(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "表情包目录为空" in _mock_cmd.finish.call_args[0][0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py::TestHandleSearchValidation -v`
Expected: FAIL — 空关键词和空索引检查不存在

- [ ] **Step 3: 实现验证逻辑**

在 `handle_search()` 中锁检查后追加：

```python
    # 空关键词
    if not keyword:
        await search_cmd.finish("/search <关键词>")
        return

    # 检查索引是否为空
    if index_manager.entry_count == 0:
        await search_cmd.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_search.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(search): 空关键词与空索引校验"
```

---

### Task 5: handle_search — 搜索与结果分支

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 编写失败测试**

在 `test_meme_search.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 测试：搜索结果分支
# ---------------------------------------------------------------------------


class TestHandleSearchResults:
    """搜索结果分支测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "MessageSegment")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_no_results_replies_no_match(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """无匹配结果时应回复无匹配。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=[])

        await handle_search(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "没有匹配到" in _mock_cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "MessageSegment")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_single_result_sends_image(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """唯一结果时应直接发送图片。"""
        from bot.engine.keyword_searcher import SearchResult

        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[SearchResult(entry_id="1", filename="加班.jpg", text="加班到心累", similarity=95.0)]
        )

        await handle_search(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_multiple_results_shows_list(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """多个结果时应显示选择列表并注册会话。"""
        from bot.engine.keyword_searcher import SearchResult

        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[
                SearchResult(entry_id="1", filename="a.jpg", text="加班到心累", similarity=95.0),
                SearchResult(entry_id="2", filename="b.jpg", text="加班到凌晨", similarity=85.0),
            ]
        )
        matcher = _make_matcher()

        await handle_search(_make_bot(), _make_event(), matcher)

        # 应发送选择列表
        matcher.send.assert_awaited_once()
        sent_text = matcher.send.call_args[0][0]
        assert "1." in sent_text
        assert "2." in sent_text
        assert "加班到心累" in sent_text
        assert "加班到凌晨" in sent_text

        # 应注册会话
        mock_register.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_search, "register")
    @patch.object(meme_search, "get_keyword_searcher")
    @patch.object(meme_search, "get_index_manager")
    @patch.object(meme_search, "is_authorized", return_value=True)
    async def test_multiple_results_stores_candidates(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """多个结果时应将候选存入 matcher.state。"""
        from bot.engine.keyword_searcher import SearchResult

        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        results = [
            SearchResult(entry_id="1", filename="a.jpg", text="加班到心累", similarity=95.0),
            SearchResult(entry_id="2", filename="b.jpg", text="加班到凌晨", similarity=85.0),
        ]
        mock_get_ks.return_value = _make_keyword_searcher(results=results)
        matcher = _make_matcher()

        await handle_search(_make_bot(), _make_event(), matcher)

        assert "candidates" in matcher.state
        assert len(matcher.state["candidates"]) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py::TestHandleSearchResults -v`
Expected: FAIL — 搜索逻辑不存在

- [ ] **Step 3: 实现搜索与结果分支**

在 `handle_search()` 中空索引检查后追加：

```python
    # 获取 KeywordSearcher
    try:
        keyword_searcher = get_keyword_searcher()
    except RuntimeError:
        logger.error("KeywordSearcher 尚未初始化")
        await search_cmd.finish("服务未就绪，请稍后再试")
        return

    # 执行搜索
    results = keyword_searcher.search(keyword)

    if not results:
        await search_cmd.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        # 唯一结果直接发送图片
        image_path = MEMES_DIR / results[0].filename
        await search_cmd.finish(MessageSegment.image(f"file:///{image_path.resolve()}"))
        return

    # 多个结果：格式化选择列表
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.text}")
    lines.append(f"回复编号即可 (1-{len(results)})")

    # 存储候选并注册会话
    matcher.state["candidates"] = results
    register(user_id, matcher, "search")

    await matcher.send("\n".join(lines))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_search.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(search): 搜索逻辑与结果分支"
```

---

### Task 6: got_selection — 用户选择处理

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 编写失败测试**

在 `test_meme_search.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 测试：got_selection 选择处理
# ---------------------------------------------------------------------------


class TestGotSelection:
    """got_selection 选择处理测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_search, "cancel")
    @patch.object(meme_search, "MessageSegment")
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_valid_selection_sends_image(
        self,
        mock_cancel: MagicMock,
        mock_segment: MagicMock,
        mock_is_cancelled: MagicMock,
    ) -> None:
        """有效选择应发送对应图片。"""
        from bot.engine.keyword_searcher import SearchResult

        _reset_cmd()
        candidates = [
            SearchResult(entry_id="1", filename="a.jpg", text="加班到心累", similarity=95.0),
            SearchResult(entry_id="2", filename="b.jpg", text="加班到凌晨", similarity=85.0),
        ]
        matcher = _make_matcher(state={"candidates": candidates})
        event = _make_event(text="2")

        await got_selection(_make_bot(), event, matcher)

        mock_segment.image.assert_called_once()
        mock_cancel.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_invalid_number_rejects(
        self, mock_is_cancelled: MagicMock
    ) -> None:
        """无效编号应 reject 并提示。"""
        from bot.engine.keyword_searcher import SearchResult

        _reset_cmd()
        candidates = [
            SearchResult(entry_id="1", filename="a.jpg", text="加班到心累", similarity=95.0),
            SearchResult(entry_id="2", filename="b.jpg", text="加班到凌晨", similarity=85.0),
        ]
        matcher = _make_matcher(state={"candidates": candidates})
        event = _make_event(text="abc")

        await got_selection(_make_bot(), event, matcher)

        matcher.reject.assert_awaited_once()
        assert "无效编号" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "is_cancelled", return_value=False)
    async def test_out_of_range_rejects(
        self, mock_is_cancelled: MagicMock
    ) -> None:
        """越界编号应 reject 并提示。"""
        from bot.engine.keyword_searcher import SearchResult

        _reset_cmd()
        candidates = [
            SearchResult(entry_id="1", filename="a.jpg", text="加班到心累", similarity=95.0),
        ]
        matcher = _make_matcher(state={"candidates": candidates})
        event = _make_event(text="5")

        await got_selection(_make_bot(), event, matcher)

        matcher.reject.assert_awaited_once()
        assert "1-1" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_search, "is_cancelled", return_value=True)
    async def test_cancelled_session_returns(
        self, mock_is_cancelled: MagicMock
    ) -> None:
        """已取消的会话应直接返回。"""
        _reset_cmd()
        matcher = _make_matcher()
        event = _make_event(text="1")

        await got_selection(_make_bot(), event, matcher)

        matcher.finish.assert_not_awaited()
        matcher.reject.assert_not_awaited()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py::TestGotSelection -v`
Expected: FAIL — `got_selection` 不存在

- [ ] **Step 3: 实现 got_selection**

在 `bot/plugins/meme_search.py` 末尾追加：

```python
@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
) -> None:
    """接收用户选择编号并发送对应表情包。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()

    # 会话有效性检查
    if is_cancelled(user_id):
        return

    candidates = matcher.state.get("candidates", [])
    text = event.get_plaintext().strip()

    # 解析编号
    try:
        choice = int(text)
    except ValueError:
        await matcher.reject(f"无效编号，请回复 1-{len(candidates)} 之间的数字")
        return

    if choice < 1 or choice > len(candidates):
        await matcher.reject(f"无效编号，请回复 1-{len(candidates)} 之间的数字")
        return

    # 发送图片
    selected = candidates[choice - 1]
    cancel(user_id)
    image_path = MEMES_DIR / selected.filename
    await matcher.finish(MessageSegment.image(f"file:///{image_path.resolve()}"))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/plugins/test_meme_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 语法检查**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run python -m compileall bot/plugins/meme_search.py`
Expected: 无错误

- [ ] **Step 6: 全量测试**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add bot/plugins/meme_search.py tests/unit/plugins/test_meme_search.py
git commit -m "feat(search): got_selection 用户选择处理"
```

---

### Task 7: 更新 API 文档

**Files:**
- Modify: `docs/api/API.md`

- [ ] **Step 1: 追加 meme_search 文档**

在 `docs/api/API.md` 的 `docs/api/bot/plugins/` 目录树中新增 `meme_search.md` 条目，并追加文档节：

```markdown
### `bot/plugins/meme_search.py`

NoneBot2 命令插件，注册 `/search` 命令。

- 依赖：`app_state.get_keyword_searcher()`、`app_state.get_index_manager()`、`auth.is_authorized()`、`bot.session`
- 锁：只读检查 `IndexManager.is_locked`
- 搜索：`KeywordSearcher.search(keyword) -> list[SearchResult]`
- 多结果：`got("selection")` 等待用户选择，候选存入 `matcher.state["candidates"]`
- 图片：`MessageSegment.image(f"file:///{path.resolve()}")`
```

- [ ] **Step 2: 提交**

```bash
git add docs/api/API.md
git commit -m "docs(api): 新增 /search 插件文档"
```
