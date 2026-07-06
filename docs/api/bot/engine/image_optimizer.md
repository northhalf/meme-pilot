# bot/engine/image_optimizer.py — 图片无损压缩 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `OptimizeResult` 数据类

```python
@dataclass(frozen=True, slots=True)
```

图片压缩结果。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `original_size` | `int` | — | 原始文件大小（字节） |
| `optimized_size` | `int` | — | 压缩后文件大小（字节） |
| `saved` | `int` | — | 节省的字节数 |
| `skipped` | `bool` | `False` | 是否跳过压缩（如 .bmp 或压缩后反而变大） |

---

## `ImageOptimizer` 类

图片无损压缩器。对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，成功后覆盖原文件。.bmp 文件跳过压缩。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `COMPRESSIBLE` | `frozenset[str]` | 可压缩格式：`{".jpg", ".jpeg", ".png", ".webp", ".gif"}` |
| `PASS_THROUGH` | `frozenset[str]` | 跳过格式：`{".bmp"}` |

---

### `__init__(jpeg_quality: int = 85, webp_quality: int = 80, concurrency: int | None = None) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `jpeg_quality` | `int` | `85` | JPEG 重编码质量（1-100） |
| `webp_quality` | `int` | `80` | WebP 无损压缩质量（0-100） |
| `concurrency` | `int \| None` | `None` | 图片压缩并发上限，默认从 `COMPRESS_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 optimize() 调用数，防止 `asyncio.to_thread` 线程池耗尽。 |

---

### `async optimize(image_path: str | Path) -> OptimizeResult`

尝试无损压缩图片，成功后覆盖原文件。

各格式压缩策略：

| 格式 | 策略 |
|------|------|
| `.jpg` / `.jpeg` | 去除 EXIF/元数据 + 高质量重编码（`quality=85, optimize=True, progressive=True`） |
| `.png` | 去除元数据 + 重新压缩（`optimize=True`，真正无损） |
| `.webp` | 无损模式重编码（`lossless=True, quality=80, method=6`） |
| `.gif` | 去除冗余元数据，保留全部帧和动画（`optimize=True, save_all=True`） |
| `.bmp` | 跳过，返回 `OptimizeResult(skipped=True)` |

| | 类型 | 说明 |
|--|------|------|
| **参数** `image_path` | `str \| Path` | 图片文件路径 |
| **返回** | `OptimizeResult` | 压缩结果，包含大小变化信息 |
| **异常** | `FileNotFoundError` | 文件不存在 |
| | `ValueError` | 不支持的文件格式 |
| | `RuntimeError` | 压缩过程失败 |

**行为说明：**

- 原子写入：先写入 `.tmp` 临时文件，成功后 `os.replace()` 覆盖原文件
- 压缩后反而变大时保留原文件，返回 `skipped=True`
- `.bmp` 文件直接跳过，返回 `skipped=True`
