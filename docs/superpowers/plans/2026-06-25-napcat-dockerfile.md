# NapCat 配置 + Bot Dockerfile 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补全 MemePilot Docker 部署的缺失部分 — NapCat 反向 WebSocket 预置配置和 Bot Dockerfile，实现 `docker compose up -d` 一键启动。

**Architecture:** NapCat 容器通过自定义 entrypoint 脚本在首次启动时自动生成 OneBot v11 反向 WebSocket 配置，连接到同一 Docker 网络中的 Bot 容器。Bot 容器使用 python:3.12-slim + uv 构建。

**Tech Stack:** Docker, docker-compose, sh (entrypoint), Dockerfile (multi-stage uv)

---

## File Structure

| 操作 | 文件 | 职责 |
|------|------|------|
| 创建 | `napcat/entrypoint.sh` | 启动时自动生成 OneBot v11 反向 WebSocket 配置 |
| 创建 | `napcat/config/.gitkeep` | 保持空目录在 Git 中 |
| 创建 | `napcat/qq/.gitkeep` | 保持空目录在 Git 中 |
| 创建 | `bot/Dockerfile` | Bot 容器构建文件 |
| 修改 | `docker-compose.yml` | napcat 服务添加 entrypoint 和脚本挂载 |
| 修改 | `.gitignore` | 添加 .gitkeep 排除规则 |

---

### Task 1: 创建 NapCat 目录结构

**Files:**
- Create: `napcat/config/.gitkeep`
- Create: `napcat/qq/.gitkeep`

- [ ] **Step 1: 创建目录和 .gitkeep 文件**

```bash
mkdir -p napcat/config napcat/qq
touch napcat/config/.gitkeep napcat/qq/.gitkeep
```

- [ ] **Step 2: 验证文件存在**

```bash
ls -la napcat/config/.gitkeep napcat/qq/.gitkeep
```

Expected: 两个文件都存在，大小为 0。

- [ ] **Step 3: Commit**

```bash
git add napcat/config/.gitkeep napcat/qq/.gitkeep
git commit -m "chore: 创建 napcat 目录结构及 .gitkeep"
```

---

### Task 2: 创建 NapCat entrypoint 脚本

**Files:**
- Create: `napcat/entrypoint.sh`

- [ ] **Step 1: 创建 entrypoint.sh**

```bash
cat > napcat/entrypoint.sh << 'EOF'
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
EOF
```

- [ ] **Step 2: 设置可执行权限**

```bash
chmod +x napcat/entrypoint.sh
```

- [ ] **Step 3: 验证脚本内容和权限**

```bash
head -1 napcat/entrypoint.sh
# Expected: #!/bin/sh

ls -la napcat/entrypoint.sh
# Expected: -rwxr-xr-x (可执行权限)
```

- [ ] **Step 4: Commit**

```bash
git add napcat/entrypoint.sh
git commit -m "feat(napcat): 添加 entrypoint 脚本，自动生成 OneBot v11 反向 WebSocket 配置"
```

---

### Task 3: 更新 .gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 添加 .gitkeep 排除规则**

在 `.gitignore` 的 `napcat/config/` 和 `napcat/qq/` 行之后添加 `.gitkeep` 排除规则。

当前 `.gitignore` 第 9-11 行：
```
# NapCat 配置
napcat/config/
napcat/qq/
```

替换为：
```
# NapCat 配置
napcat/config/*
napcat/qq/*
!napcat/config/.gitkeep
!napcat/qq/.gitkeep
```

- [ ] **Step 2: 验证 .gitkeep 不被忽略**

```bash
git status napcat/config/.gitkeep napcat/qq/.gitkeep
```

Expected: 文件显示为 tracked 或 untracked（不被忽略）。

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: 更新 .gitignore，保留 napcat 目录的 .gitkeep 文件"
```

---

### Task 4: 创建 Bot Dockerfile

**Files:**
- Create: `bot/Dockerfile`

- [ ] **Step 1: 创建 Dockerfile**

```dockerfile
cat > bot/Dockerfile << 'EOF'
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
EOF
```

- [ ] **Step 2: 验证 Dockerfile 语法**

```bash
docker build --check -f bot/Dockerfile . 2>&1 || true
# 或者简单检查文件存在
cat bot/Dockerfile
```

Expected: Dockerfile 内容与 spec 一致。

- [ ] **Step 3: Commit**

```bash
git add bot/Dockerfile
git commit -m "feat(bot): 添加 Dockerfile，基于 python:3.12-slim + uv 构建"
```

---

### Task 5: 更新 docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: 添加 entrypoint 和脚本挂载**

当前 napcat 服务配置（第 5-19 行）：
```yaml
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: qq-meme-napcat
    restart: always
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
    networks:
      - meme-bot-net
```

替换为：
```yaml
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: qq-meme-napcat
    restart: always
    entrypoint: ["/custom-entrypoint.sh"]
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
      - ./napcat/entrypoint.sh:/custom-entrypoint.sh:ro
    networks:
      - meme-bot-net
```

变更点：
1. 第 8 行后插入 `entrypoint: ["/custom-entrypoint.sh"]`
2. 第 18 行后插入 `- ./napcat/entrypoint.sh:/custom-entrypoint.sh:ro`

- [ ] **Step 2: 验证 YAML 语法**

```bash
python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml'))" && echo "YAML OK"
```

Expected: `YAML OK`

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): napcat 服务添加 entrypoint 脚本挂载，自动生成反向 WebSocket 配置"
```

---

### Task 6: 部署验证

**Files:**
- 无新增/修改（纯验证）

- [ ] **Step 1: 构建 Bot 镜像**

```bash
docker compose build bot
```

Expected: 构建成功，无错误。

- [ ] **Step 2: 启动所有容器**

```bash
docker compose up -d
```

Expected: 两个容器都启动成功。

- [ ] **Step 3: 检查 NapCat 容器日志**

```bash
docker compose logs napcat | head -20
```

Expected: 日志中包含 `[memepilot] 已生成 NapCat 配置` 字样。

- [ ] **Step 4: 检查 Bot 容器日志**

```bash
docker compose logs bot | head -30
```

Expected: 日志显示 NoneBot2 启动、索引同步流程。

- [ ] **Step 5: 确认配置文件已生成**

```bash
ls -la napcat/config/
```

Expected: 存在 `onebot11_<QQ_ACCOUNT>.json` 文件。

- [ ] **Step 6: 清理（测试完成后）**

```bash
docker compose down
```
