"""索引管理模块 — 薄编排层。

持有 MetadataStore + VectorStore + providers，负责压缩→OCR→Embed 管道编排、
递归扫描、跨库一致性、合集生命周期、图片增删、读写锁与去重/无文字移图。
不直接写 SQL/Chroma，全部委托两个 Store。
"""

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, TypeVar

from bot.config import read_add_command_timeout, read_read_lock_timeout
from bot.log_context import timed

from .ai_matcher import AIMatcher, AIMatchResult
from .collection_manager import CollectionNotFoundError
from .combined_searcher import CombinedSearcher
from .image_optimizer import ImageOptimizer, OptimizeResult
from .index_types import (
    AddResult,
    AddTagResult,
    CollectionSelectionExpiredError,
    CompressionError,
    DeleteResult,
    DuplicateMemeInCollectionError,
    DuplicateTextError,
    EditTextResult,
    EmbeddingError,
    FileSystemSnapshot,
    IndexAddCancelledError,
    IndexCorruptedError,
    IndexInfo,
    MemeMoveError,
    MemeMoveSourceExpiredError,
    MovePreview,
    MoveResult,
    MoveSourceSnapshot,
    OcrError,
    OcrProvider,
    RefreshInProgressError,
    SetSpeakerResult,
    SyncResult,
    WriteOp,
    _OptimizerLockEntry,
    _WriteRequest,
)
from .keyword_searcher import KeywordSearcher
from .metadata_store import MemeEntry, MetadataStore
from .protocols import EmbeddingProvider
from .random_searcher import RandomSearcher
from .rwlock import IndexRwLock
from .semantic_searcher import SemanticSearcher
from .types import (
    GLOBAL_COLLECTION_NAME,
    CollectionSelection,
    MemePublicId,
    ScopeLike,
    SearchResult,
)
from .utils import (
    SecureMoveError,
    SecureMoveResult,
    get_regular_file_identity,
    resolve_unique_filename,
    secure_move_file,
    vector_norm,
)
from .vector_store import VectorRecord, VectorStore

if TYPE_CHECKING:
    from .collection_manager import (
        CollectionManager,
        CollectionSelection,
        CollectionSummary,
    )

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

__all__ = [
    "AddResult",
    "CollectionSelectionExpiredError",
    "CompressionError",
    "DeleteResult",
    "DuplicateMemeInCollectionError",
    "DuplicateTextError",
    "EditTextResult",
    "EmbeddingError",
    "FileSystemSnapshot",
    "IndexAddCancelledError",
    "IndexCorruptedError",
    "IndexInfo",
    "IndexManager",
    "MemeMoveError",
    "MemeMoveSourceExpiredError",
    "MovePreview",
    "MoveResult",
    "MoveSourceSnapshot",
    "OcrError",
    "OcrProvider",
    "RefreshInProgressError",
    "SetSpeakerResult",
    "SyncResult",
    "WriteOp",
    "resolve_unique_filename",
]


