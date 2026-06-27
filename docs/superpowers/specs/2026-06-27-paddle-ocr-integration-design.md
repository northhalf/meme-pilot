# PaddleOCR 官方 API 集成设计文档

> 日期：2026-06-27
> 状态：设计确认待实现

## 1. 目标

在 MemePilot 中增加 PaddleOCR 官方云 API 作为可选的 OCR 引擎，与现有的 DeepSeek OCR 并存，通过环境变量切换。

## 2. 变更范围

### 2.1 新增文件

- **`bot/engine/paddle_ocr_client.py`** — `PaddleOcrClientService` 类

### 2.2 修改文件

| 文件 | 变更说明 |
|------|---------|
| `bot/engine/__init__.py` | 导出 `PaddleOcrClientService` |
| `bot/config.py` | 新增 `read_ocr_provider()` 函数 |
| `bot/bot.py` | 按 `OCR_PROVIDER` 条件创建 OCR 服务；新增 shutdown 钩子 |
| `.env.example` | 新增 `OCR_PROVIDER`、`PADDLEOCR_ACCESS_TOKEN` |
| `docs/api/API.md` | 添加 `PaddleOcrClientService` 接口文档 |

### 2.3 依赖

```toml
# pyproject.toml — 用户已通过 uv add paddleocr 添加
"paddleocr>=3.7.0",
```

`paddleocr` 包不直接依赖 `paddlepaddle`，仅通过 `paddlex[ocr-core]` + `requests` + `aiohttp` 实现 HTTP 调用，对 Docker 镜像体积影响小。

## 3. 设计详情

### 3.1 PaddleOcrClientService

```python
class PaddleOcrClientService:
    """使用 AsyncPaddleOCRClient 调用 PaddleOCR 官方云 API。
    实现 index_manager.OcrProvider 协议。
    """

    def __init__(
        self,
        access_token: str | None = None,       # PADDLEOCR_ACCESS_TOKEN
        base_url: str | None = None,            # PADDLEOCR_BASE_URL
        model: Model | str | None = None,       # 默认 Model.PP_OCRV6
        request_timeout: float = 120.0,
        poll_timeout: float = 300.0,
    )

    async def ocr(self, image_path: str) -> str
    async def close(self) -> None
```

- 使用 `AsyncPaddleOCRClient` 异步 SDK，与 NoneBot2 异步架构一致
- `ocr()` 调用 `client.ocr(file_path=..., model=...)` 提交任务并等待完成
- `pruned_result` 文本提取兼容多种格式（字符串/列表/dict），按 `text`、`content`、`transcription` 等常见字段名提取
- `close()` 释放 `AsyncPaddleOCRClient` 内部 HTTP 会话

### 3.2 配置切换

**环境变量：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OCR_PROVIDER` | `deepseek` | `deepseek` → DeepSeekOcrService；`paddle` → PaddleOcrClientService |
| `PADDLEOCR_ACCESS_TOKEN` | — | AIStudio Access Token，必填当使用 paddle 时 |

**bot.py 逻辑：**

```python
from bot.config import read_ocr_provider

provider = read_ocr_provider()
if provider == "paddle":
    ocr_service = PaddleOcrClientService()
else:
    ocr_service = DeepSeekOcrService()
```

**shutdown 钩子：**

```python
async def _on_shutdown() -> None:
    ocr_service = get_ocr_service()
    if hasattr(ocr_service, "close"):
        await ocr_service.close()
```

### 3.3 错误处理

| 场景 | 行为 |
|------|------|
| Token 未设置 | `AsyncPaddleOCRClient` 抛出 `AuthError`，转为日志输出 |
| API 超时 | `PollTimeoutError` → 转为 `RuntimeError` |
| API 返回空 | 返回空字符串 |
| 图片不存在 | `FileNotFoundError` |

### 3.4 测试策略

- `tests/unit/engine/test_paddle_ocr_client.py`：mock `AsyncPaddleOCRClient` 验证 ocr 方法
- `tests/integration/test_paddle_ocr_client_api.py`：需要真实 Access Token
- `tests/unit/engine/test_ocr_provider_switch.py`：验证 config 读取和 bot.py 切换逻辑

## 4. 不涉及变更

- `bot/engine/protocols.py` — `OcrProvider` 协议适配，无需修改
- `bot/engine/index_manager.py` — 已通过协议解耦，无需修改
- `bot/app_state.py` — `get_ocr_service()` 返回值类型不变
