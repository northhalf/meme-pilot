"""/del 命令插件 — 按 id 删除一个或多个表情包。

授权用户私聊中发送 /del <entry_id>...，
Bot 发送摘要确认消息，用户回复「确认」或「yes」后执行删除。
"""

import asyncio
import logging
import uuid

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.rule import to_me

from bot.app_state import get_index_manager, get_metadata_store
from bot.auth import is_authorized, log_unauthorized
from bot.engine.index_manager import IndexAddCancelledError, RefreshInProgressError
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

delete_cmd = on_command("del", rule=to_me(), priority=5, block=True, aliases={"d"})


def _truncate_text(text: str, limit: int = 30) -> str:
    """截断 OCR 文本，超出长度时附加总字数提示。

    Args:
        text: 原始 OCR 文本。
        limit: 保留的最大字符数。

    Returns:
        截断后的文本描述。
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...（共 {len(text)} 字）"


@delete_cmd.handle()
async def handle_delete(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """入口：授权校验 → 参数解析 → 发送摘要确认。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（CommandArg 注入），包含待删除的 id 列表。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /del", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "del")
                await matcher.finish(None)
                return

            # 仅限私聊
            if event.message_type != "private":
                await matcher.finish("此命令仅限私聊使用")
                return

            # 会话检查
            if not session_manager.activate_chat(user_id, "del", matcher):
                await matcher.finish("已有命令在处理中，请先 /cancel")
                return

            # 解析参数
            text_part = args.extract_plain_text().strip()
            tokens = text_part.split()
            if not tokens:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("用法：/del <id>...")
                return

            entry_ids: list[int] = []
            for token in tokens:
                try:
                    entry_ids.append(int(token))
                except ValueError:
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish("id 必须为数字")
                    return

            # 去重，保持顺序
            entry_ids = list(dict.fromkeys(entry_ids))
            logger.debug("/del 目标 entry_ids: %s", entry_ids)

            # 查询每个 id
            store = get_metadata_store()
            found: list[tuple[int, str]] = []
            not_found_ids: list[int] = []
            for eid in entry_ids:
                entry = store.get_entry(eid)
                if entry is None:
                    not_found_ids.append(eid)
                else:
                    found.append((eid, entry.text))

            logger.debug(
                "/del 找到 %d 条，未找到 %d 条", len(found), len(not_found_ids)
            )

            if not found:
                session_manager.deactivate_chat(user_id)
                await matcher.finish("未找到任何表情包")
                return

            # 构建摘要确认消息
            lines = ["确认删除以下表情包？回复「确认」执行删除，回复其他内容取消。"]
            for eid, text in found:
                lines.append(f"{eid}, {_truncate_text(text)}")
            if not_found_ids:
                lines.append(f"未找到 id：{', '.join(str(i) for i in not_found_ids)}")

            await matcher.send("\n".join(lines))

            # 存入 state
            matcher.state["entry_ids"] = [eid for eid, _ in found]
            matcher.state["not_found_ids"] = not_found_ids

            # 注册超时
            selection_id = str(uuid.uuid4())
            task = asyncio.create_task(
                timeout_session(
                    bot, event, user_id, selection_id, "删除已取消（超时）"
                ),
            )
            session_manager.create_selection(user_id, selection_id, task)
            session_manager.reset_current_task(user_id)

        except asyncio.CancelledError:
            raise FinishedException


@delete_cmd.got("confirm")
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
    request_id = generate_request_id()
    with set_request_id(request_id):
        with session_manager.handler_context(user_id, matcher):
            try:
                text = event.get_plaintext().strip()

                # 旁路拦截 /help 和 /cancel
                if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                    return

                if text.strip().lower() in ("确认", "yes", "y"):
                    session_manager.remove_selection(user_id)

                    try:
                        result = await asyncio.wait_for(
                            get_index_manager().delete(matcher.state["entry_ids"]),
                            timeout=get_index_manager().add_user_timeout,
                        )
                    except asyncio.TimeoutError:
                        await matcher.finish("删除处理超时，请稍后再试")
                    except IndexAddCancelledError:
                        await matcher.finish("服务正在关闭，请稍后再试")
                    except RefreshInProgressError:
                        await matcher.finish("索引正在刷新，请稍后再试")
                    except Exception:
                        logger.exception("用户 %s 的 /del 删除异常", user_id)
                        await matcher.finish("删除过程中发生异常，请稍后重试")
                    else:
                        session_manager.deactivate_chat(user_id)
                        logger.info(
                            "/del 完成: 成功=%d, 未找到=%d, 失败=%d",
                            len(result.deleted_ids),
                            len(result.not_found_ids),
                            len(result.failed_ids),
                        )
                        logger.debug(
                            "/del 详情: 成功=%r, 未找到=%r, 失败=%r",
                            result.deleted_ids,
                            result.not_found_ids,
                            result.failed_ids,
                        )
                        lines = ["删除结果如下:"]
                        if result.deleted_ids:
                            lines.append(
                                "成功：" + "、".join(str(i) for i in result.deleted_ids)
                            )
                        if result.not_found_ids:
                            lines.append(
                                "未找到："
                                + "、".join(str(i) for i in result.not_found_ids)
                            )
                        if result.failed_ids:
                            failed_parts = [
                                f"id:{eid} 原因:『{reason}』"
                                for eid, reason in result.failed_ids
                            ]
                            lines.append("失败：" + "、".join(failed_parts))
                        await matcher.finish("\n".join(lines))
                        return
                else:
                    session_manager.deactivate_chat(user_id)
                    logger.info("用户 %s 取消 /del", user_id)
                    await matcher.finish("已取消删除")

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
                logger.exception("用户 %s 的 /del 处理异常", user_id)
                session_manager.deactivate_chat(user_id)
                raise
