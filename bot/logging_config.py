"""日志配置模块。

通过 setup_logging() 配置全局日志：
- RotatingFileHandler：写入 log/bot.log，DEBUG 级别，单文件 <= 1MB，保留 1 个备份。
- StreamHandler：输出到 stdout，INFO 级别。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: str = "log") -> None:
    """配置全局日志滚动机制。

    日志同时输出到：
    - stdout（INFO 级别及以上）
    - <log_dir>/bot.log（DEBUG 级别及以上，单文件 <= 1MB，保留 1 个备份）

    Args:
        log_dir: 日志目录路径，默认 "log"。

    日志格式：时间 - 模块名 - 级别 - 消息
    """
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)

    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"

    file_handler = RotatingFileHandler(
        _log_dir / "bot.log",
        maxBytes=1_048_576,
        backupCount=1,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))

    logging.basicConfig(
        level=logging.DEBUG,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FMT,
        handlers=[stream_handler, file_handler],
        force=True,
    )
