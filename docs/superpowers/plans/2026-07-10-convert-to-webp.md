# 图片默认转 WebP 存储 + 迁移脚本 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增图片默认转有损 q85 WebP 存储（`/add`、启动 sync、`/refresh`），提供 `CONVERT_TO_WEBP` 开关与存量图迁移脚本。

**Architecture:** 转换职责集中在 `ImageOptimizer`（`optimize` 按开关决定转换或同格式压缩，返回最终路径 `output_path`）；`IndexManager._process_image_pipeline` 回传 `final_filename` 贯穿 sqlite 写入，转换失败降级保留原格式；`resolve_unique_filename` 迁至 `utils` 共用；迁移脚本参考 `png_to_jpg.py` 范式。

**Tech Stack:** Python 3.12、Pillow（WebP 编码）、pytest + pytest-asyncio、sqlite3、uv。

> **⚠️ 项目约束（CLAUDE.md）：** 禁止自行在 main 分支 `git add/commit`。每个 Task 末尾的 commit 步骤为建议命令，**须经用户审核后由用户执行**；实现者完成代码与测试后，标记 Task 完成并等待用户审核。

**关联 spec：** `docs/superpowers/specs/2026-07-10-convert-to-webp-design.md`

---

## 文件结构

| 文件 | 职责 | 改动 |
|------|------|------|
| `bot/engine/utils.py` | 共用工具（`vector_norm` + 迁入 `resolve_unique_filename`） | 改 |
| `bot/config.py` | 配置读取（新增 `read_convert_to_webp`） | 改 |
| `bot/engine/image_optimizer.py` | 图片压缩/转换（`OptimizeResult.output_path`、`convert_to_webp`、`_convert_to_webp`） | 改 |
| `bot/engine/index_manager.py` | 管道编排（`_process_image_pipeline` 返回 `final_filename` + 降级、add/sync 流转） | 改 |
| `bot/bot.py` | startup 注入 `convert_to_webp` | 改 |
| `scripts/convert_memes_to_webp.py` | 存量图迁移脚本 | 新增 |
| `.env.example` / `docker-compose.yml` | `CONVERT_TO_WEBP` 环境变量 | 改 |
| `README.md` / `docs/PRD.md` / `CONTEXT.md` / `docs/api/API.md` | 文档同步 | 改 |
| `tests/unit/engine/test_image_optimizer.py` | image_optimizer 测试 | 改 |
| `tests/unit/engine/test_index_manager.py` | index_manager pipeline 测试 | 改 |
| `tests/unit/engine/test_utils.py` | utils 测试 | 新增 |
| `tests/unit/test_config.py` | config 测试 | 改 |
| `tests/unit/test_convert_memes_to_webp.py` | 迁移脚本测试 | 新增 |

---

## Task 1: `config.read_convert_to_webp()`

**Files:**
- Modify: `bot/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_config.py` 末尾追加，并在顶部 import 中加 `read_convert_to_webp`：

```python
class TestReadConvertToWebp:
    def test_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONVERT_TO_WEBP", raising=False)
        assert read_convert_to_webp() is True

    def test_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "false")
        assert read_convert_to_webp() is False

    def test_no_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "no")
        assert read_convert_to_webp() is False

    def test_zero_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "0")
        assert read_convert_to_webp() is False

    def test_yes_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "yes")
        assert read_convert_to_webp() is True

    def test_one_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "1")
        assert read_convert_to_webp() is True

    def test_invalid_fallback_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "maybe")
        assert read_convert_to_webp() is True

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERT_TO_WEBP", "  False  ")
        assert read_convert_to_webp() is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/test_config.py::TestReadConvertToWebp -v`
Expected: FAIL（`ImportError: cannot import name 'read_convert_to_webp'`）

- [ ] **Step 3: 实现 `read_convert_to_webp`**

在 `bot/config.py` 的 `read_ocr_text_score` 之后追加：

```python
def read_convert_to_webp() -> bool:
    """从环境变量读取是否将新增图片转为 WebP。

    开关开启时（默认）新增图片转有损 WebP；关闭时按传输格式存储（现状）。
    "false"/"0"/"no" 返回 False，其余无效值回退 True（默认开启）。

    Returns:
        bool:是否转 WebP。
    """
    raw = os.environ.get("CONVERT_TO_WEBP", "true").strip().lower()
    if raw in ("false", "0", "no"):
        return False
    return True
```

在 `bot/config.py` 的 `__all__` 列表中追加 `"read_convert_to_webp"`。

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/test_config.py::TestReadConvertToWebp -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交（需用户审核）**

```bash
git add bot/config.py tests/unit/test_config.py
git commit -m "feat(config): 新增 read_convert_to_webp 读取 CONVERT_TO_WEBP 开关"
```

⚠️ 需用户审核后执行。

---

## Task 2: `resolve_unique_filename` 迁至 `utils.py`

**Files:**
- Modify: `bot/engine/utils.py`
- Modify: `bot/engine/index_manager.py`（删除定义，改从 utils 导入）
- Test: `tests/unit/engine/test_utils.py`（新增）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/engine/test_utils.py`：

```python
"""bot.engine.utils 工具函数测试。"""

from pathlib import Path

from bot.engine.utils import resolve_unique_filename, vector_norm


def test_vector_norm() -> None:
    assert vector_norm([3.0, 4.0]) == 5.0


class TestResolveUniqueFilename:
    def test_no_conflict(self, tmp_path: Path) -> None:
        assert resolve_unique_filename(tmp_path, "a.webp") == tmp_path / "a.webp"

    def test_appends_1(self, tmp_path: Path) -> None:
        (tmp_path / "a.webp").write_bytes(b"x")
        assert resolve_unique_filename(tmp_path, "a.webp") == tmp_path / "a_1.webp"

    def test_appends_2(self, tmp_path: Path) -> None:
        (tmp_path / "a.webp").write_bytes(b"x")
        (tmp_path / "a_1.webp").write_bytes(b"x")
        assert resolve_unique_filename(tmp_path, "a.webp") == tmp_path / "a_2.webp"

    def test_preserves_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "meme_abc.jpg").write_bytes(b"x")
        result = resolve_unique_filename(tmp_path, "meme_abc.jpg")
        assert result == tmp_path / "meme_abc_1.jpg"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_utils.py -v`
Expected: FAIL（`ImportError: cannot import name 'resolve_unique_filename'`）

- [ ] **Step 3: 在 `utils.py` 实现 `resolve_unique_filename`**

将 `bot/engine/utils.py` 全文替换为：

```python
"""engine 包公共工具函数。"""

import itertools
import math
from pathlib import Path

__all__ = ["vector_norm", "resolve_unique_filename"]


def vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))


def resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。

    Returns:
        目标目录下不冲突的完整路径。
    """
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for n in itertools.count(1):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")
```

- [ ] **Step 4: `index_manager.py` 改为从 utils 导入**

在 `bot/engine/index_manager.py`：
- 删除 `resolve_unique_filename` 函数定义（约 34-53 行的整个函数）。
- 在 import 区（现有 `from .utils import vector_norm` 处）改为：

```python
from .utils import resolve_unique_filename, vector_norm
```

注意：`index_manager` 模块通过该 import 绑定 `resolve_unique_filename` 名称，现有 `tests/unit/engine/test_index_manager.py` 的 `from bot.engine.index_manager import resolve_unique_filename` 仍可用，无需改动。

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_utils.py tests/unit/engine/test_index_manager.py -v`
Expected: PASS（test_utils 全过；test_index_manager 现有用例不受影响）

- [ ] **Step 6: 提交（需用户审核）**

```bash
git add bot/engine/utils.py bot/engine/index_manager.py tests/unit/engine/test_utils.py
git commit -m "refactor(engine): resolve_unique_filename 迁至 utils 共用"
```

⚠️ 需用户审核后执行。

---

## Task 3: `OptimizeResult.output_path` + `convert_to_webp` 参数

**Files:**
- Modify: `bot/engine/image_optimizer.py`
- Test: `tests/unit/engine/test_image_optimizer.py`

本 Task 仅加字段与参数，`optimize` 在所有返回点设 `output_path=str(path)`（行为不变，为后续转换铺路）。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_image_optimizer.py` 的 `TestOptimizeResult` 类中追加：

```python
    def test_output_path_default_empty(self) -> None:
        r = OptimizeResult(original_size=1000, optimized_size=800, saved=200)
        assert r.output_path == ""

    def test_output_path_set(self) -> None:
        r = OptimizeResult(
            original_size=1000, optimized_size=800, saved=200, output_path="/x/a.webp"
        )
        assert r.output_path == "/x/a.webp"
```

并在 `TestImageOptimizerEdgeCases` 中追加：

```python
    def test_optimize_returns_output_path_original(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")
        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(jpg))
        assert result.output_path == str(jpg)

    def test_bmp_output_path_original(self, tmp_path: Path) -> None:
        bmp = tmp_path / "t.bmp"
        bmp.write_bytes(b"\x42\x4d" + b"\x00" * 100)
        optimizer = ImageOptimizer()
        result = asyncio.run(optimizer.optimize(bmp))
        assert result.output_path == str(bmp)

    def test_convert_to_webp_param_default_false(self) -> None:
        optimizer = ImageOptimizer()
        assert optimizer._convert_to_webp is False

    def test_convert_to_webp_param_true(self) -> None:
        optimizer = ImageOptimizer(convert_to_webp=True)
        assert optimizer._convert_to_webp is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_image_optimizer.py::TestOptimizeResult::test_output_path_default_empty tests/unit/engine/test_image_optimizer.py::TestImageOptimizerEdgeCases::test_convert_to_webp_param_true -v`
Expected: FAIL（`output_path` 字段不存在 / `_convert_to_webp` 属性不存在）

- [ ] **Step 3: 修改 `OptimizeResult` 与 `__init__`**

在 `bot/engine/image_optimizer.py`：

`OptimizeResult` 增加字段（在 `skipped` 之后）：

```python
@dataclass(frozen=True, slots=True)
class OptimizeResult:
    """图片压缩结果。

    Attributes:
        original_size: 原始文件大小（字节）。
        optimized_size: 压缩后文件大小（字节）。
        saved: 节省的字节数。
        skipped: 是否跳过压缩（如 .bmp 或压缩后反而变大）。
        output_path: 最终文件路径（同格式压缩=原路径；转 WebP=新 .webp 路径）。
    """

    original_size: int
    optimized_size: int
    saved: int
    skipped: bool = False
    output_path: str = ""
```

`__init__` 增加 `convert_to_webp` 参数并存储：

```python
    def __init__(
        self,
        jpeg_quality: int = 85,
        webp_quality: int = 80,
        concurrency: int | None = None,
        convert_to_webp: bool = False,
    ) -> None:
        """初始化 ImageOptimizer。

        Args:
            jpeg_quality: JPEG 重编码质量（1-100），默认 85。
            webp_quality: WebP 质量（0-100），默认 80。
            concurrency: 并发数，默认从 COMPRESS_CONCURRENCY 环境变量读取，回退为 5。
            convert_to_webp: 是否将图片转为 WebP（默认 False，维持现状同格式压缩）。
        """
        self._jpeg_quality = jpeg_quality
        self._webp_quality = webp_quality
        self._convert_to_webp = convert_to_webp

        c = concurrency or int(os.environ.get("COMPRESS_CONCURRENCY", 5))
        self._semaphore = asyncio.Semaphore(c)
```

- [ ] **Step 4: `optimize` 所有返回点设 `output_path=str(path)`**

修改 `bot/engine/image_optimizer.py` 的 `optimize` 方法。在 BMP 跳过、不支持格式、压缩后变大跳过、压缩成功四个返回点，把 `OptimizeResult(...)` 都加上 `output_path=str(path)`。

BMP 跳过分支：

```python
        if suffix in self.PASS_THROUGH:
            size = path.stat().st_size
            logger.debug("跳过压缩: %s (节省 0 字节)", path.name)
            return OptimizeResult(
                original_size=size,
                optimized_size=size,
                saved=0,
                skipped=True,
                output_path=str(path),
            )
```

压缩后变大跳过分支：

```python
            if saved <= 0:
                logger.debug("跳过压缩: %s (压缩后反而变大)", path.name)
                return OptimizeResult(
                    original_size=original_size,
                    optimized_size=optimized_size,
                    saved=0,
                    skipped=True,
                    output_path=str(path),
                )
```

压缩成功分支：

```python
            return OptimizeResult(
                original_size=original_size,
                optimized_size=optimized_size,
                saved=saved,
                output_path=str(path),
            )
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_image_optimizer.py -v`
Expected: PASS（全部用例，含新增）

- [ ] **Step 6: 提交（需用户审核）**

```bash
git add bot/engine/image_optimizer.py tests/unit/engine/test_image_optimizer.py
git commit -m "feat(image_optimizer): OptimizeResult 增加 output_path，新增 convert_to_webp 开关"
```

⚠️ 需用户审核后执行。

---

## Task 4: `_convert_to_webp` 方法 + `optimize` 转换分支

**Files:**
- Modify: `bot/engine/image_optimizer.py`
- Test: `tests/unit/engine/test_image_optimizer.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_image_optimizer.py` 末尾追加：

```python
class TestConvertToWebp:
    """convert_to_webp=True 时各格式转 WebP 测试。"""

    def test_jpg_converted(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (200, 200), color=(128, 64, 32))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG", quality=100)
        optimizer = ImageOptimizer(convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(jpg))
        assert str(result.output_path).endswith(".webp")
        assert Path(result.output_path).exists()
        assert not jpg.exists()
        with Image.open(result.output_path) as w:
            assert w.format == "WEBP"

    def test_png_alpha_preserved(self, tmp_path: Path) -> None:
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        png = tmp_path / "t.png"
        img.save(png, "PNG")
        optimizer = ImageOptimizer(convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(png))
        with Image.open(result.output_path) as w:
            assert w.mode == "RGBA"
        assert not png.exists()

    def test_gif_animated_converted(self, tmp_path: Path) -> None:
        frames = [
            Image.new("RGB", (50, 50), color=(i * 80, 0, 0)).quantize(colors=256)
            for i in range(3)
        ]
        gif = tmp_path / "t.gif"
        frames[0].save(
            gif, save_all=True, append_images=frames[1:], duration=100, loop=0
        )
        optimizer = ImageOptimizer(convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(gif))
        with Image.open(result.output_path) as w:
            assert w.format == "WEBP"
            assert getattr(w, "n_frames", 1) == 3
        assert not gif.exists()

    def test_bmp_converted_when_switch_on(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        bmp = tmp_path / "t.bmp"
        img.save(bmp, "BMP")
        optimizer = ImageOptimizer(convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(bmp))
        assert str(result.output_path).endswith(".webp")
        assert not bmp.exists()

    def test_webp_source_lossy_reencode_when_switch_on(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        webp = tmp_path / "t.webp"
        img.save(webp, "WEBP", lossless=True, quality=100)
        optimizer = ImageOptimizer(convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(webp))
        assert result.output_path == str(webp)
        # 有损重编码：变大则 skipped 保留原文件，变小则覆盖
        with Image.open(webp) as w:
            assert w.format == "WEBP"

    def test_webp_source_lossless_when_switch_off(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        webp = tmp_path / "t.webp"
        img.save(webp, "WEBP")
        optimizer = ImageOptimizer(convert_to_webp=False)
        result = asyncio.run(optimizer.optimize(webp))
        assert result.output_path == str(webp)

    def test_switch_off_keeps_jpg(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (100, 100), color=(10, 20, 30))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")
        optimizer = ImageOptimizer(convert_to_webp=False)
        result = asyncio.run(optimizer.optimize(jpg))
        assert result.output_path == str(jpg)
        assert jpg.exists()
        with Image.open(jpg) as j:
            assert j.format == "JPEG"

    def test_convert_failure_preserves_original(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")

        def fail(_p: Path) -> Path:
            raise RuntimeError("convert fail")

        optimizer = ImageOptimizer(convert_to_webp=True)
        optimizer._convert_to_webp = fail  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="convert fail"):
            asyncio.run(optimizer.optimize(jpg))
        assert jpg.exists()

    def test_target_exists_appends_n(self, tmp_path: Path) -> None:
        img = Image.new("RGB", (50, 50), color=(0, 0, 0))
        jpg = tmp_path / "t.jpg"
        img.save(jpg, "JPEG")
        (tmp_path / "t.webp").write_bytes(b"existing")
        optimizer = ImageOptimizer(convert_to_webp=True)
        result = asyncio.run(optimizer.optimize(jpg))
        assert str(result.output_path).endswith("t_1.webp")
        assert Path(result.output_path).exists()
        assert not jpg.exists()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_image_optimizer.py::TestConvertToWebp -v`
Expected: FAIL（jpg 未转 webp，`output_path` 仍为原路径）

- [ ] **Step 3: 实现 `_convert_to_webp` 与 `optimize` 分支**

在 `bot/engine/image_optimizer.py` 的 `optimize` 方法中，替换「BMP 跳过」到「分发到各格式压缩方法」之间的逻辑。新的 `optimize` 分发部分（从 `suffix = path.suffix.lower()` 之后）：

```python
        suffix = path.suffix.lower()

        # 优先级 1：.webp 源。开关开 -> 有损重编码；开关关 -> 现有 lossless 重编码。
        # 均不改名、遵循「变小才覆盖」。
        if suffix == ".webp":
            async with self._semaphore:
                original_size = path.stat().st_size
                try:
                    if self._convert_to_webp:
                        optimized_size = await asyncio.to_thread(
                            self._compress_webp_lossy, path, original_size
                        )
                    else:
                        optimized_size = await asyncio.to_thread(
                            self._compress_webp, path, original_size
                        )
                except (ValueError, RuntimeError):
                    raise
                except Exception as exc:
                    raise RuntimeError(f"图片压缩失败: {path.name}") from exc
                saved = original_size - optimized_size
                if saved <= 0:
                    return OptimizeResult(
                        original_size=original_size,
                        optimized_size=optimized_size,
                        saved=0,
                        skipped=True,
                        output_path=str(path),
                    )
                return OptimizeResult(
                    original_size=original_size,
                    optimized_size=optimized_size,
                    saved=saved,
                    output_path=str(path),
                )

        # 优先级 2：开关开 + 可转换格式（jpg/jpeg/png/gif/bmp）-> 转 webp（强制，不比较体积）
        if self._convert_to_webp and suffix in frozenset(
            {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
        ):
            async with self._semaphore:
                original_size = path.stat().st_size
                try:
                    new_path = await asyncio.to_thread(
                        self._convert_to_webp, path
                    )
                except (ValueError, RuntimeError):
                    raise
                except Exception as exc:
                    raise RuntimeError(f"图片转换失败: {path.name}") from exc
                optimized_size = new_path.stat().st_size
                return OptimizeResult(
                    original_size=original_size,
                    optimized_size=optimized_size,
                    saved=original_size - optimized_size,
                    output_path=str(new_path),
                )

        # 优先级 3：开关关 或 .bmp -> 现有同格式压缩 / PASS_THROUGH
        # BMP 跳过
        if suffix in self.PASS_THROUGH:
            size = path.stat().st_size
            logger.debug("跳过压缩: %s (节省 0 字节)", path.name)
            return OptimizeResult(
                original_size=size,
                optimized_size=size,
                saved=0,
                skipped=True,
                output_path=str(path),
            )

        # 不支持的格式
        if suffix not in self.COMPRESSIBLE:
            raise ValueError(f"不支持的图片格式: {suffix}")

        # 分发到各格式压缩方法
        async with self._semaphore:
            original_size = path.stat().st_size
            try:
                if suffix in (".jpg", ".jpeg"):
                    optimized_size = await asyncio.to_thread(
                        self._compress_jpeg, path, original_size
                    )
                elif suffix == ".png":
                    optimized_size = await asyncio.to_thread(
                        self._compress_png, path, original_size
                    )
                else:
                    optimized_size = await asyncio.to_thread(
                        self._compress_gif, path, original_size
                    )
            except (ValueError, RuntimeError):
                raise
            except Exception as exc:
                raise RuntimeError(f"图片压缩失败: {path.name}") from exc

            saved = original_size - optimized_size
            if saved <= 0:
                logger.debug("跳过压缩: %s (压缩后反而变大)", path.name)
                return OptimizeResult(
                    original_size=original_size,
                    optimized_size=optimized_size,
                    saved=0,
                    skipped=True,
                    output_path=str(path),
                )
            return OptimizeResult(
                original_size=original_size,
                optimized_size=optimized_size,
                saved=saved,
                output_path=str(path),
            )
```

在类中新增 `_compress_webp_lossy` 与 `_convert_to_webp` 方法（放在 `_compress_webp` 之后）：

```python
    def _compress_webp_lossy(self, path: Path, original_size: int) -> int:
        """有损重编码 WebP（开关开启时用于 .webp 源）。

        Args:
            path: WebP 文件路径。
            original_size: 原始文件大小（字节）。

        Returns:
            最终文件大小（字节）。若重编码后更大则保留原文件并返回原始大小。
        """
        img = Image.open(path)
        try:
            save_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
            return self._atomic_save(
                save_img, path, original_size, format="WEBP", quality=self._jpeg_quality, method=6
            )
        finally:
            img.close()

    def _convert_to_webp(self, path: Path) -> Path:
        """将图片转换为有损 WebP，返回新路径，成功后删除原文件。

        强制转换不比较体积。透明通道保留（P/RGBA 保持 RGBA）。
        GIF 动图保留 duration/loop/transparency 转 animated WebP。
        失败时清理临时文件与已生成 .webp，原文件保留。

        Args:
            path: 源图片路径。

        Returns:
            生成的 WebP 文件路径。

        Raises:
            RuntimeError: 转换失败。
        """
        from .utils import resolve_unique_filename

        target_dir = path.parent
        target = resolve_unique_filename(target_dir, f"{path.stem}.webp")
        tmp_path = target.with_suffix(".webp.tmp")
        try:
            img = Image.open(path)
            try:
                save_kwargs: dict[str, Any] = {
                    "format": "WEBP",
                    "quality": self._jpeg_quality,
                    "method": 6,
                }
                n_frames: int = getattr(img, "n_frames", 1)
                if n_frames > 1:
                    # 动图：提取所有帧，保留 duration/loop。
                    # transparency 保留依赖 Pillow 对 P/RGBA 帧的 WEBP 转换，若丢失需调整。
                    frames: list[Image.Image] = []
                    for i in range(n_frames):
                        img.seek(i)
                        frames.append(img.copy())
                    if "duration" in img.info:
                        save_kwargs["duration"] = img.info["duration"]
                    if "loop" in img.info:
                        save_kwargs["loop"] = img.info["loop"]
                    frames[0].save(
                        tmp_path, append_images=frames[1:], save_all=True, **save_kwargs
                    )
                    for f in frames:
                        f.close()
                else:
                    # 静态图：保留透明（P/RGBA 保持），否则转 RGB
                    save_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
                    save_img.save(tmp_path, **save_kwargs)
            finally:
                img.close()
            os.replace(tmp_path, target)
            # 转换成功后删除原文件（若与目标不同）
            if path.resolve() != target.resolve():
                path.unlink(missing_ok=True)
            return target
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            # 若 .webp 已生成但后续失败，清理孤儿
            if target.exists() and path.exists():
                target.unlink(missing_ok=True)
            raise RuntimeError(f"WebP 转换失败: {path.name}") from exc
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_image_optimizer.py -v`
Expected: PASS（含 TestConvertToWebp 全部）

- [ ] **Step 5: 提交（需用户审核）**

```bash
git add bot/engine/image_optimizer.py tests/unit/engine/test_image_optimizer.py
git commit -m "feat(image_optimizer): 新增 _convert_to_webp 与 optimize 转换分支（统一有损 q85）"
```

⚠️ 需用户审核后执行。

---

## Task 5: `_process_image_pipeline` 返回 `final_filename` + 降级

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_index_manager.py` 末尾追加测试辅助与用例：

```python
class FakeOptimizer:
    """optimize 测试替身，返回指定 output_path 或抛异常。"""

    def __init__(
        self, output_path: str | None = None, raises: Exception | None = None
    ) -> None:
        self._output_path = output_path
        self._raises = raises

    async def optimize(self, image_path: str) -> OptimizeResult:
        if self._raises is not None:
            raise self._raises
        return OptimizeResult(
            original_size=100,
            optimized_size=80,
            saved=20,
            output_path=self._output_path or image_path,
        )


class FakeOcrProvider:
    def __init__(self, text: str = "text") -> None:
        self._text = text

    async def ocr(self, image_path: str) -> str:
        return self._text

    async def close(self) -> None:
        pass


class FakeEmbeddingProvider:
    def __init__(self, vec: list[float] | None = None) -> None:
        self._vec = vec or [0.1] * 1024

    async def embed(self, text: str) -> list[float]:
        return self._vec


class TestPipelineFinalFilename:
    """_process_image_pipeline 返回 final_filename 与降级测试。"""

    @pytest.mark.asyncio
    async def test_pipeline_uses_output_path(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.webp"))
        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.webp"
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_pipeline_keeps_filename_when_no_convert(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.jpg"))
        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.jpg"

    @pytest.mark.asyncio
    async def test_pipeline_degrades_on_optimize_error(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(raises=RuntimeError("convert fail"))
        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=FakeOcrProvider("hello"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, _ = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.jpg"
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_pipeline_empty_text_returns_empty_embedding(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "a.webp"))
        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=FakeOcrProvider(""),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        final_fn, text, emb = await im._process_image_pipeline("a.jpg")
        assert final_fn == "a.webp"
        assert text == ""
        assert emb == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestPipelineFinalFilename -v`
Expected: FAIL（`_process_image_pipeline` 返回二元组，无法解包三元组）

- [ ] **Step 3: 改 `_process_image_pipeline` 返回值与降级**

将 `bot/engine/index_manager.py` 的 `_process_image_pipeline` 全文替换为：

```python
    async def _process_image_pipeline(self, filename: str) -> tuple[str, str, list[float]]:
        """压缩 -> OCR -> Embedding 管道。

        optimize 后读取 result.output_path 作为最终路径；若与原 filename 不同（转 webp），
        final_filename 取 output_path 的文件名。optimize 失败时降级：清理可能已生成的
        .webp 孤儿，回退用原 filename 继续 OCR/embed，不抛错。

        Args:
            filename: memes/ 下的文件名。

        Returns:
            (final_filename, text, embedding)：final_filename 可能与原 filename 不同（转 webp 后为 .webp）。

        Raises:
            OcrError: OCR 服务未注入或调用失败。
            EmbeddingError: Embedding 服务未注入或调用失败。
        """
        image_path = self._memes_dir / filename
        final_filename = filename
        if self._optimizer is not None:
            try:
                result = await self._optimizer.optimize(str(image_path))
                final_image_path = Path(result.output_path)
                final_filename = final_image_path.name
                image_path = final_image_path
            except Exception as exc:
                # 降级：optimize 失败时 _convert_to_webp 内部已清理 .webp 孤儿，回退原 filename
                logger.warning(
                    "转 webp 失败，降级保留原格式: filename=%s, error=%s", filename, exc
                )
                final_filename = filename
                image_path = self._memes_dir / filename
        if self._ocr_provider is None:
            raise OcrError("OCR 服务未注入")
        try:
            text = await self._ocr_provider.ocr(str(image_path))
        except Exception as exc:
            raise OcrError(f"OCR 调用失败: {filename}") from exc
        text = "".join(text.split())  # 统一去除所有空白
        if not text:
            return final_filename, "", []
        if self._embedding_provider is None:
            raise EmbeddingError("Embedding 服务未注入")
        try:
            embedding = await self._embedding_provider.embed(text)
        except Exception as exc:
            raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc
        return final_filename, text, embedding
```

确保 `from pathlib import Path` 已在 index_manager.py 顶部 import（现有已有）。

- [ ] **Step 3b: 更新现有测试适配三元组返回值**

Task 5 改 `_process_image_pipeline` 返回三元组并读 `output_path`，需同步更新 `tests/unit/engine/test_index_manager.py` 三处现有用例：

1. `MockOptimizer.optimize`（约 207-209 行）补 `output_path`，避免 `final_filename` 变空串：

```python
class MockOptimizer:
    """图片优化器 mock：不做任何操作。"""

    async def optimize(self, image_path: str) -> OptimizeResult:
        return OptimizeResult(
            original_size=0,
            optimized_size=0,
            saved=0,
            skipped=True,
            output_path=image_path,
        )
```

2. `test_process_image_pipeline_empty_text`（约 467 行）解包三元组：

```python
    final_filename, text, embedding = await index_manager._process_image_pipeline("blank.jpg")
    assert final_filename == "blank.jpg"
    assert text == ""
    assert embedding == []
```

3. `TestConcurrencyAndDrain.test_add_direct_pipeline` 的 `counting_pipeline`（约 918 行）返回类型标注改为三元组：

```python
        async def counting_pipeline(filename: str) -> tuple[str, str, list[float]]:
            nonlocal call_count
            call_count += 1
            return await original(filename)
```

- [ ] **Step 4: 修复 `_process_image_pipeline` 的两处调用点（临时保持可编译）**

`add()` 中（约 579 行）与 `_sync_phase2_add` 中（约 1339-1351 行）会因返回值变化而解包失败。本 Step 先临时改为解包三元组，filename 流转在 Task 6 完善。

`add()` 中：

```python
            text, embedding = await self._process_image_pipeline(filename)
```
改为：
```python
            final_filename, text, embedding = await self._process_image_pipeline(filename)
```

`_sync_phase2_add` 中：

```python
        success: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                text, embedding = result
                success[filename] = (text, embedding)
```
改为：
```python
        success: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                final_filename, text, embedding = result
                success[final_filename] = (text, embedding)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: PASS（含 TestPipelineFinalFilename；现有 add/sync 测试若有解包需检查，本 Task 暂用 final_filename 但 add 的 _WriteRequest 仍传原 filename，Task 6 修正）

- [ ] **Step 6: 提交（需用户审核）**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(index_manager): _process_image_pipeline 返回 final_filename 并支持转换降级"
```

⚠️ 需用户审核后执行。

---

## Task 6: `add()` 与 sync 阶段2 filename 流转 + 并发同名去重

**Files:**
- Modify: `bot/engine/index_manager.py`
- Test: `tests/unit/engine/test_index_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/engine/test_index_manager.py` 追加：

```python
class TestAddConvertsToWebp:
    """add() 转换后 sqlite image_path 为 .webp 测试。"""

    @pytest.mark.asyncio
    async def test_add_writes_webp_image_path(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "meme_001.jpg").write_bytes(b"x")
        opt = FakeOptimizer(output_path=str(memes / "meme_001.webp"))
        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=FakeOcrProvider("加班"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        result = await im.add("meme_001.jpg", speaker="小明", tags=["吐槽"])
        assert result.reason == "added"
        entry = md.get_entry(result.entry_id)
        assert entry is not None
        assert entry.image_path == "meme_001.webp"

    @pytest.mark.asyncio
    async def test_add_degrades_to_original_format(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "meme_002.png").write_bytes(b"x")
        opt = FakeOptimizer(raises=RuntimeError("fail"))
        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=FakeOcrProvider("心累"),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=opt,
        )
        result = await im.add("meme_002.png")
        assert result.reason == "added"
        entry = md.get_entry(result.entry_id)
        assert entry is not None
        assert entry.image_path == "meme_002.png"


class PerFileOcrProvider:
    """按文件 stem 返回不同 OCR 文本，避免 phase2 text 去重干扰。"""

    async def ocr(self, image_path: str) -> str:
        return f"text_{Path(image_path).stem}"

    async def close(self) -> None:
        pass


class CountingOcrProvider:
    """按调用次数返回不同 OCR 文本（同 stem 不同图场景）。"""

    def __init__(self) -> None:
        self._n = 0

    async def ocr(self, image_path: str) -> str:
        self._n += 1
        return f"text{self._n}"

    async def close(self) -> None:
        pass


class TestSyncConvertsToWebp:
    """sync 阶段2 转换 + 并发同名去重测试。"""

    @pytest.mark.asyncio
    async def test_sync_converts_new_files(self, tmp_path: Path) -> None:
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "a.jpg").write_bytes(b"x")
        (memes / "b.png").write_bytes(b"x")

        class PerFileOptimizer:
            async def optimize(self, image_path: str) -> OptimizeResult:
                p = Path(image_path)
                new_p = p.with_suffix(".webp")
                return OptimizeResult(100, 80, 20, output_path=str(new_p))

        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=PerFileOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=PerFileOptimizer(),
        )
        sync_result = await im.refresh()
        assert sync_result.added == 2
        paths = {e.image_path for e in md.get_all_entries().values()}
        assert paths == {"a.webp", "b.webp"}

    @pytest.mark.asyncio
    async def test_sync_dedups_same_stem_final_filename(self, tmp_path: Path) -> None:
        """两张同 stem 不同扩展名新增图转 webp 后同名，需去重 rename，两张均入库。"""
        md = FakeMetadataStore()
        vs = FakeVectorStore()
        memes = tmp_path / "memes"
        memes.mkdir()
        (memes / "dup.jpg").write_bytes(b"x")
        (memes / "dup.png").write_bytes(b"x")

        class SameStemOptimizer:
            """两张都返回 dup.webp（模拟未去重），由 pipeline 去重 rename。"""

            async def optimize(self, image_path: str) -> OptimizeResult:
                return OptimizeResult(100, 80, 20, output_path=str(memes / "dup.webp"))

        im = IndexManager(
            md, vs, str(memes),
            ocr_provider=CountingOcrProvider(),
            embedding_provider=FakeEmbeddingProvider(),
            optimizer=SameStemOptimizer(),
        )
        sync_result = await im.refresh()
        assert sync_result.added == 2
        paths = {e.image_path for e in md.get_all_entries().values()}
        assert "dup.webp" in paths
        assert len(paths) == 2
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestAddConvertsToWebp tests/unit/engine/test_index_manager.py::TestSyncConvertsToWebp -v`
Expected: FAIL（add 传原 filename，image_path 非 .webp；sync 并发同名未去重）

- [ ] **Step 3: `add()` 用 final_filename 入队**

在 `bot/engine/index_manager.py` 的 `add()` 方法中，将：

```python
            self._ensure_write_worker()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            await self._write_queue.put(
                _WriteRequest(
                    op=WriteOp.ADD,
                    future=future,
                    filename=filename,
                    text=text,
                    speaker=speaker,
                    tags=tags,
                    embedding=embedding,
                )
            )
```

改为（`filename=final_filename`）：

```python
            self._ensure_write_worker()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            await self._write_queue.put(
                _WriteRequest(
                    op=WriteOp.ADD,
                    future=future,
                    filename=final_filename,
                    text=text,
                    speaker=speaker,
                    tags=tags,
                    embedding=embedding,
                )
            )
```

- [ ] **Step 4: sync 阶段2 并发同名去重**

在 `bot/engine/index_manager.py` 的 `_sync_phase2_add` 中，将 Task 5 临时改写的 `success` 收集逻辑替换为带去重 rename 的版本：

```python
        success: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
                continue
            final_filename, text, embedding = result
            # 并发同名去重：多张新增图转 webp 后可能产出同名 final_filename
            # （_convert_to_webp 并行 resolve 的 TOCTOU 竞态兜底）。
            # 基于 success dict 已有 key 去重，不依赖文件存在性。
            if final_filename in success:
                stem = Path(final_filename).stem
                suffix = Path(final_filename).suffix
                old_path = self._memes_dir / final_filename
                n = 1
                while (
                    f"{stem}_{n}{suffix}" in success
                    or (self._memes_dir / f"{stem}_{n}{suffix}").exists()
                ):
                    n += 1
                final_filename = f"{stem}_{n}{suffix}"
                new_path = self._memes_dir / final_filename
                if old_path.exists():
                    shutil.move(str(old_path), str(new_path))
                logger.info("并发同名去重 rename: %s", final_filename)
            success[final_filename] = (text, embedding)
```

确保 `shutil` 已在 index_manager.py 顶部 import（现有已有 `import shutil`）。

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py -v`
Expected: PASS（含新增 add/sync 转换用例；现有用例不受影响）

- [ ] **Step 6: 运行全量 engine 测试**

Run: `uv run pytest tests/unit/engine/ -v`
Expected: PASS

- [ ] **Step 7: 提交（需用户审核）**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(index_manager): add/sync 用 final_filename 贯穿写入，并发同名去重"
```

⚠️ 需用户审核后执行。

---

## Task 7: `bot.py` 注入 `convert_to_webp`

**Files:**
- Modify: `bot/bot.py`
- Test: 语法检查 + 现有 `tests/unit/test_bot.py`

- [ ] **Step 1: 修改 `bot.py` 导入与 ImageOptimizer 构造**

在 `bot/bot.py` 顶部 import 区，找到现有 `from bot.config import ...` 语句，将 `read_convert_to_webp` 加入导入列表（与现有 `read_ocr_provider` 等同处）。

将约 99 行：

```python
    image_optimizer = ImageOptimizer(concurrency=read_int_env("COMPRESS_CONCURRENCY"))
```

改为：

```python
    image_optimizer = ImageOptimizer(
        concurrency=read_int_env("COMPRESS_CONCURRENCY"),
        convert_to_webp=read_convert_to_webp(),
    )
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot/bot.py`
Expected: 无错误

- [ ] **Step 3: 运行现有 bot 测试**

Run: `uv run pytest tests/unit/test_bot.py -v`
Expected: PASS（现有用例不受影响）

- [ ] **Step 4: 提交（需用户审核）**

```bash
git add bot/bot.py
git commit -m "feat(bot): startup 注入 convert_to_webp 开关到 ImageOptimizer"
```

⚠️ 需用户审核后执行。

---

## Task 8: 迁移脚本 `scripts/convert_memes_to_webp.py`

**Files:**
- Create: `scripts/convert_memes_to_webp.py`
- Test: `tests/unit/test_convert_memes_to_webp.py`（新增）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_convert_memes_to_webp.py`：

```python
"""迁移脚本 convert_memes_to_webp.py 单元测试。

