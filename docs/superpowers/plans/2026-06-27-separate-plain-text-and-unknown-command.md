# 分离普通文本与未知斜杠命令处理 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `meme_help.py` 兜底处理分离为两条路径：普通文本当作 `/search` 执行，未知斜杠命令保持现有行为。

**Architecture:** 从 `meme_search.py` 提取 `execute_search` 和 `handle_selection` 到新文件 `_search_utils.py`，`meme_search.py` 和 `meme_help.py` 均从 `_search_utils` 导入调用。

**Tech Stack:** Python 3.12, NoneBot2, pytest, pytest-asyncio

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `bot/plugins/_search_utils.py` | 新增 | 搜索核心逻辑（`execute_search` + `handle_selection`） |
| `bot/plugins/meme_search.py` | 修改 | 薄包装：授权 → 会话覆盖 → 提取关键词 → 调用 `execute_search` |
| `bot/plugins/meme_help.py` | 修改 | 普通文本调用 `execute_search`，新增 `catch_all.got("selection")` |
| `tests/unit/plugins/test_search_utils.py` | 新增 | `_search_utils` 单元测试 |
| `tests/unit/plugins/test_meme_search.py` | 修改 | 适配新结构，移除已迁移的测试 |
| `tests/unit/plugins/test_meme_help.py` | 修改 | 适配普通文本走搜索的新行为 |
| `docs/PRD.md` | 修改 | 更新 3.4 节描述 |
| `CONTEXT.md` | 修改 | 更新 `/help` 术语描述 |

---

### Task 1: 创建 `_search_utils.py` — `handle_selection`

**Files:**
- Create: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 编写 `handle_selection` 测试**

