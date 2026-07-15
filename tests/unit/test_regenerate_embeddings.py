"""regenerate_embeddings.py 单元测试。

验证脚本读取 ``SELECT id, text, collection_id FROM meme`` 并在调用
``VectorStore.rebuild_all`` 时传入 ``(entry_id, embedding, collection_id)`` 三元组。
"""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.engine.metadata_store import MetadataStore


def _make_embedding(entry_id: int, dimensionality: int = 1024) -> list[float]:
    """生成与 entry_id 对应的确定性向量。"""
    return [float(entry_id)] * dimensionality


def _create_test_db(db_path: Path) -> None:
    """创建包含多条记录的测试数据库。"""
    store = MetadataStore(str(db_path))
    store.load()
    collection = store.create_collection("新三国")
    store.add("a.webp", "文本一", collection_id=0)
    store.add("新三国/b.webp", "文本二", collection_id=collection.id)
    store.add("c.webp", "   ", collection_id=0)
    store.close()


@pytest.fixture
def patched_module(tmp_path: Path) -> Any:
    """导入脚本模块并把配置指向临时目录。"""
    import importlib

    import scripts.regenerate_embeddings as mod

    importlib.reload(mod)
    mod.INDEX_DB_PATH = tmp_path / "index.db"
    mod.CHROMA_DIR = tmp_path / "chroma"
    _create_test_db(mod.INDEX_DB_PATH)
    return mod


@pytest.mark.asyncio
async def test_rebuild_all_receives_collection_id(patched_module: Any) -> None:
    """空文本被跳过，有效文本按 collection_id 传入 rebuild_all。"""
    mod = patched_module
    mock_client = MagicMock()
    mock_model = "test-model"

    async def fake_embed_batch(
        client: Any, model: str, batch: list[tuple[int, str]]
    ) -> list[tuple[int, list[float]]]:
        return [(entry_id, _make_embedding(entry_id)) for entry_id, _ in batch]

    mock_vector_store = MagicMock()
    mock_vector_store.load = MagicMock()
    mock_vector_store.close = MagicMock()
    mock_vector_store.rebuild_all = AsyncMock()

    with (
        patch.object(
            mod, "_create_genai_client", return_value=(mock_client, mock_model)
        ),
        patch.object(mod, "_embed_batch", side_effect=fake_embed_batch),
        patch.object(mod, "VectorStore", return_value=mock_vector_store),
    ):
        await mod._regenerate(batch_size=10, sleep_seconds=0, dry_run=False)

    mock_vector_store.rebuild_all.assert_awaited_once()
    assert mock_vector_store.rebuild_all.await_args is not None
    rebuilt_items = mock_vector_store.rebuild_all.await_args.args[0]
    assert rebuilt_items == [
        (1, _make_embedding(1), 0),
        (2, _make_embedding(2), 1),
    ]


@pytest.mark.asyncio
async def test_dry_run_does_not_call_rebuild_all(patched_module: Any) -> None:
    """dry-run 模式只统计数量，不调用 API 也不写入向量库。"""
    mod = patched_module
    mock_vector_store = MagicMock()
    mock_vector_store.rebuild_all = AsyncMock()

    with (
        patch.object(mod, "_create_genai_client") as mock_create_client,
        patch.object(mod, "VectorStore", return_value=mock_vector_store),
    ):
        await mod._regenerate(batch_size=10, sleep_seconds=0, dry_run=True)

    mock_create_client.assert_not_called()
    mock_vector_store.rebuild_all.assert_not_called()
    mock_vector_store.load.assert_not_called()
