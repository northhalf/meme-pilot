#!/usr/bin/env bash
# MemePilot 部署引导脚本（Linux/macOS/WSL）
#
# 从 GitHub 原始文件拉取运行时所需的三项：
#   - napcat/entrypoint.sh（同时创建 napcat/config、napcat/qq 空目录供卷挂载）
#   - docker-compose.yml
#   - .env.example（自动改名为 .env）
#
# 用法：
#   ./deploy.sh [目标目录]            # 默认当前目录
#   REPO_REF=v1.0.0 ./deploy.sh dir  # 指定仓库引用（默认 main）
#
# 依赖：curl（Debian/Ubuntu: sudo apt-get install -y curl；macOS: brew install curl）
# 幂等：已存在的文件一律跳过（.env 永不覆盖），可安全重复执行。

set -euo pipefail

REPO="northhalf/meme-pilot"
REF="${REPO_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
TARGET_DIR="${1:-.}"

# 依赖检查
if ! command -v curl >/dev/null 2>&1; then
  echo "[memepilot] 缺少依赖 curl，请先安装：sudo apt-get install -y curl" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

echo "[memepilot] 目标目录: $(pwd)"
echo "[memepilot] 仓库引用: ${REPO} @ ${REF}"

# 1. napcat/entrypoint.sh（并确保 config、qq 目录存在以供卷挂载）
mkdir -p napcat/config napcat/qq
if [ -f napcat/entrypoint.sh ]; then
  echo "[memepilot] napcat/entrypoint.sh 已存在，跳过"
else
  if curl -fsSL --retry 3 --max-time 60 "${RAW_BASE}/napcat/entrypoint.sh" -o napcat/entrypoint.sh; then
    echo "[memepilot] napcat/entrypoint.sh 已拉取"
  else
    echo "[memepilot] 拉取 napcat/entrypoint.sh 失败（检查网络或 REPO_REF=${REF}）" >&2
    rm -f napcat/entrypoint.sh
    exit 1
  fi
fi

# 2. docker-compose.yml
if [ -f docker-compose.yml ]; then
  echo "[memepilot] docker-compose.yml 已存在，跳过"
else
  if curl -fsSL --retry 3 --max-time 60 "${RAW_BASE}/docker-compose.yml" -o docker-compose.yml; then
    echo "[memepilot] docker-compose.yml 已拉取"
  else
    echo "[memepilot] 拉取 docker-compose.yml 失败" >&2
    rm -f docker-compose.yml
    exit 1
  fi
fi

# 3. .env（由 .env.example 改名而来；已存在则保留不动）
if [ -f .env ]; then
  echo "[memepilot] .env 已存在，跳过（保留本地配置）"
else
  if curl -fsSL --retry 3 --max-time 60 "${RAW_BASE}/.env.example" -o .env; then
    echo "[memepilot] .env 已生成（源自 .env.example）"
  else
    echo "[memepilot] 拉取 .env.example 失败" >&2
    rm -f .env
    exit 1
  fi
fi

echo "[memepilot] 拉取完成"
