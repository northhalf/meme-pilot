# IndexManager 拆分实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 3041 行的单体 `bot/engine/index_manager.py` 按职责拆为薄门面 + 四协作者，迁入新子包 `bot/index_manager/`，公开 API 零变化。

**Architecture:** 新建 `bot/index_manager/` 子包（与 `engine/` 平级）。`IndexManager` 退化为薄门面（搜索 + 查询 + 写入薄壳），四个内部协作者各司其职：`EntryWriter`（无状态写单条）、`ImagePipeline`（压缩→OCR→embed 管道 + optimizer 锁表）、`_WriteCoordinator`（worker + queue + 七个 `_execute_*` + move 补偿）、`SyncEngine`（refresh 全流程）。`rwlock`/`index_types` 随门面迁入；通用模块（stores/searchers/optimizer/collection_manager/protocols/types/utils/providers）留 `engine/`。依赖图扁平、无横向依赖。

**Tech Stack:** Python 3.12、asyncio、pytest 9.1.0、ty 0.0.58（类型检查）、ruff 0.15.21（lint）。

## Global Constraints

- Python 3.12，**不使用** `from __future__ import annotations`。
- 函数使用 Google 风格 docstring，内容用中文；函数参数、返回值需类型标注。
- `dataclass` 字段严格用 `tuple`；下游消费参数放宽 `Sequence`（见项目 memory 偏好）。
- 保持现有中文注释和用户提示风格。
- **公开 API 零变化**：`IndexManager` 的所有公开方法签名、`index_types` 导出的异常/结果类型名称不变。**测试改动原则**：测试体应尽量不动；但当私有内部方法/属性因迁入协作者而改变归属时，测试中对这些**私有** monkeypatch/rebind/直调目标的访问路径可相应更新（如 `index_manager._execute_move` -> `index_manager._coordinator._execute_move`），这不违反公开 API 零变化。Task 2 走"保留薄委托/property 转发、零测试改动"路线；Task 3 裁决走"执行器迁入 coordinator + ~5 处测试 monkeypatch 目标改路径"路线（见 Task 3 裁决）。
- **迁移策略**：逐个抽取，每步完成后必须跑 `pytest tests/unit/engine/ tests/integration/test_index_manager_api.py` + `ty check <改动范围>` + `ruff check <改动范围>` 三工具全绿才提交。
- 每步独立提交；当前分支 `refactor/index-manager-split`，可在此分支提交（已脱离 main）。
- 文本搜索用 `rg`、文件查找用 `fd`、代码语义查询用 LSP/codegraph、Python 类型检查用 `ty`、lint 用 `ruff`、复杂推理用 `sequential-thinking` MCP。
- 验证命令：`pytest` 用 `.venv/bin/python -m pytest`；`ty`/`ruff` 直接调用（已在 PATH）。
- **测试兼容性原则（Task 1-4 抽协作者时适用，Task 2 裁确立）**：测试大量 monkeypatch/rebind/直读 `IndexManager` 门面的私有方法与属性（如 `_process_image_pipeline`、`_optimizer`、`_optimizer_target_locks` 等）。凡被测试触及的门面私有方法或属性，抽取后必须在门面**保留薄委托**或改 **property 转发**到对应协作者，保证 rebind/monkeypatch/直读仍生效、测试体零改动；只有测试完全不碰的纯内部方法才从门面删除。调用点对被测试触及的方法须保持调 `self.<方法>(...)`（薄委托），不改指 `self._<协作者>.<方法>(...)`，以保留 monkeypatch 接缝。

**设计文档**：`docs/superpowers/specs/2026-07-17-index-manager-split-design.md`（权威，与本计划冲突时以 spec 为准）。

---

## File Structure

最终目标结构（6 步迁移后）：

```
bot/index_manager/              # 新子包
  __init__.py                   # 导出 IndexManager + index_types 公开符号 + IndexRwLock
  manager.py                    # IndexManager 门面（搜索 + 查询 + 写入薄壳 + load/close）
  write_coordinator.py          # _WriteCoordinator（worker + queue + _execute_* + move 补偿）
  image_pipeline.py             # ImagePipeline（管道 + optimizer 锁表）
  sync_engine.py                # SyncEngine（refresh + phase0-2 + 扫描）
  entry_writer.py               # EntryWriter（无状态写单条到两库）
  index_types.py                # 从 engine/ 迁入（异常/枚举/dataclass/结果类型，不含 OcrProvider）
  rwlock.py                     # 从 engine/ 迁入（IndexRwLock）
```

`bot/engine/` 留下并调整的文件：
- `engine/__init__.py`：移除 `IndexManager` 及 `index_types` 重导出项；`OcrProvider` 改从 `.protocols` 重导出；新增 `from .utils import resolve_unique_filename` 重导出。
- `engine/protocols.py`：新增 `OcrProvider`（从 `index_types` 移入）。
- `engine/provider_factory.py` / `app_state.py`：`OcrProvider` 改从 `engine.protocols` import。
- `engine/index_manager.py`：步骤 5 末删除（内容迁入 `bot/index_manager/manager.py`）。

消费方改动（步骤 5）：9 个插件 + `app_state.py` + `bot.py` 的 `from bot.engine.index_manager import ...` / `from .engine.index_manager import ...` 改为 `from bot.index_manager import ...` / `from .index_manager import ...`。

---

## Task 0: 建子包骨架 + 移动 index_types / rwlock / OcrProvider

**Files:**
- Create: `bot/index_manager/__init__.py`、`bot/index_manager/index_types.py`、`bot/index_manager/rwlock.py`
- Modify: `bot/engine/index_types.py`（删除，内容迁走）、`bot/engine/rwlock.py`（删除，内容迁走）
- Modify: `bot/engine/protocols.py`（新增 `OcrProvider`）
- Modify: `bot/engine/provider_factory.py`、`bot/app_state.py`、`bot/engine/index_manager.py`
- **不改**（推迟到 Task 5）：`bot/engine/__init__.py` 的 `from .index_manager import (...)` 重导出块与 `__all__` 索引符号——Task 0 保持它们原样工作（`index_manager.py` 此步仍在 engine 且仍 `__all__` 导出这些符号，故 `from bot.engine import IndexManager/...` 仍可用）。

