"""IndexRwLock 单元测试。"""

import asyncio

import pytest

from bot.index_manager.rwlock import IndexRwLock

pytestmark = pytest.mark.asyncio


async def test_multiple_readers_concurrent() -> None:
    lock = IndexRwLock()
    async with lock.read():
        async with lock.read():
            pass


async def test_write_blocks_read() -> None:
    lock = IndexRwLock()
    async with lock.write():
        with pytest.raises(asyncio.TimeoutError):
            async with lock.read(timeout=0.1):
                pass


async def test_read_blocks_write() -> None:
    lock = IndexRwLock()
    async with lock.read():
        with pytest.raises(asyncio.TimeoutError):
            async with lock.write(timeout=0.1):
                pass


async def test_writer_priority() -> None:
    lock = IndexRwLock()
    writer_started = asyncio.Event()

    async def writer() -> None:
        writer_started.set()
        async with lock.write():
            pass

    async with lock.read():
        task = asyncio.create_task(writer())
        await writer_started.wait()
        await asyncio.sleep(0.05)
        with pytest.raises(asyncio.TimeoutError):
            async with lock.read(timeout=0.1):
                pass
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_two_writes_are_serial() -> None:
    lock = IndexRwLock()
    values: list[int] = []

    async def writer(value: int) -> None:
        async with lock.write():
            values.append(value)
            await asyncio.sleep(0)

    await asyncio.gather(writer(1), writer(2))
    assert values == [1, 2] or values == [2, 1]


async def test_read_timeout_does_not_leak_readers() -> None:
    lock = IndexRwLock()
    async with lock.write():
        with pytest.raises(asyncio.TimeoutError):
            async with lock.read(timeout=0.05):
                pass
    # 写锁释放后应能正常获取读锁
    async with lock.read():
        pass
