"""共享网络请求重试配置。"""

import logging
from typing import TypeAlias

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

ExceptionTuple: TypeAlias = tuple[type[Exception], ...]


def api_retry(
    *,
    max_attempts: int = 3,
    wait_min: float = 1,
    wait_max: float = 10,
    multiplier: float = 1,
    extra_exceptions: ExceptionTuple = (),
):
    """网络请求通用重试装饰器工厂。

    默认重试：httpx 网络/连接/超时异常、服务端中途断连（RemoteProtocolError）、
    Python 内置 ConnectionError / TimeoutError，以及调用方传入的额外异常
    （如 OpenAI API 异常）。

    不重试：ValueError、FileNotFoundError 等本地/业务异常。
    """
    exceptions: ExceptionTuple = (
        httpx.NetworkError,
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
        ConnectionError,
        TimeoutError,
    ) + extra_exceptions

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, min=wait_min, max=wait_max),
        retry=retry_if_exception_type(exceptions),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
