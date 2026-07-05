# 设计文档：新增 OCR/Embedding Provider 与统一重试机制

> 日期：2026-07-05
> 状态：待实现
> 关联需求：
> 1. 提供 RapidOCR 本地 OCR 引擎
> 2. 提供 Google Embedding API 调用（gemini-embedding-001）
> 3. `deepseek_ocr.py` 重命名为通用 OpenAI 格式 OCR 模块
> 4. `embedding_service.py` 重命名为通用 OpenAI 格式 Embedding 模块
> 5. 为涉及联网请求的函数增加 tenacity 重试机制

---

## 1. 目标

- 将 OCR 与 Embedding provider 的创建从 `bot.py` 的硬编码 if/else 中解耦，改为**工厂/注册表**模式。
- 新增 RapidOCR 本地 OCR 与 Google Embedding 两个 provider。
- 重命名现有 OpenAI 兼容 provider 的文件与类名，使其不再绑定具体服务商。
- 为所有涉及网络请求的核心函数增加统一的 tenacity 重试策略。
- 保持协议接口不变，确保 `IndexManager`、`AIMatcher` 等上层模块无需改动。

---

## 2. 总体架构

```text
bot/engine/
├── __init__.py              # 导出公共接口，并在加载时自动注册所有可用 provider
├── provider_factory.py      # Provider 注册表与 create_*_provider() 工厂函数
├── retry_config.py          # tenacity 通用网络重试装饰器
├── openai_ocr.py            # 原 deepseek_ocr.py（OpenAI 兼容 vision OCR）
├── paddle_ocr.py            # PaddleOCR 云 API
├── rapidocr_ocr.py          # RapidOCR 本地 OCR（新增）
├── openai_embedding.py      # 原 embedding_service.py（OpenAI 兼容 Embedding）
├── google_embedding.py      # Google Embedding API（新增）
├── rerank_service.py        # DeepSeek LLM 精排（增加重试）
└── ...
```

---

## 3. Provider 工厂/注册表

### 3.1 `provider_factory.py`

维护两个注册表，并提供创建函数：

```python
OCR_REGISTRY: dict[str, Callable[[], OcrProvider]]
EMBEDDING_REGISTRY: dict[str, Callable[[], EmbeddingProvider]]

_UNAVAILABLE_OCR_PROVIDERS: dict[str, str]
_UNAVAILABLE_EMBEDDING_PROVIDERS: dict[str, str]

class ProviderNotAvailableError(ValueError): ...

def register_ocr(name: str, factory: Callable[[], OcrProvider]) -> None: ...
def mark_ocr_unavailable(name: str, reason: str) -> None: ...
def create_ocr_provider(name: str) -> OcrProvider: ...

def register_embedding(name: str, factory: Callable[[], EmbeddingProvider]) -> None: ...
def mark_embedding_unavailable(name: str, reason: str) -> None: ...
def create_embedding_provider(name: str) -> EmbeddingProvider: ...
```

**创建函数行为：**
- provider 已注册：调用工厂函数返回实例。
- provider 因 `ImportError` 被标记为不可用：抛 `ProviderNotAvailableError`，消息包含不可用原因。
- provider 完全未知：抛 `ValueError: 未知 OCR provider: xxx`。

### 3.2 `__init__.py` 自动注册

`bot/engine/__init__.py` 在导出公共接口的同时完成注册。每个 provider 的导入包裹在 `try/except ImportError` 中，缺失依赖时记录原因并跳过。

```python
# bot/engine/__init__.py（注册片段）
import logging

from .provider_factory import (
    mark_embedding_unavailable,
    mark_ocr_unavailable,
    register_embedding,
    register_ocr,
)

logger = logging.getLogger(__name__)

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
```

任何 `from bot.engine import ...` 都会触发 `__init__.py` 执行，从而完成自动注册。

### 3.3 `bot.py` 使用工厂

```python
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
    create_ocr_provider,
    create_embedding_provider,
)

ocr_service = create_ocr_provider(read_ocr_provider())
embedding_service = create_embedding_provider(read_embedding_provider())
```

---

## 4. 模块/类重命名

| 原文件 | 新文件 | 原类名 | 新类名 |
|---|---|---|---|
| `bot/engine/deepseek_ocr.py` | `bot/engine/openai_ocr.py` | `DeepSeekOcrService` | `OpenAIOcrService` |
| `bot/engine/embedding_service.py` | `bot/engine/openai_embedding.py` | `EmbeddingService` | `OpenAIEmbeddingService` |

**说明：**
- 新文件名与类名强调其实现的是 **OpenAI 兼容协议**，不再绑定 SiliconFlow/DeepSeek 品牌。
- 默认环境变量使用 `OPENAI_OCR_API_KEY` / `OPENAI_OCR_BASE_URL` / `OPENAI_OCR_MODEL`，不再绑定 SiliconFlow。
- 不保留旧类名/文件名的兼容别名。

---

## 5. 新增 Provider

