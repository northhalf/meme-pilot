# bot/logging_config.py — 日志配置 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 模块级常量

| 常量 | 类型 | 值 | 说明 |
|------|------|------|------|
| `MAX_LOG_FILE_BYTES` | `int` | `10_485_760` | 单个日志文件大小上限，10 MB |
| `MAX_LOG_BACKUP_COUNT` | `int` | `3` | 滚动日志保留的备份文件数量 |

## 模块级函数

### `setup_logging(log_dir: str = "log") -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `log_dir` | `str` | `"log"` | 日志目录路径 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **副作用** | 配置 `bot` logger 层次 | 添加 `RotatingFileHandler` 和 `StreamHandler` 到 `logging.getLogger("bot")`；不修改根 logger，不影响第三方库日志 |

配置内容：

| 处理器 | 目标 | 级别 | 格式 |
|--------|------|------|------|
| `RotatingFileHandler` | `<log_dir>/bot.log`，单文件不超过 10 MB，保留 3 个备份 | DEBUG | `时间 - 模块名 - 级别 - [req:xxx] 消息`（通过 `RequestIdFormatter` 注入） |
| `StreamHandler` | stdout | INFO | `时间 - 模块名 - 级别 - [req:xxx] 消息`（通过 `RequestIdFormatter` 注入） |

启动时调用一次。自动创建 `<log_dir>` 目录。

子 logger（`bot.plugins.*`、`bot.engine.*` 等）通过 Python logging 的继承关系自动获取父级 handler 与 formatter。`bot` logger 设置 `propagate = False`，日志消息不会传播到根 logger。