"""EmbeddingService 单元测试。"""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.embedding_service import EmbeddingService


class TestEmbeddingServiceInit:
    """构造函数测试。"""

    @patch("bot.engine.embedding_service.AsyncOpenAI")
    @patch.dict("os.environ", {}, clear=True)
    def test_default_values(self, _mock_openai: MagicMock) -> None:
        """无参数无环境变量时使用默认值。"""
        service = EmbeddingService()
        assert service._model == "BAAI/bge-m3"
        assert service._base_url == "https://api.siliconflow.cn/v1"

    @patch("bot.engine.embedding_service.AsyncOpenAI")
    @patch.dict(
        "os.environ",
        {
            "EMBEDDING_API_KEY": "test-key",
            "EMBEDDING_BASE_URL": "https://custom.api/v1",
            "EMBEDDING_MODEL": "custom-model",
        },
    )
    def test_from_env_vars(self, _mock_openai: MagicMock) -> None:
        """从环境变量读取配置。"""
        service = EmbeddingService()
        assert service._model == "custom-model"

    @patch("bot.engine.embedding_service.AsyncOpenAI")
    def test_client_wired_correctly(self, mock_openai: MagicMock) -> None:
        """验证 AsyncOpenAI 使用正确的参数构造。"""
        EmbeddingService(
            api_key="my-key",
            base_url="https://custom.api/v1",
        )
        mock_openai.assert_called_once_with(
            api_key="my-key",
            base_url="https://custom.api/v1",
        )

    @patch("bot.engine.embedding_service.AsyncOpenAI")
    def test_constructor_params_override_env(self, _mock_openai: MagicMock) -> None:
        """构造参数优先于环境变量。"""
        service = EmbeddingService(model="override-model")
        assert service._model == "override-model"


class TestEmbed:
    """embed 方法测试。"""

    @pytest.mark.asyncio
    async def test_empty_text_raises_value_error(self) -> None:
        """空文本抛出 ValueError。"""
        service = EmbeddingService(api_key="test-key")
        with pytest.raises(ValueError, match="不能为空"):
            await service.embed("")

    @pytest.mark.asyncio
    async def test_whitespace_only_text_raises_value_error(self) -> None:
        """纯空白文本抛出 ValueError。"""
        service = EmbeddingService(api_key="test-key")
        with pytest.raises(ValueError, match="不能为空"):
            await service.embed("   ")

    @pytest.mark.asyncio
    async def test_returns_embedding(self) -> None:
        """正常调用返回 embedding 向量。"""
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]

        service = EmbeddingService(api_key="test-key")
        service._client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await service.embed("test text")
        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_api_failure_raises_runtime_error(self) -> None:
        """API 调用失败抛出 RuntimeError。"""
        service = EmbeddingService(api_key="test-key")
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

        service = EmbeddingService(api_key="test-key")
        service._client.embeddings.create = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="返回为空"):
            await service.embed("test text")


class TestEmbeddingSemaphore:
    """验证 EmbeddingService 的 Semaphore 并发控制。"""

    @pytest.mark.asyncio
    async def test_default_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不传 concurrency 时使用环境变量默认值 (5)。"""
        monkeypatch.delenv("EMBEDDING_CONCURRENCY", raising=False)
        service = EmbeddingService(api_key="sk-test", base_url="http://test")
        assert service._semaphore._value == 5

    @pytest.mark.asyncio
    async def test_custom_concurrency(self) -> None:
        """传 concurrency=2 时 Semaphore 值为 2。"""
        service = EmbeddingService(
            api_key="sk-test", base_url="http://test", concurrency=2
        )
        assert service._semaphore._value == 2

    @pytest.mark.asyncio
    async def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """设置 EMBEDDING_CONCURRENCY 环境变量时生效。"""
        monkeypatch.setenv("EMBEDDING_CONCURRENCY", "3")
        service = EmbeddingService(api_key="sk-test", base_url="http://test")
        assert service._semaphore._value == 3

    @pytest.mark.asyncio
    async def test_semaphore_blocks_concurrent(self) -> None:
        """concurrency=1 时第二个并发调用应阻塞。"""
        from unittest.mock import AsyncMock, MagicMock

        service = EmbeddingService(
            api_key="sk-test", base_url="http://test", concurrency=1
        )

        async def slow_create(*args: object, **kwargs: object) -> MagicMock:
            await asyncio.sleep(10)
            mock_r = MagicMock()
            mock_r.data = [MagicMock(embedding=[0.1])]
            return mock_r

        service._client.embeddings.create = AsyncMock(side_effect=slow_create)

        task1 = asyncio.create_task(service.embed("text1"))
        await asyncio.sleep(0.05)  # 确保 task1 进入 semaphore

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service.embed("text2"), timeout=0.1)

        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
