"""/add 命令插件单元测试。"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# got() 返回透传 decorator，Arg 返回特殊标记供测试替换。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn

with (
    patch("nonebot.on_command", return_value=_mock_cmd),
    patch("nonebot.params.Arg", return_value="IMAGE_ARG_SENTINEL"),
):
    from bot.plugins import meme_add
    from bot.plugins.meme_add import (
        _build_filename,
        _get_extension,
        _release_lock_safe,
        _sanitize_filename,
        got_image,
        handle_add,
    )


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", text: str = "/add") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.get_plaintext.return_value = text
    event.message_type = "private"
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    bot.download_file = AsyncMock()
    return bot


def _make_matcher(*, state: dict | None = None) -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.state = state if state is not None else {}
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_index_manager(*, lock_ok: bool = True) -> MagicMock:
    """创建模拟的 IndexManager。"""
    im = MagicMock()
    im.acquire_lock = AsyncMock(return_value=lock_ok)
    im.release_lock = MagicMock()
    im.add_single_file = AsyncMock()
    return im


def _make_response(content_type: str = "image/jpeg") -> MagicMock:
    """创建模拟的 HTTP 响应。"""
    resp = MagicMock()
    resp.headers = {"content-type": content_type}
    return resp


# ===========================================================================
# 辅助函数测试
# ===========================================================================


class TestSanitizeFilename:
    """_sanitize_filename 测试。"""

    def test_unsafe_chars_replaced(self) -> None:
        """非法字符替换为下划线。"""
        result = _sanitize_filename('a/b:c*d?"f')
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result
        assert "?" not in result
        assert '"' not in result

    def test_whitespace_merged(self) -> None:
        """连续空白合并为单个下划线。"""
        result = _sanitize_filename("a   b\t\tc")
        assert "__" not in result
        assert "_" in result

    def test_strips_leading_trailing_underscores(self) -> None:
        """首尾下划线被去除。"""
        result = _sanitize_filename("___abc___")
        assert result == "abc"

    def test_truncates_at_80(self) -> None:
        """截断至 80 字符。"""
        long_name = "a" * 200
        result = _sanitize_filename(long_name)
        assert len(result) <= 80

    def test_preserves_chinese(self) -> None:
        """中文字符保留（非非法字符）。"""
        result = _sanitize_filename("你好世界")
        assert result == "你好世界"

    def test_empty_returns_empty(self) -> None:
        """全非法字符返回空字符串。"""
        result = _sanitize_filename("/:?*")
        assert result == ""


class TestGetExtension:
    """_get_extension 测试。"""

    def test_jpg_from_url(self) -> None:
        """从 URL 路径提取 .jpg。"""
        resp = _make_response()
        assert _get_extension("https://example.com/photo.jpg", resp) == ".jpg"

    def test_png_from_url(self) -> None:
        """从 URL 路径提取 .png。"""
        resp = _make_response()
        assert _get_extension("https://example.com/img.png", resp) == ".png"

    def test_url_with_query_params(self) -> None:
        """带查询参数的 URL 正确提取扩展名。"""
        resp = _make_response()
        assert _get_extension("https://example.com/img.jpg?width=100", resp) == ".jpg"

    def test_fallback_to_content_type(self) -> None:
        """URL 无扩展名时从 Content-Type 推断。"""
        resp = _make_response("image/png")
        assert _get_extension("https://example.com/image", resp) == ".png"

    def test_content_type_gif(self) -> None:
        """Content-Type image/gif。"""
        resp = _make_response("image/gif")
        assert _get_extension("https://example.com/image", resp) == ".gif"

    def test_content_type_webp(self) -> None:
        """Content-Type image/webp。"""
        resp = _make_response("image/webp")
        assert _get_extension("https://example.com/image", resp) == ".webp"

    def test_no_extension_no_content_type(self) -> None:
        """无法推断时返回 None。"""
        resp = _make_response("text/plain")
        assert _get_extension("https://example.com/image", resp) is None


class TestBuildFilename:
    """_build_filename 测试。"""

    def test_with_target_name(self) -> None:
        """有目标命名时使用目标命名。"""
        result = _build_filename("我的表情", b"fake", ".jpg")
        assert result == "我的表情.jpg"

    def test_empty_target_uses_auto(self) -> None:
        """空目标命名时自动生成文件名。"""
        result = _build_filename("", b"fake_data", ".png")
        assert result.startswith("meme_")
        assert result.endswith(".png")

    def test_sanitizes_target_name(self) -> None:
        """目标命名被安全化。"""
        result = _build_filename("a/b", b"fake", ".jpg")
        assert "/" not in result
        assert result.endswith(".jpg")


class TestReleaseLockSafe:
    """_release_lock_safe 测试。"""

    def test_releases_lock(self) -> None:
        """正常释放锁。"""
        im = MagicMock()
        _release_lock_safe(im)
        im.release_lock.assert_called_once()

    def test_exception_swallowed(self) -> None:
        """释放锁异常不抛出。"""
        im = MagicMock()
        im.release_lock.side_effect = RuntimeError("锁错误")
        _release_lock_safe(im)  # 不应抛异常


class TestFormatOcrText:
    """_format_ocr_text 测试。"""

    def test_short_text_returned_as_is(self) -> None:
        """短于等于 50 字的文本原样返回。"""
        assert meme_add._format_ocr_text("心好累啊") == "心好累啊"

    def test_exactly_50_chars(self) -> None:
        """刚好 50 字不截断。"""
        text = "a" * 50
        assert meme_add._format_ocr_text(text) == text

    def test_long_text_truncated(self) -> None:
        """超过 50 字截断并标注总长度。"""
        text = "a" * 60
        expected = "a" * 50 + "...（总文本长度60）"
        assert meme_add._format_ocr_text(text) == expected

    def test_empty_string(self) -> None:
        """空字符串不截断。"""
        assert meme_add._format_ocr_text("") == ""


# ===========================================================================
# handle_add 测试
# ===========================================================================


class TestHandleAdd:
    """handle_add 处理函数测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value=None)
    @patch.object(meme_add, "is_authorized", return_value=False)
    async def test_unauthorized_rejected(
        self,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("999"), matcher)

        matcher.finish.assert_not_awaited()
        matcher.send.assert_not_awaited()
        mock_register.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    async def test_group_chat_rejected(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """群聊中调用 /add 应回复仅限私聊提示。"""
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.get_plaintext.return_value = "/add 测试"
        event.message_type = "group"

        matcher = _make_matcher()
        await handle_add(_make_bot(), event, matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        assert "仅限私聊" in call_args
        mock_get_im.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value=None)
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "get_index_manager")
    async def test_authorized_proceeds(
        self,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """授权用户应正常注册会话。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111"), matcher)

        mock_register.assert_called_once_with("111", matcher, "add")

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value="旧会话已取消")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "get_index_manager")
    async def test_existing_session_cancelled(
        self,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """旧会话存在时应取消并提示。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111"), matcher)

        matcher.send.assert_awaited_once()
        assert "旧会话已取消" in matcher.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value=None)
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "get_index_manager")
    async def test_lock_contention(
        self,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """锁占用时应回复提示。"""
        mock_get_im.return_value = _make_index_manager(lock_ok=False)

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111"), matcher)

        matcher.finish.assert_awaited_once()
        assert "索引正在更新" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value=None)
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "get_index_manager")
    async def test_target_name_captured(
        self,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """/add 后的目标命名应正确提取到 state。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add 我的表情"), matcher)

        assert matcher.state["target_name"] == "我的表情"

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value=None)
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "get_index_manager")
    async def test_target_name_empty_when_no_arg(
        self,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """/add 无参数时 target_name 为空字符串。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add"), matcher)

        assert matcher.state["target_name"] == ""

    @pytest.mark.asyncio
    @patch.object(meme_add, "register")
    @patch.object(meme_add, "check_and_cancel", return_value=None)
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "get_index_manager")
    async def test_init_error_replies(
        self,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
        mock_check: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """IndexManager 未初始化时应回复错误。"""
        mock_get_im.side_effect = RuntimeError("未初始化")

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111"), matcher)

        matcher.finish.assert_awaited_once()
        assert "未就绪" in matcher.finish.call_args[0][0]


# ===========================================================================
# got_image 测试
# ===========================================================================


class TestGotImage:
    """got_image 处理函数测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "is_cancelled", return_value=True)
    @patch.object(meme_add, "extract_image_urls")
    async def test_cancelled_session_exits(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """已取消的会话应静默退出。"""
        matcher = _make_matcher()
        image_msg = MagicMock()
        await got_image(_make_bot(), _make_event(), matcher, image_msg)

        mock_cancel.assert_not_called()  # 不额外调用 cancel
        matcher.finish.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=[])
    async def test_no_image_rejects(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_cancelled: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """无图片时应 reject 提示重发。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        image_msg = MagicMock()
        await got_image(_make_bot(), _make_event(), matcher, image_msg)

        matcher.reject.assert_awaited_once()
        assert "图片" in matcher.reject.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_success(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """正常流程应回复成功。"""
        from bot.engine.index_manager import AddResult

        im = _make_index_manager()
        im.add_single_file = AsyncMock(
            return_value=AddResult(entry_id="1", reason="added", text="加班心好累")
        )
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        bot = _make_bot()
        await got_image(bot, _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "新增表情包✅" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="我的表情.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_success_with_target_name(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """带 target_name 时应回复成功。"""
        from bot.engine.index_manager import AddResult

        im = _make_index_manager()
        im.add_single_file = AsyncMock(
            return_value=AddResult(entry_id="1", reason="added", text="加班心好累")
        )
        mock_get_im.return_value = im

        fake_file = tmp_path / "我的表情.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher(state={"target_name": "我的表情"})
        bot = _make_bot()
        await got_image(bot, _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "新增表情包✅" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "_download_image", side_effect=RuntimeError("下载失败"))
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_download_error_replies(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """下载失败时应回复错误。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "下载失败" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_compression_error_replies(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """压缩失败时应回复对应错误。"""
        from bot.engine.index_manager import CompressionError

        im = _make_index_manager()
        im.add_single_file = AsyncMock(side_effect=CompressionError("压缩失败"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "压缩失败" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_ocr_error_replies(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """OCR 失败时应回复对应错误。"""
        from bot.engine.index_manager import OcrError

        im = _make_index_manager()
        im.add_single_file = AsyncMock(side_effect=OcrError("OCR 失败"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "OCR" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_embedding_error_replies(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Embedding 失败时应回复对应错误。"""
        from bot.engine.index_manager import EmbeddingError

        im = _make_index_manager()
        im.add_single_file = AsyncMock(side_effect=EmbeddingError("Embedding 失败"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "Embedding" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_generic_error_replies(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """未知异常时应回复通用错误。"""
        im = _make_index_manager()
        im.add_single_file = AsyncMock(side_effect=RuntimeError("未知错误"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "添加失败" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_lock_released_on_success(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """成功时应释放锁。"""
        from bot.engine.index_manager import AddResult

        im = _make_index_manager()
        im.add_single_file = AsyncMock(
            return_value=AddResult(entry_id="1", reason="added", text="加班心好累")
        )
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        mock_release.assert_called_once_with(im)

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "_download_image", side_effect=RuntimeError("下载失败"))
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_lock_released_on_error(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
    ) -> None:
        """异常时也应释放锁。"""
        im = _make_index_manager()
        mock_get_im.return_value = im

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        mock_release.assert_called_once_with(im)

    @pytest.mark.asyncio
    @patch.object(meme_add, "cancel")
    @patch.object(meme_add, "_release_lock_safe")
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_cancelled", return_value=False)
    @patch.object(
        meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"]
    )
    async def test_session_cancelled_on_success(
        self,
        mock_extract: MagicMock,
        mock_cancelled: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_release: MagicMock,
        mock_cancel: MagicMock,
        tmp_path: Path,
    ) -> None:
        """成功时应清理会话。"""
        from bot.engine.index_manager import AddResult

        im = _make_index_manager()
        im.add_single_file = AsyncMock(
            return_value=AddResult(entry_id="1", reason="added", text="加班心好累")
        )
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event("user999"), matcher, MagicMock())

        mock_cancel.assert_called_once_with("user999")
