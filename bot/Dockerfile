FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /var/cache/apt/archives/lock /var/lib/apt/lists/lock && \
    apt-get update && \
    apt-get install -y --no-install-recommends g++ libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen

FROM python:3.12-slim
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /var/cache/apt/archives/lock /var/lib/apt/lists/lock && \
    apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY bot/ ./bot/
RUN mkdir -p /app/memes /app/data /app/log /app/meme_no_text
CMD ["/app/.venv/bin/python", "-m", "bot.bot"]