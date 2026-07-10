"""PaddleOcrClientService 单元测试。"""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from paddleocr import AuthError, NetworkError, PaddleOCRAPIError

from bot.engine.paddle_ocr import PaddleOcrClientService


@pytest.fixture
def _no_async_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 tenacity 异步等待置空，避免重试测试耗时过长。"""

    async def _instant(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


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
    async def test_ocr_returns_text_from_rec_texts(self) -> None:
        """OCR 正常返回文本（pruned_result 含 rec_texts）。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {
            "rec_texts": ["识别到的文本内容"],
            "rec_scores": [0.9999],
        }
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
    async def test_pruned_result_dict_with_overall_ocr_res(self) -> None:
        """PP-Structure v3 格式：文本嵌套在 overall_ocr_res 下。"""
        mock_client = MagicMock()
        mock_ocr_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {
            "page_count": 1,
            "overall_ocr_res": {
                "rec_texts": ["酒", "你是想收买我"],
                "rec_scores": [0.61, 0.999],
            },
        }
        mock_ocr_result.pages = [mock_page]
        mock_client.ocr = AsyncMock(return_value=mock_ocr_result)

        service = PaddleOcrClientService(
            access_token="test-token", text_rec_score_thresh=0.5
        )
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "酒你是想收买我"

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
    async def test_unretryable_api_error_raises_original(self) -> None:
        """不可重试的 PaddleOCRAPIError 以原始类型透传，不包装为 RuntimeError。"""
        mock_client = MagicMock()
        mock_client.ocr = AsyncMock(side_effect=PaddleOCRAPIError("认证失败"))

        service = PaddleOcrClientService(access_token="bad-token")
        service._client = mock_client

        with pytest.raises(PaddleOCRAPIError, match="认证失败"):
            await service.ocr("/path/to/image.png")
        mock_client.ocr.assert_called_once()

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
        mock_page.pruned_result = {
            "rec_texts": ["加 班", "心\n累"],
            "rec_scores": [0.99, 0.99],
        }
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


class TestOcrRetry:
    """验证 ocr 方法的网络重试行为。"""

    def _make_retryable_error(self) -> NetworkError:
        """构造一个可重试的 PaddleOCR 网络错误。"""
        return NetworkError("connection lost")

    def _make_success_result(self) -> MagicMock:
        """构造正常返回单页文本的 OCR 结果。"""
        mock_result = MagicMock()
        mock_page = MagicMock()
        mock_page.pruned_result = {"rec_texts": ["识别成功"], "rec_scores": [0.99]}
        mock_result.pages = [mock_page]
        return mock_result

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_retry_succeeds_after_two_network_errors(self) -> None:
        """连续 2 次 NetworkError 后成功，应返回正确结果。"""
        mock_client = MagicMock()
        mock_client.ocr = AsyncMock(
            side_effect=[
                self._make_retryable_error(),
                self._make_retryable_error(),
                self._make_success_result(),
            ]
        )

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        result = await service.ocr("/path/to/image.png")
        assert result == "识别成功"
        assert mock_client.ocr.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_retry_exhausted_raises_last_exception(self) -> None:
        """连续 3 次 NetworkError 后应抛出最后一个异常（原始类型）。"""
        mock_client = MagicMock()
        mock_client.ocr = AsyncMock(
            side_effect=[
                self._make_retryable_error(),
                self._make_retryable_error(),
                self._make_retryable_error(),
            ]
        )

        service = PaddleOcrClientService(access_token="test-token")
        service._client = mock_client

        with pytest.raises(NetworkError):
            await service.ocr("/path/to/image.png")
        assert mock_client.ocr.call_count == 3

    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self) -> None:
        """不可重试的 AuthError 不应触发重试，直接抛出。"""
        mock_client = MagicMock()
        mock_client.ocr = AsyncMock(side_effect=AuthError("token invalid"))

        service = PaddleOcrClientService(access_token="bad-token")
        service._client = mock_client

        with pytest.raises(AuthError, match="token invalid"):
            await service.ocr("/path/to/image.png")
        mock_client.ocr.assert_called_once()
