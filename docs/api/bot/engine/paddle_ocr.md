# bot/engine/paddle_ocr.py — PaddleOCR 云 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `PaddleOcrClientService` 类

实现 `index_manager.OcrProvider` 协议，通过 `AsyncPaddleOCRClient` 调用 PaddleOCR 官方云 API 进行图片文字识别。

---

### `__init__(access_token: str | None = None, base_url: str | None = None, model: Model | str | None = None, request_timeout: float = 300.0, poll_timeout: float = 600.0) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `access_token` | `str \| None` | `None` | AIStudio Access Token，默认从 `PADDLEOCR_ACCESS_TOKEN` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `PADDLEOCR_BASE_URL` 环境变量读取 |
| `model` | `Model \| str \| None` | `None` | OCR 模型，默认 `Model.PP_OCRV6` |
| `request_timeout` | `float` | `300.0` | 请求超时秒数 |
| `poll_timeout` | `float` | `600.0` | 轮询超时秒数 |

---

### `async ocr(image_path: str) -> str`

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str` | 图片文件路径 |
| **返回** | `str` | 识别到的文本字符串（可能为空字符串） |
| **异常** | `RuntimeError` | API 调用失败 |

调用 `AsyncPaddleOCRClient.ocr()` 提交 OCR 任务并等待完成，从返回结果的 `pruned_result` 中防御性提取文本。

---

### `async close() -> None`

释放 `AsyncPaddleOCRClient` 内部 HTTP 会话。
