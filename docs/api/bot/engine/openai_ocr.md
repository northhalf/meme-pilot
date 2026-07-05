# bot/engine/openai_ocr.py — OpenAI 兼容 OCR API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `OpenAIOcrService` 类

实现 `index_manager.OcrProvider` 协议，通过 OpenAI 兼容的 chat completions API 调用视觉模型进行图片文字识别。

示例默认使用 `deepseek-ai/DeepSeek-OCR` 视觉模型，也可通过 `OPENAI_OCR_MODEL` 环境变量切换为其他 OpenAI 兼容的视觉模型。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `MIME_MAP` | `dict[str, str]` | 支持的图片扩展名到 MIME 类型映射 |
| `OCR_PROMPT` | `str` | DeepSeek-OCR 通用文字识别 prompt |

---

### `__init__(api_key: str | None = None, base_url: str | None = None, model: str | None = None, concurrency: int | None = None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `api_key` | `str \| None` | `None` | OpenAI 兼容 OCR API Key，默认从 `OPENAI_OCR_API_KEY` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `OPENAI_OCR_BASE_URL` 环境变量读取；未提供时使用 OpenAI SDK 默认地址 |
| `model` | `str \| None` | `None` | OCR 模型名，默认从 `OPENAI_OCR_MODEL` 环境变量读取；**必须配置**，否则构造函数抛出 `ValueError` |
| `concurrency` | `int \| None` | `None` | OCR API 并发上限，默认从 `OCR_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 `ocr()` 调用数。 |

---

### `async ocr(image_path: str) -> str`

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str` | 图片文件路径 |
| **返回** | `str` | 识别到的文本字符串（已清洗定位标记并去除所有空白字符） |
| **异常** | `FileNotFoundError` | 图片文件不存在 |
| | `ValueError` | 不支持的图片格式 |
| | `RuntimeError` | API 调用失败或返回为空 |

将图片转为 base64 data URL 后，通过 OpenAI 兼容 chat completions API 调用视觉模型进行文字识别；返回前去除所有空白字符。

方法装饰有 `@api_retry(...)`，对 `openai.APIConnectionError`、`openai.APITimeoutError`、`openai.RateLimitError`、`openai.InternalServerError` 及 httpx 网络异常进行最多 3 次指数退避重试。

---

### `async close() -> None`

释放 `AsyncOpenAI` HTTP 客户端会话。

---

## 工厂函数

### `create_openai_ocr_service() -> OpenAIOcrService`

从环境变量创建 `OpenAIOcrService` 实例。

| | 说明 |
|--|------|
| 并发数 | 通过 `bot.config.read_int_env("OCR_CONCURRENCY")` 读取，无效时 Service 内部回退为 5 |

通常由 `bot/engine/__init__.py` 注册为 `"deepseek"` OCR provider。
