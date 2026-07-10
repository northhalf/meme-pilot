# bot/engine/image_optimizer.py — 图片压缩/转换 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数不在此列出。

## `OptimizeResult` 数据类

```python
@dataclass(frozen=True, slots=True)
```

图片压缩/转换结果。

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `original_size` | `int` | — | 原始文件大小（字节） |
| `optimized_size` | `int` | — | 压缩/转换后文件大小（字节） |
| `saved` | `int` | — | 节省的字节数 |
| `skipped` | `bool` | `False` | 是否跳过压缩（如 .bmp 或压缩后反而变大） |
| `output_path` | `str` | `""` | 最终文件路径（同格式压缩=原路径；转 WebP=新 `.webp` 路径） |

---

## `ImageOptimizer` 类

图片压缩/转换器。`should_convert_to_webp=True` 时将 `.jpg/.jpeg/.png/.gif/.bmp` 转为有损 WebP（q85）；`should_convert_to_webp=False` 时对 `.jpg/.jpeg/.png/.webp/.gif` 执行同格式无损压缩，`.bmp` 跳过。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `COMPRESSIBLE` | `frozenset[str]` | 可压缩格式：`{".jpg", ".jpeg", ".png", ".webp", ".gif"}` |
| `PASS_THROUGH` | `frozenset[str]` | 跳过格式：`{".bmp"}` |

---

### `__init__(lossy_quality: int = 85, webp_quality: int = 80, concurrency: int | None = None, should_convert_to_webp: bool = False) -> None`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `lossy_quality` | `int` | `85` | 有损编码质量（1-100，用于 JPEG 与有损 WebP） |
| `webp_quality` | `int` | `80` | WebP 无损压缩质量（0-100），仅 `should_convert_to_webp=False` 时用于 `.webp` 源 |
| `concurrency` | `int \| None` | `None` | 图片压缩并发上限，默认从 `COMPRESS_CONCURRENCY` 环境变量读取，回退为 5。使用 `asyncio.Semaphore` 限制并发 optimize() 调用数，防止 `asyncio.to_thread` 线程池耗尽。 |
| `should_convert_to_webp` | `bool` | `False` | 是否将图片转为 WebP（`True` 时强制转有损 WebP q85；`False` 时同格式无损压缩）。`bot.py` startup 通过 `read_convert_to_webp()` 注入。 |

---

### `async optimize(image_path: str | Path) -> OptimizeResult`

按 `should_convert_to_webp` 开关执行图片压缩/转换，返回 `OptimizeResult`（含 `output_path`）。

**`should_convert_to_webp=True` 时（转 WebP）：**

| 格式 | 策略 |
|------|------|
| `.webp` | 有损重编码（`quality=lossy_quality, method=6`），变小才覆盖原文件，变大返回 `skipped=True` |
| `.jpg` / `.jpeg` / `.png` / `.gif` / `.bmp` | 强制转为有损 WebP（`quality=lossy_quality, method=6`），不比较体积；透明通道保留（P/RGBA -> RGBA）；GIF 动图保留 duration/loop 转 animated WebP；成功后删除原文件，返回 `output_path` 为新 `.webp` 路径 |

**`should_convert_to_webp=False` 时（同格式压缩）：**

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
| **返回** | `OptimizeResult` | 压缩/转换结果，含 `output_path`（同格式=原路径；转 WebP=新 `.webp` 路径） |
| **异常** | `FileNotFoundError` | 文件不存在 |
| | `ValueError` | 不支持的文件格式 |
| | `RuntimeError` | 压缩/转换过程失败 |

**行为说明：**

- 原子写入：先写入 `.tmp` 临时文件，成功后 `os.replace()` 覆盖原文件
- 同格式压缩后反而变大时保留原文件，返回 `skipped=True`
- 转 WebP 时强制转换不比较体积；目标 `.webp` 已存在则追加 `_n` 序号（`resolve_unique_filename`）
- 转换失败时 `_convert_image_to_webp` 内部清理临时文件与已生成 `.webp` 孤儿，原文件保留，抛 `RuntimeError`
- `should_convert_to_webp=False` 时 `.bmp` 文件直接跳过，返回 `skipped=True`
