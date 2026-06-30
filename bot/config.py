"""全局路径常量与配置读取。"""

import os
from pathlib import Path

# bot/ 的上级目录即项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

MEMES_DIR = PROJECT_ROOT / "memes"

# 索引数据目录与文件
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DB_PATH = DATA_DIR / "index.db"
CHROMA_DIR = DATA_DIR / "chroma"


def read_session_timeout() -> int:
    """从环境变量读取会话超时秒数。

    支持格式：
    - 纯数字（秒）：如 "60"
    - HH:MM:SS / DD:HH:MM:SS 等 pydantic 支持的 timedelta 格式

    Returns:
        超时秒数，默认 60。
    """
    from datetime import timedelta

    from pydantic import TypeAdapter

    raw = os.environ.get("SESSION_EXPIRE_TIMEOUT", "")
    if not raw:
        return 60
    # 纯数字（秒）
    try:
        value = int(raw)
        return value if value > 0 else 60
    except ValueError:
        pass
    # pydantic 解析 HH:MM:SS 等格式
    try:
        td = TypeAdapter(timedelta).validate_python(raw)
        total = int(td.total_seconds())
        return total if total > 0 else 60
    except Exception:
        pass
    return 60


# 有效 OCR Provider 值
_VALID_OCR_PROVIDERS: frozenset[str] = frozenset({"deepseek", "paddle"})


def read_ocr_provider() -> str:
    """从环境变量读取 OCR provider 类型。

    Returns:
        "paddle"（默认）或 "deepseek"。
    """
    raw = os.environ.get("OCR_PROVIDER", "paddle").strip().lower()
    return raw if raw in _VALID_OCR_PROVIDERS else "paddle"


__all__ = [
    "PROJECT_ROOT",
    "MEMES_DIR",
    "DATA_DIR",
    "INDEX_DB_PATH",
    "CHROMA_DIR",
    "read_session_timeout",
    "read_ocr_provider",
]
