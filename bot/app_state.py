"""共享实例管理模块。

模块级单例模式，供插件获取 IndexManager、MetadataStore、VectorStore、
OcrProvider、EmbeddingProvider、ImageOptimizer、KeywordSearcher、RandomSearcher、
SemanticSearcher、CombinedSearcher、CollectionManager。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from .engine import (
    CollectionManager,
    ImageOptimizer,
    KeywordSearcher,
    MetadataStore,
    VectorStore,
)
from .engine.protocols import EmbeddingProvider, OcrProvider
from .engine.random_searcher import RandomSearcher
from .engine.semantic_searcher import SemanticSearcher
from .engine.combined_searcher import CombinedSearcher
from .index_manager import IndexManager

_index_manager: IndexManager | None = None
_metadata_store: MetadataStore | None = None
_vector_store: VectorStore | None = None
_collection_manager: CollectionManager | None = None
_ocr_service: OcrProvider | None = None
_embedding_service: EmbeddingProvider | None = None
_image_optimizer: ImageOptimizer | None = None
_keyword_searcher: KeywordSearcher | None = None
_random_searcher: RandomSearcher | None = None
_semantic_searcher: SemanticSearcher | None = None
_combined_searcher: CombinedSearcher | None = None


def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    vector_store: VectorStore,
    ocr_service: OcrProvider,
    embedding_service: EmbeddingProvider,
    image_optimizer: ImageOptimizer | None = None,
    keyword_searcher: KeywordSearcher | None = None,
    random_searcher: RandomSearcher | None = None,
    semantic_searcher: SemanticSearcher | None = None,
    combined_searcher: CombinedSearcher | None = None,
    collection_manager: CollectionManager | None = None,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        metadata_store: 元数据存储实例。
        vector_store: 向量存储实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
        image_optimizer: 图片压缩器实例，可选。
        keyword_searcher: 关键词搜索器实例，可选。
        random_searcher: 随机搜索器实例，可选。
        semantic_searcher: 语义搜索器实例，可选。
        combined_searcher: 组合搜索器实例，可选。
        collection_manager: 合集管理器实例，可选。
    """
    global _index_manager, _metadata_store, _vector_store, _collection_manager
    global _ocr_service, _embedding_service, _image_optimizer
    global _keyword_searcher, _random_searcher, _semantic_searcher, _combined_searcher
    _index_manager = index_manager
    _metadata_store = metadata_store
    _vector_store = vector_store
    _collection_manager = collection_manager
    _ocr_service = ocr_service
    _embedding_service = embedding_service
    _image_optimizer = image_optimizer
    _keyword_searcher = keyword_searcher
    _random_searcher = random_searcher
    _semantic_searcher = semantic_searcher
    _combined_searcher = combined_searcher


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


def get_vector_store() -> VectorStore:
    """获取 VectorStore 单例。

    Returns:
        已初始化的 VectorStore 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _vector_store is None:
        raise RuntimeError("VectorStore 尚未初始化，请先调用 init_app()")
    return _vector_store


def get_collection_manager() -> CollectionManager:
    """获取 CollectionManager 单例。

    Returns:
        已初始化的 CollectionManager 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 注入 CollectionManager。
    """
    if _collection_manager is None:
        raise RuntimeError("CollectionManager 尚未初始化，请先调用 init_app()")
    return _collection_manager


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


def get_image_optimizer() -> ImageOptimizer | None:
    """获取 ImageOptimizer 单例。

    Returns:
        已初始化的 ImageOptimizer 实例，或 None（未注入时）。
    """
    return _image_optimizer


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


def get_random_searcher() -> RandomSearcher:
    """获取 RandomSearcher 单例。

    Returns:
        已初始化的 RandomSearcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _random_searcher is None:
        raise RuntimeError("RandomSearcher 尚未初始化，请先调用 init_app()")
    return _random_searcher


def get_semantic_searcher() -> SemanticSearcher:
    """获取 SemanticSearcher 单例。

    Returns:
        已初始化的 SemanticSearcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _semantic_searcher is None:
        raise RuntimeError("SemanticSearcher 尚未初始化，请先调用 init_app()")
    return _semantic_searcher


def get_combined_searcher() -> CombinedSearcher:
    """获取 CombinedSearcher 单例。

    Returns:
        已初始化的 CombinedSearcher 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _combined_searcher is None:
        raise RuntimeError("CombinedSearcher 尚未初始化，请先调用 init_app()")
    return _combined_searcher
