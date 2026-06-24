# image_optimizer.py 设计文档

## 概述

实现 `bot/engine/image_optimizer.py`，对表情包图片执行无损压缩，在进入索引前减小文件体积。

## 需求来源

- PRD：表情包进入索引前的文件优化步骤
- CONTEXT.md："新增图片进入索引前的文件优化步骤...对新增的 .jpg/.jpeg/.png/.webp/.gif 尝试无损压缩，成功后覆盖原文件"

## 调用时机

| 场景 | 触发点 |
|------|--------|
| `/add` 命令 | 下载图片后、OCR 前 |
| 启动同步 | 新发现的未索引图片 |
| `/refresh` 命令 | 增量同步中的新图片 |

流程：下载/发现 → 压缩 → 覆盖原文件 → OCR → Embedding → 入索引

## 支持格式

| 格式 | 处理方式 |
|------|---------|
| `.jpg` / `.jpeg` | 去除 EXIF/元数据 + 高质量重编码 |
| `.png` | 去除元数据 + 重新压缩（真正无损） |
| `.webp` | 无损模式重编码 |
| `.gif` | 去除冗余元数据，保留全部帧和动画 |
| `.bmp` | 跳过，不处理 |

## 架构方案

采用**方案 A：单类 + 私有方法分发**。所有逻辑集中在 `ImageOptimizer` 类中，通过文件后缀分发到对应的私有压缩方法。

理由：当前仅 5 种格式且 PRD 明确列出，不存在扩展需求。代码量少、可读性高，与 engine 模块的务实风格一致。

## 类设计

### ImageOptimizer

```python
class ImageOptimizer:
    """图片无损压缩器。

    对 .jpg/.jpeg/.png/.webp/.gif 文件执行无损压缩，
    成功后覆盖原文件。.bmp 文件跳过压缩。

    Attributes:
        _jpeg_quality: JPEG 重编码质量（默认 95）。
        _webp_quality: WebP 无损压缩质量（默认 80）。
    """

    COMPRESSIBLE: frozenset[str] = frozenset({
        ".jpg", ".jpeg", ".png", ".webp", ".gif",
    })
    PASS_THROUGH: frozenset[str] = frozenset({".bmp"})

    def __init__(
        self,
        jpeg_quality: int = 95,
        webp_quality: int = 80,
    ) -> None: ...

    async def optimize(self, image_path: str | Path) -> OptimizeResult: ...
    async def _compress_jpeg(self, path: Path) -> int: ...
    async def _compress_png(self, path: Path) -> int: ...
    async def _compress_webp(self, path: Path) -> int: ...
    async def _compress_gif(self, path: Path) -> int: ...
    async def _atomic_save(self, img: Image, path: Path, **save_kwargs) -> int: ...
```

### OptimizeResult

```python
@dataclass(frozen=True, slots=True)
class OptimizeResult:
    """图片压缩结果。

    Attributes:
        original_size: 原始文件大小（字节）。
        optimized_size: 压缩后文件大小（字节）。
        saved: 节省的字节数。
        skipped: 是否跳过压缩（如 .bmp 或压缩后反而变大）。
    """
    original_size: int
    optimized_size: int
    saved: int
    skipped: bool = False
```

## 各格式压缩策略

### JPEG

- 使用 `Pillow.Image.open()` 读取
- 转为 RGB 模式（去除 alpha 通道，JPEG 不支持）
- 以 `quality=95, optimize=True, progressive=True` 重编码
- EXIF/ICC 等元数据自动去除（不传入 `exif` 参数）

### PNG

- 使用 `Pillow.Image.open()` 读取
- 以 `optimize=True` 重新保存（使用更好的 deflate 压缩）
- 像素数据完全不变，仅优化压缩参数

### WebP

- 使用 `Pillow.Image.open()` 读取
- 以 `lossless=True, quality=80, method=6` 保存
- `method=6` 为最高压缩比（速度最慢，但仅执行一次）

### GIF

