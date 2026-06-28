"""共享会话管理模块。

管理 /add、/search 等命令的待处理会话，
支持跨命令的会话覆盖（新命令取消旧命令）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.matcher import Matcher

from bot.config import read_session_timeout

logger = logging.getLogger(__name__)


@dataclass
class PendingSession:
    """待处理会话。

    Attributes:
        matcher: NoneBot2 Matcher 实例。
        cancelled: 是否已被新命令取消。
        type: 命令类型，如 "add" 或 "search"。
        timeout_task: 超时 asyncio.Task 引用，用于在 got 中取消。
    """

    matcher: Matcher
    cancelled: bool = False
    type: str = "add"
    timeout_task: asyncio.Task | None = None


# 模块级会话字典：user_id → PendingSession
pending_sessions: dict[str, PendingSession] = {}


def check_and_cancel(user_id: str, new_type: str) -> str | None:
    """检查旧会话并标记取消。

    Args:
        user_id: 用户 ID。
        new_type: 新命令类型。

    Returns:
        取消提示文本，无旧会话返回 None。
    """
    if user_id not in pending_sessions:
        return None

    old = pending_sessions[user_id]
    old.cancelled = True
    logger.info(
        "取消用户 %s 的旧会话: type=%s, 新命令=%s",
        user_id,
        old.type,
        new_type,
    )
    return f"已取消上一条未完成的操作，开始新的 /{new_type}"


def register(user_id: str, matcher: Matcher, type: str) -> None:
    """注册新会话。

    Args:
        user_id: 用户 ID。
        matcher: NoneBot2 Matcher 实例。
        type: 命令类型。
    """
    pending_sessions[user_id] = PendingSession(matcher=matcher, type=type)
    logger.debug("注册会话: user=%s, type=%s", user_id, type)


def cancel(user_id: str) -> None:
    """移除会话。

    Args:
        user_id: 用户 ID。
    """
    if user_id in pending_sessions:
        del pending_sessions[user_id]
        logger.debug("移除会话: user=%s", user_id)


def cancel_timeout_task(user_id: str) -> None:
    """取消用户会话的超时 asyncio Task。

    在 got 处理函数确认用户已有效响应后调用，
    防止 timeout_session 在后台继续计时并发送超时消息。

    Args:
        user_id: 用户 ID。
    """
    session = pending_sessions.get(user_id)
    if session is not None and session.timeout_task is not None:
        session.timeout_task.cancel()
        session.timeout_task = None


def is_cancelled(user_id: str) -> bool:
    """检查会话是否已被取消。

    Args:
        user_id: 用户 ID。

    Returns:
        True 表示已取消。
    """
    session = pending_sessions.get(user_id)
    if session is None:
        return False
    return session.cancelled


async def timeout_session(
    bot: Bot,
    event: Event,
    user_id: str,
    message: str,
    *,
    on_cleanup: Callable[[], Any | Awaitable[Any]] | None = None,
    timeout: int | None = None,
) -> None:
    """会话超时检查任务。

    等待指定秒数后，如果用户会话仍然活跃（未被用户完成或新命令取消），
    则发送超时提示消息并清理会话状态。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 原始消息事件（用于确定回复目标）。
        user_id: 用户 ID。
        message: 超时提示消息。
        on_cleanup: 可选的清理回调（如释放索引锁），支持同步和异步。
        timeout: 超时秒数，为 None 时从 SESSION_EXPIRE_TIMEOUT 环境变量读取。
    """
    if timeout is None:
        timeout = read_session_timeout()
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return  # 任务被外部取消（got 已接手处理），静默退出
    if not is_cancelled(user_id) and user_id in pending_sessions:
        logger.info("用户 %s 的会话超时（%d 秒）", user_id, timeout)
        cancel(user_id)
        if on_cleanup is not None:
            result = on_cleanup()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
        try:
            await bot.send(event, message)
        except Exception:
            logger.debug("发送超时消息失败", exc_info=True)
