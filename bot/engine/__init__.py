"""engine 包 — MemePilot 核心引擎模块。

导出各子模块的公共接口，供插件层和外部代码使用。
"""

from .ai_matcher import (
    AIMatcher,
    AIMatchCandidate,
    AIMatchResult,
    MetadataEntryProvider,
    RerankProvider,
    VectorQueryProvider,
)
from .embedding_service import EmbeddingService
from .image_optimizer import ImageOptimizer, OptimizeResult
from .index_manager import (
    AddResult,
    IndexCorruptedError,
    IndexManager,
    OcrProvider,
    SyncResult,
    resolve_unique_filename,
)
from .keyword_searcher import (
    KeywordSearcher,
    SearchResult,
)
from .metadata_store import MemeEntry, MetadataStore
from .vector_store import VectorHit, VectorStore
from .deepseek_ocr import DeepSeekOcrService
from .paddle_ocr import PaddleOcrClientService
from .protocols import EmbeddingProvider
from .rerank_service import RerankService

__all__ = [
    # protocols
    "EmbeddingProvider",
    # ai_matcher
    "AIMatcher",
    "AIMatchCandidate",
    "AIMatchResult",
    "MetadataEntryProvider",
    "RerankProvider",
    "VectorQueryProvider",
    # embedding_service
    "EmbeddingService",
    # image_optimizer
    "ImageOptimizer",
    "OptimizeResult",
    # index_manager
    "AddResult",
    "IndexCorruptedError",
    "IndexManager",
    "OcrProvider",
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
    # ocr
    "DeepSeekOcrService",
    "PaddleOcrClientService",
    # rerank
    "RerankService",
]
