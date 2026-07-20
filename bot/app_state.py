"""共享实例管理模块。

模块级单例模式，供插件获取 IndexManager、MetadataStore、OcrProvider、
EmbeddingProvider、KeywordSearcher。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from .engine import (
    KeywordSearcher,
    MetadataStore,
)
from .engine.protocols import EmbeddingProvider, OcrProvider
from .index_manager import IndexManager

_index_manager: IndexManager | None = None
_metadata_store: MetadataStore | None = None
_ocr_service: OcrProvider | None = None
_embedding_service: EmbeddingProvider | None = None
_keyword_searcher: KeywordSearcher | None = None


def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    ocr_service: OcrProvider,
    embedding_service: EmbeddingProvider,
    keyword_searcher: KeywordSearcher | None = None,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        metadata_store: 元数据存储实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
        keyword_searcher: 关键词搜索器实例，可选。
    """
    global _index_manager, _metadata_store
    global _ocr_service, _embedding_service, _keyword_searcher
    _index_manager = index_manager
    _metadata_store = metadata_store
    _ocr_service = ocr_service
    _embedding_service = embedding_service
    _keyword_searcher = keyword_searcher


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


def get_metadata_store() -> MetadataStore:
    """获取 MetadataStore 单例。

    Returns:
        已初始化的 MetadataStore 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _metadata_store is None:
        raise RuntimeError("MetadataStore 尚未初始化，请先调用 init_app()")
    return _metadata_store


def get_ocr_service() -> OcrProvider:
    """获取 OCR 服务单例。

    Returns:
        已初始化的 OCR 服务实例（实现 OcrProvider 协议）。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _ocr_service is None:
        raise RuntimeError("OCR 服务尚未初始化，请先调用 init_app()")
    return _ocr_service


def get_embedding_service() -> EmbeddingProvider:
    """获取 Embedding 服务单例。

    Returns:
        已初始化的 Embedding 服务实例（实现 EmbeddingProvider 协议）。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _embedding_service is None:
        raise RuntimeError("EmbeddingService 尚未初始化，请先调用 init_app()")
    return _embedding_service


def get_keyword_searcher() -> KeywordSearcher:
    """获取 KeywordSearcher 单例。

    Returns:
        已初始化的 KeywordSearcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _keyword_searcher is None:
        raise RuntimeError("KeywordSearcher 尚未初始化，请先调用 init_app()")
    return _keyword_searcher
