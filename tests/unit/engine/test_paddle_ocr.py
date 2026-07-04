"""PaddleOcrClientService 单元测试。"""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.paddle_ocr import PaddleOcrClientService


class TestPaddleOcrClientServiceInit:
    """构造函数测试。"""

    @patch("bot.engine.paddle_ocr.AsyncPaddleOCRClient")
    @patch.dict("os.environ", {"PADDLEOCR_ACCESS_TOKEN": ""})
    def test_default_values(self, mock_client_cls: MagicMock) -> None:
        """无参数无环境变量时使用默认值。"""
        service = PaddleOcrClientService()
        mock_client_cls.assert_called_once()
        _, kwargs = mock_client_cls.call_args
        # 默认 token 为空字符串
        assert kwargs.get("token") == ""
        assert kwargs.get("request_timeout") == 300.0
        assert kwargs.get("poll_timeout") == 600.0

    @patch("bot.engine.paddle_ocr.AsyncPaddleOCRClient")
    @patch.dict(
        "os.environ",
        {"PADDLEOCR_ACCESS_TOKEN": "my-access-token"},
    )
    def test_from_env_var(self, mock_client_cls: MagicMock) -> None:
        """从环境变量读取 access_token。"""
        service = PaddleOcrClientService()
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("token") == "my-access-token"

    @patch("bot.engine.paddle_ocr.AsyncPaddleOCRClient")
    def test_constructor_params_override_env(self, mock_client_cls: MagicMock) -> None:
        """构造参数优先于环境变量。"""
        service = PaddleOcrClientService(access_token="explicit-token")
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("token") == "explicit-token"

    @patch("bot.engine.paddle_ocr.AsyncPaddleOCRClient")
    @patch.dict(
        "os.environ",
        {
            "PADDLEOCR_ACCESS_TOKEN": "env-token",
            "PADDLEOCR_BASE_URL": "https://custom.api.com",
        },
    )
    def test_base_url_from_env(self, mock_client_cls: MagicMock) -> None:
        """PADDLEOCR_BASE_URL 传递到 AsyncPaddleOCRClient。"""
        service = PaddleOcrClientService()
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("base_url") == "https://custom.api.com"


