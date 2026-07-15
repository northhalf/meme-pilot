"""IndexManager 的类型定义：异常、枚举、dataclass、Protocol 与结果类型。

从 index_manager.py 抽离，供编排层与外部调用方共享；index_manager.py 负责 re-export。
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bot.session import ChatScope

from .metadata_store import MemeEntry
from .types import CollectionSelection, MemePublicId


@dataclass(slots=True)
class _OptimizerLockEntry:
    """图片优化目标锁注册项。

    Attributes:
        lock: 同目标任务共享的互斥锁。
        users: 已取得引用或正在等待该锁的任务数。
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


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


class DuplicateMemeInCollectionError(RuntimeError):
    """目标合集存在相同 OCR 文本的条目。"""

    def __init__(self, conflicting_entry_id: int) -> None:
        """初始化目标合集文本冲突。

        Args:
            conflicting_entry_id: 冲突条目的内部 ID。
        """
        self.conflicting_entry_id = conflicting_entry_id
        super().__init__(f"目标合集存在重复条目: {conflicting_entry_id}")


class MemeMoveError(RuntimeError):
    """移动文件或补偿跨存储写入失败。"""


class MemeMoveSourceExpiredError(RuntimeError):
    """移动确认前保存的源表情包身份已经失效。"""


class WriteOp(Enum):
    """Write Worker 操作类型枚举。"""

    ADD = auto()
    EDIT_TEXT = auto()
    SET_SPEAKER = auto()
    ADD_TAG = auto()
    DELETE = auto()
    MOVE = auto()


@dataclass(slots=True)
class _WriteRequest:
    """写入任务单元，由 Write Worker 串行处理。

    Attributes:
        op: 操作类型（ADD / EDIT_TEXT / SET_SPEAKER / ADD_TAG / DELETE / MOVE）。
        future: 用于返回结果的 asyncio.Future。
        entry_id: EDIT_TEXT / SET_SPEAKER / ADD_TAG 时为目标 id；ADD 时为 0（store 自动分配）。
        filename: ADD 时 memes/ 下文件名。
        text: 写入的 text（ADD=OCR text，EDIT_TEXT=新文本）。
        speaker: SET_SPEAKER 或 ADD 时使用的说话人。
        tags: ADD / ADD_TAG 时使用的标签列表。
        entry_ids: DELETE 时为目标 id 列表。
        embedding: 对应的 embedding 向量。
        old_text: EDIT_TEXT 旧 text（回滚用）。
        collection_id: ADD 时的目标合集编号。
        scope: ADD 时发起命令的 ChatScope，用于写锁内校验选择快照。
        expected_selection: ADD 时捕获的完整合集选择快照。
        target_collection_id: MOVE 时的目标合集编号。
        expected_source: MOVE 确认前捕获的源条目与文件身份快照，用于写锁内校验源文件未变化。
        expected_target_name: MOVE 确认前捕获的目标合集名称。
        transaction_started: MOVE 取得写锁并决定执行事务时置位。
    """

    op: WriteOp
    future: "asyncio.Future[AddResult | EditTextResult | SetSpeakerResult | AddTagResult | DeleteResult | MoveResult]"
    entry_id: int = 0
    filename: str = ""
    text: str = ""
    speaker: str | None = None
    tags: list[str] | None = None
    entry_ids: list[int] | None = None
    embedding: list[float] | None = None
    old_text: str = ""
    collection_id: int = 0
    scope: "ChatScope | None" = None
    expected_selection: CollectionSelection | None = None
    target_collection_id: int = 0
    expected_source: "MoveSourceSnapshot | None" = None
    expected_target_name: str | None = None
    transaction_started: asyncio.Event | None = None


