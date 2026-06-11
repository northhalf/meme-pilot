# MemePilot

MemePilot 是一个部署在 Docker 中的 QQ 私聊表情包机器人，帮你从本地表情包库中快速找到想要的表情包。

隐私说明：表情包图片始终本地存储；OCR 文本会按功能需要发送给 SiliconFlow 和 DeepSeek。

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
```

授权用户在私聊中直接发送普通文本时，Bot 会默认返回帮助。发送未知斜杠命令时，Bot 会提示“未知命令”并附帮助摘要。

### 🔍 关键词搜索 `/search`
```
你: /search 加班
Bot: (直接发送匹配的表情包)
或: 找到多个匹配的表情包，请选择：
    1. 当你的老板说今天要加班
    2. 加班到凌晨三点的我
    回复编号即可 (1-2)，60 秒内有效
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
Bot: 请发送图片，60 秒内有效
授权用户: (发送一张图片)
Bot: 已成功添加表情包 ✅
```

`/add` 中的目标命名会作为保存到 `memes/` 的文件名基名；搜索文本仍来自 OCR 结果。目标命名会被安全化：路径分隔符、不安全字符和空白会替换为 `_`。如果只发送 `/add` 不带命名，Bot 会根据发送时间和图片内容 hash 自动生成文件名。

新增图片会按格式执行无损压缩：`.jpg/.jpeg/.png/.webp/.gif` 会尝试压缩并覆盖原文件，`.bmp` 不压缩。不支持的扩展名不会作为表情包处理。

### 🔄 增量更新 `/refresh`
```
授权用户: /refresh
Bot: 正在扫描新图片并更新索引... 🔄
Bot: 索引更新完成 ✅
```

`/help`、`/search`、`/ai`、`/add`、`/refresh` 使用同一组授权用户白名单。非授权用户的私聊消息会被静默忽略。群聊不在 v1.0 范围内，所有群聊消息都会被静默忽略。

## 🚀 快速开始

### 前置条件

- Docker & Docker Compose
- DeepSeek API Key（用于 LLM 精排，[点此获取](https://platform.deepseek.com)）
- SiliconFlow API Key（用于生成 embedding，[点此获取](https://siliconflow.cn)）

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
#   SILICONFLOW_API_KEY=sk-你的SiliconFlowKey
#   BOT_PORT=8080  # 可选，Bot 监听端口

# 3. 放入表情包
# 把你的 .jpg/.jpeg/.png/.gif/.webp/.bmp 放到 memes/ 目录

# 4. 启动
docker compose up -d

# 5. 查看日志
docker compose logs -f bot
```

首次启动会自动扫描 `memes/` 目录中的图片，用 PaddleOCR 提取文字并建立索引。

### 扫码登录与反向 WebSocket

启动后访问 `http://服务器IP:6099`，用 NapCat WebUI 扫码登录 QQ。

v1.0 使用反向 WebSocket：NapCat 主动连接 Bot。NapCat 侧反向 WebSocket 地址配置为：

```text
ws://bot:${BOT_PORT}/onebot/v11/ws
```

如果在 NapCat WebUI 中手动填写，请将 `${BOT_PORT}` 替换为实际端口，例如默认端口为 `8080` 时填写：

```text
ws://bot:8080/onebot/v11/ws
```

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
                                   │  └ /refresh 增量更新 │
                                   └──────────┬───────────┘
                                              │
                                   ┌──────────▼───────────┐
                                   │  本地表情包库          │
                                   │  ├ memes/ 图片文件    │
                                   │  ├ data/index.json      │
                                   │  └ data/embeddings.json │
                                   └──────────────────────┘
```

### 索引文件（JSON，可直接查看和编辑）

```json
// data/index.json
{
  "version": 1,
  "entries": {
    "001": {
      "filename": "cat_jump.jpg",
      "text": "一只猫抓蝴蝶 哈哈哈",
      "text_hash": "sha256:..."
    }
  }
}
```

`index.json` 是用户可维护的主索引，可以手动修正 OCR 文本；`embeddings.json` 是由系统生成的向量索引，不建议手动编辑。修改 `index.json` 中的 `text` 后，系统会在启动或 `/refresh` 时自动更新 `text_hash` 并重建对应 embedding。

## 📂 项目结构

```
meme-pilot/
├── docker-compose.yml       # 容器编排
├── .env                     # 配置（QQ号、API Key）
├── memes/                   # 放你的表情包图片
├── data/                    # 索引数据
│   ├── index.json
│   └── embeddings.json
├── tests/                   # 测试目录规划
│   ├── unit/                # 单元测试
│   │   ├── engine/
│   │   └── plugins/
│   ├── integration/         # 集成测试
│   └── fixtures/            # 测试样本和基准数据
└── bot/
    ├── Dockerfile
    ├── requirements.txt
    ├── bot.py               # 入口
    ├── config.py            # 配置读取
    ├── plugins/
    │   ├── meme_search.py   # /search 命令
    │   ├── meme_ai.py       # /ai 命令
    │   ├── meme_add.py      # /add 命令
    │   ├── meme_help.py     # /help 命令
    │   └── meme_refresh.py  # /refresh 命令
    └── engine/
        ├── image_optimizer.py   # 图片无损压缩
        ├── ocr_service.py       # PaddleOCR
        ├── index_manager.py     # 索引管理
        ├── keyword_searcher.py  # 模糊搜索
        └── ai_matcher.py        # DeepSeek AI 匹配
```

## 🧪 测试目录规划

测试文件统一放在仓库根目录 `tests/` 下：

```text
tests/
├── unit/
│   ├── engine/      # 索引、搜索、AI 匹配、图片压缩等单元测试
│   └── plugins/     # 命令解析、权限判断、回复内容等单元测试
├── integration/     # 跨模块流程测试
├── fixtures/
│   ├── memes/       # 测试表情包图片
│   ├── data/        # 测试索引样本
│   └── images/      # 图片格式和压缩样本
└── conftest.py      # pytest 共享 fixture，添加测试框架后再创建
```

当前只规划目录结构，尚未引入 pytest 或固定测试命令。

## ⚙️ 依赖

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端 (9.4k ⭐)
- [NoneBot2](https://github.com/nonebot/nonebot2) — 聊天机器人框架 (7.5k ⭐)
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — OCR 引擎
- [DeepSeek](https://platform.deepseek.com) — LLM 精排 API
- [SiliconFlow](https://siliconflow.cn) — Embedding API，默认模型 `Qwen/Qwen3-Embedding-8B`
- [rapidfuzz](https://github.com/maxbachmann/rapidfuzz) — 模糊字符串匹配
- 图片无损压缩工具/库 — 实现阶段选择具体方案，需求要求支持 `.jpg/.jpeg/.png/.webp/.gif`，`.bmp` 跳过压缩

## 📄 许可

MIT
