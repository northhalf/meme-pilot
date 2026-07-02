# MemePilot

MemePilot 是一个部署在 Docker 中的 QQ 表情包机器人，帮你从本地表情包库中快速找到想要的表情包。/search、/help 和普通文本支持群聊 @bot 使用；/add、/refresh、/ai 仅限私聊。/cancel 私聊和群聊均可使用。

隐私说明：表情包图片始终本地存储；OCR 文本会按 `OCR_PROVIDER` 配置发送给对应服务（默认 `paddle` 时使用百度 PaddleOCR 云 API，`deepseek` 时使用 SiliconFlow DeepSeek-OCR）；Embedding 调用由 `EMBEDDING_API_KEY` 指定的服务；LLM 精排调用 DeepSeek。

## ✨ 功能

### 🧭 帮助 `/help`
```
你: /help
Bot: 当前可用命令：
     /help：查看命令帮助
     /search <关键词>：按 OCR 文本关键词搜索表情包
     /ai <自然语言描述>：按自然语言描述匹配表情包
     /add [目标命名]：通过聊天添加一张表情包
     /refresh：扫描 memes/ 并增量更新索引
     /cancel：取消当前正在执行的命令
```

授权用户可以直接发送 `/search <关键词>` 搜索表情包，或在群聊中 @bot + `/search <关键词>` 触发。授权用户发送普通文本时（私聊或群聊 @bot），Bot 默认当作 `/search` 执行搜索。发送未知斜杠命令时，Bot 会提示"未知命令"并附帮助摘要。

### 🔍 关键词搜索 `/search`
```
你: /search 加班
Bot: (直接发送匹配的表情包)
或: 找到多个匹配的表情包，请选择：
    1. 当你的老板说今天要加班
    2. 加班到凌晨三点的我
    回复编号即可 (1-2)，（默认 60 秒内有效，由 SESSION_EXPIRE_TIMEOUT 控制）
你: 2
Bot: (发送对应表情包)
```

### 🤖 AI 描述匹配 `/ai`
```
你: /ai 一张表达心累的加班表情包
Bot: 正在根据你的描述搜索表情包，请稍候...
Bot: (发送最匹配的表情包)
```

### ➕ 聊天添加 `/add`
```
授权用户: /add 加班心累
Bot: 请发送图片，{SESSION_EXPIRE_TIMEOUT} 秒内有效
授权用户: (发送一张图片)
Bot: 新增表情包✅，识别到的文字为：加班心累时的表情包
```

OCR 识别到的文字会展示给用户，超 50 字时自动截断并标注总长度。

`/add` 中的目标命名会作为保存到 `memes/` 的文件名基名；搜索文本仍来自 OCR 结果。目标命名会被安全化：路径分隔符、不安全字符和空白会替换为 `_`。如果只发送 `/add` 不带命名，Bot 会根据发送时间和图片内容 hash 自动生成文件名。

新增图片会按格式执行无损压缩：`.jpg/.jpeg/.png/.webp/.gif` 会尝试压缩并覆盖原文件，`.bmp` 不压缩。不支持的扩展名不会作为表情包处理。

`/add` 在写入索引前会做两项检查：若新图 OCR 文本去除所有空白后与已有表情包完全相同，则用新图替换旧图（删除旧图片文件、复用旧索引 ID）；若 OCR 结果去除所有空白后为空（无文字图片），则将该图移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引并提示「未识别到文字」。

### 🔄 增量更新 `/refresh`
```
授权用户: /refresh
Bot: 正在扫描新图片并更新索引... 🔄
Bot: 索引更新完成 ✅
```

`/refresh` 扫描时同样会对新增图片做去重与无文字排除：OCR 文本去重键命中已有条目或其他新图的新增图片会被删除（保留已有或文件名靠前者），无文字图片移至 `meme_no_text/`；完成回复包含新增、删除、去重、无文字移走、失败五项数量。

`/help`、`/search`、`/ai`、`/add`、`/refresh`、`/cancel` 使用同一组授权用户白名单。非授权用户的私聊和群聊消息都会被静默忽略。

### 群聊支持
`/search`、`/help` 和普通文本支持在群聊中 @bot 触发。`/add`、`/ai`、`/refresh` 在群聊中 @bot 调用时会回复"此命令仅限私聊使用"。`/cancel` 私聊和群聊均可使用。

## 🚀 快速开始

### 前置条件