```python
# tests/unit/plugins/test_search_utils.py
"""_search_utils 模块单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.engine.keyword_searcher import SearchResult


def _make_search_result(
    entry_id: str = "1",
    filename: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id, filename=filename, text=text, similarity=similarity
    )


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


# handle_selection 测试

class TestHandleSelection:
    """handle_selection 测试。"""

    def test_valid_choice_returns_result(self) -> None:
        """有效编号应返回对应 SearchResult。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [
            _make_search_result(entry_id="1", filename="a.jpg", text="甲"),
            _make_search_result(entry_id="2", filename="b.jpg", text="乙"),
        ]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "2")

        assert isinstance(result, SearchResult)
        assert result.entry_id == "2"
        assert result.filename == "b.jpg"

    def test_invalid_text_returns_error(self) -> None:
        """非数字输入应返回错误消息字符串。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "abc")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_low_returns_error(self) -> None:
        """编号小于 1 时应返回错误消息。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [_make_search_result(), _make_search_result()]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "0")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_out_of_range_high_returns_error(self) -> None:
        """编号超出范围时应返回错误消息。"""
        from bot.plugins._search_utils import handle_selection

        candidates = [_make_search_result()]
        matcher = _make_matcher()

        result = handle_selection(matcher, candidates, "5")

        assert isinstance(result, str)
        assert "无效编号" in result

    def test_empty_candidates_returns_error(self) -> None:
        """candidates 为空时应返回错误消息。"""
        from bot.plugins._search_utils import handle_selection

        matcher = _make_matcher()

        result = handle_selection(matcher, [], "1")

        assert isinstance(result, str)
        assert "搜索状态异常" in result
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `handle_selection`**

```python
# bot/plugins/_search_utils.py
"""搜索核心逻辑模块。

提供 execute_search 和 handle_selection 供 meme_search 和 meme_help 复用。
以下划线开头避免 NoneBot2 自动加载为插件。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher

from bot.app_state import get_index_manager, get_keyword_searcher
from bot.config import MEMES_DIR
from bot.session import cancel, register, timeout_session

if TYPE_CHECKING:
    from bot.engine.keyword_searcher import SearchResult

logger = logging.getLogger(__name__)


def handle_selection(
    matcher: Matcher,
    candidates: list[SearchResult],
    text: str,
) -> SearchResult | str:
    """处理用户选择编号。

    Args:
        matcher: NoneBot2 Matcher 实例。
        candidates: 搜索结果候选列表。
        text: 用户输入的编号文本。

    Returns:
        SearchResult: 选择成功时返回对应结果。
        str: 错误消息（无效编号、candidates 为空等）。
    """
    if not candidates:
        return "搜索状态异常，请重新搜索"

    try:
        choice = int(text)
    except ValueError:
        return f"无效编号，请回复 1-{len(candidates)} 之间的数字"

    if choice < 1 or choice > len(candidates):
        return f"无效编号，请回复 1-{len(candidates)} 之间的数字"

    return candidates[choice - 1]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/_search_utils.py tests/unit/plugins/test_search_utils.py
git commit -m "feat(plugins): 新增 _search_utils.handle_selection"
```

---

### Task 2: `_search_utils.py` — `execute_search`

**Files:**
- Modify: `bot/plugins/_search_utils.py`
- Test: `tests/unit/plugins/test_search_utils.py`

- [ ] **Step 1: 编写 `execute_search` 测试**

追加到 `tests/unit/plugins/test_search_utils.py`：

```python
from unittest.mock import patch


def _make_index_manager(*, is_locked: bool = False, entry_count: int = 10) -> MagicMock:
    im = MagicMock()
    im.is_locked = is_locked
    im.entry_count = entry_count
    return im


def _make_keyword_searcher(*, results: list | None = None) -> MagicMock:
    ks = MagicMock()
    if results is not None:
        ks.search.return_value = results
    else:
        ks.search.return_value = [_make_search_result()]
    return ks


def _make_event(user_id: str = "12345") -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    return event


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


class TestExecuteSearch:
    """execute_search 测试。"""

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_lock_contention_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引锁占用时应回复提示。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(is_locked=True)
        matcher = _make_matcher()
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "索引正在更新" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_empty_index_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """索引为空时应回复提示。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager(entry_count=0)
        matcher = _make_matcher()
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "表情包目录为空" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_no_results_replies(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """无匹配结果时应回复提示。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=[])
        matcher = _make_matcher()
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "xyz")

        _cmd.finish.assert_awaited_once()
        assert "没有匹配到" in _cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.MessageSegment")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_single_result_sends_image(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock, mock_segment: MagicMock
    ) -> None:
        """唯一结果应直接发送图片。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(
            results=[_make_search_result(filename="加班心累.jpg")]
        )
        matcher = _make_matcher()
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.timeout_session")
    @patch("bot.plugins._search_utils.register")
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_multiple_results_registers_session(
        self,
        mock_get_im: MagicMock,
        mock_get_ks: MagicMock,
        mock_register: MagicMock,
        mock_timeout: MagicMock,
    ) -> None:
        """多个结果时应注册会话并启动超时。"""
        from bot.plugins._search_utils import execute_search

        results = [
            _make_search_result(entry_id="1", text="甲"),
            _make_search_result(entry_id="2", text="乙"),
        ]
        mock_get_im.return_value = _make_index_manager()
        mock_get_ks.return_value = _make_keyword_searcher(results=results)
        matcher = _make_matcher()
        _cmd = MagicMock()
        _cmd.send = AsyncMock()

        await execute_search(_make_bot(), _make_event("111"), _cmd, "加班")

        mock_register.assert_called_once_with("111", matcher, "search")
        assert "candidates" in matcher.state
        assert len(matcher.state["candidates"]) == 2

    @pytest.mark.asyncio
    @patch("bot.plugins._search_utils.get_keyword_searcher")
    @patch("bot.plugins._search_utils.get_index_manager")
    async def test_search_exception_replies_error(
        self, mock_get_im: MagicMock, mock_get_ks: MagicMock
    ) -> None:
        """search() 抛异常时应回复服务不可用。"""
        from bot.plugins._search_utils import execute_search

        mock_get_im.return_value = _make_index_manager()
        ks = _make_keyword_searcher()
        ks.search.side_effect = RuntimeError("pylcs 错误")
        mock_get_ks.return_value = ks
        matcher = _make_matcher()
        _cmd = MagicMock()
        _cmd.finish = AsyncMock()

        await execute_search(_make_bot(), _make_event(), _cmd, "加班")

        _cmd.finish.assert_awaited_once()
        assert "搜索服务暂时不可用" in _cmd.finish.call_args[0][0]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: `test_execute_search*` 系列 FAIL（函数不存在）

- [ ] **Step 3: 实现 `execute_search`**

在 `bot/plugins/_search_utils.py` 中追加：

```python
async def execute_search(
    bot: Bot,
    event: PrivateMessageEvent,
    cmd_matcher: Matcher,
    keyword: str,
) -> None:
    """核心搜索逻辑。

    流程：锁检查 → 索引空检查 → 执行搜索 → 结果分支。
    多结果时注册 session 并启动超时任务。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        cmd_matcher: 调用方的 Matcher（用于 send/finish）。
        keyword: 搜索关键词。
    """
    user_id = event.get_user_id()

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    # 锁检查
    if index_manager.is_locked:
        logger.info("用户 %s 的搜索被拒绝：索引正在更新", user_id)
        await cmd_matcher.finish("索引正在更新，请稍后再试")
        return

    # 索引空检查
    if index_manager.entry_count == 0:
        await cmd_matcher.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 获取 KeywordSearcher
    try:
        keyword_searcher = get_keyword_searcher()
    except RuntimeError:
        logger.error("KeywordSearcher 尚未初始化")
        await cmd_matcher.finish("服务未就绪，请稍后再试")
        return

    # 执行搜索
    try:
        results = keyword_searcher.search(keyword)
    except Exception:
        logger.exception("关键词搜索异常: keyword=%r", keyword)
        await cmd_matcher.finish("搜索服务暂时不可用，稍后重试")
        return

    if not results:
        await cmd_matcher.finish("没有匹配到任何表情包 🙁")
        return

    if len(results) == 1:
        image_path = MEMES_DIR / results[0].filename
        await cmd_matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )
        return

    # 多个结果：格式化选择列表
    lines = ["找到多个匹配的表情包，请选择："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.text}")
    lines.append(f"回复编号即可 (1-{len(results)})")

    # 存储候选并注册会话
    cmd_matcher.state["candidates"] = results
    register(user_id, cmd_matcher, "search")

    await cmd_matcher.send("\n".join(lines))

    # 启动超时任务
    asyncio.create_task(
        timeout_session(bot, event, user_id, "选择已过期，请重新搜索")
    )
```

在文件顶部补充 `import asyncio`。

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_search_utils.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/_search_utils.py tests/unit/plugins/test_search_utils.py
git commit -m "feat(plugins): 新增 _search_utils.execute_search"
```

---

### Task 3: 重构 `meme_search.py` 为薄包装

**Files:**
- Modify: `bot/plugins/meme_search.py`
- Modify: `tests/unit/plugins/test_meme_search.py`

- [ ] **Step 1: 修改 `meme_search.py`**

将 `handle_search` 和 `got_selection` 改为薄包装：

```python
"""/search 命令插件 — 关键词搜索表情包。

授权用户在私聊中发送 /search <关键词>，Bot 通过 KeywordSearcher
对索引 OCR 文本做模糊匹配，返回搜索结果。
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.plugins._search_utils import execute_search, handle_selection
from bot.session import cancel, check_and_cancel, is_cancelled

search_cmd = on_command("search", rule=to_me(), priority=5, block=True)


@search_cmd.handle()
async def handle_search(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None:
    """/search 命令入口。

    流程：授权校验 → 会话覆盖 → 提取关键词 → 调用 execute_search。

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

    # 会话覆盖检查
    hint = check_and_cancel(user_id, "search")
    if hint:
        await matcher.send(hint)

    # 提取关键词
    raw_text = event.get_plaintext().strip()
    keyword = raw_text.removeprefix("/search").removeprefix("search").strip()
    if not keyword:
        await search_cmd.finish("/search <关键词>")
        return

    await execute_search(bot, event, search_cmd, keyword)


@search_cmd.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        selection_msg: 用户回复的选择编号消息。
    """
    user_id = event.get_user_id()

    try:
        if is_cancelled(user_id):
            return

        candidates = matcher.state.get("candidates", [])
        text = selection_msg.extract_plain_text().strip()

        result = handle_selection(matcher, candidates, text)
        if isinstance(result, str):
            await matcher.reject(result)
            return

        cancel(user_id)
        image_path = MEMES_DIR / result.filename
        await matcher.finish(
            MessageSegment.image("file://" + str(image_path.resolve()))
        )

    except Exception:
        from bot.session import cancel as session_cancel
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("用户 %s 的 /search 处理异常", user_id)
        session_cancel(user_id)
        raise
```

- [ ] **Step 2: 运行现有测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_meme_search.py -v
```

Expected: PASS（现有测试行为不变）

- [ ] **Step 3: 提交**

```bash
git add bot/plugins/meme_search.py
git commit -m "refactor(plugins): meme_search 使用 _search_utils 薄包装"
```

---

### Task 4: 修改 `meme_help.py` — 普通文本走搜索

**Files:**
- Modify: `bot/plugins/meme_help.py`
- Modify: `tests/unit/plugins/test_meme_help.py`

- [ ] **Step 1: 修改 `meme_help.py`**

```python
"""/help 命令插件 — 显示命令帮助摘要。

授权用户在私聊中发送 /help 时，Bot 返回当前可用命令和简单用法。
授权用户发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
授权用户发送普通文本时，等同执行 /search。
"""

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.plugins._search_utils import execute_search, handle_selection
from bot.session import cancel, check_and_cancel, is_cancelled

_HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [目标命名]：通过聊天添加一张表情包
/refresh：扫描 memes/ 并增量更新索引"""

help_cmd = on_command("help", rule=to_me(), priority=5, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: PrivateMessageEvent) -> None:
    """/help 命令处理入口。

    流程：授权校验 → 回复帮助文本。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
    """
    user_id = event.get_user_id()

    if not is_authorized(user_id):
        log_unauthorized(user_id, "help")
        return

    await help_cmd.finish(_HELP_TEXT)


# ---------------------------------------------------------------------------
# 兜底：纯文本 → /search；未知斜杠命令 → 回复帮助摘要
# priority=99 在所有具体命令（priority=5）之后运行；
# block=False 不阻止其他 matcher 处理消息。
# ---------------------------------------------------------------------------

catch_all = on_message(rule=to_me(), priority=99, block=False)


@catch_all.handle()
async def handle_plain_text(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
) -> None:
    """兜底处理授权用户的普通文本和未知斜杠命令。

    授权用户私聊发送不以 / 开头的普通文本时，等同执行 /search。
    授权用户私聊发送未知斜杠命令时，回复"未知命令"并附帮助摘要。
    非授权用户静默忽略。
    """
    user_id = event.get_user_id()

    if not is_authorized(user_id):
        log_unauthorized(user_id, "plain_text")
        return

    text = event.get_plaintext().strip()

    if text.startswith("/"):
        await catch_all.finish(f"未知命令\n\n{_HELP_TEXT}")
        return

    # 普通文本当作 /search
    hint = check_and_cancel(user_id, "search")
    if hint:
        await matcher.send(hint)
    await execute_search(bot, event, catch_all, text)


@catch_all.got("selection")
async def got_selection(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None:
    """接收用户选择编号并发送对应表情包。

    仅处理由本 matcher（catch_all）触发的搜索会话。
    """
    user_id = event.get_user_id()

    if is_cancelled(user_id):
        return

    candidates = matcher.state.get("candidates", [])
    if not candidates:
        # 非本 matcher 触发的搜索会话，静默忽略
        return

    text = selection_msg.extract_plain_text().strip()
    result = handle_selection(matcher, candidates, text)

    if isinstance(result, str):
        await catch_all.reject(result)
        return

    cancel(user_id)
    image_path = MEMES_DIR / result.filename
    await catch_all.finish(
        MessageSegment.image("file://" + str(image_path.resolve()))
    )
```

- [ ] **Step 2: 运行现有测试（预期部分失败）**

```bash
uv run pytest tests/unit/plugins/test_meme_help.py -v
```

Expected: `test_plain_text_replies_help` FAIL（行为已变）

- [ ] **Step 3: 更新 `test_meme_help.py`**

```python
"""/help 命令插件单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.keyword_searcher import SearchResult

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command / nonebot.on_message，
# 避免需要 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

_mock_message = MagicMock()
_mock_message.handle.return_value = lambda fn: fn
_mock_message.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.on_message", return_value=_mock_message),
):
    from bot.plugins import meme_help
    from bot.plugins.meme_help import handle_help, handle_plain_text


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "") -> MagicMock:
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


def _reset_mocks() -> None:
    """重置 mock matcher 的 finish 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()
    _mock_message.finish = AsyncMock()
    _mock_message.send = AsyncMock()


def _make_search_result(
    entry_id: str = "1",
    filename: str = "test.jpg",
    text: str = "测试文本",
    similarity: float = 90.0,
) -> SearchResult:
    return SearchResult(
        entry_id=entry_id, filename=filename, text=text, similarity=similarity
    )


# ---------------------------------------------------------------------------
# /help 命令测试
# ---------------------------------------------------------------------------


class TestHandleHelp:
    """/help 命令测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_authorized_user_receives_help(
        self, mock_auth: MagicMock
    ) -> None:
        """授权用户应收到帮助文本。"""
        _reset_mocks()

        await handle_help(_make_bot(), _make_event("111"))

        _mock_cmd.finish.assert_awaited_once()
        call_args = _mock_cmd.finish.call_args[0][0]
        assert "/help" in call_args
        assert "/search" in call_args
        assert "/ai" in call_args
        assert "/add" in call_args
        assert "/refresh" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_help(bot, _make_event("999"))

        _mock_cmd.finish.assert_not_called()
        bot.send.assert_not_called()


# ---------------------------------------------------------------------------
# 兜底处理测试 — 未知斜杠命令
# ---------------------------------------------------------------------------


class TestHandleUnknownSlashCommand:
    """未知斜杠命令测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_unknown_slash_command_replies_unknown(
        self, mock_auth: MagicMock
    ) -> None:
        """授权用户发送未知斜杠命令应回复"未知命令"。"""
        _reset_mocks()

        await handle_plain_text(_make_bot(), _make_event("111", "/foo"))

        _mock_message.finish.assert_awaited_once()
        call_args = _mock_message.finish.call_args[0][0]
        assert "未知命令" in call_args
        assert "/help" in call_args

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=False)
    async def test_unauthorized_slash_command_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户发送未知斜杠命令应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_plain_text(bot, _make_event("999", "/foo"))

        _mock_message.finish.assert_not_called()
        bot.send.assert_not_called()


# ---------------------------------------------------------------------------
# 兜底处理测试 — 普通文本走搜索
# ---------------------------------------------------------------------------


class TestHandlePlainTextAsSearch:
    """普通文本当作 /search 测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_help, "execute_search", new_callable=AsyncMock)
    @patch.object(meme_help, "check_and_cancel", return_value=None)
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_plain_text_calls_execute_search(
        self,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_exec: MagicMock,
    ) -> None:
        """普通文本应调用 execute_search。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_plain_text(_make_bot(), _make_event("111", "加班"), matcher)

        mock_exec.assert_awaited_once()
        call_args = mock_exec.call_args
        assert call_args[0][3] == "加班"  # keyword 参数

    @pytest.mark.asyncio
    @patch.object(meme_help, "execute_search", new_callable=AsyncMock)
    @patch.object(
        meme_help, "check_and_cancel", return_value="已取消上一条未完成的操作"
    )
    @patch.object(meme_help, "is_authorized", return_value=True)
    async def test_plain_text_with_session_cancel(
        self,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_exec: MagicMock,
    ) -> None:
        """有旧会话时应先取消并提示。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_plain_text(_make_bot(), _make_event("111", "加班"), matcher)

        matcher.send.assert_awaited_once()
        assert "已取消" in matcher.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_help, "is_authorized", return_value=False)
    async def test_unauthorized_plain_text_ignored(
        self, mock_auth: MagicMock
    ) -> None:
        """非授权用户发送纯文本应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()

        await handle_plain_text(bot, _make_event("999", "你好"))

        _mock_message.finish.assert_not_called()
        bot.send.assert_not_called()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_meme_help.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_help.py tests/unit/plugins/test_meme_help.py
git commit -m "feat(plugins): 普通文本当作 /search 执行"
```

---

### Task 5: 更新文档

**Files:**
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`

- [ ] **Step 1: 更新 PRD.md 3.4 节**

修改 `docs/PRD.md` 第 218-219 行：

```markdown
授权用户在私聊中发送不以 `/` 开头的普通文本时，Bot 等同执行 `/search`。
```

- [ ] **Step 2: 更新 CONTEXT.md `/help` 术语**

修改 `CONTEXT.md` 中 `/help` 的定义：

```markdown
| **/help** | 帮助命令；授权用户私聊发送 `/help` 时，Bot 返回当前命令和简单用法；授权用户发送未知斜杠命令时，Bot 回复"未知命令"并附帮助摘要；授权用户发送普通文本时，Bot 等同执行 `/search` |
```

- [ ] **Step 3: 运行全量测试**

```bash
uv run pytest tests/unit/ -v
```

Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add docs/PRD.md CONTEXT.md
git commit -m "docs: 普通文本行为从 /help 更新为 /search"
```

---

### Task 6: 全量验证

- [ ] **Step 1: 语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 无错误

- [ ] **Step 2: 全量单元测试**

```bash
uv run pytest tests/unit/ -v
```

Expected: PASS

- [ ] **Step 3: 最终提交（如有修复）**

```bash
git add -A
git commit -m "chore: 最终验证与修复"
```
