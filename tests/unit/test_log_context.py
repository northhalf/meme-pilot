"""bot/log_context.py 单元测试。"""

import logging

import pytest

from bot.log_context import (
    RequestIdFilter,
    generate_request_id,
    get_request_id,
    set_request_id,
    timed,
)


class _Capture(logging.Handler):
    """用于测试的临时日志捕获 Handler。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture_with_filter(logger: logging.Logger) -> _Capture:
    """创建带 RequestIdFilter 的捕获 Handler 并附加到 logger。"""
    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    handler.addFilter(RequestIdFilter())
    logger.addHandler(handler)
    return handler


def test_generate_request_id_format():
    """generate_request_id 应返回 8 位 hex 字符串。"""
    rid = generate_request_id()
    assert isinstance(rid, str)
    assert len(rid) == 8
    assert all(c in "0123456789abcdef" for c in rid)


def test_set_request_id_sets_and_resets():
    """set_request_id 应设置并恢复 request_id。"""
    assert get_request_id() is None
    with set_request_id("abc123"):
        assert get_request_id() == "abc123"
    assert get_request_id() is None


def test_set_request_id_nested():
    """嵌套 set_request_id 不应串号。"""
    with set_request_id("outer"):
        assert get_request_id() == "outer"
        with set_request_id("inner"):
            assert get_request_id() == "inner"
        assert get_request_id() == "outer"
    assert get_request_id() is None


def test_request_id_filter_injects_prefix(caplog):
    """RequestIdFilter 应在日志消息前注入 [req:xxx]。"""
    logger = logging.getLogger("test_request_id_filter")
    logger.setLevel(logging.DEBUG)
    capture = _capture_with_filter(logger)

    with set_request_id("rid123"):
        logger.info("测试消息")

    record = capture.records[0]
    assert "[req:rid123] 测试消息" in record.getMessage()


def test_request_id_filter_no_prefix_without_id(caplog):
    """无 request_id 时不应注入前缀。"""
    logger = logging.getLogger("test_request_id_filter_no_id")
    logger.setLevel(logging.DEBUG)
    capture = _capture_with_filter(logger)

    logger.info("无 id 消息")

    record = capture.records[0]
    assert record.msg == "无 id 消息"


def test_request_id_filter_preserves_lazy_formatting_args(caplog):
    """RequestIdFilter 应保留 record.args，不破坏延迟格式化。"""
    logger = logging.getLogger("test_request_id_filter_args")
    logger.setLevel(logging.DEBUG)
    capture = _capture_with_filter(logger)

    with set_request_id("rid123"):
        logger.info("用户 %s 调用", "alice")

    record = capture.records[0]
    assert record.msg == "[req:rid123] 用户 %s 调用"
    assert record.args == ("alice",)
    assert record.getMessage() == "[req:rid123] 用户 alice 调用"


@pytest.mark.asyncio
async def test_timed_async_context_manager(caplog):
    """timed 异步上下文管理器应记录耗时。"""
    logger = logging.getLogger("test_timed_async")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    async with timed(logger, "异步操作"):
        pass

    assert any("异步操作 完成，耗时" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_timed_async_decorator(caplog):
    """timed 异步装饰器应记录耗时。"""
    logger = logging.getLogger("test_timed_async_deco")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    @timed(logger, "装饰操作")
    async def do_something():
        return 42

    result = await do_something()
    assert result == 42
    assert any("装饰操作 完成，耗时" in r.getMessage() for r in caplog.records)


def test_timed_sync_context_manager(caplog):
    """timed 同步上下文管理器应记录耗时。"""
    logger = logging.getLogger("test_timed_sync")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    with timed(logger, "同步操作"):
        pass

    assert any("同步操作 完成，耗时" in r.getMessage() for r in caplog.records)


def test_timed_sync_context_manager_failure(caplog):
    """timed 同步上下文管理器在异常发生时应记录失败。"""
    logger = logging.getLogger("test_timed_sync_fail")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    with pytest.raises(ValueError):
        with timed(logger, "同步失败操作"):
            raise ValueError("boom")

    assert any("同步失败操作 失败，耗时" in r.getMessage() for r in caplog.records)


def test_timed_sync_decorator(caplog):
    """timed 同步装饰器应记录耗时。"""
    logger = logging.getLogger("test_timed_sync_deco")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    @timed(logger, "同步装饰操作")
    def do_something():
        return 42

    result = do_something()
    assert result == 42
    assert any("同步装饰操作 完成，耗时" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_timed_records_failure_on_exception(caplog):
    """timed 在异常发生时应记录失败。"""
    logger = logging.getLogger("test_timed_fail")
    logger.setLevel(logging.DEBUG)
    handler = caplog.handler
    logger.addHandler(handler)

    with pytest.raises(ValueError):
        async with timed(logger, "失败操作"):
            raise ValueError("boom")

    assert any("失败操作 失败，耗时" in r.getMessage() for r in caplog.records)
