"""NoneBot2 入口 — MemePilot QQ 表情包机器人。

启动流程：
1. 初始化 NoneBot2 框架（fastapi 驱动器 + OneBot V11 适配器）
2. 注册 startup hook：初始化 engine 服务，后台执行首次索引同步
3. 加载 bot/plugins/ 下所有命令插件
4. 启动驱动器监听反向 WebSocket
"""

import asyncio
import logging
import os

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from bot.app_state import init_app
from bot.config import (
    CHROMA_DIR,
    INDEX_DB_PATH,
    MEMES_DIR,
    PROJECT_ROOT,
    read_ocr_provider,
)
from bot.engine import (
    AIMatcher,
    DeepSeekOcrService,
    EmbeddingService,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    MetadataStore,
    PaddleOcrClientService,
    RerankService,
    VectorStore,
)
from bot.logging_config import setup_logging

logger = logging.getLogger("bot")


def _read_sync_concurrency() -> int | None:
    """从环境变量读取索引同步并发上限。

    Returns:
        有效正整数或 None（使用默认值）。
    """
    raw = os.environ.get("SYNC_CONCURRENCY", "")
    if not raw:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return None


def _read_bot_port() -> int:
    """从环境变量读取 Bot 监听端口，无效值回退为 8080。"""
    raw = os.environ.get("BOT_PORT", "8080")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 8080


async def _background_sync(index_manager: IndexManager) -> None:
    """后台索引同步任务，不阻塞启动。

    Args:
        index_manager: 已加载索引的 IndexManager 实例。
    """
    logger.info("开始后台索引同步...")
    try:
        result = await index_manager.refresh()
        logger.info(
            "后台索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
            result.added,
            result.deleted,
            result.deduped,
            result.no_text_moved,
            len(result.failed),
        )
        if result.failed:
            logger.warning("同步失败文件（前 10 个）: %s", result.failed[:10])
    except Exception:
        logger.exception("后台索引同步失败，Bot 继续运行（用已有索引）")


async def _on_startup() -> None:
    """NoneBot2 启动钩子 — 初始化引擎服务，后台执行首次索引同步。

    流程：
    1. 配置日志
    2. 创建 OCR / Embedding / Rerank / ImageOptimizer 服务
    3. 创建 MetadataStore + VectorStore
    4. 创建 AIMatcher / KeywordSearcher
    5. 创建 IndexManager 并加载索引
    6. 注册到 app_state 供插件获取（Bot 立即可用）
    7. 后台执行 refresh()（不阻塞启动）
    """
    # 1. 日志
    setup_logging("log")
    logger.info("MemePilot 正在启动...")

    # 2. 根据 OCR_PROVIDER 环境变量选择 OCR 引擎
    provider = read_ocr_provider()
    if provider == "paddle":
        ocr_service = PaddleOcrClientService()
        logger.info("OCR 引擎: PaddleOCR 云 API")
    else:
        ocr_service = DeepSeekOcrService()
        logger.info("OCR 引擎: DeepSeek-OCR（硅基流动）")
    embedding_service = EmbeddingService()
    rerank_service = RerankService()
    image_optimizer = ImageOptimizer()

    # 3. 创建 MetadataStore 与 VectorStore
    metadata_store = MetadataStore(str(INDEX_DB_PATH))
    vector_store = VectorStore(str(CHROMA_DIR))

    # 4. 创建搜索和匹配服务（IndexManager 内部持锁后委托调用）
    ai_matcher = AIMatcher(
        metadata_store=metadata_store,
        vector_store=vector_store,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )
    keyword_searcher = KeywordSearcher(metadata_store)

    # 5. 创建 IndexManager 并加载索引
    memes_dir = str(MEMES_DIR)
    sync_concurrency = _read_sync_concurrency()

    index_manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=memes_dir,
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        optimizer=image_optimizer,
        keyword_searcher=keyword_searcher,
        ai_matcher=ai_matcher,
        sync_concurrency=sync_concurrency,
    )
    index_manager.load()

    # 6. 注册到 app_state（Bot 立即可用）
    init_app(
        index_manager=index_manager,
        metadata_store=metadata_store,
        vector_store=vector_store,
        ocr_service=ocr_service,
        embedding_service=embedding_service,
        image_optimizer=image_optimizer,
        ai_matcher=ai_matcher,
        keyword_searcher=keyword_searcher,
    )
    logger.info("MemePilot 启动完成，后台索引同步进行中...")

    # 7. 后台执行首次索引同步（不阻塞启动）
    asyncio.create_task(_background_sync(index_manager))


async def _on_shutdown() -> None:
    """NoneBot2 关闭钩子 — 先关闭 IndexManager，再关闭 OCR 服务。"""
    from bot.app_state import get_index_manager, get_ocr_service

    try:
        index_manager = get_index_manager()
        await index_manager.close()
        logger.info("IndexManager 已关闭")
    except RuntimeError:
        pass

    try:
        ocr_service = get_ocr_service()
        await ocr_service.close()
        logger.info("OCR 服务 HTTP 会话已关闭")
    except RuntimeError:
        pass


def main() -> None:
    """NoneBot2 主入口。"""
    # 初始化 NoneBot2（driver、host、port 从环境变量读取）
    nonebot.init(
        driver="~fastapi",
        host=os.environ.get("BOT_HOST", "0.0.0.0"),
        port=_read_bot_port(),
        env_file=str(PROJECT_ROOT / ".env"),
    )

    # 注册 startup/shutdown 钩子（必须在 init 之后）
    driver = nonebot.get_driver()
    driver.on_startup(_on_startup)
    driver.on_shutdown(_on_shutdown)

    # 注册 OneBot V11 适配器
    driver.register_adapter(OneBotV11Adapter)

    # 加载插件
    nonebot.load_plugins(str(PROJECT_ROOT / "bot" / "plugins"))

    # 启动
    nonebot.run()


if __name__ == "__main__":
    main()
