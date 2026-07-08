"""engine 包 — MemePilot 核心引擎模块。

导出各子模块的公共接口，供插件层和外部代码使用。
加载时自动注册所有可用的 OCR 与 Embedding provider。
"""

import logging

from .provider_factory import (
    mark_embedding_unavailable,
    mark_ocr_unavailable,
    register_embedding,
    register_ocr,
)

logger = logging.getLogger(__name__)

# 从各子模块导出公共接口
from .ai_matcher import (
    AIMatcher,
    AIMatchCandidate,
    AIMatchResult,
    RerankProvider,
    VectorQueryProvider,
)
from .image_optimizer import ImageOptimizer, OptimizeResult
from .index_manager import (
    AddResult,
    DuplicateTextError,
    EditTextResult,
    IndexCorruptedError,
    IndexManager,
    OcrProvider,
    SyncResult,
    resolve_unique_filename,
)
from .keyword_searcher import KeywordSearcher, SearchResult
from .metadata_store import MemeEntry, MetadataStore
from .protocols import EmbeddingProvider, MetadataEntryProvider
from .rerank_service import RerankService
from .vector_store import VectorHit, VectorStore

# OCR providers（导入失败时标记为不可用）
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
    "MetadataEntryProvider",
    "OcrProvider",
    # ai_matcher
    "AIMatcher",
    "AIMatchCandidate",
    "AIMatchResult",
    "RerankProvider",
    "VectorQueryProvider",
    # image_optimizer
    "ImageOptimizer",
    "OptimizeResult",
    # index_manager
    "AddResult",
    "DuplicateTextError",
    "EditTextResult",
    "IndexCorruptedError",
    "IndexManager",
    "SyncResult",
    "resolve_unique_filename",
    # keyword_searcher
    "KeywordSearcher",
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
    "OpenAIOcrService",
    "PaddleOcrClientService",
    "RapidOcrService",
    # rerank
    "RerankService",
]
