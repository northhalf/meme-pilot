"""provider_factory 模块单元测试。"""

import pytest

from bot.engine.provider_factory import (
    ProviderNotAvailableError,
    create_embedding_provider,
    create_ocr_provider,
    mark_embedding_unavailable,
    mark_ocr_unavailable,
    register_embedding,
    register_ocr,
    reset_provider_registries,
)


@pytest.fixture(autouse=True)
def _clear_registries() -> None:
    """每个测试前清空注册表，避免状态污染。"""
    reset_provider_registries()


class FakeOcrProvider:
    async def ocr(self, image_path: str) -> str:
        return "fake"

    async def close(self) -> None:
        pass

class FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    async def close(self) -> None:
        pass

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
