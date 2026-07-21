"""/refresh 命令插件 — 增量更新表情包索引。

授权用户在私聊中发送 /refresh，触发 IndexManager.refresh()
执行按文件名同步的增量刷新。
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.index_manager import IndexCorruptedError, RefreshInProgressError
from bot.log_context import generate_request_id, set_request_id
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)

refresh_cmd = on_command("refresh", rule=to_me(), priority=5, block=True, aliases={"r"})


@refresh_cmd.handle()
async def handle_refresh(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/refresh 命令处理入口。

    流程：授权校验 → 群聊拦截 → 执行同步 → 回复摘要。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件（仅限私聊）。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    scope = ChatScope.from_event(event)
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /refresh", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "refresh")
                await matcher.finish(None)
                return

            # 群聊拦截：/refresh 仅限私聊使用
            if event.message_type != "private":
                logger.info("用户 %s 在群聊中调用 /refresh，已拒绝", user_id)
                await reply_utils.finish(event, matcher, "此命令仅限私聊使用")
                return

            # 会话激活
            if not session_manager.activate_chat(scope, "refresh", matcher):
                await reply_utils.finish(
                    event, matcher, "已有命令在处理中，请先 /cancel"
                )
                return

            # 获取 IndexManager
            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "服务未就绪，请稍后再试")
                return

            try:
                await reply_utils.bot_send(event, bot, "正在刷新索引，请稍候...")
                result = await index_manager.refresh()
                logger.info("用户 %s 的 /refresh 完成", user_id)
                logger.info(
                    "/refresh 统计: 新增合集=%d, 删除合集=%d, 回退窗口=%d, "
                    "新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
                    result.collections_added,
                    result.collections_deleted,
                    result.scopes_reset,
                    result.added,
                    result.deleted,
                    result.deduped,
                    result.no_text_moved,
                    len(result.failed),
                )
            except RefreshInProgressError:
                logger.info("用户 %s 触发刷新但已有任务在运行", user_id)
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, "已有刷新任务在进行中，请稍后再试"
                )
                return
            except IndexCorruptedError:
                logger.exception("索引数据库损坏，已拒绝刷新")
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event,
                    matcher,
                    "索引数据库损坏，请修复 data/index.db 后重启 Bot",
                )
                return
            except Exception:
                logger.exception("索引刷新失败")
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(event, matcher, "索引刷新失败，请查看日志")
                return

            # 无任何条目且无新增 → 可能 memes/ 为空
            if index_manager.entry_count == 0 and result.added == 0:
                session_manager.deactivate_chat(scope)
                await reply_utils.finish(
                    event, matcher, "表情包目录为空，请先添加图片并执行 /refresh"
                )
                return

            # 格式化摘要
            lines = [
                "索引刷新完成 ✅",
                f"新增合集：{result.collections_added}",
                f"删除合集：{result.collections_deleted}",
                f"回退窗口：{result.scopes_reset}",
                f"新增: {result.added} | 删除: {result.deleted} "
                f"| 去重: {result.deduped} | 无文字移走: {result.no_text_moved} "
                f"| 失败: {len(result.failed)}",
            ]

            if result.failed:
                shown = result.failed[:10]
                lines.append(f"失败文件: {'，'.join(shown)}")

            session_manager.deactivate_chat(scope)
            await reply_utils.finish(event, matcher, "\n".join(lines))
        except asyncio.CancelledError:
            session_manager.deactivate_chat(scope)
            raise FinishedException
