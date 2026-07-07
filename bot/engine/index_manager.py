"""索引管理模块 — 薄编排层。

持有 MetadataStore + VectorStore + providers，负责压缩→OCR→Embed 管道编排、
sync 四阶段（含阶段0跨库一致性修复）、跨库写入一致性、读写锁、并发上限、
去重/无文字移图。不直接写 SQL/Chroma，全部委托两个 Store。
"""

import asyncio
import itertools
import logging
import shutil
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Protocol

from bot.config import read_add_command_timeout, read_read_lock_timeout
from bot.session import session_manager
from .ai_matcher import AIMatcher, AIMatchResult
from .image_optimizer import OptimizeResult
from .keyword_searcher import KeywordSearcher, SearchResult
from .metadata_store import MemeEntry
from .protocols import EmbeddingProvider
from .random_searcher import RandomSearcher
from .rwlock import IndexRwLock
from .semantic_searcher import SemanticSearcher
from .utils import vector_norm
from .vector_store import VectorHit

logger = logging.getLogger(__name__)


def resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。

    Returns:
        目标目录下不冲突的完整路径。
    """
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for n in itertools.count(2):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")


class IndexCorruptedError(Exception):
    """索引数据库结构损坏时抛出。"""


class CompressionError(RuntimeError):
    """图片压缩失败。"""


class OcrError(RuntimeError):
    """OCR 识别失败。"""


class EmbeddingError(RuntimeError):
    """Embedding 生成失败。"""


class RefreshInProgressError(RuntimeError):
    """索引刷新进行中，新的写入请求应被拒绝。"""


class IndexAddCancelledError(RuntimeError):
    """/add 任务因刷新或关闭而被取消。"""


class WriteOp(Enum):
    """Write Worker 操作类型枚举。"""

    ADD = auto()
    EDIT_TEXT = auto()
    SET_SPEAKER = auto()
    ADD_TAG = auto()
    DELETE = auto()


@dataclass
class _WriteRequest:
    """写入任务单元，由 Write Worker 串行处理。

    Attributes:
        op: 操作类型（ADD / EDIT_TEXT / SET_SPEAKER）。
        future: 用于返回结果的 asyncio.Future。
        entry_id: EDIT_TEXT / SET_SPEAKER 时为目标 id；ADD 时为 0（store 自动分配）。
        filename: ADD 时 memes/ 下文件名。
        text: 写入的 text（ADD=OCR text，EDIT_TEXT=新文本）。
        speaker: SET_SPEAKER 或 ADD 时使用的说话人。
        tags: ADD 时使用的标签列表。
        embedding: 对应的 embedding 向量。
        old_text: EDIT_TEXT 旧 text（回滚用）。
    """

    op: WriteOp
    future: "asyncio.Future[AddResult | EditTextResult | SetSpeakerResult | AddTagResult | DeleteResult]"
    entry_id: int = 0
    filename: str = ""
    text: str = ""
    speaker: str | None = None
    tags: list[str] | None = None
    entry_ids: list[int] | None = None
    embedding: list[float] | None = None
    old_text: str = ""


@dataclass
class EditTextResult:
    """edit_text() 的返回结果。

    Attributes:
        entry_id: 被修改的条目 id。
        old_text: 修改前的 OCR 文本。
        new_text: 修改后的 OCR 文本。
    """

    entry_id: int
    old_text: str
    new_text: str


@dataclass
class SetSpeakerResult:
    """set_speaker() 的返回结果。

    Attributes:
        entry_id: 被修改的条目 id。
        old_speaker: 修改前的 speaker 值。
        new_speaker: 修改后的 speaker 值。
    """

    entry_id: int
    old_speaker: str | None
    new_speaker: str | None


@dataclass
class AddTagResult:
    """add_tag() 的返回结果。

    Attributes:
        entry_id: 被修改的条目 id。
        added_tags: 本次新增的标签列表。
        all_tags: 修改后的全部标签列表。
    """

    entry_id: int
    added_tags: list[str]
    all_tags: list[str]


@dataclass
class DeleteResult:
    """delete() 的返回结果。

    Attributes:
        deleted_ids: 成功删除的条目 id 列表。
        not_found_ids: 不存在的条目 id 列表。
        failed_ids: 删除失败的 (id, reason) 列表。
    """

    deleted_ids: list[int]
    not_found_ids: list[int]
    failed_ids: list[tuple[int, str]]


@dataclass
class IndexInfo:
    """get_info() 的返回结果。

    Attributes:
        entry_count: 当前索引条目总数。
        speaker_ranking: speaker 使用频率排行（speaker, count）。
        status: 索引状态描述。
    """

    entry_count: int
    speaker_ranking: list[tuple[str | None, int]]
    status: str


class DuplicateTextError(RuntimeError):
    """edit_text 要修改的文本已被其他条目使用。"""


class OcrProvider(Protocol):
    """OCR 服务提供者协议。ocr() 返回去除所有空白后的文本。"""

    async def ocr(self, image_path: str) -> str: ...

    async def close(self) -> None:
        """关闭 provider 占用的资源。"""
        ...


class MetadataStoreProtocol(Protocol):
    """元数据存储协议（IndexManager 编排所需接口子集）。

    仅声明 IndexManager 实际调用的方法（load/entry_count/get_all_entries/
    get_entry/get_id_by_text/add/update/remove）。
    """

    def load(self) -> None: ...
    def entry_count(self) -> int: ...
    def get_all_entries(self) -> dict[int, MemeEntry]: ...
    def get_entry(self, entry_id: int) -> MemeEntry | None: ...
    def get_id_by_text(self, text: str) -> int | None: ...
    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int: ...
    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = None,
        text: str | None = None,
        speaker: (
            str | None
        ) = None,  # None means "clear speaker"; _UNSET=no-change internally
        tags: list[str] | None = None,
    ) -> bool: ...
    def remove(self, entry_id: int) -> bool: ...
    def close(self) -> None: ...


class VectorStoreProtocol(Protocol):
    """向量存储协议（IndexManager 编排所需接口子集）。

    仅声明 IndexManager 实际调用的方法（load/count/upsert/remove/
    remove_many/query/rebuild_all）。
    """

    def load(self) -> None: ...
    def count(self) -> int: ...

    async def upsert(self, entry_id: int, embedding: list[float]) -> None: ...
    async def remove(self, entry_id: int) -> None: ...
    async def remove_many(self, entry_ids: list[int]) -> None: ...
    async def query(
        self, query_embedding: list[float], n_results: int = 10
    ) -> list[VectorHit]: ...
    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None: ...
    def close(self) -> None: ...


class ImageOptimizerProtocol(Protocol):
    """图片压缩器协议。IndexManager 仅调用 optimize。"""

    async def optimize(self, image_path: str) -> OptimizeResult: ...


@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因 text 命中已有条目而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class AddResult:
    """add() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID（int）；无文字移图场景为 None。
        reason: 结果类别：added / replaced / no_text。
        text: OCR 文本（无空格）。
        replaced_image_path: reason="replaced" 时为被替换旧图路径，否则 None。
        archived_path: reason="replaced" 时为旧图归档后的完整路径，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
        speaker: ADD 时写入的说话人（无文字移图时为 None）。
        tags: ADD 时写入的标签列表（无文字移图时为空列表）。
    """

    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    archived_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)


class IndexManager:
    """索引管理薄编排层。

    持有 MetadataStore + VectorStore + providers，负责管道编排与跨库一致性。

    Attributes:
        _metadata_store: 元数据存储。
        _vector_store: 向量存储。
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
        metadata_store: MetadataStoreProtocol,
        vector_store: VectorStoreProtocol,
        memes_dir: str,
        no_text_dir: str | None = None,
        deleted_dir: str | None = None,
        replaced_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizerProtocol | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
        random_searcher: RandomSearcher | None = None,
        semantic_searcher: SemanticSearcher | None = None,
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

    # ------------------------------------------------------------------
    # load / 查询
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """委托两个 Store.load()，并记录当前条目数。

        启动时必须调用此方法后再使用其他查询或写入方法。
        """
        await asyncio.gather(
            asyncio.to_thread(self._metadata_store.load),
            asyncio.to_thread(self._vector_store.load),
        )
        logger.info("IndexManager 加载完成: %d 条记录", self.entry_count)

    @property
    def entry_count(self) -> int:
        """当前索引条目总数。

        Returns:
            当前 sqlite 中的条目数量。
        """
        return self._metadata_store.entry_count()

    async def search(self, keyword: str) -> list[SearchResult]:
        """关键词搜索。

        Args:
            keyword: 用户输入的关键词。

        Returns:
            搜索结果列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: KeywordSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._metadata_store.entry_count() == 0:
                return []
            if self._keyword_searcher is None:
                raise RuntimeError("KeywordSearcher 未注入")
            return self._keyword_searcher.search(keyword)

    async def random_search(self, keyword: str | None = None) -> list[SearchResult]:
        """随机搜索入口。持读锁调用 RandomSearcher.search_random。

        Args:
            keyword: 可选关键词；None 或空串表示全库随机。

        Returns:
            随机取样后的 SearchResult 列表；空库时返回空列表。

        Raises:
            asyncio.TimeoutError: 等待读锁超时。
            RuntimeError: RandomSearcher 未注入。
        """
        async with self._rwlock.read(timeout=self.read_timeout):
            if self._metadata_store.entry_count() == 0:
                return []
            if self._random_searcher is None:
                raise RuntimeError("RandomSearcher 未注入")
            return self._random_searcher.search_random(keyword)

    async def semantic_search(self, description: str, limit: int = 10) -> list[SearchResult]:
        """语义搜索入口。锁外 embed，持读锁查询。

        Args:
            description: 用户自然语言描述。
            limit: 返回结果数量上限，默认 10。

        Returns:
            语义相似度 Top-N SearchResult 列表；空库时返回空列表。

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
            if self._vector_store.count() == 0:
                return []
            return await self._semantic_searcher.search_semantic(query_vector, limit=limit)

    async def ai_match(self, description: str) -> AIMatchResult | None:
        """AI 描述匹配。

        Args:
            description: 用户自然语言描述。

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
            return await self._ai_matcher.match_with_vector(description, query_vector)

    async def add(
        self,
        filename: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> AddResult:
        """提交 /add 任务并等待执行完成。

        Args:
            filename: memes/ 下的文件名。
            speaker: 可选说话人。
            tags: 可选标签列表。

        Returns:
            AddResult 描述添加结果。

        Raises:
            RefreshInProgressError: 当前有刷新任务在运行。
            IndexAddCancelledError: Bot 正在关闭。
        """
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在批量刷新，请稍后再试")

        text, embedding = await self._process_image_pipeline(filename)

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
                filename=filename,
                text=text,
                speaker=speaker,
                tags=tags,
                embedding=embedding,
            )
        )
        return await future

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
        # 检查①：shutting_down（最高优先级，避免浪费 embed API）
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")

        # 检查②：refresh 状态
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # 确保 Write Worker 已启动
        self._ensure_write_worker()

        # 校验 entry 存在 + 获取旧 text（用于回滚）
        entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")
        old_text = entry.text
        if old_text == new_text:
            return EditTextResult(
                entry_id=entry_id, old_text=old_text, new_text=new_text
            )

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
        return await future

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
        # 检查①：shutting_down
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")

        # 检查②：refresh 状态
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        # 确保 Write Worker 已启动
        self._ensure_write_worker()

        # 校验 entry 存在 + 获取 old_speaker
        entry = await self._run_sync(self._metadata_store.get_entry, entry_id)

        # TOCTOU 防护（get_entry 期间 shutting_down 或 refresh 可能已激活）
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        if entry is None:
            raise ValueError(f"entry_id={entry_id} 不存在")
        old_speaker = entry.speaker
        if old_speaker == speaker:
            return SetSpeakerResult(
                entry_id=entry_id,
                old_speaker=old_speaker,
                new_speaker=speaker,
            )

        # 提交写入任务（不需要 embed，直接入队）
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[SetSpeakerResult]" = loop.create_future()
        req = _WriteRequest(
            op=WriteOp.SET_SPEAKER,
            future=future,  # type: ignore[arg-type]
            entry_id=entry_id,
            speaker=speaker,
        )
        await self._write_queue.put(req)
        return await future

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
        if self._shutting_down:
            raise IndexAddCancelledError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("索引正在刷新，请稍后再试")

        self._ensure_write_worker()

        entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
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
            future=future,  # type: ignore[arg-type]
            entry_id=entry_id,
            tags=list(tags),
        )
        await self._write_queue.put(req)
        return await future

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
            future=future,  # type: ignore[arg-type]
            entry_ids=list(entry_ids),
        )
        await self._write_queue.put(req)
        return await future

    async def info(self) -> IndexInfo:
        """返回当前索引内部统计信息（不含硬件）。

        Returns:
            IndexInfo 描述当前统计与状态。
        """
        entries = await self._run_sync(self._metadata_store.get_all_entries)

        speaker_counts: dict[str | None, int] = {}
        for entry in entries.values():
            speaker_counts[entry.speaker] = speaker_counts.get(entry.speaker, 0) + 1

        speaker_ranking = sorted(
            speaker_counts.items(),
            key=lambda item: (-item[1], item[0] or ""),
        )[:3]

        if self._refresh_active:
            status = "正在刷新索引"
        elif session_manager.has_active_session():
            status = "正在处理命令"
        else:
            status = "空闲"

        return IndexInfo(
            entry_count=len(entries),
            speaker_ranking=speaker_ranking,
            status=status,
        )

    async def refresh(self) -> SyncResult:
        """独占执行索引同步（refresh）。

        Returns:
            SyncResult 描述同步结果。

        Raises:
            RefreshInProgressError: 已有刷新任务在运行或 Bot 正在关闭。
        """
        if self._shutting_down:
            raise RefreshInProgressError("Bot 正在关闭")
        if self._refresh_active:
            raise RefreshInProgressError("已有刷新任务在运行")

        self._refresh_active = True
        try:
            if not self._write_queue.empty():
                self._write_drained.clear()
                await self._write_drained.wait()

            async with self._rwlock.write():
                return await self._run_sync_internal()
        finally:
            self._refresh_active = False

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

            try:
                async with self._rwlock.write():
                    try:
                        if req.op is WriteOp.ADD:
                            if req.embedding is None:
                                raise ValueError("req 中的 embedding 为 None")
                            result = await self._write_entry(
                                req.filename,
                                req.text,
                                req.embedding,
                                req.speaker,
                                req.tags,
                            )
                        elif req.op is WriteOp.EDIT_TEXT:
                            result = await self._execute_edit_text(req)
                        elif req.op is WriteOp.SET_SPEAKER:
                            result = await self._execute_set_speaker(req)
                        elif req.op is WriteOp.ADD_TAG:
                            result = await self._execute_add_tags(req)
                        elif req.op is WriteOp.DELETE:
                            result = await self._execute_delete(req)
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
        # 写锁内 TOCTOU 检查 text 冲突
        existing_id = await self._run_sync(
            self._metadata_store.get_id_by_text,
            req.text,
        )
        if existing_id is not None and existing_id != req.entry_id:
            raise DuplicateTextError(
                f"OCR 文本「{req.text}」已被 entry_id={existing_id} 使用",
            )

        # 先 sqlite
        success = await self._run_sync(
            self._metadata_store.update,
            req.entry_id,
            text=req.text,
        )
        if not success:
            raise ValueError(f"entry_id={req.entry_id} 不存在")

        # 后 chroma，失败回滚 sqlite
        assert req.embedding is not None
        try:
            await self._vector_store.upsert(req.entry_id, req.embedding)
        except Exception as exc:
            try:
                await self._run_sync(
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
        entry = await self._run_sync(self._metadata_store.get_entry, req.entry_id)
        if entry is None:
            raise ValueError(f"entry_id={req.entry_id} 不存在（并发删除）")
        old_speaker = entry.speaker

        # 写 sqlite
        success = await self._run_sync(
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
        entry = await self._run_sync(self._metadata_store.get_entry, req.entry_id)
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

        success = await self._run_sync(
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
        """写锁内执行 delete（先 sqlite 后 chroma，再移动文件到 memes_deleted/）。

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
            entry = await self._run_sync(self._metadata_store.get_entry, entry_id)
            if entry is None:
                not_found_ids.append(entry_id)
                continue

            try:
                await self._run_sync(self._metadata_store.remove, entry_id)
                await self._vector_store.remove(entry_id)

                src = self._memes_dir / entry.image_path
                if src.exists():
                    dst = self._resolve_unique_deleted_path(entry.image_path)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.rename(dst)

                deleted_ids.append(entry_id)
            except Exception as exc:
                logger.error("删除条目失败: id=%s, error=%s", entry_id, exc)
                failed_ids.append((entry_id, str(exc)))

        return DeleteResult(
            deleted_ids=deleted_ids,
            not_found_ids=not_found_ids,
            failed_ids=failed_ids,
        )

    def _resolve_unique_deleted_path(self, image_path: str) -> Path:
        """生成 memes_deleted/ 下的唯一目标路径（冲突时追加 _n）。

        Args:
            image_path: 原始图片相对路径（memes/ 下文件名）。

        Returns:
            memes_deleted/ 下不冲突的完整路径。
        """
        dst = self._deleted_dir / Path(image_path).name
        if not dst.exists():
            return dst

        stem = dst.stem
        suffix = dst.suffix
        parent = dst.parent
        n = 1
        while True:
            candidate = parent / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def _move_to_replaced(self, filename: str) -> str:
        """将被替换的文件移动到 memes_replaced/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._replaced_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._replaced_dir, filename)
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
        """按文件名同步索引与 memes/ 目录（四阶段）。

        0. 一致性修复：对齐 sqlite ↔ chroma id 集合。
        1. 删除：memes/ 已不存在的图片。
        2. 新增：新图并行 OCR→embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []

        await self._sync_phase0_consistency(failed)
        deleted_count = await self._sync_phase1_delete()
        added_count, deduped_count, no_text_count = await self._sync_phase2_add(failed)

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
            added_count,
            deleted_count,
            deduped_count,
            no_text_count,
            len(failed),
        )
        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            failed=failed,
        )

    async def _run_sync[_T](
        self, fn: Callable[..., _T], *args: Any, **kwargs: Any
    ) -> _T:
        """用 asyncio.to_thread 包装 MetadataStore 同步方法，避免阻塞事件循环。

        Args:
            fn: 要在线程中执行的同步函数（通常是 MetadataStore 方法）。
            *args: 传给 fn 的位置参数。
            **kwargs: 传给 fn 的关键字参数。

        Returns:
            fn 的返回值（类型由 fn 签名推断）。
        """
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _sync_phase0_consistency(self, failed: list[str]) -> None:
        """阶段0：对齐 sqlite ↔ chroma 的 id 集合。

        - chroma 为空且 sqlite 有数据 → 全量重 embed 后 rebuild_all。
        - sqlite 有、chroma 无的 id → 逐条重 embed 并 upsert。
        - chroma 有、sqlite 无的 id → 删孤儿向量。

        Args:
            failed: 失败文件名收集列表，阶段0 重 embed 失败的 image_path 追加至此。
        """
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        sqlite_ids = set(entries)
        vs_count = self._vector_store.count()
        chroma_ids = await self._get_chroma_ids()

        # chroma 损坏/为空、sqlite 有数据 → rebuild_all 全量重 embed
        if vs_count == 0 and sqlite_ids:
            await self._rebuild_all_from_sqlite(entries, failed)
            return

        # sqlite 有、chroma 无 → 重 embed upsert
        missing = sqlite_ids - chroma_ids
        for eid in missing:
            text = entries[eid].text
            if not text:
                continue
            try:
                vec = await self._embedding_provider.embed(text)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("阶段0 重 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entries[eid].image_path)
                continue
            await self._vector_store.upsert(eid, vec)

        # chroma 有、sqlite 无 → 删孤儿向量
        orphans = chroma_ids - sqlite_ids
        if orphans:
            logger.info("阶段0 清理孤儿向量: %s", orphans)
            await self._vector_store.remove_many(list(orphans))

    async def _get_chroma_ids(self) -> set[int]:
        """获取 chroma 当前所有 id（用 query 全量召回实现）。

        Returns:
            chroma 中现存向量对应的 entry_id 集合；chroma 为空时返回空集。
        """
        if self._vector_store.count() == 0:
            return set()
        # 用零向量召回最多 count 条，提取 id
        n = self._vector_store.count()
        hits = await self._vector_store.query([0.0] * 1024, n_results=n)
        return {h.entry_id for h in hits}

    async def _rebuild_all_from_sqlite(
        self, entries: dict[int, MemeEntry], failed: list[str]
    ) -> None:
        """chroma 为空、sqlite 有数据 → 全量重 embed 后 rebuild_all。

        Args:
            entries: sqlite 当前全量条目（id → MemeEntry）。
            failed: 失败文件名收集列表，重 embed 失败的 image_path 追加至此。
        """
        items: list[tuple[int, list[float]]] = []
        for eid, entry in entries.items():
            if not entry.text:
                continue
            try:
                vec = await self._embedding_provider.embed(entry.text)  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("阶段0 全量重建 embed 失败: id=%s, error=%s", eid, exc)
                failed.append(entry.image_path)
                continue
            items.append((eid, vec))
        await self._vector_store.rebuild_all(items)

    async def _sync_phase1_delete(self) -> int:
        """阶段1：删除 memes/ 已不存在的图片对应记录。先 sqlite 后 chroma。

        Returns:
            本次删除的图片数量。
        """
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        deleted = 0
        for eid, entry in entries.items():
            if entry.image_path not in existing:
                logger.info(
                    "图片已删除，移除索引: id=%s, image_path=%s", eid, entry.image_path
                )
                await self._run_sync(self._metadata_store.remove, eid)
                await self._vector_store.remove(eid)
                deleted += 1
        return deleted

    async def _sync_phase2_add(self, failed: list[str]) -> tuple[int, int, int]:
        """阶段2：新图并行 OCR→embed，串行三分类（无文字移图 / 去重删新图 / 正常新增）。

        Args:
            failed: 失败文件名收集列表，处理异常或 upsert 失败回滚的文件名追加至此。

        Returns:
            (added, deduped, no_text_moved) 三元组：新增、去重删除、无文字移走数量。
        """
        existing = self._scan_meme_files()
        entries = await self._run_sync(self._metadata_store.get_all_entries)
        existing_paths = {e.image_path for e in entries.values()}
        new_files = sorted(f for f in existing if f not in existing_paths)
        if not new_files:
            return (0, 0, 0)

        logger.info("开始并行处理 %d 张新增图片", len(new_files))
        raw = await asyncio.gather(
            *(self._process_image_pipeline(fn) for fn in new_files),
            return_exceptions=True,
        )

        success: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                text, embedding = result
                success[filename] = (text, embedding)

        # winner_keys 初始 = 已有条目的 text 集合
        winner_keys: set[str] = {e.text for e in entries.values() if e.text}

        added = deduped = no_text_moved = 0
        for filename in sorted(success):
            text, embedding = success[filename]
            if not text:
                await self._run_sync(self._move_to_no_text, filename)
                no_text_moved += 1
                continue
            if text in winner_keys:
                try:
                    archived_path = await self._run_sync(
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
            eid = await self._run_sync(self._metadata_store.add, filename, text)
            try:
                await self._vector_store.upsert(eid, embedding)
            except Exception as exc:
                logger.error("新增 upsert 失败，回滚 sqlite: id=%s, error=%s", eid, exc)
                await self._run_sync(self._metadata_store.remove, eid)
                failed.append(filename)
                continue
            winner_keys.add(text)
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
    ) -> AddResult:
        """三分类写入：无文字移图 / 去重替换 / 正常新增。

        写入顺序统一"先 sqlite 后 chroma"，失败可回滚。

        Args:
            filename: memes/ 下的文件名。
            text: OCR 去除所有空白后的文本（空串表示无文字）。
            embedding: 与 text 对应的 embedding 向量。
            speaker: 可选说话人。
            tags: 可选标签列表。

        Returns:
            AddResult 描述本次写入结果（added / replaced / no_text）。

        Raises:
            EmbeddingError: 去重替换或正常新增时 upsert 失败，已回滚 sqlite 后重抛。
        """
        # 1. 无文字 → 移图，不进索引
        if not text:
            moved_to = await self._run_sync(self._move_to_no_text, filename)
            logger.info("OCR 无文字，已移至无文字目录: filename=%s", filename)
            return AddResult(
                entry_id=None,
                reason="no_text",
                moved_to=moved_to,
                speaker=None,
                tags=[],
            )

        # 2. 去重命中已有条目 → update image_path + upsert，删旧图
        old_id = await self._run_sync(self._metadata_store.get_id_by_text, text)
        if old_id is not None:
            old_entry = await self._run_sync(self._metadata_store.get_entry, old_id)
            old_image_path = old_entry.image_path if old_entry else ""
            old_speaker = old_entry.speaker if old_entry else None
            old_tags = old_entry.tags if old_entry else []
            # 顺序：先改 sqlite 指向新图，再 upsert 向量，最后删旧图
            await self._run_sync(
                self._metadata_store.update,
                old_id,
                image_path=filename,
                speaker=speaker,
                tags=tags,
            )
            try:
                await self._vector_store.upsert(old_id, embedding)
            except Exception as exc:
                logger.error(
                    "去重替换 upsert 失败，回滚 update: id=%s, error=%s", old_id, exc
                )
                await self._run_sync(
                    self._metadata_store.update,
                    old_id,
                    image_path=old_image_path,
                    speaker=old_speaker,
                    tags=old_tags,
                )
                (self._memes_dir / filename).unlink(missing_ok=True)
                raise EmbeddingError(f"去重替换 upsert 失败: {filename}") from exc
            # 归档旧图（最后移动，保证前序失败时旧图仍在）
            archived_path: str | None = None
            if old_image_path and old_image_path != filename:
                archived_path = await self._run_sync(
                    self._move_to_replaced, old_image_path
                )
            logger.info(
                "去重替换: id=%s, 旧=%s, 新=%s, archived=%s",
                old_id,
                old_image_path,
                filename,
                archived_path,
            )
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                text=text,
                replaced_image_path=old_image_path,
                archived_path=archived_path,
                speaker=speaker,
                tags=tags or [],
            )

        # 3. 正常新增：先 sqlite 后 chroma；upsert 失败回滚 sqlite + 删图
        eid = await self._run_sync(
            self._metadata_store.add, filename, text, speaker, tags
        )
        try:
            await self._vector_store.upsert(eid, embedding)
        except Exception as exc:
            logger.error(
                "新增 upsert 失败，回滚 sqlite + 删图: id=%s, error=%s", eid, exc
            )
            await self._run_sync(self._metadata_store.remove, eid)
            (self._memes_dir / filename).unlink(missing_ok=True)
            raise EmbeddingError(f"新增 upsert 失败: {filename}") from exc
        logger.info("已添加索引记录: id=%s, filename=%s", eid, filename)
        return AddResult(
            entry_id=eid,
            reason="added",
            text=text,
            speaker=speaker,
            tags=tags or [],
        )

    # ------------------------------------------------------------------
    # 管道与工具
    # ------------------------------------------------------------------

    def _scan_meme_files(self) -> set[str]:
        """扫描 memes/，返回受支持扩展名的文件名集合。

        Returns:
            memes/ 下所有受支持图片扩展名的文件名集合（仅文件名，不含路径）。
        """
        return {
            f.name
            for f in self._memes_dir.iterdir()
            if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        }

    async def _process_image_pipeline(self, filename: str) -> tuple[str, list[float]]:
        """压缩 → OCR → Embedding 管道。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            (text, embedding) 二元组：text 为去空白后的 OCR 文本（可能为空串），
            embedding 为对应向量。

        Raises:
            CompressionError: 图片压缩失败。
            OcrError: OCR 服务未注入或调用失败。
            EmbeddingError: Embedding 服务未注入或调用失败。
        """
        image_path = self._memes_dir / filename
        if self._optimizer is not None:
            try:
                await self._optimizer.optimize(str(image_path))
            except Exception as exc:
                raise CompressionError(f"图片压缩失败: {filename}") from exc
        if self._ocr_provider is None:
            raise OcrError("OCR 服务未注入")
        try:
            text = await self._ocr_provider.ocr(str(image_path))
        except Exception as exc:
            raise OcrError(f"OCR 调用失败: {filename}") from exc
        text = "".join(
            text.split()
        )  # 统一去除所有空白（plan line 46 约定 + T8 不变量）
        if self._embedding_provider is None:
            raise EmbeddingError("Embedding 服务未注入")
        try:
            embedding = await self._embedding_provider.embed(text)
        except Exception as exc:
            raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc
        return text, embedding

    def _move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            移入后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = resolve_unique_filename(self._no_text_dir, filename)
        shutil.move(str(src), str(dst))
        logger.warning("OCR 未识别到文字，已移至无文字目录: %s -> %s", filename, dst)
        return str(dst)
