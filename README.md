<div align="center">
  <div style="width:200px">
    <a href="https://github.com/northhalf/meme-pilot">
      <img src="assets/icon.webp" alt="MemePilot" width="200">
    </a>
  </div>

<h1>MemePilot</h1>

![Status](https://img.shields.io/badge/status-active-brightgreen) ![Stage](https://img.shields.io/badge/stage-beta-blue) ![Build Status](https://github.com/northhalf/meme-pilot/actions/workflows/ci.yml/badge.svg) ![Docker Pulls](https://img.shields.io/docker/pulls/northhalf/meme-pilot) ![License](https://img.shields.io/badge/license-MIT-blue)

<h5>一个部署在 Docker 中的 QQ 表情包机器人，帮你从本地表情包库中快速找到想要的表情包。</h5>

</div>

## ✨ 功能

### 🧭 帮助 `/help`
```
你: /help
Bot: 当前可用命令：
     /help (/h)：查看命令帮助
     直接发送关键词：按关键词检索表情包（结果过多时支持翻页）
     /query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足；结果过多时支持翻页）
     /rand [关键词]：随机给出 10 个表情包，回复 0 换一批
     /sim <描述文本>：按语义相似度给出前 10 个表情包（结果过多时支持翻页）
     /add [speaker <tags...>] (/a)：通过聊天添加一张表情包
     /addtag <公开ID> <tag> [<tag>...] (/at)：为指定表情包添加标签
     /del <公开ID>... (/d)：删除指定表情包（需确认）
     /edittext <公开ID> <新文本> (/e)：修改指定表情包的 OCR 文本
     /setspeaker <公开ID> [说话人] (/sp)：设置或清空表情包的说话人
     /collection create <名称>：创建表情包合集
     /collection delete <编号|名称>：删除空合集
     /collection rename <旧编号|名称> <新名称>：重命名合集
     /switch [合集编号|名称]：查看或切换表情包合集
     /mv <公开ID> <目标合集编号|名称>：移动表情包（需确认）
     /refresh (/r)：扫描 memes/ 并增量更新索引
     /info [公开ID]：查看机器人状态与统计信息，或查看指定表情包详情
     /cancel (/c)：取消当前正在执行的命令
```

授权用户可以直接发送 `/query <关键词>` 等命令搜索表情包，或在群聊中 @bot + 命令触发。授权用户发送普通文本时（私聊或群聊 @bot），Bot 默认按关键词搜索执行兜底搜索。发送未知斜杠命令时，Bot 会提示"未知命令"并附帮助摘要。


### 群聊支持
`/query`、`/rand`、`/sim`、`/help`、`/info`、`/switch` 和普通文本支持在群聊中 @bot 触发。`/collection create`、`/collection delete`、`/collection rename`、`/add`、`/addtag`、`/del`、`/refresh`、`/edittext`、`/setspeaker`、`/mv` 在群聊中 @bot 调用时会回复"此命令仅限私聊使用"。`/cancel` 私聊和群聊均可使用。

## 🚀 部署与使用

支持两种部署方式：使用部署脚本拉取运行时文件后以预构建镜像启动（推荐，无需克隆源码），或克隆仓库本地构建镜像。两种方式都需先配置 `.env`（见 [环境变量配置](#环境变量配置)）并把表情包放入 `memes/`（见 [放入表情包](#放入表情包)）。

### 前置条件

- Docker & Docker Compose
- Embedding 凭证：Bot 运行时只使用 `EMBEDDING_PROVIDER` 选中的凭证。默认 `openai` 需要任意 OpenAI 兼容 Embedding 服务的 API Key（示例使用 [GLM](https://open.bigmodel.cn/)）；切换为 `google` 时需要 Google AI API Key。当前 Compose 模板仍要求 `OPENAI_EMBEDDING_API_KEY` 非空，使用 Google 时可保留非空占位值，Bot 不会调用该值。
- OCR 凭证（四选一）：
  - `OCR_PROVIDER=rapidocr`（默认）：无需 API Key，使用本地 ONNX 模型推理
  - `OCR_PROVIDER=paddle`：百度 PaddleOCR 云 API Access Token（[点此获取](https://aistudio.baidu.com/paddleocr)）
  - `OCR_PROVIDER=deepseek`：OpenAI 兼容视觉 OCR 服务的 API Key（如 SiliconFlow）
  - `OCR_PROVIDER=baidu`：百度智能云 API Key 与 Secret Key，支持 PP-OCRv6 及六种传统通用 OCR 接口

### 部署方式

<details>
<summary><strong>方式一：部署脚本 + Docker Compose（预构建镜像，推荐）</strong></summary>

通过部署脚本从 GitHub 拉取运行时文件（`napcat/`、`docker-compose.yml`、`.env`），再以预构建镜像 `northhalf/meme-pilot:latest` 启动，无需克隆源码。脚本幂等，已存在的文件会跳过（`.env` 永不覆盖），可安全重复执行。

**Linux / macOS / WSL（一键命令）**

```bash
curl -fsSL https://raw.githubusercontent.com/northhalf/meme-pilot/main/deploy/deploy.sh | bash
```

也可先下载再执行，以便指定目标目录或仓库引用：

```bash
curl -fsSL https://raw.githubusercontent.com/northhalf/meme-pilot/main/deploy/deploy.sh -o deploy.sh
chmod +x deploy.sh
./deploy.sh [目标目录]            # 默认当前目录
REPO_REF=v1.0.0 ./deploy.sh      # 指定仓库引用，默认 main
```

**Windows PowerShell（需管理员）**

从网络下载的脚本会被 Windows 标记，需先放行执行策略再解除锁定，然后在目标目录中执行：

```powershell
# 以管理员身份打开 PowerShell，在目标目录中执行：
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
Invoke-WebRequest -Uri https://raw.githubusercontent.com/northhalf/meme-pilot/main/deploy/deploy.ps1 -OutFile deploy.ps1
Unblock-File .\deploy.ps1
.\deploy.ps1 [-TargetDir <目录>]    # 默认当前目录；$env:REPO_REF="v1.0.0" 可指定引用
```

**完成拉取后**

1. 编辑 `.env`，填入 `QQ_ACCOUNT`、`AUTHORIZED_USER_IDS` 与所选 provider 凭证（详见 [环境变量配置](#环境变量配置)）。
2. 把表情包放入 `memes/`（详见 [放入表情包](#放入表情包)）。
3. 启动并查看日志：

```bash
docker compose up -d
docker compose logs -f bot
```

默认 Compose 使用 `northhalf/meme-pilot:latest`，并通过 `pull_policy: always` 在每次启动时检查并拉取最新发布镜像。

</details>

<details>
<summary><strong>方式二：克隆仓库 + 本地构建镜像</strong></summary>

不使用预构建镜像时，克隆仓库并用 `docker-compose.build.yml` 本地构建 `meme-pilot:local` 镜像。

```bash
# 1. 克隆项目
git clone https://github.com/northhalf/meme-pilot.git meme-pilot
cd meme-pilot

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env：QQ_ACCOUNT、AUTHORIZED_USER_IDS、Embedding/OCR 凭证（详见 环境变量配置）

# 3. 放入表情包（详见 放入表情包）

# 4. 本地构建并启动
docker compose -f docker-compose.build.yml up -d --build

# 5. 查看日志
docker compose logs -f bot
```

**构建代理（可选）**

国内网络构建镜像时若需走代理，按以下单一流程处理覆盖文件：旧版 `docker-compose.override.yml` 存在时先迁移；否则在新文件不存在时从示例创建。无论采用哪种来源，最后都显式组合两个 Compose 文件启动：

```bash
if [ -f docker-compose.override.yml ]; then
  mv docker-compose.override.yml docker-compose.build.override.yml
elif [ ! -f docker-compose.build.override.yml ]; then
  cp docker-compose.build.override.yml.example docker-compose.build.override.yml
fi

# 按需编辑 docker-compose.build.override.yml 中的代理地址
docker compose \
  -f docker-compose.build.yml \
  -f docker-compose.build.override.yml \
  up -d --build
```

这样旧文件不会再被默认 `docker compose up -d` 自动加载，本地构建覆盖也不会影响默认镜像部署。

</details>

> 启动后日志同时写入 `log/bot.log`（滚动日志，单文件 <= 10MB，最多保留 3 个备份 `bot.log.1~3`）；文件日志级别为 DEBUG，控制台为 INFO。完整说明见 [日志格式与追踪](#日志格式与追踪)。

### 环境变量配置

部署完成后编辑 `.env`，必填项与可选项说明如下（完整模板见 `.env.example`）：

```bash
QQ_ACCOUNT=机器人登录的QQ号
AUTHORIZED_USER_IDS=允许使用机器人的QQ号，多个用英文逗号分隔

# Embedding：默认使用 OpenAI 兼容 API
OPENAI_EMBEDDING_API_KEY=sk-你的EmbeddingKey  # Compose 要求非空；google 模式可填 unused-for-google
# OPENAI_EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4  # 可选，OpenAI 兼容 Embedding 地址
# OPENAI_EMBEDDING_MODEL=embedding-3  # 可选，OpenAI 兼容 Embedding 模型名
# EMBEDDING_PROVIDER=openai            # 可选，默认 openai；仅当使用 google 时才改为 google
# GOOGLE_API_KEY=你的GoogleKey         # 仅 EMBEDDING_PROVIDER=google 时必填
# GOOGLE_EMBEDDING_MODEL=gemini-embedding-001  # 仅 EMBEDDING_PROVIDER=google 时生效
# GOOGLE_BASE_URL=                     # 仅 EMBEDDING_PROVIDER=google 时可选

# OCR：默认使用本地 RapidOCR，无需 API Key
# OCR_PROVIDER=rapidocr                # 可选：rapidocr / paddle / deepseek / baidu
# OCR_TEXT_SCORE=0.9                   # 可选，OCR 文本置信度阈值

# 仅当 OCR_PROVIDER=paddle 时必填：
# PADDLEOCR_ACCESS_TOKEN=你的百度OCRToken

# 仅当 OCR_PROVIDER=baidu 时必填：
# BAIDU_API_KEY=你的百度智能云APIKey
# BAIDU_SECRET_KEY=你的百度智能云SecretKey
# BAIDU_OCR_TYPE=pp_ocrv6             # 可选，默认 pp_ocrv6
# PP-OCRv6 当前通过百度官方兼容路径 /rest/2.0/ocr/v1/pp_ocrv5 调用
# 可选模式：pp_ocrv6 / general_basic / general / accurate_basic / accurate / webimage / webimage_loc

# 仅当 OCR_PROVIDER=deepseek 时必填：
# OPENAI_OCR_API_KEY=sk-你的OpenAI兼容OCRKey
# OPENAI_OCR_BASE_URL=https://api.siliconflow.cn/v1  # 可选
# OPENAI_OCR_MODEL=deepseek-ai/DeepSeek-OCR          # 可选

BOT_PORT=8080  # 可选，Bot 监听端口
NAPCAT_WEBUI_TOKEN=你的密码  # 可选，WebUI 登录密钥
EMBEDDING_CONCURRENCY=5  # 可选，Embedding API 并发上限
OCR_CONCURRENCY=5  # 可选，OCR API 并发上限
COMPRESS_CONCURRENCY=5  # 可选，图片压缩并发上限
CONVERT_TO_WEBP=true  # 可选，图片转 WebP 开关（默认开启）

# 内存治理（默认值已内置，按需覆盖；详见 .env.example）
LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libmimalloc.so.3  # 可选，用 mimalloc 接管 malloc，治容器 RSS 持续上涨
ORT_INTRA_OP_NUM_THREADS=2  # 可选，onnxruntime 推理线程（原默认占满 CPU 核）
ORT_INTER_OP_NUM_THREADS=1  # 可选，onnxruntime inter-op 线程
READ_LOCK_TIMEOUT=00:00:30  # 可选，搜索命令等待读锁的超时
ADD_COMMAND_TIMEOUT=00:01:00  # 可选，/add 从提交到结果返回的超时
SESSION_EXPIRE_TIMEOUT=00:01:00  # 可选，会话超时时间
PADDLEOCR_BASE_URL=https://paddleocr.aistudio-app.com  # 可选，百度 PaddleOCR API 地址
```

使用百度智能云 OCR 时，可在源码仓库中手动调用真实 API 验证配置。该脚本不属于 pytest/CI 测试：

```bash
# 默认验证 PP-OCRv6
uv run python -m scripts.test_baidu_ocr image.png

# 验证指定模式
uv run python -m scripts.test_baidu_ocr image.png --type webimage

# 顺序验证全部七种模式（消耗 7 次 OCR 额度）
uv run python -m scripts.test_baidu_ocr image.png --all
```

### 放入表情包

把你的 `.jpg`/`.jpeg`/`.png`/`.gif`/`.webp`/`.bmp` 放到 `memes/` 目录。`memes/` 支持根目录直接存放（归属“全局”，公开 ID `0.x`），也支持用一级目录作为表情包合集，目录内可继续使用子目录：

```text
memes/
├── root.webp              # 全局，公开 ID 0.1
├── 新三国/
│   ├── a.webp             # 新三国，公开 ID 1.1
│   └── 截图/
│       └── b.webp         # 新三国，公开 ID 1.2
└── 甄嬛传/
    └── c.webp             # 甄嬛传，公开 ID 2.1
```

### 日志格式与追踪

- 所有 `bot.*` 模块的日志统一输出到 `log/bot.log`（DEBUG 及以上）和容器标准输出（INFO 及以上）。
- 每条用户命令会生成一个 8 位短 `request_id`，以 `[req:xxxxxxx]` 前缀贯穿插件、engine、OCR、Embedding、Store 等全链路日志，方便定位一次请求的完整调用路径。
- 关键操作（OCR、Embedding、搜索、索引刷新、图片优化、Store 批量写入等）会自动记录耗时：`<操作> 完成/失败，耗时 x.xx ms`，默认写入 DEBUG 级别日志。
- 如需调整某个模块的日志级别，可在 `bot.py` 启动后通过标准库 `logging` 设置，例如：
  ```python
  logging.getLogger("bot.engine.index_manager").setLevel(logging.WARNING)
  ```

首次启动会自动扫描 `memes/` 目录中的图片，按 `OCR_PROVIDER` 配置（默认 `rapidocr`，本地 ONNX 推理）提取文字并建立索引。索引同步在后台执行，Bot 启动后立即可用；同步期间搜索命令会提示"索引更新较慢，请稍后再试"。

### 扫码登录与反向 WebSocket

启动后访问 NapCat WebUI 扫码登录 QQ（Token 在首次启动日志中查看，或在 `.env` 中通过 `NAPCAT_WEBUI_TOKEN` 自定义）：

```text
http://127.0.0.1:6099/webui?token=<你的Token>
```

WebUI 端口默认仅绑定到宿主机回环地址（`127.0.0.1:6099`），不对外网暴露；远程管理请用 SSH 端口转发：

```bash
ssh -L 6099:127.0.0.1:6099 用户@服务器
```

v1.0 使用反向 WebSocket：NapCat 通过 `napcat/entrypoint.sh` 自动生成反向 WebSocket 配置，主动连接 Bot 容器（`ws://bot:8080/onebot/v11/ws`），无需手动配置。

如果修改了 `BOT_PORT`，首次启动后需在 NapCat WebUI 中手动更新 WebSocket 地址中的端口号。

## 🏗️ 架构

```
┌──────────────┐  反向 WebSocket   ┌──────────────────────┐
│  NapCatQQ    │ ───────────────►  │  NoneBot2 (Python)   │
│  (协议端)    │    OneBot v11     │  ├ /help  帮助说明  │
│  Docker 容器 │                   │  ├ /query 组合检索  │
│              │                   │  ├ /rand  随机选择  │
│              │                   │  ├ /sim   语义选择  │
└──────────────┘                   │  ├ /collection 管理  │
                                   │  ├ /add   聊天添加   │
                                   │  ├ /addtag 添加标签  │
                                   │  ├ /del   删除表情   │
                                   │  ├ /edittext 文本编辑│
                                   │  ├ /setspeaker 说话人│
                                   │  ├ /switch 切换合集  │
                                   │  ├ /mv    跨合集移动 │
                                   │  ├ /info  状态信息   │
                                   │  ├ /refresh 增量更新 │
                                   │  └ /cancel 取消命令  │
                                   └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │  本地表情包库          │
                                   │  ├ memes/ 图片文件    │  根目录=全局，一级目录=合集
                                   │  ├ memes_deleted/     │  被删除备份
                                   │  ├ memes_replaced/    │  被替换归档
                                   │  ├ data/index.db      │  sqlite 元数据（含合集表）
                                   │  └ data/chroma/       │  chroma 向量库（含 collection_id）
                                   └──────────────────────┘
```

OCR 与 Embedding 均通过 `bot/engine/provider_factory.py` 按 `OCR_PROVIDER` / `EMBEDDING_PROVIDER` 配置创建：`OCR_PROVIDER` 可选 `rapidocr`（本地 ONNX 推理，无需联网，默认）、`paddle`（PaddleOCR 云 API）、`deepseek`（OpenAI 兼容视觉 OCR）或 `baidu`（百度智能云 OCR REST API，支持 PP-OCRv6 及六种传统通用接口）；`EMBEDDING_PROVIDER` 可选 `openai`（OpenAI 兼容 API，默认）或 `google`（Google GenAI API）。网络 provider 使用 `tenacity` 执行分类重试。

### 索引文件（sqlite + chroma）

```sql
-- data/index.db（sqlite3）
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE meme_collection (
    id INTEGER PRIMARY KEY CHECK (id > 0),
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL CHECK (collection_id >= 0),  -- 0=全局根目录，正整数=普通合集
    local_id INTEGER NOT NULL CHECK (local_id > 0),             -- 合集内编号
    image_path TEXT NOT NULL,   -- memes/ 下相对路径（根目录文件名或合集内嵌套路径）
    text TEXT NOT NULL,         -- OCR 去除所有空白后的文本
    speaker TEXT                -- 说话人
);
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
CREATE UNIQUE INDEX idx_meme_collection_local ON meme(collection_id, local_id);
CREATE UNIQUE INDEX idx_meme_collection_text ON meme(collection_id, text);

CREATE TABLE meme_tag (
    meme_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (meme_id, tag),
    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
);

CREATE TABLE chat_collection_scope (
    user_id INTEGER NOT NULL,
    chat_type TEXT NOT NULL CHECK (chat_type IN ('private', 'group')),
    chat_id INTEGER NOT NULL,
    selected_collection_id INTEGER NOT NULL CHECK (selected_collection_id >= 0),
    PRIMARY KEY (user_id, chat_type, chat_id)
);
```

`data/index.db` 是 sqlite 元数据库，可用 `sqlite3` CLI 查询（如 `sqlite3 data/index.db "SELECT id, collection_id, local_id, image_path, text FROM meme;"`）；`data/chroma/` 是 ChromaDB 向量库（collection `memes`，HNSW cosine），每条向量附带 `collection_id` 元数据用于按合集过滤召回，由系统自动维护，不建议手动编辑。OCR 文本在写入前统一去除所有空白字符。

## ⚙️ 依赖

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端 (9.4k ⭐)
- [NoneBot2](https://github.com/nonebot/nonebot2) — 聊天机器人框架 (7.5k ⭐)

## 📄 许可

MIT
