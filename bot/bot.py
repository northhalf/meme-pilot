"""NoneBot2 入口 — MemePilot QQ 表情包机器人。

启动流程：
1. 初始化 NoneBot2 框架（fastapi 驱动器 + OneBot V11 适配器）
2. 注册 startup hook：初始化 engine 服务并预热关键词搜索，后台执行首次索引同步
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
    MEMES_DELETED_DIR,
    MEMES_DIR,
    MEMES_REPLACED_DIR,
    PROJECT_ROOT,
    read_bot_port,
    read_convert_to_webp,
    read_embedding_provider,
    read_int_env,
    read_ocr_provider,
)
from bot.log_context import set_request_id
from bot.engine import (
    CollectionManager,
    ImageOptimizer,
    KeywordSearcher,
    MetadataStore,
    VectorStore,
)
from bot.engine.provider_factory import (
    create_embedding_provider,
    create_ocr_provider,
)
from bot.engine.random_searcher import RandomSearcher
from bot.engine.semantic_searcher import SemanticSearcher
from bot.engine.combined_searcher import CombinedSearcher
from bot.index_manager import IndexManager
from bot.logging_config import setup_logging

logger = logging.getLogger("bot")


async def _background_sync() -> None:
    """后台索引同步与关键词搜索预热任务。

    在 Bot 对外可用前并发完成：
    - jieba 默认词典预热（避免首个模糊查询承担初始化耗时）
    - 首次索引刷新

    依赖 app_state 已注册 IndexManager 与 KeywordSearcher。
    """
    from bot.app_state import get_index_manager, get_keyword_searcher

    index_manager = get_index_manager()
    keyword_searcher = get_keyword_searcher()
    with set_request_id("background"):
        result = await asyncio.gather(
            asyncio.to_thread(keyword_searcher.warm_up),
            index_manager.refresh(),
        )
        refresh_result = result[1]
        if refresh_result.failed:
            logger.warning("同步失败文件（前 10 个）: %s", refresh_result.failed[:10])


async def _on_startup() -> None:
    """NoneBot2 启动钩子 — 初始化引擎服务，注册后等待预热与首次索引同步完成。

    流程：
    1. 配置日志
    2. 创建 OCR / Embedding / ImageOptimizer 服务
    3. 创建 MetadataStore + VectorStore
    4. 创建 CollectionManager
    5. 创建 KeywordSearcher / RandomSearcher / SemanticSearcher / CombinedSearcher
    6. 创建 IndexManager 并加载索引
    7. 注册到 app_state 供插件获取
    8. 并发执行 jieba 预热与首次索引刷新，等待完成后 Bot 才真正可用
    """
    # 1. 日志
    setup_logging("log")
    logger.info("MemePilot 正在启动...")

    # 2. 根据环境变量选择 OCR / Embedding 引擎
    ocr_service = create_ocr_provider(read_ocr_provider())
    embedding_service = create_embedding_provider(read_embedding_provider())
    logger.info(
        "OCR 引擎: %s, Embedding 引擎: %s",
        read_ocr_provider(),
        read_embedding_provider(),
    )
    image_optimizer = ImageOptimizer(
        concurrency=read_int_env("COMPRESS_CONCURRENCY"),
        should_convert_to_webp=read_convert_to_webp(),
    )
    logger.info(
        "图片优化器已初始化: convert_to_webp=%s",
        read_convert_to_webp(),
    )

    # 3. 创建 MetadataStore 与 VectorStore
    metadata_store = MetadataStore(str(INDEX_DB_PATH))
    vector_store = VectorStore(str(CHROMA_DIR))

    # 4. 创建合集管理器
    collection_manager = CollectionManager(metadata_store)

    # 5. 创建搜索服务（IndexManager 内部持锁后委托调用）
    keyword_searcher = KeywordSearcher(metadata_store)
    random_searcher = RandomSearcher(metadata_store, keyword_searcher)
    semantic_searcher = SemanticSearcher(metadata_store, vector_store)
    combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)

    # 6. 创建 IndexManager 并加载索引
    memes_dir = str(MEMES_DIR)

    index_manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=memes_dir,
        deleted_dir=str(MEMES_DELETED_DIR),
        replaced_dir=str(MEMES_REPLACED_DIR),
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        optimizer=image_optimizer,
        keyword_searcher=keyword_searcher,
        random_searcher=random_searcher,
        semantic_searcher=semantic_searcher,
        combined_searcher=combined_searcher,
        collection_manager=collection_manager,
    )
    with set_request_id("background"):
        await index_manager.load()

    # 7. 注册到 app_state（Bot 立即可用）
    init_app(
        index_manager=index_manager,
        metadata_store=metadata_store,
        vector_store=vector_store,
        ocr_service=ocr_service,
        embedding_service=embedding_service,
        image_optimizer=image_optimizer,
        keyword_searcher=keyword_searcher,
        random_searcher=random_searcher,
        semantic_searcher=semantic_searcher,
        combined_searcher=combined_searcher,
        collection_manager=collection_manager,
    )
    logger.info("MemePilot 服务已注册，正在等待预热与索引同步完成...")

    # 8. 并发执行 jieba 预热与首次索引刷新，等待完成后 Bot 对外可用
    await _background_sync()
    logger.info("MemePilot 启动完成")


async def _on_shutdown() -> None:
    """NoneBot2 关闭钩子 — 先关闭 IndexManager，再关闭各服务。"""
    from bot.app_state import (
        get_embedding_service,
        get_index_manager,
        get_ocr_service,
    )

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

    try:
        embedding_service = get_embedding_service()
        if embedding_service is not None:
            await embedding_service.close()
            logger.info("Embedding 服务 HTTP 会话已关闭")
    except RuntimeError:
        pass


def main() -> None:
    """NoneBot2 主入口。"""
    # 初始化 NoneBot2（driver、host、port 从环境变量读取）
    nonebot.init(
        driver="~fastapi",
        host=os.environ.get("BOT_HOST", "0.0.0.0"),
        port=read_bot_port(),
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
