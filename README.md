# MemePilot

MemePilot 是一个部署在 Docker 中的 QQ 表情包机器人，帮你从本地表情包库中快速找到想要的表情包。/search、/help、/info 和普通文本支持群聊 @bot 使用；/add、/addtag、/del、/refresh、/ai、/edittext、/setspeaker 仅限私聊。/cancel 私聊和群聊均可使用。

隐私说明：表情包图片始终本地存储；OCR 文本会按 `OCR_PROVIDER` 配置发送给对应服务（默认 `rapidocr` 使用本地 ONNX 模型，无需联网；`paddle` 使用百度 PaddleOCR 云 API；`deepseek` 使用任意 OpenAI 兼容视觉 OCR 服务）；Embedding 调用由 `EMBEDDING_PROVIDER` 指定的服务（默认 `openai`，即 OpenAI 兼容 API）；LLM 精排调用 DeepSeek。

## ✨ 功能

### 🧭 帮助 `/help`
```
你: /help
Bot: 当前可用命令：
     /help：查看命令帮助
     /search <关键词>：按 OCR 文本关键词搜索表情包
     /ai <自然语言描述>：按自然语言描述匹配表情包
     /add [speaker <tags...>]：通过聊天添加一张表情包
     /addtag <id> <tag> [<tag>...]：为指定表情包添加标签
     /del <id>...：删除指定表情包（需确认）
     /edittext <id> <新文本>：修改指定表情包的 OCR 文本
     /setspeaker <id> [说话人]：设置或清空表情包的说话人
     /refresh：扫描 memes/ 并增量更新索引
     /info：查看机器人状态与统计信息
     /cancel：取消当前正在执行的命令
```

授权用户可以直接发送 `/search <关键词>` 搜索表情包，或在群聊中 @bot + `/search <关键词>` 触发。授权用户发送普通文本时（私聊或群聊 @bot），Bot 默认当作 `/search` 执行搜索。发送未知斜杠命令时，Bot 会提示"未知命令"并附帮助摘要。

### 🔍 关键词搜索 `/search`
```
你: /search 加班
Bot: (直接发送匹配的表情包)
Bot: 42, 无, 吐槽, 加班
或：
找到多个匹配的表情包，请选择：
    1. 当你的老板说今天要加班 -- 12, 无
    2. 加班到凌晨三点的我 -- 23, 小明, 吐槽, 加班
    回复编号即可 (1-2)
你: 2
Bot: (发送对应表情包)
Bot: 23, 小明, 吐槽, 加班
```

### 🤖 AI 描述匹配 `/ai`
```
你: /ai 一张表达心累的加班表情包
Bot: 正在根据你的描述搜索表情包，请稍候...
Bot: (发送最匹配的表情包)
Bot: 42, 无, 吐槽, 加班
```

### ➕ 聊天添加 `/add`
```
授权用户: /add 小明 吐槽 加班
Bot: 请发送图片，60 秒内有效
授权用户: (发送一张图片)
Bot: 新增表情包✅，id：42，识别到的文字为：
「加班心累时的表情包」
Bot: 42, 小明, 吐槽, 加班
```

OCR 识别到的文字会展示给用户，超 50 字时自动截断并标注总长度。

`/add` 后的参数按空白切分，第一个词作为 `speaker`（说话人），剩余词作为 `tags`（标记词）；不填参数时 `speaker` 为空，`tags` 为空列表。文件名始终由 Bot 按 `meme_<YYYYMMDDHHMMSS>_<hash8>` 规则自动生成，不再使用用户输入作为文件名基名。

新增图片会按格式执行无损压缩：`.jpg/.jpeg/.png/.webp/.gif` 会尝试压缩并覆盖原文件，`.bmp` 不压缩。不支持的扩展名不会作为表情包处理。

`/add` 在写入索引前会做两项检查：若新图 OCR 文本去除所有空白后与已有表情包完全相同，则用新图替换旧图（删除旧图片文件、复用旧索引 ID）；若 OCR 结果去除所有空白后为空（无文字图片），则将该图移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引并提示「未识别到文字」。

### 🏷️ 标签添加 `/addtag`

授权用户在私聊中发送 `/addtag <id> <tag> [<tag>...]`，Bot 发送确认消息（包含当前 OCR 文本、当前标签和新增标签），用户回复「确认」或「yes」后执行追加。

