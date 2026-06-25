"""/add 命令插件 — 通过聊天添加表情包。

授权用户在私聊中发送 /add [目标命名]，Bot 等待图片后
下载、压缩、OCR、Embedding 并写入索引。
"""

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.adapters.onebot.v11.helpers import extract_image_urls
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.rule import to_me

from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.config import MEMES_DIR
from bot.engine.index_manager import (
    CompressionError,
    EmbeddingError,
    IndexManager,
    OcrError,
    resolve_unique_filename,
)
from bot.session import cancel, check_and_cancel, is_cancelled, register

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 30  # 图片下载超时（秒）
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# 文件名安全化：替换非法字符
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")

add_cmd = on_command("add", rule=to_me(), priority=5, block=True)


@add_cmd.handle()
async def handle_add(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    """/add 命令入口。

    流程：授权校验 → 会话覆盖 → 锁检查 → 捕获目标命名 → 注册会话。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
    """
    user_id = event.get_user_id()

    # 授权校验
    if not is_authorized(user_id):
        log_unauthorized(user_id, "add")
        return

    # 会话覆盖检查
    hint = check_and_cancel(user_id, "add")
    if hint:
        await matcher.send(hint)

    # 获取 IndexManager
    try:
        index_manager = get_index_manager()
    except RuntimeError:
        logger.error("IndexManager 尚未初始化")
        await matcher.finish("服务未就绪，请稍后再试")
        return

    # 检查索引锁
    if not await index_manager.acquire_lock():
        logger.info("用户 %s 的 /add 被拒绝：索引正在更新", user_id)
        await matcher.finish("索引正在更新，请稍后再试")
        return

    # 捕获目标命名（命令参数），存入 state 供 got_image 使用
    raw_text = event.get_plaintext().strip()
    target_name = raw_text.removeprefix("/add").removeprefix("add").strip()
    matcher.state["target_name"] = target_name

    # 注册会话
    register(user_id, matcher, "add")


@add_cmd.got("image", prompt="请发送图片，60 秒内有效")
async def got_image(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    image_msg: Message = Arg("image"),
) -> None:
    """接收图片并处理。

    非图片消息时 reject 重新等待；图片消息执行完整添加流程。
    会话超时时清理 session 状态并提示用户。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        image_msg: got("image") 接收到的消息。
    """
    user_id = event.get_user_id()

    try:
        # 会话有效性检查
        if is_cancelled(user_id):
            return

        # 获取 IndexManager
        try:
            index_manager = get_index_manager()
        except RuntimeError:
            return

        # 从 got() 获取的消息中提取图片 URL
        urls = extract_image_urls(image_msg)
        if not urls:
            await matcher.reject("请发送一张图片")
            return

        image_url = urls[0]
        target_name = str(matcher.state.get("target_name", ""))

        # 下载图片
        try:
            image_data, response = await _download_image(image_url)
        except Exception as exc:
            logger.error("图片下载失败: %s", exc)
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish("图片下载失败")
            return

        # 确定扩展名
        ext = _get_extension(image_url, response)
        if ext is None or ext.lower() not in SUPPORTED_EXTENSIONS:
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish(f"不支持的图片格式: {ext or '未知'}")
            return

        # 文件名处理
        filename = _build_filename(target_name, image_data, ext)

        # 检查文件名冲突
        filepath = resolve_unique_filename(MEMES_DIR, filename)
        filename = filepath.name

        # 保存图片
        MEMES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            filepath.write_bytes(image_data)
        except OSError as exc:
            logger.error("保存图片失败: %s", exc)
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish("图片保存失败")
            return

        # 调用 IndexManager 处理
        try:
            result = await index_manager.add_single_file(filename)
        except CompressionError as exc:
            logger.error("图片压缩失败: %s", exc)
            filepath.unlink(missing_ok=True)
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish("图片压缩失败")
            return
        except OcrError as exc:
            logger.error("OCR 失败: %s", exc)
            filepath.unlink(missing_ok=True)
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish("OCR 服务不可用")
            return
        except EmbeddingError as exc:
            logger.error("Embedding 失败: %s", exc)
            filepath.unlink(missing_ok=True)
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish("Embedding 服务不可用")
            return
        except Exception as exc:
            logger.exception("添加表情包异常")
            filepath.unlink(missing_ok=True)
            _release_lock_safe(index_manager)
            cancel(user_id)
            await matcher.finish("添加失败，请查看日志")
            return

        # 成功：释放锁、清理会话、回复结果
        _release_lock_safe(index_manager)
        cancel(user_id)
        if result.reason == "no_text":
            await matcher.finish("未识别到文字，已移至 meme_no_text/")
        elif result.reason == "replaced":
            await matcher.finish("已成功添加（替换旧图）✅")
        else:
            await matcher.finish("已成功添加表情包 ✅")

    except BaseException:
        # 会话超时（CancelledError）或其他异常：清理 session 状态
        logger.info("用户 %s 的 /add 会话超时或异常", user_id)
        cancel(user_id)
        # 释放索引锁（如果已获取）
        try:
            index_manager = get_index_manager()
            _release_lock_safe(index_manager)
        except RuntimeError:
            pass
        # 通过 bot.send 直接发消息（matcher 已被 NoneBot2 销毁）
        try:
            await bot.send(event, "添加已取消，请重新 /add")
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


async def _download_image(url: str) -> tuple[bytes, httpx.Response]:
    """下载图片。

    Args:
        url: 图片 URL。

    Returns:
        (图片数据, HTTP 响应) 元组。

    Raises:
        httpx.HTTPError: 下载失败。
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
        )
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


def _sanitize_filename(name: str) -> str:
    """安全化文件名基名。

    规则：去除首尾空白 → 替换非法字符 → 合并空白为 _ → 截断 80 字符。

    Args:
        name: 原始文件名基名。

    Returns:
        安全化后的基名（无扩展名），可能为空字符串。
    """
    name = name.strip()
    name = _UNSAFE_CHARS.sub("_", name)
    name = _WHITESPACE.sub("_", name)
    name = name[:80].strip("_")
    return name


def _auto_filename(image_data: bytes) -> str:
    """自动生成文件名。

    格式：meme_<YYYYMMDDHHMMSS>_<hash8>

    Args:
        image_data: 图片内容。

    Returns:
        自动生成的文件名基名。
    """
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    hash8 = hashlib.sha256(image_data).hexdigest()[:8]
    return f"meme_{now}_{hash8}"


def _build_filename(target_name: str, image_data: bytes, ext: str) -> str:
    """构建最终文件名。

    Args:
        target_name: 用户指定的目标命名（可能为空）。
        image_data: 图片内容。
        ext: 扩展名（含点号）。

    Returns:
        包含扩展名的完整文件名。
    """
    if target_name:
        base = _sanitize_filename(target_name)
    else:
        base = ""

    if not base:
        base = _auto_filename(image_data)

    return f"{base}{ext}"


def _release_lock_safe(index_manager: IndexManager) -> None:
    """安全释放索引锁。

    Args:
        index_manager: 索引管理器实例。
    """
    try:
        index_manager.release_lock()
    except Exception:
        logger.debug("释放锁时异常", exc_info=True)
