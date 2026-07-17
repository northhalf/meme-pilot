# IndexManager 拆分设计

- 日期：2026-07-17
- 状态：待用户审阅
- 目标文件：`bot/engine/index_manager.py`（3041 行，单体 `IndexManager` 类，约 60 个方法）

## 1. 目标与范围

### 目标

把 3041 行的单体 `IndexManager` 按职责拆分为一个薄门面 + 四个内部协作者，物理上迁到与 `bot/engine/` 平级的新子包 `bot/index_manager/`。

### 迁移边界（已确认）

迁入 `bot/index_manager/`：

- 门面 `IndexManager`
- 四协作者：`_WriteCoordinator`、`ImagePipeline`、`SyncEngine`、`EntryWriter`
- `index_types.py`（异常 / 枚举 / dataclass / 结果类型）
- `rwlock.py`（`IndexRwLock`，索引并发专属，已核实 `engine/` 其他模块不使用）

留 `bot/engine/`（较为通用的类不迁移）：

- `metadata_store`、`vector_store`
- `keyword_searcher`、`random_searcher`、`semantic_searcher`、`combined_searcher`
- `image_optimizer`
- `collection_manager`
- `protocols`、`types`、`utils`
- 各 provider 实现（`openai_ocr` / `paddle_ocr` / `rapidocr_ocr` / `openai_embedding` / `google_embedding`）、`provider_factory`

### 公开 API 完全不变

- `IndexManager` 的所有公开方法签名不变。
- `index_types` 导出的异常 / 结果类型名称不变。
- 消费方与测试的改动仅限 import 路径，不含调用方式。

### 非目标

- 不动 `engine/` 留下的模块内部实现。
- 不调整横切模块（`protocols` / `types` / `utils`）归属（`OcrProvider` 移入 `protocols.py` 是唯一例外，见 §5）。
- 不改任何公开方法签名。
- 不借机改业务逻辑。

## 2. 目标目录结构与依赖图

```
bot/index_manager/                 # 新子包，与 bot/engine/ 平级
  __init__.py                      # 导出 IndexManager + index_types 公开符号 + IndexRwLock
  manager.py                       # IndexManager 门面（~500 行）
  write_coordinator.py             # _WriteCoordinator
  image_pipeline.py                # ImagePipeline
  sync_engine.py                   # SyncEngine
  entry_writer.py                  # EntryWriter
  index_types.py                   # 从 engine/ 迁入
  rwlock.py                        # 从 engine/ 迁入
```

`bot/index_manager/__init__.py` 导出：`IndexManager`、`IndexRwLock`、以及 `index_types` 里当前由 `engine/__init__.py` 重导出的公开符号（`AddResult` / `CreateCollectionResult` / `DeleteResult` / `EditTextResult` / `IndexInfo` / `MovePreview` / `MoveResult` / `MoveSourceSnapshot` / `SetSpeakerResult` / `SyncResult` / 各异常类 / `WriteOp` 等）。`OcrProvider` 不在此列（移到 `engine/protocols.py`，见 §5）；`resolve_unique_filename` 不在此列（由 `engine/__init__.py` 直接从 `.utils` 重导出，见 §5）。

### 依赖方向（扁平，无横向依赖）

```
                    bot.index_manager.manager（门面）
                   /      |        |         \
          coordinator  sync_engine  image_pipeline  entry_writer
               |           |                           |
          image_pipeline  entry_writer                  |
               |           |                           |
               +----- 依赖 engine（留） -----+          |
                     stores / searchers / optimizer / collection_manager
                     protocols / types / utils
```

- 门面 → 四协作者（构造时注入 `rwlock`、`_write_drained` Event、stores、providers、目录）。
- `_WriteCoordinator` → `ImagePipeline`（执行器内压缩图片时）、`EntryWriter`（`_execute_add` 写条目时）。
- `SyncEngine` → `EntryWriter`（phase2 写条目时）。
- 四协作者 → `bot.engine` 的通用模块（stores / searchers / optimizer / collection_manager / protocols / types / utils）——唯一跨包方向，符合"通用类留 engine"的边界。
- 无协作者间横向依赖（coordinator 不依赖 sync，反之亦然）。
- `rwlock` 跟门面同包，门面自用并注入协作者。

