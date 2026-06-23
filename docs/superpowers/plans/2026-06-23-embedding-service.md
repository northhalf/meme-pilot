# Embedding Service 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ []`) syntax for tracking.

**Goal:** 实现 SiliconFlowEmbeddingService，封装 SiliconFlow Embedding API，为 AIMatcher 提供文本向量化能力。

**Architecture:** 单一类实现 EmbeddingProvider 协议，通过 AsyncOpenAI 调用 SiliconFlow 的 OpenAI 兼容 embeddings API。

**Tech Stack:** Python 3.12, openai, pytest, pytest-asyncio

---

## 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/unit/engine/test_embedding_service.py` | 创建 | 单元测试 |
| `bot/engine/embedding_service.py` | 创建 | 实现文件 |

---

### Task 1: 实现 SiliconFlowEmbeddingService

**Files:**
- Create: `tests/unit/engine/test_embedding_service.py`
- Create: `bot/engine/embedding_service.py`

- [ ] **Step 1: 写失败的测试**

```python
"""SiliconFlowEmbeddingService 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.embedding_service import SiliconFlowEmbeddingService


class TestSiliconFlowEmbeddingServiceInit:
    """构造函数测试。"""

    @patch.dict("os.environ", {}, clear=True)
    def test_default_values(self) -> None:
        """无参数无环境变量时使用默认值。"""
        service = SiliconFlowEmbeddingService()
        assert service._model == "Qwen/Qwen3-Embedding-8B"
        assert service._base_url == "https://api.siliconflow.cn/v1"

    @patch.dict("os.environ", {
        "SILICONFLOW_API_KEY": "test-key",
        "SILICONFLOW_BASE_URL": "https://custom.api/v1",
        "SILICONFLOW_EMBEDDING_MODEL": "custom-model",
    })
    def test_from_env_vars(self) -> None:
        """从环境变量读取配置。"""
        service = SiliconFlowEmbeddingService()
        assert service._model == "custom-model"

    def test_constructor_params_override_env(self) -> None:
        """构造参数优先于环境变量。"""
        service = SiliconFlowEmbeddingService(model="override-model")
        assert service._model == "override-model"


class TestEmbed:
    """embed 方法测试。"""

    @pytest.mark.asyncio
    async def test_empty_text_raises_value_error(self) -> None:
        """空文本抛出 ValueError。"""
        service = SiliconFlowEmbeddingService(api_key="test-key")
        with pytest.raises(ValueError, match="不能为空"):
            await service.embed("")

    @pytest.mark.asyncio
    async def test_whitespace_only_text_raises_value_error(self) -> None:
        """纯空白文本抛出 ValueError。"""
        service = SiliconFlowEmbeddingService(api_key="test-key")
        with pytest.raises(ValueError, match="不能为空"):
            await service.embed("   ")

    @pytest.mark.asyncio
    async def test_returns_embedding(self) -> None:
        """正常调用返回 embedding 向量。"""
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]

        service = SiliconFlowEmbeddingService(api_key="test-key")
        service._client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await service.embed("test text")
        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_api_failure_raises_runtime_error(self) -> None:
        """API 调用失败抛出 RuntimeError。"""
        service = SiliconFlowEmbeddingService(api_key="test-key")
        service._client.embeddings.create = AsyncMock(
            side_effect=Exception("network error")
        )

        with pytest.raises(RuntimeError, match="调用失败"):
            await service.embed("test text")

    @pytest.mark.asyncio
    async def test_empty_response_raises_runtime_error(self) -> None:
        """API 返回空抛出 RuntimeError。"""
        mock_response = MagicMock()
        mock_response.data = []

        service = SiliconFlowEmbeddingService(api_key="test-key")
        service._client.embeddings.create = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="返回为空"):
            await service.embed("test text")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/engine/test_embedding_service.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'bot.engine.embedding_service'"

- [ ] **Step 3: 写最小实现**

```python
"""Embedding 服务模块 — 基于硅基流动 SiliconFlow Embedding API。

通过 OpenAI 兼容的 embeddings API 调用向量化模型，
为 AI 语义匹配提供文本 embedding 生成能力。

实现 ai_matcher.EmbeddingProvider 协议。
"""

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class SiliconFlowEmbeddingService:
    """SiliconFlow Embedding 服务，通过硅基流动 API 生成文本向量。

    实现 ai_matcher.EmbeddingProvider 协议，
    可直接注入给 AIMatcher 使用。

    Attributes:
        _client: AsyncOpenAI 客户端。
        _model: Embedding 模型名称。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        """初始化 SiliconFlowEmbeddingService。

        Args:
            api_key: 硅基流动 API Key，默认从 SILICONFLOW_API_KEY 环境变量读取。
            base_url: API 地址，默认从 SILICONFLOW_BASE_URL 环境变量读取，
                      回退为 https://api.siliconflow.cn/v1。
            model: Embedding 模型名，默认从 SILICONFLOW_EMBEDDING_MODEL 环境变量读取，
                   回退为 Qwen/Qwen3-Embedding-8B。
        """
        self._api_key = api_key or os.environ.get("SILICONFLOW_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
        )
        self._model = model or os.environ.get(
            "SILICONFLOW_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"
        )

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    async def embed(self, text: str) -> list[float]:
        """生成文本 embedding 向量。

        通过硅基流动 embeddings API 将文本转换为浮点向量。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量，浮点数列表。

        Raises:
            ValueError: 文本为空。
            RuntimeError: API 调用失败或返回为空。
        """
        text = text.strip()
        if not text:
            raise ValueError("待向量化文本不能为空")

        logger.debug(
            "调用 SiliconFlow Embedding: model=%s, text_len=%d",
            self._model,
            len(text),
        )
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text,
            )
        except Exception as exc:
            raise RuntimeError(
                f"SiliconFlow Embedding API 调用失败: {exc}"
            ) from exc

        if not response.data:
            raise RuntimeError("SiliconFlow Embedding API 返回为空")

        embedding = response.data[0].embedding
        logger.debug("Embedding 完成: %d 维", len(embedding))
        return embedding
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run pytest tests/unit/engine/test_embedding_service.py -v`
Expected: 5 passed

- [ ] **Step 5: 语法检查**

Run: `cd /home/northhalf/tmp/meme-pilot && uv run python -m compileall bot/engine/embedding_service.py`
Expected: `Compiling ... Syntax OK`

- [ ] **Step 6: 提交（需用户审核）**

```bash
git add tests/unit/engine/test_embedding_service.py bot/engine/embedding_service.py
git commit -m "feat(engine): 实现 SiliconFlowEmbeddingService

- 新增 bot/engine/embedding_service.py
- 实现 ai_matcher.EmbeddingProvider 协议
- 支持构造参数、环境变量、默认值三级配置
- 新增 tests/unit/engine/test_embedding_service.py"
```

---

## 文档更新

实现完成后更新：
- `docs/process.md` — 记录实现进度
- `docs/api/API.md` — 添加 embedding_service 接口说明
