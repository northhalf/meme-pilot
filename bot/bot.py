"""NoneBot2 入口 — MemePilot QQ 表情包机器人。

启动流程：
1. 初始化 NoneBot2 框架（fastapi 驱动器 + OneBot V11 适配器）
2. 注册 startup hook：初始化 engine 服务并执行首次索引同步
3. 加载 bot/plugins/ 下所有命令插件
4. 启动驱动器监听反向 WebSocket
"""

import logging
import os

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from bot.app_state import init_app
from bot.config import MEMES_DIR, PROJECT_ROOT
from bot.engine import (
    AIMatcher,
    DeepSeekOcrService,
    EmbeddingService,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    RerankService,
)
from bot.logging_config import setup_logging

logger = logging.getLogger(__name__)


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


async def _on_startup() -> None:
    """NoneBot2 启动钩子 — 初始化引擎服务并执行首次索引同步。

    流程：
    1. 配置日志
    2. 创建 OCR / Embedding / Rerank / ImageOptimizer 服务
    3. 创建 IndexManager 并加载现有索引
    4. 执行 sync_with_filesystem()，失败则抛异常阻止启动
    5. 创建 AIMatcher / KeywordSearcher
    6. 注册到 app_state 供插件获取

    Raises:
        RuntimeError: 索引同步失败，阻止 Bot 启动。
    """
    # 1. 日志
    setup_logging("log")
    logger.info("MemePilot 正在启动...")

    # 2. 创建引擎服务（各服务从环境变量读取配置）
    ocr_service = DeepSeekOcrService()
    embedding_service = EmbeddingService()
    rerank_service = RerankService()
    image_optimizer = ImageOptimizer()

    # 3. 创建 IndexManager 并加载索引
    data_dir = str(PROJECT_ROOT / "data")
    memes_dir = str(MEMES_DIR)
    sync_concurrency = _read_sync_concurrency()

    index_manager = IndexManager(
        data_dir=data_dir,
        memes_dir=memes_dir,
        ocr_provider=ocr_service,
        embedding_provider=embedding_service,
        sync_concurrency=sync_concurrency,
        optimizer=image_optimizer,
    )
    index_manager.load()

    # 4. 首次索引同步
    logger.info("开始首次索引同步...")
    try:
        result = await index_manager.sync_with_filesystem()
    except Exception as e:
        logger.exception("首次索引同步失败，Bot 无法启动")
        raise RuntimeError("首次索引同步失败") from e

    logger.info(
        "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 失败=%d",
        result.added,
        result.deleted,
        result.deduped,
        result.no_text_moved,
        len(result.failed),
    )

    if index_manager.entry_count == 0 and result.added == 0:
        logger.warning("表情包目录为空，Bot 启动但搜索功能不可用")

    # 5. 创建搜索和匹配服务
    ai_matcher = AIMatcher(
        index_provider=index_manager,
        embedding_provider=embedding_service,
        rerank_provider=rerank_service,
    )
    keyword_searcher = KeywordSearcher(index_provider=index_manager)

    # 6. 注册到 app_state
    init_app(
        index_manager=index_manager,
        ocr_service=ocr_service,
        embedding_service=embedding_service,
        image_optimizer=image_optimizer,
        ai_matcher=ai_matcher,
        keyword_searcher=keyword_searcher,
    )

    logger.info("MemePilot 启动完成")


def main() -> None:
    """NoneBot2 主入口。"""
    # 初始化 NoneBot2（driver、host、port 从环境变量读取）
    nonebot.init(
        driver="~fastapi",
        host=os.environ.get("BOT_HOST", "0.0.0.0"),
        port=_read_bot_port(),
        env_file=str(PROJECT_ROOT / ".env"),
    )

    # 注册 startup hook（必须在 init 之后）
    driver = nonebot.get_driver()
    driver.on_startup(_on_startup)

    # 注册 OneBot V11 适配器
    driver.register_adapter(OneBotV11Adapter)

    # 加载插件
    nonebot.load_plugins(str(PROJECT_ROOT / "bot" / "plugins"))

    # 启动
    nonebot.run()


if __name__ == "__main__":
    main()