**Pre-flight 修正（已并入本计划）**：原稿 Task 0 Step 4 删除 `engine/__init__` 重导出会导致 `tests/unit/engine/test_index_manager_move.py:12-16`、`tests/unit/plugins/test_collection.py:9-13`、`bot/plugins/collection.py`、`bot/bot.py` 这 4 个经 `from bot.engine import <索引符号>` 重导出形式 import 的消费方在 Task 0 即断引用。故把 `engine/__init__` 重导出的移除整体推迟到 Task 5（与消费方改路径同批）。Task 0 只移动数据/锁/协议 + 调整 `index_manager.py` 自身 import 来源。

**Interfaces:**
- Produces: `bot.index_manager.index_types`（含所有原 `index_types` 符号，**不含 `OcrProvider`**）、`bot.index_manager.rwlock.IndexRwLock`、`bot.engine.protocols.OcrProvider`。

**关键事实（已核实）**：
- `OcrProvider` 真实使用点：`bot/engine/provider_factory.py:9`（`from .index_manager import OcrProvider`）、`provider_factory.py:12`（`Factory: TypeAlias = Callable[[], OcrProvider]`）、`provider_factory.py:36`（`def create_ocr_provider(name: str) -> OcrProvider`）；`bot/app_state.py:17`（`from .engine.index_manager import OcrProvider`）、`app_state.py:27/40/139`（标注使用）。3 个 OCR 实现文件仅 docstring 提及，非运行时依赖。
- `rwlock`（`IndexRwLock`）已核实 `engine/` 其他模块不使用，仅 `index_manager.py` 用，随迁无断引用。

- [ ] **Step 1: 创建子包与迁移 index_types（移除 OcrProvider）**

把 `bot/engine/index_types.py` 整体复制为 `bot/index_manager/index_types.py`，但**移除 `OcrProvider` 类定义**（原 `index_types.py:321` 附近的 `class OcrProvider(Protocol)`）。同时调整 `index_types.py` 顶部的 import：若移除 `OcrProvider` 后不再需要 `Protocol`，删掉对应 import。保留文件其余内容（所有异常、`WriteOp`、`_WriteRequest`、各 dataclass、`_OptimizerLockEntry` 等）原样。

`bot/index_manager/__init__.py` 暂时留空（步骤 5 再填充导出），但加一行注释说明：

```python
"""index_manager 子包 — 索引管理薄编排层。

步骤 0 先迁入 index_types 与 rwlock；门面与协作者在后续步骤陆续加入，
__init__ 导出在步骤 5 最终填充。
"""
```

- [ ] **Step 2: 迁移 rwlock**

把 `bot/engine/rwlock.py` 整体复制为 `bot/index_manager/rwlock.py`，内容原样（仅 `IndexRwLock` 类）。

- [ ] **Step 3: OcrProvider 移入 engine/protocols.py**

在 `bot/engine/protocols.py` 末尾新增 `OcrProvider`。先读取现有 `bot/engine/index_types.py` 中 `OcrProvider` 的完整定义（`class OcrProvider(Protocol):` 及其方法），原样粘到 `protocols.py`。确保 `protocols.py` 顶部有 `from typing import Protocol`（已有）。`OcrProvider` 的方法签名与 docstring 完整保留。

- [ ] **Step 4: 更新 engine/__init__.py（仅 OcrProvider 与 resolve_unique_filename，不动索引重导出）**

修改 `bot/engine/__init__.py`（此步**只做两件最小改动**，索引符号重导出推迟到 Task 5）：
- 新增/调整 `from .protocols import EmbeddingProvider, OcrProvider`（若原来只 import `EmbeddingProvider`，加上 `OcrProvider`）。
- 新增 `from .utils import resolve_unique_filename`（独立一行）。
- 在 `__all__` 中**新增** `resolve_unique_filename`（现来自 utils）；`OcrProvider` 已在 `__all__` 中（原来来自 index_manager，现来源变 protocols，名字不变，无需改 `__all__`）。
- **保留** `from .index_manager import (...)` 块与 `__all__` 中所有索引符号（`IndexManager`/`AddResult`/... 等）原样不动——Task 5 再统一移除。这样 `from bot.engine import IndexManager/MemeMoveError/...` 在 Task 0 仍可用，不破坏 4 个消费方。

- [ ] **Step 5: 更新 provider_factory.py 与 app_state.py 的 OcrProvider import**

`bot/engine/provider_factory.py:9`：`from .index_manager import OcrProvider` → `from .protocols import OcrProvider`。

`bot/app_state.py:17`：`from .engine.index_manager import OcrProvider` → `from .engine.protocols import OcrProvider`。

3 个 OCR 实现文件（`openai_ocr.py`/`paddle_ocr.py`/`rapidocr_ocr.py`）的 docstring 里 "实现 index_manager.OcrProvider 协议" 文案改为 "实现 engine.protocols.OcrProvider 协议"（仅注释，非运行时依赖）。

- [ ] **Step 6: 更新 index_manager.py 的 import 来源**

`bot/engine/index_manager.py` 顶部 import 块：
- `from .index_types import (...)` → `from bot.index_manager.index_types import (...)`（原样保留所有导入符号名）。
- `from .rwlock import IndexRwLock` → `from bot.index_manager.rwlock import IndexRwLock`。
- `OcrProvider`：原从 `.index_types` import，现改为 `from .protocols import OcrProvider`。
- `__all__` 列表：移除 `OcrProvider`（已移到 protocols，由 engine/__init__ 从 protocols 导出）；移除 `resolve_unique_filename`（改由 engine/__init__ 从 utils 直接导出，不归 index_manager）。其余符号保留在 `__all__`（这些会在步骤 5 随门面迁走，但此步 index_manager 仍在 engine，保持导出以兼容现有消费方 `from bot.engine.index_manager import XxxError`）。

- [ ] **Step 7: 删除 engine 下的旧 index_types.py 与 rwlock.py**

确认 `bot/engine/index_manager.py` 已改用 `bot.index_manager.index_types` / `bot.index_manager.rwlock` 后，删除 `bot/engine/index_types.py` 与 `bot/engine/rwlock.py`（内容已迁走）。同步删除对应 `__pycache__`。

- [ ] **Step 8: 跑验证**