### `engine/__init__.py` 调整

- 移除已迁走的符号：`IndexManager` 及 `index_types` 重导出项、`IndexRwLock`（如有）。
- `OcrProvider` 改从 `.protocols` 重导出（已移到 `protocols.py`）。
- 新增 `from .utils import resolve_unique_filename` 重导出（原经 `index_manager.__all__` 的历史路径改为直引）。
- 保留 provider 注册逻辑与 `CollectionManager` / `MetadataStore` / `VectorStore` / searcher 等仍属 engine 的导出。

## 3. 各协作者的职责与接口

### `EntryWriter`（无状态 helper，最先抽取）

- 职责：把单条 `MemeEntry` + 向量写入两个 Store（即现 `index_manager._write_entry`，约 180 行）。
- 持有状态：无。构造时注入 `MetadataStore`、`VectorStore`、所需目录路径。
- 接口：`async def write_entry(self, entry: MemeEntry, vector: list[float] | None, ...) -> None`（签名对齐现 `_write_entry`，去掉 `self._` 前缀依赖，改为注入的 store 引用）。
- 被调用：`_WriteCoordinator._execute_add`、`SyncEngine._sync_phase2_add`。

### `ImagePipeline`（自包含，含 optimizer 锁表）

- 职责：压缩 → OCR → Embed 管道编排 + optimizer 并发锁表 + 取消控制。
- 搬入方法：`_process_image_pipeline`、`_optimize_with_cancellation`、`_optimizer_target_lock`、`_wait_task_through_cancellation`、`_release_optimizer_lock_entry`、`_cleanup_pipeline_output`、`_validate_add_relative_path`、`_move_to_no_text`、`_has_supported_ext`。
- 持有状态：`ImageOptimizer`、`OcrProvider`、`EmbeddingProvider`、`_memes_dir` / `_no_text_dir`、optimizer 锁表 `_optimizer_target_locks` + `_optimizer_registry_guard`。
- 接口：`async def process(self, relative_path: str) -> PipelineResult`（含压缩后路径、OCR 文本、embedding 向量等），以及内部取消控制方法。
- 被调用：`_WriteCoordinator._execute_add`、`_WriteCoordinator._execute_move`。
- 关键：optimizer 锁表单一归属在此，`_execute_move` 不再从门面借锁。

### `_WriteCoordinator`（写入编排全收口）

- 职责：worker 循环 + queue + future 分发 + 七个 `_execute_*` + move 补偿。
- 搬入方法：`_enqueue_write_request`、`_ensure_write_worker`、`_write_worker_loop`、`_await_move_future`、`_execute_create_collection`、`_execute_edit_text`、`_execute_set_speaker`、`_execute_add_tags`、`_execute_delete`、`_execute_move`、`_compensate_move`、`_restore_move_file`、`_move_to_replaced`、`_resolve_move_target_name`、`_resolve_move_paths`、`_preview_move_locked`。
- 持有状态：`_write_queue`、`_write_worker_task`、`_write_drained`（注入的 Event 引用，操作权在此）。
- 注入：两 Store、目录、`rwlock`、`_write_drained` Event、`ImagePipeline`、`EntryWriter`、`CollectionManager`、`_shutting_down` 读取回调或门面引用（worker 在关闭时拒绝新请求并排空）。
- 接口：公开方法对齐现 `IndexManager` 的写入入口语义——`async def enqueue_create_collection(...)`、`enqueue_add(...)`、`enqueue_edit_text(...)` 等，每个做"构造 `_WriteRequest` → enqueue → await future"。门面的 `add` / `edit_text` / ... 退化为参数校验后调对应 `enqueue_*`。
- 门面协调：`_refresh_active` / `_shutting_down` 检查仍在门面公开方法里（refresh 与写入互斥的入口判断），coordinator 只管执行。

### `SyncEngine`（refresh 全流程）

