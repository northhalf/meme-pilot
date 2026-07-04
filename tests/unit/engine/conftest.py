"""引擎模块单元测试共享 fixture 与测试替身。"""


class FakeSemaphore:
    """asyncio.Semaphore 测试替身。

    concurrency=0 表示无限制（永远不阻塞）。
    用于注入 Service 测试，验证 acquire/release 行为。
    """

    def __init__(self, concurrency: int = 0) -> None:
        self.concurrency = concurrency
        self.acquire_count = 0
        self.release_count = 0

    async def __aenter__(self) -> "FakeSemaphore":
        self.acquire_count += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        self.release_count += 1
