"""AIMatcher 真实 API 调用集成测试。

使用真实服务验证 AI 匹配的完整流程：
IndexManager(OCR+Embedding) → AIMatcher → OpenAIEmbeddingService → RerankService

需要设置环境变量：
- OPENAI_OCR_API_KEY（OCR 服务）
- OPENAI_EMBEDDING_API_KEY（Embedding 服务）
- DEEPSEEK_API_KEY（Rerank 服务）

运行方式：
    uv run pytest tests/integration/test_ai_matcher_api.py -v -s
"""

import os
import shutil
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.ai_matcher import AIMatcher  # noqa: E402
from bot.engine.index_manager import IndexManager  # noqa: E402
from bot.engine.openai_embedding import OpenAIEmbeddingService  # noqa: E402
from bot.engine.openai_ocr import OpenAIOcrService  # noqa: E402
from bot.engine.metadata_store import MetadataStore  # noqa: E402
from bot.engine.rerank_service import RerankService  # noqa: E402
from bot.engine.vector_store import VectorStore  # noqa: E402

# fixture 图片目录
FIXTURE_IMAGES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "images"

# 跳过条件：三个 API Key 均需要
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_OCR_API_KEY")
    or not os.environ.get("OPENAI_EMBEDDING_API_KEY")
    or not os.environ.get("DEEPSEEK_API_KEY"),
    reason="OPENAI_OCR_API_KEY / OPENAI_EMBEDDING_API_KEY / DEEPSEEK_API_KEY 未全部设置，跳过集成测试",
)


@pytest.fixture
def work_dirs(tmp_path: Path) -> dict[str, Path]:
    """创建隔离的工作目录结构。"""
    data_dir = tmp_path / "data"
    memes_dir = tmp_path / "memes"
    no_text_dir = tmp_path / "meme_no_text"
    index_db = data_dir / "index.db"
    chroma_dir = data_dir / "chroma"
    data_dir.mkdir()
    memes_dir.mkdir()
    return {
        "data_dir": data_dir,
        "memes_dir": memes_dir,
        "no_text_dir": no_text_dir,
        "index_db": index_db,
        "chroma_dir": chroma_dir,
    }


@pytest_asyncio.fixture
async def ocr_service() -> AsyncGenerator[OpenAIOcrService, None]:
    """创建真实的 OpenAIOcrService 实例。"""
    service = OpenAIOcrService()
    yield service
    await service._client.close()


@pytest_asyncio.fixture
async def embedding_service() -> AsyncGenerator[OpenAIEmbeddingService, None]:
    """创建真实的 OpenAIEmbeddingService 实例。"""
    service = OpenAIEmbeddingService()
    yield service
    await service._client.close()


@pytest_asyncio.fixture
async def rerank_service() -> AsyncGenerator[RerankService, None]:
    """创建真实的 RerankService 实例。"""
    service = RerankService()
    yield service
    await service._client.close()


def _copy_fixture_images(target_dir: Path, names: list[str]) -> None:
    """将 fixture 图片复制到目标目录。"""
    for name in names:
        shutil.copy2(FIXTURE_IMAGES_DIR / name, target_dir / name)


async def _build_index(
    work_dirs: dict[str, Path],
    ocr_service: OpenAIOcrService,
    embedding_service: OpenAIEmbeddingService,
    image_names: list[str],
) -> tuple[IndexManager, MetadataStore, VectorStore]:
    """同步索引并返回就绪的 IndexManager 及其底层存储。"""
    _copy_fixture_images(work_dirs["memes_dir"], image_names)

    metadata_store = MetadataStore(str(work_dirs["index_db"]))
    vector_store = VectorStore(str(work_dirs["chroma_dir"]))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    await manager.load()
    await manager.refresh()
    return manager, metadata_store, vector_store


@pytest.mark.asyncio
async def test_match_embedding_only(
    work_dirs: dict[str, Path],
    ocr_service: OpenAIOcrService,
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：仅用 embedding 匹配，返回相似度最高的结果。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    manager, metadata_store, vector_store = await _build_index(
        work_dirs, ocr_service, embedding_service, images
    )

    matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
    )

    query = "没办法了 听天由命"
    query_vector = await embedding_service.embed(query)
    result = await matcher.match_with_vector(query, query_vector)

    print(f"\n描述: {query}")
    print(f"结果: {result}")

    assert result is not None
    assert result.source == "embedding"
    assert "听天由命吧" in result.text
    assert result.similarity > 0


@pytest.mark.asyncio
async def test_match_with_rerank(
    work_dirs: dict[str, Path],
    ocr_service: OpenAIOcrService,
    embedding_service: OpenAIEmbeddingService,
    rerank_service: RerankService,
) -> None:
    """测试：embedding + rerank 精排，返回精排后的结果。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    manager, metadata_store, vector_store = await _build_index(
        work_dirs, ocr_service, embedding_service, images
    )

    matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )

    query = "没办法了 只能认命"
    query_vector = await embedding_service.embed(query)
    result = await matcher.match_with_vector(query, query_vector)

    print(f"\n描述: {query}")
    print(f"结果: {result}")

    assert result is not None
    assert result.source == "rerank"
    assert result.similarity > 0


@pytest.mark.asyncio
async def test_match_empty_description_returns_none(
    work_dirs: dict[str, Path],
    ocr_service: OpenAIOcrService,
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：空描述返回 None。"""
    images = ["听天由命吧.png"]
    manager, metadata_store, vector_store = await _build_index(
        work_dirs, ocr_service, embedding_service, images
    )

    matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
    )

    result = await matcher.match_with_vector("", [1.0])

    print(f"\n空描述结果: {result}")
    assert result is None


@pytest.mark.asyncio
async def test_match_returns_correct_filename(
    work_dirs: dict[str, Path],
    ocr_service: OpenAIOcrService,
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：匹配结果包含正确的文件名。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    manager, metadata_store, vector_store = await _build_index(
        work_dirs, ocr_service, embedding_service, images
    )

    matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
    )

    query = "放弃这个东西"
    query_vector = await embedding_service.embed(query)
    result = await matcher.match_with_vector(query, query_vector)

    print(f"\n描述: {query}")
    print(f"结果: {result}")

    assert result is not None
    assert result.image_path in images