```bash
.venv/bin/python -m pytest tests/unit/engine/ tests/integration/test_index_manager_api.py -x -q
ty check bot/index_manager/ bot/engine/ bot/app_state.py bot/bot.py
ruff check bot/index_manager/ bot/engine/ bot/app_state.py bot/bot.py
```
Expected: pytest 全绿；ty `All checks passed!`；ruff 无报错。

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(index): 迁出 index_types/rwlock 并移 OcrProvider 至 protocols"
```

---

## Task 1: 抽取 EntryWriter（无状态写单条）

**Files:**
- Create: `bot/index_manager/entry_writer.py`
- Modify: `bot/engine/index_manager.py`（`_write_entry` 改为委托；`_execute_add` 与 `_sync_phase2_add` 改调 `self._entry_writer.write_entry(...)`）

**Interfaces:**
- Consumes: `MetadataStore`、`VectorStore`、`Path`（memes_dir）、`_move_to_no_text`/`_move_to_replaced` 的逻辑（这两个辅助在 Task 2 归 ImagePipeline，但 `_write_entry` 调用它们——此步需处理依赖）。
- Produces: `EntryWriter.write_entry(filename, text, embedding, speaker, tags, *, collection_id) -> AddResult`。

**关键决策**：`_write_entry` 内部调用 `self._move_to_no_text`（行 2547）和 `self._move_to_replaced`（行 2609）。`_move_to_no_text` 归 ImagePipeline（Task 2），`_move_to_replaced` 归 `_WriteCoordinator`（move 补偿，Task 3）。为避免 Task 1 引入对未抽取协作者的依赖，**此步把这两个移动函数作为回调注入 `EntryWriter`**：构造 `EntryWriter` 时传入两个 `Callable`（`move_to_no_text: Callable[[str], str]`、`move_to_replaced: Callable[[str], str]`），由门面在 Task 2/3 抽取后提供绑定。Task 1 此步门面先把 `self._move_to_no_text` 与 `self._move_to_replaced` 的现有实现作为回调传入。

- [ ] **Step 1: 创建 EntryWriter**

创建 `bot/index_manager/entry_writer.py`。读取 `bot/engine/index_manager.py:2508-2681`（`_write_entry` 全文），将其转为 `EntryWriter` 类的方法 `write_entry`：

```python
"""EntryWriter — 把单条 MemeEntry + 向量写入两个 Store 的无状态 helper。"""

from collections.abc import Callable, Sequence
from pathlib import Path

from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore

from .index_types import AddResult, CollectionNotFoundError, EmbeddingError