### 5.1 RapidOCR 本地 OCR（`rapidocr_ocr.py`）

```python
class RapidOcrService:
    def __init__(
        self,
        text_score: float = 0.9,
        concurrency: int | None = None,
    ) -> None:
        ...

    async def ocr(self, image_path: str) -> str: ...
```

**实现要点：**
- 使用 `from rapidocr import RapidOCR`。
- 初始化时按 `text_score` 配置 `Global.text_score`（默认 0.9）。
- `text_score` 由工厂函数从 `OCR_TEXT_SCORE` 环境变量读取，与 PaddleOCR 共享同一阈值配置。
- `ocr()` 是同步 CPU 推理，通过 `asyncio.to_thread(...)` 在 `OCR_CONCURRENCY` 限流的线程池中执行。
- 返回去除所有空白后的文本，与 `OcrProvider` 协议一致。
- `close()` 为 no-op（无网络会话需要释放）。

### 5.2 Google Embedding（`google_embedding.py`）

```python
class GoogleEmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        ...

    async def embed(self, text: str) -> list[float]: ...
```

**实现要点：**
- 使用 `from google import genai`。
- 默认从 `GOOGLE_API_KEY`、`GOOGLE_BASE_URL`、`GOOGLE_EMBEDDING_MODEL` 环境变量读取配置。
- 默认模型 `gemini-embedding-001`。
- `embed()` 调用 `client.models.embed_content(..., config={"output_dimensionality": 1024})`，**固定 1024 维**，与现有 ChromaDB 索引保持一致。
- 使用 `EMBEDDING_CONCURRENCY` Semaphore 控制并发。

---

## 6. 配置与环境变量

### 6.1 新增/变更环境变量

| 变量名 | 作用 | 默认值 |
|---|---|---|
| `OCR_PROVIDER` | OCR 引擎选择 | `rapidocr` |
| `EMBEDDING_PROVIDER` | Embedding 引擎选择 | `openai` |
| `EMBEDDING_API_KEY` | OpenAI 兼容 Embedding API Key | `EMBEDDING_PROVIDER=openai` 时必填 |
| `GOOGLE_API_KEY` | Google Embedding API Key | `EMBEDDING_PROVIDER=google` 时必填 |
| `GOOGLE_BASE_URL` | Google GenAI API 代理地址 | 可选 |
| `GOOGLE_EMBEDDING_MODEL` | Google Embedding 模型名 | `gemini-embedding-001` |
| `OPENAI_OCR_API_KEY` | OpenAI 兼容 OCR API Key | `OCR_PROVIDER=deepseek` 时必填 |
| `PADDLEOCR_ACCESS_TOKEN` | PaddleOCR 云 API Access Token | `OCR_PROVIDER=paddle` 时必填 |
| `OCR_TEXT_SCORE` | OCR 文本置信度阈值（PaddleOCR 与 RapidOCR 共用） | `0.9` |

### 6.2 `bot/config.py` 变更

- 扩展 `_VALID_OCR_PROVIDERS` 为 `{"paddle", "deepseek", "rapidocr"}`。
- 新增 `_VALID_EMBEDDING_PROVIDERS = {"openai", "google"}`。
- 新增 `read_embedding_provider() -> str`，默认返回 `"openai"`。
- 新增 `read_ocr_text_score() -> float`，默认返回 `0.9`，支持环境变量缺失/非数字时回退；PaddleOCR 与 RapidOCR 共用此阈值，由各自工厂函数传入。

### 6.3 配置 `.env.example` 与 `docker-compose.yml`

同步新增上述环境变量，并在 `docker-compose.yml` 中透传给 `bot` 容器。

---

## 7. 网络请求重试机制（tenacity）

### 7.1 依赖

`tenacity` 依赖已由用户通过 `uv add tenacity` 加入 `pyproject.toml` 和 `uv.lock`，实现阶段不再修改依赖配置。

### 7.2 `retry_config.py`

```python
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
    """网络请求通用重试装饰器工厂。"""
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

### 7.3 应用范围

为以下涉及联网请求的函数增加 `@api_retry(...)`：

| 服务 | 方法 | 额外重试异常 |
|---|---|---|
| `OpenAIOcrService` | `ocr()` | `openai.APIConnectionError`, `openai.APITimeoutError`, `openai.RateLimitError`, `openai.InternalServerError` |
| `PaddleOcrClientService` | `ocr()` | `PaddleOCRAPIError` |
| `OpenAIEmbeddingService` | `embed()` | 同 OpenAI OCR |
| `GoogleEmbeddingService` | `embed()` | Google GenAI SDK 网络/服务端异常（以实际 SDK 异常类名为准） |
| `RerankService` | `rerank()` | 同 OpenAI OCR |

**OpenAI 客户端设置：**
为避免 OpenAI SDK 内部重试与 tenacity 重复，初始化 `AsyncOpenAI` 时设置 `max_retries=0`：

```python
self._client = AsyncOpenAI(
    api_key=self._api_key,
    base_url=self._base_url,
    max_retries=0,
)
```

**RapidOcrService** 为本地推理，不涉及网络，不加重试。

### 7.4 重试行为

- 最多尝试 3 次（1 次原始 + 2 次重试）。
- 指数退避：最小 1 秒，最大 10 秒，multiplier=1。
- 每次重试前记录 WARNING 日志。
- 最终失败时抛出原始异常。
- 不重试本地业务异常，如 `ValueError`、`FileNotFoundError`。

---

## 8. `app_state.py` 更新

`app_state.py` 使用已定义的 Protocol 作为 OCR 与 Embedding 服务的类型注解，避免依赖具体实现类：

```python
from .engine.index_manager import OcrProvider
from .engine.protocols import EmbeddingProvider

