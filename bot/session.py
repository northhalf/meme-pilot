"""共享会话管理模块。

管理 /add、/search 等命令的待处理会话，
支持跨命令的会话覆盖（新命令取消旧命令）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from nonebot.matcher import Matcher

logger = logging.getLogger(__name__)


@dataclass
class PendingSession:
    """待处理会话。

    Attributes:
        matcher: NoneBot2 Matcher 实例。
        cancelled: 是否已被新命令取消。
        type: 命令类型，如 "add" 或 "search"。
    """

    matcher: Matcher
    cancelled: bool = False
    type: str = "add"


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
