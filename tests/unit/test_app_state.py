"""app_state 共享实例管理模块单元测试。"""

from typing import Any, Generator
from unittest.mock import MagicMock

import pytest

from bot import app_state


@pytest.fixture(autouse=True)
def _reset_globals() -> Generator[None, Any, None]:
    """每个测试前后重置模块级全局变量。"""
    app_state._index_manager = None
    app_state._metadata_store = None
    app_state._vector_store = None
    app_state._ocr_service = None
    app_state._embedding_service = None
    app_state._image_optimizer = None
    app_state._ai_matcher = None
    app_state._keyword_searcher = None
    yield
    app_state._index_manager = None
    app_state._metadata_store = None
    app_state._vector_store = None
    app_state._ocr_service = None
    app_state._embedding_service = None
    app_state._image_optimizer = None
    app_state._ai_matcher = None
    app_state._keyword_searcher = None


class TestInitApp:
    """init_app() 测试。"""

    def test_sets_all_globals(self) -> None:
        """init_app 应设置所有全局变量。"""
        im = MagicMock()
        md = MagicMock()
        vs = MagicMock()
        ocr = MagicMock()
        emb = MagicMock()
        ai = MagicMock()
        ks = MagicMock()
        app_state.init_app(im, md, vs, ocr, emb, ai_matcher=ai, keyword_searcher=ks)
        assert app_state._index_manager is im
        assert app_state._metadata_store is md
        assert app_state._vector_store is vs
        assert app_state._ocr_service is ocr
        assert app_state._embedding_service is emb
        assert app_state._ai_matcher is ai
        assert app_state._keyword_searcher is ks

    def test_overwrites_existing(self) -> None:
        """重复调用 init_app 应覆盖旧实例。"""
        im1, md1, vs1, ocr1, emb1 = (MagicMock() for _ in range(5))
        im2, md2, vs2, ocr2, emb2 = (MagicMock() for _ in range(5))
        app_state.init_app(im1, md1, vs1, ocr1, emb1)
        app_state.init_app(im2, md2, vs2, ocr2, emb2)
        assert app_state._index_manager is im2
        assert app_state._metadata_store is md2
        assert app_state._vector_store is vs2
        assert app_state._ocr_service is ocr2
        assert app_state._embedding_service is emb2


class TestGetIndexManager:
    """get_index_manager() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 IndexManager 实例。"""
        im = MagicMock()
        app_state.init_app(im, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        assert app_state.get_index_manager() is im

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="IndexManager 尚未初始化"):
            app_state.get_index_manager()


class TestGetOcrService:
    """get_ocr_service() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 OCR 服务实例。"""
        ocr = MagicMock()
        app_state.init_app(MagicMock(), MagicMock(), MagicMock(), ocr, MagicMock())
        assert app_state.get_ocr_service() is ocr

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="OCR 服务尚未初始化"):
            app_state.get_ocr_service()


class TestGetEmbeddingService:
    """get_embedding_service() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 Embedding 服务实例。"""
        emb = MagicMock()
        app_state.init_app(MagicMock(), MagicMock(), MagicMock(), MagicMock(), emb)
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

        ai = MagicMock(spec=AIMatcher)
        app_state.init_app(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), ai_matcher=ai
        )
        assert app_state.get_ai_matcher() is ai

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="AIMatcher 尚未初始化"):
            app_state.get_ai_matcher()


class TestGetKeywordSearcher:
    """get_keyword_searcher() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 KeywordSearcher 实例。"""
        from bot.engine import KeywordSearcher

        ks = MagicMock(spec=KeywordSearcher)
        app_state.init_app(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            keyword_searcher=ks,
        )
        assert app_state.get_keyword_searcher() is ks

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="KeywordSearcher 尚未初始化"):
            app_state.get_keyword_searcher()


class TestGetMetadataStore:
    """get_metadata_store() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 MetadataStore 实例。"""
        from bot.engine import MetadataStore

        md = MagicMock(spec=MetadataStore)
        app_state.init_app(
            MagicMock(), md, MagicMock(), MagicMock(), MagicMock()
        )
        assert app_state.get_metadata_store() is md

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="MetadataStore 尚未初始化"):
            app_state.get_metadata_store()


class TestGetVectorStore:
    """get_vector_store() 测试。"""

    def test_returns_instance(self) -> None:
        """初始化后应返回 VectorStore 实例。"""
        from bot.engine import VectorStore

        vs = MagicMock(spec=VectorStore)
        app_state.init_app(
            MagicMock(), MagicMock(), vs, MagicMock(), MagicMock()
        )
        assert app_state.get_vector_store() is vs

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="VectorStore 尚未初始化"):
            app_state.get_vector_store()
