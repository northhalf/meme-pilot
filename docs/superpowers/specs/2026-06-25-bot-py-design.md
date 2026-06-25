# bot.py — NoneBot2 入口设计

> 日期：2026-06-25
> 状态：已批准

## 目标

实现 `bot/bot.py`，NoneBot2 应用入口，负责：
1. 配置 NoneBot2 框架（驱动器、适配器、插件加载）
2. 在 startup hook 中初始化所有 engine 服务并执行首次索引同步
3. 同步失败时阻止 Bot 启动

## 技术选型

| 项 | 选择 |
|----|------|
| 驱动器 | fastapi（内置，轻量） |
| 适配器 | nonebot-adapter-onebot（反向 WebSocket） |
| 插件加载 | `nonebot.load_plugins("bot/plugins")` |
| 配置来源 | 环境变量 + `.env` 文件 |

## 启动流程

```
NoneBot2 初始化
  ├─ nonebot.init() — 从 .env 加载配置
  ├─ nonebot.register_adapter(ONEBOT_V11)
  ├─ nonebot.load_plugins("bot/plugins")
  └─ driver.run(host=BOT_HOST, port=BOT_PORT)
       └─ on_startup hook:
           ├─ setup_logging("log")
           ├─ 创建 DeepSeekOcrService(api_key, base_url, model)
           ├─ 创建 EmbeddingService(api_key, base_url, model)
           ├─ 创建 RerankService(api_key, base_url, model)
           ├─ 创建 ImageOptimizer()
           ├─ 创建 IndexManager(data_dir, memes_dir, ocr, embedding, optimizer, concurrency, no_text_dir)
           ├─ index_manager.load()  # 加载 index.json
           ├─ await index_manager.sync_with_filesystem()  # 首次同步
           │   └─ 失败 → raise RuntimeError，阻止启动
           ├─ 创建 AIMatcher(index_manager, embedding_service, rerank_service)
           ├─ 创建 KeywordSearcher(index_manager)
           └─ app_state.init_app(...)
```

## 环境变量映射

| 环境变量 | 用途 | 默认值 |
|----------|------|--------|
| `BOT_HOST` | 驱动器监听地址 | `0.0.0.0` |
| `BOT_PORT` | 驱动器监听端口 | `8080` |
| `DEEPSEEK_API_KEY` | OCR + Rerank API Key | 必填 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | Rerank 模型名 | `deepseek-v4-flash` |
| `SILICONFLOW_API_KEY` | OCR API Key | 必填 |
| `SILICONFLOW_BASE_URL` | SiliconFlow 地址 | `https://api.siliconflow.cn/v1` |
| `SILICONFLOW_OCR_MODEL` | OCR 模型名 | `deepseek-ai/DeepSeek-OCR` |
| `EMBEDDING_API_KEY` | Embedding API Key | 必填 |
| `EMBEDDING_BASE_URL` | Embedding 地址 | `https://api.siliconflow.cn/v1` |
| `EMBEDDING_MODEL` | Embedding 模型名 | `BAAI/bge-m3` |
| `SYNC_CONCURRENCY` | 索引同步并发上限 | `5` |

## 错误处理

- **同步失败**：startup hook 中 `sync_with_filesystem()` 抛异常 → NoneBot2 不启动
- **缺少必填环境变量**：各 Service 构造函数或 NoneBot2 init 阶段报错 → 阻止启动
- **插件加载失败**：NoneBot2 自身报错 → 阻止启动

## 文件变更

| 文件 | 操作 |
|------|------|
| `bot/bot.py` | 新建 |
| `pyproject.toml` | 确认 fastapi/uvicorn 依赖已存在 |
| `docs/api/API.md` | 更新（新增 bot.py 条目） |
