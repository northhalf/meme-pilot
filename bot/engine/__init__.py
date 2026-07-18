"""engine 包 — MemePilot 核心引擎模块。

导出各子模块的公共接口，供插件层和外部代码使用。
加载时自动注册所有可用的 OCR 与 Embedding provider。
"""

import logging

# 从各子模块导出公共接口
from .collection_manager import CollectionManager
from .combined_searcher import CombinedSearcher
from .image_optimizer import ImageOptimizer, OptimizeResult
from .keyword_searcher import KeywordSearcher
from .metadata_store import MemeEntry, MetadataStore
from .protocols import EmbeddingProvider, OcrProvider
from .provider_factory import (
    mark_embedding_unavailable,
    mark_ocr_unavailable,
    register_embedding,
    register_ocr,
)
from .types import SearchResult
from .utils import resolve_unique_filename
from .vector_store import VectorHit, VectorStore

logger = logging.getLogger(__name__)

# OCR providers（导入失败时标记为不可用）
try:
    from .baidu_ocr import BaiduOcrService, create_baidu_ocr_service

    register_ocr("baidu", create_baidu_ocr_service)
except ImportError as exc:
    mark_ocr_unavailable("baidu", f"baidu_ocr 模块加载失败: {exc}")
    logger.warning("百度 OCR provider 不可用: %s", exc)

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

# Embedding providers
try:
    from .openai_embedding import (
        OpenAIEmbeddingService,
        create_openai_embedding_service,
    )

    register_embedding("openai", create_openai_embedding_service)
except ImportError as exc:
    mark_embedding_unavailable("openai", f"openai_embedding 模块加载失败: {exc}")
    logger.warning("OpenAI Embedding provider 不可用: %s", exc)

try:
    from .google_embedding import (
        GoogleEmbeddingService,
        create_google_embedding_service,
    )

    register_embedding("google", create_google_embedding_service)
except ImportError as exc:
    mark_embedding_unavailable("google", f"google_embedding 模块加载失败: {exc}")
    logger.warning("Google Embedding provider 不可用: %s", exc)

__all__ = [
    # protocols
    "EmbeddingProvider",
    "OcrProvider",
    # collection_manager
    "CollectionManager",
    # image_optimizer
    "ImageOptimizer",
    "OptimizeResult",
    # utils
    "resolve_unique_filename",
    # keyword_searcher
    "KeywordSearcher",
    "CombinedSearcher",
    "SearchResult",
    # metadata_store
    "MemeEntry",
    "MetadataStore",
    # vector_store
    "VectorHit",
    "VectorStore",
    # embedding
    "OpenAIEmbeddingService",
    "GoogleEmbeddingService",
    # ocr
    "BaiduOcrService",
    "OpenAIOcrService",
    "PaddleOcrClientService",
    "RapidOcrService",
]