class EntryWriter:
    """无状态写入器：先 sqlite 后 chroma，失败可回滚。

    Args:
        metadata_store: 元数据存储。
        vector_store: 向量存储。
        memes_dir: memes/ 目录路径。
        move_to_no_text: 无文字移图回调，filename -> 归档路径。
        move_to_replaced: 去重替换归档旧图回调，filename -> 归档路径。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: Path,
        move_to_no_text: Callable[[str], str],
        move_to_replaced: Callable[[str], str],
    ) -> None:
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._memes_dir = memes_dir
        self._move_to_no_text = move_to_no_text
        self._move_to_replaced = move_to_replaced

    async def write_entry(
        self,
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        """三分类写入：无文字移图 / 去重替换 / 正常新增。

        写入顺序统一"先 sqlite 后 chroma"，失败可回滚。
        （docstring 与原 _write_entry 完全一致，复制 bot/engine/index_manager.py:2518-2536 的中文 docstring）
        """
        # 方法体原样复制自 _write_entry（行 2537-2681），
        # 把 self._metadata_store / self._vector_store / self._memes_dir /
        # self._move_to_no_text / self._move_to_replaced 保持不变（构造时已绑定同名属性）。
        ...  # 实际实现时粘贴 _write_entry 行 2537-2681 的完整方法体
```

注意：原方法体引用的 `CollectionNotFoundError`、`EmbeddingError`、`AddResult` 从 `.index_types` import；`asyncio`/`logger` 需补 import（`import asyncio`、`import logging`、`logger = logging.getLogger(__name__)`）。

- [ ] **Step 2: 在 IndexManager 构造 EntryWriter 并改 _write_entry 为委托**

`bot/engine/index_manager.py` `__init__`（约行 185-226）末尾新增：

```python
        from .entry_writer import EntryWriter  # 顶部 import 也可，但延迟 import 避免循环
        # 注意：entry_writer 在 bot.index_manager 包，import 路径为 bot.index_manager.entry_writer
```

实际在 `__init__` 中构造：

```python
        from bot.index_manager.entry_writer import EntryWriter
        self._entry_writer = EntryWriter(
            metadata_store=self._metadata_store,
            vector_store=self._vector_store,
            memes_dir=self._memes_dir,
            move_to_no_text=self._move_to_no_text,
            move_to_replaced=self._move_to_replaced,
        )
```

把原 `_write_entry` 方法体（行 2508-2681）替换为薄委托：

```python
    async def _write_entry(
        self,
        filename: str,
        text: str,
        embedding: Sequence[float],
        speaker: str | None = None,
        tags: Sequence[str] | None = None,
        *,
        collection_id: int = 0,
    ) -> AddResult:
        """委托 EntryWriter.write_entry。"""
        return await self._entry_writer.write_entry(
            filename,
            text,
            embedding,
            speaker=speaker,
            tags=tags,
            collection_id=collection_id,
        )
```

`_execute_add`（worker 循环内行 1392 调 `self._write_entry(...)`）与 `_sync_phase2_add`（若直接调 `_write_entry`）无需改动——它们调 `self._write_entry`，而 `_write_entry` 现委托给 `self._entry_writer`。确认 `_sync_phase2_add` 内是否直接调 `_write_entry`，若是同样保持调 `self._write_entry`。

- [ ] **Step 3: 跑验证**

```bash
.venv/bin/python -m pytest tests/unit/engine/ tests/integration/test_index_manager_api.py -x -q
ty check bot/index_manager/ bot/engine/
ruff check bot/index_manager/ bot/engine/
```
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(index): 抽取无状态 EntryWriter 写单条条目"
```

---

## Task 2: 抽取 ImagePipeline（管道 + optimizer 锁表）

**Files:**
- Create: `bot/index_manager/image_pipeline.py`
- Modify: `bot/engine/index_manager.py`（构造 `ImagePipeline`，`_execute_add`/`_execute_move` 改调 `self._image_pipeline`）

**Interfaces:**
- Consumes: `ImageOptimizer`、`OcrProvider`、`EmbeddingProvider`、`Path`（memes_dir/no_text_dir）。
- Produces: `ImagePipeline.process(filename) -> tuple[str, str, list[float]]`（即原 `_process_image_pipeline` 返回）、`ImagePipeline.optimize_with_cancellation(filename) -> tuple[OptimizeResult, set[Path]]`（供 move 复用）、`ImagePipeline.validate_add_relative_path(relative_path) -> str`、`ImagePipeline.move_to_no_text(filename) -> str`。

**涉及方法（行号）**：`_process_image_pipeline`（2958-3026）、`_validate_add_relative_path`（2931-2956）、`_move_to_no_text`（3027-3041）、`_optimizer_target_lock`（2777-2811）、`_wait_task_through_cancellation`（2813-2848，静态）、`_release_optimizer_lock_entry`（2850-2864）、`_optimize_with_cancellation`（2866-2916）、`_cleanup_pipeline_output`（2917-2930，静态）、`_has_supported_ext`（2762-2775，静态）。`_OptimizerLockEntry`（index_types）随迁已在 `bot.index_manager.index_types`。

- [ ] **Step 1: 创建 ImagePipeline**

创建 `bot/index_manager/image_pipeline.py`。把上述方法从 `index_manager.py` 原样迁入 `ImagePipeline` 类，方法名去掉前导下划线改公开（`process`/`optimize_with_cancellation`/`validate_add_relative_path`/`move_to_no_text`/`optimizer_target_lock`/`wait_task_through_cancellation`/`release_optimizer_lock_entry`/`cleanup_pipeline_output`/`has_supported_ext`），`self._optimizer`/`self._ocr_provider`/`self._embedding_provider`/`self._memes_dir`/`self._no_text_dir`/`self._optimizer_target_locks`/`self._optimizer_registry_guard` 改为构造时绑定的同名属性。

构造函数：

```python
class ImagePipeline:
    """压缩 -> OCR -> Embedding 管道 + optimizer 并发锁表。

    Args:
        optimizer: 图片压缩器（可选，None 时跳过压缩）。
        ocr_provider: OCR 服务提供者。
        embedding_provider: Embedding 服务提供者。
        memes_dir: memes/ 目录。
        no_text_dir: 无文字图目录。
    """

    def __init__(
        self,
        optimizer: ImageOptimizer | None,
        ocr_provider: OcrProvider | None,
        embedding_provider: EmbeddingProvider | None,
        memes_dir: Path,
        no_text_dir: Path,
    ) -> None:
        self._optimizer = optimizer
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider
        self._memes_dir = memes_dir
        self._no_text_dir = no_text_dir
        self._optimizer_target_locks: dict[tuple[str, str], _OptimizerLockEntry] = {}
        self._optimizer_registry_guard = asyncio.Lock()
```

`process` 方法体复制 `_process_image_pipeline`（2958-3026），内部对 `self._optimizer`/`self._ocr_provider`/`self._embedding_provider` 的 None 检查与原逻辑一致。`SUPPORTED_EXTENSIONS`（现 IndexManager 类属性）迁移为 `ImagePipeline` 的类属性或保留在门面（`_has_supported_ext` 用到它——迁移时一并搬入 ImagePipeline 作为类属性）。

import：`asyncio`、`logging`、`from contextlib import asynccontextmanager`、`from pathlib import Path`、`from typing import AsyncIterator, TypeVar`、`from bot.engine.image_optimizer import ImageOptimizer, OptimizeResult`、`from bot.engine.protocols import EmbeddingProvider`、`from .index_types import OcrError, EmbeddingError, _OptimizerLockEntry`、`from bot.engine.utils import ...`（若有用到）。

- [ ] **Step 2: IndexManager 构造 ImagePipeline 并改调用**

`__init__` 构造：

```python
        from bot.index_manager.image_pipeline import ImagePipeline
        self._image_pipeline = ImagePipeline(
            optimizer=self._optimizer,
            ocr_provider=self._ocr_provider,
            embedding_provider=self._embedding_provider,
            memes_dir=self._memes_dir,
            no_text_dir=self._no_text_dir,
        )
```

`_execute_add` 内调 `self._process_image_pipeline(filename)` 改为 `self._image_pipeline.process(filename)`；`add` 公开方法内调 `self._validate_add_relative_path(relative_path)` 改为 `self._image_pipeline.validate_add_relative_path(relative_path)`。

`_execute_move` 内若调用 `self._optimize_with_cancellation` / `self._process_image_pipeline`，改为 `self._image_pipeline.optimize_with_cancellation(...)` / `self._image_pipeline.process(...)`。确认 `_execute_move` 对 optimizer 锁的使用点全部改指 `self._image_pipeline`。

`EntryWriter` 的 `move_to_no_text` 回调：更新为 `self._image_pipeline.move_to_no_text`（在构造 EntryWriter 处改绑定）。`move_to_replaced` 仍绑定 `self._move_to_replaced`（Task 3 归 coordinator 前暂留门面）。

从 `index_manager.py` **删除**已迁走的方法定义（`_process_image_pipeline`/`_validate_add_relative_path`/`_move_to_no_text`/`_optimizer_target_lock`/`_wait_task_through_cancellation`/`_release_optimizer_lock_entry`/`_optimize_with_cancellation`/`_cleanup_pipeline_output`/`_has_supported_ext`）。

- [ ] **Step 3: 跑验证**

```bash
.venv/bin/python -m pytest tests/unit/engine/ tests/integration/test_index_manager_api.py -x -q
ty check bot/index_manager/ bot/engine/
ruff check bot/index_manager/ bot/engine/
```
Expected: 全绿。重点看 move 相关测试（`test_index_manager_move.py`）验证 optimizer 锁路径仍工作。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(index): 抽取 ImagePipeline 含管道与 optimizer 锁表"
```

---

## Task 3: 抽取 _WriteCoordinator（写入编排全收口）

**Files:**
- Create: `bot/index_manager/write_coordinator.py`
- Modify: `bot/engine/index_manager.py`（写入公开方法退化为薄壳；删除 worker/executor/move 补偿方法）

**Interfaces:**
- Consumes: `rwlock`（注入）、`_write_drained` Event（注入）、`ImagePipeline`、`EntryWriter`、`MetadataStore`、`VectorStore`、`CollectionManager`、目录路径、`_shutting_down` 读取方式。
- Produces: `_WriteCoordinator.enqueue_create_collection(raw_name) -> CreateCollectionResult`、`enqueue_add(req) -> AddResult`、`enqueue_edit_text(req) -> EditTextResult`、`enqueue_set_speaker(req)`、`enqueue_add_tags(req)`、`enqueue_delete(req)`、`enqueue_move(req)`、`ensure_worker()`、`shutdown()`。

**涉及方法（行号）**：`_enqueue_write_request`（1353-1360）、`_ensure_write_worker`（1362-1365）、`_write_worker_loop`（1367-1470）、`_await_move_future`（1114-1124，静态）、`_get_collection_directory_identity`（1472-1491，静态）、`_execute_create_collection`（1493-1599）、`_execute_edit_text`（1601-1664）、`_execute_set_speaker`（1666-1697）、`_execute_add_tags`（1699-1739）、`_execute_delete`（1741-1793）、`_resolve_move_target_name`（1795-1824）、`_resolve_move_paths`（1826-1866）、`_execute_move`（1868-1997）、`_compensate_move`（1999-2067）、`_restore_move_file`（2069-2098）、`_move_to_replaced`（2100-2114）、`_preview_move_locked`（924-967）、`_validate_collection_selection_locked`（1200-1216，coordinator 的 `_execute_add` 调用它——此方法留门面还是迁入？见决策）。

**关键决策**：
- `_validate_collection_selection_locked` 被 worker 循环内 ADD 分支调用（行 1388）。此方法同时被门面的 `validate_collection_selection` 公开方法使用。**保留在门面**，coordinator 通过注入的门面引用或回调调用。为避免循环依赖，coordinator 构造时接收一个 `validate_collection_selection_locked: Callable[[ChatScope, CollectionSelection], None]` 回调，门面把 `self._validate_collection_selection_locked` 绑定传入。
- `_shutting_down`：归门面。coordinator 的 worker 循环不需要读 `_shutting_down`（关闭由门面 `close()` 调 `coordinator.shutdown()` 触发）；但门面写入公开方法在 enqueue 前检查 `self._shutting_down`/`self._refresh_active`（此检查留门面）。coordinator 的 `enqueue_*` 不重复检查。
- worker 循环内 ADD 分支调 `self._write_entry`（行 1392）改为 `self._entry_writer.write_entry`（直接用注入的 EntryWriter）；调 `self._validate_collection_selection_locked`（行 1388）改为注入的回调。
- `_execute_move` 调 `self._image_pipeline.optimize_with_cancellation` / `process` / `move_to_no_text`（注入的 ImagePipeline）；调 `self._move_to_replaced`（现在是 coordinator 自己的方法）。

- [ ] **Step 1: 创建 _WriteCoordinator**

创建 `bot/index_manager/write_coordinator.py`。把上述方法原样迁入 `_WriteCoordinator` 类。构造函数：

```python
class _WriteCoordinator:
    """写入编排：worker 循环 + queue + 七个 _execute_* + move 补偿。

    Args:
        metadata_store: 元数据存储。
        vector_store: 向量存储。
        collection_manager: 合集管理器。
        memes_dir / deleted_dir / replaced_dir: 目录路径。
        rwlock: 读写锁（注入，写锁在 worker 内获取）。
        write_drained: 无排队且无执行中写请求时 set 的 Event（注入，操作权在此）。
        image_pipeline: 图片管道协作者。
        entry_writer: 单条写入器。
        validate_collection_selection_locked: ADD 写锁内校验合集选择快照的回调。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        collection_manager: "CollectionManager",
        memes_dir: Path,
        deleted_dir: Path,
        replaced_dir: Path,
        rwlock: IndexRwLock,
        write_drained: asyncio.Event,
        image_pipeline: ImagePipeline,
        entry_writer: EntryWriter,
        validate_collection_selection_locked: Callable[["ChatScope", "CollectionSelection"], None],
    ) -> None:
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._collection_manager = collection_manager
        self._memes_dir = memes_dir
        self._deleted_dir = deleted_dir
        self._replaced_dir = replaced_dir
        self._rwlock = rwlock
        self._write_drained = write_drained
        self._image_pipeline = image_pipeline
        self._entry_writer = entry_writer
        self._validate_collection_selection_locked = validate_collection_selection_locked
        self._write_queue: asyncio.Queue[_WriteRequest] = asyncio.Queue()
        self._write_worker_task: asyncio.Task | None = None
