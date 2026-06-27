# PaddleOCR 官方 API 集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MemePilot 中新增 `PaddleOcrClientService`，通过环境变量 `OCR_PROVIDER` 在 `deepseek` 与 `paddle` 之间切换 OCR 引擎。

**Architecture:** 新增 `bot/engine/paddle_ocr_client.py` 模块，`PaddleOcrClientService` 实现 `index_manager.OcrProvider` 协议（`async def ocr(image_path: str) -> str`）。`bot.py` 启动时根据 `read_ocr_provider()` 返回值选择创建 `PaddleOcrClientService` 或 `DeepSeekOcrService`，并注册 shutdown 钩子关闭 HTTP 会话。

**Tech Stack:** Python 3.12, NoneBot2, paddleocr>=3.7.0（已有）, pytest-asyncio

---

### Task 1: 创建 `PaddleOcrClientService` 类

**Files:**
- Create: `bot/engine/paddle_ocr_client.py`
- Test: `tests/unit/engine/test_paddle_ocr_client.py`

- [ ] **Step 1: 编写单元测试（mock AsyncPaddleOCRClient）**

```python
"""PaddleOcrClientService 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from bot.engine.paddle_ocr_client import PaddleOcrClientService


class TestPaddleOcrClientServiceInit:
    """构造函数测试。"""

    @patch("bot.engine.paddle_ocr_client.AsyncPaddleOCRClient")
    @patch.dict("os.environ", {}, clear=True)
    def test_default_values(self, mock_client_cls: MagicMock) -> None:
        """无参数无环境变量时使用默认值。"""
        service = PaddleOcrClientService()
        mock_client_cls.assert_called_once()
        _, kwargs = mock_client_cls.call_args
        # 默认 token 为空字符串
        assert kwargs.get("token") == ""
        assert kwargs.get("request_timeout") == 300.0
        assert kwargs.get("poll_timeout") == 600.0

    @patch("bot.engine.paddle_ocr_client.AsyncPaddleOCRClient")
    @patch.dict(
        "os.environ",
        {"PADDLEOCR_ACCESS_TOKEN": "my-access-token"},
    )
    def test_from_env_var(self, mock_client_cls: MagicMock) -> None:
        """从环境变量读取 access_token。"""
        service = PaddleOcrClientService()
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("token") == "my-access-token"

    @patch("bot.engine.paddle_ocr_client.AsyncPaddleOCRClient")
    def test_constructor_params_override_env(
        self, mock_client_cls: MagicMock
    ) -> None:
        """构造参数优先于环境变量。"""
        service = PaddleOcrClientService(access_token="explicit-token")
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("token") == "explicit-token"

    @patch("bot.engine.paddle_ocr_client.AsyncPaddleOCRClient")
    @patch.dict(
        "os.environ",
        {
            "PADDLEOCR_ACCESS_TOKEN": "env-token",
            "PADDLEOCR_BASE_URL": "https://custom.api.com",
        },
    )
    def test_base_url_from_env(self, mock_client_cls: MagicMock) -> None:
        """PADDLEOCR_BASE_URL 传递到 AsyncPaddleOCRClient。"""
        service = PaddleOcrClientService()
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("base_url") == "https://custom.api.com"


class TestOcr:
    """ocr 方法测试。"""

    @pytest.mark.asyncio
    async def test_ocr_returns_text_from_pruned_result(self) -> None:
        """OCR 正常返回文本（pruned_result 为字符串）。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = "识别到的文本内容"
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "识别到的文本内容"
        mock_client.ocr.assert_called_once_with(
            file_path="/path/to/image.png",
            model=service._model,
        )

    @pytest.mark.asyncio
    async def test_pruned_result_is_list_of_dicts(self) -> None:
        """pruned_result 为 list[dict] 时提取 text 字段拼接。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = [
            {"text": "第一行", "score": 0.95},
            {"text": "第二行", "score": 0.88},
        ]
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "第一行 第二行"

    @pytest.mark.asyncio
    async def test_pruned_result_is_none(self) -> None:
        """pruned_result 为 None 时返回空字符串。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = None
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_pages_returns_empty_string(self) -> None:
        """无识别结果时返回空字符串。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_ocr_result.pages = []
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == ""

    @pytest.mark.asyncio
    async def test_pruned_result_dict_with_text_key(self) -> None:
        """pruned_result 为 dict 时尝试提取 text 字段。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {"text": "从dict提取的文本"}
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "从dict提取的文本"

    @pytest.mark.asyncio
    async def test_api_auth_error_raises_runtime_error(self) -> None:
        """AuthError 转为 RuntimeError。"""
        from paddleocr.paddleocr import PaddleOCRAPIError

        mock_client = MagicMock()
        mock_client.ocr = AsyncMock(
            side_effect=PaddleOCRAPIError("认证失败")
        )

        service = PaddleOcrClientService(access_token="bad-token")
        service._client = mock_client

        with pytest.raises(RuntimeError, match="PaddleOCR API 调用失败"):
            await service.ocr("/path/to/image.png")

    @pytest.mark.asyncio
    async def test_close_releases_client(self) -> None:
        """close() 调用 _client.close()。"""
        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        await service.close()
        mock_client.close.assert_awaited_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_paddle_ocr_client.py -v`
