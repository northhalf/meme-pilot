# 设计：图片默认转 WebP 存储 + 迁移脚本

> 日期：2026-07-10
> 状态：待用户审阅
> 关联文档：`docs/PRD.md`、`CONTEXT.md`、`docs/api/API.md`、`README.md`、`.env.example`、`docker-compose.yml`

## 1. 目标与背景

### 1.1 目标

1. 新增图片（`/add`、启动 sync、`/refresh`）默认转换为 WebP 格式存储，以统一存储格式、降低磁盘占用。
2. 提供环境变量开关 `CONVERT_TO_WEBP`：开启（默认）时转 WebP；关闭时按传输格式（原始格式）存储，即维持现状行为。
3. 提供迁移脚本，将 `memes/` 目录内已有的非 WebP 表情包批量转换为 WebP，并保持 sqlite 索引一致。

### 1.2 背景

现有 `ImageOptimizer.optimize(image_path)` 对图片做**同格式**压缩并原地覆盖，不改扩展名；`filename` 在 `_process_image_pipeline` 中贯穿 OCR/embed/`metadata_store.add`，全程不变。转 WebP 要求文件改名为 `.webp`，因此需让 `optimize` 产出最终路径，并让该路径回传到 sqlite 写入。`scripts/png_to_jpg.py` 已提供「转换 + 改名 + 更新 image_path + 失败回滚」的迁移脚本范式。

## 2. 决策汇总

| # | 决策点 | 选定方案 |
|---|--------|----------|
| ① | WebP 编码模式 | 统一有损 `quality=85` |
| ② | GIF 处理 | 一律转 WebP；动图保留 `duration`/`loop`/`transparency` 转 animated WebP |
| ③ | 转换后体积变大 | 强制转（不保留原格式，不比较体积） |
| ④ | 开关关闭语义 | 保持原格式 + 仍同格式压缩（=现状）；`.bmp` 仍 PASS_THROUGH |
| ⑤ | 迁移同名冲突 | `resolve_unique_filename` 追加 `_n` 序号改名 |
| ⑥ | 在线转换失败 | 降级：保留原格式继续入库（不删图、不抛错、记 warning） |
| ⑦ | 迁移是否强制转 | 与③一致，强制转（不比较体积） |
| ⑧ | 迁移范围 | 默认仅 `memes/`；提供 `--include-archives` 处理归档目录 |
| ⑨ | 迁移后原文件 | 移到 `memes_migrated_backup/`（可手动恢复） |

因统一有损编码，项目文档现有「无损压缩」措辞统一改为「图片压缩/转换」。**开关开启时，统一有损覆盖所有格式**，包括已是 `.webp` 的源文件（有损 `quality=85` 重编码，不再 lossless）；开关关闭时，`.webp` 源维持现有 lossless 重编码（决策④「=现状」）。

## 3. 架构与范围

- 新增环境变量 `CONVERT_TO_WEBP`（bool，默认 `true`）。
- 影响入库路径：`/add`、启动 sync、`/refresh`。**已存量图不由开关自动转换**，由迁移脚本处理。
- 转换职责集中在 `ImageOptimizer`（方案 A：`optimize` 内部按开关决定转换或同格式压缩，并返回最终路径）。`IndexManager` 管道回传最终 filename 贯穿 sqlite 写入。
- `ImageOptimizerProtocol` 签名不变（仍 `optimize(image_path) -> OptimizeResult`），仅结果对象增加字段，向后兼容。
- 因统一有损，文档「无损压缩」措辞改为「图片压缩/转换」。

## 4. 组件改动

### 4.1 `bot/engine/image_optimizer.py`

- `OptimizeResult` 增加字段 `output_path: str`（默认 `""`；`optimize` 在所有返回点显式设置为最终文件路径）。
  - 未转换（同格式压缩 / 跳过）：`output_path = str(path)`（原路径）。
  - 转 WebP 成功：`output_path = str(new_webp_path)`。