```
授权用户: /addtag 42 心累 深夜
Bot: 当前 OCR 文本：加班心累时的表情包
     当前标签：吐槽, 加班
     新增标签：心累, 深夜
     回复「确认」或「yes」确认添加，回复其他内容取消
授权用户: 确认
Bot: 标签已添加 ✅
     本次新增：心累, 深夜
     全部标签：吐槽, 加班, 心累, 深夜
```

### 🗑️ 删除表情包 `/del`

授权用户在私聊中发送 `/del <id>...`，Bot 发送待删除表情包的 OCR 文本摘要，用户回复「确认」或「yes」后执行删除。删除的图片会移动到 `memes_deleted/` 目录备份，可手动恢复。

```
授权用户: /del 12 42
Bot: 确认删除以下表情包？回复「确认」执行删除，回复其他内容取消。
     12, 老板又说加班...
     42, 加班心累时的表情包
授权用户: 确认
Bot: 已删除表情包 ✅
     成功：12、42
```

### ℹ️ 状态信息 `/info`

授权用户在私聊或群聊 @bot 中发送 `/info`，Bot 返回索引统计、当前状态以及本机内存/CPU 占用。

```
授权用户: /info
Bot: 表情包数量：128
     排行（前 3）：
       1. 小明 45
       2. 无 32
       3. 小红 28
     当前机器人状态：就绪
     内存占用：512 MB / 2048 MB (25%)
     CPU占用：12%
```

### ✏️ OCR 文本编辑 `/edittext`

授权用户在私聊中发送 `/edittext <id> <新文本>`，Bot 发送图片和确认消息，
用户回复「确认」后执行修改。修改会同步更新文本索引和向量库。

### 🎤 说话人设置 `/setspeaker`

授权用户在私聊中发送 `/setspeaker <id> [说话人]`，Bot 发送图片和确认消息，
用户回复「确认」或「yes」后执行修改。`[说话人]` 缺省时清空 sqlite 元数据中的说话人字段。

### 🔄 增量更新 `/refresh`
```
授权用户: /refresh
Bot: 正在扫描新图片并更新索引... 🔄
Bot: 索引更新完成 ✅
```

`/refresh` 扫描时同样会对新增图片做去重与无文字排除：OCR 文本去重键命中已有条目或其他新图的新增图片会被删除（保留已有或文件名靠前者），无文字图片移至 `meme_no_text/`；完成回复包含新增、删除、去重、无文字移走、失败五项数量。

`/help`、`/search`、`/ai`、`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/refresh`、`/info`、`/cancel` 使用同一组授权用户白名单。非授权用户的私聊和群聊消息都会被静默忽略。

### 群聊支持
`/search`、`/help`、`/info` 和普通文本支持在群聊中 @bot 触发。`/add`、`/addtag`、`/del`、`/ai`、`/refresh`、`/edittext`、`/setspeaker` 在群聊中 @bot 调用时会回复"此命令仅限私聊使用"。`/cancel` 私聊和群聊均可使用。

## 🚀 快速开始

### 前置条件