_ocr_service: OcrProvider | None = None
_embedding_service: EmbeddingProvider | None = None
```

同步更新 `init_app()`、`get_ocr_service()`、`get_embedding_service()` 的签名：

```python
def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    vector_store: VectorStore,
    ocr_service: OcrProvider,
    embedding_service: EmbeddingProvider,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None: ...


def get_ocr_service() -> OcrProvider: ...


def get_embedding_service() -> EmbeddingProvider: ...
```

---

## 9. 文档更新

### 9.1 API 文档

- 重命名并更新 `docs/api/bot/engine/deepseek_ocr.md` → `openai_ocr.md`。
- 重命名并更新 `docs/api/bot/engine/embedding_service.md` → `openai_embedding.md`。
- 新增 `docs/api/bot/engine/rapidocr_ocr.md`。
- 新增 `docs/api/bot/engine/google_embedding.md`。
- 新增 `docs/api/bot/engine/provider_factory.md`。
- 新增 `docs/api/bot/engine/retry_config.md`。
- 更新 `docs/api/API.md` 目录索引。

### 9.2 项目文档

- 更新 `README.md`：依赖列表、环境变量说明、provider 选择说明。
- 更新 `.env.example`：新增 `EMBEDDING_PROVIDER`、`GOOGLE_*`、`OCR_TEXT_SCORE`。
- 更新 `docker-compose.yml`：透传新增环境变量。
- 更新 `CONTEXT.md`：术语表中补充 RapidOCR、Google Embedding 等概念。

---

## 10. 测试策略

### 10.1 单元测试

- 重命名：
  - `tests/unit/engine/test_deepseek_ocr.py` → `test_openai_ocr.py`
  - `tests/unit/engine/test_embedding_service.py` → `test_openai_embedding.py`
- 新增：
  - `tests/unit/engine/test_rapidocr_ocr.py`
  - `tests/unit/engine/test_google_embedding.py`
  - `tests/unit/engine/test_provider_factory.py`
  - `tests/unit/engine/test_retry_config.py`
- 更新：
  - `tests/unit/engine/test_ocr_provider_switch.py`：覆盖 `rapidocr`。
  - 新增 `tests/unit/test_embedding_provider_switch.py`。
  - `tests/unit/test_app_state.py`：更新导入的类名。
  - 集成测试文件名与引用。

### 10.2 重试测试要点

- 模拟 API 连续失败 2 次、第 3 次成功，验证最终返回结果。
- 模拟连续失败 3 次，验证最终抛出原始异常。
- 模拟 `ValueError` / `FileNotFoundError`，验证 tenacity **不重试**。

### 10.3 Provider 工厂测试要点

- 验证已注册 provider 可被创建。
- 验证未注册 provider 抛 `ValueError`。
- 验证被标记为不可用的 provider 抛 `ProviderNotAvailableError`。

---

## 11. 实现顺序建议

1. 新增 `provider_factory.py` 与 `retry_config.py`。
2. 确认 `tenacity` 依赖已通过 `uv add` 加入 `pyproject.toml` 与 `uv.lock`。
3. 重命名 `deepseek_ocr.py` → `openai_ocr.py` 并更新类名/导入/重试。
4. 重命名 `embedding_service.py` → `openai_embedding.py` 并更新类名/导入/重试。
5. 新增 `rapidocr_ocr.py` 与 `google_embedding.py`。
6. 更新 `rerank_service.py` 增加重试。
7. 更新 `bot/engine/__init__.py` 完成自动注册。
8. 更新 `bot/config.py`、`.env.example`、`docker-compose.yml`。
9. 更新 `bot.py`、`app_state.py`。
10. 更新/新增测试。
11. 更新文档。

---

## 12. 注意事项

- **禁止自行在 `main` 分支执行 `git add` / `git commit`**：本设计文档与后续实现需经用户审核后由用户提交。
- 所有 provider 必须继续实现 `index_manager.OcrProvider` 与 `protocols.EmbeddingProvider` 协议。
- Google Embedding 固定输出 1024 维，避免与现有 ChromaDB 向量维度不一致导致写入失败。
- `RapidOcrService` 使用线程池执行同步推理，避免阻塞事件循环。

---

*本设计经用户确认后进入实现阶段。*
