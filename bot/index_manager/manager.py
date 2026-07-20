"""索引管理模块 — 薄编排层。

持有 MetadataStore + VectorStore + providers，负责压缩→OCR→Embed 管道编排、
递归扫描、跨库一致性、合集生命周期、图片增删、读写锁与去重/无文字移图。
不直接写 SQL/Chroma，全部委托两个 Store。
"""

import asyncio
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from bot.config import read_add_command_timeout, read_read_lock_timeout
from bot.engine.collection_manager import validate_collection_name
from bot.engine.combined_searcher import CombinedSearcher
from bot.engine.image_optimizer import ImageOptimizer
from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry, MetadataStore
from bot.engine.protocols import EmbeddingProvider, OcrProvider
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.types import (
    CollectionSelection,
    MemePublicId,
    SearchResult,
)
from bot.engine.utils import get_regular_file_identity, vector_norm
from bot.engine.vector_store import VectorStore
from bot.log_context import timed
from bot.session import ChatScope

from .image_pipeline import ImagePipeline
from .index_types import (
    AddResult,
    AddTagResult,
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    CollectionSelectionExpiredError,
    CompressionError,
    CreateCollectionResult,
    DeleteCollectionResult,
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
    RefreshInProgressError,
    RenameCollectionResult,
    SetSpeakerResult,
    SyncResult,
    WriteOp,
    _OptimizerLockEntry,
    _WriteRequest,
)
from .rwlock import IndexRwLock

if TYPE_CHECKING:
    from bot.engine.collection_manager import (
        CollectionManager,
        CollectionSelection,
        CollectionSummary,
    )

logger = logging.getLogger(__name__)

_CORRUPTED_DB_MESSAGE = (
    "索引数据库损坏或非 sqlite 格式，请修复 data/index.db 后重启 Bot"
)

