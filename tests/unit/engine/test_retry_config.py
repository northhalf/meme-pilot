# tests/unit/engine/test_retry_config.py
import httpx
import pytest
from unittest import mock

from bot.engine.retry_config import api_retry


class TransientError(RuntimeError):
    pass


@api_retry(extra_exceptions=(TransientError,))
async def flaky_function(fail_count: list[int]) -> str:
    if fail_count[0] < 2:
        fail_count[0] += 1
        raise TransientError("fail")
    return "ok"


@api_retry(extra_exceptions=())
async def no_retry_on_value_error() -> str:
    raise ValueError("should not retry")


@api_retry()
async def _network_operation(mock_call: mock.Mock) -> str:
    mock_call()
    return "success"


@pytest.mark.anyio
async def test_api_retry_succeeds_after_transient_failures() -> None:
    assert await flaky_function([0]) == "ok"


@pytest.mark.anyio
async def test_api_retry_does_not_retry_value_error() -> None:
    with pytest.raises(ValueError, match="should not retry"):
        await no_retry_on_value_error()


@pytest.mark.anyio
async def test_api_retry_default_network_error_retries_twice_then_succeeds() -> None:
    mock_call = mock.Mock(side_effect=[httpx.NetworkError("fail 1"), httpx.NetworkError("fail 2"), None])
    assert await _network_operation(mock_call) == "success"
    assert mock_call.call_count == 3


@pytest.mark.anyio
async def test_api_retry_default_network_error_fails_after_three_attempts() -> None:
    mock_call = mock.Mock(side_effect=httpx.NetworkError("fail"))
    with pytest.raises(httpx.NetworkError, match="fail"):
        await _network_operation(mock_call)
    assert mock_call.call_count == 3


@pytest.mark.anyio
async def test_api_retry_non_retry_exception_called_once() -> None:
    mock_call = mock.Mock(side_effect=ValueError("business error"))
    with pytest.raises(ValueError, match="business error"):
        await _network_operation(mock_call)
    assert mock_call.call_count == 1
