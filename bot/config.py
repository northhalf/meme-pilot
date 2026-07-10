"""全局路径常量与配置读取。"""

import os
from pathlib import Path

# bot/ 的上级目录即项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

MEMES_DIR = PROJECT_ROOT / "memes"
MEMES_DELETED_DIR: Path = PROJECT_ROOT / "memes_deleted"
"""被删除表情包的备份目录（可从该目录手动恢复）。"""

MEMES_REPLACED_DIR: Path = PROJECT_ROOT / "memes_replaced"
"""被替换表情包的归档目录。"""

# 索引数据目录与文件
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DB_PATH = DATA_DIR / "index.db"
CHROMA_DIR = DATA_DIR / "chroma"


def read_bot_port() -> int:
    """从环境变量读取 Bot 监听端口，无效值回退为 8080。

    Returns:
        Bot 监听端口号。
    """
    raw = os.environ.get("BOT_PORT", "8080")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 8080


def read_int_env(key: str) -> int | None:
    """从环境变量读取可选整数值。

    读取指定环境变量的值并转为 int，无效值或缺失时返回 None。
    Service 收到 None 后会回退到自身的默认值（通常是 5）。

    Returns:
        有效正整数或 None。
    """
    raw = os.environ.get(key)
    if not raw:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return None


def read_session_timeout() -> int:
    """从环境变量读取会话超时秒数。

    支持格式：
    - 纯数字（秒）：如 "60"
    - HH:MM:SS / DD:HH:MM:SS 等 pydantic 支持的 timedelta 格式

    Returns:
        超时秒数，默认 60。
    """
    return _parse_timeout_seconds(os.environ.get("SESSION_EXPIRE_TIMEOUT", ""), 60)


# 有效 OCR Provider 值
_VALID_OCR_PROVIDERS: frozenset[str] = frozenset({"deepseek", "paddle", "rapidocr"})

# 有效 Embedding Provider 值
_VALID_EMBEDDING_PROVIDERS: frozenset[str] = frozenset({"openai", "google"})


def _parse_timeout_seconds(raw: str, default: int) -> int:
    """解析超时秒数，支持纯数字或 timedelta 格式。

    Args:
        raw: 环境变量原始值。
        default: 解析失败时的默认值。

    Returns:
        正整数秒数。
    """
    from datetime import timedelta

    from pydantic import TypeAdapter

    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        pass
    try:
        td = TypeAdapter(timedelta).validate_python(raw)
        total = int(td.total_seconds())
        return total if total > 0 else default
    except Exception:
        pass
    return default


def read_read_lock_timeout() -> int:
    """从环境变量读取读锁等待超时秒数。

    Returns:
        超时秒数，默认 30。
    """
    return _parse_timeout_seconds(os.environ.get("READ_LOCK_TIMEOUT", ""), 30)


def read_add_command_timeout() -> int:
    """从环境变量读取 /add 命令用户等待超时秒数。

    Returns:
        超时秒数，默认 60。
    """
    return _parse_timeout_seconds(os.environ.get("ADD_COMMAND_TIMEOUT", ""), 60)


def read_ocr_provider() -> str:
    """从环境变量读取 OCR provider 类型。

    Returns:
        "rapidocr"（默认）、"paddle" 或 "deepseek"。
    """
    raw = os.environ.get("OCR_PROVIDER", "rapidocr").strip().lower()
    return raw if raw in _VALID_OCR_PROVIDERS else "rapidocr"


def read_embedding_provider() -> str:
    """从环境变量读取 Embedding provider 类型。

    Returns:
        "openai"（默认）或 "google"。
    """
    raw = os.environ.get("EMBEDDING_PROVIDER", "openai").strip().lower()
    return raw if raw in _VALID_EMBEDDING_PROVIDERS else "openai"


# 默认 OCR 文本置信度阈值
_DEFAULT_OCR_TEXT_SCORE = 0.9


def read_ocr_text_score() -> float:
    """从环境变量读取 OCR 文本置信度阈值。

    PaddleOCR 与 RapidOCR 共用此阈值。

    Returns:
        阈值浮点数，默认 0.9；解析失败或越界时回退为 0.9。
    """
    raw = os.environ.get("OCR_TEXT_SCORE", "")
    if not raw:
        return _DEFAULT_OCR_TEXT_SCORE
    try:
        value = float(raw)
        return value if 0.0 <= value <= 1.0 else _DEFAULT_OCR_TEXT_SCORE
    except ValueError:
        return _DEFAULT_OCR_TEXT_SCORE


def read_convert_to_webp() -> bool:
    """从环境变量读取是否将新增图片转为 WebP。

    开关开启时（默认）新增图片转有损 WebP；关闭时按传输格式存储（现状）。
    "false"/"0"/"no" 返回 False，其余无效值回退 True（默认开启）。

    Returns:
        bool:是否转 WebP。
    """
    raw = os.environ.get("CONVERT_TO_WEBP", "true").strip().lower()
    if raw in ("false", "0", "no"):
        return False
    return True


__all__ = [
    "PROJECT_ROOT",
    "MEMES_DIR",
    "MEMES_DELETED_DIR",
    "MEMES_REPLACED_DIR",
    "DATA_DIR",
    "INDEX_DB_PATH",
    "CHROMA_DIR",
    "read_session_timeout",
    "read_read_lock_timeout",
    "read_add_command_timeout",
    "read_ocr_provider",
    "read_embedding_provider",
    "read_ocr_text_score",
    "read_convert_to_webp",
]
