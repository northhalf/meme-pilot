"""OCR_PROVIDER 环境变量读取测试。"""

from unittest.mock import patch

from bot.config import read_ocr_provider


class TestReadOcrProvider:
    """read_ocr_provider() 测试。"""

    @patch.dict("os.environ", {}, clear=True)
    def test_default_is_paddle(self) -> None:
        """无环境变量时返回 'paddle'。"""
        assert read_ocr_provider() == "paddle"

    @patch.dict("os.environ", {"OCR_PROVIDER": "paddle"}, clear=True)
    def test_paddle(self) -> None:
        """OCR_PROVIDER=paddle 时返回 'paddle'。"""
        assert read_ocr_provider() == "paddle"

    @patch.dict("os.environ", {"OCR_PROVIDER": "deepseek"}, clear=True)
    def test_deepseek(self) -> None:
        """OCR_PROVIDER=deepseek 时返回 'deepseek'。"""
        assert read_ocr_provider() == "deepseek"

    @patch.dict("os.environ", {"OCR_PROVIDER": "  paddle  "}, clear=True)
    def test_whitespace_is_stripped(self) -> None:
        """值中的首尾空白被去除。"""
        assert read_ocr_provider() == "paddle"

    @patch.dict("os.environ", {"OCR_PROVIDER": "invalid-value"}, clear=True)
    def test_invalid_fallback_to_paddle(self) -> None:
        """无效值回退为 'paddle'。"""
        assert read_ocr_provider() == "paddle"
