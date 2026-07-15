"""CollectionManager 单元测试。"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from bot.engine.metadata_store import MetadataStore
from bot.engine.collection_manager import (
    CollectionManager,
    CollectionNotFoundError,
    InvalidPublicIdError,
    ShortIdUnavailableError,
)
from bot.engine.types import (
    ALL_COLLECTIONS_NAME,
    GLOBAL_COLLECTION_ID,
    GLOBAL_COLLECTION_NAME,
    CollectionSelection,
    MemeCollection,
    MemePublicId,
    ScopeLike,
)
from bot.session import ChatScope


class FakeCollectionStore:
    """仅实现 CollectionManager 所需接口的内存 Store。"""

    def __init__(self) -> None:
        self.collections = {
            1: MemeCollection(id=1, name="新三国"),
            2: MemeCollection(id=2, name="1"),
        }
        self.selected: dict[tuple[int, str, int], int] = {}
        self.entry_counts: dict[int | None, int] = {None: 10, 1: 3, 2: 7}

    def get_collection(self, collection_id: int) -> MemeCollection | None:
        """按编号返回合集。"""
        return self.collections.get(collection_id)

    def get_collection_by_name(self, name: str) -> MemeCollection | None:
        """按区分大小写的精确名称返回合集。"""
        return next(
            (
                collection
                for collection in self.collections.values()
                if collection.name == name
            ),
            None,
        )

    def list_collections(self) -> list[MemeCollection]:
        """按存储顺序返回普通合集。"""
        return list(self.collections.values())

    def get_selected_collection(self, scope: ScopeLike) -> int:
        """返回 ChatScope 的持久选择，默认选择全部合集。"""
        return self.selected.get(
            (scope.user_id, scope.chat_type, scope.chat_id), GLOBAL_COLLECTION_ID
        )

    def set_selected_collection(self, scope: ScopeLike, collection_id: int) -> None:
        """保存 ChatScope 的合集选择。"""
        self.selected[(scope.user_id, scope.chat_type, scope.chat_id)] = collection_id

    def collection_entry_count(self, collection_id: int | None) -> int:
        """返回全库或指定合集的条目数。"""
        return self.entry_counts[collection_id]


def test_collection_constants_distinguish_storage_and_selection_names() -> None:
    assert GLOBAL_COLLECTION_ID == 0
    assert GLOBAL_COLLECTION_NAME == "全局"
    assert ALL_COLLECTIONS_NAME == "全部合集"


def test_parse_full_id_accepts_leading_zeroes_and_normalizes_output() -> None:
    manager = CollectionManager(FakeCollectionStore())

    public_id = manager.parse_meme_id(" 01.002 ", selected_collection_id=0)

    assert public_id == MemePublicId(collection_id=1, local_id=2)
    assert str(public_id) == "1.2"


def test_parse_short_id_uses_current_collection() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.parse_meme_id("002", selected_collection_id=1) == MemePublicId(1, 2)


def test_parse_short_id_normalizes_long_leading_zeroes() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.parse_meme_id(
        "0" * 5000 + "2", selected_collection_id=1
    ) == MemePublicId(1, 2)


def test_parse_full_id_normalizes_long_collection_leading_zeroes() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.parse_meme_id(
        "0" * 5000 + "1.2", selected_collection_id=0
    ) == MemePublicId(1, 2)


def test_parse_full_id_normalizes_long_local_leading_zeroes() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.parse_meme_id(
        "1." + "0" * 5000 + "2", selected_collection_id=0
    ) == MemePublicId(1, 2)


@pytest.mark.parametrize(
    "raw",
    ["1" * 5000, "1" * 5000 + ".2", "1." + "2" * 5000],
)
def test_parse_meme_id_rejects_long_nonzero_values(raw: str) -> None:
    manager = CollectionManager(FakeCollectionStore())

    with pytest.raises(InvalidPublicIdError):
        manager.parse_meme_id(raw, selected_collection_id=1)


def test_parse_short_id_rejects_all_collections_scope() -> None:
    manager = CollectionManager(FakeCollectionStore())

    with pytest.raises(ShortIdUnavailableError):
        manager.parse_meme_id("2", selected_collection_id=0)


@pytest.mark.parametrize(
    "raw",
    ["", "1.0", "+1.2", "1.2.3", "１.２", ".2", "1.", "1e2", "1e2.3"],
)
def test_parse_meme_id_rejects_invalid_syntax(raw: str) -> None:
    manager = CollectionManager(FakeCollectionStore())

    with pytest.raises(InvalidPublicIdError):
        manager.parse_meme_id(raw, selected_collection_id=1)


def test_numeric_target_prefers_collection_id_then_exact_name() -> None:
    store = FakeCollectionStore()
    manager = CollectionManager(store)

    assert manager.resolve_collection("1").name == "新三国"
    del store.collections[1]
    assert manager.resolve_collection("1").name == "1"


def test_collection_name_match_is_exact_and_case_sensitive() -> None:
    store = FakeCollectionStore()
    store.collections[3] = MemeCollection(id=3, name="Meme")
    manager = CollectionManager(store)

    assert manager.resolve_collection(" Meme ") == MemeCollection(id=3, name="Meme")
    with pytest.raises(CollectionNotFoundError):
        manager.resolve_collection("meme")


def test_zero_resolves_to_all_collections_selection() -> None:
    manager = CollectionManager(FakeCollectionStore())

    selection = manager.resolve_selection("000")

    assert selection == CollectionSelection(collection_id=0, name="全部合集")


def test_long_zero_resolves_to_all_collections_selection() -> None:
    manager = CollectionManager(FakeCollectionStore())

    selection = manager.resolve_selection("0" * 5000)

    assert selection == CollectionSelection(0, "全部合集")


def test_long_leading_zero_target_prefers_collection_id() -> None:
    manager = CollectionManager(FakeCollectionStore())
    raw = "0" * 5000 + "1"

    assert manager.resolve_collection(raw) == MemeCollection(id=1, name="新三国")
    assert manager.resolve_selection(raw) == CollectionSelection(1, "新三国")


def test_long_numeric_target_falls_back_to_exact_name() -> None:
    store = FakeCollectionStore()
    long_name = "1" * 5000
    store.collections[3] = MemeCollection(id=3, name=long_name)
    manager = CollectionManager(store)

    assert manager.resolve_collection(long_name) == MemeCollection(id=3, name=long_name)
    assert manager.resolve_selection(long_name) == CollectionSelection(3, long_name)


def test_unknown_long_numeric_target_raises_collection_not_found() -> None:
    manager = CollectionManager(FakeCollectionStore())

    with pytest.raises(CollectionNotFoundError):
        manager.resolve_selection("1" * 5000)


def test_regular_collection_selection_uses_collection_id_as_filter() -> None:
    manager = CollectionManager(FakeCollectionStore())

    assert manager.resolve_selection("新三国") == CollectionSelection(
        collection_id=1, name="新三国"
    )


def test_get_and_set_chat_scope_selection() -> None:
    store = FakeCollectionStore()
    manager = CollectionManager(store)
    scope = ChatScope(user_id=10001, chat_type="private", chat_id=10001)

    assert manager.get_selected(scope).collection_id == 0

    manager.set_selected(scope, 1)

    assert manager.get_selected(scope) == CollectionSelection(
        collection_id=1, name="新三国"
    )


def test_get_selected_resets_missing_persisted_collection() -> None:
    store = FakeCollectionStore()
    manager = CollectionManager(store)
    scope = ChatScope(user_id=10001, chat_type="group", chat_id=20001)
    store.selected[(scope.user_id, scope.chat_type, scope.chat_id)] = 99

    selection = manager.get_selected(scope)

    assert selection == CollectionSelection(0, "全部合集")
    assert store.get_selected_collection(scope) == 0


def test_list_summaries_includes_all_and_regular_collection_counts() -> None:
    store = FakeCollectionStore()
    manager = CollectionManager(store)
    scope = ChatScope(user_id=10001, chat_type="private", chat_id=10001)
    manager.set_selected(scope, 2)

    summaries = manager.list_summaries(scope)

    assert [summary.collection_id for summary in summaries] == [0, 1, 2]
    assert [summary.entry_count for summary in summaries] == [10, 3, 7]
    assert [summary.selected for summary in summaries] == [False, False, True]
    assert summaries[0].name == "全部合集"


def test_real_store_list_summaries_include_all_and_collection_counts(
    tmp_sqlite_path: Path,
) -> None:
    """真实 Store 摘要首项为全部合集，后续按合集编号统计。"""
    store = MetadataStore(str(tmp_sqlite_path))
    store.load()
    first = store.create_collection("合集一")
    second = store.create_collection("合集二")
    store.add("global.webp", "全局")
    store.add("first.webp", "甲", collection_id=first.id)
    store.add("second-1.webp", "乙", collection_id=second.id)
    store.add("second-2.webp", "丙", collection_id=second.id)
    manager = CollectionManager(store)
    scope = ChatScope(user_id=10001, chat_type="private", chat_id=10001)
    manager.set_selected(scope, second.id)

    summaries = manager.list_summaries(scope)

    assert [
        (summary.collection_id, summary.name, summary.entry_count)
        for summary in summaries
    ] == [
        (0, ALL_COLLECTIONS_NAME, 4),
        (first.id, first.name, 1),
        (second.id, second.name, 2),
    ]
    assert [summary.selected for summary in summaries] == [False, False, True]
    store.close()


def test_real_store_scope_selection_persists_and_deleted_collection_resets(
    tmp_sqlite_path: Path,
) -> None:
    """真实 Store 选择可重载，并在合集删除后持久回退到全部合集。"""
    scope = ChatScope(user_id=10001, chat_type="group", chat_id=20001)
    store = MetadataStore(str(tmp_sqlite_path))
    store.load()
    collection = store.create_collection("合集")
    manager = CollectionManager(store)

    manager.set_selected(scope, collection.id)
    store.close()
    store.load()
    assert manager.get_selected(scope) == CollectionSelection(
        collection.id, collection.name
    )

    assert store.delete_collection_and_reset_scopes(collection.id) == 1
    assert manager.get_selected(scope) == CollectionSelection(0, ALL_COLLECTIONS_NAME)
    store.close()
    store.load()
    assert manager.get_selected(scope) == CollectionSelection(0, ALL_COLLECTIONS_NAME)
    store.close()


def test_meme_public_id_rejects_invalid_numeric_ranges() -> None:
    with pytest.raises(ValueError, match="公开 ID 数值范围无效"):
        MemePublicId(collection_id=-1, local_id=1)
    with pytest.raises(ValueError, match="公开 ID 数值范围无效"):
        MemePublicId(collection_id=0, local_id=0)


def test_collection_value_types_are_immutable_and_slotted() -> None:
    collection = MemeCollection(id=1, name="新三国")

    with pytest.raises(FrozenInstanceError):
        setattr(collection, "name", "甄嬛传")
    assert not hasattr(collection, "__dict__")
