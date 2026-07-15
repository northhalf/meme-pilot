"""表情包合集名称、公开 ID 与 ChatScope 选择解析。"""

import re
from typing import Protocol

from .types import (
    ALL_COLLECTIONS_NAME,
    GLOBAL_COLLECTION_ID,
    CollectionSelection,
    CollectionSummary,
    MemeCollection,
    MemePublicId,
    ScopeLike,
)

_FULL_ID_RE = re.compile(r"^[0-9]+\.[0-9]+$", re.ASCII)
_SHORT_ID_RE = re.compile(r"^[0-9]+$", re.ASCII)


def _try_parse_ascii_integer(text: str) -> int | None:
    """尝试转换 ASCII 整数字符串，转换受限时返回 None。

    Args:
        text: 已通过 ASCII 纯数字校验的字符串。

    Returns:
        转换成功时返回整数，超出 Python 转换位数限制时返回 None。
    """
    normalized = text.lstrip("0") or "0"
    try:
        return int(normalized)
    except ValueError:
        return None


class CollectionNotFoundError(ValueError):
    """未找到指定合集。"""


class InvalidCollectionNameError(ValueError):
    """合集名称不能映射为安全的单层目录名。"""


class InvalidPublicIdError(ValueError):
    """公开 ID 语法或数值范围无效。"""


class ShortIdUnavailableError(InvalidPublicIdError):
    """全部合集模式下不能解析局部短号。"""


class MemeNotFoundError(ValueError):
    """未找到指定表情包条目。"""


class CollectionStoreProtocol(Protocol):
    """CollectionManager 所需的元数据接口。"""

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        """按编号返回合集。"""
        ...

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        """按精确名称返回合集。"""
        ...

    def list_collections(self) -> list[MemeCollection]:
        """返回所有普通合集。"""
        ...

    def get_selected_collection(self, scope: ScopeLike) -> int:
        """返回聊天作用域当前选择的合集编号。"""
        ...

    def set_selected_collection(self, scope: ScopeLike, collection_id: int) -> None:
        """保存聊天作用域当前选择的合集编号。"""
        ...

    def collection_entry_count(self, collection_id: int | None) -> int:
        """返回全库或指定合集的条目数。"""
        ...


class CollectionManager:
    """集中实现合集参数与公开 ID 规则。

    Attributes:
        _store: 元数据 Store
    """

    def __init__(self, store: CollectionStoreProtocol) -> None:
        """初始化合集管理器。

        Args:
            store: 提供合集、选择和统计数据的元数据 Store。
        """
        self._store = store

    def parse_meme_id(
        self, raw: str, *, selected_collection_id: int = 0
    ) -> MemePublicId:
        """解析完整公开 ID 或当前普通合集内的短号。

        Args:
            raw: 待解析的用户输入。
            selected_collection_id: 当前选择的合集编号。

        Returns:
            去除前导零后的公开 ID。

        Raises:
            ShortIdUnavailableError: 全部合集模式下使用了短号。
            InvalidPublicIdError: 输入语法或数值范围无效。
        """
        text = raw.strip()
        if _FULL_ID_RE.fullmatch(text):
            collection_text, local_text = text.split(".", maxsplit=1)
            collection_id = _try_parse_ascii_integer(collection_text)
            local_id = _try_parse_ascii_integer(local_text)
            if collection_id is None or local_id is None:
                raise InvalidPublicIdError(text)
            try:
                return MemePublicId(collection_id, local_id)
            except ValueError as exc:
                raise InvalidPublicIdError(text) from exc
        if _SHORT_ID_RE.fullmatch(text):
            if selected_collection_id == GLOBAL_COLLECTION_ID:
                raise ShortIdUnavailableError(text)
            local_id = _try_parse_ascii_integer(text)
            if local_id is None:
                raise InvalidPublicIdError(text)
            try:
                return MemePublicId(selected_collection_id, local_id)
            except ValueError as exc:
                raise InvalidPublicIdError(text) from exc
        raise InvalidPublicIdError(text)

    def resolve_collection(self, raw: str) -> MemeCollection:
        """按编号优先、精确名称兜底解析普通合集。

        Args:
            raw: 合集编号或名称。

        Returns:
            匹配的普通合集。

        Raises:
            CollectionNotFoundError: 编号和名称均未匹配到合集。
        """
        text = raw.strip()
        if _SHORT_ID_RE.fullmatch(text):
            collection_id = _try_parse_ascii_integer(text)
            if collection_id is not None:
                by_id = self._store.get_collection(collection_id)
                if by_id is not None:
                    return by_id
        by_name = self._store.get_collection_by_name(text)
        if by_name is None:
            raise CollectionNotFoundError(text)
        return by_name

    def resolve_selection(self, raw: str) -> CollectionSelection:
        """解析 `/switch` 目标并生成搜索过滤条件。

        Args:
            raw: 合集编号或名称。

        Returns:
            全部合集或普通合集选择。

        Raises:
            CollectionNotFoundError: 未匹配到选择目标。
        """
        text = raw.strip()
        if _SHORT_ID_RE.fullmatch(text) and not text.strip("0"):
            return CollectionSelection(0, ALL_COLLECTIONS_NAME)
        collection = self.resolve_collection(text)
        return CollectionSelection(collection.id, collection.name)

    def get_selected(self, scope: ScopeLike) -> CollectionSelection:
        """返回聊天作用域当前选择，失效选择自动回退到全部合集。

        Args:
            scope: 聊天作用域。

        Returns:
            当前有效的合集选择。
        """
        collection_id = self._store.get_selected_collection(scope)
        if collection_id == GLOBAL_COLLECTION_ID:
            return CollectionSelection(0, ALL_COLLECTIONS_NAME)
        collection = self._store.get_collection(collection_id)
        if collection is None:
            self._store.set_selected_collection(scope, GLOBAL_COLLECTION_ID)
            return CollectionSelection(0, ALL_COLLECTIONS_NAME)
        return CollectionSelection(collection.id, collection.name)

    def set_selected(self, scope: ScopeLike, collection_id: int) -> None:
        """保存聊天作用域当前选择的合集编号。

        Args:
            scope: 聊天作用域。
            collection_id: 要保存的合集编号。
        """
        self._store.set_selected_collection(scope, collection_id)

    def list_summaries(self, scope: ScopeLike) -> list[CollectionSummary]:
        """返回全部合集入口和各普通合集的统计摘要。

        Args:
            scope: 用于标记当前选择的聊天作用域。

        Returns:
            首项为全部合集、其后为普通合集的统计摘要。
        """
        selected = self.get_selected(scope).collection_id
        summaries = [
            CollectionSummary(
                collection_id=GLOBAL_COLLECTION_ID,
                name=ALL_COLLECTIONS_NAME,
                entry_count=self._store.collection_entry_count(None),
                selected=selected == GLOBAL_COLLECTION_ID,
            )
        ]
        summaries.extend(
            CollectionSummary(
                collection_id=collection.id,
                name=collection.name,
                entry_count=self._store.collection_entry_count(collection.id),
                selected=selected == collection.id,
            )
            for collection in self._store.list_collections()
        )
        return summaries