Expected: FAIL — ModuleNotFoundError（paddle_ocr_client.py 尚未创建）

- [ ] **Step 3: 创建 `PaddleOcrClientService`**

```python
"""PaddleOCR 云 API 客户端服务模块。

通过 paddleocr 库的 AsyncPaddleOCRClient 调用
PaddleOCR 官方云 API 进行图片文字识别。

实现 index_manager.OcrProvider 协议。
"""

from __future__ import annotations

import logging
import os

from paddleocr.paddleocr import (
    AsyncPaddleOCRClient,
    Model,
    PaddleOCRAPIError,
)

logger = logging.getLogger(__name__)

# pruned_result 中可能包含文本的常见字段名
_TEXT_FIELDS = frozenset({"text", "content", "transcription", "txt"})


def _extract_text(pruned_result: object) -> str:
    """从 pruned_result 中提取文本字符串。

    兼容多种返回格式：
    - str: 直接返回
    - list[dict]: 提取每个 dict 的 text/content/transcription/txt 字段，空格拼接
    - dict: 尝试提取 text/content/transcription/txt 字段
    - None: 返回空字符串

    Args:
        pruned_result: OCRResult.pages[i].pruned_result，类型 Any。

    Returns:
        提取到的文本字符串。
    """
    if pruned_result is None:
        return ""

    # 直接是字符串
    if isinstance(pruned_result, str):
        return pruned_result

    # 列表：可能是 list[dict] 或 list[str]
    if isinstance(pruned_result, list):
        parts: list[str] = []
        for item in pruned_result:
            if isinstance(item, dict):
                for field in _TEXT_FIELDS:
                    value = item.get(field)
                    if value and isinstance(value, str):
                        parts.append(value)
                        break
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)

    # 单个 dict
    if isinstance(pruned_result, dict):
        for field in _TEXT_FIELDS:
            value = pruned_result.get(field)
            if value and isinstance(value, str):
                return value

    # 兜底：转字符串
    text = str(pruned_result)
    return text if text.strip() else ""


class PaddleOcrClientService:
    """PaddleOCR 云 API OCR 服务。

    使用 AsyncPaddleOCRClient 调用 PaddleOCR 官方云 API
    进行图片文字识别。实现 index_manager.OcrProvider 协议。

    Attributes:
        _client: AsyncPaddleOCRClient 实例。
        _model: 使用的模型枚举值。
    """

    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        model: Model | str | None = None,
        request_timeout: float = 300.0,
        poll_timeout: float = 600.0,
    ) -> None:
        """初始化 PaddleOcrClientService。

        Args:
            access_token: AIStudio Access Token，默认从 PADDLEOCR_ACCESS_TOKEN
                          环境变量读取。
            base_url: API 地址，默认从 PADDLEOCR_BASE_URL 环境变量读取。
            model: OCR 模型，默认 Model.PP_OCRV6。
            request_timeout: 请求超时秒数，默认 300。
            poll_timeout: 轮询超时秒数，默认 600。
        """
        token = access_token or os.environ.get("PADDLEOCR_ACCESS_TOKEN", "")
        api_base_url = base_url or os.environ.get("PADDLEOCR_BASE_URL")

        self._model = model or Model.PP_OCRV6
        self._client = AsyncPaddleOCRClient(
            token=token,
            base_url=api_base_url,
            request_timeout=request_timeout,
            poll_timeout=poll_timeout,
        )

    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        调用 AsyncPaddleOCRClient.ocr() 提交 OCR 任务并等待完成，
        从返回结果的 pruned_result 中提取文本。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（可能为空字符串）。

        Raises:
            RuntimeError: API 调用失败。
        """
        logger.debug("调用 PaddleOCR API: %s", image_path)
        try:
            result = await self._client.ocr(
                file_path=image_path,
                model=self._model,
            )
        except PaddleOCRAPIError as exc:
            raise RuntimeError(f"PaddleOCR API 调用失败: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"PaddleOCR 调用异常: {exc}") from exc

        # 提取文本
        if not result.pages:
            logger.debug("PaddleOCR 无识别结果: %s", image_path)
            return ""

        texts: list[str] = []
        for page in result.pages:
            text = _extract_text(page.pruned_result)
            if text:
                texts.append(text)

        full_text = " ".join(texts)
        logger.debug("PaddleOCR 完成: %s → %d 字符", image_path, len(full_text))
        return full_text

    async def close(self) -> None:
        """释放 AsyncPaddleOCRClient 内部 HTTP 会话。"""
        await self._client.close()
        logger.debug("PaddleOcrClientService HTTP 会话已关闭")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_paddle_ocr_client.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: 提交**

```bash
git add bot/engine/paddle_ocr_client.py tests/unit/engine/test_paddle_ocr_client.py
git commit -m "feat(engine): 新增 PaddleOcrClientService 调用 PaddleOCR 云 API"
```

---

### Task 2: 扩展 `bot/config.py` 增加 `read_ocr_provider()`

**Files:**
- Modify: `bot/config.py`
- Test: `tests/unit/engine/test_ocr_provider_switch.py`

- [ ] **Step 1: 编写 config 测试**

```python
"""OCR_PROVIDER 环境变量读取测试。"""

