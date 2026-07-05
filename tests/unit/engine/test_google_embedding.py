"""Google Embedding provider 单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from bot.engine.google_embedding import GoogleEmbeddingService, create_google_embedding_service


@pytest.mark.anyio
async def test_embed_returns_vector() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")
    fake_response = MagicMock()
    fake_response.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]

    def _fake_embed(*args, **kwargs):
        return fake_response

    with patch.object(service._client.models, "embed_content", side_effect=_fake_embed) as mock_embed:
        vector = await service.embed("hello")
        assert vector == [0.1, 0.2, 0.3]
        assert mock_embed.call_args is not None
        assert mock_embed.call_args.kwargs["config"].output_dimensionality == 1024


@pytest.mark.anyio
async def test_embed_empty_text_raises_value_error() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")
    with pytest.raises(ValueError, match="待向量化文本不能为空"):
        await service.embed("   ")


@pytest.mark.anyio
async def test_embed_empty_response_raises_runtime_error() -> None:
    service = GoogleEmbeddingService(api_key="test", model="gemini-embedding-2")
    fake_response = MagicMock()
    fake_response.embeddings = []

    def _fake_embed(*args, **kwargs):
        return fake_response

    with patch.object(service._client.models, "embed_content", side_effect=_fake_embed):
        with pytest.raises(RuntimeError, match="Google Embedding API 返回为空"):
            await service.embed("hello")


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