测试用 memes_dir = tmp_path/memes（memes 在 tmp_path 子目录），
使默认 backup 目录 tmp_path/memes_migrated_backup 落在 memes 外，
避免被 _collect_files.rglob 误扫。
"""

import importlib
from pathlib import Path

from PIL import Image

from bot.engine.metadata_store import MetadataStore


def _make_img(path: Path, mode: str = "RGB", color=(128, 64, 32), fmt: str = "JPEG") -> None:
    Image.new(mode, (50, 50), color=color).save(path, fmt)


def _run(memes_dir: Path, db_path: Path, dry_run: bool = False) -> tuple[int, int, int]:
    mod = importlib.import_module("scripts.convert_memes_to_webp")
    importlib.reload(mod)
    return mod.run_conversion(
        memes_dir=memes_dir, db_path=db_path, quality=85, dry_run=dry_run
    )


class TestConvertToWebp:
    def test_converts_jpg_and_updates_db(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        md.add("a.jpg", "加班")
        md.close()

        success, skipped, failed = _run(memes, tmp_sqlite_path)

        assert success == 1 and failed == 0
        assert not jpg.exists()
        assert (memes / "a.webp").exists()
        md = MetadataStore(str(tmp_sqlite_path))
        md.load()
        assert md.get_by_filename("a.webp") is not None
        assert md.get_by_filename("a.jpg") is None
        md.close()

    def test_dry_run_no_change(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        success, _, _ = mod.run_conversion(memes, tmp_sqlite_path, 85, True)
        assert success == 1
        assert jpg.exists()
        assert not (memes / "a.webp").exists()

    def test_target_exists_appends_n(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        (memes / "a.webp").write_bytes(b"existing")
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        assert (memes / "a_1.webp").exists()

    def test_no_db_record_only_convert(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        assert (memes / "a.webp").exists()

    def test_backup_dir_holds_original(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        backup = tmp_path / "backup"
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        mod.run_conversion(
            memes_dir=memes, db_path=tmp_sqlite_path, quality=85,
            dry_run=False, backup_dir=backup,
        )
        assert (backup / "a.jpg").exists()

    def test_idempotent_second_run(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        jpg = memes / "a.jpg"
        _make_img(jpg)
        _run(memes, tmp_sqlite_path)
        success, _, _ = _run(memes, tmp_sqlite_path)
        assert success == 0

    def test_gif_animated_converted(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        memes = tmp_path / "memes"
        memes.mkdir()
        frames = [
            Image.new("RGB", (50, 50), color=(i * 80, 0, 0)).quantize(colors=256)
            for i in range(3)
        ]
        gif = memes / "a.gif"
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
        success, _, failed = _run(memes, tmp_sqlite_path)
        assert success == 1 and failed == 0
        with Image.open(memes / "a.webp") as w:
            assert w.format == "WEBP"
            assert getattr(w, "n_frames", 1) == 3

    def test_include_archives_no_sqlite_update(self, tmp_path: Path, tmp_sqlite_path: Path) -> None:
        """归档目录图仅转文件+备份，不更新 sqlite。"""
        memes = tmp_path / "memes"
        memes.mkdir()
        deleted = tmp_path / "memes_deleted"
        deleted.mkdir()
        jpg = deleted / "arch.jpg"
        _make_img(jpg)
        mod = importlib.import_module("scripts.convert_memes_to_webp")
        importlib.reload(mod)
        success, _, failed = mod.run_conversion(
            memes, tmp_sqlite_path, 85, False, include_archives=True
        )
        assert success == 1 and failed == 0
        assert (deleted / "arch.webp").exists()
        assert not jpg.exists()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/test_convert_memes_to_webp.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'scripts.convert_memes_to_webp'`）

- [ ] **Step 3: 实现迁移脚本**

创建 `scripts/convert_memes_to_webp.py`：

```python
"""将 memes/ 下的非 WebP 图片批量转为 WebP 并更新 index.db。

