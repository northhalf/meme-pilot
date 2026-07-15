"""插件合集与公开 ID 共享适配单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.collection_manager import (
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.engine.metadata_store import MemeEntry
from bot.session import ChatScope


def _make_event() -> MagicMock:
    """创建私聊消息事件。"""
    event = MagicMock()
    event.get_user_id.return_value = "12345"
    event.message_type = "private"
    return event


@pytest.mark.asyncio
async def test_resolve_entry_argument_passes_scope_and_raw_id() -> None:
    """共享解析器应保留原始 ID 并传入当前 ChatScope。"""
    from bot.plugins._collection_utils import resolve_entry_argument

    event = _make_event()
    entry = MemeEntry(
        id=42,
        image_path="新三国/a.webp",
        text="测试",
        collection_id=1,
        local_id=3,
        collection_name="新三国",
    )
    manager = MagicMock()
    manager.resolve_entry = AsyncMock(return_value=entry)

    with patch("bot.plugins._collection_utils.get_index_manager", return_value=manager):
        result = await resolve_entry_argument(event, "01.003")

    assert result is entry
    manager.resolve_entry.assert_awaited_once_with(
        ChatScope.from_event(event), "01.003"
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            ShortIdUnavailableError("003"),
            "全部合集模式下请使用完整 ID，例如 1.3",
        ),
        (
            InvalidPublicIdError("１.２"),
            "表情包 ID 格式错误，请使用“合集编号.局部编号”，例如 1.3",
        ),
        (MemeNotFoundError("01.003"), "未找到 ID 为 01.003 的表情包"),
        (ValueError("other"), "表情包 ID 无效"),
    ],
)
def test_public_id_error_message_maps_domain_errors(
    exc: ValueError, expected: str
) -> None:
    """领域异常应按继承顺序映射为精确用户提示。"""
    from bot.plugins._collection_utils import public_id_error_message

    assert public_id_error_message(exc) == expected


def test_not_found_message_does_not_depend_on_exception_string_format() -> None:
    """未找到提示应读取异常参数，不依赖 ValueError 的字符串格式。"""
    from bot.plugins._collection_utils import public_id_error_message

    class DecoratedNotFoundError(MemeNotFoundError):
        def __str__(self) -> str:
            return f"MemeNotFoundError(public_id={self.args[0]!r})"

    exc = DecoratedNotFoundError("01.003")

    assert public_id_error_message(exc) == "未找到 ID 为 01.003 的表情包"
