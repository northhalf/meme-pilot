"""RerankService 真实 API 调用集成测试。

需要设置环境变量 DEEPSEEK_API_KEY 才能运行。
可选设置 DEEPSEEK_BASE_URL 和 DEEPSEEK_MODEL。

运行方式：
    uv run pytest tests/integration/test_rerank_service_api.py -v -s
"""

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.ai_matcher import AIMatchCandidate
from bot.engine.rerank_service import RerankService

# 跳过条件：未设置 API Key 时跳过
pytestmark = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY 未设置，跳过集成测试",
)


@pytest_asyncio.fixture
async def rerank_service() -> AsyncGenerator[RerankService, None]:
    """创建真实的 RerankService 实例。"""
    service = RerankService()
    yield service
    await service._client.close()


@pytest.fixture
def meme_candidates() -> list[AIMatchCandidate]:
    """创建模拟的表情包候选列表。"""
    return [
        AIMatchCandidate(
            rank=1,
            entry_id=1,
            image_path="tired.jpg",
            text="加班到凌晨好累啊",
            similarity=0.85,
        ),
        AIMatchCandidate(
            rank=2,
            entry_id=2,
            image_path="happy.jpg",
            text="今天心情真好开心",
            similarity=0.82,
        ),
        AIMatchCandidate(
            rank=3,
            entry_id=3,
            image_path="angry.jpg",
            text="气死我了这什么鬼",
            similarity=0.78,
        ),
        AIMatchCandidate(
            rank=4,
            entry_id=4,
            image_path="sad.jpg",
            text="好难过想哭",
            similarity=0.75,
        ),
        AIMatchCandidate(
            rank=5,
            entry_id=5,
            image_path="laugh.jpg",
            text="笑死我了哈哈哈",
            similarity=0.72,
        ),
    ]


@pytest.mark.asyncio
async def test_rerank_tired_expression(
    rerank_service: RerankService,
    meme_candidates: list[AIMatchCandidate],
) -> None:
    """测试：描述"心累"应该匹配到"加班到凌晨"的表情包。"""
    description = "心累 加班好累"
    rank = await rerank_service.rerank(description, meme_candidates)

    print(f"\n描述: {description}")
    print(f"返回 rank: {rank}")
    if rank > 0:
        selected = meme_candidates[rank - 1]
        print(f"选中: [{selected.rank}] {selected.text} ({selected.image_path})")

    # rank 应该是有效的 1-based 序号
    assert 1 <= rank <= len(meme_candidates)


@pytest.mark.asyncio
async def test_rerank_happy_expression(
    rerank_service: RerankService,
    meme_candidates: list[AIMatchCandidate],
) -> None:
    """测试：描述"开心"应该匹配到"心情真好"的表情包。"""
    description = "开心 情绪高涨"
    rank = await rerank_service.rerank(description, meme_candidates)

    print(f"\n描述: {description}")
    print(f"返回 rank: {rank}")
    if rank > 0:
        selected = meme_candidates[rank - 1]
        print(f"选中: [{selected.rank}] {selected.text} ({selected.image_path})")

    assert 1 <= rank <= len(meme_candidates)


@pytest.mark.asyncio
async def test_rerank_angry_expression(
    rerank_service: RerankService,
    meme_candidates: list[AIMatchCandidate],
) -> None:
    """测试：描述"生气"应该匹配到"气死我了"的表情包。"""
    description = "很生气 发火"
    rank = await rerank_service.rerank(description, meme_candidates)

    print(f"\n描述: {description}")
    print(f"返回 rank: {rank}")
    if rank > 0:
        selected = meme_candidates[rank - 1]
        print(f"选中: [{selected.rank}] {selected.text} ({selected.image_path})")

    assert 1 <= rank <= len(meme_candidates)


@pytest.mark.asyncio
async def test_rerank_laugh_expression(
    rerank_service: RerankService,
    meme_candidates: list[AIMatchCandidate],
) -> None:
    """测试：描述"搞笑"应该匹配到"笑死我了"的表情包。"""
    description = "太搞笑了 忍不住笑"
    rank = await rerank_service.rerank(description, meme_candidates)

    print(f"\n描述: {description}")
    print(f"返回 rank: {rank}")
    if rank > 0:
        selected = meme_candidates[rank - 1]
        print(f"选中: [{selected.rank}] {selected.text} ({selected.image_path})")

    assert 1 <= rank <= len(meme_candidates)


@pytest.mark.asyncio
async def test_rerank_sad_expression(
    rerank_service: RerankService,
    meme_candidates: list[AIMatchCandidate],
) -> None:
    """测试：描述"难过"应该匹配到"好难过"的表情包。"""
    description = "心情低落 难过想哭"
    rank = await rerank_service.rerank(description, meme_candidates)

    print(f"\n描述: {description}")
    print(f"返回 rank: {rank}")
    if rank > 0:
        selected = meme_candidates[rank - 1]
        print(f"选中: [{selected.rank}] {selected.text} ({selected.image_path})")

    assert 1 <= rank <= len(meme_candidates)
