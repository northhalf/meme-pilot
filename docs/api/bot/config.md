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
