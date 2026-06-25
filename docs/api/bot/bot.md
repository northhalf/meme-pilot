# bot/bot.py — NoneBot2 入口 API

> NoneBot2 应用入口，负责框架初始化、引擎服务组装、首次索引同步和插件加载。

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

### `_on_startup() -> None`

NoneBot2 启动钩子，按顺序执行：

1. `setup_logging("log")` — 配置日志
2. 创建 `DeepSeekOcrService`、`EmbeddingService`、`RerankService`、`ImageOptimizer`
3. 创建 `IndexManager` 并调用 `load()`
4. `await index_manager.sync_with_filesystem()` — 首次索引同步
5. 创建 `AIMatcher`、`KeywordSearcher`
6. `app_state.init_app(...)` — 注册全局单例

| | 类型 | 说明 |
|--|------|------|
| **异常** | `RuntimeError` | 索引同步失败时抛出，阻止 Bot 启动 |

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `BOT_HOST` | 否 | `0.0.0.0` | 驱动器监听地址 |
| `BOT_PORT` | 否 | `8080` | 驱动器监听端口（无效值回退 8080） |
| `SYNC_CONCURRENCY` | 否 | `5` | 索引同步并发上限 |
| `DEEPSEEK_API_KEY` | 是 | — | DeepSeek API Key（OCR + Rerank） |
| `SILICONFLOW_API_KEY` | 是 | — | SiliconFlow API Key（OCR） |
| `EMBEDDING_API_KEY` | 是 | — | Embedding API Key |

其余可选环境变量（`DEEPSEEK_BASE_URL`、`SILICONFLOW_BASE_URL` 等）由各 engine 服务从环境变量读取，详见对应模块文档。
