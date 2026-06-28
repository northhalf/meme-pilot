"""/refresh 命令插件 — 增量更新表情包索引。

授权用户在私聊中发送 /refresh，触发 IndexManager.sync_with_filesystem()
执行按文件名同步的增量刷新。使用全局索引更新锁，锁占用期间拒绝服务。
"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.session import activate_chat, deactivate_chat

logger = logging.getLogger(__name__)

refresh_cmd = on_command("refresh", rule=to_me(), priority=5, block=True)


@refresh_cmd.handle()
async def handle_refresh(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/refresh 命令处理入口。

    流程：授权校验 → 群聊拦截 → 获取锁 → 执行同步 → 释放锁 → 回复摘要。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件（仅限私聊）。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /refresh", user_id)

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "refresh")
        return

    # 群聊拦截：/refresh 仅限私聊使用
    if event.message_type != "private":
        logger.info("用户 %s 在群聊中调用 /refresh，已拒绝", user_id)
        await matcher.finish("此命令仅限私聊使用")
        return

    # 会话激活
    if not activate_chat(user_id, "refresh", matcher):
        await matcher.finish("已有命令在处理中，请先 /cancel")
        return

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        deactivate_chat(user_id)
        await matcher.finish("服务未就绪，请稍后再试")
        return

    # 尝试获取全局索引更新锁
    if not await index_manager.acquire_lock():
        logger.info("用户 %s 的 /refresh 被拒绝：索引正在更新", user_id)
        deactivate_chat(user_id)
        await matcher.finish("索引正在更新，请稍后再试")
        return

    try:
        await bot.send(event, "正在刷新索引，请稍候...")
        result = await index_manager.sync_with_filesystem()
    except Exception:
        logger.exception("sync_with_filesystem 执行失败")
        deactivate_chat(user_id)
        await matcher.finish("索引刷新失败，请查看日志")
        return
    finally:
        index_manager.release_lock()
        deactivate_chat(user_id)

    # 无任何条目且无新增 → 可能 memes/ 为空
    if index_manager.entry_count == 0 and result.added == 0:
        deactivate_chat(user_id)
        await matcher.finish("表情包目录为空，请先添加图片并执行 /refresh")
        return

    # 格式化摘要
    lines = [
        "索引刷新完成 ✅",
        f"新增: {result.added} | 删除: {result.deleted} "
        f"| 去重: {result.deduped} | 无文字移走: {result.no_text_moved} "
        f"| 失败: {len(result.failed)}",
    ]

    if result.failed:
        shown = result.failed[:10]
        lines.append(f"失败文件: {', '.join(shown)}")

    deactivate_chat(user_id)
    await matcher.finish("\n".join(lines))
