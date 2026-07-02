"""asyncio 读写锁，写者优先。"""

import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class IndexRwLock:
    """asyncio 读写锁。

    特性：
    - 多个读者并发持有。
    - 写者独占。
    - 写者优先：有写者等待时，新读者阻塞。
    - 支持获取超时。

    用法：
        async with lock.read(timeout=30):
            ...

        async with lock.write(timeout=30):
            ...
    """

    def __init__(self) -> None:
        """初始化读写锁。"""
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer_active = False
        self._writer_waiters = 0

    async def acquire_read(self, timeout: float | None = None) -> None:
        """获取读锁。

        Args:
            timeout: 最大等待秒数；None 表示无限等待。

        Raises:
            asyncio.TimeoutError: 等待超时。
        """
        async with self._cond:
            await asyncio.wait_for(
                self._cond.wait_for(
                    lambda: not self._writer_active and self._writer_waiters == 0
                ),
                timeout=timeout,
            )
            self._readers += 1

    async def release_read(self) -> None:
        """释放读锁。"""
        async with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self, timeout: float | None = None) -> None:
        """获取写锁。

        Args:
            timeout: 最大等待秒数；None 表示无限等待。

        Raises:
            asyncio.TimeoutError: 等待超时。
        """
        async with self._cond:
            self._writer_waiters += 1
            try:
                await asyncio.wait_for(
                    self._cond.wait_for(
                        lambda: self._readers == 0 and not self._writer_active
                    ),
                    timeout=timeout,
                )
                self._writer_active = True
            finally:
                self._writer_waiters -= 1

    async def release_write(self) -> None:
        """释放写锁。"""
        async with self._cond:
            self._writer_active = False
            self._cond.notify_all()

    @asynccontextmanager
    async def read(self, timeout: float | None = None):
        """读锁 async context manager。"""
        await self.acquire_read(timeout)
        try:
            yield
        finally:
            await self.release_read()

    @asynccontextmanager
    async def write(self, timeout: float | None = None):
        """写锁 async context manager。"""
        await self.acquire_write(timeout)
        try:
            yield
        finally:
            await self.release_write()
