"""PaddleOcrClientService 真实 API 调用集成测试。

需要设置环境变量 PADDLEOCR_ACCESS_TOKEN 才能运行。
可选设置 PADDLEOCR_BASE_URL（默认使用 SDK 内置地址）。

运行方式：
    uv run pytest tests/integration/test_paddle_ocr_api.py -v -s
"""

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.paddle_ocr import PaddleOcrClientService

# fixture 图片目录
IMAGES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "images"

# 跳过条件：未设置 Access Token 时跳过
pytestmark = pytest.mark.skipif(
    not os.environ.get("PADDLEOCR_ACCESS_TOKEN"),
    reason="PADDLEOCR_ACCESS_TOKEN 未设置，跳过集成测试",
)


@pytest_asyncio.fixture
async def paddle_ocr() -> AsyncGenerator[PaddleOcrClientService, None]:
    """创建真实的 PaddleOcrClientService 实例。"""
    service = PaddleOcrClientService()
    yield service
    await service.close()


@pytest.mark.asyncio
async def test_ocr_jing_rao(paddle_ocr: PaddleOcrClientService) -> None:
    """测试：不可惊扰先生真乃奇人也.png"""
    image_path = IMAGES_DIR / "不可惊扰先生真乃奇人也.png"
    result = await paddle_ocr.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"PaddleOCR 结果: {result!r}")

    assert "不可惊扰" in result
    assert "先生真乃奇人也" in result


@pytest.mark.asyncio
async def test_ocr_qi_zhi(paddle_ocr: PaddleOcrClientService) -> None:
    """测试：不能用就弃之.png"""
    image_path = IMAGES_DIR / "不能用就弃之.png"
    result = await paddle_ocr.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"PaddleOCR 结果: {result!r}")

    assert "不能用" in result
    assert "弃之" in result


@pytest.mark.asyncio
async def test_ocr_jing_xiang(paddle_ocr: PaddleOcrClientService) -> None:
    """测试：与曹军铁骑下的乱世相比荆襄简直就是天上人间.png"""
    image_path = IMAGES_DIR / "与曹军铁骑下的乱世相比荆襄简直就是天上人间.png"
    result = await paddle_ocr.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"PaddleOCR 结果: {result!r}")

    assert "与曹军铁骑下的乱世相比" in result
    assert "荆襄简直就是天上人间" in result


@pytest.mark.asyncio
async def test_ocr_tian_ming(paddle_ocr: PaddleOcrClientService) -> None:
    """测试：听天由命吧.png"""
    image_path = IMAGES_DIR / "听天由命吧.png"
    result = await paddle_ocr.ocr(str(image_path))

    print(f"\n图片: {image_path.name}")
    print(f"PaddleOCR 结果: {result!r}")

    assert "听天由命吧" in result
