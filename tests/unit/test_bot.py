"""bot.bot 模块单元测试。"""

import os

import pytest

from bot.bot import _read_bot_port, _read_sync_concurrency


class TestReadSyncConcurrency:
    """_read_sync_concurrency() 测试。"""

    def test_returns_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置 SYNC_CONCURRENCY 时返回 None。"""
        monkeypatch.delenv("SYNC_CONCURRENCY", raising=False)
        assert _read_sync_concurrency() is None

    def test_returns_none_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNC_CONCURRENCY 为空字符串时返回 None。"""
        monkeypatch.setenv("SYNC_CONCURRENCY", "")
        assert _read_sync_concurrency() is None

    def test_returns_value_when_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNC_CONCURRENCY 为有效正整数时返回该值。"""
        monkeypatch.setenv("SYNC_CONCURRENCY", "10")
        assert _read_sync_concurrency() == 10

    def test_returns_none_when_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNC_CONCURRENCY 为 0 时返回 None。"""
        monkeypatch.setenv("SYNC_CONCURRENCY", "0")
        assert _read_sync_concurrency() is None

    def test_returns_none_when_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNC_CONCURRENCY 为负数时返回 None。"""
        monkeypatch.setenv("SYNC_CONCURRENCY", "-3")
        assert _read_sync_concurrency() is None

    def test_returns_none_when_not_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNC_CONCURRENCY 非整数时返回 None。"""
        monkeypatch.setenv("SYNC_CONCURRENCY", "abc")
        assert _read_sync_concurrency() is None


class TestReadBotPort:
    """_read_bot_port() 测试。"""

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置 BOT_PORT 时返回默认值 8080。"""
        monkeypatch.delenv("BOT_PORT", raising=False)
        assert _read_bot_port() == 8080

    def test_returns_value_when_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 为有效整数时返回该值。"""
        monkeypatch.setenv("BOT_PORT", "9090")
        assert _read_bot_port() == 9090

    def test_returns_default_when_not_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 非整数时回退为默认值 8080。"""
        monkeypatch.setenv("BOT_PORT", "abc")
        assert _read_bot_port() == 8080

    def test_returns_default_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 为空字符串时回退为默认值 8080。"""
        monkeypatch.setenv("BOT_PORT", "")
        assert _read_bot_port() == 8080

    def test_returns_default_when_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BOT_PORT 为浮点数字符串时回退为默认值 8080。"""
        monkeypatch.setenv("BOT_PORT", "80.80")
        assert _read_bot_port() == 8080