```

worker 循环内 `self._write_drained`/`self._write_queue`/`self._rwlock`/`self._entry_writer`/`self._validate_collection_selection_locked` 均为构造绑定的属性，方法体原样复制。`_execute_*` 内对 `self._metadata_store`/`self._vector_store`/`self._collection_manager`/`self._memes_dir`/`self._deleted_dir`/`self._replaced_dir`/`self._image_pipeline` 的引用保持。

公开 enqueue 方法：把门面现 `add`/`edit_text`/`set_speaker`/`add_tags`/`delete`/`move`/`create_collection`/`prepare_move`/`preview_move` 中"构造 `_WriteRequest` + `_ensure_write_worker` + `_enqueue_write_request` + await future"的部分抽为 coordinator 的 `enqueue_*`。每个 `enqueue_*` 接收已构造好的 `_WriteRequest`（或接收原始参数在内部构造），返回 await future 的结果。**推荐**：coordinator 暴露一个统一入口 `async def submit(self, req: _WriteRequest) -> Any` 做 `ensure_worker + enqueue + await future`，门面各公开方法构造 `req` 后调 `await self._coordinator.submit(req)`。这样 coordinator 不必为每种 op 写一个 enqueue 方法，DRY。

```python
    async def submit(self, req: _WriteRequest) -> Any:
        """提交写入请求并等待执行完成。"""
        self.ensure_worker()
        self._enqueue_write_request(req)
        return await req.future

    def ensure_worker(self) -> None:
        """确保 Write Worker task 已启动（延迟启动）。"""
        if self._write_worker_task is None or self._write_worker_task.done():
            self._write_worker_task = asyncio.create_task(self._write_worker_loop())

    def _enqueue_write_request(self, req: _WriteRequest) -> None:
        """标记写入未排空并加入队列。"""
        self._write_drained.clear()
        self._write_queue.put_nowait(req)

    async def shutdown(self) -> None:
        """停止 worker 并排空队列（close 调用）。"""
        if self._write_worker_task is not None:
            self._write_worker_task.cancel()
            try:
                await self._write_worker_task
            except asyncio.CancelledError:
                pass
            self._write_worker_task = None
