# CLAUDE.md

本文件为 Claude Code 提供项目开发指引。

## 严禁事项

- 禁止自行 `git add` 或 `git commit`，提交必须经用户审核。
- 撰写 specs 后不可跳过用户审阅步骤。

## 必读文档

- 修改需求、架构、命令交互、索引或权限逻辑前，先看 `docs/PRD.md`。
- 修改术语、领域概念或用户可见命名时，必须对照 `CONTEXT.md` 保持术语一致。
- 修改部署、环境变量或操作说明时，检查 `README.md`、`.env.example` 和 `docker-compose.yml`。
- 调用已有模块或新增模块交互前，优先查阅 `docs/api/API.md` 中的参数签名与返回值说明；仅在文档不准确或信息不足时再阅读源码。
- 每实现一个模块后，更新 `docs/api/API.md`（对外接口）。

## 代码风格

- Python 函数使用 Google 风格 docstring，内容用中文。
- 函数参数、返回值需类型标注。
- 保持现有中文注释和用户提示风格。

## 常用命令

### Docker
```bash
docker compose up -d
docker compose logs -f bot
docker compose down
docker compose build bot && docker compose up -d bot
```

### 本地开发（Python 3.12，使用 uv）
```bash
uv add <包名>              # 生产依赖
uv add --dev pytest       # 测试依赖
uv run python bot/bot.py  # 从项目根目录运行
```

### 测试与检查
```bash
uv run pytest             # 全量测试
uv run python -m compileall bot tests   # 语法检查
```

仅文档变更可不运行测试，但需在提交说明中注明“仅文档变更，未运行测试”。

## 环境变量

`.env.example` 为模板。必填：
- `QQ_ACCOUNT`：机器人 QQ 号
- `AUTHORIZED_USER_IDS`：授权用户白名单（逗号分隔）
- `DEEPSEEK_API_KEY`
- `SILICONFLOW_API_KEY`：用于 OCR
- `EMBEDDING_API_KEY`：用于 Embedding

可选：`BOT_HOST`、`BOT_PORT`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`SILICONFLOW_BASE_URL`、`SILICONFLOW_OCR_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`SYNC_CONCURRENCY`（默认5）、`SESSION_EXPIRE_TIMEOUT`（会话超时，默认60秒）、`NAPCAT_WEBUI_TOKEN`（WebUI 登录密钥，默认 memepilot）。

## 系统架构

- Docker Compose 部署：`napcat`（QQ协议）+ `bot`（NoneBot2 + 插件）。
- v1.0 使用反向 WebSocket，NapCat 连接 NoneBot2（端口仅内网）。
- NapCat WebUI 通过宿主机 `6099` 端口访问。
- 数据目录：`memes/`（图片）、`data/index.db`（sqlite 元数据）、`data/chroma/`（chroma 向量库）、`log/bot.log`（滚动日志）。
- 隐私：图片本地存储；OCR 文本可能发送至 SiliconFlow / DeepSeek。

## 当前实现注意事项

已完成：engine 全部模块（metadata_store、vector_store、index_manager、keyword_searcher、ai_matcher、deepseek_ocr、paddle_ocr、embedding_service、rerank_service、image_optimizer、protocols）、`scripts/migrate_json_to_db.py` 旧 JSON 索引迁移脚本、app_state 共享实例（含 get_ai_matcher、get_keyword_searcher、get_metadata_store、get_vector_store）、config 全局路径常量（含 PROJECT_ROOT、DATA_DIR、INDEX_DB_PATH、CHROMA_DIR、read_session_timeout、read_ocr_provider）、auth 授权校验、bot.session 会话管理（含 timeout_session）、bot.py（NoneBot2 入口，fastapi 驱动器，startup 创建并注入 MetadataStore+VectorStore，shutdown 关闭两个 Store）、/help、/refresh、/search、/add、/ai 和 /cancel 插件及其测试、bot/Dockerfile、napcat/entrypoint.sh。

尚未实现：无。实现或重构前，以 `docs/PRD.md` 和 `CONTEXT.md` 为准，并同步更新 README、`.env.example`、`docker-compose.yml`。