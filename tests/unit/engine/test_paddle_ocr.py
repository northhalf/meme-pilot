"""PaddleOcrClientService 单元测试。"""

from __future__ import annotations

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
        assert result == "第一行 第二行"

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
        assert result == "hello world"

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
