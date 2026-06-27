"""IndexManager 真实 API 调用集成测试。

使用真实 OCR（DeepSeek-OCR）和 Embedding（BAAI/bge-m3）服务，
验证 sync_with_filesystem 的完整流程：OCR → Embedding → 索引写入。

需要设置环境变量：
- SILICONFLOW_API_KEY（OCR 服务）
- EMBEDDING_API_KEY（Embedding 服务，可选，.env 中已配置）

运行方式：
    uv run pytest tests/integration/test_index_manager_api.py -v -s
"""

import os
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.embedding_service import EmbeddingService
from bot.engine.index_manager import IndexManager
from bot.engine.deepseek_ocr import DeepSeekOcrService

# fixture 图片目录
FIXTURE_IMAGES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "images"

# 跳过条件：OCR 和 Embedding 均需要 API Key
pytestmark = pytest.mark.skipif(
    not os.environ.get("SILICONFLOW_API_KEY")
    or not os.environ.get("EMBEDDING_API_KEY"),
    reason="SILICONFLOW_API_KEY 或 EMBEDDING_API_KEY 未设置，跳过集成测试",
)


@pytest.fixture
def work_dirs(tmp_path: Path) -> dict[str, Path]:
    """创建隔离的工作目录结构。

    Returns:
        包含 data_dir、memes_dir、no_text_dir 的字典。
    """
    data_dir = tmp_path / "data"
    memes_dir = tmp_path / "memes"
    no_text_dir = tmp_path / "meme_no_text"
    data_dir.mkdir()
    memes_dir.mkdir()
    return {"data_dir": data_dir, "memes_dir": memes_dir, "no_text_dir": no_text_dir}


@pytest_asyncio.fixture
async def ocr_service() -> AsyncGenerator[DeepSeekOcrService, None]:
    """创建真实的 DeepSeekOcrService 实例。"""
    service = DeepSeekOcrService()
    yield service
    await service._client.close()


@pytest_asyncio.fixture
async def embedding_service() -> AsyncGenerator[EmbeddingService, None]:
    """创建真实的 EmbeddingService 实例。"""
    service = EmbeddingService()
    yield service
    await service._client.close()


def _copy_fixture_images(target_dir: Path, names: list[str]) -> None:
    """将 fixture 图片复制到目标目录。"""
    for name in names:
        src = FIXTURE_IMAGES_DIR / name
        shutil.copy2(src, target_dir / name)


@pytest.mark.asyncio
async def test_sync_single_image(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：同步单张图片，验证 OCR 文本和 embedding 写入索引。"""
    _copy_fixture_images(work_dirs["memes_dir"], ["听天由命吧.png"])

    manager = IndexManager(
        data_dir=str(work_dirs["data_dir"]),
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()

    result = await manager.sync_with_filesystem()

    print(f"\n新增: {result.added}, 删除: {result.deleted}")
    print(f"去重: {result.deduped}, 无文字移走: {result.no_text_moved}")
    print(f"失败: {result.failed}")

    assert result.added == 1
    assert result.deleted == 0
    assert result.failed == []
    assert manager.entry_count == 1

    # 验证索引内容
    entries = manager.get_entries()
    entry = list(entries.values())[0]
    assert "听天由命吧" in entry["text"]
    assert entry["filename"] == "听天由命吧.png"

    # 验证 embedding 存在且维度正确
    embeddings = manager.get_embeddings()
    assert len(embeddings) == 1
    emb = list(embeddings.values())[0]
    vector = emb["embedding"]
    assert isinstance(vector, list)
    assert len(vector) == 1024


@pytest.mark.asyncio
async def test_sync_multiple_images(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：同步多张图片，验证全部进入索引。"""
    images = [
        "听天由命吧.png",
        "不能用就弃之.png",
    ]
    _copy_fixture_images(work_dirs["memes_dir"], images)

    manager = IndexManager(
        data_dir=str(work_dirs["data_dir"]),
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()

    result = await manager.sync_with_filesystem()

    print(f"\n新增: {result.added}, 失败: {result.failed}")
    for eid, entry in manager.get_entries().items():
        print(f"  [{eid}] {entry['filename']}: {entry['text'][:40]}...")

    assert result.added == 2
    assert result.failed == []
    assert manager.entry_count == 2


@pytest.mark.asyncio
async def test_sync_delete_removed_image(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：删除图片后再次同步，索引记录应被移除。"""
    images = ["听天由命吧.png", "不能用就弃之.png"]
    _copy_fixture_images(work_dirs["memes_dir"], images)

    manager = IndexManager(
        data_dir=str(work_dirs["data_dir"]),
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()

    # 首次同步
    await manager.sync_with_filesystem()
    assert manager.entry_count == 2

    # 删除一张图片
    (work_dirs["memes_dir"] / "不能用就弃之.png").unlink()

    # 再次同步
    result = await manager.sync_with_filesystem()

    print(f"\n新增: {result.added}, 删除: {result.deleted}")
    assert result.deleted == 1
    assert manager.entry_count == 1

    # 剩余的应该是听天由命吧
    entries = manager.get_entries()
    remaining = list(entries.values())[0]
    assert remaining["filename"] == "听天由命吧.png"


@pytest.mark.asyncio
async def test_sync_idempotent(
    work_dirs: dict[str, Path],
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
) -> None:
    """测试：重复同步不会重复添加已有记录。"""
    _copy_fixture_images(work_dirs["memes_dir"], ["听天由命吧.png"])

    manager = IndexManager(
        data_dir=str(work_dirs["data_dir"]),
        memes_dir=str(work_dirs["memes_dir"]),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        no_text_dir=str(work_dirs["no_text_dir"]),
    )
    manager.load()

    # 首次同步
    result1 = await manager.sync_with_filesystem()
    assert result1.added == 1
    assert manager.entry_count == 1

    # 再次同步（不增不减）
    result2 = await manager.sync_with_filesystem()

    print(f"\n第二次同步: 新增={result2.added}, 删除={result2.deleted}")
    assert result2.added == 0
    assert result2.deleted == 0
    assert manager.entry_count == 1
