# bot/config.py — 全局路径常量 API

> 通过 `Path(__file__).resolve().parent.parent` 定位项目根目录，导出全局路径常量。

## 常量

### `PROJECT_ROOT: Path`

| | 类型 | 说明 |
|--|------|------|
| **值** | `Path` | 项目根目录，绝对路径 |

### `MEMES_DIR: Path`

| | 类型 | 说明 |
|--|------|------|
| **值** | `Path` | 表情包图片目录，绝对路径 `<项目根>/memes` |

### `DATA_DIR: Path`

| | 类型 | 说明 |
|--|------|------|
| **值** | `Path` | 索引数据目录，绝对路径 `<项目根>/data` |

### `INDEX_DB_PATH: Path`

| | 类型 | 说明 |
|--|------|------|
| **值** | `Path` | sqlite 元数据数据库文件路径，绝对路径 `<项目根>/data/index.db` |

### `CHROMA_DIR: Path`

| | 类型 | 说明 |
|--|------|------|
| **值** | `Path` | chroma 向量库数据目录，绝对路径 `<项目根>/data/chroma` |

各插件和 `bot.py` 统一从 `bot.config` 导入，避免重复定义。

## 函数

### `read_session_timeout() -> int`

从环境变量 `SESSION_EXPIRE_TIMEOUT` 读取会话超时秒数。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 超时秒数，默认 60 |
| **格式** | — | 支持纯数字（秒）或 `HH:MM:SS` / `DD:HH:MM:SS` 等 pydantic timedelta 格式 |
| **无效值** | — | 回退为 60 |

---

### `read_ocr_provider() -> str`

从环境变量 `OCR_PROVIDER` 读取 OCR 引擎类型。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `str` | `"paddle"`（默认）或 `"deepseek"` |
| **无效值** | — | 回退为 `"paddle"` |
| **空白处理** | — | 值中的首尾空白自动去除，不区分大小写 |

---

### `read_read_lock_timeout() -> int`

从环境变量 `READ_LOCK_TIMEOUT` 读取读锁等待超时秒数。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 超时秒数，默认 30 |
| **格式** | — | 支持纯数字（秒）或 `HH:MM:SS` / `DD:HH:MM:SS` 等 pydantic timedelta 格式 |
| **无效值** | — | 回退为 30 |

---

### `read_add_command_timeout() -> int`

从环境变量 `ADD_COMMAND_TIMEOUT` 读取 `/add` 命令用户等待超时秒数。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 超时秒数，默认 60 |
| **格式** | — | 支持纯数字（秒）或 `HH:MM:SS` / `DD:HH:MM:SS` 等 pydantic timedelta 格式 |
| **无效值** | — | 回退为 60 |

---

### `read_bot_port() -> int`

从环境变量 `BOT_PORT` 读取 Bot 监听端口。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `int` | 有效端口号，无效值回退为 8080 |

---

### `read_int_env(key: str, default: int) -> int | None`

从环境变量读取可选整数值。

| | 类型 | 说明 |
|--|------|------|
| **key** | `str` | 环境变量名 |
| **default** | `int` | 回退默认值 |
| **返回** | `int \| None` | 有效正整数或 None（Service 收到 None 后会使用自身的默认值 5） |
| **异常输入** | — | 空字符串、非整数、零、负数均返回 None |
