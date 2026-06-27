"""DeepSeekOcrService 真实 API 调用集成测试。

需要设置环境变量 SILICONFLOW_API_KEY 才能运行。
可选设置 SILICONFLOW_BASE_URL 和 SILICONFLOW_OCR_MODEL。

运行方式：
    uv run pytest tests/integration/test_ocr_service_api.py -v -s
"""

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.deepseek_ocr import DeepSeekOcrService

# fixture 图片目录
IMAGES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "images"

# 跳过条件：未设置 API Key 时跳过
pytestmark = pytest.mark.skipif(
    not os.environ.get("SILICONFLOW_API_KEY"),
    reason="SILICONFLOW_API_KEY 未设置，跳过集成测试",
)


@pytest_asyncio.fixture
async def ocr_service() -> AsyncGenerator[DeepSeekOcrService, None]:
    """创建真实的 DeepSeekOcrService 实例。"""
    service = DeepSeekOcrService()
    yield service
    await service.close()


@pytest.mark.asyncio
async def test_ocr_jing_rao(ocr_service: DeepSeekOcrService) -> None:
    """测试：不可惊扰先生真乃奇人也.png → 不可惊扰 先生真乃奇人也"""
    image_path = IMAGES_DIR / "不可惊扰先生真乃奇人也.png"
    result = await ocr_service.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"OCR 结果: {result!r}")

    assert "不可惊扰" in result
    assert "先生真乃奇人也" in result


@pytest.mark.asyncio
async def test_ocr_qi_zhi(ocr_service: DeepSeekOcrService) -> None:
    """测试：不能用就弃之.png → 不能用 就弃之"""
    image_path = IMAGES_DIR / "不能用就弃之.png"
    result = await ocr_service.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"OCR 结果: {result!r}")

    assert "不能用" in result
    assert "弃之" in result


@pytest.mark.asyncio
async def test_ocr_jing_xiang(ocr_service: DeepSeekOcrService) -> None:
    """测试：与曹军铁骑下的乱世相比荆襄简直就是天上人间.png → 与曹军铁骑下的乱世相比 荆襄简直就是天上人间啊"""
    image_path = IMAGES_DIR / "与曹军铁骑下的乱世相比荆襄简直就是天上人间.png"
    result = await ocr_service.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"OCR 结果: {result!r}")

    assert "与曹军铁骑下的乱世相比" in result
    assert "荆襄简直就是天上人间" in result


@pytest.mark.asyncio
async def test_ocr_tian_ming(ocr_service: DeepSeekOcrService) -> None:
    """测试：听天由命吧.png → 听天由命吧"""
    image_path = IMAGES_DIR / "听天由命吧.png"
    result = await ocr_service.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"OCR 结果: {result!r}")

    assert "听天由命吧" in result
