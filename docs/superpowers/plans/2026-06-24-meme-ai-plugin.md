# /ai 命令插件实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `/ai <自然语言描述>` 命令插件，通过 Embedding 语义搜索 + LLM 精排两阶段匹配表情包。

**Architecture:** 扩展 `app_state` 新增 `get_ai_matcher()`；插件层使用 `on_command("ai")` 注册处理函数，检查授权和索引锁后调用 `AIMatcher.match()`，根据结果发送图片或回复提示。

**Tech Stack:** NoneBot2, AIMatcher (engine), EmbeddingService, RerankService

**Design Spec:** `docs/superpowers/specs/2026-06-24-meme-ai-plugin-design.md`

---

### Task 1: 扩展 app_state — 新增 get_ai_matcher()

**Files:**
- Modify: `bot/app_state.py`
- Modify: `tests/unit/test_app_state.py`

- [ ] **Step 1: 为 get_ai_matcher() 编写失败测试**

在 `tests/unit/test_app_state.py` 末尾新增：

```python
class TestGetAiMatcher:
    """get_ai_matcher() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 AIMatcher 实例。"""
        from bot.engine import AIMatcher

        im = MagicMock()
        ocr = MagicMock()
        emb = MagicMock()
        ai = MagicMock(spec=AIMatcher)
        app_state.init_app(im, ocr, emb, ai_matcher=ai)
        assert app_state.get_ai_matcher() is ai

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="AIMatcher 尚未初始化"):
            app_state.get_ai_matcher()
```

同时更新已有的 `_reset_globals` fixture 和 `test_sets_all_globals` 以覆盖 `_ai_matcher`：

```python
@pytest.fixture(autouse=True)
def _reset_globals() -> Generator[None, Any, None]:
    """每个测试前后重置模块级全局变量。"""
    app_state._index_manager = None
    app_state._ocr_service = None
    app_state._embedding_service = None
    app_state._image_optimizer = None
    app_state._ai_matcher = None
    yield
    app_state._index_manager = None
    app_state._ocr_service = None
    app_state._embedding_service = None
    app_state._image_optimizer = None
    app_state._ai_matcher = None
```

更新 `TestInitApp.test_sets_all_globals` 以包含 `ai_matcher` 参数。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/test_app_state.py -v
```

预期：`TestGetAiMatcher` 和更新后的 `TestInitApp` FAIL（`get_ai_matcher` / `_ai_matcher` 不存在）。

- [ ] **Step 3: 实现 app_state 扩展**

修改 `bot/app_state.py`：

```python
"""共享实例管理模块。

模块级单例模式，供插件获取 IndexManager、OcrService、EmbeddingService、AIMatcher。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from .engine import (
    AIMatcher,
    DeepSeekOcrService,
    EmbeddingService,
    ImageOptimizer,
    IndexManager,
)

_index_manager: IndexManager | None = None
_ocr_service: DeepSeekOcrService | None = None
_embedding_service: EmbeddingService | None = None
_image_optimizer: ImageOptimizer | None = None
_ai_matcher: AIMatcher | None = None


def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
        image_optimizer: 图片压缩器实例，可选。
        ai_matcher: AI 匹配器实例，可选。
    """
    global _index_manager, _ocr_service, _embedding_service, _image_optimizer, _ai_matcher
    _index_manager = index_manager
    _ocr_service = ocr_service
    _embedding_service = embedding_service
    _image_optimizer = image_optimizer
    _ai_matcher = ai_matcher


def get_index_manager() -> IndexManager:
    """获取 IndexManager 单例。

    Returns:
        已初始化的 IndexManager 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _index_manager is None:
        raise RuntimeError("IndexManager 尚未初始化，请先调用 init_app()")
    return _index_manager


def get_ocr_service() -> DeepSeekOcrService:
    """获取 DeepSeekOcrService 单例。

    Returns:
        已初始化的 DeepSeekOcrService 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _ocr_service is None:
        raise RuntimeError("DeepSeekOcrService 尚未初始化，请先调用 init_app()")
    return _ocr_service


