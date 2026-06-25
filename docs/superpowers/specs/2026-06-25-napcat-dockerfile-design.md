# NapCat 配置 + Bot Dockerfile 设计文档

> 日期：2026-06-25
> 状态：待实现

---

## 1. 目标

补全 MemePilot 的 Docker 部署缺失部分：

1. NapCat OneBot v11 反向 WebSocket 预置配置（自动连接 Bot 容器）
2. Bot 容器 Dockerfile
3. 确保 `docker compose up -d` 可一键启动

## 2. 范围

### 新增文件

| 文件 | 说明 |
|------|------|
| `napcat/entrypoint.sh` | 启动脚本：根据 `$ACCOUNT` 环境变量自动生成 OneBot v11 反向 WebSocket 配置 |
| `napcat/config/.gitkeep` | 保持空目录在 Git 中（运行时生成的配置文件不提交） |
| `napcat/qq/.gitkeep` | 保持空目录在 Git 中（QQ 登录数据不提交） |
| `bot/Dockerfile` | Bot 容器构建文件 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `docker-compose.yml` | napcat 服务添加 `entrypoint` 和脚本挂载卷 |
| `.gitignore` | 添加 `napcat/config/*`、`napcat/qq/*` 忽略规则（排除 `.gitkeep`） |

## 3. 设计详情

### 3.1 NapCat 启动脚本 (`napcat/entrypoint.sh`)

**原理**：NapCat Docker 容器的原始入口点为 `/entrypoint.sh`。自定义脚本在原始入口点之前运行，根据 `$ACCOUNT` 环境变量（docker-compose.yml 中已设置为 `${QQ_ACCOUNT}`）生成配置文件，然后 `exec` 交给原始入口点。

**配置文件名**：`onebot11_${ACCOUNT}.json`（NapCat 标准命名约定）

**生成逻辑**：
- 仅在配置文件不存在时生成（首次启动）
- 用户后续可通过 NapCat WebUI (http://服务器IP:6099) 修改配置，脚本不会覆盖

**完整脚本**：

```bash
#!/bin/sh
# NapCat 自定义入口脚本
# 根据 ACCOUNT 环境变量生成 OneBot v11 反向 WebSocket 配置

CONFIG_FILE="/app/napcat/config/onebot11_${ACCOUNT}.json"

if [ ! -f "$CONFIG_FILE" ]; then
  cat > "$CONFIG_FILE" << 'CFGEOF'
{
  "network": {
    "websocketServers": [],
    "websocketClients": [
      {
        "name": "memepilot-reverse-ws",
        "enable": true,
        "url": "ws://bot:8080/onebot/v11/ws",
        "token": "",
        "reconnectInterval": 5000,
        "heartInterval": 30000,
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "debug": false
      }
    ],
    "httpClients": [],
    "httpSseServers": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": true,
  "parseMultMsg": true
}
CFGEOF
  echo "[memepilot] 已生成 NapCat 配置: $CONFIG_FILE"
fi

# 执行 NapCat 原始入口点
exec /entrypoint.sh "$@"
```

**NapCat OneBot v11 配置内容**：

```json
{
  "network": {
    "websocketServers": [],
    "websocketClients": [
      {
        "name": "memepilot-reverse-ws",
        "enable": true,
        "url": "ws://bot:8080/onebot/v11/ws",
        "token": "",
        "reconnectInterval": 5000,
        "heartInterval": 30000,
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "debug": false
      }
    ],
    "httpClients": [],
    "httpSseServers": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": true,
  "parseMultMsg": true
}
```

**关键配置说明**：

| 字段 | 值 | 说明 |
|------|-----|------|
| `url` | `ws://bot:8080/onebot/v11/ws` | Docker 内部网络；`bot` 是 docker-compose service name；`8080` 是默认 BOT_PORT。若自定义 BOT_PORT，首次启动后需通过 WebUI 手动修改此 URL 中的端口号 |
| `enableLocalFile2Url` | `true` | 允许 Bot 获取本地图片 URL（/add 下载图片需要） |
| `parseMultMsg` | `true` | 合并消息解析（用户发送多图时只取第一张） |
| `messagePostFormat` | `array` | NoneBot2 推荐格式 |
| `reconnectInterval` | `5000` | 断线后 5 秒重连 |

### 3.2 Bot Dockerfile (`bot/Dockerfile`)

```dockerfile
FROM python:3.12-slim

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 先复制依赖声明，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# 复制 bot 源码
COPY bot/ ./bot/

# 创建挂载卷目录（Docker 挂载会覆盖）
RUN mkdir -p /app/memes /app/data /app/log /app/meme_no_text

CMD ["uv", "run", "python", "-m", "bot.bot"]
```

**要点**：
- `python:3.12-slim` — PRD 要求 Python 3.12
- `uv sync --no-dev --frozen` — 精确复现 lock 文件，不含开发依赖
- 依赖层与源码层分离 — 源码变更不触发重新安装依赖
- `uv run python -m bot.bot` — 与 CLAUDE.md 本地运行方式一致

### 3.3 docker-compose.yml 变更

napcat 服务新增：

```yaml
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: qq-meme-napcat
    restart: always
    entrypoint: ["/custom-entrypoint.sh"]              # 新增
    environment:
      - ACCOUNT=${QQ_ACCOUNT:?请设置 QQ_ACCOUNT 环境变量}
      - WS_ENABLE=true
      - WS_PORT=3001
      - TZ=Asia/Shanghai
    ports:
      - "6099:6099"
    volumes:
      - ./napcat/config:/app/napcat/config
      - ./napcat/qq:/app/.config/QQ
      - ./napcat/entrypoint.sh:/custom-entrypoint.sh:ro  # 新增
    networks:
      - meme-bot-net
```

变更点：
1. 添加 `entrypoint: ["/custom-entrypoint.sh"]`
2. 添加 volume 挂载 `./napcat/entrypoint.sh:/custom-entrypoint.sh:ro`

### 3.4 .gitignore 补充

以下目录应被忽略（含运行时生成的配置和登录凭据）：

```
napcat/config/*
napcat/qq/*
!napcat/config/.gitkeep
!napcat/qq/.gitkeep
```

## 4. 部署流程

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 QQ_ACCOUNT、API Key 等

# 2. 启动
docker compose up -d

# 3. 首次登录
# 访问 http://服务器IP:6099，用 NapCat WebUI 扫码登录 QQ
# 登录后 NapCat 自动通过反向 WebSocket 连接 Bot

# 4. 验证
# 向 QQ 发送 /help 测试
```

## 5. 验证清单

- [ ] `docker compose build bot` 成功构建
- [ ] `docker compose up -d` 两个容器正常启动
- [ ] NapCat 容器日志显示配置文件已生成
- [ ] NapCat 容器日志显示反向 WebSocket 连接成功
- [ ] Bot 容器日志显示 NoneBot2 启动完成
- [ ] NapCat WebUI 可访问 (http://服务器IP:6099)
- [ ] 扫码登录后 Bot 响应 `/help` 命令
