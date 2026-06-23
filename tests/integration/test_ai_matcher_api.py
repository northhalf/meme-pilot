"""AIMatcher 真实 API 调用集成测试。

使用真实服务验证 AI 匹配的完整流程：
IndexManager(OCR+Embedding) → AIMatcher → EmbeddingService → RerankService

需要设置环境变量：
- SILICONFLOW_API_KEY（OCR 服务）
- EMBEDDING_API_KEY（Embedding 服务）
- DEEPSEEK_API_KEY（Rerank 服务）

运行方式：
    uv run pytest tests/integration/test_ai_matcher_api.py -v -s
"""

import os
import shutil
from pathlib import Path

import pytest
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.ai_matcher import AIMatcher
from bot.engine.embedding_service import EmbeddingService
from bot.engine.index_manager import IndexManager
from bot.engine.ocr_service import DeepSeekOcrService
from bot.engine.rerank_service import RerankService

# fixture 图片目录
FIXTURE_IMAGES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "images"

# 跳过条件：三个 API Key 均需要
pytestmark = pytest.mark.skipif(
    not os.environ.get("SILICONFLOW_API_KEY")
    or not os.environ.get("EMBEDDING_API_KEY")
    or not os.environ.get("DEEPSEEK_API_KEY"),
    reason="SILICONFLOW_API_KEY / EMBEDDING_API_KEY / DEEPSEEK_API_KEY 未全部设置，跳过集成测试",
)


@pytest.fixture
def work_dirs(tmp_path: Path) -> dict[str, Path]:
    """创建隔离的工作目录结构。"""
    data_dir = tmp_path / "data"
    memes_dir = tmp_path / "memes"
    no_text_dir = tmp_path / "meme_no_text"
    data_dir.mkdir()
    memes_dir.mkdir()
    return {"data_dir": data_dir, "memes_dir": memes_dir, "no_text_dir": no_text_dir}


@pytest.fixture
def ocr_service() -> DeepSeekOcrService:
    """创建真实的 DeepSeekOcrService 实例。"""
    return DeepSeekOcrService()


@pytest.fixture
def embedding_service() -> EmbeddingService:
    """创建真实的 EmbeddingService 实例。"""
    return EmbeddingService()


@pytest.fixture
def rerank_service() -> RerankService:
    """创建真实的 RerankService 实例。"""
    return RerankService()


def _copy_fixture_images(target_dir: Path, names: list[str]) -> None:
    """将 fixture 图片复制到目标目录。"""
    for name in names:
        shutil.copy2(FIXTURE_IMAGES_DIR / name, target_dir / name)


async def _build_index(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    image_names: list[str],
) -> IndexManager:
    """同步索引并返回就绪的 IndexManager。"""
    _copy_fixture_images(work_dirs["memes_dir"], image_names)

    manager = IndexManager(
        data_dir=str(work_dirs["data_dir"]),
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()
    await manager.sync_with_filesystem()
    return manager


@pytest.mark.asyncio
async def test_match_embedding_only(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：仅用 embedding 匹配，返回相似度最高的结果。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    manager = await _build_index(work_dirs, ocr_service, embedding_service, images)

    matcher = AIMatcher(
        index_provider=manager,
        embedding_provider=embedding_service,
    )

    result = await matcher.match("没办法了 听天由命")

    print(f"\n描述: 没办法了 听天由命")
    print(f"结果: {result}")

    assert result is not None
    assert result.source == "embedding"
    assert "听天由命吧" in result.text
    assert result.similarity > 0


@pytest.mark.asyncio
async def test_match_with_rerank(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    rerank_service: RerankService,
) -> None:
    """测试：embedding + rerank 精排，返回精排后的结果。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    manager = await _build_index(work_dirs, ocr_service, embedding_service, images)

    matcher = AIMatcher(
        index_provider=manager,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )

    result = await matcher.match("没办法了 只能认命")

    print(f"\n描述: 没办法了 只能认命")
    print(f"结果: {result}")

    assert result is not None
    assert result.source == "rerank"
    assert result.similarity > 0


@pytest.mark.asyncio
async def test_match_empty_description_returns_none(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：空描述返回 None。"""
    images = ["听天由命吧.png"]
    manager = await _build_index(work_dirs, ocr_service, embedding_service, images)

    matcher = AIMatcher(
        index_provider=manager,
        embedding_provider=embedding_service,
    )

    result = await matcher.match("")

    print(f"\n空描述结果: {result}")
    assert result is None


@pytest.mark.asyncio
async def test_match_returns_correct_filename(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：匹配结果包含正确的文件名。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    manager = await _build_index(work_dirs, ocr_service, embedding_service, images)

    matcher = AIMatcher(
        index_provider=manager,
        embedding_provider=embedding_service,
    )

    result = await matcher.match("放弃这个东西")

    print(f"\n描述: 放弃这个东西")
    print(f"结果: {result}")

    assert result is not None
    assert result.filename in images
