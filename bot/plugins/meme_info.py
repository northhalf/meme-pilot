"""/info 命令插件 — 显示机器人统计与状态信息，支持查看单条表情包详情。

授权用户在私聊或群聊 @bot 中发送 /info [id]，Bot 返回索引统计、当前状态、
本机内存/CPU/进程内存占用；带 id 时返回该表情包的详细信息。
"""

import asyncio
import logging
from pathlib import Path

import humanize
import psutil
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.metadata_store import MemeEntry
from bot.session import session_manager
from bot.log_context import generate_request_id, set_request_id

logger = logging.getLogger(__name__)

info_cmd = on_command("info", rule=to_me(), priority=5, block=True)


def _build_detail_reply(entry: MemeEntry) -> str:
    """组装 /info <id> 的详情回复。

    Args:
        entry: 表情包元数据条目。

    Returns:
        包含 id、文本、文件名、大小、说话人、标签的多行文本。
    """
    image_path = Path(MEMES_DIR) / entry.image_path
    try:
        size_text = humanize.naturalsize(
            image_path.stat().st_size, binary=True, format="%.2f"
        )
    except FileNotFoundError:
        size_text = "文件不存在"
    except Exception:
        logger.warning("获取文件大小失败: %s", image_path, exc_info=True)
        size_text = "获取失败"

    speaker_text = entry.speaker if entry.speaker else "无"
    tags_text = ", ".join(entry.tags) if entry.tags else "无"

    return (
        f"id: {entry.id}\n"
        f"文本：{entry.text}\n"
        f"文件名：{entry.image_path}\n"
        f"大小：{size_text}\n"
        f"说话人：{speaker_text}\n"
        f"标签：{tags_text}"
    )


@info_cmd.handle()
async def handle_info(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """/info 命令处理入口。

    流程：授权校验 → 解析可选 id → 有效 id 则查询详情；否则返回总体统计。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 消息事件（私聊或群聊 @bot）。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入）。
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

            # 解析可选 id
            raw = args.extract_plain_text().strip()
            entry_id: int | None = None
            if raw:
                try:
                    entry_id = int(raw.split()[0])
                except ValueError:
                    entry_id = None

            # 有效 id 分支：持读锁查询；超时或异常均按设计处理
            if entry_id is not None:
                try:
                    entry = await index_manager.get_entry(entry_id)
                except asyncio.TimeoutError:
                    logger.info("用户 %s 的 /info %s 等待读锁超时", user_id, entry_id)
                    await matcher.finish("索引更新较慢，请稍后再试")
                    return
                except Exception:
                    logger.exception("获取条目详情失败: entry_id=%s", entry_id)
                    entry = None
                else:
                    if entry is not None:
                        await matcher.finish(_build_detail_reply(entry))
                        return
                # id 无效或不存在时回退到总体信息分支

            # 总体信息分支
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
                    f"{humanize.naturalsize(mem.used, binary=True, format='%.0f')} / "
                    f"{humanize.naturalsize(mem.total, binary=True, format='%.0f')} ({mem.percent}%)"
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

            try:
                process_mem_text = humanize.naturalsize(
                    psutil.Process().memory_info().rss, binary=True, format="%.0f"
                )
            except Exception:
                logger.warning("获取进程内存失败", exc_info=True)
                process_mem_text = "获取失败"

            lines = [
                f"表情包数量：{info.entry_count}",
                "排行（前 10）：",
                *ranking_lines,
                f"当前机器人状态：{info.status}",
                f"内存占用：{mem_text}",
                f"进程内存：{process_mem_text}",
                f"CPU占用：{cpu_text}",
            ]

            await matcher.finish("\n".join(lines))
        except asyncio.CancelledError:
            raise FinishedException
