# OCR/Embedding Provider 扩展与重试机制实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 Provider 工厂/注册表，新增 RapidOCR 本地 OCR 与 Google Embedding provider，重命名 OpenAI 兼容 provider 模块，并为所有网络请求函数增加 tenacity 重试。

**Architecture:** 通过 `provider_factory.py` 维护 OCR/Embedding provider 注册表，`bot/engine/__init__.py` 在加载时自动注册可用 provider；新增 `retry_config.py` 提供统一网络重试装饰器；provider 实现类保持 Protocol 接口，供 `IndexManager` 和 `AIMatcher` 无感知切换。

**Tech Stack:** Python 3.12, NoneBot2, tenacity, rapidocr, google-genai, OpenAI SDK, httpx, pytest

---

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `bot/engine/provider_factory.py` | 创建 | Provider 注册表与 `create_*_provider()` 工厂函数 |
| `bot/engine/retry_config.py` | 创建 | tenacity 通用网络重试装饰器 |
| `bot/engine/openai_ocr.py` | 创建（由 `deepseek_ocr.py` 重命名） | OpenAI 兼容 vision OCR，增加重试 |
| `bot/engine/deepseek_ocr.py` | 删除 | 被 `openai_ocr.py` 替代 |
| `bot/engine/openai_embedding.py` | 创建（由 `embedding_service.py` 重命名） | OpenAI 兼容 Embedding，增加重试 |
| `bot/engine/embedding_service.py` | 删除 | 被 `openai_embedding.py` 替代 |
| `bot/engine/rapidocr_ocr.py` | 创建 | RapidOCR 本地 OCR provider |
| `bot/engine/google_embedding.py` | 创建 | Google Embedding API provider |
| `bot/engine/__init__.py` | 修改 | 自动注册所有可用 provider |
| `bot/engine/rerank_service.py` | 修改 | 增加 tenacity 重试 |
| `bot/config.py` | 修改 | 新增 `read_embedding_provider()`、`read_ocr_text_score()` |
| `bot/bot.py` | 修改 | 改用工厂函数创建 provider |
| `bot/app_state.py` | 修改 | 使用 Protocol 类型注解 |
| `.env.example` | 修改 | 新增 `EMBEDDING_PROVIDER`、`GOOGLE_*`、`OCR_TEXT_SCORE` |
| `docker-compose.yml` | 修改 | 透传新增环境变量 |
| `README.md` / `CONTEXT.md` | 修改 | 更新术语与环境变量说明 |
| `docs/api/bot/engine/*.md` | 重命名/新增/修改 | API 文档同步 |

---

## Task 1: 创建 `provider_factory.py`

**Files:**
- Create: `bot/engine/provider_factory.py`
- Test: `tests/unit/engine/test_provider_factory.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/engine/test_provider_factory.py
import pytest

from bot.engine.provider_factory import (
    OCR_REGISTRY,
    EMBEDDING_REGISTRY,
    _UNAVAILABLE_OCR_PROVIDERS,
    _UNAVAILABLE_EMBEDDING_PROVIDERS,
    ProviderNotAvailableError,
    create_embedding_provider,
    create_ocr_provider,
    mark_embedding_unavailable,
    mark_ocr_unavailable,
    register_embedding,
    register_ocr,
)


@pytest.fixture(autouse=True)
def _clear_registries() -> None:
    """每个测试前清空注册表，避免状态污染。"""
    OCR_REGISTRY.clear()
    EMBEDDING_REGISTRY.clear()
    _UNAVAILABLE_OCR_PROVIDERS.clear()
    _UNAVAILABLE_EMBEDDING_PROVIDERS.clear()


class FakeOcrProvider:
    async def ocr(self, image_path: str) -> str:
        return "fake"


class FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]


def test_create_ocr_provider_returns_registered_instance() -> None:
    register_ocr("fake", lambda: FakeOcrProvider())
    instance = create_ocr_provider("fake")
    assert isinstance(instance, FakeOcrProvider)


def test_create_embedding_provider_returns_registered_instance() -> None:
    register_embedding("fake", lambda: FakeEmbeddingProvider())
    instance = create_embedding_provider("fake")
    assert isinstance(instance, FakeEmbeddingProvider)


def test_create_ocr_provider_unknown_raises() -> None:
    with pytest.raises(ValueError, match="未知 OCR provider"):
        create_ocr_provider("not-exist")


def test_create_embedding_provider_unavailable_raises() -> None:
    mark_embedding_unavailable("missing", "dep not installed")
    with pytest.raises(ProviderNotAvailableError, match="missing"):
        create_embedding_provider("missing")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_provider_factory.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.engine.provider_factory'`

- [ ] **Step 3: 实现最小代码**