- `ImageOptimizer.__init__` 增加参数 `convert_to_webp: bool = False`（由 `bot.py` startup 通过 `config.read_convert_to_webp()` 注入）。
- `optimize(image_path)` 行为分支（按优先级）：
  1. 源扩展名为 `.webp`：`output_path=原路径`（已是目标格式不改名）。开关开启时有损重编码（`quality=85`，去掉 `lossless=True`）；开关关闭时维持现有 `_compress_webp`（lossless 重编码，现状）。遵循压缩「变小才覆盖」语义，不适用决策③强制覆盖（不涉及格式转换）。
  2. `convert_to_webp=True` 且源扩展名 ∈ {`.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`}：调用新增 `_convert_to_webp(path)` 转 webp，返回 `OptimizeResult(output_path=<新 .webp 路径>, ...)`，**强制转换不比较体积**。注意 `.bmp` 在开关开启时转为 webp（不再 PASS_THROUGH）。
  3. `convert_to_webp=False`：维持现有同格式压缩逻辑，`output_path=原路径`；`.bmp` 仍 PASS_THROUGH。
  4. 转换失败：抛 `RuntimeError`（由上层 pipeline 捕获降级，见 4.2）。
- 新增内部方法 `_convert_to_webp(path: Path) -> Path`：
  - 用 Pillow 打开原图；按源格式做必要预处理（保留透明通道：P/RGBA 模式保持 RGBA 保存有损 WebP；非透明图转 RGB；GIF 动图提取所有帧）。
  - 保存为 `quality=85` 有损 WebP；GIF 动图用 `save_all=True` + `append_images` + `duration`/`loop`/`transparency` 保留动画。
  - 目标路径 `<stem>.webp`；若已存在且非当前源文件，用 `resolve_unique_filename` 追加 `_n` 序号（见 4.5）。
  - 原子写入：先写 `.tmp`，`os.replace` 到目标 `.webp`，再 `unlink` 原文件；**无条件覆盖目标**（不比较体积）。
  - 失败时清理临时文件 `.tmp` 与已生成的 `.webp`（若 `os.replace` 已完成但 `unlink` 原文件前失败），原文件保留，抛 `RuntimeError`。
- 模块 docstring 更新：「无损压缩」->「图片压缩/转换」，补充 WebP 转换说明。
- `webp_quality` 参数保留：用于开关关闭时 `.webp` 源的 lossless 重编码（`_compress_webp`）；开关开启时 `.webp` 源用有损 `quality=85`（与跨格式转换一致）。

### 4.2 `bot/engine/index_manager.py`

- `_process_image_pipeline(filename)` 返回值由 `tuple[str, list[float]]` 改为 `tuple[str, str, list[float]]`（`final_filename, text, embedding`）：
  - 调 `await self._optimizer.optimize(str(image_path))` 后读 `result.output_path`；`final_filename = Path(result.output_path).name`（转换时为 `.webp`，否则等于原 `filename`）。
  - 用 `result.output_path` 做 OCR/embed（而非原 `image_path`）。
  - **降级**：`optimize` 抛异常时，`except` 捕获、记 `logger.warning`、清理可能已生成的 `.webp` 孤儿文件（若 `_convert_to_webp` 在 `os.replace` 后、`unlink` 原文件前失败）、回退 `final_filename = filename`、`image_path` 保持原路径，继续 OCR/embed，不抛错。
  - 空文本仍返回 `(final_filename, "", [])`。
- `add()`：将 `final_filename` 写入 `_WriteRequest.filename`（覆盖插件传入的原 filename）；`_process_image_pipeline` 调用处解包三元组。
- sync 阶段2 `_sync_phase2_add`：
  - `raw = await asyncio.gather(*(self._process_image_pipeline(fn) for fn in new_files), ...)` 解包三元组。
  - `success: dict[str, tuple[str, list[float]]]` 的 key 改为 `final_filename`（转换后可能为 `.webp`）。
  - **并发同名去重**：并行转换时多张新增图（同 stem 不同扩展名）可能产出同名 `final_filename`；写入 `success` 前对 key 去重，冲突时对后续张调 `resolve_unique_filename` 重新命名为 `<stem>_n.webp` 并实际 rename 文件，避免后写覆盖先写导致丢图。
  - 三分类（无文字移图 / 去重 / 新增）中所有 `filename` 引用改为 `final_filename`；`_move_to_no_text`、`_move_to_replaced`、`metadata_store.add` 均用 `final_filename`。
- `_write_entry` 与去重替换分支：`filename` 参数即 `final_filename`；现有 `old_image_path != filename` 判断天然适配（新旧同名时不归档，正常）。
- `ImageOptimizerProtocol` 不变。
- `_process_image_pipeline` docstring 与 Raises 更新（降级说明）。

### 4.3 `bot/config.py`

- 新增 `read_convert_to_webp() -> bool`：读取 `CONVERT_TO_WEBP` 环境变量，默认 `true`；解析为 bool，无效值回退 `true`（参考 `read_ocr_text_score` 风格：strip/lower，`"true"/"1"/"yes"` 为真，`"false"/"0"/"no"` 为假，其余回退默认）。
- 加入 `__all__`。