```

`_enqueue_write_request`/`_ensure_write_worker` 改名 `enqueue`/`ensure_worker`（或保留私有名，由 submit 内部调）。worker 循环内的 `self._ensure_write_worker` 调用不存在（worker 是被 ensure_worker 启动的），无需改。

- [ ] **Step 2: IndexManager 构造 coordinator，写入公开方法退化为薄壳**

`__init__` 构造：

```python
        from bot.index_manager.write_coordinator import _WriteCoordinator
        self._coordinator = _WriteCoordinator(
            metadata_store=self._metadata_store,
            vector_store=self._vector_store,
            collection_manager=self._collection_manager,
            memes_dir=self._memes_dir,
            deleted_dir=self._deleted_dir,
            replaced_dir=self._replaced_dir,
            rwlock=self._rwlock,
            write_drained=self._write_drained,
            image_pipeline=self._image_pipeline,
            entry_writer=self._entry_writer,
            validate_collection_selection_locked=self._validate_collection_selection_locked,
        )
```

门面的 `add`/`edit_text`/`set_speaker`/`add_tags`/`delete`/`move`/`create_collection`/`prepare_move`/`preview_move`：保留参数校验 + `self._shutting_down`/`self._refresh_active`（或 `self._sync_engine.is_refresh_active`，Task 4 后）检查，把"构造 `_WriteRequest` + await"改为构造 `req` 后 `return await self._coordinator.submit(req)`。

`create_collection`（行 587-620）：保留 `validate_collection_name` 与 shutdown/refresh 检查，构造 `_WriteRequest(op=CREATE_COLLECTION, ...)` 后 `submit`。

`_preview_move_locked`（924-967）：迁入 coordinator，门面 `preview_move`/`prepare_move` 调 `self._coordinator` 的对应方法（或保留为 coordinator 公开方法 `preview_move_locked`）。

从 `index_manager.py` 删除已迁走的方法（worker/executor/move 补偿/`_move_to_replaced`/`_preview_move_locked`/`_await_move_future`/`_get_collection_directory_identity`/`_enqueue_write_request`/`_ensure_write_worker`/`_write_worker_loop`）。`_validate_collection_selection_locked` 保留在门面。

- [ ] **Step 3: 跑验证**

```bash
.venv/bin/python -m pytest tests/unit/engine/ tests/integration/test_index_manager_api.py -x -q
ty check bot/index_manager/ bot/engine/
ruff check bot/index_manager/ bot/engine/
```
Expected: 全绿。重点看 add/delete/move/create_collection/add_tags 全套测试。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(index): 抽取 _WriteCoordinator 收口写入编排"
```

---

## Task 4: 抽取 SyncEngine（refresh 全流程）

**Files:**
- Create: `bot/index_manager/sync_engine.py`
- Modify: `bot/engine/index_manager.py`（`refresh` 转发；`_refresh_active`/`_refresh_task` 归 SyncEngine）

**Interfaces:**
- Consumes: `rwlock`（注入）、`_write_drained` Event（注入）、`EntryWriter`、`MetadataStore`、`VectorStore`、`CollectionManager`、目录路径。
- Produces: `SyncEngine.refresh() -> SyncResult`、`SyncEngine.is_refresh_active -> bool`、`SyncEngine.cancel_refresh()`。

**涉及方法（行号）**：`refresh`（1316-1351，`@timed`）、`_run_sync_internal`（2148-2181）、`_sync_phase0_consistency`（2183-2241）、`_get_chroma_ids`（2243-2249）、`_rebuild_all_from_sqlite`（2251-2272）、`_sync_phase1_delete`（2274-2340）、`_sync_collections_delete`（2342-2363）、`_sync_collections_add`（2365-2381）、`_sync_phase2_add`（2383-2506）、`_scan_meme_files`（2687-2718）、`_scan_collection_dir`（2720-2760）。

**关键决策**：
- `_refresh_active`/`_refresh_task` 归 SyncEngine。门面写入公开方法读取 `self._sync_engine.is_refresh_active` 替代原 `self._refresh_active`。
- `refresh` 的 `@timed(logger, "索引刷新")` 装饰器保留在 `SyncEngine.refresh`。
- `_sync_phase2_add` 内调 `self._write_entry` 改为 `self._entry_writer.write_entry`（注入的 EntryWriter）。若 phase2 调 `self._image_pipeline`（扫描后处理图片），改为注入的 image_pipeline——但 SyncEngine 构造时是否注入 ImagePipeline 取决于 phase2 是否需要。**核查**：若 `_sync_phase2_add` 内需压缩/OCR/embed 新发现的图片，则需注入 `ImagePipeline`；若 phase2 只写已处理好的条目，则只需 `EntryWriter`。读取 `_sync_phase2_add`（2383-2506）确认其是否调用 `_process_image_pipeline`。若调用，SyncEngine 注入 `ImagePipeline`。
- `close` 路径：门面 `close()` 调 `self._sync_engine.cancel_refresh()`（若有 `_refresh_task`）。

- [ ] **Step 1: 创建 SyncEngine**

创建 `bot/index_manager/sync_engine.py`。把上述方法迁入 `SyncEngine` 类。构造函数：

