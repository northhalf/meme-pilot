FROM python:3.12-slim

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 安装 C++ 编译器（pylcs 等包需要 C++11 编译）以及 OpenCV 所需的图形库
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖声明，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen


# 复制 bot 源码
COPY bot/ ./bot/

# 创建挂载卷目录（Docker 挂载会覆盖）
RUN mkdir -p /app/memes /app/data /app/log /app/meme_no_text

CMD ["uv", "run", "--no-sync", "python", "-m", "bot.bot"]