@dataclass(frozen=True, slots=True)
class MoveSourceSnapshot:
    """移动确认前的完整源条目与普通文件身份快照。

    Attributes:
        entry: 移动前的源表情包条目。
        file_identity: 源文件的 (st_dev, st_ino, st_ctime_ns) 身份标识，用于校验源文件未被替换。
    """

    entry: MemeEntry
    file_identity: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class MovePreview:
    """跨合集移动确认前的只读预览。

    Attributes:
        entry_id: 待移动的源条目 id。
        old_public_id: 移动前的公开 ID。
        source_collection_name: 源合集名称。
        target_collection_id: 目标合集编号，0 表示全局根目录。
        target_collection_name: 目标合集名称。
        expected_public_id: 移动后预期的公开 ID。
        source_snapshot: 源条目与文件身份快照，执行时校验用；不含时为 None。
    """

    entry_id: int
    old_public_id: MemePublicId
    source_collection_name: str
    target_collection_id: int
    target_collection_name: str
    expected_public_id: MemePublicId
    source_snapshot: MoveSourceSnapshot | None = None


@dataclass(frozen=True, slots=True)
class MoveResult:
    """跨合集移动成功后的持久状态快照。

    Attributes:
        entry_id: 被移动条目的内部 id。
        old_public_id: 移动前的公开 ID。
        new_public_id: 移动后的公开 ID。
        target_collection_name: 目标合集名称。
        old_image_path: 移动前的图片相对路径。
        new_image_path: 移动后的图片相对路径。
    """

    entry_id: int
    old_public_id: MemePublicId
    new_public_id: MemePublicId
    target_collection_name: str
    old_image_path: str
    new_image_path: str


@dataclass(slots=True)
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


@dataclass(slots=True)
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


@dataclass(slots=True)
class AddTagResult:
    """add_tags() 的返回结果。

    Attributes:
        entry_id: 被修改的条目 id。
        added_tags: 本次新增的标签列表。
        all_tags: 修改后的全部标签列表。
    """

    entry_id: int
    added_tags: list[str]
    all_tags: list[str]


@dataclass(slots=True)
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


@dataclass(slots=True)
class IndexInfo:
    """info() 的返回结果。

    Attributes:
        entry_count: 全库索引条目总数。
        current_entry_count: 当前搜索范围内的条目数。
        collection_count: 普通合集数量。
        speaker_ranking: 当前范围内 speaker 使用频率排行（speaker, count）。
        status: 索引状态描述。
    """

    entry_count: int
    current_entry_count: int
    collection_count: int
    speaker_ranking: list[tuple[str | None, int]]
    status: str


class DuplicateTextError(RuntimeError):
    """edit_text 要修改的文本已被其他条目使用。"""


class CollectionSelectionExpiredError(RuntimeError):
    """交互会话保存的合集选择已失效。"""


class OcrProvider(Protocol):
    """OCR 服务提供者协议。ocr() 返回去除所有空白后的文本。"""

    async def ocr(self, image_path: str) -> str: ...

    async def close(self) -> None:
        """关闭 provider 占用的资源。"""
        ...


@dataclass(frozen=True, slots=True)
class FileSystemSnapshot:
    """刷新开始时的文件系统快照。

    Attributes:
        files: POSIX 相对路径到一级合集目录名的映射；根目录图片映射为 None。
        directories: 实际存在的非隐藏一级普通目录名称。
        directories_with_images: 递归包含受支持图片的一级目录名称。
    """

    files: dict[str, str | None]
    directories: set[str]
    directories_with_images: set[str]


@dataclass(slots=True)
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因 text 命中已有条目而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        collections_added: 新登记的普通合集数量。
        collections_deleted: 因一级目录消失而删除的普通合集数量。
        scopes_reset: 因合集删除而回退到全部合集的聊天窗口数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    collections_added: int = 0
    collections_deleted: int = 0
    scopes_reset: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AddResult:
    """add() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID（int）；无文字移图场景为 None。
        reason: 结果类别：added / replaced / no_text。
        text: OCR 文本（无空格）。
        public_id: 新增或替换后持久条目的公开 ID；无文字时为 None。
        collection_name: 新增或替换后持久条目的合集名称；无文字时为 None。
        replaced_image_path: reason="replaced" 时为被替换旧图路径，否则 None。
        archived_path: reason="replaced" 时为旧图归档后的完整路径，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
        speaker: ADD 时写入的说话人（无文字移图时为 None）。
        tags: ADD 时写入的标签列表（无文字移图时为空列表）。
    """

    entry_id: int | None
    reason: str
    text: str = ""
    public_id: MemePublicId | None = None
    collection_name: str | None = None
    replaced_image_path: str | None = None
    archived_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
