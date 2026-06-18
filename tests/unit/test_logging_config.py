"""日志配置模块单元测试。

测试 setup_logging() 函数的行为：
handler 类型、级别、参数、日志写入能力。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Generator

import pytest

from bot.logging_config import setup_logging


@pytest.fixture(autouse=True)
def reset_logging() -> Generator[None, None, None]:
    """每个测试前重置 Root Logger，测试后关闭 handler。

    测试前：移除已有 handler，避免测试间状态互相干扰。
    测试后：关闭文件 handler 释放句柄。
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.WARNING)

    yield

    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.WARNING)


def _get_file_handlers() -> list[RotatingFileHandler]:
    """从 Root Logger 中获取 RotatingFileHandler 列表。"""
    return [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]


def _get_stream_handlers() -> list[logging.StreamHandler]:
    """从 Root Logger 中获取 StreamHandler 列表（精确类型，不含子类）。"""
    return [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]


class TestSetupLogging:
    """setup_logging() 函数单元测试。"""

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        """调用 setup_logging() 后 log 目录应存在。"""
        log_dir = tmp_path / "log"
        setup_logging(log_dir=str(log_dir))
        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_rotating_file_handler_added(self, tmp_path: Path) -> None:
        """Root Logger 应包含 RotatingFileHandler。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        handlers = _get_file_handlers()
        assert len(handlers) == 1

    def test_stream_handler_added(self, tmp_path: Path) -> None:
        """Root Logger 应包含 StreamHandler（精确类型，不含子类）。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        handlers = _get_stream_handlers()
        assert len(handlers) == 1

    def test_handlers_count(self, tmp_path: Path) -> None:
        """Root Logger 恰好有 2 个 handler。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        assert len(logging.getLogger().handlers) == 2

    def test_root_logger_level_debug(self, tmp_path: Path) -> None:
        """Root Logger level 应为 DEBUG。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        assert logging.getLogger().level == logging.DEBUG

    def test_file_handler_debug_level(self, tmp_path: Path) -> None:
        """FileHandler level 应为 DEBUG。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.level == logging.DEBUG

    def test_stream_handler_info_level(self, tmp_path: Path) -> None:
        """StreamHandler level 应为 INFO。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        sh = _get_stream_handlers()[0]
        assert sh.level == logging.INFO

    def test_file_handler_max_bytes(self, tmp_path: Path) -> None:
        """FileHandler maxBytes 应为 1_048_576 (1 MB)。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.maxBytes == 1_048_576

    def test_file_handler_backup_count(self, tmp_path: Path) -> None:
        """FileHandler backupCount 应为 1。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.backupCount == 1

    def test_file_handler_encoding(self, tmp_path: Path) -> None:
        """FileHandler encoding 应为 utf-8。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        fh = _get_file_handlers()[0]
        assert fh.encoding == "utf-8"

    def test_can_write_to_log_file(self, tmp_path: Path) -> None:
        """写入一条 INFO 日志后，bot.log 中应包含目标字符串。"""
        log_dir = tmp_path / "log"
        setup_logging(log_dir=str(log_dir))

        test_logger = logging.getLogger("meme_bot_test")
        test_logger.info("测试日志写入")

        # 刷新 handler 确保写入磁盘
        for h in logging.getLogger().handlers:
            h.flush()

        log_file = log_dir / "bot.log"
        assert log_file.exists()

        content = log_file.read_text(encoding="utf-8")
        assert "测试日志写入" in content

    def test_debug_not_in_stdout_filtered(self, tmp_path: Path) -> None:
        """StreamHandler level 为 INFO，应过滤 DEBUG 消息。"""
        setup_logging(log_dir=str(tmp_path / "log"))
        sh = _get_stream_handlers()[0]
        assert sh.level == logging.INFO
        assert sh.level > logging.DEBUG