```python
class SyncEngine:
    """索引刷新引擎：phase0 一致性 / phase1 删除多余 / phase2 补缺失。

    Args:
        metadata_store: 元数据存储。
        vector_store: 向量存储。
        collection_manager: 合集管理器。
        memes_dir / no_text_dir / deleted_dir / replaced_dir: 目录路径。
        rwlock: 读写锁（注入，refresh 持写锁）。
        write_drained: 无排队且无执行中写请求时 set 的 Event（注入，refresh 拿写锁前等待）。
        entry_writer: 单条写入器。
        image_pipeline: 图片管道（若 phase2 需处理新图）。
    """

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        collection_manager: "CollectionManager",
        memes_dir: Path,
        no_text_dir: Path,
        deleted_dir: Path,
        replaced_dir: Path,
        rwlock: IndexRwLock,
        write_drained: asyncio.Event,
        entry_writer: EntryWriter,
        image_pipeline: ImagePipeline | None,
    ) -> None:
        self._metadata_store = metadata_store
        self._vector_store = vector_store
        self._collection_manager = collection_manager
        self._memes_dir = memes_dir
        self._no_text_dir = no_text_dir
        self._deleted_dir = deleted_dir
        self._replaced_dir = replaced_dir
        self._rwlock = rwlock
        self._write_drained = write_drained
        self._entry_writer = entry_writer
        self._image_pipeline = image_pipeline
        self._refresh_active = False
        self._refresh_task: asyncio.Task | None = None

    @property
    def is_refresh_active(self) -> bool:
        """是否正在执行刷新。"""
        return self._refresh_active

    @timed(logger, "索引刷新")
    async def refresh(self) -> SyncResult:
        """刷新索引（方法体复制自原 refresh，行 1317-1351）。"""
        ...

    async def cancel_refresh(self) -> None:
        """取消正在进行的刷新任务（close 调用）。"""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
```

`refresh` 方法体内 `self._refresh_active`/`self._refresh_task`/`self._write_drained`/`self._rwlock` 均为构造绑定属性。原 `refresh` 内 `self._refresh_task = asyncio.current_task()`（行 1333）保持。

- [ ] **Step 2: IndexManager 构造 SyncEngine，refresh 转发，状态读取改指**

`__init__` 构造（在 coordinator 之后）：

```python
        from bot.index_manager.sync_engine import SyncEngine
        self._sync_engine = SyncEngine(
            metadata_store=self._metadata_store,
            vector_store=self._vector_store,
            collection_manager=self._collection_manager,
            memes_dir=self._memes_dir,
            no_text_dir=self._no_text_dir,
            deleted_dir=self._deleted_dir,
            replaced_dir=self._replaced_dir,
            rwlock=self._rwlock,
            write_drained=self._write_drained,
            entry_writer=self._entry_writer,
            image_pipeline=self._image_pipeline,
        )
```

门面 `refresh`（行 1316-1351）替换为：

```python
    async def refresh(self) -> SyncResult:
        """委托 SyncEngine.refresh。"""
        return await self._sync_engine.refresh()
```

门面写入公开方法中所有 `if self._refresh_active:` 改为 `if self._sync_engine.is_refresh_active:`。从 `__init__` 删除 `self._refresh_active = False` 与 `self._refresh_task = ...`（已归 SyncEngine）。

门面 `close()`：增加 `await self._sync_engine.cancel_refresh()`（在 coordinator.shutdown 之后）。

从 `index_manager.py` 删除已迁走的 sync 方法（`_run_sync_internal`/`_sync_phase*`/`_get_chroma_ids`/`_rebuild_all_from_sqlite`/`_sync_collections_*`/`_scan_meme_files`/`_scan_collection_dir`）。

- [ ] **Step 3: 跑验证**

```bash
.venv/bin/python -m pytest tests/unit/engine/ tests/integration/test_index_manager_api.py -x -q
ty check bot/index_manager/ bot/engine/
ruff check bot/index_manager/ bot/engine/
```
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(index): 抽取 SyncEngine 承接刷新全流程"
```

---

## Task 5: 门面迁入子包 + 消费方改 import 路径

**Files:**
- Create: `bot/index_manager/manager.py`（从 `engine/index_manager.py` 迁入并定型）
- Modify: `bot/index_manager/__init__.py`（填充导出）
- Delete: `bot/engine/index_manager.py`
- Modify: 9 个插件 + `app_state.py` + `bot.py` + 测试文件的 import 路径

**Interfaces:**
- Produces: `bot.index_manager` 包导出 `IndexManager` + `index_types` 公开符号 + `IndexRwLock`。

**需改 import 的消费方清单（已核实）**：
- `bot/plugins/edit.py:26`、`delete.py:26`、`collection.py:21`、`setspeaker.py:28`、`refresh.py:19`、`add.py:28`、`rand.py:27`、`move.py:23`、`addtag.py:27`：`from bot.engine.index_manager import ...` → `from bot.index_manager import ...`
- `bot/app_state.py`：`from .engine.index_manager import IndexManager`（及异常）→ `from .index_manager import ...`
- `bot/bot.py`：`from .engine.index_manager import IndexManager` → `from .index_manager import IndexManager`
- 测试：`tests/unit/engine/test_index_manager*.py` 与 `tests/integration/test_index_manager_api.py` 中 `from bot.engine.index_manager import ...` / `from bot.engine import IndexManager` → `from bot.index_manager import ...`

**注意**：`from bot.engine.collection_manager import ...`、`from bot.engine.metadata_store import ...`、`from bot.engine.types import ...` 这些**不动**（通用模块留 engine）。

- [ ] **Step 1: 迁移门面到 bot/index_manager/manager.py**

把 `bot/engine/index_manager.py` 整体复制为 `bot/index_manager/manager.py`。调整其 import：
- `from .collection_manager import ...` → `from bot.engine.collection_manager import ...`
- `from .combined_searcher import ...` → `from bot.engine.combined_searcher import ...`
- `from .image_optimizer import ...` → `from bot.engine.image_optimizer import ...`
- `from .keyword_searcher import ...` → `from bot.engine.keyword_searcher import ...`
- `from .metadata_store import ...` → `from bot.engine.metadata_store import ...`
- `from .protocols import ...` → `from bot.engine.protocols import ...`
- `from .random_searcher import ...` → `from bot.engine.random_searcher import ...`
- `from .semantic_searcher import ...` → `from bot.engine.semantic_searcher import ...`
- `from .types import ...` → `from bot.engine.types import ...`
- `from .utils import ...` → `from bot.engine.utils import ...`
- `from .vector_store import ...` → `from bot.engine.vector_store import ...`
- `from .index_types import ...` → `from .index_types import ...`（同包内）
- `from .rwlock import IndexRwLock` → `from .rwlock import IndexRwLock`（同包内）
- 协作者 import：`from .entry_writer import EntryWriter`、`from .image_pipeline import ImagePipeline`、`from .write_coordinator import _WriteCoordinator`、`from .sync_engine import SyncEngine`（同包内）

确认 `TYPE_CHECKING` 块的 `CollectionManager`/`CollectionSummary`/`CollectionSelection` import 路径同步改为 `bot.engine.collection_manager` / `bot.engine.types`。

- [ ] **Step 2: 填充 bot/index_manager/__init__.py**

```python
"""index_manager 子包 — 索引管理薄编排层。

导出门面 IndexManager 及 index_types 的公开符号，供插件层与外部代码使用。
"""

