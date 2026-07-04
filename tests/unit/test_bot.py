"""bot.bot 模块单元测试。"""

import os

import pytest

from bot.config import read_bot_port, read_int_env


class TestReadIntEnv:
    """read_int_env() 测试。"""

    def test_returns_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量未设置时返回 None。"""
        monkeypatch.delenv("TEST_CONCURRENCY", raising=False)
        assert read_int_env("TEST_CONCURRENCY", 5) is None

    def test_returns_none_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量为空字符串时返回 None。"""
        monkeypatch.setenv("TEST_CONCURRENCY", "")
        assert read_int_env("TEST_CONCURRENCY", 5) is None

    def test_returns_value_when_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量为有效正整数时返回该值。"""
        monkeypatch.setenv("TEST_CONCURRENCY", "10")
        assert read_int_env("TEST_CONCURRENCY", 5) == 10

    def test_returns_none_when_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量为 0 时返回 None。"""
        monkeypatch.setenv("TEST_CONCURRENCY", "0")
        assert read_int_env("TEST_CONCURRENCY", 5) is None

    def test_returns_none_when_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量为负数时返回 None。"""
        monkeypatch.setenv("TEST_CONCURRENCY", "-3")
        assert read_int_env("TEST_CONCURRENCY", 5) is None

    def test_returns_none_when_not_integer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量非整数时返回 None。"""
        monkeypatch.setenv("TEST_CONCURRENCY", "abc")
        assert read_int_env("TEST_CONCURRENCY", 5) is None


class TestReadBotPort:
    """read_bot_port() 测试。"""

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置 BOT_PORT 时返回默认值 8080。"""
        monkeypatch.delenv("BOT_PORT", raising=False)
        assert read_bot_port() == 8080

    def test_returns_value_when_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 为有效整数时返回该值。"""
        monkeypatch.setenv("BOT_PORT", "9090")
        assert read_bot_port() == 9090

    def test_returns_default_when_not_integer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BOT_PORT 非整数时回退为默认值 8080。"""
        monkeypatch.setenv("BOT_PORT", "abc")
        assert read_bot_port() == 8080

    def test_returns_default_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 为空字符串时回退为默认值 8080。"""
        monkeypatch.setenv("BOT_PORT", "")
        assert read_bot_port() == 8080

    def test_returns_default_when_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 为浮点数字符串时回退为默认值 8080。"""
        monkeypatch.setenv("BOT_PORT", "80.80")
        assert read_bot_port() == 8080