- Docker & Docker Compose
- DeepSeek API Key（用于 LLM 精排，[点此获取](https://platform.deepseek.com)）
- Embedding 凭证（默认 `EMBEDDING_PROVIDER=openai`，OpenAI 兼容 API）：任意 OpenAI 兼容 Embedding 服务的 API Key（示例默认使用 [GLM](https://open.bigmodel.cn/)）；仅在切换为 `EMBEDDING_PROVIDER=google` 时才需要 Google AI API Key
- OCR 凭证（三选一）：
  - `OCR_PROVIDER=rapidocr`（默认）：无需 API Key，使用本地 ONNX 模型推理
  - `OCR_PROVIDER=paddle`：百度 PaddleOCR 云 API Access Token（[点此获取](https://aistudio.baidu.com/paddleocr)）
  - `OCR_PROVIDER=deepseek`：OpenAI 兼容视觉 OCR 服务的 API Key（如 SiliconFlow）

### 部署步骤

```bash
# 1. 克隆项目
git clone <your-repo-url> meme-pilot
cd meme-pilot

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env：
#   QQ_ACCOUNT=机器人登录的QQ号
#   AUTHORIZED_USER_IDS=允许使用机器人的QQ号，多个用英文逗号分隔
#   DEEPSEEK_API_KEY=sk-你的DeepSeekKey
#
#   # Embedding：默认使用 OpenAI 兼容 API
#   OPENAI_EMBEDDING_API_KEY=sk-你的EmbeddingKey  # EMBEDDING_PROVIDER=openai（默认）时必填
#   # OPENAI_EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4  # 可选，OpenAI 兼容 Embedding 地址
#   # OPENAI_EMBEDDING_MODEL=embedding-3  # 可选，OpenAI 兼容 Embedding 模型名
#   # EMBEDDING_PROVIDER=openai            # 可选，默认 openai；仅当使用 google 时才改为 google
#   # GOOGLE_API_KEY=你的GoogleKey         # 仅 EMBEDDING_PROVIDER=google 时必填
#   # GOOGLE_EMBEDDING_MODEL=gemini-embedding-001  # 仅 EMBEDDING_PROVIDER=google 时生效
#   # GOOGLE_BASE_URL=                     # 仅 EMBEDDING_PROVIDER=google 时可选
#
#   # OCR：默认使用本地 RapidOCR，无需 API Key
#   # OCR_PROVIDER=rapidocr                # 可选，默认 rapidocr
#   # OCR_TEXT_SCORE=0.9                   # 可选，OCR 文本置信度阈值
#
#   # 仅当 OCR_PROVIDER=paddle 时必填：
#   # PADDLEOCR_ACCESS_TOKEN=你的百度OCRToken
#
#   # 仅当 OCR_PROVIDER=deepseek 时必填：
#   # OPENAI_OCR_API_KEY=sk-你的OpenAI兼容OCRKey
#   # OPENAI_OCR_BASE_URL=https://api.siliconflow.cn/v1  # 可选
#   # OPENAI_OCR_MODEL=deepseek-ai/DeepSeek-OCR          # 可选
#
#   BOT_PORT=8080  # 可选，Bot 监听端口
#   NAPCAT_WEBUI_TOKEN=你的密码  # 可选，WebUI 登录密钥
#   EMBEDDING_CONCURRENCY=5  # 可选，Embedding API 并发上限
#   OCR_CONCURRENCY=5  # 可选，OCR API 并发上限
#   RERANK_CONCURRENCY=5  # 可选，LLM 精排并发上限
#   COMPRESS_CONCURRENCY=5  # 可选，图片压缩并发上限
#   READ_LOCK_TIMEOUT=00:00:30  # 可选，search/ai_match 等待写锁释放的超时
#   ADD_COMMAND_TIMEOUT=00:01:00  # 可选，/add 从提交到结果返回的超时
#   SESSION_EXPIRE_TIMEOUT=00:01:00  # 可选，会话超时时间
#   PADDLEOCR_BASE_URL=https://paddleocr.aistudio-app.com  # 可选，百度 OCR API 地址

# 3. 放入表情包
# 把你的 .jpg/.jpeg/.png/.gif/.webp/.bmp 放到 memes/ 目录

# 4. 启动
docker compose up -d

# 5. 查看日志
docker compose logs -f bot

# 日志同时写入 log/bot.log（滚动日志，单文件 <= 1MB，最多保留 1 个备份 bot.log.1）
# 文件日志级别为 DEBUG，控制台为 INFO
```

首次启动会自动扫描 `memes/` 目录中的图片，按 `OCR_PROVIDER` 配置（默认 `rapidocr`，本地 ONNX 推理）提取文字并建立索引。索引同步在后台执行，Bot 启动后立即可用；同步期间搜索命令会提示"索引更新较慢，请稍后再试"。

### 扫码登录与反向 WebSocket

启动后访问 NapCat WebUI 扫码登录 QQ（Token 在首次启动日志中查看，或在 `.env` 中通过 `NAPCAT_WEBUI_TOKEN` 自定义）：

```text
http://服务器IP:6099/webui?token=<你的Token>
```

v1.0 使用反向 WebSocket：NapCat 通过 `napcat/entrypoint.sh` 自动生成反向 WebSocket 配置，主动连接 Bot 容器（`ws://bot:8080/onebot/v11/ws`），无需手动配置。

如果修改了 `BOT_PORT`，首次启动后需在 NapCat WebUI 中手动更新 WebSocket 地址中的端口号。

### 验证

登录成功后，向你的 QQ 发送 `/search 测试` 试试吧！

## 🏗️ 架构

```
┌──────────────┐  反向 WebSocket   ┌──────────────────────┐
│  NapCatQQ    │ ───────────────►  │  NoneBot2 (Python)   │
│  (协议端)    │    OneBot v11     │  ├ /help  帮助说明  │
│  Docker 容器 │                   │  ├ /search 关键词搜索│
│              │                   │  ├ /ai    AI 描述匹配│
└──────────────┘                   │  ├ /add   聊天添加   │
                                   │  ├ /addtag 添加标签  │
                                   │  ├ /del   删除表情   │
                                   │  ├ /edittext 文本编辑│
                                   │  ├ /setspeaker 说话人│
                                   │  ├ /info  状态信息   │
                                   │  ├ /refresh 增量更新 │
                                   │  └ /cancel 取消命令  │
                                   └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │  本地表情包库          │
                                   │  ├ memes/ 图片文件    │
                                   │  ├ data/index.db      │  sqlite 元数据
                                   │  └ data/chroma/       │  chroma 向量库
                                   └──────────────────────┘
```

OCR 与 Embedding 均通过 `bot/engine/provider_factory.py` 按 `OCR_PROVIDER` / `EMBEDDING_PROVIDER` 配置创建：`OCR_PROVIDER` 可选 `rapidocr`（本地 ONNX 推理，无需联网，默认）、`paddle`（百度云 API）或 `deepseek`（OpenAI 兼容视觉 OCR）；`EMBEDDING_PROVIDER` 可选 `openai`（OpenAI 兼容 API，默认）或 `google`（Google GenAI API）。所有网络请求统一使用 `tenacity` 重试。

### 索引文件（sqlite + chroma）

```sql
-- data/index.db（sqlite3）
CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    image_path TEXT NOT NULL,   -- memes/ 下相对路径
    text TEXT NOT NULL,         -- OCR 去除所有空白后的文本
    speaker TEXT                -- 说话人，v1.0 可通过 /setspeaker 设置
);
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
CREATE UNIQUE INDEX idx_meme_text ON meme(text);
CREATE TABLE meme_tag (
    meme_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (meme_id, tag),
    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
);
```

`data/index.db` 是 sqlite 元数据库，可用 `sqlite3` CLI 查询（如 `sqlite3 data/index.db "SELECT id, image_path, text FROM meme;"`）；`data/chroma/` 是 ChromaDB 向量库（collection `memes`，HNSW cosine），由系统自动维护，不建议手动编辑。OCR 文本在写入前统一去除所有空白字符。

> 从旧版升级时（旧索引为 `data/index.json` + `data/embeddings.json`），先运行 `uv run python scripts/migrate_json_to_db.py` 再启动 Bot。否则旧 `index.json` 不会被读取，首次启动会全量重新 OCR/embed（消耗 API）。迁移成功后旧 `index.json`、`embeddings.json` 可手动删除。

## 📂 项目结构

```
meme-pilot/
├── docker-compose.yml       # 容器编排
├── .env                     # 配置（QQ号、API Key）
├── napcat/                  # NapCat 配置（运行时自动生成）
│   ├── config/              # OneBot v11 + WebUI 配置
│   ├── qq/                  # QQ 登录数据
│   └── entrypoint.sh        # 自动生成反向 WebSocket 配置
├── memes/                   # 放你的表情包图片
├── meme_no_text/            # OCR 无文字图片（不进索引，Docker 卷挂载）
├── memes_deleted/           # 被 /del 删除的表情包备份目录，可手动恢复
├── data/                    # 索引数据
│   ├── index.db             # sqlite 元数据（id、image_path、text、speaker + meme_tag）
│   └── chroma/              # ChromaDB 向量库（collection memes，cosine）
├── log/                     # 日志目录（Docker 卷挂载）
│   ├── bot.log              # 当前日志（<= 1MB）
│   └── bot.log.1            # 上一份日志备份
├── scripts/
│   └── migrate_json_to_db.py # 旧版 JSON 索引 → sqlite+chroma 迁移脚本
├── tests/                   # 测试目录规划
│   ├── unit/                # 单元测试
│   │   ├── engine/
│   │   └── plugins/
│   ├── integration/         # 集成测试
│   └── fixtures/            # 测试样本和基准数据
└── bot/
    ├── Dockerfile
    ├── bot.py               # 入口
    ├── config.py            # 配置读取
    ├── app_state.py         # 共享实例管理（模块级单例）
    ├── auth.py              # 授权校验模块
    ├── session.py           # 共享会话管理（/add、/search 防重复提交）
    ├── logging_config.py    # 日志滚动配置
    ├── plugins/
    │   ├── meme_search.py   # /search 命令
    │   ├── meme_ai.py       # /ai 命令
    │   ├── meme_add.py      # /add 命令
    │   ├── meme_cancel.py   # /cancel 命令
    │   ├── meme_help.py     # /help 命令
    │   ├── meme_plain_text.py # 兜底：普通文本/未知命令
    │   ├── meme_refresh.py  # /refresh 命令
    │   ├── meme_edit.py     # /edittext 命令
    │   ├── meme_setspeaker.py # /setspeaker 命令
    │   ├── _help_text.py    # 帮助文本常量（共享模块）
    │   └── _search_utils.py # 搜索核心逻辑（共享模块）
    └── engine/
        ├── __init__.py          # 包级公共接口导出与 provider 自动注册
        ├── protocols.py         # 共享协议定义（EmbeddingProvider 等）
        ├── provider_factory.py  # OCR/Embedding provider 注册表与工厂函数
        ├── retry_config.py      # 统一 tenacity 网络重试配置
        ├── image_optimizer.py   # 图片无损压缩
        ├── openai_ocr.py        # OpenAI 兼容 OCR 封装（原 deepseek_ocr.py）
        ├── paddle_ocr.py        # PaddleOCR 云 API 封装
        ├── rapidocr_ocr.py      # RapidOCR 本地 ONNX OCR 封装
        ├── openai_embedding.py  # OpenAI 兼容 Embedding 封装（原 embedding_service.py）
        ├── google_embedding.py  # Google Embedding API 封装
        ├── rerank_service.py    # DeepSeek 精排封装（实现 RerankProvider）
        ├── metadata_store.py    # sqlite3 元数据存储（MemeEntry + MetadataStore）
        ├── vector_store.py      # chromadb 向量存储（VectorHit + VectorStore）
        ├── index_manager.py     # 索引薄编排（委托两个 Store）
        ├── keyword_searcher.py  # 模糊搜索
        └── ai_matcher.py        # AI 语义匹配（VectorStore 召回 + 可选精排）
```

## 🧪 测试目录规划

测试文件统一放在仓库根目录 `tests/` 下：

```text
tests/
├── unit/
│   ├── engine/      # 索引、搜索、AI 匹配、图片压缩等单元测试（使用 mock）
│   └── plugins/     # 命令解析、权限判断、回复内容等单元测试
├── integration/     # 集成测试（实际调用 API，需要配置真实 API Key）
├── fixtures/
│   ├── memes/       # 测试表情包图片
│   ├── data/        # 测试索引样本
│   └── images/      # 图片格式和压缩样本
└── conftest.py      # pytest 共享 fixture，添加测试框架后再创建
```

**单元测试 vs 集成测试：**
- `unit/`：使用 mock 隔离外部依赖，快速运行，无需 API Key
- `integration/`：实际调用 API 验证端到端流程，需要配置 `DEEPSEEK_API_KEY` 等环境变量

运行集成测试：
```bash
# 确保已设置 API Key
export DEEPSEEK_API_KEY=sk-your-key
export OPENAI_OCR_API_KEY=sk-your-key

# 运行集成测试（-s 显示输出）
uv run pytest tests/integration/ -v -s
```

## ⚙️ 依赖

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端 (9.4k ⭐)
- [NoneBot2](https://github.com/nonebot/nonebot2) — 聊天机器人框架 (7.5k ⭐)
- [OpenAI 兼容 OCR](https://siliconflow.cn) — 视觉 OCR 模型（默认 `deepseek-ai/DeepSeek-OCR`，可通过 `OPENAI_OCR_MODEL` 切换）
- [DeepSeek](https://platform.deepseek.com) — LLM 精排 API
- [GLM](https://open.bigmodel.cn/) — OpenAI 兼容 Embedding API，默认模型 `embedding-3`
- [Google GenAI](https://aistudio.google.com) — Google Embedding API，模型 `gemini-embedding-001`
- [RapidOCR](https://github.com/RapidAI/RapidOCR) — 本地 ONNX OCR 引擎
- [ChromaDB](https://www.trychroma.com/) — 向量索引（HNSW cosine `PersistentClient`，`data/chroma/`）
- [pylcs](https://github.com/InoriLyude/pylcs) — 最长公共子序列算法库（关键词模糊匹配）
- [Pillow](https://python-pillow.org/) — 图片无损压缩（支持 `.jpg/.jpeg/.png/.webp/.gif`，`.bmp` 跳过）
- [tenacity](https://github.com/jd/tenacity) — 统一网络请求重试机制
- [psutil](https://github.com/giampaolo/psutil) — 系统资源监控（用于 `/info`）

## 📄 许可

MIT