- 职责：`refresh` 入口 + phase0-2 + 扫描 + chroma 对账。
- 搬入方法：`_run_sync_internal`、`_sync_phase0_consistency`、`_sync_phase1_delete`、`_sync_phase2_add`、`_sync_collections_delete`、`_sync_collections_add`、`_get_chroma_ids`、`_rebuild_all_from_sqlite`、`_scan_meme_files`、`_scan_collection_dir`。
- 持有状态：`_refresh_active`、`_refresh_task`。
- 注入：两 Store、目录、`rwlock`、`_write_drained` Event、`EntryWriter`。
- 接口：`async def refresh(self) -> SyncResult`、`is_refresh_active` 属性、刷新任务生命周期管理。门面 `refresh()` 转发。
- 与 coordinator 协调：拿写锁前 `await self._write_drained.wait()`（Event 由 coordinator 维护、注入）。

### `IndexManager` 门面（瘦身后约 500 行）

保留：

- `__init__`（构造四协作者 + 注入 `rwlock` / `_write_drained` Event）。
- `load`、`close`、`entry_count`。
- 所有搜索方法：`search` / `random_search` / `semantic_search` / `search_combined` 及对应 `_locked` 内部实现，`search_for_scope` / `random_search_for_scope` / `random_search_for_scope_snapshot` / `semantic_search_for_scope` / `search_combined_for_scope`。
- 查询与合集：`info`、`get_entry`、`get_selected_collection`、`_validate_collection_selection_locked`、`validate_collection_selection`、`list_collections`、`switch_collection`、`resolve_entry`。
- 写入公开 API 薄壳：`add` / `edit_text` / `set_speaker` / `add_tags` / `delete` / `move` / `create_collection` / `prepare_move` / `preview_move`——参数校验 + `_refresh_active` / `_shutting_down` 检查 + 调 `coordinator.enqueue_*`。

持有状态：

- `_rwlock`、`_write_drained` Event、四协作者引用、`_collection_manager`。
- stores / providers 引用（注入给协作者）。
- 目录路径、`read_timeout` / `add_user_timeout`、`_shutting_down`（`close()` 时设置）。

## 4. 数据流

### 写入路径（以 `add` 为例）

```
插件 add.py
  → app_state.get_index_manager().add(relative_path, ...)
  → IndexManager.add()  [门面]
       参数校验 + _validate_add_relative_path（委托 ImagePipeline）
       _refresh_active / _shutting_down 检查
       构造 _WriteRequest(op=ADD, future=...)
       → _WriteCoordinator.enqueue_add(req)
            self._write_queue.put_nowait(req)
            返回 req.future
       await future
  ↓ (worker 线程内)
  _WriteCoordinator._write_worker_loop()
       取 req → async with self._rwlock.write()  [注入的 rwlock]
       分发到 _execute_add(req)
            → ImagePipeline.process(relative_path)  [压缩→OCR→embed，含 optimizer 锁]
            → EntryWriter.write_entry(entry, vector)  [写两库]
            set result 到 future
       维护 _write_drained Event
```

- 锁：写锁在 coordinator 的 worker 内获取（注入的 rwlock）。
- Event：`_write_drained` 在 queue 空 + 无执行中时 set，coordinator 维护。

### refresh 路径

```
插件 refresh.py → IndexManager.refresh()  [门面转发]
  → SyncEngine.refresh()
       _refresh_active = True
       await self._write_drained.wait()  [等 coordinator 排空写入]
       async with self._rwlock.write()  [注入的 rwlock]
            phase0 一致性 → phase1 删除多余 → phase2 补缺失
              phase2 内 → EntryWriter.write_entry(...)  [写两库]
       _refresh_active = False
```

- 互斥：`_refresh_active` 由 SyncEngine 维护；门面写入公开方法读它做"refresh 进行中拒绝写入"判断。

### 搜索路径（留门面，无协作者）

```
插件 → IndexManager.search(keyword, collection_id=...)
  async with self._rwlock.read()  [门面自有锁]
       → self._keyword_searcher.search_in(entries, keyword)  [注入的 searcher]
  返回结果
```

