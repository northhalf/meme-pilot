"""pytest 共享 fixture。"""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_sqlite_path(tmp_path: Path) -> Path:
    """返回一个不存在的 sqlite 数据库文件路径（在 tmp_path 下）。

    MetadataStore.load() 会自动创建该文件与目录。
    """
    return tmp_path / "index.db"


@pytest.fixture
def tmp_chroma_dir(tmp_path: Path) -> Path:
    """返回一个 chroma PersistentClient 目录路径（在 tmp_path 下）。

    VectorStore.load() 会自动创建该目录。
    """
    return tmp_path / "chroma"