from __future__ import annotations

from unittest.mock import patch

from bot.config import read_ocr_provider


class TestReadOcrProvider:
    """read_ocr_provider() 测试。"""

    @patch.dict("os.environ", {}, clear=True)
    def test_default_is_deepseek(self) -> None:
        """无环境变量时返回 'deepseek'。"""
        assert read_ocr_provider() == "deepseek"

    @patch.dict("os.environ", {"OCR_PROVIDER": "paddle"}, clear=True)
    def test_paddle(self) -> None:
        """OCR_PROVIDER=paddle 时返回 'paddle'。"""
        assert read_ocr_provider() == "paddle"

    @patch.dict("os.environ", {"OCR_PROVIDER": "deepseek"}, clear=True)
    def test_deepseek(self) -> None:
        """OCR_PROVIDER=deepseek 时返回 'deepseek'。"""
        assert read_ocr_provider() == "deepseek"

    @patch.dict("os.environ", {"OCR_PROVIDER": "  paddle  "}, clear=True)
    def test_whitespace_is_stripped(self) -> None:
        """值中的首尾空白被去除。"""
        assert read_ocr_provider() == "paddle"

    @patch.dict("os.environ", {"OCR_PROVIDER": "invalid-value"}, clear=True)
    def test_invalid_fallback_to_deepseek(self) -> None:
        """无效值回退为 'deepseek'。"""
        assert read_ocr_provider() == "deepseek"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_ocr_provider_switch.py -v`
Expected: FAIL — ImportError（read_ocr_provider 未定义）

- [ ] **Step 3: 在 `config.py` 中添加 `read_ocr_provider()`**

在 `bot/config.py` 末尾添加：

```python
# 有效 OCR Provider 值
_VALID_OCR_PROVIDERS: frozenset[str] = frozenset({"deepseek", "paddle"})


