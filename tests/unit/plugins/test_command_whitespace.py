"""命令空白边界机制测试 — 锁定 /dal 误匹配 /d 的修复机制。

根因：NoneBot2 命令匹配基于全局 TrieRule 最长前缀。消息 "/dal 123" 会命中
已注册的 "/d" 前缀（命令解析为 ("d",)，参数为 "al 123"）；CommandRule 默认
force_whitespace=None 时只要命令命中即放行，于是 /dal 误触发 /d。

项目约定：所有 on_command 注册传 force_whitespace=True —— 命令后带参数时
必须以空白分隔，裸命令不受影响。本文件按 command("del", aliases={"d"}) 的
真实注册内容手工构建 Trie，直接驱动框架的 TrieRule + CommandRule，
锁定该机制的匹配语义，防止 nonebot 升级后语义漂移导致回归。
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.consts import CMD_ARG_KEY, CMD_KEY, CMD_WHITESPACE_KEY, PREFIX_KEY
from nonebot.rule import TRIE_VALUE, CommandRule, TrieRule
from pygtrie import CharTrie

_DEL_CMDS: list[tuple[str, ...]] = [("del",), ("d",)]


@pytest.fixture
def del_trie() -> Generator[None, Any, None]:
    """在隔离 Trie 中注册 /del 与别名 /d，测试结束后恢复原全局 Trie。

    Yields:
        无。
    """
    original = TrieRule.prefix
    TrieRule.prefix = CharTrie()
    TrieRule.add_prefix("/del", TRIE_VALUE("/", ("del",)))
    TrieRule.add_prefix("/d", TRIE_VALUE("/", ("d",)))
    yield
    TrieRule.prefix = original


def _make_event(message: Message) -> MagicMock:
    """创建模拟的 MessageEvent，携带真实 OneBot Message。

    Args:
        message: 真实构造的 OneBot 消息。

    Returns:
        get_type/get_message 可注入 TrieRule 解析流程的模拟事件。
    """
    event = MagicMock()
    event.get_type.return_value = "message"
    event.get_message.return_value = message
    return event


async def _matches(message: Message, force_whitespace: bool | None) -> bool:
    """驱动真实 TrieRule 解析与 CommandRule 判定，返回 /del 命令是否命中。

    Args:
        message: 待判定的用户消息。
        force_whitespace: 传给 CommandRule 的空白边界配置。

    Returns:
        True 表示命令命中，False 表示不命中。
    """
    state: dict[Any, Any] = {}
    TrieRule.get_value(MagicMock(), _make_event(message), state)
    prefix = state[PREFIX_KEY]
    rule = CommandRule(_DEL_CMDS, force_whitespace=force_whitespace)
    return await rule(
        cmd=prefix[CMD_KEY],
        cmd_arg=prefix[CMD_ARG_KEY],
        cmd_whitespace=prefix[CMD_WHITESPACE_KEY],
    )


@pytest.mark.usefixtures("del_trie")
class TestWhitespaceBoundary:
    """force_whitespace=True 下的命令匹配边界。"""

    @pytest.mark.asyncio
    async def test_bare_alias_matches(self) -> None:
        """裸命令 /d（无参数）应命中。"""
        assert await _matches(Message("/d"), force_whitespace=True) is True

    @pytest.mark.asyncio
    async def test_bare_full_command_matches(self) -> None:
        """裸命令 /del（无参数）应命中。"""
        assert await _matches(Message("/del"), force_whitespace=True) is True

    @pytest.mark.asyncio
    async def test_alias_with_space_args_matches(self) -> None:
        """/d 123（空白分隔参数）应命中。"""
        assert await _matches(Message("/d 123"), force_whitespace=True) is True

    @pytest.mark.asyncio
    async def test_full_command_with_space_args_matches(self) -> None:
        """/del 123（空白分隔参数）应命中。"""
        assert await _matches(Message("/del 123"), force_whitespace=True) is True

    @pytest.mark.asyncio
    async def test_full_width_space_matches(self) -> None:
        """/d　123（全角空格分隔）应命中。"""
        assert await _matches(Message("/d　123"), force_whitespace=True) is True

    @pytest.mark.asyncio
    async def test_prefixed_text_does_not_match(self) -> None:
        """/dal 123 不应命中 /d —— 用户报告的误匹配场景。"""
        assert await _matches(Message("/dal 123"), force_whitespace=True) is False

    @pytest.mark.asyncio
    async def test_joined_args_do_not_match(self) -> None:
        """/d123（命令与参数间无空白）不应命中。"""
        assert await _matches(Message("/d123"), force_whitespace=True) is False

    @pytest.mark.asyncio
    async def test_image_without_space_does_not_match(self) -> None:
        """/d 后紧跟图片段（无空白）不应命中。"""
        message = Message("/d") + MessageSegment.image("http://example.com/1.jpg")
        assert await _matches(message, force_whitespace=True) is False


@pytest.mark.usefixtures("del_trie")
class TestDefaultRuleReproducesBug:
    """框架默认 force_whitespace=None 的行为，复现本 bug 的根因。"""

    @pytest.mark.asyncio
    async def test_default_rule_matches_prefixed_text(self) -> None:
        """默认规则下 /dal 123 会误命中 /d —— 即本次修复的 bug。"""
        assert await _matches(Message("/dal 123"), force_whitespace=None) is True
