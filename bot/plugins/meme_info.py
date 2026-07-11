"""/info 命令插件 — 显示机器人统计与状态信息。

授权用户在私聊或群聊 @bot 中发送 /info，Bot 返回索引统计、
当前状态以及本机内存/CPU 占用。
"""

import asyncio
import logging

import psutil
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.session import session_manager
from bot.log_context import generate_request_id, set_request_id

logger = logging.getLogger(__name__)

info_cmd = on_command("info", rule=to_me(), priority=5, block=True)


@info_cmd.handle()
async def handle_info(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/info 命令处理入口。

    流程：授权校验 → 获取索引统计 → 读取硬件信息 → 组装回复。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件（私聊或群聊 @bot）。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /info", user_id)

        try:
            # 授权校验：/info 允许私聊和群聊 @bot
            if not is_authorized(user_id):
                log_unauthorized(user_id, "info")
                await matcher.finish(None)
                return

            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                await matcher.finish("服务未就绪，请稍后再试")
                return

            try:
                info = await index_manager.info()
            except Exception:
                logger.exception("获取索引信息失败")
                await matcher.finish("索引信息获取失败，请稍后再试")
                return

            # engine 只感知刷新态；"正在处理命令"属应用层语义，由插件层覆写
            if info.status == "空闲" and session_manager.has_active_session():
                info.status = "正在处理命令"

            # 读取硬件信息
            try:
                mem = psutil.virtual_memory()
                mem_text = (
                    f"{mem.used // (1024 * 1024)} MB / "
                    f"{mem.total // (1024 * 1024)} MB ({mem.percent}%)"
                )
            except Exception:
                logger.warning("获取内存信息失败", exc_info=True)
                mem_text = "获取失败"

            try:
                cpu_percent = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
                cpu_text = f"{cpu_percent}%"
            except Exception:
                logger.warning("获取 CPU 信息失败", exc_info=True)
                cpu_text = "获取失败"

            # 组装说话人排行（前 10）
            ranking_lines: list[str] = []
            for idx, (speaker, count) in enumerate(info.speaker_ranking, start=1):
                speaker_name = speaker if speaker is not None else "无"
                ranking_lines.append(f"  {idx}. {speaker_name} {count}")

            if not ranking_lines:
                ranking_lines.append("  暂无数据")

            lines = [
                f"表情包数量：{info.entry_count}",
                "排行（前 10）：",
                *ranking_lines,
                f"当前机器人状态：{info.status}",
                f"内存占用：{mem_text}",
                f"CPU占用：{cpu_text}",
            ]

            await matcher.finish("\n".join(lines))
        except asyncio.CancelledError:
            raise FinishedException
