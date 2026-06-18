# 日志滚动机制设计

> 日期：2026-06-18
> 状态：待实现

---

## 1. 目标

为 MemePilot bot 容器建立统一日志记录机制：

- 日志文件写入根目录 `log/`，宿主机通过 Docker 挂载卷持久化。
- 同时输出到 stdout，兼容 `docker compose logs -f bot`。
- 文件级别为 DEBUG，控制台级别为 INFO。
- 使用滚动日志：当前文件 `bot.log`，备份 `bot.log.1`，每个文件上限 1 MB。

---

## 2. 总体架构

```
┌─────────────────────────────────┐
│          bot 容器 /app           │
│                                  │
│  bot.py ──► logging_config.py   │
│                │                 │
│                ▼                 │
│         logging.basicConfig(     │
│           level=DEBUG,           │
│           handlers=[             │
│             StreamHandler ──► stdout (INFO)
│             RotatingFileHandler │
│               ──► log/bot.log (DEBUG)
│               ──► log/bot.log.1 │
│           ]                      │
│         )                        │
│                                  │
│  Docker 卷: ./log:/app/log       │
└──────────────────────────────────┘
```

---

## 3. 模块设计

### 3.1 `bot/logging_config.py`

单一职责：`setup_logging()` 函数，调用一次即完成全局日志配置。

```python
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logging() -> None:
    LOG_DIR = Path("log")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"

    file_handler = RotatingFileHandler(
        LOG_DIR / "bot.log",
        maxBytes=1_048_576,
        backupCount=1,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[stream_handler, file_handler],
    )
```

### 3.2 `bot/bot.py` 入口调用

```python
from bot.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("meme_bot")
logger.info("MemePilot 启动中...")
```

### 3.3 业务模块使用

各 engine/plugin 模块沿用现有风格：

```python
import logging
logger = logging.getLogger(__name__)
```

---

## 4. 数据流

```
logger.debug(...) ──► Root Logger (DEBUG) ──┬── StreamHandler (INFO)   ✗ 过滤
                                            │
                                            └── RotatingFileHandler (DEBUG) ✓ log/bot.log

logger.info(...)  ──► Root Logger (DEBUG) ──┬── StreamHandler (INFO)   ✓ stdout
                                            │
                                            └── RotatingFileHandler (DEBUG) ✓ log/bot.log
```

### 轮转流程

```
bot.log 写入中
    │
    ├── 文件大小 < 1 MB → 继续写入
    │
    └── 文件大小 >= 1 MB →
        ├── 关闭 bot.log 句柄
        ├── 删除旧 bot.log.1（如果存在）
        ├── bot.log → bot.log.1
        └── 新建 bot.log 继续写入
```

---

## 5. 配置参数

| 参数 | 值 | 说明 |
|------|-----|------|
| Root Logger level | `DEBUG` | 不过滤，由各 handler 自行控级 |
| FileHandler level | `DEBUG` | 文件记录全部日志 |
| StreamHandler level | `INFO` | 控制台只到 INFO |
| 日志文件路径 | `log/bot.log` | 相对容器工作目录 `/app` |
| 备份文件 | `log/bot.log.1` | 最多 1 个备份 |
| 单文件上限 | `1_048_576` (1 MB) | `maxBytes` |
| 编码 | `utf-8` | 支持中文日志 |
| 格式 | `时间 - 模块名 - 级别 - 消息` | |
| 时间格式 | `%Y-%m-%d %H:%M:%S` | |

---

## 6. Docker 变更

`docker-compose.yml` bot 服务新增挂载：

```yaml
volumes:
  - ./memes:/app/memes
  - ./data:/app/data
  - ./log:/app/log          # 新增
```

新增 `.gitignore` 规则：

```gitignore
log/
```

---

## 7. 错误处理

| 场景 | 处理方式 |
|------|----------|
| `log/` 目录不存在 | `Path.mkdir(parents=True, exist_ok=True)` 自动创建 |
| `log/` 目录无写权限 | `logging` 模块 `handleError` 静默处理，不抛异常 |
| 磁盘满 | `handleError` 静默处理，不中断业务 |
| 轮转时文件被外部锁定 | `RotatingFileHandler` 内部处理 `OSError` |

---

## 8. 测试策略

文件：`tests/unit/test_logging_config.py`

| 测试用例 | 验证内容 |
|----------|----------|
| `test_creates_log_directory` | 调用 `setup_logging()` 后 `log/` 目录存在 |
| `test_rotating_file_handler_added` | Root Logger 包含 `RotatingFileHandler` |
| `test_stream_handler_added` | Root Logger 包含 `StreamHandler` |
| `test_file_handler_debug_level` | FileHandler level 为 `DEBUG` |
| `test_stream_handler_info_level` | StreamHandler level 为 `INFO` |
| `test_file_handler_max_bytes` | `maxBytes` = `1_048_576` |
| `test_file_handler_backup_count` | `backupCount` = `1` |
| `test_file_handler_encoding` | 编码为 `utf-8` |
| `test_can_write_to_log_file` | 写入日志后文件内容包含目标字符串 |
| `test_handlers_count` | Root Logger 恰好有 2 个 handler |
| `test_root_logger_level_debug` | Root Logger level 为 `DEBUG` |

不测试：容器内实际轮转行为、Docker 挂载卷映射（属于集成/部署验证）。

---

## 9. 不依赖第三方库

仅使用 Python 标准库 `logging` 和 `logging.handlers`，`requirements.txt` 无需新增依赖。
