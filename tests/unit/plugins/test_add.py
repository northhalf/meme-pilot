"""/add 命令插件单元测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.engine.index_manager import (
    AddResult,
    CompressionError,
    EmbeddingError,
    CollectionSelectionExpiredError,
    IndexAddCancelledError,
    OcrError,
    RefreshInProgressError,
)
from bot.engine.types import CollectionSelection, MemePublicId
from bot.session import ChatScope
from tests.conftest import extract_message_text

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，避免 NoneBot2 完整初始化。
# got() 返回透传 decorator，Arg 返回特殊标记供测试替换。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_mock_cmd.got.return_value = lambda fn: fn
_mock_on_command = MagicMock(return_value=_mock_cmd)

with (
    patch("nonebot.on_command", _mock_on_command),
    patch("nonebot.params.Arg", return_value="IMAGE_ARG_SENTINEL"),
):
    from bot.plugins import add
    from bot.plugins.add import (
        _get_extension,
        got_image,
        handle_add,
    )


def _make_scope(user_id: str = "12345") -> ChatScope:
    """构造私聊 ChatScope。"""
    return ChatScope(user_id=int(user_id), chat_type="private", chat_id=int(user_id))


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
    matcher.state = {
        "collection_selection": CollectionSelection(0, "全部合集"),
        **(state or {}),
    }
    matcher.finish = AsyncMock()
    matcher.send = AsyncMock()
    matcher.reject = AsyncMock()
    return matcher


def _make_index_manager() -> MagicMock:
    """创建模拟的 IndexManager。"""

    im = MagicMock()
    im.add = AsyncMock(
        return_value=AddResult(
            entry_id=1,
            reason="added",
            text="加班心好累",
            public_id=MemePublicId(1, 3),
            collection_name="新三国",
        )
    )
    im.get_selected_collection = AsyncMock(
        return_value=CollectionSelection(0, "全部合集")
    )
    im.validate_collection_selection = AsyncMock()
    im.add_user_timeout = 60.0
    return im


def _make_response(content_type: str = "image/jpeg") -> MagicMock:
    """创建模拟的 HTTP 响应。"""
    resp = MagicMock()
    resp.headers = {"content-type": content_type}
    return resp


def _make_message(text: str = "") -> MagicMock:
    """创建模拟的 Message 对象（CommandArg 注入）。"""
    msg = MagicMock()
    msg.extract_plain_text.return_value = text
    return msg


class TestAddCommandRegistration:
    """测试 /add 命令注册边界。"""

    def test_short_alias_requires_whitespace_boundary(self) -> None:
        """短别名 /a 后有参数时必须以空白分隔，避免捕获 /ai。"""
        registration = _mock_on_command.call_args

        assert registration is not None
        assert registration.args[0] == "add"
        assert registration.kwargs["aliases"] == {"a"}
        assert registration.kwargs.get("force_whitespace") is True


# ===========================================================================
# /add 参数解析测试
# ===========================================================================


class TestParseAddArgs:
    """/add 参数解析测试。"""

    @pytest.mark.asyncio
    @patch.object(add, "get_index_manager")
    @patch.object(add, "is_authorized", return_value=True)
    @patch.object(add, "session_manager")
    async def test_no_args(self, mock_sm, mock_auth, mock_get_im) -> None:
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(
            _make_bot(), _make_event("111", "/add"), matcher, args=_make_message("")
        )
        assert matcher.state["speaker"] is None
        assert matcher.state["tags"] == []
        assert matcher.state["collection_selection"] == CollectionSelection(
            0, "全部合集"
        )

    @pytest.mark.asyncio
    @patch.object(add, "get_index_manager")
    @patch.object(add, "is_authorized", return_value=True)
    @patch.object(add, "session_manager")
    async def test_speaker_only(self, mock_sm, mock_auth, mock_get_im) -> None:
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(
            _make_bot(),
            _make_event("111", "/add 小明"),
            matcher,
            args=_make_message("小明"),
        )
        assert matcher.state["speaker"] == "小明"
        assert matcher.state["tags"] == []

    @pytest.mark.asyncio
    @patch.object(add, "get_index_manager")
    @patch.object(add, "is_authorized", return_value=True)
    @patch.object(add, "session_manager")
    async def test_speaker_and_tags(self, mock_sm, mock_auth, mock_get_im) -> None:
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(
            _make_bot(),
            _make_event("111", "/add 小明 吐槽 加班"),
            matcher,
            args=_make_message("小明 吐槽 加班"),
        )
        assert matcher.state["speaker"] == "小明"
        assert matcher.state["tags"] == ["吐槽", "加班"]

    @pytest.mark.asyncio
    @patch.object(add, "get_index_manager")
    @patch.object(add, "is_authorized", return_value=True)
    @patch.object(add, "session_manager")
    async def test_short_command_extracts_speaker_and_tags(
        self, mock_sm, mock_auth, mock_get_im
    ) -> None:
        """短命令 /a 的参数经 CommandArg 提取后应与 /add 一致。"""
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()
        matcher = _make_matcher()
        await handle_add(
            _make_bot(),
            _make_event("111", "/a 小明 吐槽 加班"),
            matcher,
            args=_make_message("小明 吐槽 加班"),
        )
        assert matcher.state["speaker"] == "小明"
        assert matcher.state["tags"] == ["吐槽", "加班"]


# ===========================================================================
# 辅助函数测试
# ===========================================================================


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


class TestFormatOcrText:
    """_format_ocr_text 测试。"""

    def test_short_text_returned_as_is(self) -> None:
        """短于等于 50 字的文本原样返回。"""
        assert add._format_ocr_text("心好累啊") == "心好累啊"

    def test_exactly_50_chars(self) -> None:
        """刚好 50 字不截断。"""
        text = "a" * 50
        assert add._format_ocr_text(text) == text

    def test_long_text_truncated(self) -> None:
        """超过 50 字截断并标注总长度。"""
        text = "a" * 60
        expected = "a" * 50 + "...（总文本长度60）"
        assert add._format_ocr_text(text) == expected

    def test_empty_string(self) -> None:
        """空字符串不截断。"""
        assert add._format_ocr_text("") == ""


# ===========================================================================
# _download_image 重试测试
# ===========================================================================


class TestDownloadImageRetry:
    """_download_image 重试行为测试。"""

    @pytest.mark.asyncio
    async def test_5xx_retries_and_succeeds(self) -> None:
        """5xx 服务器错误应重试，最终成功时返回内容。"""
        bad_response = MagicMock()
        bad_response.status_code = 503
        bad_response.headers = {}

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.headers = {"content-type": "image/jpeg"}
        good_response.content = b"fake_image"

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=[bad_response, good_response])

        with patch("httpx.AsyncClient", return_value=mock_client):
            content, response = await add._download_image(
                "https://img.example.com/a.jpg"
            )

        assert content == b"fake_image"
        assert response is good_response
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_network_error_retries_and_succeeds(self) -> None:
        """网络连接错误应重试，最终成功时返回内容。"""
        good_response = MagicMock()
        good_response.status_code = 200
        good_response.headers = {"content-type": "image/jpeg"}
        good_response.content = b"fake_image"

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(
            side_effect=[httpx.ConnectError("connection failed"), good_response]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            content, response = await add._download_image(
                "https://img.example.com/a.jpg"
            )

        assert content == b"fake_image"
        assert response is good_response
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_remote_protocol_error_retries_and_succeeds(self) -> None:
        """服务端中途断连（RemoteProtocolError）应重试，最终成功时返回内容。

        RemoteProtocolError 属于 ProtocolError 分支而非 NetworkError 子类，
        用于回归「Server disconnected without sending a response」未重试的问题。
        """
        good_response = MagicMock()
        good_response.status_code = 200
        good_response.headers = {"content-type": "image/jpeg"}
        good_response.content = b"fake_image"

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(
            side_effect=[
                httpx.RemoteProtocolError(
                    "Server disconnected without sending a response."
                ),
                good_response,
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            content, response = await add._download_image(
                "https://img.example.com/a.jpg"
            )

        assert content == b"fake_image"
        assert response is good_response
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_5xx_exhausts_retries_and_fails(self) -> None:
        """5xx 持续返回时，重试耗尽后抛出 DownloadServerError。"""
        bad_response = MagicMock()
        bad_response.status_code = 502
        bad_response.headers = {}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=bad_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(add.DownloadServerError):
                await add._download_image("https://img.example.com/a.jpg")

        # api_retry 默认最多 3 次尝试
        assert mock_client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_4xx_does_not_retry(self) -> None:
        """4xx 客户端错误不应重试，立即失败。"""
        from httpx import HTTPStatusError

        bad_response = MagicMock()
        bad_response.status_code = 404
        bad_response.headers = {}
        bad_response.raise_for_status.side_effect = HTTPStatusError(
            "Not Found", request=MagicMock(), response=bad_response
        )

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=bad_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPStatusError):
                await add._download_image("https://img.example.com/a.jpg")

        assert mock_client.get.await_count == 1


# ===========================================================================
# handle_add 测试
# ===========================================================================


class TestHandleAdd:
    """handle_add 处理函数测试。"""

    @pytest.mark.asyncio
    @patch.object(add, "is_authorized", return_value=False)
    async def test_unauthorized_rejected(
        self,
        mock_auth: MagicMock,
    ) -> None:
        """非授权用户应调用 finish(None) 结束匹配。"""
        matcher = _make_matcher()
        await handle_add(
            _make_bot(), _make_event("999"), matcher, args=_make_message("")
        )

        matcher.finish.assert_awaited_once_with(None)
        matcher.send.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(add, "get_index_manager")
    @patch.object(add, "is_authorized", return_value=True)
    async def test_group_chat_rejected(
        self, mock_auth: MagicMock, mock_get_im: MagicMock
    ) -> None:
        """群聊中调用 /add 应回复仅限私聊提示。"""
        event = MagicMock()
        event.get_user_id.return_value = "111"
        event.get_plaintext.return_value = "/add 测试"
        event.message_type = "group"
        event.message_id = 123456

        matcher = _make_matcher()
        await handle_add(_make_bot(), event, matcher, args=_make_message("测试"))

        matcher.finish.assert_awaited_once()
        msg = matcher.finish.await_args[0][0]
        assert "仅限私聊" in extract_message_text(msg)
        if isinstance(msg, Message):
            assert msg[0].type == "reply"
        mock_get_im.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(add, "get_index_manager")
    @patch.object(add, "is_authorized", return_value=True)
    @patch.object(add, "session_manager")
    async def test_authorized_proceeds(
        self,
        mock_sm: MagicMock,
        mock_auth: MagicMock,
        mock_get_im: MagicMock,
    ) -> None:
        """授权用户应正常激活会话。"""
        mock_sm.activate_chat.return_value = True
        mock_get_im.return_value = _make_index_manager()

        matcher = _make_matcher()
        await handle_add(
            _make_bot(), _make_event("111"), matcher, args=_make_message("")
        )

        mock_sm.activate_chat.assert_called_once_with(
            _make_scope("111"), "add", matcher
        )

    @pytest.mark.asyncio
    @patch.object(add, "is_authorized", return_value=True)
    @patch.object(add, "session_manager")
    async def test_existing_session_rejected(
        self,
        mock_sm: MagicMock,
        mock_auth: MagicMock,
    ) -> None:
        """激活失败（已有活跃会话）时应拒绝。"""
        mock_sm.activate_chat.return_value = False

        matcher = _make_matcher()
        await handle_add(
            _make_bot(), _make_event("111"), matcher, args=_make_message("")
        )

        matcher.finish.assert_awaited_once()
        msg = matcher.finish.await_args[0][0]
        assert "已有命令在处理中" in extract_message_text(msg)


# ===========================================================================
# got_image 测试
# ===========================================================================


class TestGotImage:
    """got_image 处理函数测试。"""

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=True)
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
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=True)
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
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "extract_image_urls", return_value=[])
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
        msg = matcher.reject.await_args[0][0]
        assert "图片" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_not_called()  # reject 后不反激活

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "get_index_manager", side_effect=RuntimeError("未初始化"))
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
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
        msg = matcher.finish.await_args[0][0]
        assert "未就绪" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("111"))

    @pytest.mark.asyncio
    async def test_selected_collection_path_and_snapshot_are_passed_atomically(
        self, tmp_path: Path
    ) -> None:
        """普通合集应写入其一级目录并把完整选择快照传给引擎。"""
        memes_dir = tmp_path / "memes"
        selection = CollectionSelection(1, "新三国")
        im = _make_index_manager()
        im.get_selected_collection.return_value = selection
        matcher = _make_matcher(state={})

        with (
            patch.object(add, "MEMES_DIR", memes_dir),
            patch.object(add, "is_authorized", return_value=True),
            patch.object(add, "session_manager") as mock_sm,
            patch.object(add, "get_index_manager", return_value=im),
            patch.object(add, "got_intercept_bypass", return_value=False),
            patch.object(add, "extract_image_urls", return_value=["https://img/a.jpg"]),
            patch.object(
                add,
                "_download_image",
                new=AsyncMock(return_value=(b"fake", _make_response())),
            ),
            patch.object(add, "_auto_filename", return_value="meme"),
        ):
            mock_sm.activate_chat.return_value = True
            await handle_add(
                _make_bot(), _make_event(), matcher, args=_make_message("")
            )
            await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        im.validate_collection_selection.assert_awaited_once_with(
            _make_scope(), selection
        )
        im.add.assert_awaited_once_with(
            "新三国/meme.jpg",
            speaker=None,
            tags=[],
            collection_id=1,
            scope=_make_scope(),
            expected_selection=selection,
        )
        assert (memes_dir / "新三国/meme.jpg").read_bytes() == b"fake"

    @pytest.mark.asyncio
    async def test_global_collection_saves_directly_under_memes(
        self, tmp_path: Path
    ) -> None:
        """选择 0 时图片应直接保存到 MEMES_DIR。"""
        memes_dir = tmp_path / "memes"
        selection = CollectionSelection(0, "全部合集")
        im = _make_index_manager()
        matcher = _make_matcher(state={"collection_selection": selection})

        with (
            patch.object(add, "MEMES_DIR", memes_dir),
            patch.object(add, "session_manager"),
            patch.object(add, "get_index_manager", return_value=im),
            patch.object(add, "got_intercept_bypass", return_value=False),
            patch.object(add, "extract_image_urls", return_value=["https://img/a.jpg"]),
            patch.object(
                add,
                "_download_image",
                new=AsyncMock(return_value=(b"fake", _make_response())),
            ),
            patch.object(add, "_auto_filename", return_value="meme"),
        ):
            await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        assert (memes_dir / "meme.jpg").read_bytes() == b"fake"
        im.add.assert_awaited_once_with(
            "meme.jpg",
            speaker=None,
            tags=[],
            collection_id=0,
            scope=_make_scope(),
            expected_selection=selection,
        )

    @pytest.mark.asyncio
    async def test_expired_selection_before_download_finishes_without_file(
        self, tmp_path: Path
    ) -> None:
        """下载前选择已失效时不得发起下载或留下文件。"""
        im = _make_index_manager()
        im.validate_collection_selection.side_effect = CollectionSelectionExpiredError()
        download = AsyncMock()
        matcher = _make_matcher(
            state={"collection_selection": CollectionSelection(1, "旧合集")}
        )

        with (
            patch.object(add, "MEMES_DIR", tmp_path / "memes"),
            patch.object(add, "session_manager"),
            patch.object(add, "get_index_manager", return_value=im),
            patch.object(add, "got_intercept_bypass", return_value=False),
            patch.object(add, "extract_image_urls", return_value=["https://img/a.jpg"]),
            patch.object(add, "_download_image", new=download),
        ):
            await got_image(_make_bot(), _make_event(), matcher, MagicMock())

        download.assert_not_awaited()
        assert extract_message_text(matcher.finish.await_args[0][0]) == (
            "当前合集已变化，请重新 /add"
        )
        assert not (tmp_path / "memes").exists()

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_success(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """正常流程应回复成功。"""

        im = _make_index_manager()
        im.add = AsyncMock(
            return_value=AddResult(
                entry_id=1,
                reason="added",
                text="加班心好累",
                public_id=MemePublicId(1, 3),
                collection_name="新三国",
            )
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
        msg = matcher.finish.await_args[0][0]
        assert "新增表情包" in extract_message_text(msg)
        im.add.assert_awaited_once_with(
            "a.jpg",
            speaker=None,
            tags=[],
            collection_id=0,
            scope=_make_scope(),
            expected_selection=CollectionSelection(0, "全部合集"),
        )
        assert "ID：1.3" in extract_message_text(msg)
        assert "1, " not in extract_message_text(msg)

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="meme")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_success_with_speaker_and_tags(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """带 speaker 和 tags 时应回复成功。"""

        im = _make_index_manager()
        im.add = AsyncMock(
            return_value=AddResult(
                entry_id=1,
                reason="added",
                text="加班心好累",
                public_id=MemePublicId(1, 3),
                collection_name="新三国",
            )
        )
        mock_get_im.return_value = im

        fake_file = tmp_path / "meme.jpg"
        fake_file.write_bytes(b"fake")
        mock_resolve.return_value = fake_file

        mock_download.return_value = (b"fake", _make_response())

        matcher = _make_matcher(state={"speaker": "小明", "tags": ["吐槽"]})
        bot = _make_bot()
        await got_image(bot, _make_event(), matcher, MagicMock())

        matcher.finish.assert_awaited_once()
        msg = matcher.finish.await_args[0][0]
        assert "新增表情包" in extract_message_text(msg)
        im.add.assert_awaited_once_with(
            "meme.jpg",
            speaker="小明",
            tags=["吐槽"],
            collection_id=0,
            scope=_make_scope(),
            expected_selection=CollectionSelection(0, "全部合集"),
        )
        assert "ID：1.3" in extract_message_text(msg)
        assert "1, " not in extract_message_text(msg)

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "_download_image", side_effect=RuntimeError("下载失败"))
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
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
        msg = matcher.finish.await_args[0][0]
        assert "下载失败" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_unsupported_extension_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
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
        msg = matcher.finish.await_args[0][0]
        assert "不支持的图片格式" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_compression_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """压缩失败时应回复对应错误。"""

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
        msg = matcher.finish.await_args[0][0]
        assert "压缩失败" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_ocr_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """OCR 失败时应回复对应错误。"""

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
        msg = matcher.finish.await_args[0][0]
        assert "OCR" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_embedding_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
        mock_resolve: MagicMock,
        mock_bypass: MagicMock,
        mock_sm: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Embedding 失败时应回复对应错误。"""

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
        msg = matcher.finish.await_args[0][0]
        assert "Embedding" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_generic_error_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
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
        msg = matcher.finish.await_args[0][0]
        assert "添加失败" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_lock_contention_in_got(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
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
        msg = matcher.finish.await_args[0][0]
        assert "索引正在刷新" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_add_cancelled_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
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
        msg = matcher.finish.await_args[0][0]
        assert "添加任务已取消" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "resolve_unique_filename")
    @patch.object(add, "_auto_filename", return_value="a")
    @patch.object(add, "_get_extension", return_value=".jpg")
    @patch.object(add, "_download_image")
    @patch.object(add, "get_index_manager")
    @patch.object(add, "extract_image_urls", return_value=["https://img.com/a.jpg"])
    async def test_add_timeout_replies(
        self,
        mock_extract: MagicMock,
        mock_get_im: MagicMock,
        mock_download: MagicMock,
        mock_ext: MagicMock,
        mock_auto: MagicMock,
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
        msg = matcher.finish.await_args[0][0]
        assert "添加处理超时" in extract_message_text(msg)
        mock_sm.deactivate_chat.assert_called_once_with(_make_scope("12345"))

    @pytest.mark.asyncio
    @patch.object(add, "session_manager")
    @patch.object(add, "got_intercept_bypass", return_value=False)
    @patch.object(add, "extract_image_urls", side_effect=ValueError("解析失败"))
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
