"""OpenAIOcrService 单元测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from bot.engine.openai_ocr import OpenAIOcrService


@pytest.fixture
def _no_async_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 tenacity 异步等待置空，避免重试测试耗时过长。"""

    async def _instant(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


class TestOpenAIOcrServiceInit:
    """构造函数测试。"""

    @patch("bot.engine.openai_ocr.AsyncOpenAI")
    @patch.dict("os.environ", {}, clear=True)
    def test_default_values(self, mock_openai_cls: MagicMock) -> None:
        """无参数无环境变量时 base_url 为 None，且因缺少 model 抛出 ValueError。"""
        with pytest.raises(ValueError, match="必须提供 OCR 模型名"):
            OpenAIOcrService()

    @patch("bot.engine.openai_ocr.AsyncOpenAI")
    @patch.dict(
        "os.environ",
        {
            "OPENAI_OCR_API_KEY": "sf-key",
            "OPENAI_OCR_BASE_URL": "https://custom.api/v1",
            "OPENAI_OCR_MODEL": "custom-model",
        },
    )
    def test_from_env_vars(self, mock_openai_cls: MagicMock) -> None:
        """从环境变量读取配置。"""
        service = OpenAIOcrService()
        assert service._api_key == "sf-key"
        assert service._base_url == "https://custom.api/v1"
        assert service._model == "custom-model"

    @patch("bot.engine.openai_ocr.AsyncOpenAI")
    @patch.dict(
        "os.environ",
        {
            "OPENAI_OCR_API_KEY": "env-key",
            "OPENAI_OCR_BASE_URL": "https://env.api/v1",
            "OPENAI_OCR_MODEL": "env-model",
        },
    )
    def test_constructor_params_override_env(self, mock_openai_cls: MagicMock) -> None:
        """构造参数优先于环境变量。"""
        service = OpenAIOcrService(
            api_key="explicit-key",
            base_url="https://explicit.api/v1",
            model="explicit-model",
        )
        assert service._api_key == "explicit-key"
        assert service._base_url == "https://explicit.api/v1"
        assert service._model == "explicit-model"

    @patch("bot.engine.openai_ocr.AsyncOpenAI")
    def test_client_wired_correctly(self, mock_openai_cls: MagicMock) -> None:
        """验证 AsyncOpenAI 使用正确的参数构造，且关闭 SDK 内部重试。"""
        OpenAIOcrService(
            api_key="my-key",
            base_url="https://my.api/v1",
            model="my-model",
        )
        mock_openai_cls.assert_called_once_with(
            api_key="my-key",
            base_url="https://my.api/v1",
            max_retries=0,
        )


class TestOcr:
    """ocr 方法测试。"""

    @pytest.mark.asyncio
    async def test_normal_ocr_with_ref_tags(self, tmp_path) -> None:
        """正常 OCR 返回带 ref 标记的文本并清洗。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        # mock AsyncOpenAI 返回值
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = (
            "<|ref|>不可惊扰<|/ref|><|det|>[[123,456]]<|/det|>"
            " <|ref|>先生真乃奇人也<|/ref|><|det|>[[789,0]]<|/det|>"
        )
        mock_response.choices = [mock_choice]

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == "不可惊扰先生真乃奇人也"

    @pytest.mark.asyncio
    async def test_ocr_without_ref_tags(self, tmp_path) -> None:
        """OCR 返回无 ref 标记的纯文本。"""
        img = tmp_path / "test.jpg"
        img.write_text("fake-jpg-data")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "纯文本识别结果"
        mock_response.choices = [mock_choice]

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == "纯文本识别结果"

    @pytest.mark.asyncio
    async def test_empty_api_response(self, tmp_path) -> None:
        """API 返回空内容时返回空字符串。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_response.choices = [mock_choice]

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == ""

    @pytest.mark.asyncio
    async def test_file_not_found(self) -> None:
        """文件不存在时抛出 FileNotFoundError。"""
        service = OpenAIOcrService(api_key="test-key", model="test-model")
        with pytest.raises(FileNotFoundError, match="图片文件不存在"):
            await service.ocr("/不存在/的/文件.png")

    @pytest.mark.asyncio
    async def test_unsupported_format(self, tmp_path) -> None:
        """不支持的图片格式抛出 ValueError。"""
        img = tmp_path / "test.tiff"
        img.write_text("fake-data")

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        with pytest.raises(ValueError, match="不支持的图片格式"):
            await service.ocr(str(img))

    @pytest.mark.asyncio
    async def test_api_failure_raises_runtime_error(self, tmp_path) -> None:
        """非 OpenAI APIError 的异常被包装为 RuntimeError。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(
            side_effect=Exception("API Error")
        )

        with pytest.raises(RuntimeError, match="OCR API 调用失败"):
            await service.ocr(str(img))

    @pytest.mark.asyncio
    async def test_ocr_strips_all_whitespace(self, tmp_path) -> None:
        """OCR 返回去除所有空白字符。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = (
            "<|ref|>加 班\t心 累<|/ref|><|det|>[[1,2]]<|/det|>"
        )
        mock_response.choices = [mock_choice]

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == "加班心累"


class TestOcrRetry:
    """验证 ocr 方法的网络重试行为。"""

    def _make_api_error(self) -> openai.APIConnectionError:
        """构造一个可重试的 OpenAI 连接错误。"""
        return openai.APIConnectionError(message="connection lost", request=MagicMock())

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_retry_succeeds_after_two_connection_errors(self, tmp_path) -> None:
        """连续 2 次 APIConnectionError 后成功，应返回正确结果。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "识别成功"
        mock_response.choices = [mock_choice]

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(
            side_effect=[
                self._make_api_error(),
                self._make_api_error(),
                mock_response,
            ]
        )

        result = await service.ocr(str(img))
        assert result == "识别成功"
        assert service._client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_retry_exhausted_raises_last_exception(self, tmp_path) -> None:
        """连续 3 次 APIConnectionError 后应抛出最后一个异常。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock(
            side_effect=[
                self._make_api_error(),
                self._make_api_error(),
                self._make_api_error(),
            ]
        )

        with pytest.raises(openai.APIConnectionError):
            await service.ocr(str(img))
        assert service._client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_value_error_not_retried(self, tmp_path) -> None:
        """本地业务异常 ValueError 不应触发重试。"""
        img = tmp_path / "test.tiff"
        img.write_text("fake-data")

        service = OpenAIOcrService(api_key="test-key", model="test-model")
        service._client.chat.completions.create = AsyncMock()

        with pytest.raises(ValueError, match="不支持的图片格式"):
            await service.ocr(str(img))

        service._client.chat.completions.create.assert_not_called()


class TestOpenAIOcrSemaphore:
    """验证 OpenAIOcrService 的 Semaphore 并发控制。"""

    @pytest.mark.asyncio
    async def test_default_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不传 concurrency 时使用环境变量默认值 (5)。"""
        monkeypatch.delenv("OCR_CONCURRENCY", raising=False)
        service = OpenAIOcrService(api_key="sk-test", model="test-model")
        assert service._semaphore._value == 5

    @pytest.mark.asyncio
    async def test_custom_concurrency(self) -> None:
        """传 concurrency=2 时 Semaphore 值为 2。"""
        service = OpenAIOcrService(api_key="sk-test", model="test-model", concurrency=2)
        assert service._semaphore._value == 2

    @pytest.mark.asyncio
    async def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """设置 OCR_CONCURRENCY 环境变量时生效。"""
        monkeypatch.setenv("OCR_CONCURRENCY", "3")
        service = OpenAIOcrService(api_key="sk-test", model="test-model")
        assert service._semaphore._value == 3

    @pytest.mark.asyncio
    async def test_semaphore_blocks_concurrent(self, tmp_path: Path) -> None:
        """concurrency=1 时第二个并发调用应阻塞。"""
        service = OpenAIOcrService(
            api_key="sk-test",
            base_url="http://test",
            model="test-model",
            concurrency=1,
        )

        img1 = tmp_path / "test1.png"
        img1.write_text("fake-data")
        img2 = tmp_path / "test2.png"
        img2.write_text("fake-data")

        async def slow_create(*args: object, **kwargs: object) -> MagicMock:
            await asyncio.sleep(10)
            mock_r = MagicMock()
            mock_r.choices = [MagicMock()]
            mock_r.choices[0].message.content = "text"
            return mock_r

        service._client.chat.completions.create = AsyncMock(side_effect=slow_create)

        task1 = asyncio.create_task(service.ocr(str(img1)))
        await asyncio.sleep(0.05)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service.ocr(str(img2)), timeout=0.1)

        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
