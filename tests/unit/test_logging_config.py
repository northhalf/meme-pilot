"""日志配置模块单元测试。

测试 setup_logging() 函数的行为：
handler 类型、级别、参数、日志写入能力。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from bot.logging_config import setup_logging


@pytest.fixture(autouse=True)
def reset_logging() -> None:
    """每个测试前重置 Root Logger，测试后关闭 handler 并清理 log/ 目录。

    测试前：移除已有 handler，避免测试间状态互相干扰。
    测试后：关闭文件 handler 释放句柄，删除测试生成的 log/ 目录。
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.WARNING)

    yield

    # 测试后关闭本次创建的 handler，释放文件句柄
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.WARNING)

    # 清理测试生成的 log/ 目录
    log_dir = Path("log")
    if log_dir.exists():
        for f in log_dir.iterdir():
            f.unlink()
        log_dir.rmdir()


def _get_handlers_by_type(
    handler_type: type,
) -> list[logging.Handler]:
    """从 Root Logger 中获取指定类型的 handler。

    Args:
        handler_type: 目标 handler 类型。

    Returns:
        匹配类型的 handler 列表。
    """
    return [h for h in logging.getLogger().handlers if isinstance(h, handler_type)]


class TestSetupLogging:
    """setup_logging() 函数单元测试。"""

    def test_creates_log_directory(self) -> None:
        """调用 setup_logging() 后 log/ 目录应存在。"""
        log_dir = Path("log")
        # 确保初始不存在
        if log_dir.exists():
            for f in log_dir.iterdir():
                f.unlink()
            log_dir.rmdir()

        setup_logging()

        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_rotating_file_handler_added(self) -> None:
        """Root Logger 应包含 RotatingFileHandler。"""
        setup_logging()
        handlers = _get_handlers_by_type(RotatingFileHandler)
        assert len(handlers) == 1

    def test_stream_handler_added(self) -> None:
        """Root Logger 应包含 StreamHandler（精确类型，不含子类）。"""
        setup_logging()
        # RotatingFileHandler 是 StreamHandler 的子类，需精确匹配
        handlers = [
            h for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler
        ]
        assert len(handlers) == 1

    def test_handlers_count(self) -> None:
        """Root Logger 恰好有 2 个 handler。"""
        setup_logging()
        assert len(logging.getLogger().handlers) == 2

    def test_root_logger_level_debug(self) -> None:
        """Root Logger level 应为 DEBUG。"""
        setup_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_file_handler_debug_level(self) -> None:
        """FileHandler level 应为 DEBUG。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.level == logging.DEBUG

    def test_stream_handler_info_level(self) -> None:
        """StreamHandler level 应为 INFO。"""
        setup_logging()
        sh = _get_handlers_by_type(logging.StreamHandler)[0]
        assert sh.level == logging.INFO

    def test_file_handler_max_bytes(self) -> None:
        """FileHandler maxBytes 应为 1_048_576 (1 MB)。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.maxBytes == 1_048_576

    def test_file_handler_backup_count(self) -> None:
        """FileHandler backupCount 应为 1。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.backupCount == 1

    def test_file_handler_encoding(self) -> None:
        """FileHandler encoding 应为 utf-8。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.encoding == "utf-8"

    def test_can_write_to_log_file(self) -> None:
        """写入一条 INFO 日志后，bot.log 中应包含目标字符串。"""
        setup_logging()

        test_logger = logging.getLogger("meme_bot_test")
        test_logger.info("测试日志写入")

        # 刷新 handler 确保写入磁盘
        for h in logging.getLogger().handlers:
            h.flush()

        log_file = Path("log") / "bot.log"
        assert log_file.exists()

        content = log_file.read_text(encoding="utf-8")
        assert "测试日志写入" in content

    def test_debug_not_in_stdout_filtered(self) -> None:
        """StreamHandler level 为 INFO，应过滤 DEBUG 消息。"""
        setup_logging()
        sh = _get_handlers_by_type(logging.StreamHandler)[0]
        assert sh.level == logging.INFO
        assert sh.level > logging.DEBUG
