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

注：以下配置读取函数定义于 `bot/config.py`，由 `bot.py` 导入使用。

### `read_int_env(key: str, default: int) -> int | None`

从环境变量读取可选整数值。

| | 类型 | 说明 |
|--|------|------|
| **key** | `str` | 环境变量名 |
| **default** | `int` | 回退默认值 |
| **返回** | `int \| None` | 有效正整数或 None（Service 收到 None 后会使用自身的默认值 5） |
| **异常输入** | — | 空字符串、非整数、零、负数均返回 None |

### `read_bot_port() -> int`

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
2. 通过 `provider_factory.create_ocr_provider(read_ocr_provider())` 与 `provider_factory.create_embedding_provider(read_embedding_provider())` 创建 OCR/Embedding 服务，以及 `RerankService`、`ImageOptimizer`；支持的 OCR 引擎：`paddle`（PaddleOCR 云 API）、`deepseek`（OpenAI 兼容 OCR，示例默认硅基流动 DeepSeek-OCR）、`rapidocr`（RapidOCR 本地 OCR）；支持的 Embedding 引擎：`openai`（OpenAI 兼容 Embedding，示例默认 GLM `embedding-3`）、`google`（Google Embedding API）
3. 创建 `MetadataStore(str(INDEX_DB_PATH))` 与 `VectorStore(str(CHROMA_DIR))`，再创建 `AIMatcher(metadata_store, vector_store, embedding_provider, rerank_provider)` 与 `KeywordSearcher(metadata_store)`
4. 创建 `IndexManager(metadata_store, vector_store, memes_dir, ocr_provider, embedding_provider, optimizer, keyword_searcher, ai_matcher)` 并调用 `load()`（搜索/匹配服务由 IndexManager 内部持锁后委托调用）
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
| `EMBEDDING_CONCURRENCY` | 否 | `5` | Embedding API 并发上限 |
| `OCR_CONCURRENCY` | 否 | `5` | OCR API 并发上限（不区分 OCR_PROVIDER） |
| `RERANK_CONCURRENCY` | 否 | `5` | LLM 精排并发上限 |
| `COMPRESS_CONCURRENCY` | 否 | `5` | 图片压缩并发上限 |
| `READ_LOCK_TIMEOUT` | 否 | `00:00:30` | `search` / `ai_match` 等待写锁释放的最大时间 |
| `ADD_COMMAND_TIMEOUT` | 否 | `00:01:00` | `/add` 从提交到结果返回的用户等待超时 |
| `SESSION_EXPIRE_TIMEOUT` | 否 | `00:01:00` | 用户命令会话超时时间 |
| `OCR_PROVIDER` | 否 | `rapidocr` | OCR 引擎选择：`rapidocr`（RapidOCR 本地 ONNX，默认）、`paddle`（PaddleOCR 云 API）、`deepseek`（OpenAI 兼容 OCR） |
| `OCR_TEXT_SCORE` | 否 | `0.9` | OCR 文本置信度阈值，`paddle` 与 `rapidocr` 共用 |
| `EMBEDDING_PROVIDER` | 否 | `openai` | Embedding 引擎选择：`openai`（OpenAI 兼容 Embedding，默认）、`google`（Google Embedding API） |
| `GOOGLE_API_KEY` | google 时必填 | — | Google API Key（`EMBEDDING_PROVIDER=google` 时必填） |
| `GOOGLE_BASE_URL` | 否 | — | Google API 代理地址（`EMBEDDING_PROVIDER=google` 时可选） |
| `GOOGLE_EMBEDDING_MODEL` | 否 | `gemini-embedding-001` | Google Embedding 模型名 |
| `DEEPSEEK_API_KEY` | 是 | — | DeepSeek API Key（Rerank 精排） |
| `OPENAI_OCR_API_KEY` | deepseek 时必填 | — | OpenAI 兼容 OCR API Key（`OCR_PROVIDER=deepseek` 时必填） |
| `PADDLEOCR_ACCESS_TOKEN` | paddle 时必填 | — | PaddleOCR 云 API Access Token（`OCR_PROVIDER=paddle` 时必填） |
| `OPENAI_EMBEDDING_API_KEY` | openai 时必填 | — | OpenAI 兼容 Embedding API Key（`EMBEDDING_PROVIDER=openai` 时必填） |
| `OPENAI_EMBEDDING_BASE_URL` | 否 | `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容 Embedding 地址（`EMBEDDING_PROVIDER=openai` 时可选） |
| `OPENAI_EMBEDDING_MODEL` | 否 | `embedding-3` | OpenAI 兼容 Embedding 模型名（`EMBEDDING_PROVIDER=openai` 时可选） |

其余可选环境变量（`DEEPSEEK_BASE_URL`、`OPENAI_OCR_BASE_URL`、`OPENAI_OCR_MODEL` 等）由各 engine 服务从环境变量读取，详见对应模块文档。

并发控制由各 Service 自身的 `asyncio.Semaphore` 管理，不再由 IndexManager 统一控制。各 Service 的 `__init__` 均接受 `concurrency: int | None = None` 参数，默认读取对应环境变量。