### 4.4 `bot/bot.py`

- startup 创建 `ImageOptimizer` 时传入 `convert_to_webp=read_convert_to_webp()`。

### 4.5 `resolve_unique_filename` 共用

- 现位于 `index_manager.py`。`_convert_to_webp`（image_optimizer）与迁移脚本均需用。
- 方案：将 `resolve_unique_filename` 移至 `bot/engine/utils.py`（已有 `vector_norm` 等共用工具），`index_manager` 改为从 `utils` 导入；image_optimizer 与迁移脚本也从 `utils` 导入。避免跨层依赖与代码复制。

### 4.6 `bot/plugins/meme_add.py`

- `_auto_filename` 仍按原扩展名生成 `meme_<ts>_<hash8>.<ext>`（下载时尚不知是否转换）。
- 转换与改名在 `_process_image_pipeline` 内完成；插件层无需感知 `final_filename`，`AddResult` / 回复展示的 id 等不变（image_path 由 store 记录为 `.webp`）。
- 无需改动插件逻辑，仅需确认下载后文件名用原扩展名、pipeline 内改名后磁盘最终为 `.webp`。

## 5. 数据流

### 5.1 `/add`

```
插件下载 meme_<ts>_<hash8>.<ext> 到 memes/
  -> IndexManager.add(filename)
  -> _process_image_pipeline(filename):
       optimize(开关开) -> 转 webp -> meme_<ts>_<hash8>.webp，删原文件
       output_path = memes/meme_<ts>_<hash8>.webp
       OCR(output_path) -> embed(text)
       return (final_filename="meme_<ts>_<hash8>.webp", text, embedding)
  -> 入队 _WriteRequest(filename=final_filename)
  -> _write_entry -> metadata_store.add(final_filename, text, ...)
  -> sqlite image_path = meme_<ts>_<hash8>.webp
```

### 5.2 `/refresh` / 启动 sync 阶段2

```
扫描 memes/ 得 new_files（原扩展名）
  -> 并行 _process_image_pipeline(fn) -> 返回 final_filename（可能 .webp）
  -> success[final_filename] = (text, embedding)
  -> 三分类（无文字移图 / 去重 / 新增）均用 final_filename
```

### 5.3 降级

```
optimize 抛异常
  -> logger.warning("转 webp 失败，降级保留原格式: filename=%s, error=%s", ...)
  -> 清理可能已生成的 .webp 孤儿（os.replace 后 unlink 前失败的情况）
  -> final_filename = 原 filename；image_path = 原路径
  -> 继续 OCR/embed -> 入库（image_path=原扩展名，未转 webp）
```

## 6. 错误处理

- **转换失败（在线）**：降级保留原格式继续入库，不删图、不抛错、记 warning（见 5.3）。
- **在线同名冲突**：`<stem>.webp` 已存在（hash8 碰撞或去重场景）-> `resolve_unique_filename` 生成 `<stem>_n.webp`。
- **去重替换分支**：`_write_entry` 现有 `old_image_path != filename` 判断天然适配；新旧同名时不归档旧图（正常）。
- **迁移脚本失败**：见 7.4。
- **`CONVERT_TO_WEBP` 无效值**：回退默认 `true`。

## 7. 迁移脚本 `scripts/convert_memes_to_webp.py`

### 7.1 定位

独立于 `CONVERT_TO_WEBP` 开关，直接批量转换 `memes/` 内已有非 WebP 图片为 WebP 并更新 sqlite `image_path`。参考 `scripts/png_to_jpg.py` 骨架。

### 7.2 命令行参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--memes-dir` | `MEMES_DIR` | 表情包目录 |
| `--db-path` | `INDEX_DB_PATH` | sqlite 路径 |
| `--quality` | `85` | WebP 质量（与在线一致） |
| `--dry-run` | `false` | 模拟运行，不修改文件与数据库 |
| `--include-archives` | `false` | 同时处理 `memes_deleted/`、`memes_replaced/`、`meme_no_text/`（仅转文件+备份，不更新 sqlite；归档目录图转后原文件移到 backup-dir） |
| `--backup-dir` | `memes_migrated_backup/` | 原文件备份目录 |
| `-v/--verbose` | `false` | DEBUG 日志 |

### 7.3 转换逻辑