- scope 变体在读锁内额外 `_collection_manager.get_selected(scope)` 再搜索，逻辑不变。

### close 路径

```
IndexManager.close()
  → _WriteCoordinator.shutdown()  [停 worker，等排空]
  → SyncEngine.cancel_refresh()  [若有]
  → 两个 Store.close()
```

## 5. 错误处理、状态可见性与边界细节

1. **`_refresh_active` / `_shutting_down` 跨协作者可见性**：
   - `_refresh_active` 归 `SyncEngine`，暴露 `is_refresh_active` 属性；门面写入公开方法调 `self._sync_engine.is_refresh_active` 做判断。
   - `_shutting_down` 归门面（`close()` 时设置），注入给 coordinator 使 worker 在关闭时拒绝新请求并排空。
   - 状态单一归属，避免三处各自维护一份 bool 漂移。

2. **`OcrProvider` 归属**：从 `index_types.py` 移到 `engine/protocols.py`（与 `EmbeddingProvider` 同住）。已核实真实使用点为 `provider_factory.py`（第 9/12/36 行，运行时 import + 类型标注）与 `app_state.py`（第 17/27/40/139 行，运行时 import + 标注）；3 个 OCR 实现文件仅 docstring 提及，非运行时依赖。改动：`provider_factory.py` 改 `from .protocols import OcrProvider`；`app_state.py` 改 `from .engine.protocols import OcrProvider`；3 个 OCR 实现的 docstring 路径同步。`index_types.py` 随迁时不再带 `OcrProvider`。engine provider 层就近引用、不跨包。

3. **`resolve_unique_filename`**：由 `engine/__init__.py` 直接 `from .utils import resolve_unique_filename` 重导出（`utils` 留 engine）；消费方 `from bot.engine import resolve_unique_filename` 路径不变。`index_manager.__all__` 移除该项，`bot.index_manager.__init__` 不再重导出它。

4. **测试**：测试文件改 import 路径为 `from bot.index_manager import IndexManager`，与消费方同批改；测试体零改动（公开 API 不变）。按特性拆分的测试作为每个协作者抽取后的回归安全网。

5. **异常类型归属**：所有异常随 `index_types` 迁到 `bot.index_manager`；消费方 `from bot.engine.index_manager import XxxError` 改 `from bot.index_manager import XxxError`；`engine/__init__.py` 不再重导出这些异常（抽取时核查 engine 内部是否有消费方）。

## 6. 迁移步骤（逐个抽取 + 每步全测）

每步是可独立提交 / 回滚的原子改动。每步完成后跑 `pytest tests/unit/engine/ tests/integration/test_index_manager_api.py` + `ty check` + `ruff check`。

### 步骤 0：建子包骨架 + 移动 `index_types` / `rwlock` / `OcrProvider`

- 新建 `bot/index_manager/`（空 `__init__.py`）。
- `engine/index_types.py` → `bot/index_manager/index_types.py`；从中移出 `OcrProvider` 到 `engine/protocols.py`。
- `engine/rwlock.py` → `bot/index_manager/rwlock.py`。
- `engine/__init__.py`：移除 `IndexManager` 及 `index_types` 重导出项、`OcrProvider`、`IndexRwLock`（如有）；新增 `from .utils import resolve_unique_filename` 重导出；`OcrProvider` 改从 `.protocols` 重导出。
- `provider_factory.py` / `app_state.py`：`OcrProvider` 改从 `engine.protocols` import。
- 此时 `index_manager.py` 仍在 `engine/`，改为 `from bot.index_manager.index_types import ...` / `from bot.index_manager.rwlock import IndexRwLock`。
- 跑全测 + ty + ruff。这是面最广的一步，但只移数据 / 锁 / 协议，不动逻辑。

### 步骤 1：抽 `EntryWriter`

- 在 `bot/index_manager/entry_writer.py` 新建无状态 `EntryWriter`，搬入 `_write_entry`。
- `index_manager.py` 的 `_execute_add` / `_sync_phase2_add` 改调 `self._entry_writer.write_entry(...)`。
- 跑全测 + ty + ruff。

