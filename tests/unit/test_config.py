"""bot.config 全局路径常量与配置读取测试。"""

from pathlib import Path

from bot.config import CHROMA_DIR, INDEX_DB_PATH, MEMES_DIR, PROJECT_ROOT


def test_index_db_path_under_data() -> None:
    """INDEX_DB_PATH 位于 <项目根>/data/index.db。"""
    assert INDEX_DB_PATH == PROJECT_ROOT / "data" / "index.db"


def test_chroma_dir_under_data() -> None:
    """CHROMA_DIR 位于 <项目根>/data/chroma。"""
    assert CHROMA_DIR == PROJECT_ROOT / "data" / "chroma"
