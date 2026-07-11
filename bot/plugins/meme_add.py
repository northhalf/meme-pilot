"""/add 命令插件 — 通过聊天添加表情包。

授权用户在私聊中发送 /add [说话人] [标签1] [标签2] ...，Bot 等待图片后
下载、压缩、OCR、Embedding 并写入索引。
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.adapters.onebot.v11.helpers import extract_image_urls
from nonebot.exception import FinishedException, RejectedException
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR, read_session_timeout
from bot.engine.index_manager import (
    CompressionError,
    EmbeddingError,
    IndexAddCancelledError,
    OcrError,
    RefreshInProgressError,
    resolve_unique_filename,
)
from bot.engine.retry_config import api_retry
from bot.log_context import generate_request_id, set_request_id
from bot.plugins._help_text import HELP_TEXT
from bot.plugins._search_utils import format_metadata_line, got_intercept_bypass
from bot.session import session_manager, timeout_session

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 30  # 图片下载超时（秒）
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

add_cmd = on_command("add", rule=to_me(), priority=5, block=True, aliases={"a"})


@add_cmd.handle()
async def handle_add(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    """/add 命令入口。

    流程：授权校验 → 会话检查 → 捕获说话人和标签。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令参数（说话人 + 标签），由 CommandArg 注入。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        logger.info("用户 %s 调用 /add", user_id)

        try:
            # 授权校验
            if not is_authorized(user_id):
                log_unauthorized(user_id, "add")
                await matcher.finish(None)
                return

            # 群聊拦截：/add 仅限私聊使用
            if event.message_type != "private":
                logger.info("用户 %s 在群聊中调用 /add，已拒绝", user_id)
                await matcher.finish("此命令仅限私聊使用")
                return

            # 会话检查：拒绝而非覆盖
            if not session_manager.activate_chat(user_id, "add", matcher):
                await matcher.finish("已有命令在处理中，请先 /cancel")
                return

            # 解析 speaker 和 tags
            args_text = args.extract_plain_text().strip()
            parts = args_text.split()
            speaker = parts[0] if parts else None
            tags = parts[1:] if len(parts) > 1 else []
            matcher.state["speaker"] = speaker
            matcher.state["tags"] = tags
            logger.debug("/add 参数: speaker=%r, tags=%r", speaker, tags)

            selection_id = str(uuid.uuid4())
            task = asyncio.create_task(
                timeout_session(
                    bot, event, user_id, selection_id, "发送图片超时，请重新 /add"
                )
            )
            session_manager.create_selection(user_id, selection_id, task)
            session_manager.reset_current_task(user_id)
        except asyncio.CancelledError:
            raise FinishedException


