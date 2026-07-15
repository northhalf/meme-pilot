"""插件层共享的合集和公开 ID 适配。"""

from nonebot.adapters.onebot.v11 import MessageEvent

from bot.app_state import get_index_manager
from bot.engine.collection_manager import (
    InvalidPublicIdError,
    MemeNotFoundError,
    ShortIdUnavailableError,
)
from bot.engine.metadata_store import MemeEntry
from bot.session import ChatScope


async def resolve_entry_argument(event: MessageEvent, raw_id: str) -> MemeEntry:
    """按当前聊天作用域解析用户输入并读取条目。

    Args:
        event: 当前 OneBot 消息事件。
        raw_id: 用户输入的完整公开 ID 或当前普通合集短号。

    Returns:
        解析到的表情包条目。

    Raises:
        InvalidPublicIdError: 公开 ID 格式或数值范围无效。
        ShortIdUnavailableError: 全部合集模式下使用短号。
        MemeNotFoundError: 未找到对应条目。
    """
    scope = ChatScope.from_event(event)
    return await get_index_manager().resolve_entry(scope, raw_id)


def public_id_error_message(exc: ValueError) -> str:
    """把公开 ID 领域异常转换为用户提示。

    Args:
        exc: 公开 ID 解析或查询抛出的领域异常。

    Returns:
        可直接回复给用户的中文提示。
    """
    if isinstance(exc, ShortIdUnavailableError):
        return "全部合集模式下请使用完整 ID，例如 1.3"
    if isinstance(exc, InvalidPublicIdError):
        return "表情包 ID 格式错误，请使用“合集编号.局部编号”，例如 1.3"
    if isinstance(exc, MemeNotFoundError):
        raw_id = exc.args[0] if exc.args else "未知"
        return f"未找到 ID 为 {raw_id} 的表情包"
    return "表情包 ID 无效"
