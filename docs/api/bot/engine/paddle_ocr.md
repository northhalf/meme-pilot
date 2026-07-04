# bot/engine/paddle_ocr.py — PaddleOCR 云 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `PaddleOcrClientService` 类

实现 `index_manager.OcrProvider` 协议，通过 `AsyncPaddleOCRClient` 调用 PaddleOCR 官方云 API 进行图片文字识别。

---

### `__init__(access_token: str | None = None, base_url: str | None = None, model: Model | str | None = None, request_timeout: float = 300.0, poll_timeout: float = 600.0, text_rec_score_thresh: float = 0.9, concurrency: int | None = None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `access_token` | `str \| None` | `None` | AIStudio Access Token，默认从 `PADDLEOCR_ACCESS_TOKEN` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `PADDLEOCR_BASE_URL` 环境变量读取 |
| `model` | `Model \| str \| None` | `None` | OCR 模型，默认 `Model.PP_OCRV6` |
| `request_timeout` | `float` | `300.0` | 请求超时秒数 |
| `poll_timeout` | `float` | `600.0` | 轮询超时秒数 |
| `text_rec_score_thresh` | `float` | `0.9` | 置信度阈值（0~1），低于此值的文本行被过滤；设为 0 关闭过滤 |
| `concurrency` | `int \| None` | `None` | OCR API 并发上限，默认从 `OCR_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 ocr() 调用数。 |

---

### `async ocr(image_path: str) -> str`

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str` | 图片文件路径 |
| **返回** | `str` | 识别到的文本字符串（已去除所有空白字符，可能为空字符串） |
| **异常** | `RuntimeError` | API 调用失败 |

调用 `AsyncPaddleOCRClient.ocr()` 提交 OCR 任务并等待完成，从返回结果的 `pruned_result` 中防御性提取文本，返回前用 `"".join(" ".join(texts).split())` 去除所有空白字符。

兼容多种 API 返回格式：
- PaddleOCR v3.7 新版：API 返回 `prunedResult` 为完整字典，从 `rec_texts` 列表中提取所有文本行并用空格拼接；根据 `text_rec_score_thresh` 阈值，以 `rec_scores` 置信度过滤低分行
- 旧版兼容：`prunedResult` 为字符串或 `list[dict]` 时直接提取

调用 API 时传入 `OCROptions` 禁用文档预处理和文本行方向检测，避免多行文本被预处理合并。

---

### `async close() -> None`

释放 `AsyncPaddleOCRClient` 内部 HTTP 会话。
