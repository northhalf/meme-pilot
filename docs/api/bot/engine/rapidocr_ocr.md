# bot/engine/rapidocr_ocr.py — RapidOCR 本地 OCR API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `RapidOcrService` 类

实现 `index_manager.OcrProvider` 协议，使用本地 RapidOCR ONNX 模型进行图片文字识别。

无需网络调用，适合在本地或离线环境运行。

---

### `__init__(text_score: float = 0.9, concurrency: int | None = None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `text_score` | `float` | `0.9` | 文本置信度阈值（0~1），低于此值的文本行被过滤 |
| `concurrency` | `int \| None` | `None` | OCR 并发上限，默认从 `OCR_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 `ocr()` 调用数。 |

---

### `async ocr(image_path: str) -> str`

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str` | 图片文件路径 |
| **返回** | `str` | 识别到的文本字符串（已去除所有空白字符，可能为空字符串） |
| **异常** | `FileNotFoundError` | 图片文件不存在 |
| | `RuntimeError` | 推理异常 |

通过 `asyncio.to_thread` 在线程池中调用本地 RapidOCR 引擎（`use_det=True, use_cls=False, use_rec=True`）；
解析返回的 `RapidOCROutput` 对象，按行读取 `txts` 与 `scores`，过滤低于 `text_score` 的文本行，
最后用 `"".join(" ".join(lines).split())` 去除所有空白字符。

---

### `async close() -> None`

本地引擎无需释放网络会话，当前为空操作。

---

## 工厂函数

### `create_rapidocr_service() -> RapidOcrService`

从环境变量创建 `RapidOcrService` 实例。

| | 说明 |
|--|------|
| `text_score` | 通过 `bot.config.read_ocr_text_score()` 读取，默认 0.9 |
| 并发数 | 通过 `bot.config.read_int_env("OCR_CONCURRENCY")` 读取，无效时 Service 内部回退为 5 |

通常由 `bot/engine/__init__.py` 注册为 `"rapidocr"` OCR provider。

---

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `OCR_TEXT_SCORE` | 文本置信度阈值 | `0.9` |
| `OCR_CONCURRENCY` | OCR 并发上限 | `5` |
