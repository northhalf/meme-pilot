"""引擎层共用数据类型。"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .metadata_store import MemeEntry

GLOBAL_COLLECTION_ID = 0
GLOBAL_COLLECTION_NAME = "全局"
ALL_COLLECTIONS_NAME = "全部合集"


@dataclass(frozen=True, slots=True)
class MemePublicId:
    """用户可见的表情包复合 ID。

    Attributes:
        collection_id: 合集编号，0 表示全局根目录。
        local_id: 合集内正整数编号。
    """

    collection_id: int
    local_id: int

    def __post_init__(self) -> None:
        """校验公开 ID 的数值范围。

        Raises:
            ValueError: 合集编号为负数或合集内编号小于 1。
        """
        if self.collection_id < 0 or self.local_id < 1:
            raise ValueError("公开 ID 数值范围无效")

    def __str__(self) -> str:
        """返回规范化的公开 ID 字符串。"""
        return f"{self.collection_id}.{self.local_id}"


@dataclass(frozen=True, slots=True)
class MemeCollection:
    """普通表情包合集。

    Attributes:
        id: 合集编号，正整数。
        name: 合集名称。
    """

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class CollectionSelection:
    """ChatScope 当前合集及对应搜索过滤。

    Attributes:
        collection_id: 合集编号，0 表示全部合集。
        name: 合集名称。
    """

    collection_id: int
    name: str

    @property
    def search_filter(self) -> int | None:
        """返回与合集选择对应的搜索过滤条件。

        Returns:
            普通合集返回其编号；全部合集（collection_id 为 0）返回 None。
        """
        return (
            None if self.collection_id == GLOBAL_COLLECTION_ID else self.collection_id
        )


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    """用于 `/switch` 列表展示的合集统计。

    Attributes:
        collection_id: 合集编号，0 表示全部合集。
        name: 合集名称。
        entry_count: 合集条目数。
        selected: 是否为当前聊天作用域的选择。
    """

    collection_id: int
    name: str
    entry_count: int
    selected: bool


@dataclass(slots=True)
class SearchResult:
    """单条关键词搜索结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本（无空格）。
        similarity: 相似度分数，0-100。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
        collection_id: 所属合集编号，0 表示全局根目录。
        local_id: 合集内正整数编号。
        collection_name: 所属合集名称。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
    collection_id: int = 0
    local_id: int = 1
    collection_name: str = GLOBAL_COLLECTION_NAME

    @property
    def public_id(self) -> MemePublicId:
        """返回用户可见的复合 ID。

        Returns:
            当前结果所属合集编号和合集内编号。
        """
        return MemePublicId(self.collection_id, self.local_id)

    @classmethod
    def from_entry(cls, entry: "MemeEntry", similarity: float) -> "SearchResult":
        """从 MemeEntry 构造 SearchResult。

        Args:
            entry: 表情包元数据条目。
            similarity: 相似度分数。

        Returns:
            携带完整合集身份的搜索结果实例。
        """
        return cls(
            entry_id=entry.id,
            image_path=entry.image_path,
            text=entry.text,
            similarity=similarity,
            speaker=entry.speaker,
            tags=list(entry.tags),
            collection_id=entry.collection_id,
            local_id=entry.local_id,
            collection_name=entry.collection_name,
        )
