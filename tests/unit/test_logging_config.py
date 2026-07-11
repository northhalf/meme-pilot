"""日志配置模块单元测试。

测试 setup_logging() 函数的行为：
handler 类型、级别、参数、日志写入能力。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Generator

import pytest

from bot.log_context import set_request_id
from bot.logging_config import (
    MAX_LOG_BACKUP_COUNT,
    MAX_LOG_FILE_BYTES,
    setup_logging,
)


@pytest.fixture(autouse=True)
def reset_logging() -> Generator[None, None, None]:
    """每个测试前重置 bot Logger，测试后关闭 handler。

    测试前：移除已有 handler，避免测试间状态互相干扰。
    测试后：关闭文件 handler 释放句柄。
    """
    bot = logging.getLogger("bot")
    for h in bot.handlers[:]:
        bot.removeHandler(h)
        h.close()
    bot.setLevel(logging.NOTSET)
    bot.propagate = True

    yield

    for h in bot.handlers[:]:
        bot.removeHandler(h)
        h.close()
    bot.setLevel(logging.NOTSET)
    bot.propagate = True


def _get_file_handlers() -> list[RotatingFileHandler]:
    """从 bot Logger 中获取 RotatingFileHandler 列表。"""
    return [h for h in logging.getLogger("bot").handlers if isinstance(h, RotatingFileHandler)]


def _get_stream_handlers() -> list[logging.StreamHandler]:
    """从 bot Logger 中获取 StreamHandler 列表（精确类型，不含子类）。"""
    return [h for h in logging.getLogger("bot").handlers if type(h) is logging.StreamHandler]


class TestSetupLogging:
    """setup_logging() 函数单元测试。"""

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        """调用 setup_logging() 后 log 目录应存在。"""
        log_dir = tmp_path / "log"
        setup_logging(log_dir=str(log_dir))
        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_rotating_file_handler_added(self, tmp_path: Path) -> None:
        """bot Logger 应包含 RotatingFileHandler。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        handlers = _get_file_handlers()
        assert len(handlers) == 1

    def test_stream_handler_added(self, tmp_path: Path) -> None:
        """bot Logger 应包含 StreamHandler（精确类型，不含子类）。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        handlers = _get_stream_handlers()
        assert len(handlers) == 1

    def test_handlers_count(self, tmp_path: Path) -> None:
        """bot Logger 恰好有 2 个 handler。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        assert len(logging.getLogger("bot").handlers) == 2

    def test_bot_logger_level_debug(self, tmp_path: Path) -> None:
        """bot Logger level 应为 DEBUG。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        assert logging.getLogger("bot").level == logging.DEBUG

    def test_file_handler_debug_level(self, tmp_path: Path) -> None:
        """RotatingFileHandler level 应为 DEBUG。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.level == logging.DEBUG

    def test_stream_handler_info_level(self, tmp_path: Path) -> None:
        """StreamHandler level 应为 INFO。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        sh = _get_stream_handlers()[0]
        assert sh.level == logging.INFO

    def test_file_handler_max_bytes(self, tmp_path: Path) -> None:
        """RotatingFileHandler maxBytes 应为 10_485_760 (10 MB)。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.maxBytes == MAX_LOG_FILE_BYTES

    def test_file_handler_backup_count(self, tmp_path: Path) -> None:
        """RotatingFileHandler backupCount 应为 3。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.backupCount == MAX_LOG_BACKUP_COUNT

    def test_file_handler_encoding(self, tmp_path: Path) -> None:
        """RotatingFileHandler encoding 应为 utf-8。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.encoding == "utf-8"

    def test_can_write_to_log_file(self, tmp_path: Path) -> None:
        """写入一条 INFO 日志后，bot.log 中应包含目标字符串。"""
        log_dir = tmp_path / "log"
        setup_logging(log_dir=str(log_dir))

        test_logger = logging.getLogger("bot.test")
        test_logger.info("测试日志写入")

        # 刷新 handler 确保写入磁盘
        for h in logging.getLogger("bot").handlers:
            h.flush()

        log_file = log_dir / "bot.log"
        assert log_file.exists()

        content = log_file.read_text(encoding="utf-8")
        assert "测试日志写入" in content

    def test_request_id_prefix_on_child_logger(self, tmp_path: Path) -> None:
        """setup_logging 应使 bot 子 logger 的日志也带 request_id 前缀。"""
        log_dir = tmp_path / "log"
        setup_logging(log_dir=str(log_dir))

        child = logging.getLogger("bot.child")
        with set_request_id("testrid"):
            child.info("子 logger 测试消息")

        # 刷新 handler 确保写入磁盘
        for h in logging.getLogger("bot").handlers:
            h.flush()

        log_file = log_dir / "bot.log"
        content = log_file.read_text(encoding="utf-8")
        assert "[req:testrid]" in content
        assert "子 logger 测试消息" in content

    def test_debug_not_in_stdout_filtered(self, tmp_path: Path) -> None:
        """StreamHandler level 为 INFO，应过滤 DEBUG 消息。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        sh = _get_stream_handlers()[0]
        assert sh.level == logging.INFO
        assert sh.level > logging.DEBUG
