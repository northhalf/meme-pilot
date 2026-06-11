# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 必读文档

- 修改需求、架构、命令交互、索引格式或权限逻辑前，必须先查看 `docs/PRD.md`。
- 修改术语、领域概念或用户可见命名时，必须查看 `CONTEXT.md` 并保持术语一致。
- 修改部署、环境变量或用户操作说明时，同时检查 `README.md`、`.env.example` 和 `docker-compose.yml`。

## 代码风格要求

- Python 函数需要使用 Google 风格 docstring。
- Python 函数 docstring 内容使用中文。
- Python 变量、参数和返回值需要进行类型标注。
- 写代码时保持现有中文注释和中文用户提示风格。

示例：

```python
def normalize_text(text: str) -> str:
    """规范化 OCR 文本。

    Args:
        text: 原始 OCR 文本。

    Returns:
        去除首尾空白并合并连续空白后的文本。
    """
```

## 常用命令

### Docker 部署

```bash
docker compose up -d
```

```bash
docker compose logs -f bot
```

```bash
docker compose down
```

### 重新构建 bot 镜像

```bash
docker compose build bot
```

```bash
docker compose up -d bot
```

### 本地安装 Python 依赖

项目 Python 依赖在 `bot/requirements.txt`。本地开发优先使用 `uv`：

```bash
uv venv
uv pip install -r bot/requirements.txt
```

如环境尚未安装 `uv`，先安装：

```bash
pip install uv
```

用途：`uv` 用于创建虚拟环境并安装 Python 依赖。

### 本地运行 Bot

```bash
uv run --directory bot python bot.py
```

注意：当前文档以 Docker Compose 运行为主。本地运行时要确认 `memes/`、`data/`、环境变量和 OneBot 反向 WebSocket 配置是否与 Docker 模式一致。

### 测试与检查

当前仓库规划使用根目录 `tests/` 存放测试文件，但尚未引入测试框架或固定测试命令。添加测试框架前，不要在文档中声称已有固定测试命令。

可以用 Python 编译检查做基础语法验证：

```bash
uv run python -m compileall bot
```

## 环境变量与外部服务

`.env.example` 是环境变量模板。

必填：

- `QQ_ACCOUNT`：NapCat 登录的机器人 QQ 号。
- `AUTHORIZED_USER_IDS`：授权用户 QQ 号白名单，多个用英文逗号分隔。
- `DEEPSEEK_API_KEY`：DeepSeek API Key，用于 `/ai` 的 LLM 精排。
- `SILICONFLOW_API_KEY`：SiliconFlow API Key，用于生成 embedding。

可选：

- `BOT_HOST`，默认 `0.0.0.0`。
- `BOT_PORT`，默认 `8080`。
- `DEEPSEEK_BASE_URL`。
- `DEEPSEEK_MODEL`。
- `SILICONFLOW_BASE_URL`。
- `SILICONFLOW_EMBEDDING_MODEL`，v1.0 默认 `Qwen/Qwen3-Embedding-8B`。
- `LOG_LEVEL`。

## 系统架构概览

这是一个 Docker Compose 部署的 QQ 私聊表情包机器人：

- `napcat` 容器负责 QQ 协议端。
- `bot` 容器运行 NoneBot2 和业务插件。
- v1.0 使用反向 WebSocket：NapCat 主动连接 NoneBot2。
- Bot 端口 `BOT_PORT` 只在 Docker 网络内供 NapCat 连接，不映射到宿主机。
- NapCat WebUI 通过宿主机 `6099` 端口访问。

核心数据目录：

- `memes/`：本地表情包图片文件。
- `data/index.json`：用户可维护主索引，保存 id、文件名、OCR 文本和 `text_hash`。
- `data/embeddings.json`：系统生成的向量索引，不建议手动编辑。

隐私边界：表情包图片始终本地存储；OCR 文本会按功能需要发送给 SiliconFlow 和 DeepSeek。

## 索引格式要点

`index.json` 示例：

```json
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

`embeddings.json` 使用同一 id 作为 key，并保存 `text_hash` 与 embedding。

`text_hash` 规则：先规范化 OCR 文本，去除首尾空白并合并连续空白，再计算 SHA-256，格式为 `sha256:<hex>`。

如果用户手动修改 `index.json` 中的 `text` 导致 `text_hash` 不一致，启动或 `/refresh` 时应自动更新该条目的 `text_hash` 并重建对应 embedding。

`index.json` 损坏或缺少必要字段时，拒绝启动或刷新，要求用户修复。`embeddings.json` 是派生文件；如果损坏且 `index.json` 有效，应自动重建。

## 当前实现注意事项

现有代码仍是早期框架，可能尚未完全实现 `docs/PRD.md` 的最新设计，例如 `index.json`、`/add`、SiliconFlow embedding、`BOT_HOST`/`BOT_PORT` 和反向 WebSocket 配置。实现或重构前，以 `docs/PRD.md` 和 `CONTEXT.md` 为准，并同步更新 README、`.env.example`、`docker-compose.yml`。