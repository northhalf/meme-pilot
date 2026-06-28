"""共享会话管理模块。

管理用户的聊天会话（ChatSession）和选择会话（SelectionSession），
支持 /cancel 和 /help 在任何状态下旁路触发。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher

from bot.config import read_session_timeout

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """每个用户一个，持久存在，首次访问时懒创建。

    Attributes:
        session_id: UUID，首次创建时永久固定。
        active: True 表示有命令正在处理。
        command_type: 命令类型，"add"/"search"/"ai"/"refresh"。
        matcher: 当前命令的 NoneBot2 Matcher。
        current_task: 异步任务引用，handle/got 入口通过 activate_chat 设置。
    """

    session_id: str
    active: bool = False
    command_type: str | None = None
    matcher: Matcher | None = None
    current_task: asyncio.Task | None = None


@dataclass
class SelectionSession:
    """选择会话，至多一个，是 ChatSession 的子集。

    Attributes:
        selection_id: UUID，每次创建选择时生成，用于超时双重校验。
        timeout_task: 超时监控任务引用。
    """

    selection_id: str
    timeout_task: asyncio.Task | None = None


# 模块级字典
chat_sessions: dict[str, ChatSession] = {}
selection_sessions: dict[str, SelectionSession] = {}


def get_or_create_chat(user_id: str) -> ChatSession:
    """首次访问时创建并存储 ChatSession，之后复用。

    Args:
        user_id: 用户 ID。

    Returns:
        该用户的 ChatSession 实例。
    """
    if user_id not in chat_sessions:
        chat_sessions[user_id] = ChatSession(session_id=str(uuid.uuid4()))
    return chat_sessions[user_id]


def activate_chat(
    user_id: str,
    command_type: str,
    matcher: Matcher,
) -> bool:
    """激活聊天会话。

    - 设置 active=True, matcher, command_type, current_task=asyncio.current_task()
    - 返回 True=成功, False=已在活跃（调用方应拒绝新命令）
    - 注意：NoneBot2 的 handle() 和 got() 运行在不同 asyncio task 中，
      各自的 handler 入口都需要调用 activate_chat 更新 current_task。
    - handler 的 finally 块中调用 deactivate_chat 清空。

    Args:
        user_id: 用户 ID。
        command_type: 命令类型。
        matcher: NoneBot2 Matcher。

    Returns:
        True 表示成功激活，False 表示已有活跃会话。
    """
    chat = get_or_create_chat(user_id)
    if chat.active:
        return False
    chat.active = True
    chat.command_type = command_type
    chat.matcher = matcher
    chat.current_task = asyncio.current_task()
    return True


def deactivate_chat(user_id: str) -> None:
    """重置聊天会话为空闲状态。

    Args:
        user_id: 用户 ID。
    """
    chat = chat_sessions.get(user_id)
    if chat is None:
        return
    chat.active = False
    chat.command_type = None
    chat.matcher = None
    chat.current_task = None


def create_selection(
    user_id: str,
    selection_id: str,
    timeout_task: asyncio.Task,
) -> None:
    """创建选择会话。覆盖同一用户的旧选择会话。

    Args:
        user_id: 用户 ID。
        selection_id: 选择会话 ID（UUID 字符串）。
        timeout_task: 超时监控任务。
    """
    selection_sessions[user_id] = SelectionSession(
        selection_id=selection_id,
        timeout_task=timeout_task,
    )


def remove_selection(user_id: str) -> SelectionSession | None:
    """移除选择会话，返回旧会话（用于取消 timeout_task）。

    Args:
        user_id: 用户 ID。

    Returns:
        被移除的选择会话，不存在时返回 None。
    """
    return selection_sessions.pop(user_id, None)


def get_selection(user_id: str) -> SelectionSession | None:
    """查询用户的选择会话。

    Args:
        user_id: 用户 ID。

    Returns:
        该用户的选择会话，不存在时返回 None。
    """
    return selection_sessions.get(user_id)


async def execute_cancel(user_id: str) -> str | None:
    """执行取消逻辑。

    1. 检查是否有活跃会话，无则返回 None
    2. current_task.cancel()（非当前 task 且未完成时）
    3. remove_selection() + 取消 timeout_task（若有）
    4. 在旧 matcher 上 finish()（发送"会话已取消"到原上下文）
    5. deactivate_chat(user_id)

    Args:
        user_id: 用户 ID。

    Returns:
        str: 成功提示 "已取消 ✅"
        None: 无活跃会话，调用方自行发送提示
    """
    chat = chat_sessions.get(user_id)
    if not (chat and chat.active):
        return None

    # 防止自取消：同频道 /cancel 时 current_task 等于当前 task，跳过
    current = asyncio.current_task()
    if (
        chat.current_task
        and not chat.current_task.done()
        and chat.current_task is not current
    ):
        chat.current_task.cancel()

    # 移除选择会话 + 取消超时任务
    ss = selection_sessions.pop(user_id, None)
    if ss and ss.timeout_task and not ss.timeout_task.done():
        ss.timeout_task.cancel()

    # finish 老 matcher（发送取消消息到原上下文）
    if chat.matcher:
        try:
            await chat.matcher.finish("当前会话已取消")
        except FinishedException:
            pass

    deactivate_chat(user_id)
    return "已取消 ✅"


async def got_intercept_bypass(
    user_id: str,
    matcher: Matcher,
    text: str,
    HELP_TEXT: str,
) -> bool:
    """Got handler 入口统一拦截 /help 和 /cancel。

    内部 /cancel 分支委托给 execute_cancel。

    Args:
        user_id: 用户 ID。
        matcher: 当前 got handler 的 matcher。
        text: 用户消息文本。
        HELP_TEXT: 帮助文本常量。

    Returns:
        True 表示拦截到命令（调用方应 return），
        False 表示正常流程继续。
    """
    if text.startswith("/cancel ") or text == "/cancel":
        result = await execute_cancel(user_id)
        if result is None:
            await matcher.finish("当前没有活跃的会话")
        return True

    if text.startswith("/help ") or text == "/help":
        await matcher.send(HELP_TEXT)
        await matcher.reject(None)
        return True

    return False


async def timeout_session(
    bot: Bot,
    event: Event,
    user_id: str,
    selection_id: str,
    message: str,
    *,
    on_cleanup: Callable[[], Any | Awaitable[Any]] | None = None,
    timeout: int | None = None,
) -> None:
    """会话超时检查任务。

    超时后按 user_id + selection_id 双重校验。
    匹配则发送超时提示 + remove_selection + on_cleanup。
    不匹配（被新选择或 /cancel 覆盖）则静默退出。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 原始消息事件（用于确定回复目标）。
        user_id: 用户 ID。
        selection_id: 闭包捕获的选择会话 ID。
        message: 超时提示消息。
        on_cleanup: 可选的清理回调，支持同步和异步。
        timeout: 超时秒数，为 None 时从 SESSION_EXPIRE_TIMEOUT 读取。
    """
    if timeout is None:
        timeout = read_session_timeout()
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return  # 被外部取消，静默退出

    # 双重校验：仅当 selection_id 仍然匹配时才发送超时提示
    ss = selection_sessions.get(user_id)
    if ss is not None and ss.selection_id == selection_id:
        logger.info("用户 %s 的选择会话超时（%d 秒）", user_id, timeout)
        selection_sessions.pop(user_id, None)
        if on_cleanup is not None:
            result = on_cleanup()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
        try:
            await bot.send(event, message)
        except Exception:
            logger.debug("发送超时消息失败", exc_info=True)
