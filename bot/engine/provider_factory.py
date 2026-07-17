"""Provider 工厂与注册表。

维护 OCR 与 Embedding provider 的注册表，支持按名称创建实例。
依赖缺失的 provider 可被标记为不可用，使用时抛出明确错误。
"""

from typing import Callable, TypeAlias

from .protocols import EmbeddingProvider, OcrProvider

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
    """按名称创建 OCR provider 实例。

    如果该 provider 已被注册，直接返回工厂创建的实例；
    不可用标记仅在 provider 未注册时生效。
    """
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
    """按名称创建 Embedding provider 实例。

    如果该 provider 已被注册，直接返回工厂创建的实例；
    不可用标记仅在 provider 未注册时生效。
    """
    factory = EMBEDDING_REGISTRY.get(name)
    if factory is not None:
        return factory()
    if name in _UNAVAILABLE_EMBEDDING_PROVIDERS:
        raise ProviderNotAvailableError(
            f"Embedding provider '{name}' 不可用: {_UNAVAILABLE_EMBEDDING_PROVIDERS[name]}"
        )
    raise ValueError(f"未知 Embedding provider: {name}")


def reset_provider_registries() -> None:
    """清空 OCR 与 Embedding 的注册表及不可用状态。

    主要用于测试隔离，避免注册表状态污染。
    """
    OCR_REGISTRY.clear()
    EMBEDDING_REGISTRY.clear()
    _UNAVAILABLE_OCR_PROVIDERS.clear()
    _UNAVAILABLE_EMBEDDING_PROVIDERS.clear()


__all__ = [
    "OCR_REGISTRY",
    "EMBEDDING_REGISTRY",
    "ProviderNotAvailableError",
    "register_ocr",
    "register_embedding",
    "mark_ocr_unavailable",
    "mark_embedding_unavailable",
    "create_ocr_provider",
    "create_embedding_provider",
    "reset_provider_registries",
]
