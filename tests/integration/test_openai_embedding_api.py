"""OpenAIEmbeddingService 真实 API 调用集成测试。

需要设置环境变量 OPENAI_EMBEDDING_API_KEY 才能运行。
可选设置 OPENAI_EMBEDDING_BASE_URL 和 OPENAI_EMBEDDING_MODEL。

运行方式：
    uv run pytest tests/integration/test_openai_embedding_api.py -v -s
"""

import math
import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from bot.engine.openai_embedding import OpenAIEmbeddingService  # noqa: E402

# 跳过条件：未设置 API Key 时跳过
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_EMBEDDING_API_KEY"),
    reason="OPENAI_EMBEDDING_API_KEY 未设置，跳过集成测试",
)


@pytest_asyncio.fixture
async def embedding_service() -> AsyncGenerator[OpenAIEmbeddingService, None]:
    """创建真实的 OpenAIEmbeddingService 实例。"""
    service = OpenAIEmbeddingService()
    yield service
    await service.close()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm)


@pytest.mark.asyncio
async def test_embed_returns_vector(
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：embed 返回非空浮点数列表。"""
    result = await embedding_service.embed("测试文本")

    print(f"\n向量维度: {len(result)}")
    print(f"前 5 维: {result[:5]}")

    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(v, float) for v in result)


@pytest.mark.asyncio
async def test_embed_dimension_1024(
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：默认模型输出 1024 维向量。"""
    result = await embedding_service.embed("你好世界")

    print(f"\n向量维度: {len(result)}")

    assert len(result) == 1024


@pytest.mark.asyncio
async def test_embed_different_texts_different_vectors(
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：不同文本生成不同向量。"""
    vec_a = await embedding_service.embed("今天天气真好")
    vec_b = await embedding_service.embed("这个表情包太搞笑了")

    similarity = _cosine_similarity(vec_a, vec_b)

    print(f"\n向量 A 维度: {len(vec_a)}")
    print(f"向量 B 维度: {len(vec_b)}")
    print(f"余弦相似度: {similarity:.4f}")

    # 不同语义的文本相似度不应接近 1
    assert similarity < 0.99


@pytest.mark.asyncio
async def test_embed_similar_texts_high_similarity(
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：语义相似的文本余弦相似度高于不相似文本。"""
    vec_tired_1 = await embedding_service.embed("加班好累 心累")
    vec_tired_2 = await embedding_service.embed("工作太辛苦了 疲惫")
    vec_happy = await embedding_service.embed("今天心情真开心")

    sim_similar = _cosine_similarity(vec_tired_1, vec_tired_2)
    sim_different = _cosine_similarity(vec_tired_1, vec_happy)

    print(f"\n相似文本相似度: {sim_similar:.4f}")
    print(f"不同文本相似度: {sim_different:.4f}")

    assert sim_similar > sim_different


@pytest.mark.asyncio
async def test_embed_deterministic(
    embedding_service: OpenAIEmbeddingService,
) -> None:
    """测试：相同文本多次调用返回相同向量。"""
    text = "听天由命吧"
    vec_1 = await embedding_service.embed(text)
    vec_2 = await embedding_service.embed(text)

    similarity = _cosine_similarity(vec_1, vec_2)

    print(f"\n两次调用余弦相似度: {similarity:.10f}")

    # 相同文本应返回完全一致的向量
    assert similarity > 0.9999
