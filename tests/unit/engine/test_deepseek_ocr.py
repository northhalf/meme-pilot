"""DeepSeekOcrService 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.deepseek_ocr import DeepSeekOcrService


class TestDeepSeekOcrServiceInit:
    """构造函数测试。"""

    @patch("bot.engine.deepseek_ocr.AsyncOpenAI")
    @patch.dict("os.environ", {}, clear=True)
    def test_default_values(self, mock_openai_cls: MagicMock) -> None:
        """无参数无环境变量时使用默认值。"""
        service = DeepSeekOcrService()
        assert service._model == "deepseek-ai/DeepSeek-OCR"
        assert service._base_url == "https://api.siliconflow.cn/v1"

    @patch("bot.engine.deepseek_ocr.AsyncOpenAI")
    @patch.dict(
        "os.environ",
        {
            "SILICONFLOW_API_KEY": "sf-key",
            "SILICONFLOW_BASE_URL": "https://custom.api/v1",
            "SILICONFLOW_OCR_MODEL": "custom-model",
        },
    )
    def test_from_env_vars(self, mock_openai_cls: MagicMock) -> None:
        """从环境变量读取配置。"""
        service = DeepSeekOcrService()
        assert service._api_key == "sf-key"
        assert service._base_url == "https://custom.api/v1"
        assert service._model == "custom-model"

    @patch("bot.engine.deepseek_ocr.AsyncOpenAI")
    def test_constructor_params_override_env(self, mock_openai_cls: MagicMock) -> None:
        """构造参数优先于环境变量。"""
        service = DeepSeekOcrService(
            api_key="explicit-key",
            base_url="https://explicit.api/v1",
            model="explicit-model",
        )
        assert service._api_key == "explicit-key"
        assert service._base_url == "https://explicit.api/v1"
        assert service._model == "explicit-model"

    @patch("bot.engine.deepseek_ocr.AsyncOpenAI")
    def test_client_wired_correctly(self, mock_openai_cls: MagicMock) -> None:
        """验证 AsyncOpenAI 使用正确的参数构造。"""
        DeepSeekOcrService(api_key="my-key", base_url="https://my.api/v1")
        mock_openai_cls.assert_called_once_with(
            api_key="my-key",
            base_url="https://my.api/v1",
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

        service = DeepSeekOcrService(api_key="test-key")
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

        service = DeepSeekOcrService(api_key="test-key")
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

        service = DeepSeekOcrService(api_key="test-key")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == ""

    @pytest.mark.asyncio
    async def test_file_not_found(self) -> None:
        """文件不存在时抛出 FileNotFoundError。"""
        service = DeepSeekOcrService(api_key="test-key")
        with pytest.raises(FileNotFoundError, match="图片文件不存在"):
            await service.ocr("/不存在/的/文件.png")

    @pytest.mark.asyncio
    async def test_unsupported_format(self, tmp_path) -> None:
        """不支持的图片格式抛出 ValueError。"""
        img = tmp_path / "test.tiff"
        img.write_text("fake-data")

        service = DeepSeekOcrService(api_key="test-key")
        with pytest.raises(ValueError, match="不支持的图片格式"):
            await service.ocr(str(img))

    @pytest.mark.asyncio
    async def test_api_failure_raises_runtime_error(self, tmp_path) -> None:
        """API 调用失败抛出 RuntimeError。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        service = DeepSeekOcrService(api_key="test-key")
        service._client.chat.completions.create = AsyncMock(
            side_effect=Exception("API Error")
        )

        with pytest.raises(RuntimeError, match="DeepSeek-OCR API 调用失败"):
            await service.ocr(str(img))

    @pytest.mark.asyncio
    async def test_ocr_strips_all_whitespace(self, tmp_path) -> None:
        """OCR 返回去除所有空白字符。"""
        img = tmp_path / "test.png"
        img.write_text("fake-png-data")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "<|ref|>加 班\t心 累<|/ref|><|det|>[[1,2]]<|/det|>"
        mock_response.choices = [mock_choice]

        service = DeepSeekOcrService(api_key="test-key")
        service._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await service.ocr(str(img))
        assert result == "加班心累"
