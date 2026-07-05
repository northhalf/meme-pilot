"""RapidOcrService 单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from bot.engine.rapidocr_ocr import RapidOcrService, create_rapidocr_service


@pytest.mark.anyio
async def test_ocr_returns_cleaned_text() -> None:
    """ocr 应返回去除空白后的文本。"""
    service = RapidOcrService(text_score=0.9)
    fake_result = MagicMock()
    fake_result.txts = ("hello  world", "第二行")
    fake_result.scores = (0.95, 0.99)

    with (
        patch("os.path.exists", return_value=True),
        patch("asyncio.to_thread", return_value=fake_result) as mock_to_thread,
    ):
        text = await service.ocr("/tmp/fake.png")
        assert text == "helloworld第二行"
        mock_to_thread.assert_awaited_once()
        # 关闭方向分类，仅做检测+识别
        _, kwargs = mock_to_thread.call_args
        assert kwargs == {"use_det": True, "use_cls": False, "use_rec": True}


@pytest.mark.anyio
async def test_ocr_filters_low_score_lines() -> None:
    """低于 text_score 置信度的文本行应被过滤。"""
    service = RapidOcrService(text_score=0.9)
    fake_result = MagicMock()
    fake_result.txts = ("清晰文本", "模糊文本")
    fake_result.scores = (0.99, 0.5)

    with (
        patch("os.path.exists", return_value=True),
        patch("asyncio.to_thread", return_value=fake_result),
    ):
        text = await service.ocr("/tmp/fake.png")
        assert text == "清晰文本"


@pytest.mark.anyio
async def test_ocr_returns_empty_when_no_txts() -> None:
    """结果对象无 txts 时返回空字符串。"""
    service = RapidOcrService(text_score=0.9)
    fake_result = MagicMock()
    fake_result.txts = ()
    fake_result.scores = ()

    with (
        patch("os.path.exists", return_value=True),
        patch("asyncio.to_thread", return_value=fake_result),
    ):
        text = await service.ocr("/tmp/fake.png")
        assert text == ""


@pytest.mark.anyio
async def test_ocr_raises_file_not_found() -> None:
    """图片文件不存在时应抛出 FileNotFoundError。"""
    service = RapidOcrService(text_score=0.9)

    with pytest.raises(FileNotFoundError, match="图片文件不存在"):
        await service.ocr("/tmp/does_not_exist.png")


def test_create_rapidocr_service_uses_env() -> None:
    """工厂函数应从环境变量读取 text_score 与 concurrency。"""
    with patch.dict(
        "os.environ",
        {"OCR_TEXT_SCORE": "0.8", "OCR_CONCURRENCY": "3"},
        clear=False,
    ):
        service = create_rapidocr_service()
        assert service._text_score == 0.8