def read_ocr_provider() -> str:
    """从环境变量读取 OCR provider 类型。

    Returns:
        "deepseek"（默认）或 "paddle"。
    """
    raw = os.environ.get("OCR_PROVIDER", "deepseek").strip().lower()
    return raw if raw in _VALID_OCR_PROVIDERS else "deepseek"
```

同时在文件末尾更新 `__all__`：

```python
__all__ = ["PROJECT_ROOT", "MEMES_DIR", "read_session_timeout", "read_ocr_provider"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_ocr_provider_switch.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: 提交**

```bash
git add bot/config.py tests/unit/engine/test_ocr_provider_switch.py
git commit -m "feat(config): 新增 read_ocr_provider() 读取 OCR_PROVIDER 环境变量"
```

---

### Task 3: 导出 `PaddleOcrClientService`

**Files:**
- Modify: `bot/engine/__init__.py`

- [ ] **Step 1: 在 `__init__.py` 中增加导入和导出**

在 `from .ocr_service import DeepSeekOcrService` 之后添加：

```python
from .paddle_ocr_client import PaddleOcrClientService
```

在 `__all__` 列表的 `# ocr_service` 部分之后添加：

```python
    # paddle_ocr_client
    "PaddleOcrClientService",
```

具体来说：

1. 在 `from .ocr_service import DeepSeekOcrService` 行后插入 `from .paddle_ocr_client import PaddleOcrClientService`
2. 在 `# ocr_service` 说明行和 `"DeepSeekOcrService"` 后插入：
   ```python
   # paddle_ocr_client
   "PaddleOcrClientService",
   ```

- [ ] **Step 2: 验证导入正常**

Run: `uv run python -c "from bot.engine import PaddleOcrClientService; print('OK')"`
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add bot/engine/__init__.py
git commit -m "feat(engine): 导出 PaddleOcrClientService"
```

---

### Task 4: 修改 `bot.py` 实现条件 OCR 创建与 shutdown 钩子

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: 确认当前 _on_startup 代码**

当前 `bot.py:113` 写死了 `ocr_service = DeepSeekOcrService()`，需要改为条件创建。

- [ ] **Step 2: 修改 `bot.py`**

**修改导入部分** — 在 `from bot.config import MEMES_DIR, PROJECT_ROOT` 行后添加 `read_ocr_provider`：

```python
from bot.config import MEMES_DIR, PROJECT_ROOT, read_ocr_provider
```

**修改 `_on_startup()` 函数** — 将原第 113 行替换为：

```python
    # 2. 根据 OCR_PROVIDER 环境变量选择 OCR 引擎
    from bot.config import read_ocr_provider

    provider = read_ocr_provider()
    if provider == "paddle":
        ocr_service = PaddleOcrClientService()
        logger.info("OCR 引擎: PaddleOCR 云 API")
    else:
        ocr_service = DeepSeekOcrService()
        logger.info("OCR 引擎: DeepSeek-OCR（硅基流动）")
```

**新增 shutdown 钩子** — 在 `_on_startup` 函数之后、`main` 函数之前添加：

```python
async def _on_shutdown() -> None:
    """NoneBot2 关闭钩子 — 释放 OCR 服务的 HTTP 会话。"""
    from bot.app_state import get_ocr_service

    try:
        ocr_service = get_ocr_service()
    except RuntimeError:
        return  # 未初始化，跳过
    if hasattr(ocr_service, "close"):
        await ocr_service.close()
        logger.info("OCR 服务 HTTP 会话已关闭")
```

**在 `main()` 中注册 shutdown 钩子** — 在 `driver.on_startup(_on_startup)` 之后添加：

```python
    driver.on_shutdown(_on_shutdown)
```

同时需要在导入部分添加 `PaddleOcrClientService`：

```python
from bot.engine import (
    AIMatcher,
    DeepSeekOcrService,
    EmbeddingService,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    PaddleOcrClientService,
)
```

- [ ] **Step 3: 运行语法检查**

Run: `uv run python -m compileall bot/bot.py`
Expected: OK (no syntax errors)

- [ ] **Step 4: 提交**

```bash
git add bot/bot.py
git commit -m "feat(bot): 支持 OCR_PROVIDER 切换引擎，注册 shutdown 钩子"
```

---

### Task 5: 更新 `.env.example` 与 API 文档

**Files:**
- Modify: `.env.example`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 修改 `.env.example`**

在可选配置部分末尾添加：

```bash
# OCR 引擎选择：deepseek（默认）或 paddle
OCR_PROVIDER=deepseek

# PaddleOCR 官方云 API Access Token（当 OCR_PROVIDER=paddle 时必填）
PADDLEOCR_ACCESS_TOKEN=

# PaddleOCR API 地址（可选，默认使用 SDK 内置地址）
# PADDLEOCR_BASE_URL=https://aip.baidubce.com
```

- [ ] **Step 2: 更新 `docs/api/API.md`**

在 `docs/api/bot/engine/ocr_service.md` 部分之后、`docs/api/bot/engine/rerank_service.md` 部分之前添加：

```markdown
### `docs/api/bot/engine/paddle_ocr_client.md`

```python
class PaddleOcrClientService:
    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        model: Model | str | None = None,
        request_timeout: float = 300.0,
        poll_timeout: float = 600.0,
    ) -> None

    async def ocr(self, image_path: str) -> str
    async def close(self) -> None
```

- `access_token` 默认从 `PADDLEOCR_ACCESS_TOKEN` 环境变量读取
- `base_url` 默认从 `PADDLEOCR_BASE_URL` 环境变量读取
- `model` 默认 `Model.PP_OCRV6`
- `ocr()` 返回识别文本（空字符串表示无结果）
- `close()` 释放 HTTP 会话
- 异常：`RuntimeError`（API 调用失败）
```

同时更新 `app_state.md` 中 `init_app` 和 `get_ocr_service` 的类型签名，将 `DeepSeekOcrService` 改为更通用的描述（或保留原样——`init_app` 接受 `DeepSeekOcrService`，运行时实际传入可能是 `PaddleOcrClientService`。二者都实现了 `OcrProvider` 协议，无需改类型标注）。

在 `bot/config.py` 说明部分末尾添加：

```markdown
- `read_ocr_provider() -> str` — 从 `OCR_PROVIDER` 环境变量读取 OCR 引擎类型，默认 `"deepseek"`，有效值：`"deepseek"`、`"paddle"`
```

- [ ] **Step 3: 提交**

```bash
git add .env.example docs/api/API.md
git commit -m "docs: 添加 PaddleOCR 环境变量与 API 文档"
```

---

### Task 6: 最终集成测试与验证

**Files:**
- (None — integration test only)

- [ ] **Step 1: 运行全量测试确保无回归**

Run: `uv run pytest -v`
Expected: All existing tests PASS

- [ ] **Step 2: 验证 provider 切换逻辑覆盖完整**

快速确认三种场景：

```bash
# 默认 deepseek
OCR_PROVIDER= uv run python -c "from bot.config import read_ocr_provider; print(read_ocr_provider())"
# → deepseek

# paddle
OCR_PROVIDER=paddle uv run python -c "from bot.config import read_ocr_provider; print(read_ocr_provider())"
# → paddle

# 无效值回退
OCR_PROVIDER=invalid uv run python -c "from bot.config import read_ocr_provider; print(read_ocr_provider())"
# → deepseek
```

Expected: 三组分别输出 `deepseek`、`paddle`、`deepseek`

- [ ] **Step 3: 提交**

```bash
git commit -m "chore: PaddleOCR 集成最终验证通过"
```

---

## 实施范围清单

| 文件 | 操作 | Task |
|------|------|------|
| `bot/engine/paddle_ocr_client.py` | 新建 | Task 1 |
| `bot/engine/__init__.py` | 修改 | Task 3 |
| `bot/config.py` | 修改 | Task 2 |
| `bot/bot.py` | 修改 | Task 4 |
| `.env.example` | 修改 | Task 5 |
| `docs/api/API.md` | 修改 | Task 5 |
| `tests/unit/engine/test_paddle_ocr_client.py` | 新建 | Task 1 |
| `tests/unit/engine/test_ocr_provider_switch.py` | 新建 | Task 2 |

## 未覆盖的需求

- `tests/integration/test_paddle_ocr_client_api.py` — 需要真实 Access Token，设计文档已列出但标记为可选，留待后续补充