def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例。

    Returns:
        已初始化的 EmbeddingService 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _embedding_service is None:
        raise RuntimeError("EmbeddingService 尚未初始化，请先调用 init_app()")
    return _embedding_service


def get_image_optimizer() -> ImageOptimizer | None:
    """获取 ImageOptimizer 单例。

    Returns:
        已初始化的 ImageOptimizer 实例，或 None（未注入时）。
    """
    return _image_optimizer


def get_ai_matcher() -> AIMatcher:
    """获取 AIMatcher 单例。

    Returns:
        已初始化的 AIMatcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _ai_matcher is None:
        raise RuntimeError("AIMatcher 尚未初始化，请先调用 init_app()")
    return _ai_matcher
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/test_app_state.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 运行全量测试确认无回归**

```bash
uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add bot/app_state.py tests/unit/test_app_state.py
git commit -m "feat(engine): app_state 新增 get_ai_matcher() 及 init_app 扩展"
```

---

### Task 2: 创建 meme_ai.py 插件

**Files:**
- Create: `bot/plugins/meme_ai.py`
- Create: `tests/unit/plugins/test_meme_ai.py`

- [ ] **Step 1: 编写全部失败测试**

创建 `tests/unit/plugins/test_meme_ai.py`：

```python
"""/ai 命令插件单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
):
    from bot.plugins import meme_ai
    from bot.plugins.meme_ai import handle_ai


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "/ai 加班心累") -> MagicMock:
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


def _make_matcher() -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    return matcher


def _make_index_manager(*, is_locked: bool = False, entry_count: int = 10) -> MagicMock:
    """创建模拟的 IndexManager。"""
    im = MagicMock()
    im.is_locked = is_locked
    im.entry_count = entry_count
    return im


def _make_ai_matcher(*, result: object = None, side_effect: Exception | None = None) -> MagicMock:
    """创建模拟的 AIMatcher。"""
    from bot.engine.ai_matcher import AIMatchResult

    am = MagicMock()
    if side_effect is not None:
        am.match = AsyncMock(side_effect=side_effect)
    elif result is not None:
        am.match = AsyncMock(return_value=result)
    else:
        am.match = AsyncMock(
            return_value=AIMatchResult(
                entry_id="1",
                filename="加班心累.jpg",
                text="加班到心累",
                similarity=0.95,
                source="rerank",
            )
        )
    return am