- Docker & Docker Compose
- DeepSeek API Key（用于 LLM 精排，[点此获取](https://platform.deepseek.com)）
- Embedding API Key（任意 OpenAI 兼容服务，默认配置使用 SiliconFlow，[点此获取](https://siliconflow.cn)）
- OCR 凭证（二选一）：
  - `OCR_PROVIDER=paddle`（默认）：百度 PaddleOCR 云 API Access Token（[点此获取](https://ai.baidu.com/tech/ocr/general)）
  - `OCR_PROVIDER=deepseek`：SiliconFlow API Key（与 DeepSeek-OCR 共用同一账户）

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
#   EMBEDDING_API_KEY=sk-你的EmbeddingKey
#   PADDLEOCR_ACCESS_TOKEN=你的百度OCRToken  # 当 OCR_PROVIDER=paddle（默认）时必填
#   SILICONFLOW_API_KEY=sk-你的SiliconFlowKey  # 当 OCR_PROVIDER=deepseek 时必填
#   BOT_PORT=8080  # 可选，Bot 监听端口
#   NAPCAT_WEBUI_TOKEN=你的密码  # 可选，WebUI 登录密钥
#   SYNC_CONCURRENCY=5  # 可选，索引同步并发上限
#   READ_LOCK_TIMEOUT=00:00:30  # 可选，search/ai_match 等待写锁释放的超时
#   ADD_COMMAND_TIMEOUT=00:01:00  # 可选，/add 从提交到结果返回的超时
#   SESSION_EXPIRE_TIMEOUT=00:01:00  # 可选，会话超时时间
#   OCR_PROVIDER=paddle  # 可选，OCR 引擎：paddle（默认，需 PADDLEOCR_ACCESS_TOKEN）或 deepseek（需 SILICONFLOW_API_KEY）
#   PADDLEOCR_BASE_URL=https://aip.baidubce.com  # 可选，百度 OCR API 地址

# 3. 放入表情包
# 把你的 .jpg/.jpeg/.png/.gif/.webp/.bmp 放到 memes/ 目录

# 4. 启动
docker compose up -d

# 5. 查看日志
docker compose logs -f bot

# 日志同时写入 log/bot.log（滚动日志，单文件 <= 1MB，最多保留 1 个备份 bot.log.1）
# 文件日志级别为 DEBUG，控制台为 INFO
```

首次启动会自动扫描 `memes/` 目录中的图片，按 `OCR_PROVIDER` 配置（默认 `paddle`）提取文字并建立索引。索引同步在后台执行，Bot 启动后立即可用；同步期间搜索命令会提示"索引更新较慢，请稍后再试"。

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

### 索引文件（sqlite + chroma）

```sql
-- data/index.db（sqlite3）
CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    image_path TEXT NOT NULL,   -- memes/ 下相对路径
    text TEXT NOT NULL,         -- OCR 去除所有空白后的文本
    speaker TEXT                -- 说话人，v1.0 预留
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
    │   ├── _help_text.py    # 帮助文本常量（共享模块）
    │   └── _search_utils.py # 搜索核心逻辑（共享模块）
    └── engine/
        ├── __init__.py          # 包级公共接口导出
        ├── protocols.py         # 共享协议定义（EmbeddingProvider 等）
        ├── image_optimizer.py   # 图片无损压缩
        ├── deepseek_ocr.py       # DeepSeek-OCR 封装（硅基流动 API）
        ├── paddle_ocr.py         # PaddleOCR 云 API 封装
        ├── embedding_service.py # SiliconFlow Embedding 封装（实现 EmbeddingProvider）
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
export SILICONFLOW_API_KEY=sk-your-key

# 运行集成测试（-s 显示输出）
uv run pytest tests/integration/ -v -s
```

## ⚙️ 依赖

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端 (9.4k ⭐)
- [NoneBot2](https://github.com/nonebot/nonebot2) — 聊天机器人框架 (7.5k ⭐)
- [DeepSeek-OCR](https://siliconflow.cn) — 视觉 OCR 模型（硅基流动）
- [DeepSeek](https://platform.deepseek.com) — LLM 精排 API
- [SiliconFlow](https://siliconflow.cn) — Embedding API，默认模型 `BAAI/bge-m3`
- [ChromaDB](https://www.trychroma.com/) — 向量索引（HNSW cosine `PersistentClient`，`data/chroma/`）
- [pylcs](https://github.com/InoriLyude/pylcs) — 最长公共子序列算法库（关键词模糊匹配）
- [Pillow](https://python-pillow.org/) — 图片无损压缩（支持 `.jpg/.jpeg/.png/.webp/.gif`，`.bmp` 跳过）

## 📄 许可

MIT
