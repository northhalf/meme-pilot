---
name: verify
description: Use when MemePilot source changes affect a CLI, provider, bot startup or shutdown, Compose wiring, filesystem/index behavior, or another runtime surface that must be observed.
---

# Verifying MemePilot Runtime Changes

## Overview

Drive the runtime surface and capture output. Tests, type checks, and direct internal calls are not runtime evidence.

**Core principle:** Exercise the smallest real flow without touching real data, active services, credentials, or paid APIs.

## Workflow

1. Scope with `git status --short` and `git diff HEAD --stat`.
2. Choose a surface from the table.
3. Isolate state before launching.
4. Drive one happy path and one adjacent failure/probe.
5. Report captured output as `PASS`, `FAIL`, `BLOCKED`, or `SKIP`.

## Quick Reference

| Change | Surface | Evidence |
|---|---|---|
| CLI | `uv run python -m scripts.<module>` | `--help`, safe normal path, invalid/conflicting flags |
| Provider wiring | Isolated `python -m bot.bot` | Provider selection, startup, graceful close |
| Compose env | `docker compose ... config --quiet` | Exit 0 and rendered wiring |
| Index/filesystem | Temporary directories/project copy | Before/after files and app output |
| No safe surface | Do not use real state | `BLOCKED` with exact reason |

## Safe Isolated Bot Startup

Provider/lifecycle reference pattern:

```bash
repo=$(pwd)
tmp=$(mktemp -d)
port=$(shuf -i 20000-50000 -n1)
trap 'rm -rf "$tmp"' EXIT
cp -a "$repo/bot" "$tmp/bot"
mkdir -p "$tmp"/{memes,data,log,meme_no_text,memes_deleted,memes_replaced}

env -C "$tmp" PYTHONPATH="$tmp" \
  OCR_PROVIDER=baidu BAIDU_API_KEY=verify BAIDU_SECRET_KEY=verify \
  EMBEDDING_PROVIDER=openai OPENAI_EMBEDDING_API_KEY=verify \
  OPENAI_EMBEDDING_MODEL=embedding-3 \
  BOT_HOST=127.0.0.1 BOT_PORT="$port" \
  timeout --signal=INT 10s \
  uv run --project "$repo" python -m bot.bot
```

Require provider selection, startup, and shutdown logs. Accept timeout 124/130 only after graceful shutdown.

## Safety Rules

- Never read or print `.env` secrets.
- Never call real OCR/Embedding APIs unless explicitly requested.
- Never run `docker compose up` in the working deployment.
- Never use real `memes/`, `data/`, or active containers.
- Use module entrypoints: `uv run python -m scripts.<module>`.
- Do not replace runtime observation with pytest, ty, Ruff, imports, or direct function calls.

## Report Contract

```markdown
## Verification: <surface>
**Verdict:** PASS | FAIL | BLOCKED | SKIP
**Claim:** <behavior checked>
**Method:** <isolated runtime or CLI>
1. ✅ <happy path> → <captured output>
2. 🔍 <probe> → <captured output>
### Findings
- <friction, limitation, or none>
```

## Common Mistakes and Rationalizations

| Excuse | Reality |
|---|---|
| “Tests pass.” | CI evidence is not the running surface. |
| “I can import the class.” | Imports bypass CLI, factory, startup, and shutdown wiring. |
| “Dummy keys make the workspace safe.” | Startup can scan images, mutate indexes, or call services. |
| “One real API call is harmless.” | It consumes quota and sends user data externally. |
| “Compose config is enough.” | It checks wiring, not runtime startup. |

## Red Flags

Stop and isolate or report `BLOCKED` before using real `.env`, `memes/`, `data/`, active containers, a paid API, an internal function instead of a surface, or tests as final evidence.