class TestOcr:
    """ocr 方法测试。"""

    @pytest.mark.asyncio
    async def test_ocr_returns_text_from_pruned_result(self) -> None:
        """OCR 正常返回文本（pruned_result 为字符串）。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = "识别到的文本内容"
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "识别到的文本内容"
        mock_client.ocr.assert_called_once_with(
            file_path="/path/to/image.png",
            model=service._model,
            options=service._ocr_options,
        )

    @pytest.mark.asyncio
    async def test_pruned_result_is_list_of_dicts(self) -> None:
        """pruned_result 为 list[dict] 时提取 text 字段拼接。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = [
            {"text": "第一行", "score": 0.95},
            {"text": "第二行", "score": 0.88},
        ]
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "第一行第二行"

    @pytest.mark.asyncio
    async def test_pruned_result_is_none(self) -> None:
        """pruned_result 为 None 时返回空字符串。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = None
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_pages_returns_empty_string(self) -> None:
        """无识别结果时返回空字符串。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_ocr_result.pages = []
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == ""

    @pytest.mark.asyncio
    async def test_pruned_result_is_list_of_strings(self) -> None:
        """pruned_result 为 list[str] 时直接拼接。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = ["hello", "world"]
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "helloworld"

    @pytest.mark.asyncio
    async def test_pruned_result_unexpected_type_fallback(self) -> None:
        """pruned_result 为意外类型时使用 str() 兜底。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = 12345
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "12345"

    @pytest.mark.asyncio
    async def test_pruned_result_dict_with_text_key(self) -> None:
        """pruned_result 为 dict 时尝试提取 text 字段。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {"text": "从dict提取的文本"}
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "从dict提取的文本"

    @pytest.mark.asyncio
    async def test_pruned_result_dict_with_rec_texts(self) -> None:
        """pruned_result 为含 rec_texts 的 dict 时提取文本（PaddleOCR 新版 API 格式）。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {
            "model_settings": {"use_doc_preprocessor": True},
            "text_type": "general",
            "rec_texts": ["你走了我们吃什么"],
            "rec_scores": [0.99995],
            "textline_orientation_angles": [0],
        }
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "你走了我们吃什么"

    @pytest.mark.asyncio
    async def test_pruned_result_dict_with_rec_texts_filters_low_score(self) -> None:
        """rec_scores 低于阈值时对应文本被过滤（PaddleOCR 新版 API 多行格式）。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {
            "rec_texts": ["皇叔入住的话", "能使我东吴人丁兴旺", "模糊不清"],
            "rec_scores": [0.999, 0.97, 0.85],
        }
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(
            access_token="test-token", text_rec_score_thresh=0.9
        )
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        # "模糊不清" 得分 0.85 < 0.9 应被过滤
        assert result == "皇叔入住的话能使我东吴人丁兴旺"

    @pytest.mark.asyncio
    async def test_rec_texts_without_scores_all_included(self) -> None:
        """rec_texts 无 rec_scores 时全部保留（向后兼容）。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {
            "rec_texts": ["第一行", "第二行"],
        }
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "第一行第二行"

    @pytest.mark.asyncio
    async def test_all_texts_below_threshold_returns_empty(self) -> None:
        """所有文本行均低于阈值时返回空字符串。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {
            "rec_texts": ["低分文本"],
            "rec_scores": [0.3],
        }
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(
            access_token="test-token", text_rec_score_thresh=0.9
        )
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == ""

    @pytest.mark.asyncio
    async def test_api_auth_error_raises_runtime_error(self) -> None:
        """PaddleOCRAPIError 转为 RuntimeError。"""
        from paddleocr import PaddleOCRAPIError

        mock_client = MagicMock()
        mock_client.ocr = AsyncMock(side_effect=PaddleOCRAPIError("认证失败"))

        service = PaddleOcrClientService(access_token="bad-token")
        service._client = mock_client

        with pytest.raises(RuntimeError, match="PaddleOCR API 调用失败"):
            await service.ocr("/path/to/image.png")

    @pytest.mark.asyncio
    async def test_close_releases_client(self) -> None:
        """close() 调用 _client.close()。"""
        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        await service.close()
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ocr_strips_all_whitespace(self) -> None:
        """OCR 返回去除所有空白字符。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = "加 班\t心\n累"
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "加班心累"


class TestPaddleOcrSemaphore:
    """验证 PaddleOcrClientService 的 Semaphore 并发控制。"""

    @pytest.mark.asyncio
    async def test_default_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不传 concurrency 时使用环境变量默认值 (5)。"""
        monkeypatch.delenv("OCR_CONCURRENCY", raising=False)
        service = PaddleOcrClientService(access_token="test")
        assert service._semaphore._value == 5

    @pytest.mark.asyncio
    async def test_custom_concurrency(self) -> None:
        """传 concurrency=2 时 Semaphore 值为 2。"""
        service = PaddleOcrClientService(access_token="test", concurrency=2)
        assert service._semaphore._value == 2

    @pytest.mark.asyncio
    async def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """设置 OCR_CONCURRENCY 环境变量时生效。"""
        monkeypatch.setenv("OCR_CONCURRENCY", "3")
        service = PaddleOcrClientService(access_token="test")
        assert service._semaphore._value == 3

    @pytest.mark.asyncio
    async def test_semaphore_blocks_concurrent(self) -> None:
        """concurrency=1 时第二个并发调用应阻塞。"""
        service = PaddleOcrClientService(access_token="test", concurrency=1)

        async def slow_ocr(*args: object, **kwargs: object) -> MagicMock:
            await asyncio.sleep(10)
            mock_result = MagicMock()
            mock_result.pages = []
            return mock_result

        service._client.ocr = AsyncMock(side_effect=slow_ocr)

        task1 = asyncio.create_task(service.ocr("/fake/path1.png"))
        await asyncio.sleep(0.05)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service.ocr("/fake/path2.png"), timeout=0.1)

        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
