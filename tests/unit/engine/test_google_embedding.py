"""Google Embedding provider 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.google_embedding import (
    GoogleEmbeddingService,
    create_google_embedding_service,
)


@pytest.mark.asyncio
async def test_embed_returns_vector() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")
    fake_response = MagicMock()
    fake_response.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]

    with patch("asyncio.to_thread", return_value=fake_response) as mock_to_thread:
        vector = await service.embed("hello")
        assert vector == [0.1, 0.2, 0.3]
        args, kwargs = mock_to_thread.call_args
        assert args == (service._client.models.embed_content,)
        assert kwargs["model"] == service._model
        assert kwargs["contents"] == "hello"
        assert kwargs["config"].output_dimensionality == 1024


@pytest.mark.asyncio
async def test_embed_empty_text_raises_value_error() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")
    with pytest.raises(ValueError, match="待向量化文本不能为空"):
        await service.embed("   ")


@pytest.mark.asyncio
async def test_embed_empty_response_raises_runtime_error() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")
    fake_response = MagicMock()
    fake_response.embeddings = []

    with patch("asyncio.to_thread", return_value=fake_response):
        with pytest.raises(RuntimeError, match="Google Embedding API 返回为空"):
            await service.embed("hello")


@pytest.mark.asyncio
async def test_close_passes_client_close_directly_to_thread() -> None:
    """close 应直接把 SDK close 方法交给 asyncio.to_thread。"""
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")

    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        await service.close()

    mock_to_thread.assert_awaited_once_with(service._client.close)


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
        assert service._base_url == "https://proxy.example.com"
        assert service._semaphore._value == 2