class IndexManager:
    """索引管理薄编排层。

    持有 MetadataStore + VectorStore + providers，负责管道编排与跨库一致性。

    Attributes:
        _metadata_store: 元数据存储。
        _vector_store: 向量存储。
        _collection_manager: 合集管理器，负责合集选择、公开 ID 解析与统计摘要。
        _memes_dir: 表情包图片目录。
        _no_text_dir: 无文字图目录。
        _deleted_dir: 已删除图目录。
        _ocr_provider / _embedding_provider / _optimizer: providers。
        _keyword_searcher: 关键词搜索器，由 IndexManager 持锁后调用。
        _ai_matcher: AI 匹配器，由 IndexManager 持锁后调用。
        _rwlock: 读写锁，写者优先。
        _refresh_active: 是否有 refresh 正在执行写锁内的同步。
        _shutting_down: 是否正在关闭。
        _write_drained: 写队列排空 Event（初始已 set；refresh 等待它之后获取写锁）。

    Class Attributes:
        SUPPORTED_EXTENSIONS: 支持的图片扩展名集合。
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: str,
        no_text_dir: str | None = None,
        deleted_dir: str | None = None,
        replaced_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizer | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
        random_searcher: RandomSearcher | None = None,
        semantic_searcher: SemanticSearcher | None = None,
        combined_searcher: CombinedSearcher | None = None,
        collection_manager: "CollectionManager | None" = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            metadata_store: 元数据存储实例。
            vector_store: 向量存储实例。
            memes_dir: 表情包图片目录路径。
            no_text_dir: 无文字图目录；None 时取 memes_dir 同级的 meme_no_text/。
            deleted_dir: 已删除图目录；None 时取 memes_dir 同级的 memes_deleted/。
            replaced_dir: 被替换旧图归档目录；None 时取 memes_dir 同级的 memes_replaced/。
            ocr_provider: OCR 服务提供者。
            embedding_provider: Embedding 服务提供者。
            optimizer: 图片压缩器。
            keyword_searcher: 关键词搜索器，由 IndexManager 持锁后调用。
            ai_matcher: AI 匹配器，由 IndexManager 持锁后调用。
            random_searcher: 随机搜索器，由 IndexManager 持锁后调用。
            semantic_searcher: 语义搜索器，由 IndexManager 持锁后调用。
            combined_searcher: 组合搜索器，由 IndexManager 持锁后调用。
            collection_manager: 合集管理器；None 时内部构造一个。
        """
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = Path(memes_dir)
        if no_text_dir is not None:
            self._no_text_dir = Path(no_text_dir)
        else:
            self._no_text_dir = Path(memes_dir).parent / "meme_no_text"
        if deleted_dir is not None:
            self._deleted_dir = Path(deleted_dir)
        else:
            self._deleted_dir = Path(memes_dir).parent / "memes_deleted"
        if replaced_dir is not None:
            self._replaced_dir = Path(replaced_dir)
        else:
            self._replaced_dir = Path(memes_dir).parent / "memes_replaced"
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider
        self._optimizer = optimizer
        self._keyword_searcher = keyword_searcher
        self._ai_matcher = ai_matcher
        self._random_searcher = random_searcher
        self._semantic_searcher = semantic_searcher
        self._combined_searcher = combined_searcher

        if collection_manager is None:
            from .collection_manager import CollectionManager

            collection_manager = CollectionManager(metadata_store)
        self._collection_manager = collection_manager

        self.read_timeout = float(read_read_lock_timeout())
        self.add_user_timeout = float(read_add_command_timeout())

        self._rwlock = IndexRwLock()
        self._refresh_active = False
        self._refresh_task: asyncio.Task | None = None
        self._shutting_down = False
        self._write_queue: asyncio.Queue[_WriteRequest] = asyncio.Queue()
        self._write_worker_task: asyncio.Task | None = None
        self._write_drained = asyncio.Event()
        self._write_drained.set()
        self._optimizer_target_locks: dict[tuple[str, str], _OptimizerLockEntry] = {}
        self._optimizer_registry_guard = asyncio.Lock()

    # ------------------------------------------------------------------
    # load / 查询
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """委托两个 Store.load()，并记录当前条目数。

        启动时必须调用此方法后再使用其他查询或写入方法。
        """
        logger.info("开始加载索引...")
        await asyncio.gather(
            asyncio.to_thread(self._metadata_store.load),
            asyncio.to_thread(self._vector_store.load),
        )
        logger.info("索引加载完成")
        logger.info("IndexManager 加载完成: %d 条记录", self.entry_count)

    @property
    def entry_count(self) -> int:
        """当前索引条目总数。

        Returns:
            当前 sqlite 中的条目数量。
        """
        return self._metadata_store.entry_count()

    def _search_locked(
        self, keyword: str, collection_id: int | None
    ) -> list[SearchResult]:
        """在已持有读锁时执行关键词搜索。

        Args:
            keyword: 用户输入的关键词。
            collection_id: 只搜索该合集；None 表示全库搜索。

        Returns:
            搜索结果列表；空库时返回空列表。

        Raises:
            RuntimeError: KeywordSearcher 未注入。
        """
        if self._metadata_store.entry_count() == 0:
            return []
        if self._keyword_searcher is None:
            raise RuntimeError("KeywordSearcher 未注入")
        entries = self._metadata_store.get_entries(collection_id)
        return self._keyword_searcher.search_in(entries, keyword)

    async def search(
        self, keyword: str, *, collection_id: int | None = None
    ) -> list[SearchResult]:
        """关键词搜索。

        Args:
            keyword: 用户输入的关键词。
            collection_id: 只搜索该合集；None 表示全库搜索。

        Returns:
            搜索结果列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: KeywordSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return self._search_locked(keyword, collection_id)

    def _random_search_locked(
        self, keyword: str | None, collection_id: int | None
    ) -> list[SearchResult]:
        """在已持有读锁时执行随机搜索。

        Args:
            keyword: 可选关键词；None 或空串表示全库随机。
            collection_id: 只在该合集内搜索；None 表示全库。

        Returns:
            随机取样后的 SearchResult 列表；空库时返回空列表。

        Raises:
            RuntimeError: RandomSearcher 未注入。
        """
        if self._metadata_store.entry_count() == 0:
            return []
        if self._random_searcher is None:
            raise RuntimeError("RandomSearcher 未注入")
        entries = self._metadata_store.get_entries(collection_id)
        return self._random_searcher.search_random_in(entries, keyword)

    async def random_search(
        self, keyword: str | None = None, *, collection_id: int | None = None
    ) -> list[SearchResult]:
        """随机搜索入口。持读锁调用 RandomSearcher.search_random_in。

        Args:
            keyword: 可选关键词；None 或空串表示全库随机。
            collection_id: 只在该合集内搜索；None 表示全库。

        Returns:
            随机取样后的 SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: RandomSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return self._random_search_locked(keyword, collection_id)

    async def _semantic_search_locked(
        self,
        query_vector: list[float],
        limit: int | None,
        collection_id: int | None,
    ) -> list[SearchResult]:
        """在已持有读锁时执行语义向量查询。

        Args:
            query_vector: 用户描述的 embedding 向量。
            limit: 返回结果数量上限；None 表示全库召回。
            collection_id: 只搜索该合集；None 表示全库。

        Returns:
            语义相似度 SearchResult 列表；向量库为空时返回空列表。
        """
        if self._vector_store.count() == 0:
            return []
        assert self._semantic_searcher is not None
        return await self._semantic_searcher.search_semantic(
            query_vector, limit=limit, collection_id=collection_id
        )

    async def semantic_search(
        self,
        description: str,
        limit: int | None = 10,
        *,
        collection_id: int | None = None,
    ) -> list[SearchResult]:
        """语义搜索入口。锁外 embed，持读锁查询。

        Args:
            description: 用户自然语言描述。
            limit: 返回结果数量上限；None 表示全库召回，默认 10。
            collection_id: 只搜索该合集；None 表示全库。

        Returns:
            语义相似度 SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: SemanticSearcher 或 EmbeddingProvider 未注入。
            ValueError: embedding 结果为零向量。
        """
        if self._semantic_searcher is None:
            raise RuntimeError("SemanticSearcher 未注入")
        if self._embedding_provider is None:
            raise RuntimeError("EmbeddingProvider 未注入")
        query_vector = await self._embedding_provider.embed(description)
        if vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")
        async with self._rwlock.read(timeout=self.read_timeout):
            return await self._semantic_search_locked(
                query_vector, limit, collection_id
            )

    def _search_combined_locked(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str],
        collection_id: int | None,
    ) -> list[SearchResult]:
        """在已持有读锁时执行组合搜索。

        Args:
            keyword: 关键词；None 或空串表示纯过滤。
            speakers: 说话人列表（OR，精确相等）；空列表不过滤。
            tags: 标签列表（AND，区分大小写）；空列表不过滤。
            collection_id: 只搜索该合集；None 表示全库。

        Returns:
            SearchResult 列表；空库时返回空列表。

        Raises:
            RuntimeError: CombinedSearcher 未注入。
        """
        if self._metadata_store.entry_count() == 0:
            return []
        if self._combined_searcher is None:
            raise RuntimeError("CombinedSearcher 未注入")
        entries = self._metadata_store.get_entries(collection_id)
        return self._combined_searcher.search_in(entries, keyword, speakers, tags)

    async def search_combined(
        self,
        keyword: str | None,
        speakers: list[str],
        tags: list[str],
        *,
        collection_id: int | None = None,
    ) -> list[SearchResult]:
        """组合检索入口。持读锁调用 CombinedSearcher.search_in。

        Args:
            keyword: 关键词；None 或空串表示纯过滤。
            speakers: 说话人列表（OR，精确相等）；空列表不过滤。
            tags: 标签列表（AND，区分大小写）；空列表不过滤。
            collection_id: 只搜索该合集；None 表示全库。

        Returns:
            SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: CombinedSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return self._search_combined_locked(keyword, speakers, tags, collection_id)

    async def _ai_match_locked(
        self,
        description: str,
        query_vector: list[float],
        collection_id: int | None,
    ) -> AIMatchResult | None:
        """在已持有读锁时执行 AI 候选匹配。

        Args:
            description: 用户自然语言描述。
            query_vector: 用户描述的 embedding 向量。
            collection_id: 只在该合集内匹配；None 表示全库。

        Returns:
            匹配结果；空库或无可行候选时返回 None。
        """
        assert self._ai_matcher is not None
        return await self._ai_matcher.match_with_vector(
            description, query_vector, collection_id=collection_id
        )

    async def ai_match(
        self, description: str, *, collection_id: int | None = None
    ) -> AIMatchResult | None:
        """AI 描述匹配。

        Args:
            description: 用户自然语言描述。
            collection_id: 只在该合集内匹配；None 表示全库。

        Returns:
            匹配结果；空库或无可行候选时返回 None。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: AIMatcher 未注入。
        """
        if self._ai_matcher is None:
            raise RuntimeError("AIMatcher 未注入")
        if self._embedding_provider is None:
            raise RuntimeError("EmbeddingProvider 未注入")
        query_vector = await self._embedding_provider.embed(description)
        async with self._rwlock.read(timeout=self.read_timeout):
            return await self._ai_match_locked(description, query_vector, collection_id)

    async def search_for_scope(
        self, scope: "ScopeLike", keyword: str
    ) -> list[SearchResult]:
        """在同一读锁内读取聊天合集并执行关键词搜索。

        Args:
            scope: 聊天作用域，用于读取当前合集选择。
            keyword: 用户输入的关键词。

        Returns:
            当前合集范围内的关键词搜索结果列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: KeywordSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            return self._search_locked(keyword, selection.search_filter)

    async def random_search_for_scope(
        self, scope: "ScopeLike", keyword: str | None = None
    ) -> tuple["CollectionSelection", list[SearchResult]]:
        """在同一读锁内读取聊天合集并执行随机搜索。

        Args:
            scope: 聊天作用域，用于读取当前合集选择。
            keyword: 可选关键词；None 或空串表示全库随机。

        Returns:
            (当前合集选择, 随机搜索结果) 二元组；空库时结果为空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: RandomSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            return selection, self._random_search_locked(
                keyword, selection.search_filter
            )

    async def random_search_for_scope_snapshot(
        self,
        scope: "ScopeLike",
        keyword: str | None,
        expected_selection: "CollectionSelection",
    ) -> list[SearchResult]:
        """校验聊天合集仍等于首次快照后执行随机搜索。

        Args:
            scope: 聊天作用域。
            keyword: 首次命令保存的可选关键词。
            expected_selection: 首次随机搜索返回的合集选择快照。

        Returns:
            使用原合集范围生成的新一批随机搜索结果。

        Raises:
            CollectionSelectionExpiredError: 当前选择已与首次快照不同。
            asyncio.TimeoutError: 等待读锁超时。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            current_selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            if current_selection != expected_selection:
                raise CollectionSelectionExpiredError("当前合集选择已变化")
            return self._random_search_locked(keyword, expected_selection.search_filter)

    async def semantic_search_for_scope(
        self,
        scope: "ScopeLike",
        description: str,
        limit: int | None = 10,
    ) -> list[SearchResult]:
        """锁外生成向量，并在同一读锁内读取聊天合集和执行语义搜索。

        Args:
            scope: 聊天作用域，用于读取当前合集选择。
            description: 用户自然语言描述。
            limit: 返回结果数量上限；None 表示全库召回，默认 10。

        Returns:
            当前合集范围内的语义相似度 SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: SemanticSearcher 或 EmbeddingProvider 未注入。
            ValueError: 用户描述 embedding 为零向量。
        """
        if self._semantic_searcher is None:
            raise RuntimeError("SemanticSearcher 未注入")
        if self._embedding_provider is None:
            raise RuntimeError("EmbeddingProvider 未注入")
        query_vector = await self._embedding_provider.embed(description)
        if vector_norm(query_vector) == 0:
            raise ValueError("用户描述 embedding 不能是零向量")
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            return await self._semantic_search_locked(
                query_vector, limit, selection.search_filter
            )

    async def search_combined_for_scope(
        self,
        scope: "ScopeLike",
        keyword: str | None,
        speakers: list[str],
        tags: list[str],
    ) -> list[SearchResult]:
        """在同一读锁内读取聊天合集并执行组合搜索。

        Args:
            scope: 聊天作用域，用于读取当前合集选择。
            keyword: 关键词；None 或空串表示纯过滤。
            speakers: 说话人列表（OR，精确相等）；空列表不过滤。
            tags: 标签列表（AND，区分大小写）；空列表不过滤。

        Returns:
            当前合集范围内的 SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: CombinedSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            return self._search_combined_locked(
                keyword, speakers, tags, selection.search_filter
            )

    async def ai_match_for_scope(
        self, scope: "ScopeLike", description: str
    ) -> AIMatchResult | None:
        """锁外生成向量，并在同一读锁内读取聊天合集和执行 AI 匹配。

        Args:
            scope: 聊天作用域，用于读取当前合集选择。
            description: 用户自然语言描述。

        Returns:
            当前合集范围内的匹配结果；空库或无可行候选时返回 None。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: AIMatcher 或 EmbeddingProvider 未注入。
        """
        if self._ai_matcher is None:
            raise RuntimeError("AIMatcher 未注入")
        if self._embedding_provider is None:
            raise RuntimeError("EmbeddingProvider 未注入")
        query_vector = await self._embedding_provider.embed(description)
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            return await self._ai_match_locked(
                description, query_vector, selection.search_filter
            )

    async def add(
        self,
        relative_path: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
        *,
        collection_id: int = 0,
        scope: "ScopeLike | None" = None,
        expected_selection: CollectionSelection | None = None,
    ) -> AddResult:
        """提交 /add 任务并等待执行完成。

        Args:
            relative_path: 图片相对 memes/ 的 POSIX 路径。
            speaker: 可选说话人。
            tags: 可选标签列表。
            collection_id: 目标合集编号，默认 0（全局）。
            scope: 发起添加的聊天作用域；与 expected_selection 同时传入。
            expected_selection: handle 阶段捕获的完整合集选择快照。

        Returns:
            AddResult 描述添加结果。

        Raises:
            RefreshInProgressError: 当前有刷新任务在运行。
            IndexAddCancelledError: Bot 正在关闭。
        """
        if (scope is None) != (expected_selection is None):
            raise ValueError("scope 与 expected_selection 必须同时提供")
        if expected_selection is not None and (
            expected_selection.collection_id != collection_id
        ):
            raise ValueError("collection_id 与 expected_selection 不一致")
        relative_path = self._validate_add_relative_path(relative_path)
        image_path = self._memes_dir / relative_path
        logger.info("添加图片: %s", image_path)
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

        async with asyncio.timeout(self.add_user_timeout):
            final_filename, text, embedding = await self._process_image_pipeline(
                relative_path
            )

            # TOCTOU 防护
            if self._shutting_down:
                raise IndexAddCancelledError("Bot 正在关闭")
            if self._refresh_active:
                raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

            self._ensure_write_worker()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            await self._write_queue.put(
                _WriteRequest(
                    op=WriteOp.ADD,
                    future=future,
                    filename=final_filename,
                    text=text,
                    speaker=speaker,
                    tags=tags,
                    embedding=embedding,
                    collection_id=collection_id,
                    scope=scope,
                    expected_selection=expected_selection,
                )
            )
            try:
                result = await future
                logger.info("图片添加完成: %s", image_path)
                return result
            except CollectionSelectionExpiredError:
                self._cleanup_pipeline_output(self._memes_dir / final_filename)
                raise
            except asyncio.CancelledError:
                # 超时/取消时取消已入队的 future，worker 据此跳过写入避免孤儿条目
                if not future.done():
                    future.cancel()
                raise

    async def edit_text(self, entry_id: int, new_text: str) -> EditTextResult:
        """修改指定条目的 OCR 文本。

        流程：校验 → embed(锁外) → 二次检查 refresh → put WriteRequest → await future。

        Args:
            entry_id: 要修改的索引 id。
            new_text: 新的 OCR 文本（调用方已去空白）。

        Returns:
            EditTextResult 描述修改结果。

        Raises:
            IndexAddCancelledError: Bot 正在关闭。
            RefreshInProgressError: 刷新进行中或 pending 中。
            ValueError: entry_id 不存在。
            DuplicateTextError: new_text 已被其他条目使用。
            EmbeddingError: Embedding 生成失败。
        """
        logger.info("编辑文字: entry_ids=%s", entry_id)
        # 检查①：shutting_down（最高优先级，避免浪费 embed API）
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")

        # 检查②：refresh 状态
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # 确保 Write Worker 已启动
        self._ensure_write_worker()

        # 校验 entry 存在 + 获取旧 text（用于回滚）
        entry = await asyncio.to_thread(self._metadata_store.get_entry, entry_id)
        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")
        old_text = entry.text
        if old_text == new_text:
            result = EditTextResult(
                entry_id=entry_id, old_text=old_text, new_text=new_text
            )
            logger.info("文字编辑完成: entry_ids=%s", entry_id)
            return result

        # 锁外生成新 embedding
        assert self._embedding_provider is not None
        new_embedding = await self._embedding_provider.embed(new_text)

        # 检查③：TOCTOU 防护（embed 期间 shutting_down 或 refresh 可能已激活）
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # 提交写入任务
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.EDIT_TEXT,
            future=future,
            entry_id=entry_id,
            text=new_text,
            embedding=new_embedding,
            old_text=old_text,
        )
        await self._write_queue.put(req)
        result = await future
        logger.info("文字编辑完成: entry_ids=%s", entry_id)
        return result

    async def set_speaker(self, entry_id: int, speaker: str | None) -> SetSpeakerResult:
        """设置或清空指定条目的 speaker。

        流程：校验 → 读 entry → 无变更直接返回 → put WriteRequest → await future。

        Args:
            entry_id: 要修改的索引 id。
            speaker: 新说话人值；None 表示清空。

        Returns:
            SetSpeakerResult 描述修改结果。

        Raises:
            IndexAddCancelledError: Bot 正在关闭。
            RefreshInProgressError: 刷新进行中或 pending 中。
            ValueError: entry_id 不存在。
        """
        logger.info("设置发言人: entry_ids=%s, speaker=%r", entry_id, speaker)
        # 检查①：shutting_down
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")

        # 检查②：refresh 状态
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # 确保 Write Worker 已启动
        self._ensure_write_worker()

        # 校验 entry 存在 + 获取 old_speaker
        entry = await asyncio.to_thread(self._metadata_store.get_entry, entry_id)

        # TOCTOU 防护（get_entry 期间 shutting_down 或 refresh 可能已激活）
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")
        old_speaker = entry.speaker
        if old_speaker == speaker:
            result = SetSpeakerResult(
                entry_id=entry_id,
                old_speaker=old_speaker,
                new_speaker=speaker,
            )
            logger.info("发言人设置完成: entry_ids=%s", entry_id)
            return result

        # 提交写入任务（不需要 embed，直接入队）
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[SetSpeakerResult]" = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.SET_SPEAKER,
            future=future,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            entry_id=entry_id,
            speaker=speaker,
        )
        await self._write_queue.put(req)
        result = await future
        logger.info("发言人设置完成: entry_ids=%s", entry_id)
        return result

    async def add_tags(self, entry_id: int, tags: list[str]) -> AddTagResult:
        """为指定条目追加标签。

        流程：校验 → put WriteRequest → await future。

        Args:
            entry_id: 要修改的索引 id。
            tags: 要追加的标签列表。

        Returns:
            AddTagResult 描述添加结果。

        Raises:
            IndexAddCancelledError: Bot 正在关闭。
            RefreshInProgressError: 刷新进行中或 pending 中。
            ValueError: entry_id 不存在。
        """
        logger.info("添加标签: entry_ids=%s, tags=%r", entry_id, tags)
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        self._ensure_write_worker()

        entry = await asyncio.to_thread(self._metadata_store.get_entry, entry_id)
        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")

        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        loop = asyncio.get_running_loop()
        future: "asyncio.Future[AddTagResult]" = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.ADD_TAG,
            future=future,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            entry_id=entry_id,
            tags=list(tags),
        )
        await self._write_queue.put(req)
        result = await future
        logger.info("标签添加完成: entry_ids=%s", entry_id)
        return result

    async def delete(self, entry_ids: list[int]) -> DeleteResult:
        """删除一个或多个表情包条目。

        流程：校验 → put WriteRequest → await future。

        Args:
            entry_ids: 要删除的索引 id 列表。

        Returns:
            DeleteResult 描述删除结果。

        Raises:
            IndexAddCancelledError: Bot 正在关闭。
            RefreshInProgressError: 刷新进行中或 pending 中。
        """
        logger.info("删除图片: %s", entry_ids)
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        self._ensure_write_worker()

        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        loop = asyncio.get_running_loop()
        future: "asyncio.Future[DeleteResult]" = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.DELETE,
            future=future,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            entry_ids=list(entry_ids),
        )
        await self._write_queue.put(req)
        result = await future
        logger.info("图片删除完成: %s", entry_ids)
        return result

    def _preview_move_locked(
        self,
        entry: MemeEntry,
        target_collection_id: int,
        *,
        include_source_snapshot: bool = False,
    ) -> MovePreview:
        """在已持有索引锁时构造移动预览。

        Args:
            entry: 待移动的源表情包条目。
            target_collection_id: 目标合集编号，0 表示全局根目录。
            include_source_snapshot: 是否采集源文件身份快照用于后续校验。

        Returns:
            包含新旧公开 ID 与目标合集信息的移动预览。

        Raises:
            ValueError: 表情包已属于目标合集。
            MemeMoveSourceExpiredError: 采集源快照时发现源文件已变化。
        """
        target_name = self._resolve_move_target_name(target_collection_id)
        if entry.collection_id == target_collection_id:
            raise ValueError("表情包已属于目标合集")
        local_id = self._metadata_store.find_next_local_id(target_collection_id)
        source_snapshot = None
        if include_source_snapshot:
            try:
                file_identity = get_regular_file_identity(
                    self._memes_dir,
                    Path(entry.image_path),
                )
            except SecureMoveError as exc:
                raise MemeMoveSourceExpiredError("源表情包文件已变化") from exc
            source_snapshot = MoveSourceSnapshot(entry, file_identity)
        return MovePreview(
            entry_id=entry.id,
            old_public_id=entry.public_id,
            source_collection_name=entry.collection_name,
            target_collection_id=target_collection_id,
            target_collection_name=target_name,
            expected_public_id=MemePublicId(target_collection_id, local_id),
            source_snapshot=source_snapshot,
        )

    async def prepare_move(
        self,
        scope: "ScopeLike",
        source_raw: str,
        target_raw: str,
    ) -> MovePreview:
        """在同一读锁内解析源、目标并生成带源身份的移动预览。

        Args:
            scope: 当前聊天作用域。
            source_raw: 源完整公开 ID 或当前普通合集短号。
            target_raw: 目标合集编号或精确名称。

        Returns:
            携带源条目及文件身份快照的移动预览。

        Raises:
            InvalidPublicIdError: 源公开 ID 无效。
            MemeNotFoundError: 源表情包不存在。
            CollectionNotFoundError: 目标合集不存在。
            ValueError: 源已属于目标合集。
            MemeMoveError: 源文件身份无法安全读取。
            asyncio.TimeoutError: 等待读锁超时。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            public_id = self._collection_manager.parse_meme_id(
                source_raw,
                selected_collection_id=selection.collection_id,
            )
            entry = await asyncio.to_thread(
                self._metadata_store.get_entry_by_public_id,
                public_id,
            )
            if entry is None:
                from .collection_manager import MemeNotFoundError

                raise MemeNotFoundError(str(public_id))
            target = await asyncio.to_thread(
                self._collection_manager.resolve_selection,
                target_raw,
            )
            return await asyncio.to_thread(
                self._preview_move_locked,
                entry,
                target.collection_id,
                include_source_snapshot=True,
            )

    async def preview_move(
        self,
        entry_id: int,
        target_collection_id: int,
    ) -> MovePreview:
        """预览跨合集移动，不预留目标局部编号。

        Args:
            entry_id: 源条目的内部 ID。
            target_collection_id: 目标合集编号，0 表示全局根目录。

        Returns:
            包含当前公开 ID 与预计新公开 ID 的只读快照。

        Raises:
            ValueError: 源条目不存在或已属于目标合集。
            CollectionNotFoundError: 目标普通合集不存在。
            asyncio.TimeoutError: 等待读锁超时。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            entry = await asyncio.to_thread(self._metadata_store.get_entry, entry_id)
            if entry is None:
                raise ValueError(f"entry_id={entry_id} 不存在")
            return await asyncio.to_thread(
                self._preview_move_locked,
                entry,
                target_collection_id,
            )

    async def move(
        self,
        entry_id: int,
        target_collection_id: int,
        *,
        expected_source: MoveSourceSnapshot | None = None,
        expected_target_name: str | None = None,
    ) -> MoveResult:
        """提交跨合集移动并等待补偿式事务完成。

        调用者取消等待时，已开始的事务仍会在 Write Worker 中完成或补偿，随后再向
        调用者传播取消，避免留下半移动状态。

        Args:
            entry_id: 源条目的内部 ID。
            target_collection_id: 目标合集编号，0 表示全局根目录。
            expected_source: 确认前捕获的完整源条目与文件身份快照。
            expected_target_name: 确认前捕获的目标合集名称；不一致时拒绝执行。

        Returns:
            从持久条目读取的实际移动结果。

        Raises:
            IndexAddCancelledError: Bot 正在关闭或 Write Worker 被关闭。
            RefreshInProgressError: 刷新正在执行。
            ValueError: 源条目不存在或已属于目标合集。
            CollectionNotFoundError: 目标普通合集不存在。
            DuplicateMemeInCollectionError: 目标合集存在相同文本。
            MemeMoveError: 文件或跨存储写入失败。
        """
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")
        self._ensure_write_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[MoveResult] = loop.create_future()
        transaction_started = asyncio.Event()
        await self._write_queue.put(
            _WriteRequest(
                op=WriteOp.MOVE,
                future=future,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
                entry_id=entry_id,
                target_collection_id=target_collection_id,
                expected_source=expected_source,
                expected_target_name=expected_target_name,
                transaction_started=transaction_started,
            )
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if not transaction_started.is_set():
                future.cancel()
                raise
            waiter = asyncio.create_task(self._await_move_future(future))
            _, error, _ = await self._wait_task_through_cancellation(waiter)
            if error is not None and not isinstance(error, asyncio.CancelledError):
                logger.error(
                    "MOVE 调用者取消后事务执行失败: entry_id=%s, error=%s",
                    entry_id,
                    error,
                )
            raise

    @staticmethod
    async def _await_move_future(future: asyncio.Future[MoveResult]) -> MoveResult:
        """等待移动结果，供可靠取消等待 helper 托管。

        Args:
            future: Write Worker 的移动结果 Future。

        Returns:
            移动成功结果。
        """
        return await asyncio.shield(future)

    async def info(self, collection_id: int | None = None) -> IndexInfo:
        """返回当前索引内部统计信息（不含硬件）。

        Args:
            collection_id: 统计范围；None 表示全库。

        Returns:
            IndexInfo 描述当前统计与状态。
        """
        entries = await asyncio.to_thread(
            self._metadata_store.get_entries, collection_id
        )
        entry_count = await asyncio.to_thread(self._metadata_store.entry_count)

        speaker_counts: dict[str | None, int] = {}
        for entry in entries.values():
            speaker_counts[entry.speaker] = speaker_counts.get(entry.speaker, 0) + 1

        speaker_ranking = sorted(
            speaker_counts.items(),
            key=lambda item: (-item[1], item[0] or ""),
        )[:10]

        collection_count = len(
            getattr(self._metadata_store, "list_collections", lambda: [])()
        )

        if self._refresh_active:
            status = "正在刷新索引"
        else:
            status = "空闲"

        return IndexInfo(
            entry_count=entry_count,
            current_entry_count=len(entries),
            collection_count=collection_count,
            speaker_ranking=speaker_ranking,
            status=status,
        )

    async def get_entry(self, entry_id: int) -> MemeEntry | None:
        """按 id 查询单条表情包元数据。

        持读锁调用 MetadataStore，保证与刷新期间的写入互斥，读取视图一致。

        Args:
            entry_id: 索引 id。

        Returns:
            对应 MemeEntry；id 不存在时返回 None。

        Raises:
            asyncio.TimeoutError: 等待读锁超时（刷新长时间占用写锁）。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return await asyncio.to_thread(self._metadata_store.get_entry, entry_id)

    async def get_selected_collection(
        self, scope: "ScopeLike"
    ) -> "CollectionSelection":
        """返回聊天作用域当前选择。

        Args:
            scope: 聊天作用域。

        Returns:
            当前有效的合集选择。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return await asyncio.to_thread(self._collection_manager.get_selected, scope)

    def _validate_collection_selection_locked(
        self,
        scope: "ScopeLike",
        expected_selection: CollectionSelection,
    ) -> None:
        """在已持有索引锁时校验 ChatScope 完整选择快照。

        Args:
            scope: 聊天作用域。
            expected_selection: 交互开始时捕获的完整选择快照。

        Raises:
            CollectionSelectionExpiredError: 当前选择已与快照不同。
        """
        current = self._collection_manager.get_selected(scope)
        if current != expected_selection:
            raise CollectionSelectionExpiredError("当前合集选择已变化")

    async def validate_collection_selection(
        self,
        scope: "ScopeLike",
        expected_selection: CollectionSelection,
    ) -> None:
        """校验聊天作用域仍保持指定完整合集选择。

        Args:
            scope: 聊天作用域。
            expected_selection: 交互开始时捕获的完整选择快照。

        Raises:
            CollectionSelectionExpiredError: 当前选择与快照不同。
            asyncio.TimeoutError: 等待读锁超时。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            await asyncio.to_thread(
                self._validate_collection_selection_locked,
                scope,
                expected_selection,
            )

    async def list_collections(self, scope: "ScopeLike") -> "list[CollectionSummary]":
        """返回全部合集入口和各普通合集的统计摘要。

        Args:
            scope: 用于标记当前选择的聊天作用域。

        Returns:
            首项为全部合集、其后为普通合集的统计摘要。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            return await asyncio.to_thread(
                self._collection_manager.list_summaries, scope
            )

    async def switch_collection(
        self, scope: "ScopeLike", target: str
    ) -> "CollectionSelection":
        """解析并切换聊天作用域的当前合集。

        Args:
            scope: 聊天作用域。
            target: 合集编号或名称。

        Returns:
            切换后的合集选择。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            CollectionNotFoundError: 目标合集不存在。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.resolve_selection, target
            )
            await asyncio.to_thread(
                self._collection_manager.set_selected,
                scope,
                selection.collection_id,
            )
            return selection

    async def resolve_entry(self, scope: "ScopeLike", raw_id: str) -> MemeEntry:
        """按当前合集的公开 ID 规则解析并查询条目。

        Args:
            scope: 聊天作用域，用于获取当前合集。
            raw_id: 用户输入的完整 ID 或当前普通合集的短号。

        Returns:
            匹配的表情包条目。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            InvalidPublicIdError: ID 格式无效或全部合集模式下使用短号。
            MemeNotFoundError: 条目不存在。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            selection = await asyncio.to_thread(
                self._collection_manager.get_selected, scope
            )
            public_id = self._collection_manager.parse_meme_id(
                raw_id,
                selected_collection_id=selection.collection_id,
            )
            entry = await asyncio.to_thread(
                self._metadata_store.get_entry_by_public_id, public_id
            )
            if entry is None:
                from .collection_manager import MemeNotFoundError

                raise MemeNotFoundError(str(public_id))
            return entry

    @timed(logger, "索引刷新")
    async def refresh(self) -> SyncResult:
        """独占执行索引同步（refresh）。

        Returns:
            SyncResult 描述同步结果。

        Raises:
            RefreshInProgressError: 已有刷新任务在运行或 Bot 正在关闭。
        """
        logger.info("开始刷新索引...")
        if self._shutting_down:
            raise RefreshInProgressError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("已有刷新任务在运行")

        self._refresh_active = True
        self._refresh_task = asyncio.current_task()
        try:
            if not self._write_queue.empty():
                self._write_drained.clear()
                await self._write_drained.wait()

            async with self._rwlock.write():
                result = await self._run_sync_internal()
                logger.info(
                    "索引刷新完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
                    result.added,
                    result.deleted,
                    result.deduped,
                    result.no_text_moved,
                    len(result.failed),
                )
                return result
        finally:
            self._refresh_active = False
            if self._refresh_task is asyncio.current_task():
                self._refresh_task = None

    def _ensure_write_worker(self) -> None:
        """确保 Write Worker task 已启动（延迟启动）。"""
        if self._write_worker_task is None or self._write_worker_task.done():
            self._write_worker_task = asyncio.create_task(self._write_worker_loop())

    async def _write_worker_loop(self) -> None:
        """串行处理所有写入任务（写锁保护）。"""
        while True:
            try:
                req = await self._write_queue.get()
            except asyncio.CancelledError:
                # 取消所有 pending future
                while not self._write_queue.empty():
                    try:
                        pending = self._write_queue.get_nowait()
                        if not pending.future.done():
                            pending.future.set_exception(
                                IndexAddCancelledError("写入工作线程已停止")
                            )
                    except asyncio.QueueEmpty:
                        break
                raise

            if req.future.done():
                # 已被取消/放弃的请求，跳过不写，避免孤儿写入
                if self._write_queue.empty():
                    self._write_drained.set()
                continue

            try:
                async with self._rwlock.write():
                    try:
                        if req.op is WriteOp.ADD:
                            if req.embedding is None:
                                raise ValueError("req 中的 embedding 为 None")
                            if req.expected_selection is not None:
                                if req.scope is None:
                                    raise ValueError("ADD 请求缺少 scope")
                                self._validate_collection_selection_locked(
                                    req.scope,
                                    req.expected_selection,
                                )
                            result = await self._write_entry(
                                req.filename,
                                req.text,
                                req.embedding,
                                req.speaker,
                                req.tags,
                                collection_id=req.collection_id,
                            )
                        elif req.op is WriteOp.EDIT_TEXT:
                            result = await self._execute_edit_text(req)
                        elif req.op is WriteOp.SET_SPEAKER:
                            result = await self._execute_set_speaker(req)
                        elif req.op is WriteOp.ADD_TAG:
                            result = await self._execute_add_tags(req)
                        elif req.op is WriteOp.DELETE:
                            result = await self._execute_delete(req)
                        elif req.op is WriteOp.MOVE:
                            if req.future.done():
                                continue
                            if req.transaction_started is None:
                                raise ValueError("MOVE 请求缺少 transaction_started")
                            req.transaction_started.set()
                            move_task = asyncio.create_task(self._execute_move(req))
                            (
                                result,
                                move_error,
                                cancelled,
                            ) = await self._wait_task_through_cancellation(move_task)
                            if move_error is not None:
                                raise move_error
                            if cancelled:
                                raise asyncio.CancelledError
                            assert result is not None
                        else:
                            raise ValueError(f"未知写入操作: {req.op}")

                        if not req.future.done():
                            req.future.set_result(result)
                    except asyncio.CancelledError:
                        if not req.future.done():
                            req.future.set_exception(
                                IndexAddCancelledError("写入工作线程被取消")
                            )
                        raise
                    except Exception as exc:
                        if not req.future.done():
                            req.future.set_exception(exc)
                    finally:
                        if self._write_queue.empty():
                            self._write_drained.set()
            except asyncio.CancelledError:
                if not req.future.done():
                    req.future.set_exception(
                        IndexAddCancelledError("写入工作线程被取消")
                    )
                raise

    async def _execute_edit_text(self, req: _WriteRequest) -> EditTextResult:
        """写锁内执行 edit_text 写入（先 sqlite 后 chroma，失败回滚）。

        Args:
            req: 写入任务单元。

        Returns:
            EditTextResult 描述修改结果。

        Raises:
            DuplicateTextError: new_text 已被其他条目使用。
            ValueError: entry_id 不存在。
            EmbeddingError: chroma upsert 失败，已回滚 sqlite。
        """
        entry = await asyncio.to_thread(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在")

        # 写锁内 TOCTOU 检查 text 冲突（同一合集内唯一）
        existing_id = await asyncio.to_thread(
            self._metadata_store.get_id_by_text,
            req.text,
            collection_id=entry.collection_id,
        )
        if existing_id is not None and existing_id != req.entry_id:
            raise DuplicateTextError(
                f"OCR 文本「{req.text}」已被 entry_id={existing_id} 使用",
            )

        # 先 sqlite
        success = await asyncio.to_thread(
            self._metadata_store.update,
            req.entry_id,
            text=req.text,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在")

        # 后 chroma，失败回滚 sqlite
        assert req.embedding is not None
        try:
            await self._vector_store.upsert(
                req.entry_id, req.embedding, collection_id=entry.collection_id
            )
        except Exception as exc:
            try:
                await asyncio.to_thread(
                    self._metadata_store.update,
                    req.entry_id,
                    text=req.old_text,
                )
            except Exception as rollback_exc:
                logger.error(
                    "edit_text 回滚失败: id=%s, error=%s", req.entry_id, rollback_exc
                )
            raise EmbeddingError(
                f"edit_text upsert 失败，已回滚: entry_id={req.entry_id}",
            ) from exc

        return EditTextResult(
            entry_id=req.entry_id,
            old_text=req.old_text,
            new_text=req.text,
        )

    async def _execute_set_speaker(self, req: _WriteRequest) -> SetSpeakerResult:
        """写锁内执行 set_speaker 写入（仅 sqlite update，无 chroma 操作）。

        Args:
            req: 写入任务单元。

        Returns:
            SetSpeakerResult 描述修改结果。

        Raises:
            ValueError: entry_id 在写锁内已不存在。
        """
        # TOCTOU 防护：写锁内重新检查 entry 是否存在
        entry = await asyncio.to_thread(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")
        old_speaker = entry.speaker

        # 写 sqlite
        success = await asyncio.to_thread(
            self._metadata_store.update,
            req.entry_id,
            speaker=req.speaker,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在（update 返回 False）")

        return SetSpeakerResult(
            entry_id=req.entry_id,
            old_speaker=old_speaker,
            new_speaker=req.speaker,
        )

    async def _execute_add_tags(self, req: _WriteRequest) -> AddTagResult:
        """写锁内执行 add_tags 写入（仅 sqlite，无 chroma 操作）。

        Args:
            req: 写入任务单元，包含 entry_id 与待追加标签。

        Returns:
            AddTagResult 描述添加结果。

        Raises:
            ValueError: entry_id 在写锁内已不存在。
        """
        entry = await asyncio.to_thread(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")

        current_tags = set(entry.tags)
        new_tags = set(req.tags or [])
        added_tags = list(new_tags - current_tags)
        merged_tags = list(current_tags | new_tags)

        if not added_tags:
            return AddTagResult(
                entry_id=req.entry_id,
                added_tags=[],
                all_tags=list(current_tags),
            )

        success = await asyncio.to_thread(
            self._metadata_store.update,
            req.entry_id,
            tags=merged_tags,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在（update 返回 False）")

        return AddTagResult(
            entry_id=req.entry_id,
            added_tags=added_tags,
            all_tags=merged_tags,
        )

    async def _execute_delete(self, req: _WriteRequest) -> DeleteResult:
        """写锁内执行 delete（先归档文件到 memes_deleted/，再先 sqlite 后 chroma 删索引）。

        先移文件可避免「索引已删但移图失败」导致文件残留 memes/、下次 refresh 被重新
        入库（已删表情包复活）。移图失败时索引原样保留，仅将本次 id 记为失败；文件
        本就不在 memes/ 时跳过移动直接删索引。sqlite 与 chroma 删除仍按「先 sqlite
        后 chroma」，残留孤儿向量由 refresh 阶段0 自愈。

        Args:
            req: 写入任务单元，包含待删除 entry_id 列表。

        Returns:
            DeleteResult 描述删除结果，含成功、未找到、失败三类 id。
        """
        self._deleted_dir.mkdir(parents=True, exist_ok=True)

        deleted_ids: list[int] = []
        not_found_ids: list[int] = []
        failed_ids: list[tuple[int, str]] = []

        for entry_id in req.entry_ids or []:
            entry = await asyncio.to_thread(self._metadata_store.get_entry, entry_id)
            if entry is None:
                not_found_ids.append(entry_id)
                continue

            try:
                # 先归档文件：移图失败时索引原样保留（仅标记本次失败），避免文件
                # 残留 memes/ 在下次 refresh 被重新入库（已删表情包复活）。
                src = self._memes_dir / entry.image_path
                if src.exists():
                    dst = resolve_unique_filename(
                        self._deleted_dir, Path(entry.image_path).name
                    )
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # 用 shutil.move 而非 Path.rename：memes/ 与 memes_deleted/ 在
                    # Docker 中是不同 bind mount，rename 跨设备会抛 EXDEV (errno 18)。
                    shutil.move(str(src), str(dst))

                # 文件已归档（或本就不在 memes/），再删索引：先 sqlite 后 chroma
                await asyncio.to_thread(self._metadata_store.remove, entry_id)
                await self._vector_store.remove(entry_id)

                deleted_ids.append(entry_id)
            except Exception as exc:
                logger.error("删除条目失败: id=%s, error=%s", entry_id, exc)
                failed_ids.append((entry_id, str(exc)))

        return DeleteResult(
            deleted_ids=deleted_ids,
            not_found_ids=not_found_ids,
            failed_ids=failed_ids,
        )

    def _resolve_move_target_name(self, target_collection_id: int) -> str:
        """重新校验移动目标合集并返回安全目录名。

        Args:
            target_collection_id: 目标合集编号。

        Returns:
            全局显示名称或普通合集目录名。

        Raises:
            CollectionNotFoundError: 目标普通合集不存在。
            MemeMoveError: 持久合集名称不再是安全的单层目录名。
        """
        if target_collection_id == 0:
            return GLOBAL_COLLECTION_NAME
        target = self._metadata_store.get_collection(target_collection_id)
        if target is None:
            raise CollectionNotFoundError(str(target_collection_id))
        name_path = Path(target.name)
        if (
            not target.name.strip()
            or target.name.startswith(".")
            or "\\" in target.name
            or name_path.is_absolute()
            or len(name_path.parts) != 1
            or target.name in {".", ".."}
            or "\x00" in target.name
        ):
            raise MemeMoveError("目标合集目录名不安全")
        return target.name

    def _resolve_move_paths(
        self,
        old_entry: MemeEntry,
        target_collection_id: int,
        target_name: str,
    ) -> tuple[Path, Path]:
        """解析安全 helper 使用的源路径与目标目录相对路径。

        Args:
            old_entry: 移动前条目快照。
            target_collection_id: 目标合集编号。
            target_name: 已校验的目标合集名称。

        Returns:
            规范源相对路径与目标目录相对路径。

        Raises:
            MemeMoveError: 源路径逃逸、经过符号链接或目标目录不安全。
        """
        memes_root = self._memes_dir.resolve()
        source_path = self._memes_dir / old_entry.image_path
        try:
            resolved_source = source_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MemeMoveError("源图片路径不存在或无法解析") from exc
        if source_path.is_symlink() or not resolved_source.is_relative_to(memes_root):
            raise MemeMoveError("源图片路径超出 memes/ 或经过符号链接")
        if not source_path.is_file():
            raise MemeMoveError("源图片路径必须是普通文件")
        current = source_path.parent
        while current != self._memes_dir:
            if current.is_symlink():
                raise MemeMoveError("源图片路径经过符号链接目录")
            if current == current.parent:
                raise MemeMoveError("源图片路径无法归属于 memes/")
            current = current.parent

        target_relative_dir = (
            Path(".") if target_collection_id == 0 else Path(target_name)
        )
        return Path(old_entry.image_path), target_relative_dir

    async def _execute_move(self, req: _WriteRequest) -> MoveResult:
        """在写锁内执行文件、SQLite 与 Chroma 的补偿式移动。

        任一步抛出异常（包括同步调用已产生副作用后抛错）都会按移动前 SQLite 与
        Chroma 快照及文件最终存在状态恢复，并复核最终快照。

        Args:
            req: 包含源内部 ID 与目标合集编号的写请求。

        Returns:
            从持久 SQLite 条目构造的实际移动结果。

        Raises:
            ValueError: 源条目不存在或已属于目标合集。
            CollectionNotFoundError: 目标普通合集不存在。
            DuplicateMemeInCollectionError: 目标合集存在相同文本。
            MemeMoveError: 移动或补偿失败。
        """
        old_entry = await asyncio.to_thread(
            self._metadata_store.get_entry,
            req.entry_id,
        )
        if old_entry is None:
            if req.expected_source is not None:
                raise MemeMoveSourceExpiredError("源表情包已不存在")
            raise ValueError(f"entry_id={req.entry_id} 不存在")
        if req.expected_source is not None and old_entry != req.expected_source.entry:
            raise MemeMoveSourceExpiredError("源表情包元数据已变化")
        source_relative_path = Path(old_entry.image_path)
        try:
            source_identity = await asyncio.to_thread(
                get_regular_file_identity,
                self._memes_dir,
                source_relative_path,
            )
        except SecureMoveError as exc:
            if req.expected_source is not None:
                raise MemeMoveSourceExpiredError("源表情包文件已变化") from exc
            raise MemeMoveError(str(exc)) from exc
        if (
            req.expected_source is not None
            and source_identity != req.expected_source.file_identity
        ):
            raise MemeMoveSourceExpiredError("源表情包文件身份已变化")
        target_name = await asyncio.to_thread(
            self._resolve_move_target_name,
            req.target_collection_id,
        )
        if (
            req.expected_target_name is not None
            and target_name != req.expected_target_name
        ):
            raise CollectionNotFoundError(req.expected_target_name)
        if old_entry.collection_id == req.target_collection_id:
            raise ValueError("表情包已属于目标合集")
        conflict_id = await asyncio.to_thread(
            self._metadata_store.get_id_by_text,
            old_entry.text,
            collection_id=req.target_collection_id,
        )
        if conflict_id is not None:
            raise DuplicateMemeInCollectionError(conflict_id)
        local_id = await asyncio.to_thread(
            self._metadata_store.find_next_local_id,
            req.target_collection_id,
        )
        source_relative_path, target_relative_dir = await asyncio.to_thread(
            self._resolve_move_paths,
            old_entry,
            req.target_collection_id,
            target_name,
        )
        vector_store = self._vector_store
        old_vector_records = await vector_store.snapshot_records([old_entry.id])
        move_state: SecureMoveResult | SecureMoveError | None = None

        try:
            move_state = await asyncio.to_thread(
                secure_move_file,
                self._memes_dir,
                source_relative_path,
                target_relative_dir,
                first_suffix=1,
                expected_source_identity=source_identity,
            )
            new_relative_path = move_state.relative_path.as_posix()
            updated = await asyncio.to_thread(
                self._metadata_store.update,
                old_entry.id,
                image_path=new_relative_path,
                collection_id=req.target_collection_id,
                local_id=local_id,
            )
            if not updated:
                raise ValueError(f"entry_id={old_entry.id} 不存在")
            await vector_store.update_collection_id(
                old_entry.id,
                req.target_collection_id,
            )
            persisted_entry = await asyncio.to_thread(
                self._metadata_store.get_entry,
                old_entry.id,
            )
            if persisted_entry is None:
                raise RuntimeError("移动完成后无法读取持久条目")
        except BaseException as exc:
            if isinstance(exc, SecureMoveError):
                move_state = exc
            compensation_errors = await self._compensate_move(
                old_entry,
                old_vector_records,
                move_state,
            )
            if compensation_errors:
                logger.critical(
                    "移动补偿失败: entry_id=%s, errors=%s",
                    old_entry.id,
                    compensation_errors,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            raise MemeMoveError(str(exc)) from exc

        return MoveResult(
            entry_id=persisted_entry.id,
            old_public_id=old_entry.public_id,
            new_public_id=persisted_entry.public_id,
            target_collection_name=persisted_entry.collection_name,
            old_image_path=old_entry.image_path,
            new_image_path=persisted_entry.image_path,
        )

    async def _compensate_move(
        self,
        old_entry: MemeEntry,
        old_vector_records: list[VectorRecord],
        move_state: SecureMoveResult | SecureMoveError | None,
    ) -> list[str]:
        """恢复移动前的 SQLite、文件与完整 Chroma 快照。

        Args:
            old_entry: 移动前 SQLite 条目快照。
            old_vector_records: 移动前完整向量快照。
            move_state: 安全文件 helper 返回或抛出的最终状态。

        Returns:
            补偿或最终复核失败信息；空列表表示完整恢复。
        """
        errors: list[str] = []
        try:
            await asyncio.to_thread(
                self._metadata_store.update,
                old_entry.id,
                image_path=old_entry.image_path,
                collection_id=old_entry.collection_id,
                local_id=old_entry.local_id,
            )
        except BaseException as exc:
            errors.append(f"SQLite 恢复调用失败: {exc}")

        try:
            await asyncio.to_thread(
                self._restore_move_file,
                old_entry,
                move_state,
            )
        except BaseException as exc:
            errors.append(f"文件恢复失败: {exc}")

        vector_store = self._vector_store
        try:
            await vector_store.restore_records(old_vector_records)
        except BaseException as exc:
            errors.append(f"Chroma 恢复调用失败: {exc}")

        try:
            persisted = await asyncio.to_thread(
                self._metadata_store.get_entry,
                old_entry.id,
            )
            if persisted != old_entry:
                errors.append(f"SQLite 最终快照不一致: {persisted!r}")
        except BaseException as exc:
            errors.append(f"SQLite 最终快照检查失败: {exc}")
        try:
            source_path = self._memes_dir / old_entry.image_path
            if not source_path.is_file() or source_path.is_symlink():
                errors.append("源文件最终状态不一致")
            if move_state is not None and move_state.relative_path is not None:
                target_path = self._memes_dir / move_state.relative_path
                if target_path.exists() or target_path.is_symlink():
                    errors.append("目标文件最终仍存在")
        except BaseException as exc:
            errors.append(f"文件最终快照检查失败: {exc}")
        try:
            restored_records = await vector_store.snapshot_records([old_entry.id])
            if restored_records != old_vector_records:
                errors.append("Chroma 最终快照不一致")
        except BaseException as exc:
            errors.append(f"Chroma 最终快照检查失败: {exc}")
        return errors

    def _restore_move_file(
        self,
        old_entry: MemeEntry,
        move_state: SecureMoveResult | SecureMoveError | None,
    ) -> None:
        """使用同一安全 helper 按最终状态恢复源文件。

        Args:
            old_entry: 移动前条目快照。
            move_state: 安全移动 helper 的最终状态。

        Raises:
            OSError: 无法恢复源文件或清理目标目录。
        """
        if move_state is None or move_state.relative_path is None:
            return
        source_path = self._memes_dir / old_entry.image_path
        if move_state.source_removed:
            source_parent = Path(old_entry.image_path).parent
            restore_result = secure_move_file(
                self._memes_dir,
                move_state.relative_path,
                source_parent,
                target_filename=Path(old_entry.image_path).name,
                expected_source_identity=move_state.target_identity,
            )
            if restore_result.relative_path != Path(old_entry.image_path):
                raise OSError("源文件未恢复到原目录项")
        if not source_path.is_file() or source_path.is_symlink():
            raise OSError("源文件恢复后不是普通文件")

    def _move_to_replaced(self, filename: str) -> str:
        """将被替换的文件移动到 memes_replaced/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._replaced_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._replaced_dir, Path(filename).name)
        shutil.move(str(src), str(dst))
        logger.info("已归档被替换文件: %s -> %s", filename, dst)
        return str(dst)

    async def close(self) -> None:
        """安全关闭 IndexManager。

        1. 设置 shutting_down，拒绝新的 add/refresh。
        2. 取消正在运行的 Write Worker 与 refresh task。
        3. 等待它们实际结束。
        4. 关闭 MetadataStore 和 VectorStore。
        """
        self._shutting_down = True

        tasks_to_wait: list[asyncio.Task] = []

        if self._write_worker_task is not None and not self._write_worker_task.done():
            self._write_worker_task.cancel()
            tasks_to_wait.append(self._write_worker_task)

        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            tasks_to_wait.append(self._refresh_task)

        if tasks_to_wait:
            await asyncio.gather(*tasks_to_wait, return_exceptions=True)

        try:
            self._metadata_store.close()
        finally:
            self._vector_store.close()

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------

    async def _run_sync_internal(self) -> SyncResult:
        """按结构化文件系统快照同步图片索引与合集。

        顺序为扫描、一致性修复、删除条目、删除消失合集、登记新合集、添加条目。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []
        snapshot = self._scan_meme_files()

        await self._sync_phase0_consistency(failed)
        deleted_count = await self._sync_phase1_delete(set(snapshot.files), failed)
        collections_deleted, scopes_reset = await self._sync_collections_delete(
            snapshot.directories
        )
        collections_added = await self._sync_collections_add(
            snapshot.directories_with_images
        )
        added_count, deduped_count, no_text_count = await self._sync_phase2_add(
            snapshot.files, failed
        )

        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            collections_added=collections_added,
            collections_deleted=collections_deleted,
            scopes_reset=scopes_reset,
            failed=failed,
        )

    @timed(logger, "索引刷新-阶段0")
    async def _sync_phase0_consistency(self, failed: list[str]) -> None:
        """阶段0：对齐 sqlite ↔ chroma 的 id 集合。

        - chroma 为空且 sqlite 有数据 → 全量重 embed 后 rebuild_all。
        - sqlite 有、chroma 无的 id → 逐条重 embed 并 upsert。
        - chroma 有、sqlite 无的 id → 删孤儿向量。

        Args:
            failed: 失败文件名收集列表，阶段0 重 embed 失败的 image_path 追加至此。
        """
        entries = await asyncio.to_thread(self._metadata_store.get_all_entries)
        sqlite_ids = set(entries)
        vs_count = self._vector_store.count()
        chroma_ids = await self._get_chroma_ids()

        # chroma 损坏/为空、sqlite 有数据 → rebuild_all 全量重 embed
        if vs_count == 0 and sqlite_ids:
            await self._rebuild_all_from_sqlite(entries, failed)
            return

        # sqlite 有、chroma 无 → 重 embed upsert
        missing = sqlite_ids - chroma_ids
        restored: set[int] = set()
        for idx, eid in enumerate(missing):
            text = entries[eid].text
            if not text:
                continue
            try:
                vec = await self._embedding_provider.embed(text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
            except Exception as exc:
                logger.error("阶段0 重 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            try:
                await self._vector_store.upsert(
                    eid, vec, collection_id=entries[eid].collection_id
                )
            except Exception as exc:
                logger.error("阶段0 补写向量失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            restored.add(eid)
            logger.debug("已处理 %d/%d 张图片", idx + 1, len(missing))

        # chroma 有、sqlite 无 → 删孤儿向量
        orphans = chroma_ids - sqlite_ids
        if orphans:
            logger.info("阶段0 清理孤儿向量: %s", orphans)
            await self._vector_store.remove_many(list(orphans))

        chroma_ids = (chroma_ids | restored) - orphans
        vector_store = self._vector_store
        vector_collections = await vector_store.get_collection_ids()
        for entry_id, entry in entries.items():
            if entry_id in chroma_ids and (
                vector_collections.get(entry_id) != entry.collection_id
            ):
                await vector_store.update_collection_id(entry_id, entry.collection_id)

    async def _get_chroma_ids(self) -> set[int]:
        """调用 VectorStore.get_all_ids() 获取全部向量 ID，与 embedding 维度无关。

        Returns:
            chroma 中现存向量对应的 entry_id 集合；chroma 为空时返回空集。
        """
        return await self._vector_store.get_all_ids()

    async def _rebuild_all_from_sqlite(
        self, entries: dict[int, MemeEntry], failed: list[str]
    ) -> None:
        """chroma 为空、sqlite 有数据 → 全量重 embed 后 rebuild_all。

        Args:
            entries: sqlite 当前全量条目（id → MemeEntry）。
            failed: 失败文件名收集列表，重 embed 失败的 image_path 追加至此。
        """
        items: list[tuple[int, list[float], int]] = []
        for idx, (eid, entry) in enumerate(entries.items()):
            if not entry.text:
                continue
            try:
                vec = await self._embedding_provider.embed(entry.text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
            except Exception as exc:
                logger.error("阶段0 全量重建 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entry.image_path)
                continue
            items.append((eid, vec, entry.collection_id))
            logger.debug("已处理 %d/%d 张图片", idx + 1, len(entries))
        await self._vector_store.rebuild_all(items)

    @timed(logger, "索引刷新-阶段1")
    async def _sync_phase1_delete(self, existing: set[str], failed: list[str]) -> int:
        """阶段1：删除缺失图片索引，向量删除失败时恢复 SQLite 条目。

        Args:
            existing: sync 开始时扫描的 POSIX 相对路径集合。
            failed: 失败图片路径收集列表。

        Returns:
            两个存储均删除成功的图片数量。
        """
        entries = await asyncio.to_thread(self._metadata_store.get_all_entries)
        metadata_store = self._metadata_store
        deleted = 0
        for eid, entry in entries.items():
            if entry.image_path in existing:
                continue
            logger.info(
                "图片已删除，移除索引: id=%s, image_path=%s",
                eid,
                entry.image_path,
            )
            await asyncio.to_thread(self._metadata_store.remove, eid)
            try:
                await self._vector_store.remove(eid)
            except Exception as exc:
                logger.error(
                    "删除向量调用异常，确认最终状态: id=%s, image_path=%s, error=%s",
                    eid,
                    entry.image_path,
                    exc,
                )
                try:
                    vector_ids = await self._vector_store.get_all_ids()
                except Exception as state_exc:
                    logger.critical(
                        "删除向量异常且无法确认最终状态: id=%s, error=%s",
                        eid,
                        state_exc,
                    )
                    raise
                if eid not in vector_ids:
                    logger.warning("删除向量调用异常但删除已生效: id=%s", eid)
                    deleted += 1
                    continue
                try:
                    await asyncio.to_thread(
                        metadata_store.add_with_id,
                        entry.id,
                        entry.image_path,
                        entry.text,
                        entry.speaker,
                        entry.tags,
                        collection_id=entry.collection_id,
                        local_id=entry.local_id,
                    )
                except Exception as rollback_exc:
                    logger.critical(
                        "删除向量失败且 sqlite 恢复失败: id=%s, error=%s",
                        eid,
                        rollback_exc,
                    )
                    raise
                failed.append(entry.image_path)
                continue
            deleted += 1
        return deleted

    async def _sync_collections_delete(
        self, existing_directories: set[str]
    ) -> tuple[int, int]:
        """删除一级目录已消失的空合集，并回退引用它的聊天窗口。

        Args:
            existing_directories: 当前实际存在的非隐藏一级目录名称。

        Returns:
            删除合集数与回退窗口数。
        """
        deleted = reset = 0
        metadata_store = self._metadata_store
        for collection in metadata_store.list_collections():
            if collection.name in existing_directories:
                continue
            reset += await asyncio.to_thread(
                metadata_store.delete_collection_and_reset_scopes,
                collection.id,
            )
            deleted += 1
        return deleted, reset

    async def _sync_collections_add(self, directories_with_images: set[str]) -> int:
        """登记递归包含受支持图片的新一级目录。

        Args:
            directories_with_images: 含图片的一级目录名称。

        Returns:
            本次新登记的合集数量。
        """
        added = 0
        metadata_store = self._metadata_store
        for name in sorted(directories_with_images):
            if metadata_store.get_collection_by_name(name) is not None:
                continue
            await asyncio.to_thread(metadata_store.create_collection, name)
            added += 1
        return added

    @timed(logger, "索引刷新-阶段2")
    async def _sync_phase2_add(
        self, existing: dict[str, str | None], failed: list[str]
    ) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR→embed，按合集串行分类与写入。

        Args:
            existing: POSIX 相对路径到一级合集目录名的映射；根目录图片值为 None。
            failed: 失败文件名收集列表，处理异常或 upsert 失败回滚的文件名追加至此。

        Returns:
            (added, deduped, no_text_moved) 三元组：新增、去重删除、无文字移走数量。
        """
        entries = await asyncio.to_thread(self._metadata_store.get_all_entries)
        metadata_store = self._metadata_store
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
        if not new_files:
            return (0, 0, 0)

        logger.info("开始并行处理 %d 张新增图片", len(new_files))

        raw = await asyncio.gather(
            *(self._process_image_pipeline(filename) for filename in new_files),
            return_exceptions=True,
        )

        success: dict[str, tuple[str, list[float], int]] = {}
        for idx, (filename, result) in enumerate(zip(new_files, raw)):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
                continue
            final_filename, text, embedding = result
            collection_name = existing[filename]
            if collection_name is None:
                collection_id = 0
            else:
                collection = metadata_store.get_collection_by_name(collection_name)
                if collection is None:
                    logger.error("新增图片所属合集未登记: filename=%s", filename)
                    failed.append(filename)
                    continue
                collection_id = collection.id
            # 并发同名去重：多张新增图转 webp 后可能产出同名 final_filename
            # （_convert_image_to_webp 并行 resolve 的 TOCTOU 竞态兜底）。
            # 基于 success dict 已有 key 去重，不依赖文件存在性。
            if final_filename in success:
                final_path = Path(final_filename)
                parent = final_path.parent
                stem = final_path.stem
                suffix = final_path.suffix
                old_path = self._memes_dir / final_path
                n = 1
                candidate = parent / f"{stem}_{n}{suffix}"
                while (
                    candidate.as_posix() in success
                    or (self._memes_dir / candidate).exists()
                ):
                    n += 1
                    candidate = parent / f"{stem}_{n}{suffix}"
                final_filename = candidate.as_posix()
                new_path = self._memes_dir / candidate
                if old_path.exists():
                    shutil.move(str(old_path), str(new_path))
                logger.info("并发同名去重 rename: %s", final_filename)
            success[final_filename] = (text, embedding, collection_id)
            logger.debug("已处理 %d/%d 张图片", idx + 1, len(new_files))

        winner_keys: set[tuple[int, str]] = {
            (entry.collection_id, entry.text)
            for entry in entries.values()
            if entry.text
        }

        added = deduped = no_text_moved = 0
        for filename in sorted(success):
            text, embedding, collection_id = success[filename]
            if not text:
                await asyncio.to_thread(self._move_to_no_text, filename)
                no_text_moved += 1
                continue
            dedupe_key = (collection_id, text)
            if dedupe_key in winner_keys:
                try:
                    archived_path = await asyncio.to_thread(
                        self._move_to_replaced, filename
                    )
                except Exception as exc:
                    logger.error(
                        "去重新图归档失败，跳过该文件: filename=%s, error=%s",
                        filename,
                        exc,
                    )
                    failed.append(filename)
                    continue
                logger.info(
                    "新图与已有索引去重，已归档新图: filename=%s, archived=%s",
                    filename,
                    archived_path,
                )
                deduped += 1
                continue
            # 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite
            eid = await asyncio.to_thread(
                self._metadata_store.add,
                filename,
                text,
                collection_id=collection_id,
            )
            try:
                await self._vector_store.upsert(
                    eid, embedding, collection_id=collection_id
                )
            except Exception as exc:
                logger.error("新增 upsert 失败，回滚 sqlite: id=%s, error=%s", eid, exc)
                await asyncio.to_thread(self._metadata_store.remove, eid)
                failed.append(filename)
                continue
            winner_keys.add(dedupe_key)
            added += 1
            logger.info("新增图片已加入索引: id=%s, filename=%s", eid, filename)

        return (added, deduped, no_text_moved)

    async def _write_entry(
        self,
        filename: str,
        text: str,
        embedding: list[float],
        speaker: str | None = None,
        tags: list[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        """三分类写入：无文字移图 / 去重替换 / 正常新增。

        写入顺序统一"先 sqlite 后 chroma"，失败可回滚。

        Args:
            filename: memes/ 下的文件名。
            text: OCR 去除所有空白后的文本（空串表示无文字）。
            embedding: 与 text 对应的 embedding 向量。
            speaker: 可选说话人。
            tags: 可选标签列表。
            collection_id: 目标合集编号，默认 0（全局）。

        Returns:
            AddResult 描述本次写入结果（added / replaced / no_text）。

        Raises:
            CollectionNotFoundError: 目标普通合集在写入前已不存在。
            EmbeddingError: 去重替换或正常新增时 upsert 失败，已回滚 sqlite 后重抛。
        """
        if collection_id != 0:
            collection = await asyncio.to_thread(
                self._metadata_store.get_collection, collection_id
            )
            if collection is None:
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise CollectionNotFoundError(str(collection_id))

        # 1. 无文字 → 移图，不进索引
        if not text:
            moved_to = await asyncio.to_thread(self._move_to_no_text, filename)
            logger.info("OCR 无文字，已移至无文字目录: filename=%s", filename)
            return AddResult(
                entry_id=None,
                reason="no_text",
                moved_to=moved_to,
                speaker=None,
                tags=[],
            )

        # 2. 去重命中已有条目 → update image_path + upsert，删旧图
        old_id = await asyncio.to_thread(
            self._metadata_store.get_id_by_text,
            text,
            collection_id=collection_id,
        )
        if old_id is not None:
            old_entry = await asyncio.to_thread(self._metadata_store.get_entry, old_id)
            old_image_path = old_entry.image_path if old_entry else ""
            old_speaker = old_entry.speaker if old_entry else None
            old_tags = old_entry.tags if old_entry else []
            old_collection_id = old_entry.collection_id if old_entry else collection_id
            vector_store = self._vector_store
            old_vector_records = await vector_store.snapshot_records([old_id])
            # 顺序：先改 sqlite 指向新图，再 upsert 向量，最后删旧图
            await asyncio.to_thread(
                self._metadata_store.update,
                old_id,
                image_path=filename,
                speaker=speaker,
                tags=tags,
            )
            try:
                await self._vector_store.upsert(
                    old_id, embedding, collection_id=old_collection_id
                )
            except Exception as exc:
                logger.error(
                    "去重替换 upsert 失败，回滚 update: id=%s, error=%s", old_id, exc
                )
                await asyncio.to_thread(
                    self._metadata_store.update,
                    old_id,
                    image_path=old_image_path,
                    speaker=old_speaker,
                    tags=old_tags,
                )
                try:
                    await vector_store.restore_records(old_vector_records)
                except Exception as rollback_exc:
                    logger.critical(
                        "去重替换向量回滚失败: id=%s, error=%s",
                        old_id,
                        rollback_exc,
                    )
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise EmbeddingError(f"去重替换 upsert 失败: {filename}") from exc
            # 归档旧图（最后移动，保证前序失败时旧图仍在）
            archived_path: str | None = None
            if old_image_path and old_image_path != filename:
                try:
                    archived_path = await asyncio.to_thread(
                        self._move_to_replaced, old_image_path
                    )
                except Exception:
                    await asyncio.to_thread(
                        self._metadata_store.update,
                        old_id,
                        image_path=old_image_path,
                        speaker=old_speaker,
                        tags=old_tags,
                    )
                    try:
                        await vector_store.restore_records(old_vector_records)
                    except Exception as rollback_exc:
                        logger.critical(
                            "去重替换归档失败且向量回滚失败: id=%s, error=%s",
                            old_id,
                            rollback_exc,
                        )
                    (self._memes_dir / filename).unlink(missing_ok=True)
                    raise
            logger.info(
                "去重替换: id=%s, 旧=%s, 新=%s, archived=%s",
                old_id,
                old_image_path,
                filename,
                archived_path,
            )
            persisted_entry = await asyncio.to_thread(
                self._metadata_store.get_entry, old_id
            )
            assert persisted_entry is not None
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                text=text,
                public_id=persisted_entry.public_id,
                collection_name=persisted_entry.collection_name,
                replaced_image_path=old_image_path,
                archived_path=archived_path,
                speaker=persisted_entry.speaker,
                tags=list(persisted_entry.tags),
            )

        # 3. 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite + 删图
        eid = await asyncio.to_thread(
            self._metadata_store.add,
            filename,
            text,
            speaker,
            tags,
            collection_id=collection_id,
        )
        persisted_entry = await asyncio.to_thread(self._metadata_store.get_entry, eid)
        assert persisted_entry is not None
        try:
            await self._vector_store.upsert(eid, embedding, collection_id=collection_id)
        except Exception as exc:
            logger.error(
                "新增 upsert 失败，回滚 sqlite + 删图: id=%s, error=%s", eid, exc
            )
            await asyncio.to_thread(self._metadata_store.remove, eid)
            (self._memes_dir / filename).unlink(missing_ok=True)
            raise EmbeddingError(f"新增 upsert 失败: {filename}") from exc
        logger.info("已添加索引记录: id=%s, filename=%s", eid, filename)
        return AddResult(
            entry_id=eid,
            reason="added",
            text=text,
            public_id=persisted_entry.public_id,
            collection_name=persisted_entry.collection_name,
            speaker=persisted_entry.speaker,
            tags=list(persisted_entry.tags),
        )

    # ------------------------------------------------------------------
    # 管道与工具
    # ------------------------------------------------------------------

    @timed(logger, "扫描 memes/ 目录")
    def _scan_meme_files(self) -> FileSystemSnapshot:
        """递归扫描 memes/，且不跟随任何符号链接。

        Returns:
            包含图片路径、一级目录和含图片目录的文件系统快照。
        """
        files: dict[str, str | None] = {}
        directories: set[str] = set()
        directories_with_images: set[str] = set()

        for entry in os.scandir(self._memes_dir):
            if entry.is_symlink():
                continue
            if (
                not entry.name.startswith(".")
                and entry.is_file()
                and Path(entry.name).suffix.lower() in self.SUPPORTED_EXTENSIONS
            ):
                files[entry.name] = None
                continue
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            directories.add(entry.name)
            for root, dir_names, file_names in os.walk(entry.path, followlinks=False):
                dir_names[:] = [
                    name
                    for name in dir_names
                    if not name.startswith(".") and not (Path(root) / name).is_symlink()
                ]
                for filename in file_names:
                    path = Path(root) / filename
                    if (
                        filename.startswith(".")
                        or path.is_symlink()
                        or path.suffix.lower() not in self.SUPPORTED_EXTENSIONS
                    ):
                        continue
                    relative_path = path.relative_to(self._memes_dir).as_posix()
                    files[relative_path] = entry.name
                    directories_with_images.add(entry.name)

        return FileSystemSnapshot(files, directories, directories_with_images)

    @asynccontextmanager
    async def _optimizer_target_lock(self, filename: str) -> AsyncIterator[None]:
        """引用计数方式持有同父目录、同 stem 图片共享的优化锁。

        waiter 在等待前计入 users，取消或完成后再递减；只有最后一个用户释放
        后才移除注册项，避免已有 waiter 与新请求取得不同锁。

        Args:
            filename: memes/ 下的 POSIX 相对路径。

        Yields:
            None；上下文期间当前任务独占目标锁。
        """
        path = self._memes_dir / filename
        key = (path.parent.as_posix().casefold(), path.stem.casefold())
        async with self._optimizer_registry_guard:
            entry = self._optimizer_target_locks.get(key)
            if entry is None:
                entry = _OptimizerLockEntry()
                self._optimizer_target_locks[key] = entry
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            release_task = asyncio.create_task(
                self._release_optimizer_lock_entry(key, entry)
            )
            _, release_error, cancelled = await self._wait_task_through_cancellation(
                release_task
            )
            if release_error is not None:
                raise release_error
            if cancelled:
                raise asyncio.CancelledError

    @staticmethod
    async def _wait_task_through_cancellation(
        task: asyncio.Task[_T],
    ) -> tuple[_T | None, BaseException | None, bool]:
        """忽略调用者重复取消并等待独立 task 真正结束。

        每次收到外部取消后调用 ``uncancel()`` 清除本次注入，使下一轮 shield
        能阻塞等待而非忙循环。独立 task 的结果或异常始终被读取；调用者是否
        曾被取消通过返回值交由上层在清理完成后显式传播。

        Args:
            task: 从开始即独立运行且不得被外部取消传播的 task。

        Returns:
            task 结果、task 异常与等待期间是否收到过外部取消。
        """
        cancelled = False
        current_task = asyncio.current_task()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                external_cancel = (
                    current_task is not None and current_task.cancelling() > 0
                )
                if external_cancel:
                    cancelled = True
                    current_task.uncancel()
                if task.done():
                    break
            except BaseException:
                break
        try:
            return task.result(), None, cancelled
        except BaseException as exc:
            return None, exc, cancelled

    async def _release_optimizer_lock_entry(
        self,
        key: tuple[str, str],
        entry: _OptimizerLockEntry,
    ) -> None:
        """可靠释放目标锁引用，并在最后一个用户离开时删除注册项。

        Args:
            key: 目标锁注册键。
            entry: 当前任务持有引用的注册项。
        """
        async with self._optimizer_registry_guard:
            entry.users -= 1
            if entry.users == 0 and self._optimizer_target_locks.get(key) is entry:
                del self._optimizer_target_locks[key]

    async def _optimize_with_cancellation(
        self, filename: str
    ) -> tuple[OptimizeResult, set[Path]]:
        """持目标锁运行 optimizer，外部取消后等待实际操作结束再传播。

        Args:
            filename: memes/ 下的 POSIX 相对路径。

        Returns:
            optimizer 结果与调用前在目标父目录内存在的路径快照。

        Raises:
            asyncio.CancelledError: 外部取消或 optimizer 自身取消。
            Exception: optimizer 调用异常。
        """
        image_path = self._memes_dir / filename
        existing_paths: set[Path] = set()
        result: OptimizeResult | None = None
        try:
            async with self._optimizer_target_lock(filename):
                existing_paths = set(image_path.parent.iterdir())
                assert self._optimizer is not None
                optimize_task = asyncio.create_task(
                    self._optimizer.optimize(str(image_path))
                )
                (
                    result,
                    optimize_error,
                    cancelled,
                ) = await self._wait_task_through_cancellation(optimize_task)
                if optimize_error is not None:
                    if cancelled:
                        logger.error(
                            "外部取消后 optimizer 仍执行失败: filename=%s, error=%s",
                            filename,
                            optimize_error,
                        )
                        raise asyncio.CancelledError
                    raise optimize_error
                assert result is not None
                if cancelled:
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            if result is not None:
                final_path = Path(result.output_path)
                if final_path not in existing_paths:
                    self._cleanup_pipeline_output(final_path)
            raise
        assert result is not None
        return result, existing_paths

    @staticmethod
    def _cleanup_pipeline_output(created_output: Path | None) -> None:
        """清理当前管线新建的输出，不让清理异常遮蔽原异常或取消。

        Args:
            created_output: 当前任务确认新建的最终输出；None 时不处理。
        """
        if created_output is None:
            return
        try:
            created_output.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("清理管线输出失败: path=%s, error=%s", created_output, exc)

    def _validate_add_relative_path(self, relative_path: str) -> str:
        """校验 add 输入是 memes/ 内的规范 POSIX 相对路径。

        Args:
            relative_path: 待校验路径。

        Returns:
            校验后的原始 POSIX 相对路径。

        Raises:
            ValueError: 路径非规范相对路径、包含父目录跳转或解析到 memes/ 外。
        """
        path = Path(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or path.as_posix() != relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("relative_path 必须是 memes/ 内的规范 POSIX 相对路径")
        memes_dir = self._memes_dir.resolve()
        resolved_path = (self._memes_dir / path).resolve(strict=False)
        if not resolved_path.is_relative_to(memes_dir):
            raise ValueError("relative_path 解析后超出 memes/ 目录")
        return relative_path

    async def _process_image_pipeline(
        self, filename: str
    ) -> tuple[str, str, list[float]]:
        """压缩 -> OCR -> Embedding 管道。

        optimize 后读取 result.output_path 作为最终路径；若与原 filename 不同（转 webp），
        final_filename 取 output_path 的文件名。optimize 失败时降级：清理可能已生成的
        .webp 孤儿，回退用原 filename 继续 OCR/embed，不抛错。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            (final_filename, text, embedding)：final_filename 可能与原 filename 不同
            （转 webp 后为 .webp）。

        Raises:
            OcrError: OCR 服务未注入或调用失败。
            EmbeddingError: Embedding 服务未注入或调用失败。
        """
        image_path = self._memes_dir / filename
        final_filename = filename
        created_output: Path | None = None
        if self._optimizer is not None:
            try:
                result, existing_paths = await self._optimize_with_cancellation(
                    filename
                )
                final_image_path = Path(result.output_path)
                if final_image_path not in existing_paths:
                    created_output = final_image_path
                final_filename = final_image_path.relative_to(
                    self._memes_dir
                ).as_posix()
                image_path = final_image_path
            except Exception as exc:
                # 降级：optimize 失败时 _convert_image_to_webp 内部已清理 .webp 孤儿，回退原 filename
                logger.warning(
                    "转 webp 失败，降级保留原格式: filename=%s, error=%s", filename, exc
                )
                final_filename = filename
                image_path = self._memes_dir / filename
        if self._ocr_provider is None:
            raise OcrError("OCR 服务未注入")
        try:
            text = await self._ocr_provider.ocr(str(image_path))
        except asyncio.CancelledError:
            self._cleanup_pipeline_output(created_output)
            raise
        except Exception as exc:
            self._cleanup_pipeline_output(created_output)
            raise OcrError(f"OCR 调用失败: {filename}") from exc
        text = "".join(text.split())  # 统一去除所有空白
        if not text:
            # 空文本不 embed，由下游 no_text 分支移图
            # （避免 provider 对空串抛 ValueError 导致 no_text 分支不可达）
            return final_filename, "", []
        if self._embedding_provider is None:
            raise EmbeddingError("Embedding 服务未注入")
        try:
            embedding = await self._embedding_provider.embed(text)
        except asyncio.CancelledError:
            self._cleanup_pipeline_output(created_output)
            raise
        except Exception as exc:
            self._cleanup_pipeline_output(created_output)
            raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc
        return final_filename, text, embedding

    def _move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._no_text_dir, Path(filename).name)
        shutil.move(str(src), str(dst))
        logger.warning("OCR 未识别到文字，已移至无文字目录: %s -> %s", filename, dst)
        return str(dst)
