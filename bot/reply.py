"""群聊消息引用回复工具模块。

提供 build_reply_text 与一组发送辅助函数，使群聊中的文本消息
自动带上 MessageSegment.reply，私聊或 message_id 缺失时退化为纯文本。
"""

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher


def build_reply_text(event: MessageEvent, text: str) -> Message | str:
    """构造群聊引用文本消息。

    当事件为群聊且 event.message_id 存在时，返回包含 reply segment 的 Message；
    否则返回原字符串，保持私聊或异常场景下的现有行为。

    Args:
        event: OneBot V11 消息事件。
        text: 要发送的纯文本内容。

    Returns:
        群聊场景下为 Message（含 reply + text），否则为原字符串。
    """
    message_id = getattr(event, "message_id", None)
    message_type = getattr(event, "message_type", None)
    if message_type == "group" and message_id is not None:
        return Message([MessageSegment.reply(message_id), MessageSegment.text(text)])
    return text


async def finish(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """调用 matcher.finish 发送已包装 reply 的文本消息。

    Args:
        event: OneBot V11 消息事件。
        matcher: 当前 NoneBot2 Matcher 实例。
        text: 要发送的纯文本内容。
    """
    await matcher.finish(build_reply_text(event, text))


async def send(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """调用 matcher.send 发送已包装 reply 的文本消息。

    Args:
        event: OneBot V11 消息事件。
        matcher: 当前 NoneBot2 Matcher 实例。
        text: 要发送的纯文本内容。
    """
    await matcher.send(build_reply_text(event, text))


async def reject(event: MessageEvent, matcher: Matcher, text: str) -> None:
    """调用 matcher.reject 发送已包装 reply 的文本消息。

    Args:
        event: OneBot V11 消息事件。
        matcher: 当前 NoneBot2 Matcher 实例。
        text: 要发送的纯文本内容。
    """
    await matcher.reject(build_reply_text(event, text))


async def bot_send(event: MessageEvent, bot: Bot, text: str) -> None:
    """调用 bot.send 发送已包装 reply 的文本消息（用于超时任务）。

    Args:
        event: OneBot V11 消息事件。
        bot: 当前 OneBot V11 Bot 实例。
        text: 要发送的纯文本内容。
    """
    await bot.send(event, build_reply_text(event, text))
