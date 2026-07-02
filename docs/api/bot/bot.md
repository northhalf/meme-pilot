# bot/bot.py — NoneBot2 入口 API

> NoneBot2 应用入口，负责框架初始化、引擎服务组装、后台索引同步和插件加载。

## 函数

### `main() -> None`

NoneBot2 主入口。

| | 说明 |
|--|------|
| **初始化** | `nonebot.init(driver="~fastapi", host=..., port=...)` |
| **适配器** | 注册 OneBot V11 适配器（反向 WebSocket） |
| **插件** | 加载 `bot/plugins/` 下所有命令插件 |
| **启动** | `nonebot.run()` |

### `_read_sync_concurrency() -> int | None`

从环境变量 `SYNC_CONCURRENCY` 读取索引同步并发上限。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int \| None` | 有效正整数或 None（使用 IndexManager 默认值 5） |
| **异常输入** | — | 空字符串、非整数、零、负数均返回 None |

### `_read_bot_port() -> int`

从环境变量 `BOT_PORT` 读取 Bot 监听端口。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 有效端口号，无效值回退为 8080 |

### `_background_sync(index_manager: IndexManager) -> None`

后台索引同步任务，不阻塞启动。

| | 类型 | 说明 |
|--|------|------|
| **参数** | `IndexManager` | 已加载索引的 IndexManager 实例 |
| **锁** | — | `IndexManager.refresh()` 内部持独占写锁；无需调用方额外加锁 |
| **异常** | — | 同步失败时记录错误日志，Bot 继续运行 |

### `_on_startup() -> None`

NoneBot2 启动钩子，按顺序执行：

1. `setup_logging("log")` — 配置日志
2. 根据 `OCR_PROVIDER` 环境变量创建 OCR 引擎（`paddle` → `PaddleOcrClientService`，`deepseek` → `DeepSeekOcrService`），以及 `EmbeddingService`、`RerankService`、`ImageOptimizer`
3. 创建 `MetadataStore(str(INDEX_DB_PATH))` 与 `VectorStore(str(CHROMA_DIR))`，再创建 `AIMatcher(metadata_store, vector_store, embedding_provider, rerank_provider)` 与 `KeywordSearcher(metadata_store)`
4. 创建 `IndexManager(metadata_store, vector_store, memes_dir, ocr_provider, embedding_provider, optimizer, keyword_searcher, ai_matcher, sync_concurrency)` 并调用 `load()`（搜索/匹配服务由 IndexManager 内部持锁后委托调用）
5. `app_state.init_app(...)` — 注册全局单例（含 IndexManager，Bot 立即可用）
6. `asyncio.create_task(_background_sync(index_manager))` — 后台索引同步

| | 类型 | 说明 |
|--|------|------|
| **行为** | — | `init_app()` 在 sync 之前调用，Bot 启动后立即可用 |
| **同步期间** | — | `IndexManager.refresh()` 持写锁期间，读取类操作将等待；插件层无需手动检查锁状态 |
| **同步失败** | — | 记录错误日志，Bot 继续运行（用已有索引） |

### `_on_shutdown() -> None`

NoneBot2 关闭钩子，释放 OCR 服务的 HTTP 会话与两个 Store 的连接。

| | 类型 | 说明 |
|--|------|------|
| **行为** | — | 调用 `index_manager.close()` 关闭 IndexManager（内部关闭 MetadataStore 与 VectorStore），然后调用 `get_ocr_service().close()` 释放 OCR HTTP 会话 |
| **未初始化** | — | 任一实例未初始化时跳过（不抛出异常） |

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `BOT_HOST` | 否 | `0.0.0.0` | 驱动器监听地址 |
| `BOT_PORT` | 否 | `8080` | 驱动器监听端口（无效值回退 8080） |
| `SYNC_CONCURRENCY` | 否 | `5` | 索引同步并发上限 |
| `READ_LOCK_TIMEOUT` | 否 | `00:00:30` | `search` / `ai_match` 等待写锁释放的最大时间 |
| `ADD_COMMAND_TIMEOUT` | 否 | `00:01:00` | `/add` 从提交到结果返回的用户等待超时 |
| `SESSION_EXPIRE_TIMEOUT` | 否 | `00:01:00` | 用户命令会话超时时间 |
| `OCR_PROVIDER` | 否 | `paddle` | OCR 引擎选择：`paddle`（PaddleOCR 云 API，默认）或 `deepseek`（硅基流动 DeepSeek-OCR） |
| `DEEPSEEK_API_KEY` | 是 | — | DeepSeek API Key（Rerank 精排） |
| `SILICONFLOW_API_KEY` | 仅 deepseek | — | SiliconFlow API Key（DeepSeek-OCR） |
| `PADDLEOCR_ACCESS_TOKEN` | 仅 paddle | — | PaddleOCR 云 API Access Token |
| `EMBEDDING_API_KEY` | 是 | — | Embedding API Key |

其余可选环境变量（`DEEPSEEK_BASE_URL`、`SILICONFLOW_BASE_URL` 等）由各 engine 服务从环境变量读取，详见对应模块文档。