转换规则：
- 使用 Pillow 打开原图，保存为有损 WebP（默认 quality=85）。
- 透明通道保留（P/RGBA 保持 RGBA）；GIF 动图保留 duration/loop 转 animated WebP。
- 强制转换不比较体积。
- 目标 .webp 已存在且非当前源文件时追加 _n 序号。
- 更新 sqlite image_path；DB 无记录则仅转文件+备份。
- 原文件移到 --backup-dir（默认 memes_migrated_backup/）。
- 不重新 OCR/embed，不动 chroma/meme_tag。

命令行示例：
    uv run python scripts/convert_memes_to_webp.py
    uv run python scripts/convert_memes_to_webp.py --quality 90 --dry-run
    uv run python scripts/convert_memes_to_webp.py --memes-dir ./memes --db-path ./data/index.db

注意：
    为避免 sqlite 写锁冲突，建议在 Bot 未运行时执行此脚本。
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from bot.config import INDEX_DB_PATH, MEMES_DIR
from bot.engine.metadata_store import DuplicateEntryError, MetadataStore
from bot.engine.utils import resolve_unique_filename

logger = logging.getLogger(__name__)

_CONVERTIBLE = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}


def _convert_to_webp(src: Path, quality: int) -> Path:
    """将单张图片转为 WebP，返回新路径（不改名原文件，不删原文件）。

    Args:
        src: 源图片路径。
        quality: WebP 质量。

    Returns:
        生成的 WebP 文件路径。
    """
    target = resolve_unique_filename(src.parent, f"{src.stem}.webp")
    img = Image.open(src)
    try:
        save_kwargs: dict[str, Any] = {"format": "WEBP", "quality": quality, "method": 6}
        n_frames: int = getattr(img, "n_frames", 1)
        if n_frames > 1:
            frames: list[Image.Image] = []
            for i in range(n_frames):
                img.seek(i)
                frames.append(img.copy())
            if "duration" in img.info:
                save_kwargs["duration"] = img.info["duration"]
            if "loop" in img.info:
                save_kwargs["loop"] = img.info["loop"]
            frames[0].save(
                target, append_images=frames[1:], save_all=True, **save_kwargs
            )
            for f in frames:
                f.close()
        else:
            save_img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
            save_img.save(target, **save_kwargs)
    finally:
        img.close()
    return target


