"""/switch 命令插件 — 查看或切换当前表情包合集。"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.engine.collection_manager import CollectionNotFoundError
from bot.engine.types import CollectionSummary
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)

switch_cmd = on_command("switch", rule=to_me(), priority=5, block=True)


def _format_collection_list(summaries: list[CollectionSummary]) -> str:
    """格式化合集列表及当前选择。

    Args:
        summaries: 全部合集入口和普通合集的统计摘要。

    Returns:
        可直接回复用户的合集列表文本。
    """
    lines = ["表情包合集："]
    selected_name = "全部合集"

    if not summaries:
        lines.append("暂无可用合集")
    else:
        for summary in summaries:
            marker = "*" if summary.selected else " "
            if summary.collection_id == 0:
                count_text = f"共 {summary.entry_count} 张"
            else:
                count_text = f"{summary.entry_count} 张"
            lines.append(
                f"{marker} {summary.collection_id}. {summary.name}（{count_text}）"
            )
            if summary.selected:
                selected_name = summary.name

    lines.extend(
        [
            "",
            f"当前合集：{selected_name}",
            "使用 /switch <编号|名称> 切换",
        ]
    )
    return "\n".join(lines)


@switch_cmd.handle()
async def handle_switch(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """处理合集查看与切换命令。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊或群聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令后的完整参数消息。
    """
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)

    if not is_authorized(user_id):
        log_unauthorized(user_id, "switch")
        await matcher.finish(None)
        return

    if not session_manager.activate_chat(scope, "switch", matcher):
        await reply_utils.finish(event, matcher, "已有命令在处理中，请先 /cancel")
        return

    target = ""
    try:
        target = args.extract_plain_text().strip()
        if not target:
            summaries = await get_index_manager().list_collections(scope)
            await reply_utils.finish(event, matcher, _format_collection_list(summaries))
            return

        selection = await get_index_manager().switch_collection(scope, target)
        if selection.collection_id == 0:
            message = "已切换到：全部合集（0）"
        else:
            message = f"已切换到合集：{selection.name}（{selection.collection_id}）"
        await reply_utils.finish(event, matcher, message)
    except CollectionNotFoundError:
        await reply_utils.finish(
            event,
            matcher,
            f"未找到表情包合集：{target}\n发送 /switch 查看可用合集",
        )
    except asyncio.TimeoutError:
        await reply_utils.finish(event, matcher, "索引更新较慢，请稍后再试")
    finally:
        session_manager.deactivate_chat(scope)