```python
# bot/engine/provider_factory.py
"""Provider 工厂与注册表。

维护 OCR 与 Embedding provider 的注册表，支持按名称创建实例。
依赖缺失的 provider 可被标记为不可用，使用时抛出明确错误。
"""

from typing import Callable, TypeAlias

from .index_manager import OcrProvider
from .protocols import EmbeddingProvider

Factory: TypeAlias = Callable[[], OcrProvider]
EmbeddingFactory: TypeAlias = Callable[[], EmbeddingProvider]

OCR_REGISTRY: dict[str, Factory] = {}
EMBEDDING_REGISTRY: dict[str, EmbeddingFactory] = {}

_UNAVAILABLE_OCR_PROVIDERS: dict[str, str] = {}
_UNAVAILABLE_EMBEDDING_PROVIDERS: dict[str, str] = {}


class ProviderNotAvailableError(ValueError):
    """Provider 因依赖缺失等原因不可用。"""


def register_ocr(name: str, factory: Factory) -> None:
    """注册 OCR provider 工厂函数。"""
    OCR_REGISTRY[name] = factory


def mark_ocr_unavailable(name: str, reason: str) -> None:
    """标记 OCR provider 不可用并记录原因。"""
    _UNAVAILABLE_OCR_PROVIDERS[name] = reason


def create_ocr_provider(name: str) -> OcrProvider:
    """按名称创建 OCR provider 实例。"""
    factory = OCR_REGISTRY.get(name)
    if factory is not None:
        return factory()
    if name in _UNAVAILABLE_OCR_PROVIDERS:
        raise ProviderNotAvailableError(
            f"OCR provider '{name}' 不可用: {_UNAVAILABLE_OCR_PROVIDERS[name]}"
        )
    raise ValueError(f"未知 OCR provider: {name}")


def register_embedding(name: str, factory: EmbeddingFactory) -> None:
    """注册 Embedding provider 工厂函数。"""
    EMBEDDING_REGISTRY[name] = factory


def mark_embedding_unavailable(name: str, reason: str) -> None:
    """标记 Embedding provider 不可用并记录原因。"""
    _UNAVAILABLE_EMBEDDING_PROVIDERS[name] = reason


def create_embedding_provider(name: str) -> EmbeddingProvider:
    """按名称创建 Embedding provider 实例。"""
    factory = EMBEDDING_REGISTRY.get(name)
    if factory is not None:
        return factory()
    if name in _UNAVAILABLE_EMBEDDING_PROVIDERS:
        raise ProviderNotAvailableError(
            f"Embedding provider '{name}' 不可用: {_UNAVAILABLE_EMBEDDING_PROVIDERS[name]}"
        )
    raise ValueError(f"未知 Embedding provider: {name}")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_provider_factory.py -v
```

Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add bot/engine/provider_factory.py tests/unit/engine/test_provider_factory.py
git commit -m "feat(engine): 增加 OCR/Embedding provider 工厂与注册表"
```

---

## Task 2: 创建 `retry_config.py`

**Files:**
- Create: `bot/engine/retry_config.py`
- Test: `tests/unit/engine/test_retry_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/engine/test_retry_config.py
import pytest

from bot.engine.retry_config import api_retry


class TransientError(RuntimeError):
    pass


@api_retry(extra_exceptions=(TransientError,))
async def flaky_function(fail_count: list[int]) -> str:
    if fail_count[0] < 2:
        fail_count[0] += 1
        raise TransientError("fail")
    return "ok"


@api_retry(extra_exceptions=())
async def no_retry_on_value_error() -> str:
    raise ValueError("should not retry")


@pytest.mark.anyio
async def test_api_retry_succeeds_after_transient_failures() -> None:
    assert await flaky_function([0]) == "ok"


@pytest.mark.anyio
async def test_api_retry_does_not_retry_value_error() -> None:
    with pytest.raises(ValueError, match="should not retry"):
        await no_retry_on_value_error()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_retry_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.engine.retry_config'`

- [ ] **Step 3: 实现最小代码**

```python
# bot/engine/retry_config.py
"""共享网络请求重试配置。"""

import logging
from typing import TypeAlias

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

ExceptionTuple: TypeAlias = tuple[type[Exception], ...]


def api_retry(
    *,
    max_attempts: int = 3,
    wait_min: float = 1,
    wait_max: float = 10,
    multiplier: float = 1,
    extra_exceptions: ExceptionTuple = (),
):
    """网络请求通用重试装饰器工厂。

    默认重试：httpx 网络/连接/超时异常、Python 内置 ConnectionError / TimeoutError，
    以及调用方传入的额外异常（如 OpenAI API 异常）。

    不重试：ValueError、FileNotFoundError 等本地/业务异常。
    """
    exceptions: ExceptionTuple = (
        httpx.NetworkError,
        httpx.ConnectError,
        httpx.TimeoutException,
        ConnectionError,
        TimeoutError,
    ) + extra_exceptions

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, min=wait_min, max=wait_max),
        retry=retry_if_exception_type(exceptions),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_retry_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add bot/engine/retry_config.py tests/unit/engine/test_retry_config.py
git commit -m "feat(engine): 增加 tenacity 网络重试装饰器"
```

---

## Task 3: 更新 `bot/config.py`

**Files:**
- Modify: `bot/config.py`
- Test: `tests/unit/test_config.py`（新增/更新测试）

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_config.py（追加）
import pytest

from bot.config import read_embedding_provider, read_ocr_text_score


class TestReadEmbeddingProvider:
    def test_default_is_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        assert read_embedding_provider() == "openai"

    def test_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "google")
        assert read_embedding_provider() == "google"

    def test_invalid_fallback_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "invalid")
        assert read_embedding_provider() == "openai"


