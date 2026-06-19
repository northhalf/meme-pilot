# DeepSeek-OCR 替换 PaddleOCR 设计

> 日期：2026-06-19
> 状态：待审阅

---

## 1. 目标

将 OCR 引擎从 **PaddleOCR**（本地部署）替换为 **硅基流动 DeepSeek-OCR**（API 调用），解决以下问题：

- **简化部署**：不再需要 PaddlePaddle / PaddleOCR Python 包，不需要 OpenGL 系统库（`libgl1-mesa-glx` 等）
- **减小镜像体积**：移除 paddle 系列依赖后 Docker 镜像显著减小
- **统一 API 供应商**：OCR 和 Embedding 都走硅基流动，只需维护 `SILICONFLOW_API_KEY` 一个 Key
- **降低维护成本**：无需管理本地 OCR 模型版本

---

## 2. 总体架构

### 2.1 变更前（PaddleOCR 方案）

```
┌────────────────────────────────────┐
│            bot 容器                 │
│                                    │
│  index_manager.py                  │
│    │                               │
│    ├── OcrProvider                  │
│    │     └── ocr_service.py        │
│    │           └── PaddleOCR 本地   │
│    │                ├── paddlepaddle│
│    │                ├── paddleocr   │
│    │                └── OpenGL libs │
│    │                               │
│    └── EmbeddingProvider            │
│          └── ai_matcher.py         │
│                └── SiliconFlow API  │
│                                    │
│  系统依赖（Dockerfile 需安装）:     │
│  libgl1-mesa-glx libglib2.0-0     │
│  libsm6 libxext6 libxrender-dev   │
│  libgomp1                          │
└────────────────────────────────────┘
```

### 2.2 变更后（DeepSeek-OCR 方案）

```
┌────────────────────────────────────┐
│            bot 容器                 │
│                                    │
│  index_manager.py                  │
│    │                               │
│    ├── OcrProvider                  │
│    │     └── ocr_service.py        │
│    │        DeepSeekOcrService      │
│    │          └── AsyncOpenAI ─────┼──► SiliconFlow API
│    │               (chat/completions)  deepseek-ocr
│    │                               │
│    └── EmbeddingProvider            │
│          └── ai_matcher.py ───────┼──► SiliconFlow API
│                  AsyncOpenAI          (embeddings)
│                                    │
│  零系统依赖（仅需 Python 运行时）   │
└────────────────────────────────────┘
```

**核心变化**：OCR 和 Embedding 都通过 `openai.AsyncOpenAI` 客户端走硅基流动 API，共用 `SILICONFLOW_API_KEY`。Docker 镜像不需要额外系统库。

---

## 3. 模块设计

### 3.1 `bot/engine/ocr_service.py`（新建）

实现 `index_manager.OcrProvider` 协议，类名 `DeepSeekOcrService`。

```python
class DeepSeekOcrService:
    """DeepSeek-OCR 服务，通过硅基流动 API 进行图片文字识别。

    实现 index_manager.OcrProvider 协议。
    """

    MIME_MAP: dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }

    OCR_PROMPT = "<image>\n<|grounding|>OCR this image."

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None: ...

    async def ocr(self, image_path: str) -> str: ...
```

**初始化逻辑**：
- `api_key` 默认从 `SILICONFLOW_API_KEY` 环境变量读取
- `base_url` 默认从 `SILICONFLOW_BASE_URL` 环境变量读取，回退至 `https://api.siliconflow.cn/v1`
- `model` 默认从 `SILICONFLOW_OCR_MODEL` 环境变量读取，回退至 `deepseek-ocr`
- 创建 `AsyncOpenAI` 客户端实例

**`ocr()` 方法流程**：
1. 检查文件是否存在 → 不存在抛出 `FileNotFoundError`
2. 检查扩展名是否在 `MIME_MAP` 中 → 不支持抛出 `ValueError`
3. 读取图片二进制 → base64 编码 → 构造 `data:<mime>;base64,...` data URL
4. 构造 chat completions 请求（多模态 vision 格式）
5. 调用 API，返回 `response.choices[0].message.content`
6. API 异常包装为 `RuntimeError`

### 3.2 协议兼容性

`index_manager.py` 已定义 `OcrProvider` 协议：

```python
class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str: ...
```

`DeepSeekOcrService.ocr()` 签名完全匹配，可直接注入给 `IndexManager`。

---

## 4. 数据流

```
index_manager.sync_with_filesystem()
  │
  ├── 扫描 memes/ → 发现新增图片 cat.jpg
  │
  └── ocr_provider.ocr("memes/cat.jpg")
        │
        ▼
      DeepSeekOcrService.ocr()
        │
        ├── 1. 读取 cat.jpg → bytes
        ├── 2. base64 编码
        ├── 3. 构造 data URL: "data:image/jpeg;base64,/9j/4AAQ..."
        ├── 4. POST https://api.siliconflow.cn/v1/chat/completions
        │      model: "deepseek-ocr"
        │      messages: [
        │        { role: "user", content: [
        │          { type: "image_url", image_url: { url: "data:image/jpeg;base64,..." } },
        │          { type: "text", text: "<image>\n<|grounding|>OCR this image." }
        │        ]}
        │      ]
        │
        ├── 5. 响应: { choices: [{ message: { content: "一只猫在跳起来抓蝴蝶" } }] }
        │
        └── 6. 返回 "一只猫在跳起来抓蝴蝶"
```