@add_cmd.got("image", prompt=f"请发送图片，{read_session_timeout()} 秒内有效")
async def got_image(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    image_msg: Message = Arg("image"),
) -> None:
    """接收图片并处理。

    通过 handler_context 更新 current_task（不同 asyncio task），
    然后拦截 /help 和 /cancel，正常图片时执行完整添加流程。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        image_msg: got("image") 接收到的消息。
    """
    user_id = event.get_user_id()
    request_id = generate_request_id()
    with set_request_id(request_id):
        with session_manager.handler_context(user_id, matcher):
            try:
                # ── 阶段 0：/help 和 /cancel 旁路拦截 ──
                text = event.get_plaintext().strip()
                if await got_intercept_bypass(user_id, matcher, text, HELP_TEXT):
                    return

                # ── 阶段 1：图片验证 ──
                try:
                    urls = extract_image_urls(image_msg)
                except Exception:
                    logger.exception("extract_image_urls 异常")
                    session_manager.deactivate_chat(user_id)
                    raise
                if not urls:
                    await matcher.reject("请发送一张图片")
                    return
                # 成功发送图片
                session_manager.remove_selection(user_id)

                # 获取 IndexManager
                try:
                    index_manager = get_index_manager()
                except RuntimeError:
                    logger.error("IndexManager 尚未初始化")
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish("服务未就绪，请稍后再试")
                    return

                # ── 阶段 2：处理流程 ──
                image_url = urls[0]
                speaker = matcher.state.get("speaker")
                tags = matcher.state.get("tags", [])
                logger.debug(
                    "/add 收到图片 URL: %r, 扩展名: %r",
                    image_url,
                    Path(image_url.split("?")[0]).suffix,
                )

                # 下载图片
                try:
                    image_data, response = await _download_image(image_url)
                except Exception as exc:
                    logger.error("图片下载失败: %s", exc)
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish("图片下载失败")
                    return

                # 确定扩展名
                ext = _get_extension(image_url, response)
                if ext is None or ext.lower() not in SUPPORTED_EXTENSIONS:
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish(f"不支持的图片格式: {ext or '未知'}")
                    return

                # 文件名处理
                filename = f"{_auto_filename(image_data)}{ext}"
                filepath = resolve_unique_filename(MEMES_DIR, filename)
                filename = filepath.name

                # 保存图片
                MEMES_DIR.mkdir(parents=True, exist_ok=True)
                try:
                    filepath.write_bytes(image_data)
                except OSError as exc:
                    logger.error("保存图片失败: %s", exc)
                    session_manager.deactivate_chat(user_id)
                    await matcher.finish("图片保存失败")
                    return

                logger.info("图片已保存: %s", filename)

                # 调用 IndexManager 处理
                try:
                    result = await index_manager.add(
                        filename, speaker=speaker, tags=tags
                    )
                except RefreshInProgressError as exc:
                    logger.info("用户 %s 的 /add 被拒绝：%s", user_id, exc)
                    msg = "索引正在刷新，请稍后再试"
                except IndexAddCancelledError as exc:
                    logger.info("用户 %s 的 /add 被取消：%s", user_id, exc)
                    msg = "添加任务已取消"
                except asyncio.TimeoutError:
                    logger.info("用户 %s 的 /add 等待超时", user_id)
                    msg = "添加处理超时，请稍后再试"
                except CompressionError as exc:
                    logger.error("图片压缩失败: %s", exc)
                    msg = "图片压缩失败"
                except OcrError as exc:
                    logger.error("OCR 失败: %s", exc)
                    msg = "OCR 服务不可用"
                except EmbeddingError as exc:
                    logger.error("Embedding 失败: %s", exc)
                    msg = "Embedding 服务不可用"
                except Exception:
                    logger.exception("添加表情包异常")
                    msg = "添加失败，请查看日志"
                else:
                    # 成功：回复结果
                    logger.info(
                        "/add 成功: entry_id=%s, reason=%s",
                        result.entry_id,
                        result.reason,
                    )
                    session_manager.deactivate_chat(user_id)
                    if result.reason == "no_text":
                        await matcher.finish("未识别到文字，已移至 meme_no_text/")
                    elif result.reason == "replaced":
                        ocr_display = _format_ocr_text(result.text)
                        await matcher.finish(
                            f"替换旧图✅，id：{result.entry_id}，识别到的文字为：\n「{ocr_display}」\n"
                            f"{
                                format_metadata_line(
                                    entry_id=result.entry_id,  # pyright: ignore[reportArgumentType]
                                    speaker=result.speaker,
                                    tags=result.tags,
                                )
                            }"
                        )
                    else:
                        ocr_display = _format_ocr_text(result.text)
                        await matcher.finish(
                            f"新增表情包✅，id：{result.entry_id}，识别到的文字为：\n「{ocr_display}」\n"
                            f"{
                                format_metadata_line(
                                    entry_id=result.entry_id,  # pyright: ignore[reportArgumentType]
                                    speaker=result.speaker,
                                    tags=result.tags,
                                )
                            }"
                        )
                    return

                # 统一错误处理：删除已保存的图片 + 清理会话
                filepath.unlink(missing_ok=True)
                session_manager.deactivate_chat(user_id)
                await matcher.finish(msg)

            except FinishedException:
                session_manager.deactivate_chat(user_id)
                raise
            except RejectedException:
                # reject 意味着等待用户再次输入，不清除会话状态
                raise
            except asyncio.CancelledError:
                # execute_cancel 通过 task.cancel() 终止处理时，
                # 捕获 CancelledError 转为 FinishedException，
                # 让 run() 正常收尾并抛出 StopPropagation，
                # 防止事件滑落到兜底处理器（如 catch_all）
                session_manager.deactivate_chat(user_id)
                raise FinishedException
            except Exception:
                logger.exception("用户 %s 的 /add 处理异常", user_id)
                session_manager.deactivate_chat(user_id)
                raise


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


class DownloadServerError(RuntimeError):
    """QQ 图片服务器返回 5xx，允许重试。"""


@api_retry(extra_exceptions=(DownloadServerError,))
async def _download_image(url: str) -> tuple[bytes, httpx.Response]:
    """下载图片，支持网络/超时/5xx 重试。

    Args:
        url: 图片 URL。

    Returns:
        (图片数据, HTTP 响应) 元组。

    Raises:
        httpx.HTTPError: 4xx 客户端错误等不可重试错误。
        DownloadServerError: 5xx 服务器错误，由 api_retry 重试。
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
        )
        if response.status_code >= 500:
            raise DownloadServerError(f"图片服务器错误 {response.status_code}: {url}")
        response.raise_for_status()
        return response.content, response


def _get_extension(url: str, response: httpx.Response) -> str | None:
    """确定图片扩展名。

    优先从 URL 路径提取，其次从 Content-Type 推断。

    Args:
        url: 图片 URL。
        response: HTTP 响应。

    Returns:
        扩展名（含点号），无法推断返回 None。
    """
    # 从 URL 路径提取
    path = Path(url.split("?")[0])
    if path.suffix:
        return path.suffix.lower()

    # 从 Content-Type 推断
    content_type = response.headers.get("content-type", "")
    mime_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    for mime, ext in mime_map.items():
        if mime in content_type:
            return ext

    return None


def _auto_filename(image_data: bytes) -> str:
    """自动生成文件名。

    格式：meme_YYYYMMDDHHMMSS_hash8

    Args:
        image_data: 图片内容。

    Returns:
        自动生成的文件名基名。
    """
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    hash8 = hashlib.sha256(image_data).hexdigest()[:8]
    return f"meme_{now}_{hash8}"


def _format_ocr_text(text: str, max_len: int = 50) -> str:
    """格式化 OCR 文本：过长时截断并标注总长度。

    Args:
        text: OCR 识别文本。
        max_len: 截断长度，默认 50。

    Returns:
        格式化后的文本。不超过 max_len 时原样返回；
        超过时截断为前 max_len 字并追加「...（总文本长度N）」。
    """
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}...（总文本长度{len(text)}）"
