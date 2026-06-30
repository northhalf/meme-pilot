"""共享会话管理模块 — 管理聊天会话和选择会话。

提供 SessionManager 类封装所有会话状态操作，
以及模块级 session_manager 单例和 timeout_session 工具函数。
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Awaitable
import uuid

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


class SessionManager:
    """统一的会话管理器，封装 ChatSession 和 SelectionSession 的生命周期。"""

    def __init__(self) -> None:
        self._chat_sessions: dict[str, ChatSession] = {}
        self._selection_sessions: dict[str, SelectionSession] = {}

    # ── 核心会话状态管理 ──

    def get_or_create_chat(self, user_id: str) -> ChatSession:
        """首次访问时创建并存储 ChatSession，之后复用。

        Args:
            user_id: 用户 ID。

        Returns:
            该用户的 ChatSession 实例。
        """
        if user_id not in self._chat_sessions:
            self._chat_sessions[user_id] = ChatSession(session_id=str(uuid.uuid4()))
        return self._chat_sessions[user_id]

    def activate_chat(
        self,
        user_id: str,
        command_type: str,
        matcher: Matcher,
    ) -> bool:
        """激活聊天会话。

        - 设置 active=True, matcher, command_type, current_task=asyncio.current_task()
        - 返回 True=成功, False=已在活跃（调用方应拒绝新命令）
        - 注意：chat.active 为 True 时直接返回 False，不会更新任何字段。
          got 入口应使用 handler_context（with 语句）而非 activate_chat。
        - handler 的 finally 块中调用 deactivate_chat 清空。

        Args:
            user_id: 用户 ID。
            command_type: 命令类型。
            matcher: NoneBot2 Matcher。

        Returns:
            True 表示成功激活，False 表示已有活跃会话。
        """
        chat = self.get_or_create_chat(user_id)
        if chat.active:
            return False
        chat.active = True
        chat.command_type = command_type
        chat.matcher = matcher
        chat.current_task = asyncio.current_task()
        return True

    def deactivate_chat(self, user_id: str) -> None:
        """重置聊天会话为空闲状态。同时删除与之相关的选择会话。

        Args:
            user_id: 用户 ID。
        """
        self.remove_selection(user_id)
        chat = self._chat_sessions.get(user_id)
        if chat is None:
            return
        chat.active = False
        chat.command_type = None
        chat.matcher = None
        chat.current_task = None

    # ── 选择会话管理 ──

    def create_selection(
        self,
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
        self._selection_sessions[user_id] = SelectionSession(
            selection_id=selection_id,
            timeout_task=timeout_task,
        )

    def remove_selection(self, user_id: str) -> SelectionSession | None:
        """移除选择会话，返回旧会话（用于取消 timeout_task）。

        Args:
            user_id: 用户 ID。

        Returns:
            被移除的选择会话，不存在时返回 None。
        """
        return self._selection_sessions.pop(user_id, None)

    def get_selection(self, user_id: str) -> SelectionSession | None:
        """查询用户的选择会话。

        Args:
            user_id: 用户 ID。

        Returns:
            该用户的选择会话，不存在时返回 None。
        """
        return self._selection_sessions.get(user_id)

    # ── Task 生命周期管理 ──

    def set_current_task(self, user_id: str, task: asyncio.Task | None) -> None:
        """显式设置用户的 current_task。

        Args:
            user_id: 用户 ID。
            task: 要设置的异步任务，或 None。
        """
        chat = self.get_or_create_chat(user_id)
        chat.current_task = task

    def reset_current_task(self, user_id: str) -> None:
        """快速将 current_task 设为 None。

        Args:
            user_id: 用户 ID。
        """
        chat = self._chat_sessions.get(user_id)
        if chat:
            chat.current_task = None

    @contextmanager
    def handler_context(self, user_id: str, matcher: Matcher):
        """进入 got handler 时更新 current_task 和 matcher，离开时自动 reset。

        用法：
            with session_manager.handler_context(user_id, matcher):
                ...

        Args:
            user_id: 用户 ID。
            matcher: 当前 got handler 的 Matcher。
        """
        chat = self.get_or_create_chat(user_id)
        chat.current_task = asyncio.current_task()
        chat.matcher = matcher
        try:
            yield
        finally:
            # 只清理没有被 deactivate_chat 重置过的情况
            if chat.current_task is asyncio.current_task():
                chat.current_task = None

    # ── 取消 ──

    async def execute_cancel(
        self, user_id: str, message: str = "当前会话已取消"
    ) -> bool:
        """执行取消逻辑。

        1. 检查是否有活跃会话，无则返回 False
        2. current_task.cancel()（非当前 task 且未完成时）
        3. remove_selection() + 取消 timeout_task（若有）
        4. 在旧 matcher 上 finish()（发送取消消息到原上下文）
        5. deactivate_chat(user_id)

        Note:
            got 处理器中捕获 CancelledError 后转为 FinishedException，
            确保 matcher.block=True 且 StopPropagation 正常抛出，
            防止被取消的事件滑落到兜底处理器。

        Args:
            user_id: 用户 ID。
            message: 结束事件的提示信息。

        Returns:
            bool: 无活跃会话返回 False，成功返回 True。
        """
        chat = self._chat_sessions.get(user_id)
        if not (chat and chat.active):
            return False

        # 防止自取消：同频道 /cancel 时 current_task 等于当前 task，跳过
        current = asyncio.current_task()
        if (
            chat.current_task
            and not chat.current_task.done()
            and chat.current_task is not current
        ):
            chat.current_task.cancel()

        # 移除选择会话 + 取消超时任务
        ss = self._selection_sessions.pop(user_id, None)
        if ss and ss.timeout_task and not ss.timeout_task.done():
            ss.timeout_task.cancel()

        # finish 老 matcher（发送取消消息到原上下文）
        if chat.matcher:
            try:
                await chat.matcher.finish(message)
            except FinishedException:
                pass

        self.deactivate_chat(user_id)
        return True


# 模块级单例
session_manager = SessionManager()


# ── 模块级工具函数 ──


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
    匹配则发送超时提示 + remove_selection + deactivate_chat + on_cleanup。
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

    # 通过公共方法 get_selection() 访问选择会话
    ss = session_manager.get_selection(user_id)
    if ss is not None and ss.selection_id == selection_id:
        logger.info("用户 %s 的选择会话超时（%d 秒）", user_id, timeout)
        session_manager.remove_selection(user_id)
        session_manager.deactivate_chat(user_id)
        if on_cleanup is not None:
            result = on_cleanup()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
        try:
            await bot.send(event, message)
        except Exception:
            logger.debug("发送超时消息失败", exc_info=True)