1. 收集目标目录下所有扩展名 ∈ {`.jpg`,`.jpeg`,`.png`,`.gif`,`.bmp`} 的文件（大小写不敏感），按路径升序。
2. 逐张，严格顺序与回滚：
   a. 转 WebP（`quality=85`，gif 保留动画，**强制转不比较体积**）；若 `<stem>.webp` 已存在且非当前源文件 -> `resolve_unique_filename` 改名 `<stem>_n.webp`。转换失败 -> 删 `.webp`、保留原文件、计失败跳过。
   b. 更新 sqlite：`get_by_filename(旧相对路径)` -> 有记录则 `update(entry.id, image_path=新相对路径)`；`UNIQUE` 冲突（`DuplicateEntryError`）-> 删 `.webp`、保留原文件与旧 sqlite、计失败跳过。DB 无记录则跳过 update（仅转文件+备份）。
   c. 原文件移到 `--backup-dir`（`shutil.move`，跨设备安全；冲突追加序号）。移备份失败 -> sqlite 已指向新 `.webp`（索引一致），原文件暂留 `memes/`，计警告不计失败（不影响索引正确性）。
3. 失败回滚原则：转换失败删 `.webp` 保留原文件；update 失败删 `.webp` 保留原文件与旧 sqlite；移备份失败不回滚（索引已一致）。每步失败均不阻塞后续图片。
4. 不重新 OCR、不重新 embed、不动 chroma、不动 `meme_tag`。
5. 可重入：已转成功的原文件已移到 backup，`memes/` 不再收集；若残留孤儿 `.webp`（上次中途失败）且 sqlite 已指向该 `.webp`，视为已处理跳过；若孤儿 `.webp` 无 sqlite 记录，清理后重新转换原文件。
6. `--include-archives` 时，归档目录的图仅转文件+备份，不更新 sqlite（已无索引记录）。

### 7.4 输出

`完成：成功 N，跳过 M，失败 K`（失败时退出码 1）。提示：建议 Bot 未运行时执行，避免 sqlite 写锁冲突。

## 8. 环境变量与文档同步

### 8.1 环境变量

- `.env.example`：新增 `CONVERT_TO_WEBP=true` 及注释（默认转 webp；关闭则按传输格式存储）。
- `docker-compose.yml`：`bot.environment` 新增 `CONVERT_TO_WEBP=${CONVERT_TO_WEBP:-true}`。

### 8.2 文档

- `README.md`：功能说明补充 WebP 转换；部署步骤提及开关；依赖说明「无损压缩」->「图片压缩/转换」；项目结构补 `scripts/convert_memes_to_webp.py` 与 `memes_migrated_backup/`。
- `docs/PRD.md`：3.4 `/add`、3.6 索引管理（启动同步/`/refresh`）、4.3 安全/环境变量（可选变量加 `CONVERT_TO_WEBP`）、5 边界情况（转换失败降级、开关关闭行为）、7 依赖清单相关处更新；「无损压缩」->「图片压缩/转换」。
- `CONTEXT.md`：「图片无损压缩」术语改为「图片压缩/转换」并补充 WebP 转换与 `CONVERT_TO_WEBP` 说明；新增 `memes_migrated_backup/` 术语。
- `docs/api/API.md`：
  - `image_optimizer.md`：`OptimizeResult.output_path`、`ImageOptimizer(convert_to_webp=...)`、`_convert_to_webp` 行为。
  - `index_manager.md`：`_process_image_pipeline` 返回 `(final_filename, text, embedding)`、降级行为、`resolve_unique_filename` 移至 utils。
  - `config.md`：`read_convert_to_webp()`。
  - 新增迁移脚本条目（`scripts/convert_memes_to_webp.py`）。
  - 目录结构补 `memes_migrated_backup/`。

## 9. 测试计划

### 9.1 `image_optimizer` 单元测试

- 各格式转 WebP：jpg/jpeg/png/bmp/静态 gif/动图 gif -> `.webp`，`output_path` 正确。
- 动图 gif 转 animated webp 后 `n_frames` / `duration` / `loop` 保留；透明 png 转 webp 后保留 alpha 通道。
- 强制转：构造转换后变大的图，仍生成 `.webp`（不保留原格式）。
- 转换失败（mock Pillow 抛异常）-> 抛 `RuntimeError`，原文件保留。
- 开关关闭：维持现有同格式压缩，`output_path=原路径`；`.bmp` PASS_THROUGH。
- `.webp` 源：开关开启时有损 q85 重编码（非 lossless），`output_path=原路径`，重编码后变大则保留原文件；开关关闭时维持 lossless 重编码。
- 同名冲突：目标 `.webp` 已存在 -> 追加 `_n` 序号。
- `.bmp` + 开关开启：转 webp（不再 PASS_THROUGH）；开关关闭仍 PASS_THROUGH。

