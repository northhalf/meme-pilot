"""engine 包 — MemePilot 核心引擎模块。

导出各子模块的公共接口，供插件层和外部代码使用。
"""

from .ai_matcher import (
    AIIndexProvider,
    AIMatcher,
    AIMatchCandidate,
    AIMatchResult,
    RerankProvider,
)
from .embedding_service import EmbeddingService
from .index_manager import (
    AddResult,
    IndexManager,
    OcrProvider,
    SyncResult,
)
from .keyword_searcher import (
    IndexProvider,
    KeywordSearcher,
    SearchResult,
)
from .ocr_service import DeepSeekOcrService
from .protocols import EmbeddingProvider
from .rerank_service import RerankService

__all__ = [
    # protocols
    "EmbeddingProvider",
    # ai_matcher
    "AIIndexProvider",
    "AIMatcher",
    "AIMatchCandidate",
    "AIMatchResult",
    "RerankProvider",
    # embedding_service
    "EmbeddingService",
    # index_manager
    "AddResult",
    "IndexManager",
    "OcrProvider",
    "SyncResult",
    # keyword_searcher
    "IndexProvider",
    "KeywordSearcher",
    "SearchResult",
    # ocr_service
    "DeepSeekOcrService",
    # rerank_service
    "RerankService",
]
