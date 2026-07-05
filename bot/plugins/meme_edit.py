"""/edittext 命令插件 — 修改指定表情包的 OCR 文本。

授权用户私聊中发送 /edittext <entry_id> <新文本>，
Bot 发送图片和确认消息，用户回复「确认」或「yes」后执行修改。
"""

import asyncio
import logging
import uuid

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager, get_metadata_store
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.index_manager import (
    DuplicateTextError,
    EmbeddingError,
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

edit_cmd = on_command("edittext", rule=to_me(), priority=5, block=True)


@edit_cmd.handle()
async def handle_edit(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """入口：授权校验 → 参数解析 → 发图确认。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()
    logger.info("用户 %s 调用 /edittext", user_id)

    try:
        # 授权校验
        if not is_authorized(user_id):
            log_unauthorized(user_id, "edittext")
            await matcher.finish(None)
            return

        # 仅限私聊
        if event.message_type != "private":
            await matcher.finish("此命令仅限私聊使用")
            return

        # 会话检查
        if not session_manager.activate_chat(user_id, "edittext", matcher):
            await matcher.finish("已有命令在处理中，请先 /cancel")
            return

        # 解析参数
        raw = event.get_plaintext().strip()
        text_part = raw.removeprefix("/edittext").removeprefix("edittext").strip()
        parts = text_part.split(maxsplit=1)
        if len(parts) < 2:
            await matcher.finish("用法：/edittext <entry_id> <新文本>")
            return

        try:
            entry_id = int(parts[0])
        except ValueError:
            await matcher.finish("entry_id 必须为数字")
            return

        new_text = "".join(parts[1].split())  # 统一去空白
        if not new_text:
            await matcher.finish("新文本不能为空")
            return

        # 校验 entry 存在
        store = get_metadata_store()
        entry = store.get_entry(entry_id)
        if entry is None:
            await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")
            return

        # 发送图片
        image_path = MEMES_DIR / entry.image_path
        if image_path.exists():
            await matcher.send(
                MessageSegment.image("file://" + str(image_path.resolve()))
            )

        # 确认消息
        await matcher.send(
            f"当前 OCR 文本：{entry.text}\n"
            f"修改后文本：{new_text}\n"
            "回复「确认」或「yes」确认修改，回复其他内容取消",
        )

        # 存入 state
        matcher.state["entry_id"] = entry_id
        matcher.state["new_text"] = new_text
        matcher.state["old_text"] = entry.text

        # 注册超时
        selection_id = str(uuid.uuid4())
        task = asyncio.create_task(
            timeout_session(bot, event, user_id, selection_id, "修改已取消（超时）"),
        )
        session_manager.create_selection(user_id, selection_id, task)
        session_manager.reset_current_task(user_id)

    except asyncio.CancelledError:
        raise FinishedException


@edit_cmd.got("confirm")
async def got_confirm(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    confirm_msg: Message = Arg("confirm"),
) -> None:
    """处理用户确认/取消。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        confirm_msg: got("confirm") 接收到的消息。
    """
    user_id = event.get_user_id()

    with session_manager.handler_context(user_id, matcher):
        try:
            text = event.get_plaintext().strip()

            # 旁路拦截 /help 和 /cancel
            if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                return

            if text.strip().lower() in ("确认", "yes", "y"):
                entry_id = matcher.state["entry_id"]
                new_text = str(matcher.state["new_text"])

                try:
                    result = await asyncio.wait_for(
                        get_index_manager().edit_text(entry_id, new_text),
                        timeout=get_index_manager().add_user_timeout,
                    )
                except asyncio.TimeoutError:
                    await matcher.finish("修改处理超时，请稍后再试")
                except IndexAddCancelledError:
                    await matcher.finish("服务正在关闭，请稍后再试")
                except RefreshInProgressError:
                    await matcher.finish("索引正在刷新，请稍后再试")
                except ValueError:
                    await matcher.finish(f"未找到 id 为 {entry_id} 的表情包")
                except DuplicateTextError as exc:
                    await matcher.finish(str(exc))
                except EmbeddingError:
                    await matcher.finish("修改失败（Embedding 异常），请稍后重试")
                else:
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish(
                        f"OCR 文本已修改 ✅\n"
                        f"旧：{result.old_text}\n"
                        f"新：{result.new_text}",
                    )
                    return
            else:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("已取消修改")

            # 异常统一清理
            session_manager.deactivate_chat(user_id)

        except FinishedException:
            session_manager.deactivate_chat(user_id)
            raise
        except RejectedException:
            raise
        except asyncio.CancelledError:
            session_manager.deactivate_chat(user_id)
            raise FinishedException
        except Exception:
            logger.exception("用户 %s 的 /edittext 处理异常", user_id)
            session_manager.deactivate_chat(user_id)
            raise
