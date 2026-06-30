"""rerank_service 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.ai_matcher import AIMatchCandidate
from bot.engine.rerank_service import RerankService, _build_candidates_text, _parse_rank


@pytest.fixture
def sample_candidates() -> list[AIMatchCandidate]:
    """创建测试用候选列表。"""
    return [
        AIMatchCandidate(rank=1, entry_id=1, image_path="a.jpg", text="开心", similarity=0.9),
        AIMatchCandidate(rank=2, entry_id=2, image_path="b.jpg", text="难过", similarity=0.8),
        AIMatchCandidate(rank=3, entry_id=3, image_path="c.jpg", text="生气", similarity=0.7),
    ]


@pytest.fixture
def mock_openai():
    """Mock AsyncOpenAI client using unittest.mock.patch."""
    mock_client = AsyncMock()
    with patch("bot.engine.rerank_service.AsyncOpenAI", return_value=mock_client):
        yield mock_client


class TestBuildCandidatesText:
    """_build_candidates_text 测试。"""

    def test_build_candidates_text(self, sample_candidates: list[AIMatchCandidate]) -> None:
        """测试候选列表格式化。"""
        result = _build_candidates_text(sample_candidates)
        expected = "1. 开心\n2. 难过\n3. 生气"
        assert result == expected

    def test_build_candidates_text_empty(self) -> None:
        """测试空候选列表。"""
        result = _build_candidates_text([])
        assert result == ""


class TestParseRank:
    """_parse_rank 测试。"""

    def test_parse_rank_pure_number(self) -> None:
        """测试纯数字解析。"""
        assert _parse_rank("3", max_rank=5) == 3

    def test_parse_rank_number_in_text(self) -> None:
        """测试包含数字的文本解析。"""
        assert _parse_rank("最匹配的是 3", max_rank=5) == 3

    def test_parse_rank_colon_format(self) -> None:
        """测试冒号格式解析。"""
        assert _parse_rank("序号：2", max_rank=5) == 2

    def test_parse_rank_out_of_range(self) -> None:
        """测试越界序号。"""
        assert _parse_rank("15", max_rank=5) is None

    def test_parse_rank_zero(self) -> None:
        """测试零序号。"""
        assert _parse_rank("0", max_rank=5) is None

    def test_parse_rank_invalid_text(self) -> None:
        """测试无效文本。"""
        assert _parse_rank("没有匹配", max_rank=5) is None

    def test_parse_rank_empty_string(self) -> None:
        """测试空字符串。"""
        assert _parse_rank("", max_rank=5) is None

    def test_parse_rank_whitespace(self) -> None:
        """测试空白字符串。"""
        assert _parse_rank("   ", max_rank=5) is None


class TestRerankService:
    """RerankService 测试。"""

    @pytest.mark.asyncio
    async def test_rerank_success(
        self,
        mock_openai: AsyncMock,
        sample_candidates: list[AIMatchCandidate],
    ) -> None:
        """测试精排成功返回。"""
        # Mock API 响应
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "2"
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        service = RerankService(api_key="test-key")
        rank = await service.rerank("开心的表情", sample_candidates)

        assert rank == 2

    @pytest.mark.asyncio
    async def test_rerank_api_failure(
        self,
        mock_openai: AsyncMock,
        sample_candidates: list[AIMatchCandidate],
    ) -> None:
        """测试 API 调用失败。"""
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=Exception("API Error")
        )

        service = RerankService(api_key="test-key")

        with pytest.raises(RuntimeError, match="DeepSeek 精排 API 调用失败"):
            await service.rerank("开心的表情", sample_candidates)

    @pytest.mark.asyncio
    async def test_rerank_unparseable_response(
        self,
        mock_openai: AsyncMock,
        sample_candidates: list[AIMatchCandidate],
    ) -> None:
        """测试无法解析的响应返回 0。"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "没有匹配"
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        service = RerankService(api_key="test-key")
        rank = await service.rerank("开心的表情", sample_candidates)

        assert rank == 0

    @pytest.mark.asyncio
    async def test_rerank_empty_candidates(self) -> None:
        """测试空候选列表抛出 ValueError。"""
        service = RerankService(api_key="test-key")

        with pytest.raises(ValueError, match="候选列表不能为空"):
            await service.rerank("开心的表情", [])