---

## 5. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SILICONFLOW_API_KEY` | （必填） | 硅基流动 API Key，OCR 和 Embedding 共用 |
| `SILICONFLOW_BASE_URL` | `https://api.siliconflow.cn/v1` | API 地址 |
| `SILICONFLOW_OCR_MODEL` | `deepseek-ai/DeepSeek-OCR` | **新增** — OCR 模型名（可通过环境变量覆盖） |

不需要新增 API Key，OCR 和 Embedding 共用同一个 `SILICONFLOW_API_KEY`。

---

## 6. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 图片文件不存在 | `FileNotFoundError`，由调用方处理 |
| 不支持的图片格式 | `ValueError("不支持的图片格式: .xxx")` |
| DeepSeek-OCR API 网络异常 | `RuntimeError("DeepSeek-OCR API 调用失败: ...")` |
| API 返回空内容 | 返回空字符串 `""` |
| `SILICONFLOW_API_KEY` 未设置 | API 调用时服务端返回 401，被 `except Exception` 捕获为 `RuntimeError` |

调用方 `index_manager.sync_with_filesystem()` 的 `except Exception` 会捕获以上所有异常，将失败文件名记录到 `SyncResult.failed` 列表。

---

## 7. 依赖变更

| 操作 | 包 | 原因 |
|------|-----|------|
| 添加 | `openai>=1.0.0` | DeepSeek-OCR 和 DeepSeek 精排均需 |
| 添加 | `httpx>=0.27.0` | openai SDK 底层依赖 |
| 不添加 | `paddlepaddle>=2.6.0` | 不再使用 PaddleOCR |
| 不添加 | `paddleocr>=2.8.0` | 不再使用 PaddleOCR |
| 不需要 | `libgl1-mesa-glx` 等 6 个系统包 | 不再需要 OpenGL（Dockerfile 创建时不写） |

注意：当前 `pyproject.toml` 尚未添加 paddle 系列包，只需确保不添加即可。

---

## 8. 文档变更清单

| 文件 | 变更内容 |
|------|----------|
| `docs/PRD.md` | 技术栈 OCR 引擎 → DeepSeek-OCR；移除 paddle 依赖和系统依赖；更新边界情况措辞 |
| `CONTEXT.md` | 术语表：PaddleOCR → DeepSeek-OCR |
| `README.md` | PaddleOCR 引用 → DeepSeek-OCR |
| `docs/API.md` | 添加 `ocr_service.py` 接口文档；更新第 4 节未实现模块 |
| `docs/process.md` | 记录 `ocr_service.py` 完成 |
| `.env.example` | 新增 `SILICONFLOW_OCR_MODEL` |
| `docker-compose.yml` | 添加 `SILICONFLOW_OCR_MODEL` 环境变量 |
| `pyproject.toml` | 添加 `openai>=1.0.0`、`httpx>=0.27.0` |

---

## 9. 测试策略

文件：`tests/unit/engine/test_ocr_service.py`（新建）

| 测试用例 | 验证内容 |
|----------|----------|
| `test_ocr_success` | Mock `AsyncOpenAI`，验证正常返回 OCR 文本 |
| `test_ocr_file_not_found` | 图片不存在时抛出 `FileNotFoundError` |
| `test_ocr_unsupported_format` | 不支持扩展名时抛出 `ValueError` |
| `test_ocr_api_error` | API 异常时抛出 `RuntimeError` |
| `test_ocr_empty_response` | API 返回空 content 时返回 `""` |
| `test_default_model_from_env` | 未传 model 参数时从环境变量读取 |
| `test_default_base_url_from_env` | 未传 base_url 参数时从环境变量读取 |
| `test_custom_params` | 传入自定义 api_key/base_url/model 时使用传入值 |

Mock 策略：使用 `unittest.mock.AsyncMock` + `patch.object` 替换 `DeepSeekOcrService._client.chat.completions.create`。

---

## 10. 性能影响

| 指标 | PaddleOCR（旧） | DeepSeek-OCR（新） |
|------|----------------|-------------------|
| 单张图片耗时 | ~3–5s（CPU 推理） | ~2–5s（API 网络调用） |
| 100 张建索引 | 5–10 分钟 | 3–8 分钟 |
| 内存占用 | 高（加载 ML 模型） | 低（仅 base64 编码） |
| Docker 镜像增量 | +~2GB（paddle 系列） | +0（仅纯 Python 包） |
