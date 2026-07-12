"""pytest 共享 fixture。"""

from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message


def extract_message_text(message: str | Message) -> str:
    """从字符串或 Message 中提取纯文本内容。"""
    if isinstance(message, str):
        return message
    return message.extract_plain_text()


def _assert_has_reply(msg: str | Message, message_id: int | None = None) -> None:
    """断言消息为包含 reply segment 的 Message。

    Args:
        msg: 待断言的消息对象或字符串。
        message_id: 可选的引用消息 ID，提供时额外断言 reply segment 的 id。
    """
    assert isinstance(msg, Message)
    assert msg[0].type == "reply"
    if message_id is not None:
        assert str(msg[0].data.get("id")) == str(message_id)


def _assert_no_reply(msg: str | Message) -> None:
    """断言消息中不存在 reply segment。

    Args:
        msg: 待断言的消息对象或字符串。
    """
    if isinstance(msg, Message):
        assert not any(seg.type == "reply" for seg in msg)


@pytest.fixture
def tmp_sqlite_path(tmp_path: Path) -> Path:
    """返回一个不存在的 sqlite 数据库文件路径（在 tmp_path 下）。

    MetadataStore.load() 会自动创建该文件与目录。
    """
    return tmp_path / "index.db"


@pytest.fixture
def tmp_chroma_dir(tmp_path: Path) -> Path:
    """返回一个 chroma PersistentClient 目录路径（在 tmp_path 下）。

    VectorStore.load() 会自动创建该目录。
    """
    return tmp_path / "chroma"
