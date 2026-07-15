"""/help 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _assert_has_reply, _assert_no_reply, extract_message_text

# ---------------------------------------------------------------------------
# 在导入插件前 mock nonebot.on_command，
# 避免需要 NoneBot2 完整初始化。
# ---------------------------------------------------------------------------

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn  # 透传 decorator

with patch("nonebot.on_command", return_value=_mock_cmd):
    from bot.plugins import help
    from bot.plugins.help import handle_help


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_event(user_id: str = "12345", message_type: str = "private") -> MagicMock:
    """创建模拟的 MessageEvent。"""
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = message_type
    if message_type == "group":
        event.message_id = 123456
    return event


def _make_bot() -> MagicMock:
    """创建模拟的 Bot。"""
    bot = MagicMock()
    bot.send = AsyncMock()
    return bot


def _make_matcher() -> MagicMock:
    """创建模拟的 Matcher。"""
    matcher = MagicMock()
    matcher.finish = AsyncMock()
    return matcher


def _reset_mocks() -> None:
    """重置 mock matcher 的 finish 为新的 AsyncMock。"""
    _mock_cmd.finish = AsyncMock()


# ---------------------------------------------------------------------------
# /help 命令测试
# ---------------------------------------------------------------------------


class TestHandleHelp:
    """/help 命令测试。"""

    @pytest.mark.asyncio
    @patch.object(help, "is_authorized", return_value=True)
    async def test_authorized_user_receives_help(self, mock_auth: MagicMock) -> None:
        """授权用户应收到帮助文本。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_help(_make_bot(), _make_event("111"), matcher)

        matcher.finish.assert_awaited_once()
        call_args = matcher.finish.call_args[0][0]
        text = extract_message_text(call_args)
        assert text.splitlines() == [
            "/help (/h)：查看命令帮助",
            "直接发送关键词：按关键词检索表情包（结果过多时支持翻页）",
            "/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足；结果过多时支持翻页）",
            "/rand [关键词]：随机给出 10 个表情包，回复 0 换一批",
            "/sim <描述文本>：按语义相似度给出前 10 个表情包（结果过多时支持翻页）",
            "/ai <自然语言描述>：按自然语言描述匹配表情包",
            "/add [speaker <tags...>] (/a)：通过聊天添加一张表情包",
            "/addtag <id> <tag>... (/at)：为指定表情包添加标签",
            "/del <id>... (/d)：删除指定表情包（需确认）",
            "/edittext <id> <新文本> (/e)：修改指定表情包的 OCR 文本",
            "/setspeaker <id> [说话人] (/sp)：设置或清空表情包的说话人",
            "/switch [合集编号|名称]：查看或切换表情包合集",
            "/move <id> <目标合集编号|名称> (/mv)：移动表情包（需确认）",
            "/refresh (/r)：扫描 memes/ 并增量更新索引",
            "/info [id]：查看机器人状态与统计信息，或查看指定表情包详情",
            "/cancel (/c)：取消当前正在执行的命令",
        ]
        _assert_no_reply(call_args)

    @pytest.mark.asyncio
    @patch.object(help, "is_authorized", return_value=False)
    async def test_unauthorized_user_ignored(self, mock_auth: MagicMock) -> None:
        """非授权用户应被静默忽略。"""
        _reset_mocks()
        bot = _make_bot()
        matcher = _make_matcher()

        await handle_help(bot, _make_event("999"), matcher)

        _mock_cmd.finish.assert_not_called()
        matcher.finish.assert_not_called()
        bot.send.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(help, "is_authorized", return_value=True)
    async def test_group_chat_reply(self, mock_auth: MagicMock) -> None:
        """群聊中授权用户应收到带 reply 的帮助文本。"""
        _reset_mocks()
        matcher = _make_matcher()

        await handle_help(
            _make_bot(), _make_event("111", message_type="group"), matcher
        )

        matcher.finish.assert_awaited_once()
        reply = matcher.finish.call_args[0][0]
        _assert_has_reply(reply)
        text = extract_message_text(reply)
        assert "/help" in text
