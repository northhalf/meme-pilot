# bot/engine/deepseek_ocr.py — DeepSeek-OCR API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `DeepSeekOcrService` 类

实现 `index_manager.OcrProvider` 协议，通过硅基流动 chat completions API 调用 `deepseek-ai/DeepSeek-OCR` 模型进行图片文字识别。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `MIME_MAP` | `dict[str, str]` | 支持的图片扩展名到 MIME 类型映射 |
| `OCR_PROMPT` | `str` | DeepSeek-OCR 通用文字识别 prompt |

---

### `__init__(api_key: str | None = None, base_url: str | None = None, model: str | None = None, concurrency: int | None = None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `api_key` | `str \| None` | `None` | 硅基流动 API Key，默认从 `SILICONFLOW_API_KEY` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `SILICONFLOW_BASE_URL` 环境变量读取，回退 `https://api.siliconflow.cn/v1` |
| `model` | `str \| None` | `None` | OCR 模型名，默认从 `SILICONFLOW_OCR_MODEL` 环境变量读取，回退 `deepseek-ai/DeepSeek-OCR` |
| `concurrency` | `int \| None` | `None` | OCR API 并发上限，默认从 `OCR_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 ocr() 调用数。 |

---

### `async ocr(image_path: str) -> str`

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str` | 图片文件路径 |
| **返回** | `str` | 识别到的文本字符串（已清洗定位标记并去除所有空白字符） |
| **异常** | `FileNotFoundError` | 图片文件不存在 |
| | `ValueError` | 不支持的图片格式 |
| | `RuntimeError` | API 调用失败或返回为空 |

将图片转为 base64 data URL 后，通过硅基流动 chat completions API 调用 DeepSeek-OCR 视觉模型进行文字识别；返回前用 `"".join(text.split())` 去除所有空白字符。