- 使用 `Pillow.Image.open()` 读取
- 逐帧复制到新 Image 对象，保留 `info` 中的 `duration`、`loop`、`transparency` 等动画属性
- 去除 comment/extensions 等冗余元数据
- 以 `optimize=True` 保存

### BMP

- 直接跳过，返回 `OptimizeResult(skipped=True)`

## 原子写入

与 `index_manager.py` 一致的原子写入模式：

1. 将压缩后的图片写入 `{原文件名}.tmp`
2. 成功后 `os.replace(tmp_path, original_path)` 覆盖原文件
3. 失败时清理 `.tmp` 文件并抛出 `RuntimeError`

## 错误处理

| 场景 | 行为 |
|------|------|
| 文件不存在 | 抛出 `FileNotFoundError` |
| 不支持的格式 | 抛出 `ValueError` |
| Pillow 读取/写入失败 | 抛出 `RuntimeError`，附带原始异常 |
| 压缩后反而变大 | 不抛异常，保留原文件，返回 `skipped=True` |

## 日志模式

```python
logger = logging.getLogger(__name__)

# 压缩成功
logger.debug("压缩完成: %s (%d → %d, 节省 %.1f%%)", name, orig, opt, pct)

# 跳过（bmp 或压缩后变大）
logger.debug("跳过压缩: %s (节省 0 字节)", name)

# 压缩失败（由调用方决定日志级别）
raise RuntimeError(f"图片压缩失败: {path.name}") from exc
```

使用 `%s` 风格，与 engine 其他模块一致。

## 依赖

- `Pillow` — 需通过 `uv add Pillow` 安装
- 无系统依赖（纯 Python 实现）

## 集成点

### 与 IndexManager 的集成

`IndexManager` 通过依赖注入使用 `ImageOptimizer`，与 `OcrProvider` 模式一致：

1. `IndexManager.__init__()` 新增可选参数 `optimizer: ImageOptimizer | None = None`
2. `_process_new_file()` 中在 OCR 前调用压缩：

```python
# 当前流程
text = await self._ocr_provider.ocr(str(image_path))

# 改为
if self._optimizer is not None:
    await self._optimizer.optimize(str(image_path))
text = await self._ocr_provider.ocr(str(image_path))
```

3. 压缩失败时抛出 `RuntimeError`，由 `_sync_additions()` 的 `asyncio.gather(return_exceptions=True)` 捕获，记入 `failed` 列表，不影响其他图片。

### 新文件发现机制

`/refresh` 和启动同步的新文件发现逻辑（已有，无需修改）：

```
sync_with_filesystem()
  ├── _scan_meme_files()         → 扫描 memes/ 得到现有文件名集合
  ├── _build_filename_to_id()    → 从 index.json 得到已索引的 filename→id 映射
  └── _sync_additions()
        └── new_files = [f for f in existing_files if f not in filename_to_id]
              └── _process_new_file(filename)
                    ├── optimize(path)   ← 新增：OCR 前压缩
                    ├── ocr(path)
                    └── embed(text)
```

### 与 /add 插件的集成

`/add` 插件在下载图片后、调用 `index_manager.add_entry()` 前，需先调用 `optimizer.optimize()`。压缩失败时删除已下载图片并回复失败原因。

### 需同步更新的文件

1. `bot/engine/__init__.py` — 添加 `ImageOptimizer`、`OptimizeResult` 到导入和 `__all__`
2. `bot/engine/index_manager.py` — `__init__` 新增 `optimizer` 参数，`_process_new_file` 插入压缩调用
3. `bot/app_state.py` — `init_app()` 新增 `ImageOptimizer` 参数，`get_image_optimizer()` 获取函数
4. `bot/plugins/meme_add.py`（未来实现）— 下载后调用压缩
5. `docs/process.md` — 记录模块完成
6. `docs/api/API.md` — 添加接口文档

## 测试策略

- 单元测试：对每种格式的小样本图片执行压缩，验证文件大小变化和 `OptimizeResult` 返回值
- 边界测试：不存在的文件、不支持的格式、压缩后变大的小图片
- GIF 动画测试：验证压缩后 GIF 仍可正常播放（帧数和 duration 不变）
