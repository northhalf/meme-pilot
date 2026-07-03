"""/add 命令插件单元测试。"""

import asyncio
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
        _sanitize_filename,
        got_image,
        handle_add,
    )


from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    EmbeddingError,
    IndexAddCancelledError,
    OcrError,
    RefreshInProgressError,
)


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


def _make_index_manager() -> MagicMock:
    """创建模拟的 IndexManager。"""
    from bot.engine.index_manager import AddResult

    im = MagicMock()
    im.add = AsyncMock(
        return_value=AddResult(entry_id=1, reason="added", text="加班心好累")
    )
    im.add_user_timeout = 60.0
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
    @patch.object(meme_add, "is_authorized", return_value=False)
    async def test_unauthorized_rejected(
        self,
        mock_auth: MagicMock,
    ) -> None:
        """非授权用户应被静默忽略。"""
        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("999"), matcher)

        matcher.finish.assert_not_awaited()
        matcher.send.assert_not_awaited()

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
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_authorized_proceeds(
        self,
        mock_sm: MagicMock,
        mock_get_im: MagicMock,
        mock_auth: MagicMock,
    ) -> None:
        """授权用户应正常激活会话。"""
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111"), matcher)

        mock_sm.activate_chat.assert_called_once_with("111", "add", matcher)

    @pytest.mark.asyncio
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_existing_session_rejected(
        self,
        mock_sm: MagicMock,
        mock_auth: MagicMock,
    ) -> None:
        """激活失败（已有活跃会话）时应拒绝。"""
        mock_sm.activate_chat.return_value = False

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111"), matcher)

        matcher.finish.assert_awaited_once()
        assert "已有命令在处理中" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_target_name_captured(
        self,
        mock_sm: MagicMock,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """/add 后的目标命名应正确提取到 state。"""
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add 我的表情"), matcher)

        assert matcher.state["target_name"] == "我的表情"

    @pytest.mark.asyncio
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "is_authorized", return_value=True)
    @patch.object(meme_add, "session_manager")
    async def test_target_name_empty_when_no_arg(
        self,
        mock_sm: MagicMock,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """/add 无参数时 target_name 为空字符串。"""
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(_make_bot(), _make_event("111", "/add"), matcher)

        assert matcher.state["target_name"] == ""


# ===========================================================================
# got_image 测试
# ===========================================================================


class TestGotImage:
    """got_image 处理函数测试。"""

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=True)
    async def test_cancel_intercepted(
        self,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
    ) -> None:
        """/cancel 旁路拦截。"""
        matcher = _make_matcher()
        event = _make_event(text="/cancel")
        await got_image(_make_bot(), event, matcher, MagicMock())

        mock_bypass.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=True)
    async def test_help_intercepted(
        self,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
    ) -> None:
        """/help 旁路拦截，不应反激活。"""
        matcher = _make_matcher()
        event = _make_event(text="/help")
        await got_image(_make_bot(), event, matcher, MagicMock())

        mock_sm.deactivate_chat.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "extract_image_urls", return_value=[])
    async def test_no_image_rejects(
        self,
        mock_extract: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
    ) -> None:
        """无图片时应 reject 提示重发。"""
        matcher = _make_matcher()
        image_msg = MagicMock()
        await got_image(_make_bot(), _make_event(), matcher, image_msg)

        matcher.reject.assert_awaited_once()
        assert "图片" in matcher.reject.call_args[0][0]
        mock_sm.deactivate_chat.assert_not_called()  # reject 后不反激活

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "get_index_manager", side_effect=RuntimeError("未初始化"))
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_get_index_manager_error(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
    ) -> None:
        """get_index_manager 抛出 RuntimeError 时应回复未就绪。"""
        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event("111"), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "未就绪" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("111")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_success(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """正常流程应回复成功。"""
        from bot.engine.index_manager import AddResult

        im = _make_index_manager()
        im.add = AsyncMock(
            return_value=AddResult(entry_id=1, reason="added", text="加班心好累")
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
        assert "新增表情包" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="我的表情.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_success_with_target_name(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """带 target_name 时应回复成功。"""
        from bot.engine.index_manager import AddResult

        im = _make_index_manager()
        im.add = AsyncMock(
            return_value=AddResult(entry_id=1, reason="added", text="加班心好累")
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
        assert "新增表情包" in matcher.finish.call_args[0][0]

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "_download_image", side_effect=RuntimeError("下载失败"))
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_download_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
    ) -> None:
        """下载失败时应回复错误。"""
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "下载失败" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_unsupported_extension_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """不支持的图片格式应回复错误。"""
        mock_get_im.return_value = _make_index_manager()
        mock_download.return_value = (b"fake", _make_response())
        mock_ext.return_value = None  # 无法推断扩展名

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "不支持的图片格式" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_compression_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """压缩失败时应回复对应错误。"""
        from bot.engine.index_manager import CompressionError

        im = _make_index_manager()
        im.add = AsyncMock(side_effect=CompressionError("压缩失败"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "压缩失败" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_ocr_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """OCR 失败时应回复对应错误。"""
        from bot.engine.index_manager import OcrError

        im = _make_index_manager()
        im.add = AsyncMock(side_effect=OcrError("OCR 失败"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "OCR" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_embedding_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Embedding 失败时应回复对应错误。"""
        from bot.engine.index_manager import EmbeddingError

        im = _make_index_manager()
        im.add = AsyncMock(side_effect=EmbeddingError("Embedding 失败"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "Embedding" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_generic_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """未知异常时应回复通用错误。"""
        im = _make_index_manager()
        im.add = AsyncMock(side_effect=RuntimeError("未知错误"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "添加失败" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_lock_contention_in_got(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """got 中索引刷新时应回复提示。"""
        im = _make_index_manager()
        im.add = AsyncMock(side_effect=RefreshInProgressError("索引正在刷新"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file
        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "索引正在刷新" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_add_cancelled_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """got 中 add 被取消时应回复提示。"""
        im = _make_index_manager()
        im.add = AsyncMock(side_effect=IndexAddCancelledError("Bot 正在关闭"))
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file
        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "添加任务已取消" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "resolve_unique_filename")
    @patch.object(meme_add, "_build_filename", return_value="a.jpg")
    @patch.object(meme_add, "_get_extension", return_value=".jpg")
    @patch.object(meme_add, "_download_image")
    @patch.object(meme_add, "get_index_manager")
    @patch.object(meme_add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_add_timeout_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_build: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """got 中 add 超时等待时应回复提示。"""
        im = _make_index_manager()
        im.add = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_get_im.return_value = im

        fake_file = tmp_path / "a.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file
        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher()
        await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        assert "添加处理超时" in matcher.finish.call_args[0][0]
        mock_sm.deactivate_chat.assert_called_once_with("12345")

    @pytest.mark.asyncio
    @patch.object(meme_add, "session_manager")
    @patch.object(meme_add, "got_intercept_bypass", return_value=False)
    @patch.object(meme_add, "extract_image_urls", side_effect=ValueError("解析失败"))
    async def test_extract_urls_exception(
        self,
        mock_extract: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
    ) -> None:
        """extract_image_urls 异常应传播。"""
        matcher = _make_matcher()
        with pytest.raises(ValueError):
            await got_image(_make_bot(), _make_event(), matcher, MagicMock())
        # deactivate_chat 在内外 except 中各调用一次
        assert mock_sm.deactivate_chat.call_count >= 1