__all__ = [
    "AddResult",
    "CollectionAlreadyExistsError",
    "CollectionCreateError",
    "CollectionPathConflictError",
    "CollectionSelectionExpiredError",
    "CompressionError",
    "CreateCollectionResult",
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
    "RefreshInProgressError",
    "SetSpeakerResult",
    "SyncResult",
    "WriteOp",
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
        _ocr_provider / _embedding_provider / _optimizer: providers（property 转发至
            ImagePipeline，保留测试 rebind）。
        _keyword_searcher: 关键词搜索器，由 IndexManager 持锁后调用。
        _rwlock: 读写锁，写者优先。
        _refresh_active: 是否有 refresh 正在执行写锁内的同步。
        _shutting_down: 是否正在关闭。
        _write_drained: 无排队且无执行中写请求时 set；refresh 等待后获取写锁。
        _image_pipeline: 压缩 -> OCR -> Embedding 管道与 optimizer 锁表的协作者。
    """

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
        # ImagePipeline 持有 optimizer/ocr/embedding providers 与 optimizer 锁表；
        # 门面通过 _optimizer/_ocr_provider/_embedding_provider property 转发，
        # 保留测试对门面实例属性 rebind 的能力（property setter 同步写回 pipeline）。
        self._image_pipeline = ImagePipeline(
            optimizer=optimizer,
            ocr_provider=ocr_provider,
            embedding_provider=embedding_provider,
            memes_dir=self._memes_dir,
            no_text_dir=self._no_text_dir,
        )
        self._keyword_searcher = keyword_searcher
        self._random_searcher = random_searcher
        self._semantic_searcher = semantic_searcher
        self._combined_searcher = combined_searcher

        if collection_manager is None:
            from bot.engine.collection_manager import CollectionManager

            collection_manager = CollectionManager(metadata_store)
        self._collection_manager = collection_manager

        self.read_timeout = float(read_read_lock_timeout())
        self.add_user_timeout = float(read_add_command_timeout())

        self._rwlock = IndexRwLock()
        self._shutting_down = False

        # EntryWriter 无状态，构造一次复用。
        # move_to_replaced 已迁入 _WriteCoordinator，lambda 延迟查找 self._coordinator，
        # 在第一次写发生时（load() 之后）coordinator 必然已构造完成。
        # move_to_no_text 已迁入 ImagePipeline，lambda 指向 self._image_pipeline.move_to_no_text，
        # 仍走调用时属性查找以保留 rebind。
        from .entry_writer import EntryWriter

        self._entry_writer = EntryWriter(
            metadata_store=self._metadata_store,
            vector_store=self._vector_store,
            memes_dir=self._memes_dir,
            move_to_no_text=lambda f: self._image_pipeline.move_to_no_text(f),
            move_to_replaced=lambda f: self._coordinator.move_to_replaced(f),
        )

        # _WriteCoordinator 收口全部写入编排：worker 循环 + queue + WriteOp 字典派发
        # + 七个 _execute_* + move 补偿。write_entry 回调用 lambda 包装门面
        # self._write_entry，保留测试对 index_manager._write_entry 的 monkeypatch
        # 接缝（调用时属性查找）。
        from .write_coordinator import _WriteCoordinator

        write_drained = asyncio.Event()
        write_drained.set()
        self._coordinator = _WriteCoordinator(
            metadata_store=self._metadata_store,
            vector_store=self._vector_store,
            collection_manager=self._collection_manager,
            memes_dir=self._memes_dir,
            deleted_dir=self._deleted_dir,
            replaced_dir=self._replaced_dir,
            rwlock=self._rwlock,
            write_drained=write_drained,
            write_entry=lambda *a, **kw: self._write_entry(*a, **kw),
            validate_collection_selection_locked=self._validate_collection_selection_locked,
        )

        # SyncEngine 承接 refresh 全流程的 phase 编排（扫描 -> 一致性 -> 删除 ->
        # 合集 -> 新增）。_refresh_active / _refresh_task 真实状态归 SyncEngine，
        # 门面经 forwarding property 透传。process_image_pipeline / move_to_replaced
        # 以 lambda 包装门面方法，保留测试对 index_manager._process_image_pipeline
        # 与 index_manager._coordinator.move_to_replaced 的 monkeypatch 接缝。
        from .sync_engine import SyncEngine

        self._sync_engine = SyncEngine(
            metadata_store=self._metadata_store,
            vector_store=self._vector_store,
            memes_dir=self._memes_dir,
            image_pipeline=self._image_pipeline,
            process_image_pipeline=lambda f: self._process_image_pipeline(f),
            move_to_replaced=lambda f: self._coordinator.move_to_replaced(f),
        )

    # ------------------------------------------------------------------
    # provider / 锁表 property 转发（保留测试 rebind 与直读）
    # ------------------------------------------------------------------

    @property
    def _optimizer(self) -> ImageOptimizer | None:
        """转发至 ImagePipeline.optimizer（保留测试 rebind 接缝）。"""
        return self._image_pipeline.optimizer

    @_optimizer.setter
    def _optimizer(self, value: ImageOptimizer | None) -> None:
        self._image_pipeline.optimizer = value

    @property
    def _ocr_provider(self) -> OcrProvider | None:
        """转发至 ImagePipeline.ocr_provider（保留测试 rebind 接缝）。"""
        return self._image_pipeline.ocr_provider

    @_ocr_provider.setter
    def _ocr_provider(self, value: OcrProvider | None) -> None:
        self._image_pipeline.ocr_provider = value

    @property
    def _embedding_provider(self) -> EmbeddingProvider | None:
        """转发至 ImagePipeline.embedding_provider（保留测试 rebind 接缝）。"""
        return self._image_pipeline.embedding_provider

    @_embedding_provider.setter
    def _embedding_provider(self, value: EmbeddingProvider | None) -> None:
        self._image_pipeline.embedding_provider = value

    @property
    def _optimizer_target_locks(self) -> dict[tuple[str, str], _OptimizerLockEntry]:
        """转发至 ImagePipeline.optimizer_target_locks（测试只读断言清空）。"""
        return self._image_pipeline.optimizer_target_locks

    @property
    def _optimizer_registry_guard(self) -> asyncio.Lock:
        """转发至 ImagePipeline.optimizer_registry_guard（测试 acquire/release 制造取消窗口）。"""
        return self._image_pipeline.optimizer_registry_guard

    @property
    def _write_queue(self) -> "asyncio.Queue[_WriteRequest]":
        """转发至 _WriteCoordinator.write_queue（测试 put/read 经 property 操作 coordinator 队列）。"""
        return self._coordinator.write_queue

    @property
    def _write_drained(self) -> asyncio.Event:
        """转发至 _WriteCoordinator.write_drained（测试 clear/is_set 经 property 操作同一 Event）。"""
        return self._coordinator.write_drained

    @property
    def _refresh_active(self) -> bool:
        """转发至 SyncEngine._refresh_active（测试 rebind + 门面写入方法读取均经 property）。"""
        return self._sync_engine._refresh_active

    @_refresh_active.setter
    def _refresh_active(self, value: bool) -> None:
        self._sync_engine._refresh_active = value

    @property
    def _refresh_task(self) -> asyncio.Task | None:
        """转发至 SyncEngine._refresh_task（refresh() 写入 + close()/测试读取均经 property）。"""
        return self._sync_engine._refresh_task

    @_refresh_task.setter
    def _refresh_task(self, value: asyncio.Task | None) -> None:
        self._sync_engine._refresh_task = value

    # ------------------------------------------------------------------
    # load / 查询
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """委托两个 Store.load()，并记录当前条目数。

        启动时必须调用此方法后再使用其他查询或写入方法。

        Raises:
            IndexCorruptedError: data/index.db 损坏或非 sqlite 格式（拒绝启动）。
        """
        logger.info("开始加载索引...")
        await asyncio.gather(
            asyncio.to_thread(self._load_metadata_store),
            asyncio.to_thread(self._vector_store.load),
        )
        logger.info("索引加载完成")
        logger.info("IndexManager 加载完成: %d 条记录", self.entry_count)

    def _load_metadata_store(self) -> None:
        """加载元数据存储，把 sqlite 损坏/格式错误归并为 IndexCorruptedError。

        Raises:
            IndexCorruptedError: data/index.db 损坏或非 sqlite 格式，要求先修复数据库。
        """
        try:
            self._metadata_store.load()
        except sqlite3.DatabaseError as exc:
            raise IndexCorruptedError(_CORRUPTED_DB_MESSAGE) from exc

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

    async def search_for_scope(
        self, scope: "ChatScope", keyword: str
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
        self, scope: "ChatScope", keyword: str | None = None
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
        scope: "ChatScope",
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
        scope: "ChatScope",
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
        scope: "ChatScope",
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

    async def create_collection(self, raw_name: str) -> CreateCollectionResult:
        """创建或登记空合集目录。

        Args:
            raw_name: 用户输入的合集名称。

        Returns:
            已持久化的合集创建结果。

        Raises:
            InvalidCollectionNameError: 合集名称非法。
            CollectionAlreadyExistsError: 名称已被登记。
            CollectionPathConflictError: 同名路径不是安全普通目录。
            CollectionCreateError: SQLite 失败后目录补偿失败。
            RefreshInProgressError: 索引刷新正在执行。
            IndexAddCancelledError: Bot 正在关闭或写入 worker 被取消。
        """
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        collection_name = validate_collection_name(raw_name)
        self._ensure_write_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CreateCollectionResult] = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.CREATE_COLLECTION,
            future=future,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            collection_name=collection_name,
        )
        return await self._coordinator.submit(req)

    async def delete_collection(self, raw: str) -> DeleteCollectionResult:
        """删除空合集：rmdir 空目录 + 删 DB 记录 + 回退引用它的 ChatScope。

        Args:
            raw: 目标合集编号或精确名称（编号优先、名称兜底）。

        Returns:
            删除结果，含回退到全部合集的 ChatScope 行数。

        Raises:
            InvalidCollectionNameError: 不会触发（delete 不创建新名称）。
            CollectionNotFoundError: 目标合集不存在。
            CollectionNotEmptyError: 合集仍含表情包。
            CollectionPathConflictError: 同名路径不是普通目录。
            CollectionDeleteError: rmdir 或删 DB 失败且补偿失败。
            RefreshInProgressError: 索引刷新正在执行。
            IndexAddCancelledError: Bot 正在关闭或写入 worker 被取消。
        """
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # CollectionNotFoundError 由 self._collection_manager.resolve_collection
        # 在编号与名称均未命中时直接抛出，门面无需显式 raise。
        collection = await asyncio.to_thread(
            self._collection_manager.resolve_collection, raw
        )
        self._ensure_write_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DeleteCollectionResult] = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.DELETE_COLLECTION,
            future=future,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            collection_id=collection.id,
        )
        return await self._coordinator.submit(req)

    async def rename_collection(
        self, raw: str, new_name: str
    ) -> RenameCollectionResult:
        """重命名合集：改 DB name + 重命名目录 + 更新该合集 image_path 首段。

        collection_id 不变，chroma 与 ChatScope 不受影响。

        Args:
            raw: 源合集编号或精确名称（编号优先、名称兜底）。
            new_name: 新合集名称（走 validate_collection_name 校验，不能与已登记名重复）。

        Returns:
            重命名结果，含旧名、新名与受影响条目数。

        Raises:
            InvalidCollectionNameError: 新名称非法。
            CollectionNotFoundError: 源合集不存在。
            CollectionRenameTargetExistsError: 目标名称已登记。
            CollectionPathConflictError: 源/目标路径不是普通目录或目标已存在。
            CollectionCreateError: 目录 rename 失败且补偿失败。
            RefreshInProgressError: 索引刷新正在执行。
            IndexAddCancelledError: Bot 正在关闭或写入 worker 被取消。
        """
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # CollectionNotFoundError 由 self._collection_manager.resolve_collection
        # 在编号与名称均未命中时直接抛出，门面无需显式 raise。
        validated_new_name = validate_collection_name(new_name)
        collection = await asyncio.to_thread(
            self._collection_manager.resolve_collection, raw
        )
        self._ensure_write_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[RenameCollectionResult] = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.RENAME_COLLECTION,
            future=future,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            collection_id=collection.id,
            new_collection_name=validated_new_name,
        )
        return await self._coordinator.submit(req)

    async def add(
        self,
        relative_path: str,
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
        scope: "ChatScope | None" = None,
        expected_selection: CollectionSelection | None = None,
    ) -> AddResult:
        """提交 /add 任务并等待执行完成。

        Args:
            relative_path: 图片相对 memes/ 的 POSIX 路径。
            speaker: 可选说话人。
            tags: 可选标签序列（list 或 tuple 均可）。
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
        relative_path = self._image_pipeline.validate_add_relative_path(relative_path)
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
            req = _WriteRequest(
                op=WriteOp.ADD,
                future=future,
                filename=final_filename,
                text=text,
                speaker=speaker,
                tags=tuple(tags) if tags is not None else None,
                embedding=tuple(embedding),
                collection_id=collection_id,
                scope=scope,
                expected_selection=expected_selection,
            )
            try:
                result = await self._coordinator.submit(req)
                logger.info("图片添加完成: %s", image_path)
                return result
            except CollectionSelectionExpiredError:
                self._image_pipeline.cleanup_pipeline_output(
                    self._memes_dir / final_filename
                )
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
            new_text: 新的 OCR 文本（调用方已按英文逗号拼接）。

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
            embedding=tuple(new_embedding),
            old_text=old_text,
        )
        result = await self._coordinator.submit(req)
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
        result = await self._coordinator.submit(req)
        logger.info("发言人设置完成: entry_ids=%s", entry_id)
        return result

    async def add_tags(self, entry_id: int, tags: Sequence[str]) -> AddTagResult:
        """为指定条目追加标签。

        流程：校验 → put WriteRequest → await future。

        Args:
            entry_id: 要修改的索引 id。
            tags: 要追加的标签序列（list 或 tuple 均可）。

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
            tags=tuple(tags),
        )
        result = await self._coordinator.submit(req)
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
            entry_ids=tuple(entry_ids),
        )
        result = await self._coordinator.submit(req)
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
        target_name = self._coordinator.resolve_move_target_name(target_collection_id)
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
            except OSError as exc:
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
        scope: "ChatScope",
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
                from bot.engine.collection_manager import MemeNotFoundError

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
        req = _WriteRequest(
            op=WriteOp.MOVE,
            future=future,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            entry_id=entry_id,
            target_collection_id=target_collection_id,
            expected_source=expected_source,
            expected_target_name=expected_target_name,
            transaction_started=transaction_started,
        )
        return await self._coordinator.submit_move(req)

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

        collection_count = len(self._metadata_store.list_collections())

        if self._refresh_active:
            status = "正在刷新索引"
        else:
            status = "空闲"

        return IndexInfo(
            entry_count=entry_count,
            current_entry_count=len(entries),
            collection_count=collection_count,
            speaker_ranking=tuple(speaker_ranking),
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
        self, scope: "ChatScope"
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
        scope: "ChatScope",
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
        scope: "ChatScope",
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

    async def list_collections(self, scope: "ChatScope") -> "list[CollectionSummary]":
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
        self, scope: "ChatScope", target: str
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

    async def resolve_entry(self, scope: "ChatScope", raw_id: str) -> MemeEntry:
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
                from bot.engine.collection_manager import MemeNotFoundError

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
            await self._write_drained.wait()

            async with self._rwlock.write():
                try:
                    result = await self._run_sync_internal()
                except sqlite3.DatabaseError as exc:
                    raise IndexCorruptedError(_CORRUPTED_DB_MESSAGE) from exc
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
        """薄委托至 _WriteCoordinator.ensure_worker（保留测试 monkeypatch 接缝）。"""
        self._coordinator.ensure_worker()

    async def close(self) -> None:
        """安全关闭 IndexManager。

        1. 设置 shutting_down，拒绝新的 add/refresh。
        2. 取消正在运行的 Write Worker 与 refresh task。
        3. 等待它们实际结束。
        4. 关闭 MetadataStore 和 VectorStore。
        """
        self._shutting_down = True

        tasks_to_wait: list[asyncio.Task] = []

        worker_task = self._coordinator.cancel_worker()
        if worker_task is not None:
            tasks_to_wait.append(worker_task)

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
        本方法留门面以保留测试 monkeypatch 接缝（slow_refresh/hanging_sync/
        record_refresh 替换整个方法）；phase 调用直指 SyncEngine，scan 经薄委托
        保 monkeypatch。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []
        snapshot = self._scan_meme_files()

        await self._sync_engine._sync_phase0_consistency(failed)
        deleted_count = await self._sync_engine._sync_phase1_delete(
            set(snapshot.files), failed
        )
        (
            collections_deleted,
            scopes_reset,
        ) = await self._sync_engine._sync_collections_delete(snapshot.directories)
        collections_added = await self._sync_engine._sync_collections_add(
            snapshot.directories_with_images
        )
        (
            added_count,
            deduped_count,
            no_text_count,
        ) = await self._sync_engine._sync_phase2_add(snapshot.files, failed)

        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            collections_added=collections_added,
            collections_deleted=collections_deleted,
            scopes_reset=scopes_reset,
            failed=tuple(failed),
        )

    async def _get_chroma_ids(self) -> set[int]:
        """薄委托至 SyncEngine.get_chroma_ids（保留测试直调接缝）。

        实际实现（VectorStore.get_all_ids）已迁入 SyncEngine；门面保留此方法
        供测试直调，经薄委托转发至 sync_engine。
        """
        return await self._sync_engine.get_chroma_ids()

    async def _write_entry(
        self,
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        """委托 EntryWriter.write_entry。"""
        return await self._entry_writer.write_entry(
            filename,
            text,
            embedding,
            speaker=speaker,
            tags=tags,
            collection_id=collection_id,
        )

    # ------------------------------------------------------------------
    # 管道与工具
    # ------------------------------------------------------------------

    def _scan_meme_files(self) -> FileSystemSnapshot:
        """薄委托至 SyncEngine.scan_meme_files（保留测试 monkeypatch 接缝）。

        实际扫描逻辑（os.scandir + DirEntry 缓存 + 递归合集目录）已迁入 SyncEngine；
        门面保留此方法供 _run_sync_internal 与测试直调/monkeypatch（counting_scan），
        经薄委托转发至 sync_engine。
        """
        return self._sync_engine.scan_meme_files()

    async def _process_image_pipeline(
        self, filename: str
    ) -> tuple[str, str, list[float]]:
        """薄委托至 ImagePipeline.process（保留测试 monkeypatch rebind 接缝）。

        实际管道逻辑（压缩 -> OCR -> Embedding + optimizer 锁表）由
        ``self._image_pipeline.process`` 承载；门面保留此方法供 ``add()``/
        ``_sync_phase2_add`` 调用，以及测试通过 ``index_manager._process_image_pipeline
        = fake`` 接管。
        """
        return await self._image_pipeline.process(filename)