def _collect_files(memes_dir: Path, include_archives: bool) -> list[Path]:
    """收集待转换文件。

    Args:
        memes_dir: 表情包目录。
        include_archives: 是否包含归档目录。

    Returns:
        待转换文件路径列表（按路径升序）。
    """
    dirs = [memes_dir]
    if include_archives:
        for name in ("memes_deleted", "memes_replaced", "meme_no_text"):
            d = memes_dir.parent / name
            if d.exists():
                dirs.append(d)
    files: list[Path] = []
    for d in dirs:
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in _CONVERTIBLE:
                files.append(p)
    return sorted(files)


def run_conversion(
    memes_dir: Path,
    db_path: Path,
    quality: int,
    dry_run: bool,
    include_archives: bool = False,
    backup_dir: Path | None = None,
) -> tuple[int, int, int]:
    """执行批量 WebP 转换。

    Args:
        memes_dir: 表情包目录。
        db_path: index.db 路径。
        quality: WebP 质量。
        dry_run: 为 True 时只打印不修改。
        include_archives: 是否处理归档目录。
        backup_dir: 原文件备份目录；None 时默认 memes_dir 同级 memes_migrated_backup/。

    Returns:
        (成功数, 跳过数, 失败数)。
    """
    if dry_run:
        logger.info("DRY-RUN 模式：不会修改文件或数据库")
    if not memes_dir.exists():
        logger.error("表情包目录不存在: %s", memes_dir)
        return 0, 0, 0

    files = _collect_files(memes_dir, include_archives)
    if not files:
        logger.info("未找到待转换的非 WebP 图片")
        return 0, 0, 0

    backup = backup_dir or (memes_dir.parent / "memes_migrated_backup")
    if not dry_run:
        backup.mkdir(parents=True, exist_ok=True)

    metadata_store: MetadataStore | None = None
    if not dry_run:
        metadata_store = MetadataStore(str(db_path))
        metadata_store.load()

    success = skipped = failed = 0

    try:
        for src in files:
            rel = src.relative_to(memes_dir.parent).as_posix() if include_archives else src.name
            logger.info("转换: %s", rel)

            if dry_run:
                success += 1
                continue

            assert metadata_store is not None

            # a. 转换
            try:
                webp_path = _convert_to_webp(src, quality)
            except Exception as exc:
                logger.error("转换失败，跳过: %s - %s", rel, exc)
                failed += 1
                continue

            # 判断是否在 memes_dir 内（扁平结构下 src.parent == memes_dir）；
            # 归档目录图仅转文件+备份，不查 sqlite（避免误匹配 memes 同名记录）。
            try:
                src.relative_to(memes_dir)
                in_memes = True
            except ValueError:
                in_memes = False

            new_rel = webp_path.name
            db_updated = False
            if not in_memes:
                logger.info("归档目录图仅转换备份，不更新 sqlite: %s", rel)
                db_updated = True
            else:
                try:
                    entry = metadata_store.get_by_filename(src.name)
                    if entry is None:
                        logger.info("index.db 中无对应记录: %s", src.name)
                        db_updated = True
                    else:
                        db_updated = metadata_store.update(entry.id, image_path=new_rel)
                        if not db_updated:
                            logger.warning("更新 image_path 失败，id=%s", entry.id)
                except DuplicateEntryError as exc:
                    logger.error("image_path UNIQUE 冲突，跳过: %s - %s", rel, exc)
                    webp_path.unlink(missing_ok=True)
                    failed += 1
                    continue
                except Exception as exc:
                    logger.error("数据库更新失败: %s - %s", rel, exc)
                    db_updated = False

            if not db_updated:
                webp_path.unlink(missing_ok=True)
                failed += 1
                continue

            # c. 原文件移到 backup
            try:
                dst = resolve_unique_filename(backup, src.name)
                shutil.move(str(src), str(dst))
            except Exception as exc:
                logger.warning("移备份失败（索引已一致，原文件暂留）: %s - %s", rel, exc)

            success += 1
    finally:
        if metadata_store is not None:
            metadata_store.close()

    return success, skipped, failed


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="将 memes/ 下的非 WebP 图片批量转为 WebP 并更新 index.db"
    )
    parser.add_argument("--memes-dir", type=Path, default=MEMES_DIR, help=f"表情包目录（默认 {MEMES_DIR}）")
    parser.add_argument("--db-path", type=Path, default=INDEX_DB_PATH, help=f"sqlite 路径（默认 {INDEX_DB_PATH}）")
    parser.add_argument("--quality", type=int, default=85, help="WebP 质量（1-100，默认 85）")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，不修改文件和数据库")
    parser.add_argument("--include-archives", action="store_true", help="同时处理 memes_deleted/memes_replaced/meme_no_text")
    parser.add_argument("--backup-dir", type=Path, default=None, help="原文件备份目录（默认 memes_migrated_backup/）")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if not (1 <= args.quality <= 100):
        logger.error("quality 必须在 1-100 之间")
        return 1

    success, skipped, failed = run_conversion(
        memes_dir=args.memes_dir,
        db_path=args.db_path,
        quality=args.quality,
        dry_run=args.dry_run,
        include_archives=args.include_archives,
        backup_dir=args.backup_dir,
    )
    print(f"完成：成功 {success}，跳过 {skipped}，失败 {failed}")
    print("提示：建议在 Bot 未运行时执行，避免 sqlite 写锁冲突。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run pytest tests/unit/test_convert_memes_to_webp.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交（需用户审核）**

```bash
git add scripts/convert_memes_to_webp.py tests/unit/test_convert_memes_to_webp.py
git commit -m "feat(scripts): 新增 convert_memes_to_webp 存量图迁移脚本"
```

⚠️ 需用户审核后执行。

**风险注**：
- 移备份失败时原文件残留 `memes/`，下次重入会重新转换（可能产生 `_n.webp`）；残留孤儿需手动清理。
- animated WebP 透明通道保留依赖 Pillow 实现，若发送异常需调整。

---

## Task 9: 环境变量 `.env.example` + `docker-compose.yml`

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`

文档/配置变更，不运行测试（CLAUDE.md：仅文档变更可不运行测试，但需注明）。

- [ ] **Step 1: `.env.example` 增加 `CONVERT_TO_WEBP`**

在 `.env.example` 的 `COMPRESS_CONCURRENCY=5` 之后追加：

```env

# 图片格式转换：true（默认）时新增图片转为有损 WebP 存储；false 时按传输格式存储（现状）
CONVERT_TO_WEBP=true
```

- [ ] **Step 2: `docker-compose.yml` 注入环境变量**

在 `docker-compose.yml` 的 `bot` 服务 `environment` 列表中，`COMPRESS_CONCURRENCY` 行之后追加：

```yaml
      - CONVERT_TO_WEBP=${CONVERT_TO_WEBP:-true}
```

- [ ] **Step 3: 验证配置可加载**

Run: `docker compose config 2>&1 | rg CONVERT_TO_WEBP`
Expected: 输出包含 `CONVERT_TO_WEBP: true`

- [ ] **Step 4: 提交（需用户审核）**

```bash
git add .env.example docker-compose.yml
git commit -m "feat(config): 新增 CONVERT_TO_WEBP 环境变量到 .env.example 与 docker-compose"
```

⚠️ 需用户审核后执行。仅文档/配置变更，未运行测试。

---

## Task 10: 文档同步（README / PRD / CONTEXT / API）

**Files:**
- Modify: `README.md`
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`
- Modify: `docs/api/API.md` 及 `docs/api/bot/engine/image_optimizer.md`、`index_manager.md`、`config.md`

文档变更，不运行测试。

- [ ] **Step 1: `CONTEXT.md` 术语更新**

将 `CONTEXT.md` 中「图片无损压缩」术语条目改为「图片压缩/转换」，并补充 WebP 转换说明；新增 `CONVERT_TO_WEBP` 与 `memes_migrated_backup/` 术语条目。在「按文件名同步的增量刷新」与 `/add` 术语中补充「转 WebP」描述。

- [ ] **Step 2: `docs/PRD.md` 更新**

更新 `docs/PRD.md`：
- 3.4 `/add`：新增图片压缩改为「转 WebP（开关开启时）」。
- 3.6 索引管理：启动同步/`/refresh` 新增图片转 WebP。
- 4.3 安全/环境变量：可选变量加 `CONVERT_TO_WEBP`（默认 true）。
- 5 边界情况：增加「转 WebP 失败降级保留原格式」「开关关闭按传输格式存储」。
- 7 依赖清单：Pillow 说明补「WebP 转换」。
- 全文「无损压缩」->「图片压缩/转换」。

- [ ] **Step 3: `README.md` 更新**

更新 `README.md`：
- 功能说明补 WebP 转换。
- 部署步骤环境变量提及 `CONVERT_TO_WEBP`。
- 依赖说明「无损压缩」->「图片压缩/转换」。
- 项目结构 `scripts/` 补 `convert_memes_to_webp.py`（若列脚本）。

- [ ] **Step 4: `docs/api/API.md` 及子文档更新**

更新 `docs/api/API.md`：
- `image_optimizer.md`：`OptimizeResult.output_path` 字段、`ImageOptimizer(convert_to_webp=...)` 参数、`_convert_to_webp` 行为、`_compress_webp_lossy`。
- `index_manager.md`：`_process_image_pipeline` 返回 `(final_filename, text, embedding)`、降级行为、`resolve_unique_filename` 移至 utils。
- `config.md`：`read_convert_to_webp()`。
- 新增迁移脚本 `scripts/convert_memes_to_webp.py` 条目。
- 目录结构补 `memes_migrated_backup/`。

- [ ] **Step 5: 提交（需用户审核）**

```bash
git add README.md docs/PRD.md CONTEXT.md docs/api/
git commit -m "docs: 同步 WebP 转换功能与 CONVERT_TO_WEBP 开关文档"
```

⚠️ 需用户审核后执行。仅文档变更，未运行测试。

---

## Task 11: 全量测试与编译检查

**Files:** 无（验证）

- [ ] **Step 1: 全量单元测试**

Run: `uv run pytest tests/unit/ -v`
Expected: PASS

- [ ] **Step 2: 语法编译检查**

Run: `uv run python -m compileall bot tests scripts`
Expected: 无错误

- [ ] **Step 3: 迁移脚本 dry-run 冒烟（可选）**

Run: `uv run python scripts/convert_memes_to_webp.py --dry-run`
Expected: 输出「完成：成功 N，跳过 0，失败 0」或「未找到待转换的非 WebP 图片」

- [ ] **Step 4: 标记完成，等待用户验收**

向用户汇报全部 Task 完成，提示可运行迁移脚本转换存量图（建议 Bot 未运行时）。