### 9.2 `index_manager` 单元测试

- `_process_image_pipeline` 返回 `final_filename` 为 `.webp`，OCR/embed 用新路径。
- 转 WebP 后 sqlite `image_path` 为 `.webp`，原文件已删。
- 降级：mock `optimize` 抛异常 -> `final_filename=原 filename`，image_path=原扩展名，入库成功，有 warning。
- sync 阶段2：新增图转换后 `success` key 与 `metadata_store.add` 用 `.webp`。
- 去重替换：新图转 webp 后与旧图同名时不归档旧图。
- 降级孤儿清理：mock optimize 在 os.replace 后 unlink 前抛异常 -> `.webp` 孤儿被清理，原文件保留并入库。
- sync 并行同名：两张同 stem 不同扩展名新增图 -> final_filename 去重 rename，两张均入库。

### 9.3 迁移脚本测试

- `--dry-run`：仅打印不修改。
- 转换 + sqlite `image_path` 更新 + 原文件移到 backup-dir。
- 同名冲突改名。
- DB 无记录：仅转文件+备份。
- 失败回滚：mock 转换失败 -> 新 `.webp` 删除、原文件保留、sqlite 未更新。
- 可重入：二次运行跳过已转换；孤儿 `.webp` 无 sqlite 记录时清理重转。
- `--include-archives`：归档目录图转文件+备份，不更新 sqlite。
- 回滚完整性：update 失败时 sqlite 未变、原文件保留；移备份失败时 sqlite 指向新 `.webp`、原文件暂留。

## 10. 影响文件清单

| 文件 | 改动类型 |
|------|----------|
| `bot/engine/image_optimizer.py` | 改：`OptimizeResult.output_path`、`convert_to_webp` 参数、`_convert_to_webp`、docstring |
| `bot/engine/index_manager.py` | 改：`_process_image_pipeline` 返回值与降级、`add`/sync 阶段2 filename 流转；`resolve_unique_filename` 改为从 utils 导入 |
| `bot/engine/utils.py` | 改：新增 `resolve_unique_filename`（从 index_manager 迁入） |
| `bot/config.py` | 改：`read_convert_to_webp()` + `__all__` |
| `bot/bot.py` | 改：startup 注入 `convert_to_webp` |
| `scripts/convert_memes_to_webp.py` | 新增 |
| `.env.example` | 改：`CONVERT_TO_WEBP` |
| `docker-compose.yml` | 改：`CONVERT_TO_WEBP` 环境变量 |
| `README.md` | 改：功能/部署/依赖/结构 |
| `docs/PRD.md` | 改：3.4/3.6/4.3/5/7 |
| `CONTEXT.md` | 改：术语 |
| `docs/api/API.md` 及子文档 | 改：接口同步 + 迁移脚本条目 |
| `tests/unit/engine/` | 新增/改：image_optimizer、index_manager 测试 |

## 11. 风险与备注

- **animated WebP 发送兼容性**：GIF 动图转 animated WebP 后，经 NapCat OneBot v11 发送时是否保留动画/能否正常发送，需在实际环境验证。如发送异常，可回退为「GIF 跳过转换」（决策②可调整）。
- **`image_path` 变更时序**：转换+改名成功后才写 sqlite，保证索引不指向不存在的文件；降级时 image_path=原路径，文件仍在。
- **现有测试**：`OptimizeResult` 新增字段、`optimize` 行为分支可能影响现有 image_optimizer / index_manager 单元测试，需同步补改。
- **迁移脚本与运行中 Bot**：sqlite 写锁冲突，沿用 `png_to_jpg.py`「建议 Bot 未运行」提示。
- **`CONVERT_TO_WEBP` 热更新**：启动时读取注入 optimizer，运行中改 `.env` 不生效，需重启（与现有并发类变量一致）。
- **sync 并行转换同名竞态**：多张新增图同 stem 不同扩展名并行转换时，`_convert_to_webp` 的 `resolve_unique_filename` 存在 TOCTOU 竞态；通过 4.2「写入 success 前对 final_filename 去重 rename」缓解，极端情况下仍可能重复，由 sync 阶段0/1 自愈。
- **close 时正在转换**：`IndexManager.close` 取消 workers 时若 `_convert_to_webp` 中途取消，可能残留 `.webp` 孤儿与原文件；下次启动 sync 阶段2 会将孤儿当新图处理（可能重复入库），需关注或手动清理。