### 步骤 2：抽 `ImagePipeline`

- 在 `bot/index_manager/image_pipeline.py` 新建 `ImagePipeline`，搬入管道 + optimizer 锁表全家 + 路径辅助。
- `_execute_add` / `_execute_move` 改调 `self._image_pipeline.process(...)`。
- 跑全测 + ty + ruff。

### 步骤 3：抽 `_WriteCoordinator`

- 在 `bot/index_manager/write_coordinator.py` 新建 `_WriteCoordinator`，搬入 worker + queue + 七个 `_execute_*` + move 补偿。
- 门面写入公开方法退化为"校验 + `_refresh_active` / `_shutting_down` 检查 + 构造 `_WriteRequest` + `coordinator.enqueue_*` + await"。
- 跑全测 + ty + ruff。

### 步骤 4：抽 `SyncEngine`

- 在 `bot/index_manager/sync_engine.py` 新建 `SyncEngine`，搬入 `refresh` + phase0-2 + 扫描 + chroma 对账。
- 门面 `refresh()` 转发；`_refresh_active` 归 SyncEngine，门面通过 `is_refresh_active` 读取。
- 跑全测 + ty + ruff。

### 步骤 5：门面迁入子包 + 消费方改 import 路径

- `engine/index_manager.py` → `bot/index_manager/manager.py`，门面定型。
- `bot/index_manager/__init__.py` 导出 `IndexManager` + `index_types` 公开符号 + `IndexRwLock`。
- 消费方 9 个插件 + `app_state.py` + `bot.py`：`from bot.engine.index_manager import ...` / `from .engine.index_manager import ...` 改 `from bot.index_manager import ...` / `from .index_manager import ...`。
- 测试 import 路径同步改。
- 跑全测 + ty + ruff。

### 顺序考量

步骤 0 先迁 `index_types` / `rwlock` / `OcrProvider`，让后续四步在 `bot/index_manager/` 包内就地新建协作者、import 路径一次到位。步骤 1→2→3→4 按"无依赖先行"排序：`EntryWriter` 无依赖最先；`ImagePipeline` 只被 coordinator 用；coordinator 依赖前两者；`SyncEngine` 依赖 `EntryWriter`。步骤 5 最后统一迁门面与消费方。

每步的提交粒度由用户审核（main 分支不自行提交）。

## 7. 验证策略

### 每步验证清单

- 测试：`pytest tests/unit/engine/ tests/integration/test_index_manager_api.py`（每步必跑）。覆盖 add / delete / move / info / create_collection / add_tags / 主流程 / 集成 API，作为回归安全网。
- 类型检查：`ty check bot/index_manager/ bot/engine/ bot/plugins/ bot/app_state.py bot/bot.py`（每步必跑）。
- Lint：`ruff check bot/index_manager/ bot/engine/ bot/plugins/ bot/app_state.py bot/bot.py`（每步必跑）。

### 最终验证（步骤 5 后）

- 跑全量 `pytest`（确认消费方 import 改动没波及其他插件）。
- `docker compose build bot` 验证容器构建（可选，由用户决定）。

### 验收标准

- 每步三个工具全绿。
- 公开 API 零变化：`IndexManager` 的所有公开方法签名、`index_types` 导出的异常 / 结果类型名称不变——测试体零改动即为证明。
- `engine/index_manager.py` 最终被移除（无残留旧文件）。
- `bot/index_manager/` 下门面约 500 行、四协作者各约 600 行以内（粗目标，非硬约束）。
- 消费方 9 插件 + `app_state.py` + `bot.py` import 路径全部指向 `bot.index_manager`，无 `from bot.engine.index_manager` 残留。

### 风险与回滚

- 最大风险在步骤 0（触动 import 路径最多）和步骤 3（coordinator 收口最复杂逻辑）。任一步测试红则回滚该步（每步独立提交）。
- `rwlock` 随迁已核实无 engine 内部其他消费者，无断引用风险。
- `OcrProvider` 移 `protocols.py` 已核实真实使用点（`provider_factory.py` + `app_state.py`），改动可控。
