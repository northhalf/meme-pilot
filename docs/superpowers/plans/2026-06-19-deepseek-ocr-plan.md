# DeepSeek-OCR Implementation Plan

> **Date:** 2026-06-19
> **Spec:** [2026-06-19-deepseek-ocr-design.md](../specs/2026-06-19-deepseek-ocr-design.md)
> **顺序：先文档，后代码**

---

## Task 1: 更新 pyproject.toml 依赖

**Files:** `pyproject.toml`

添加 `openai>=1.0.0` 和 `httpx>=0.27.0`。不添加 paddle 系列。

- [ ] `uv add openai httpx`

---

## Task 2: 更新 .env.example

**Files:** `.env.example`

新增 OCR 模型配置：

```
# SiliconFlow OCR 模型（用于图片文字识别）
SILICONFLOW_OCR_MODEL=deepseek-ocr
```

---

## Task 3: 更新 docker-compose.yml

**Files:** `docker-compose.yml`

在 bot 服务的 environment 中添加：

```yaml
- SILICONFLOW_OCR_MODEL=${SILICONFLOW_OCR_MODEL:-deepseek-ocr}
```

---

## Task 4: 更新 docs/PRD.md

**Files:** `docs/PRD.md`

逐处替换 PaddleOCR → DeepSeek-OCR：

| 行号区域 | 原文 | 改后 |
|---------|------|------|
| 63 | `PaddleOCR \| 中文 OCR` | `DeepSeek-OCR（硅基流动）\| 视觉 OCR` |
| 320 | `PaddleOCR 约 5s/张` | `DeepSeek-OCR API 调用约 3s/张` |
| 361 | `PaddleOCR 初始化失败` | `DeepSeek-OCR API 调用失败` |
| 427 | `# PaddleOCR 封装` | `# DeepSeek-OCR 封装（硅基流动 API）` |
| 446–447 | `paddlepaddle>=2.6.0` 和 `paddleocr>=2.8.0` | 移除这两行 |
| 459–461 | `libgl1-mesa-glx ...` 系统依赖 | 移除整个代码块（"系统依赖（Dockerfile 中安装）"段落） |

---

## Task 5: 更新 CONTEXT.md

**Files:** `CONTEXT.md`

术语表：
- 删除 `| **PaddleOCR** | ... |`
- 新增 `| **DeepSeek-OCR** | 硅基流动上的视觉 OCR 模型（`deepseek-ocr`），通过 chat completions API 调用，用于从图片中提取文字 |`

---

## Task 6: 更新 README.md

**Files:** `README.md`

- 第 99 行：`PaddleOCR` → `DeepSeek-OCR`
- 第 191 行：`PaddleOCR` → `DeepSeek-OCR / 硅基流动`
- 第 220 行：`[PaddleOCR]...OCR 引擎` → `[DeepSeek-OCR]...视觉 OCR 模型（硅基流动）`

---

## Task 7: 更新 docs/api/API.md

**Files:** `docs/api/API.md`

1. 第 4 节未实现模块表：`ocr_service.py` 依赖从 `PaddleOCR` → `SiliconFlow API (OpenAI SDK)`
2. 新增 `ocr_service.py` 对外接口文档（在 `keyword_searcher.py` 和 `logging_config.py` 文档之后）

新增内容约：`DeepSeekOcrService` 类，`__init__` 参数，`ocr()` 方法签名。

---

## Task 8: 更新 docs/process.md

**Files:** `docs/process.md`

追加：
```markdown
- [x] `bot/engine/ocr_service.py` — DeepSeek-OCR 封装（硅基流动 vision API，base64 图片输入，异步 OCR）
```

---

## Task 9: 新建 bot/engine/ocr_service.py

**Files:** `bot/engine/ocr_service.py`（新建）

按 spec 第 3 节实现 `DeepSeekOcrService` 类。

关键点：
- `AsyncOpenAI` 客户端，base_url 默认 `https://api.siliconflow.cn/v1`
- `model` 默认 `deepseek-ocr`
- `ocr()` 方法：文件检查 → MIME 映射 → base64 编码 → API 调用 → 返回文本
- Google 风格中文 docstring
- 类型标注完整

---

## Task 10: 验证

- [ ] `uv run python -m compileall bot` — 编译检查
- [ ] `uv run python -c "from bot.engine.ocr_service import DeepSeekOcrService; print('import ok')"` — 导入检查
- [ ] `uv run pytest tests/ -v` — 已有测试回归检查
