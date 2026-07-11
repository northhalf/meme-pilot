"""日志配置模块。

通过 setup_logging() 配置机器人日志：
- 只配置最顶层的 "bot" logger，子 logger 通过继承关系获取配置
- RotatingFileHandler：写入 log/bot.log，DEBUG 级别，单文件 <= 10 MB，保留 3 个备份
- StreamHandler：输出到 stdout，INFO 级别
- 第三方库（uvicorn、websockets 等）的日志不影响 bot.log
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.log_context import RequestIdFilter

# 日志文件滚动配置
MAX_LOG_FILE_BYTES: int = 10_485_760  # 10 MB
MAX_LOG_BACKUP_COUNT: int = 3


def setup_logging(log_dir: str = "log") -> None:
    """配置 bot 日志滚动机制。

    只配置最顶层的 "bot" logger，子 logger（bot.plugins.*、bot.engine.* 等）
    通过继承关系自动获取配置。不修改根 logger，不影响第三方库日志。

    日志同时输出到：
    - stdout（INFO 级别及以上）
    - <log_dir>/bot.log（DEBUG 级别及以上，单文件 <= 10 MB，保留 3 个备份）

    Args:
        log_dir: 日志目录路径，默认 "log"。
    """
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)

    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT)

    file_handler = RotatingFileHandler(
        _log_dir / "bot.log",
        maxBytes=MAX_LOG_FILE_BYTES,
        backupCount=MAX_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)

    # 只配置最顶层的 bot logger，不修改根 logger
    bot_logger = logging.getLogger("bot")
    bot_logger.setLevel(logging.DEBUG)
    bot_logger.addHandler(file_handler)
    bot_logger.addHandler(stream_handler)
    # 注入 request_id 前缀 filter（只在顶层 bot logger 注册一次）
    bot_logger.addFilter(RequestIdFilter())
    bot_logger.propagate = False
