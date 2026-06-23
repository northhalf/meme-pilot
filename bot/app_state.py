"""共享实例管理模块。

模块级单例模式，供各插件获取 IndexManager、OcrService、EmbeddingService。
bot.py 启动时调用 init_app() 初始化，插件通过 get_*() 函数获取实例。
"""

from .engine import DeepSeekOcrService, EmbeddingService, IndexManager

_index_manager: IndexManager | None = None
_ocr_service: DeepSeekOcrService | None = None
_embedding_service: EmbeddingService | None = None


def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """初始化全局共享实例。

    由 bot.py 的 NoneBot2 startup hook 调用，各插件随后可通过
    get_*() 函数获取已初始化的实例。

    Args:
        index_manager: 索引管理器实例。
        ocr_service: OCR 服务实例。
        embedding_service: Embedding 服务实例。
    """
    global _index_manager, _ocr_service, _embedding_service
    _index_manager = index_manager
    _ocr_service = ocr_service
    _embedding_service = embedding_service


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


def get_ocr_service() -> DeepSeekOcrService:
    """获取 DeepSeekOcrService 单例。

    Returns:
        已初始化的 DeepSeekOcrService 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _ocr_service is None:
        raise RuntimeError("DeepSeekOcrService 尚未初始化，请先调用 init_app()")
    return _ocr_service


def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例。

    Returns:
        已初始化的 EmbeddingService 实例。

    Raises:
        RuntimeError: 尚未调用 init_app() 初始化。
    """
    if _embedding_service is None:
        raise RuntimeError("EmbeddingService 尚未初始化，请先调用 init_app()")
    return _embedding_service
