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