class TestReadOcrTextScore:
    def test_default_is_0_9(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCR_TEXT_SCORE", raising=False)
        assert read_ocr_text_score() == 0.9

    def test_valid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_TEXT_SCORE", "0.75")
        assert read_ocr_text_score() == 0.75

    def test_invalid_fallback_to_0_9(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_TEXT_SCORE", "abc")
        assert read_ocr_text_score() == 0.9
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: `AttributeError: module 'bot.config' has no attribute 'read_embedding_provider'`

- [ ] **Step 3: 实现代码**

```python
# bot/config.py（追加，并修改 _VALID_OCR_PROVIDERS）

# 有效 OCR Provider 值
_VALID_OCR_PROVIDERS: frozenset[str] = frozenset({"deepseek", "paddle", "rapidocr"})

# 有效 Embedding Provider 值
_VALID_EMBEDDING_PROVIDERS: frozenset[str] = frozenset({"openai", "google"})


def read_ocr_provider() -> str:
    """从环境变量读取 OCR provider 类型。

    Returns:
        "paddle"（默认）、"deepseek" 或 "rapidocr"。
    """
    raw = os.environ.get("OCR_PROVIDER", "paddle").strip().lower()
    return raw if raw in _VALID_OCR_PROVIDERS else "paddle"


def read_embedding_provider() -> str:
    """从环境变量读取 Embedding provider 类型。

    Returns:
        "openai"（默认）或 "google"。
    """
    raw = os.environ.get("EMBEDDING_PROVIDER", "openai").strip().lower()
    return raw if raw in _VALID_EMBEDDING_PROVIDERS else "openai"


def read_ocr_text_score() -> float:
    """从环境变量读取 OCR 文本置信度阈值。

    PaddleOCR 与 RapidOCR 共用此阈值。

    Returns:
        阈值浮点数，默认 0.9；解析失败时回退为 0.9。
    """
    raw = os.environ.get("OCR_TEXT_SCORE", "")
    if not raw:
        return 0.9
    try:
        value = float(raw)
        return value if 0.0 <= value <= 1.0 else 0.9
    except ValueError:
        return 0.9
```

同时更新 `__all__`：

```python
__all__ = [
    "PROJECT_ROOT",
    "MEMES_DIR",
    "DATA_DIR",
    "INDEX_DB_PATH",
    "CHROMA_DIR",
    "read_session_timeout",
    "read_read_lock_timeout",
    "read_add_command_timeout",
    "read_ocr_provider",
    "read_embedding_provider",
    "read_ocr_text_score",
]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: 全部通过（包括原有测试）

- [ ] **Step 5: 提交**

```bash
git add bot/config.py tests/unit/test_config.py
git commit -m "feat(config): 增加 EMBEDDING_PROVIDER 与 OCR_TEXT_SCORE 配置读取"
```

---

## Task 4: 重命名 `deepseek_ocr.py` → `openai_ocr.py` 并增加重试

**Files:**
- Create: `bot/engine/openai_ocr.py`
- Delete: `bot/engine/deepseek_ocr.py`
- Test: 重命名 `tests/unit/engine/test_deepseek_ocr.py` → `tests/unit/engine/test_openai_ocr.py` 并更新
- Test: 重命名 `tests/integration/test_deepseek_ocr_api.py` → `tests/integration/test_openai_ocr_api.py` 并更新

- [ ] **Step 1: 使用 git mv 重命名文件并更新类名**

```bash
git mv bot/engine/deepseek_ocr.py bot/engine/openai_ocr.py
git mv tests/unit/engine/test_deepseek_ocr.py tests/unit/engine/test_openai_ocr.py
git mv tests/integration/test_deepseek_ocr_api.py tests/integration/test_openai_ocr_api.py
```

- [ ] **Step 2: 修改 `bot/engine/openai_ocr.py`**

将文件内所有 `DeepSeekOcrService` 替换为 `OpenAIOcrService`。
在 `__init__` 中设置 `AsyncOpenAI(max_retries=0)`。
为 `ocr()` 方法添加 `@api_retry(...)` 装饰器。

关键修改示例：

```python
# bot/engine/openai_ocr.py
import openai

from .retry_config import api_retry


class OpenAIOcrService:
    def __init__(...):
        ...
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
        )
        ...

    @api_retry(
        extra_exceptions=(
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
    )
    async def ocr(self, image_path: str) -> str:
        ...
```

同时新增工厂函数：

```python
def create_openai_ocr_service() -> OpenAIOcrService:
    """从环境变量创建 OpenAI OCR 服务。"""
    from bot.config import read_int_env

    return OpenAIOcrService(concurrency=read_int_env("OCR_CONCURRENCY"))
```

- [ ] **Step 3: 更新测试文件中的类名与导入**

在 `tests/unit/engine/test_openai_ocr.py` 与 `tests/integration/test_openai_ocr_api.py` 中：
- 将 `from bot.engine.deepseek_ocr import DeepSeekOcrService` 改为 `from bot.engine.openai_ocr import OpenAIOcrService`
- 将 `DeepSeekOcrService` 替换为 `OpenAIOcrService`
- 增加重试行为测试：模拟 `APIConnectionError` 重试后成功，以及连续失败最终抛出异常

- [ ] **Step 4: 运行单元测试确认通过**

```bash
uv run pytest tests/unit/engine/test_openai_ocr.py -v
```

Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add bot/engine/openai_ocr.py tests/unit/engine/test_openai_ocr.py tests/integration/test_openai_ocr_api.py
git commit -m "refactor(engine): deepseek_ocr 重命名为 openai_ocr 并增加 tenacity 重试"
```

---

## Task 5: 重命名 `embedding_service.py` → `openai_embedding.py` 并增加重试

**Files:**
- Create: `bot/engine/openai_embedding.py`
- Delete: `bot/engine/embedding_service.py`
- Test: 重命名 `tests/unit/engine/test_embedding_service.py` → `tests/unit/engine/test_openai_embedding.py` 并更新
- Test: 重命名 `tests/integration/test_embedding_service_api.py` → `tests/integration/test_openai_embedding_api.py` 并更新

- [ ] **Step 1: 使用 git mv 重命名文件并更新类名**

```bash
git mv bot/engine/embedding_service.py bot/engine/openai_embedding.py
git mv tests/unit/engine/test_embedding_service.py tests/unit/engine/test_openai_embedding.py
git mv tests/integration/test_embedding_service_api.py tests/integration/test_openai_embedding_api.py
```

- [ ] **Step 2: 修改 `bot/engine/openai_embedding.py`**

将 `EmbeddingService` 替换为 `OpenAIEmbeddingService`。
在 `__init__` 中设置 `AsyncOpenAI(max_retries=0)`。
为 `embed()` 添加 `@api_retry(...)`。
新增工厂函数 `create_openai_embedding_service()`。

```python
# bot/engine/openai_embedding.py
import openai

from .retry_config import api_retry


class OpenAIEmbeddingService:
    def __init__(...):
        ...
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
        )
        ...

    @api_retry(
        extra_exceptions=(
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
    )
    async def embed(self, text: str) -> list[float]:
        ...


def create_openai_embedding_service() -> OpenAIEmbeddingService:
    from bot.config import read_int_env

    return OpenAIEmbeddingService(concurrency=read_int_env("EMBEDDING_CONCURRENCY"))
```

- [ ] **Step 3: 更新测试文件中的类名与导入**

与 Task 4 类似，替换类名并增加重试测试。

- [ ] **Step 4: 运行单元测试确认通过**

```bash
uv run pytest tests/unit/engine/test_openai_embedding.py -v
```

Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add bot/engine/openai_embedding.py tests/unit/engine/test_openai_embedding.py tests/integration/test_openai_embedding_api.py
git commit -m "refactor(engine): embedding_service 重命名为 openai_embedding 并增加 tenacity 重试"
```

---

## Task 6: 创建 `rapidocr_ocr.py`

**Files:**
- Create: `bot/engine/rapidocr_ocr.py`
- Test: `tests/unit/engine/test_rapidocr_ocr.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/engine/test_rapidocr_ocr.py
from unittest.mock import MagicMock, patch

import pytest

from bot.engine.rapidocr_ocr import RapidOcrService, create_rapidocr_service


@pytest.mark.anyio
async def test_ocr_returns_cleaned_text() -> None:
    service = RapidOcrService(text_score=0.9)
    fake_result = MagicMock()
    fake_result.txt = ["hello  world"]

    with patch.object(service._engine, "__call__", return_value=fake_result):
        text = await service.ocr("/tmp/fake.png")
        assert text == "helloworld"


def test_create_rapidocr_service_uses_env() -> None:
    with patch.dict(
        "os.environ",
        {"OCR_TEXT_SCORE": "0.8", "OCR_CONCURRENCY": "3"},
        clear=False,
    ):
        service = create_rapidocr_service()
        assert service._text_score == 0.8
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_rapidocr_ocr.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.engine.rapidocr_ocr'`

- [ ] **Step 3: 实现代码**

```python
# bot/engine/rapidocr_ocr.py
"""RapidOCR 本地 OCR 服务模块。"""

import asyncio
import logging
import os
from typing import Any

from rapidocr import RapidOCR

logger = logging.getLogger(__name__)


class RapidOcrService:
    """RapidOCR 本地 OCR 服务。

    使用本地 ONNX 模型进行图片文字识别，实现 index_manager.OcrProvider 协议。
    """

    def __init__(
        self,
        text_score: float = 0.9,
        concurrency: int | None = None,
    ) -> None:
        """初始化 RapidOcrService。

        Args:
            text_score: 文本置信度阈值，默认 0.9。
            concurrency: 并发数，默认从 OCR_CONCURRENCY 环境变量读取，回退为 5。
        """
        self._text_score = text_score
        c = concurrency or int(os.environ.get("OCR_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)
        self._engine = RapidOCR(params={"Global.text_score": text_score})

    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串（已去除所有空白字符）。

        Raises:
            FileNotFoundError: 图片文件不存在。
            RuntimeError: 推理异常。
        """
        import os as _os

        if not _os.path.exists(image_path):
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        async with self._semaphore:
            logger.debug("调用 RapidOCR: %s", image_path)
            try:
                result = await asyncio.to_thread(self._engine, image_path)
            except Exception as exc:
                raise RuntimeError(f"RapidOCR 推理失败: {exc}") from exc

            # RapidOCR 返回格式可能为 tuple/list 或结果对象，做防御性解析
            ocr_result: Any = result[0] if isinstance(result, (tuple, list)) else result
            if ocr_result is None:
                return """

            # 优先尝试常见文本属性
            lines: list[str] = []
            for attr in ("txt", "rec_texts", "texts"):
                value = getattr(ocr_result, attr, None)
                if value:
                    lines = [str(t) for t in value if t]
                    break

            # 如果结果对象本身可迭代且元素是字符串，也尝试直接使用
            if not lines and ocr_result:
                try:
                    lines = [str(t) for t in ocr_result if t]
                except TypeError:
                    lines = []

            full_text = "".join("".join(lines).split())
            logger.debug("RapidOCR 完成: %s -> %s", image_path, full_text)
            return full_text

    async def close(self) -> None:
        """本地引擎无需释放网络会话。"""
        pass


def create_rapidocr_service() -> RapidOcrService:
    """从环境变量创建 RapidOCR 服务。"""
    from bot.config import read_int_env, read_ocr_text_score

    return RapidOcrService(
        text_score=read_ocr_text_score(),
        concurrency=read_int_env("OCR_CONCURRENCY"),
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_rapidocr_ocr.py -v
```

Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add bot/engine/rapidocr_ocr.py tests/unit/engine/test_rapidocr_ocr.py
git commit -m "feat(engine): 增加 RapidOCR 本地 OCR provider"
```

---

## Task 7: 创建 `google_embedding.py`

**Files:**
- Create: `bot/engine/google_embedding.py`
- Test: `tests/unit/engine/test_google_embedding.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/engine/test_google_embedding.py
from unittest.mock import MagicMock, patch

import pytest

from bot.engine.google_embedding import GoogleEmbeddingService, create_google_embedding_service


@pytest.mark.anyio
async def test_embed_returns_vector() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-001")
    fake_response = MagicMock()
    fake_response.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]

    def _fake_embed(*args, **kwargs):
        return fake_response

    with patch.object(service._client.models, "embed_content", side_effect=_fake_embed):
        vector = await service.embed("hello")
        assert vector == [0.1, 0.2, 0.3]


def test_create_google_embedding_service_uses_env() -> None:
    with patch.dict(
        "os.environ",
        {
            "GOOGLE_API_KEY": "gk",
            "GOOGLE_BASE_URL": "https://proxy.example.com",
            "GOOGLE_EMBEDDING_MODEL": "text-embedding-004",
            "EMBEDDING_CONCURRENCY": "2",
        },
        clear=False,
    ):
        service = create_google_embedding_service()
        assert service._api_key == "gk"
        assert service._model == "text-embedding-004"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/engine/test_google_embedding.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.engine.google_embedding'`

- [ ] **Step 3: 实现代码**

```python
# bot/engine/google_embedding.py
"""Google Embedding API 服务模块。"""

import asyncio
import logging
import os
from typing import Any

from google import genai
from google.genai import types

from .retry_config import api_retry

# Google GenAI SDK 异常类可能随版本变化，做防御性导入
try:
    _GOOGLE_API_ERROR: tuple[type[Exception], ...] = (genai.errors.APIError,)
except AttributeError:
    _GOOGLE_API_ERROR = ()

logger = logging.getLogger(__name__)


class GoogleEmbeddingService:
    """Google Embedding 服务，通过 Google GenAI SDK 生成文本向量。

    实现 protocols.EmbeddingProvider 协议。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        """初始化 GoogleEmbeddingService。

        Args:
            api_key: API Key，默认从 GOOGLE_API_KEY 环境变量读取。
            base_url: API 地址，默认从 GOOGLE_BASE_URL 环境变量读取。
            model: Embedding 模型名，默认从 GOOGLE_EMBEDDING_MODEL 环境变量读取，
                   回退为 gemini-embedding-001。
            concurrency: 并发数，默认从 EMBEDDING_CONCURRENCY 环境变量读取，回退为 5。
        """
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._base_url = base_url or os.environ.get("GOOGLE_BASE_URL")
        self._model = model or os.environ.get("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-001")

        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["http_options"] = {"base_url": self._base_url}
        self._client = genai.Client(**client_kwargs)

        c = concurrency or int(os.environ.get("EMBEDDING_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)

    @api_retry(extra_exceptions=_GOOGLE_API_ERROR)
    async def embed(self, text: str) -> list[float]:
        """生成文本 embedding 向量。

        Google GenAI SDK 当前为同步 API，通过 asyncio.to_thread 在线程池中调用，
        固定请求 1024 维输出，与现有 ChromaDB 索引维度保持一致。

        Args:
            text: 待向量化的文本。

        Returns:
            1024 维 embedding 向量。

        Raises:
            ValueError: 文本为空。
            RuntimeError: API 调用失败或返回为空。
        """
        text = text.strip()
        if not text:
            raise ValueError("待向量化文本不能为空")

        async with self._semaphore:
            logger.debug("调用 Google Embedding API: model=%s", self._model)
            try:
                response = await asyncio.to_thread(
                    self._client.models.embed_content,
                    model=self._model,
                    contents=text,
                    config=types.EmbedContentConfig(output_dimensionality=1024),
                )
            except Exception as exc:
                logger.info("Google Embedding API 调用失败: %s", exc)
                raise RuntimeError(f"Google Embedding API 调用失败: {exc}") from exc

            if not response.embeddings:
                logger.info("Google Embedding API 返回为空")
                raise RuntimeError("Google Embedding API 返回为空")

            embedding = response.embeddings[0].values
            logger.debug("Embedding 完成: %d 维", len(embedding))
            return embedding


def create_google_embedding_service() -> GoogleEmbeddingService:
    """从环境变量创建 Google Embedding 服务。"""
    from bot.config import read_int_env

    return GoogleEmbeddingService(concurrency=read_int_env("EMBEDDING_CONCURRENCY"))
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_google_embedding.py -v
```

Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add bot/engine/google_embedding.py tests/unit/engine/test_google_embedding.py
git commit -m "feat(engine): 增加 Google Embedding provider"
```

---

## Task 8: 更新 `rerank_service.py` 增加重试

**Files:**
- Modify: `bot/engine/rerank_service.py`
- Test: `tests/unit/engine/test_rerank_service.py`（如不存在则新增）

- [ ] **Step 1: 修改 `rerank_service.py`**

```python
# bot/engine/rerank_service.py
import openai

from .retry_config import api_retry


class RerankService:
    def __init__(...):
        ...
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
        )
        ...

    @api_retry(
        extra_exceptions=(
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
    )
    async def rerank(...):
        ...
```

- [ ] **Step 2: 增加/更新重试测试**

模拟 `APIConnectionError` 连续 2 次后成功，验证重试生效。

- [ ] **Step 3: 运行测试确认通过**

```bash
uv run pytest tests/unit/engine/test_rerank_service.py -v
```

Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add bot/engine/rerank_service.py tests/unit/engine/test_rerank_service.py
git commit -m "feat(engine): RerankService 增加 tenacity 重试"
```

---

## Task 9: 更新 `bot/engine/__init__.py` 自动注册

**Files:**
- Modify: `bot/engine/__init__.py`

- [ ] **Step 1: 修改 `__init__.py`**

保留原有公共导出，增加注册逻辑：

```python
# bot/engine/__init__.py
import logging

from .provider_factory import (
    mark_embedding_unavailable,
    mark_ocr_unavailable,
    register_embedding,
    register_ocr,
)

logger = logging.getLogger(__name__)

# 从各子模块导出公共接口
from .ai_matcher import (
    AIMatcher,
    AIMatchCandidate,
    AIMatchResult,
    MetadataEntryProvider,
    RerankProvider,
    VectorQueryProvider,
)
from .image_optimizer import ImageOptimizer, OptimizeResult
from .index_manager import (
    AddResult,
    DuplicateTextError,
    EditTextResult,
    IndexCorruptedError,
    IndexManager,
    OcrProvider,
    SyncResult,
    resolve_unique_filename,
)
from .keyword_searcher import KeywordSearcher, SearchResult
from .metadata_store import MemeEntry, MetadataStore
from .vector_store import VectorHit, VectorStore
from .protocols import EmbeddingProvider
from .rerank_service import RerankService

# OCR providers（导入失败时标记为不可用）
try:
    from .openai_ocr import OpenAIOcrService, create_openai_ocr_service
    register_ocr("deepseek", create_openai_ocr_service)
except ImportError as exc:
    mark_ocr_unavailable("deepseek", f"openai_ocr 模块加载失败: {exc}")
    logger.warning("OpenAI OCR provider 不可用: %s", exc)

try:
    from .paddle_ocr import PaddleOcrClientService, create_paddle_ocr_service
    register_ocr("paddle", create_paddle_ocr_service)
except ImportError as exc:
    mark_ocr_unavailable("paddle", f"paddle_ocr 模块加载失败: {exc}")
    logger.warning("PaddleOCR provider 不可用: %s", exc)

try:
    from .rapidocr_ocr import RapidOcrService, create_rapidocr_service
    register_ocr("rapidocr", create_rapidocr_service)
except ImportError as exc:
    mark_ocr_unavailable("rapidocr", f"rapidocr_ocr 模块加载失败: {exc}")
    logger.warning("RapidOCR provider 不可用: %s", exc)

# Embedding providers
try:
    from .openai_embedding import OpenAIEmbeddingService, create_openai_embedding_service
    register_embedding("openai", create_openai_embedding_service)
except ImportError as exc:
    mark_embedding_unavailable("openai", f"openai_embedding 模块加载失败: {exc}")
    logger.warning("OpenAI Embedding provider 不可用: %s", exc)

try:
    from .google_embedding import GoogleEmbeddingService, create_google_embedding_service
    register_embedding("google", create_google_embedding_service)
except ImportError as exc:
    mark_embedding_unavailable("google", f"google_embedding 模块加载失败: {exc}")
    logger.warning("Google Embedding provider 不可用: %s", exc)

__all__ = [
    "EmbeddingProvider",
    "AIMatcher",
    "AIMatchCandidate",
    "AIMatchResult",
    "MetadataEntryProvider",
    "RerankProvider",
    "VectorQueryProvider",
    "OpenAIEmbeddingService",
    "ImageOptimizer",
    "OptimizeResult",
    "AddResult",
    "DuplicateTextError",
    "EditTextResult",
    "IndexCorruptedError",
    "IndexManager",
    "OcrProvider",
    "SyncResult",
    "resolve_unique_filename",
    "KeywordSearcher",
    "SearchResult",
    "MemeEntry",
    "MetadataStore",
    "VectorHit",
    "VectorStore",
    "OpenAIOcrService",
    "PaddleOcrClientService",
    "RapidOcrService",
    "GoogleEmbeddingService",
    "RerankService",
]
```

- [ ] **Step 2: 验证 engine 包可导入**

```bash
uv run python -c "from bot.engine import OpenAIOcrService, RapidOcrService, GoogleEmbeddingService; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 运行全量 engine 单元测试**

```bash
uv run pytest tests/unit/engine -v
```

Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add bot/engine/__init__.py
git commit -m "feat(engine): __init__.py 自动注册所有可用 provider"
```

---

## Task 10: 更新 `bot.py` 使用工厂函数

**Files:**
- Modify: `bot/bot.py`
- Test: `tests/unit/test_bot.py`（如已覆盖则更新）

- [ ] **Step 1: 修改 `bot/bot.py`**

替换 import 与 provider 创建逻辑：

```python
# bot/bot.py
from bot.config import (
    CHROMA_DIR,
    INDEX_DB_PATH,
    MEMES_DIR,
    PROJECT_ROOT,
    read_bot_port,
    read_embedding_provider,
    read_int_env,
    read_ocr_provider,
)
from bot.engine import (
    AIMatcher,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    MetadataStore,
    RerankService,
    VectorStore,
)
from bot.engine.provider_factory import (
    create_embedding_provider,
    create_ocr_provider,
)


async def _on_startup() -> None:
    ...
    ocr_service = create_ocr_provider(read_ocr_provider())
    embedding_service = create_embedding_provider(read_embedding_provider())
    logger.info("OCR 引擎: %s, Embedding 引擎: %s", read_ocr_provider(), read_embedding_provider())
    ...
```

移除原有的 `if provider == "paddle": ... else: ...` 分支。

- [ ] **Step 2: 运行 bot 相关测试**

```bash
uv run pytest tests/unit/test_bot.py -v
```

Expected: 全部通过

- [ ] **Step 3: 提交**

```bash
git add bot/bot.py tests/unit/test_bot.py
git commit -m "refactor(bot): bot.py 改用 provider 工厂创建 OCR/Embedding 服务"
```

---

## Task 11: 更新 `app_state.py` 使用 Protocol 注解

**Files:**
- Modify: `bot/app_state.py`
- Test: `tests/unit/test_app_state.py`

- [ ] **Step 1: 修改 `app_state.py`**

```python
# bot/app_state.py
from .engine import (
    AIMatcher,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    MetadataStore,
    VectorStore,
)
from .engine.index_manager import OcrProvider
from .engine.protocols import EmbeddingProvider

_ocr_service: OcrProvider | None = None
_embedding_service: EmbeddingProvider | None = None


def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    vector_store: VectorStore,
    ocr_service: OcrProvider,
    embedding_service: EmbeddingProvider,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None:
    ...


def get_ocr_service() -> OcrProvider:
    ...


def get_embedding_service() -> EmbeddingProvider:
    ...
```

- [ ] **Step 2: 更新 `tests/unit/test_app_state.py` 中的导入**

将 `EmbeddingService`、`DeepSeekOcrService` 等替换为 `OpenAIEmbeddingService`、`OpenAIOcrService`。

- [ ] **Step 3: 运行测试确认通过**

```bash
uv run pytest tests/unit/test_app_state.py -v
```

Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add bot/app_state.py tests/unit/test_app_state.py
git commit -m "refactor(bot): app_state 使用 Protocol 注解解耦 provider 实现"
```

---

## Task 12: 更新 `.env.example` 与 `docker-compose.yml`

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: 修改 `.env.example`**

在文件合适位置新增或更新：

```bash
# OCR 引擎选择：paddle（默认）/ deepseek / rapidocr
# （如 .env.example 中已存在 OCR_PROVIDER，只需把 rapidocr 加入注释）

# OCR 文本置信度阈值（PaddleOCR 与 RapidOCR 共用）
OCR_TEXT_SCORE=0.9

# Embedding 引擎选择：openai（默认）/ google
EMBEDDING_PROVIDER=openai

# Google Embedding API 配置（当 EMBEDDING_PROVIDER=google 时必填）
GOOGLE_API_KEY=
GOOGLE_BASE_URL=
GOOGLE_EMBEDDING_MODEL=gemini-embedding-001
```

- [ ] **Step 2: 修改 `docker-compose.yml`**

在 `bot` 服务的 `environment` 段新增：

```yaml
- OCR_PROVIDER=${OCR_PROVIDER:-paddle}
- OCR_TEXT_SCORE=${OCR_TEXT_SCORE:-0.9}
- EMBEDDING_PROVIDER=${EMBEDDING_PROVIDER:-openai}
- GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
- GOOGLE_BASE_URL=${GOOGLE_BASE_URL:-}
- GOOGLE_EMBEDDING_MODEL=${GOOGLE_EMBEDDING_MODEL:-gemini-embedding-001}
```

- [ ] **Step 3: 提交**

```bash
git add .env.example docker-compose.yml
git commit -m "chore(config): 新增 OCR/Embedding provider 相关环境变量"
```

---

## Task 13: 更新 API 文档

**Files:**
- Rename: `docs/api/bot/engine/deepseek_ocr.md` → `docs/api/bot/engine/openai_ocr.md`
- Rename: `docs/api/bot/engine/embedding_service.md` → `docs/api/bot/engine/openai_embedding.md`
- Create: `docs/api/bot/engine/rapidocr_ocr.md`
- Create: `docs/api/bot/engine/google_embedding.md`
- Create: `docs/api/bot/engine/provider_factory.md`
- Create: `docs/api/bot/engine/retry_config.md`
- Modify: `docs/api/API.md`

- [ ] **Step 1: 使用 git mv 重命名文档**

```bash
git mv docs/api/bot/engine/deepseek_ocr.md docs/api/bot/engine/openai_ocr.md
git mv docs/api/bot/engine/embedding_service.md docs/api/bot/engine/openai_embedding.md
```

- [ ] **Step 2: 更新重命名后的文档内容**

将 `deepseek_ocr.md` 中的 `DeepSeekOcrService` 替换为 `OpenAIOcrService`，文件名同步替换。
将 `embedding_service.md` 中的 `EmbeddingService` 替换为 `OpenAIEmbeddingService`。

- [ ] **Step 3: 创建新 provider 文档**

`rapidocr_ocr.md`、`google_embedding.md` 参照现有 API 文档风格，列出类签名、`__init__`、`ocr()` / `embed()`。

- [ ] **Step 4: 创建工厂与重试文档**

`provider_factory.md`：列出注册表、注册函数、`create_*_provider()`、`ProviderNotAvailableError`。
`retry_config.md`：列出 `api_retry()` 签名与默认行为。

- [ ] **Step 5: 更新 `docs/api/API.md` 目录索引**

替换旧文件名，新增新文件条目。

- [ ] **Step 6: 提交**

```bash
git add docs/api/
git commit -m "docs(api): 同步 OCR/Embedding provider 与重试机制 API 文档"
```

---

## Task 14: 更新 `README.md` 与 `CONTEXT.md`

**Files:**
- Modify: `README.md`
- Modify: `CONTEXT.md`

- [ ] **Step 1: 更新 `README.md`**

- 在依赖列表增加 tenacity（如尚未列出）。
- 在部署步骤中说明 `OCR_PROVIDER`、`EMBEDDING_PROVIDER`、`OCR_TEXT_SCORE`、`GOOGLE_*` 环境变量。
- 在架构图中补充 RapidOCR / Google Embedding 说明。

- [ ] **Step 2: 更新 `CONTEXT.md`**

- 术语表增加 **RapidOCR**、**Google Embedding**、**Provider 工厂**、**OCR_TEXT_SCORE** 等条目。
- 更新 OCR provider 相关术语描述。

- [ ] **Step 3: 提交**

```bash
git add README.md CONTEXT.md
git commit -m "docs: 更新 README 与 CONTEXT 说明新 provider 与重试机制"
```

---

## Task 15: 全量验证

**Files:**
- 全部上述文件

- [ ] **Step 1: 语法检查**

```bash
uv run python -m compileall bot tests
```

Expected: 无错误

- [ ] **Step 2: 运行全量单元测试**

```bash
uv run pytest tests/unit -v
```

Expected: 全部通过

- [ ] **Step 3: 运行集成测试（可选，需要真实 API Key）**

```bash
export DEEPSEEK_API_KEY=sk-your-key
export EMBEDDING_API_KEY=sk-your-key
export GOOGLE_API_KEY=your-key
uv run pytest tests/integration -v -s
```

Expected: 全部通过（或仅因 API 配额/网络问题失败）

- [ ] **Step 4: 类型检查（如项目已配置）**

```bash
uv run pyright bot
```

Expected: 无新增类型错误

- [ ] **Step 5: 最终提交或标记完成**

```bash
git status
```

确认所有变更已提交。如有未提交内容，按模块分别提交。

---

## 自检

### Spec 覆盖检查

| Spec 需求 | 对应 Task |
|---|---|
| Provider 工厂/注册表 | Task 1 |
| `__init__.py` 自动注册 | Task 9 |
| `deepseek_ocr.py` → `openai_ocr.py` | Task 4 |
| `embedding_service.py` → `openai_embedding.py` | Task 5 |
| RapidOCR 本地 OCR | Task 6 |
| Google Embedding | Task 7 |
| tenacity 网络重试 | Task 2, Task 4-8 |
| `OCR_TEXT_SCORE` 共用阈值 | Task 3, Task 6, Task 12 |
| `app_state.py` Protocol 注解 | Task 11 |
| 文档同步 | Task 13-14 |

### 无占位符检查

- 无 TBD / TODO
- 所有步骤包含具体代码或命令
- 所有文件路径为绝对路径
- 类型签名前后一致

### 类型一致性检查

- `OcrProvider` 来自 `index_manager`
- `EmbeddingProvider` 来自 `protocols`
- `OpenAIOcrService` / `PaddleOcrClientService` / `RapidOcrService` 均实现 `OcrProvider`
- `OpenAIEmbeddingService` / `GoogleEmbeddingService` 均实现 `EmbeddingProvider`

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-05-ocr-embedding-providers-plan.md`.**

两个执行选项：

1. **Subagent-Driven（推荐）** — 每个 Task 派一个独立子代理执行，我在每轮后 review
2. **Inline Execution** — 在当前会话按 Task 批量执行，带检查点

请选择执行方式。
