# bot/logging_config.py — 日志配置 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 模块级函数

### `setup_logging(log_dir: str = "log") -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `log_dir` | `str` | `"log"` | 日志目录路径 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **副作用** | 配置全局 `logging.root` | 添加 `RotatingFileHandler` 和 `StreamHandler` |

配置内容：

| 处理器 | 目标 | 级别 | 格式 |
|--------|------|------|------|
| `RotatingFileHandler` | `<log_dir>/bot.log`，单文件不超过 1 MB，保留 1 个备份 | DEBUG | `时间 - 模块名 - 级别 - 消息` |
| `StreamHandler` | stdout | INFO | `时间 - 模块名 - 级别 - 消息` |

启动时调用一次。自动创建 `<log_dir>` 目录。