from .index_types import (
    AddResult,
    AddTagResult,
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    CollectionSelectionExpiredError,
    CompressionError,
    CreateCollectionResult,
    DeleteResult,
    DuplicateMemeInCollectionError,
    DuplicateTextError,
    EditTextResult,
    EmbeddingError,
    FileSystemSnapshot,
    IndexAddCancelledError,
    IndexCorruptedError,
    IndexInfo,
    MemeMoveError,
    MemeMoveSourceExpiredError,
    MovePreview,
    MoveResult,
    MoveSourceSnapshot,
    OcrError,
    RefreshInProgressError,
    SetSpeakerResult,
    SyncResult,
    WriteOp,
)
from .manager import IndexManager
from .rwlock import IndexRwLock

__all__ = [
    "IndexManager",
    "IndexRwLock",
    "AddResult",
    "AddTagResult",
    "CollectionAlreadyExistsError",
    "CollectionCreateError",
    "CollectionPathConflictError",
    "CollectionSelectionExpiredError",
    "CompressionError",
    "CreateCollectionResult",
    "DeleteResult",
    "DuplicateMemeInCollectionError",
    "DuplicateTextError",
    "EditTextResult",
    "EmbeddingError",
    "FileSystemSnapshot",
    "IndexAddCancelledError",
    "IndexCorruptedError",
    "IndexInfo",
    "MemeMoveError",
    "MemeMoveSourceExpiredError",
    "MovePreview",
    "MoveResult",
    "MoveSourceSnapshot",
    "OcrError",
    "RefreshInProgressError",
    "SetSpeakerResult",
    "SyncResult",
    "WriteOp",
]
```

**核查**：对照原 `engine/__init__.py` 从 `index_manager` 重导出的符号列表，确保 `bot.index_manager.__init__` 导出集与消费方原从 `bot.engine` 拿到的索引符号集一致（不漏不增）。`OcrProvider` 不导出（在 `engine.protocols`）；`resolve_unique_filename` 不导出（在 `engine.utils`）。

- [ ] **Step 3: 更新 engine/__init__.py 移除迁走的符号**

`engine/__init__.py`：移除 `from .index_manager import (...)` 整块及 `__all__` 中对应的索引符号（`IndexManager`/`AddResult`/`CreateCollectionResult`/`DeleteResult`/`EditTextResult`/`IndexCorruptedError`/`MemeMoveError`/`MemeMoveSourceExpiredError`/`MovePreview`/`MoveResult`/`MoveSourceSnapshot`/`SyncResult` 等）。保留 `CollectionManager`/`MetadataStore`/`VectorStore`/searcher/`EmbeddingProvider`/`OcrProvider`（来自 protocols）/`resolve_unique_filename`（来自 utils）/各 provider 实现的导出。

- [ ] **Step 4: 改消费方 import 路径**

按上述清单逐文件改：9 个插件的 `from bot.engine.index_manager import ...` → `from bot.index_manager import ...`；`app_state.py` 与 `bot.py` 的 `from .engine.index_manager import ...` → `from .index_manager import ...`。注意 `app_state.py` 已在 Task 0 把 `OcrProvider` 改到 `from .engine.protocols import OcrProvider`，此步不动该行。

- [ ] **Step 5: 改测试 import 路径**

`tests/unit/engine/test_index_manager*.py` 与 `tests/integration/test_index_manager_api.py`：`from bot.engine.index_manager import ...` / `from bot.engine import IndexManager, ...` → `from bot.index_manager import ...`。测试体零改动。

- [ ] **Step 6: 删除 engine/index_manager.py**

确认门面已迁入 `bot/index_manager/manager.py` 且无残留引用后，删除 `bot/engine/index_manager.py`（及 `__pycache__`）。

- [ ] **Step 7: 跑全量验证**

```bash
.venv/bin/python -m pytest -x -q
ty check bot/ tests/
ruff check bot/ tests/
```
Expected: 全量 pytest 全绿（不只索引测试）；ty `All checks passed!`；ruff 无报错。

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(index): 门面迁入 bot/index_manager 并更新消费方 import 路径"
```

---

## Self-Review 结论

**Spec coverage**：spec §1-7 全覆盖——§1 范围 → Task 0 边界；§2 目录与依赖图 → File Structure；§3 四协作者职责 → Task 1-4；§4 数据流 → 各 Task 的调用改指；§5 边界细节（`OcrProvider` 移 protocols、`resolve_unique_filename` 直引、`_refresh_active`/`_shutting_down` 归属、测试改路径、异常归属）→ Task 0/4/5；§6 六步迁移 → Task 0-5 一一对应；§7 验证策略 → 每个 Task 的 Step 跑三工具 + Task 5 全量。

**Placeholder**：无 TBD/TODO；每个代码块给出真实签名或明确"复制自原文件行号范围"（执行者读取该范围即可获得完整方法体，避免在计划里重复 3000 行）。

**Type consistency**：`EntryWriter.write_entry`、`ImagePipeline.process`/`optimize_with_cancellation`/`validate_add_relative_path`/`move_to_no_text`、`_WriteCoordinator.submit`/`ensure_worker`/`shutdown`、`SyncEngine.refresh`/`is_refresh_active`/`cancel_refresh` 在各 Task 间签名一致；`_WriteRequest`/`WriteOp` 来自 `index_types`（Task 0 迁入 `bot.index_manager.index_types`），各 Task import 路径统一。
