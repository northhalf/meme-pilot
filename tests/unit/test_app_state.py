"""app_state 共享实例管理模块单元测试。"""

from typing import Any, Generator
from unittest.mock import MagicMock

import pytest

from bot import app_state


@pytest.fixture(autouse=True)
def _reset_globals() -> Generator[None, Any, None]:
    """每个测试前后重置模块级全局变量。"""
    app_state._index_manager = None
    app_state._ocr_service = None
    app_state._embedding_service = None
    app_state._image_optimizer = None
    app_state._ai_matcher = None
    yield
    app_state._index_manager = None
    app_state._ocr_service = None
    app_state._embedding_service = None
    app_state._image_optimizer = None
    app_state._ai_matcher = None


class TestInitApp:
    """init_app() 测试。"""

    def test_sets_all_globals(self) -> None:
        """init_app 应设置所有全局变量。"""
        im = MagicMock()
        ocr = MagicMock()
        emb = MagicMock()
        ai = MagicMock()
        app_state.init_app(im, ocr, emb, ai_matcher=ai)
        assert app_state._index_manager is im
        assert app_state._ocr_service is ocr
        assert app_state._embedding_service is emb
        assert app_state._ai_matcher is ai

    def test_overwrites_existing(self) -> None:
        """重复调用 init_app 应覆盖旧实例。"""
        im1, ocr1, emb1 = MagicMock(), MagicMock(), MagicMock()
        im2, ocr2, emb2 = MagicMock(), MagicMock(), MagicMock()
        app_state.init_app(im1, ocr1, emb1)
        app_state.init_app(im2, ocr2, emb2)
        assert app_state._index_manager is im2
        assert app_state._ocr_service is ocr2
        assert app_state._embedding_service is emb2


class TestGetIndexManager:
    """get_index_manager() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 IndexManager 实例。"""
        im = MagicMock()
        app_state.init_app(im, MagicMock(), MagicMock())
        assert app_state.get_index_manager() is im

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="IndexManager 尚未初始化"):
            app_state.get_index_manager()


class TestGetOcrService:
    """get_ocr_service() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 DeepSeekOcrService 实例。"""
        ocr = MagicMock()
        app_state.init_app(MagicMock(), ocr, MagicMock())
        assert app_state.get_ocr_service() is ocr

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="DeepSeekOcrService 尚未初始化"):
            app_state.get_ocr_service()


class TestGetEmbeddingService:
    """get_embedding_service() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 EmbeddingService 实例。"""
        emb = MagicMock()
        app_state.init_app(MagicMock(), MagicMock(), emb)
        assert app_state.get_embedding_service() is emb

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="EmbeddingService 尚未初始化"):
            app_state.get_embedding_service()


class TestGetAiMatcher:
    """get_ai_matcher() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 AIMatcher 实例。"""
        from bot.engine import AIMatcher

        im = MagicMock()
        ocr = MagicMock()
        emb = MagicMock()
        ai = MagicMock(spec=AIMatcher)
        app_state.init_app(im, ocr, emb, ai_matcher=ai)
        assert app_state.get_ai_matcher() is ai

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="AIMatcher 尚未初始化"):
            app_state.get_ai_matcher()
