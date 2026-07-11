"""日志上下文工具。

提供 request_id 的隐式传播、请求 ID 注入 filter 和操作耗时统计。
"""

import asyncio
import contextvars
import functools
import inspect
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Generator, TypeVar

F = TypeVar("F", bound=Callable)
_T = TypeVar("_T")

REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    """获取当前上下文的 request_id。

    Returns:
        当前 request_id；未设置时返回 None。
    """
    return REQUEST_ID.get()


def generate_request_id() -> str:
    """生成短请求 ID。

    Returns:
        uuid hex 前 8 位，足够一次用户请求的全链路追踪。
    """
    return uuid.uuid4().hex[:8]


@contextmanager
def set_request_id(request_id: str | None) -> Generator[None, None, None]:
    """设置当前上下文的 request_id，退出时自动恢复。

    Args:
        request_id: 要设置的请求 ID；None 表示清空当前上下文。

    Yields:
        无。
    """
    token = REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        REQUEST_ID.reset(token)


async def run_sync_with_request_id(
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> _T:
    """在线程池中执行同步函数，并在线程内恢复 request_id。

    调用前捕获当前 request_id，在线程包装函数内通过 ``set_request_id`` 恢复，
    保证跨 ``asyncio.to_thread`` 边界的日志仍带 ``[req:xxx]`` 前缀。

    Args:
        fn: 要在线程中执行的同步函数。
        *args: 传给 fn 的位置参数。
        **kwargs: 传给 fn 的关键字参数。

    Returns:
        fn 的返回值。
    """
    rid = get_request_id()

    def _wrapper(*args: Any, **kwargs: Any) -> _T:
        with set_request_id(rid):
            return fn(*args, **kwargs)

    return await asyncio.to_thread(_wrapper, *args, **kwargs)


class RequestIdFilter(logging.Filter):
    """把当前 request_id 注入日志消息前的 Filter。

    注意：本 Filter 应只在顶层 ``bot`` logger 上注册一次，子 logger 通过继承获得。
    重复注册会导致 ``[req:xxx]`` 前缀被重复添加。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """将当前 request_id 注入日志记录的消息前。

        Args:
            record: 日志记录对象。

        Returns:
            始终返回 True，表示该记录继续向后传递。
        """
        rid = get_request_id()
        if rid is not None:
            record.msg = f"[req:{rid}] {record.msg}"
        return True


class timed:
    """操作耗时统计。

    支持三种用法：
    1. async with timed(logger, "操作名"):
           ...
    2. with timed(logger, "操作名"):
           ...
    3. @timed(logger, "操作名")
       def func(...): ...

    注意：同一个 ``timed`` 实例不宜被多个协程/线程并发使用，否则 ``_start`` 会被覆盖，
    导致计时不准。上下文管理器用法应每次创建新实例；装饰器用法天然顺序进入，安全。
    """

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        level: int = logging.DEBUG,
    ) -> None:
        """初始化 timed 实例。

        Args:
            logger: 输出耗时日志的 logger。
            operation: 操作名称，会出现在日志消息中。
            level: 耗时日志的输出级别，默认为 DEBUG。
        """
        self._logger = logger
        self._operation = operation
        self._level = level
        self._start: float = 0.0

    def __enter__(self) -> "timed":
        """进入同步上下文，记录起始时间。

        Returns:
            timed 实例自身。
        """
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object | None,
    ) -> None:
        """退出同步上下文，输出耗时日志。

        Args:
            exc_type: 异常类型；无异常时为 None。
            _exc_val: 异常对象；未使用，以下划线前缀命名。
            _exc_tb: 异常 traceback；未使用，以下划线前缀命名。
        """
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        status = "失败" if exc_type is not None else "完成"
        self._logger.log(
            self._level,
            "%s %s，耗时 %.2f ms",
            self._operation,
            status,
            elapsed_ms,
        )

    async def __aenter__(self) -> "timed":
        """进入异步上下文，委托给同步实现。

        Returns:
            timed 实例自身。
        """
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object | None,
    ) -> None:
        """退出异步上下文，委托给同步实现。

        Args:
            exc_type: 异常类型；无异常时为 None。
            _exc_val: 异常对象；未使用，以下划线前缀命名。
            _exc_tb: 异常 traceback；未使用，以下划线前缀命名。
        """
        self.__exit__(exc_type, _exc_val, _exc_tb)

    def __call__(self, func: F) -> F:
        """支持装饰器用法。

        Args:
            func: 要装饰的函数；支持同步函数与协程函数。

        Returns:
            包装后的函数，保持原函数签名与名称。
        """
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: object, **kwargs: object) -> object:
                async with timed(self._logger, self._operation, self._level):
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: object, **kwargs: object) -> object:
            with timed(self._logger, self._operation, self._level):
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]