def _reset_cmd() -> None:
    """重置 mock_cmd 的 finish/send 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()
    _mock_cmd.send = AsyncMock()


# ---------------------------------------------------------------------------
# 测试：授权校验
# ---------------------------------------------------------------------------


class TestHandleAiAuth:
    """授权校验测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_authorized_user_proceeds(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """授权用户应正常执行。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher()

        await handle_ai(_make_bot(), _make_event())

        mock_get_ai.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """非授权用户应被静默忽略。"""
        _reset_cmd()
        bot = _make_bot()

        await handle_ai(bot, _make_event("999"))

        mock_get_im.assert_not_called()
        mock_get_ai.assert_not_called()
        _mock_cmd.finish.assert_not_awaited()
        bot.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# 测试：索引锁
# ---------------------------------------------------------------------------


class TestHandleAiLock:
    """索引锁测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_lock_contention_replies(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """索引锁占用时应回复提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager(is_locked=True)

        await handle_ai(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "索引正在更新" in _mock_cmd.finish.call_args[0][0]
        mock_get_ai.assert_not_called()


# ---------------------------------------------------------------------------
# 测试：描述为空
# ---------------------------------------------------------------------------


class TestHandleAiEmptyDesc:
    """描述为空测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_empty_description_replies_usage(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """/ai 无参数时应回复用法提示。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()

        await handle_ai(_make_bot(), _make_event(text="/ai"))

        _mock_cmd.finish.assert_awaited_once()
        assert "/ai" in _mock_cmd.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：匹配成功
# ---------------------------------------------------------------------------


class TestHandleAiSuccess:
    """匹配成功测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "MessageSegment")
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_match_sends_image(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ai: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """匹配成功时应发送图片。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher()

        await handle_ai(_make_bot(), _make_event("12345", "/ai 加班心累"))

        _mock_cmd.finish.assert_awaited_once()
        mock_segment.image.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_ai, "MessageSegment")
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_image_path_correct(
        self,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
        mock_get_ai: MagicMock,
        mock_segment: MagicMock,
    ) -> None:
        """图片路径应为 file:/// URI 格式。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher()

        await handle_ai(_make_bot(), _make_event("12345", "/ai 加班心累"))

        call_args = mock_segment.image.call_args[0][0]
        assert "memes" in str(call_args)
        assert str(call_args).startswith("file:///")


# ---------------------------------------------------------------------------
# 测试：无候选
# ---------------------------------------------------------------------------


class TestHandleAiNoMatch:
    """无候选测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_none_result_replies_no_match(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """AIMatcher 返回 None 时应回复无匹配。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher(result=None)

        await handle_ai(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "没有找到" in _mock_cmd.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：服务异常
# ---------------------------------------------------------------------------


class TestHandleAiServiceError:
    """服务异常测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_value_error_replies_unavailable(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """ValueError（embedding 无效）时应回复服务不可用。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher(
            side_effect=ValueError("embedding 为空")
        )

        await handle_ai(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "AI 服务暂时不可用" in _mock_cmd.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_generic_error_replies_unavailable(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """通用异常时应回复服务不可用。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager()
        mock_get_ai.return_value = _make_ai_matcher(
            side_effect=RuntimeError("API 超时")
        )

        await handle_ai(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "AI 服务暂时不可用" in _mock_cmd.finish.call_args[0][0]


# ---------------------------------------------------------------------------
# 测试：空索引
# ---------------------------------------------------------------------------


class TestHandleAiEmptyIndex:
    """空索引测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_ai, "get_ai_matcher")
    @patch.object(meme_ai, "get_index_manager")
    @patch.object(meme_ai, "is_authorized", return_value=True)
    async def test_empty_index_replies_empty(
        self, mock_auth: MagicMock, mock_get_im: MagicMock, mock_get_ai: MagicMock
    ) -> None:
        """索引为空时应回复表情包目录为空。"""
        _reset_cmd()
        mock_get_im.return_value = _make_index_manager(entry_count=0)

        await handle_ai(_make_bot(), _make_event())

        _mock_cmd.finish.assert_awaited_once()
        assert "表情包目录为空" in _mock_cmd.finish.call_args[0][0]
        mock_get_ai.assert_not_called()
```

- [ ] **Step 2: 运行测试确认全部失败**

```bash
uv run pytest tests/unit/plugins/test_meme_ai.py -v
```

预期：全部 FAIL（模块 `meme_ai` 不存在）。

- [ ] **Step 3: 实现 meme_ai.py 插件**

创建 `bot/plugins/meme_ai.py`：

```python
"""/ai 命令插件 — AI 描述匹配表情包。

授权用户在私聊中发送 /ai <自然语言描述>，Bot 通过 Embedding 语义搜索
+ LLM 精排两阶段匹配表情包并发送结果图片。
"""

import logging
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageSegment, PrivateMessageEvent
from nonebot.rule import to_me

from bot.app_state import get_ai_matcher, get_index_manager
from bot.auth import is_authorized, log_unauthorized

logger = logging.getLogger(__name__)

_MEMES_DIR = Path("memes")

ai_cmd = on_command("ai", rule=to_me(), priority=5, block=True)


@ai_cmd.handle()
async def handle_ai(bot: Bot, event: PrivateMessageEvent) -> None:
    """/ai 命令处理入口。

    流程：授权校验 → 锁检查 → 空索引检查 → AI 匹配 → 发送结果。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
    """
    user_id = event.get_user_id()

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "ai")
        return

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await ai_cmd.finish("服务未就绪，请稍后再试")
        return

    # 检查索引锁（只读检查，不持有锁）
    if index_manager.is_locked:
        logger.info("用户 %s 的 /ai 被拒绝：索引正在更新", user_id)
        await ai_cmd.finish("索引正在更新，请稍后再试")
        return

    # 提取描述
    raw_text = event.get_plaintext().strip()
    description = raw_text.removeprefix("/ai").removeprefix("ai").strip()
    if not description:
        await ai_cmd.finish("/ai <自然语言描述>")
        return

    # 检查索引是否为空
    if index_manager.entry_count == 0:
        await ai_cmd.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 获取 AIMatcher
    try:
        ai_matcher = get_ai_matcher()
    except RuntimeError:
        logger.error("AIMatcher 尚未初始化")
        await ai_cmd.finish("服务未就绪，请稍后再试")
        return

    # 发送进度提示
    await ai_cmd.send("正在根据你的描述搜索表情包，请稍候...")

    # 调用 AI 匹配
    try:
        result = await ai_matcher.match(description)
    except ValueError:
        logger.warning("AI 匹配 embedding 异常: description=%r", description)
        await ai_cmd.finish("AI 服务暂时不可用，稍后重试")
        return
    except Exception:
        logger.exception("AI 匹配异常: description=%r", description)
        await ai_cmd.finish("AI 服务暂时不可用，稍后重试")
        return

    # 无匹配结果
    if result is None:
        await ai_cmd.finish("没有找到匹配的表情包 🙁")
        return

    # 发送匹配图片（本地文件使用 file:/// URI）
    image_path = _MEMES_DIR / result.filename
    await ai_cmd.finish(MessageSegment.image(f"file:///{image_path.resolve()}"))
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/plugins/test_meme_ai.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 运行全量测试确认无回归**

```bash
uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add bot/plugins/meme_ai.py tests/unit/plugins/test_meme_ai.py
git commit -m "feat(plugins): 实现 /ai 命令插件及单元测试"
```

---

### Task 3: 更新 API 文档

**Files:**
- Create: `docs/api/bot/plugins/meme_ai.md`
- Modify: `docs/api/API.md`
- Modify: `docs/api/bot/app_state.md` (如果存在独立文件，否则在 API.md 中更新)

- [ ] **Step 1: 创建 meme_ai.md**

创建 `docs/api/bot/plugins/meme_ai.md`：

```markdown
# meme_ai 插件

`/ai <自然语言描述>` — AI 描述匹配表情包。

## 注册

```python
ai_cmd = on_command("ai", rule=to_me(), priority=5, block=True)
```

## 处理函数

```python
async def handle_ai(bot: Bot, event: PrivateMessageEvent) -> None
```

## 依赖

- `app_state.get_index_manager()` — 检查索引锁和条目数
- `app_state.get_ai_matcher()` — AI 匹配
- `auth.is_authorized()` — 授权校验

## 流程

1. 授权校验
2. 检查索引锁 (`index_manager.is_locked`) — 只读检查
3. 提取描述（去除 `/ai` 前缀）
4. 检查索引是否为空
5. 发送进度提示
6. 调用 `AIMatcher.match(description)`
7. 发送结果图片或错误提示

## 错误处理

| 场景 | 回复 |
|------|------|
| 索引锁占用 | "索引正在更新，请稍后再试" |
| 描述为空 | "/ai <自然语言描述>" |
| 索引为空 | "表情包目录为空，请先添加图片并执行 /refresh" |
| ValueError (embedding 异常) | "AI 服务暂时不可用，稍后重试" |
| 通用异常 | "AI 服务暂时不可用，稍后重试" |
| 无候选 | "没有找到匹配的表情包 🙁" |
```

- [ ] **Step 2: 更新 API.md 索引**

在 `docs/api/API.md` 的目录结构中新增 `meme_ai.md` 条目，在 API 文件索引中新增：

```markdown
### `bot/plugins/meme_ai.py`

NoneBot2 命令插件，注册 `/ai` 命令。

- 依赖：`app_state.get_ai_matcher()`、`app_state.get_index_manager()`、`auth.is_authorized()`
- 锁：只读检查 `IndexManager.is_locked`
- 匹配：`AIMatcher.match() -> AIMatchResult | None`
```

同时更新 `app_state` 相关文档，反映 `get_ai_matcher()` 和 `init_app()` 新增参数。

- [ ] **Step 3: Commit**

```bash
git add docs/api/bot/plugins/meme_ai.md docs/api/API.md
git commit -m "docs(api): 新增 /ai 插件 API 文档及 app_state 更新"
```
