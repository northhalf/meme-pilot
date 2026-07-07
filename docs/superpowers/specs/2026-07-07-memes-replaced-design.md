# 被替换文件归档到 memes_replaced/ 设计文档

> 日期：2026-07-07
> 状态：设计已确认，待实现

## 1. 背景与目标

当前 `/add` 去重替换旧图时会直接删除旧图文件，`/refresh` 去重删除重复新图时也会直接删除文件。用户希望把这些"被替换的文件"保留下来，移动到独立的 `memes_replaced/` 目录，以便必要时手动恢复或查看历史。

## 2. 需求确认

| 问题 | 确认结果 |
|------|----------|
| 目标目录 | 新建 `memes_replaced/`（`memes/` 同级） |
| 覆盖场景 | `/add` 去重替换旧图 + `/refresh` 去重删除的重复新图 |
| 文件名处理 | 保持原文件名，冲突时追加 `_2`、`_3` 等序号 |
| 用户提示 | 不额外显示归档路径 |
| 配置方式 | 硬编码，不新增环境变量 |

## 3. 设计细节

### 3.1 新增目录常量

`bot/config.py` 增加：

```python
MEMES_REPLACED_DIR: Path = PROJECT_ROOT / "memes_replaced"
"""被替换表情包的归档目录。"""
```

并加入 `__all__`。

### 3.2 IndexManager 扩展

`bot/engine/index_manager.py`：

- `__init__` 新增参数 `replaced_dir: str | None = None`，默认取 `Path(memes_dir).parent / "memes_replaced"`。
- 新增 `self._replaced_dir: Path`。
- 新增同步方法 `_move_to_replaced(filename: str) -> str`：
  - `src = self._memes_dir / filename`
  - `self._replaced_dir.mkdir(parents=True, exist_ok=True)`
  - `dst = resolve_unique_filename(self._replaced_dir, filename)`
  - `shutil.move(str(src), str(dst))`
  - 记录 info 日志并返回移入后的完整路径。

### 3.3 `/add` 替换旧图流程

在 `_write_entry` 的去重替换分支中：

1. sqlite update 指向新图。
2. chroma upsert 成功后，将旧图移动到 `memes_replaced/`（替代原来的 `unlink`）。
3. `AddResult` 新增 `archived_path: str | None = None`，`reason="replaced"` 时填充为移入后的完整路径，便于测试和日志。

### 3.4 `/refresh` 去重流程

在 `_sync_phase2_add` 的去重分支中：

- 将 `(self._memes_dir / filename).unlink(...)` 替换为 `await self._run_sync(self._move_to_replaced, filename)`。
- 更新日志文案为"新图与已有索引去重，已归档新图"。
- `SyncResult` 字段保持不变；`deduped` 计数逻辑不变。

### 3.5 `/del` 行为

`/del` 仍使用 `memes_deleted/`，保持独立，不受本次改动影响。

## 4. 用户交互

- `/add` 替换旧图时，插件层仍回复"替换旧图✅，id：…"，不显示归档路径。
- `/refresh` 完成后的摘要统计不变，不额外提示归档目录。

## 5. 部署与文档同步

- `docker-compose.yml`：为 `bot` 服务新增卷挂载 `./memes_replaced:/app/memes_replaced`。
- `README.md`：在项目结构图中增加 `memes_replaced/` 说明。
- `CONTEXT.md`：在术语表中增加"替换归档目录"条目。
- `docs/api/API.md`：更新 `AddResult` 字段说明与 `IndexManager.__init__` 签名。

## 6. 测试计划

- 更新 `tests/unit/engine/test_index_manager.py`：
  - `/add` 去重替换场景：断言旧图出现在 `memes_replaced/`，而非被删除。
  - `/refresh` 去重场景：断言重复新图被归档到 `memes_replaced/`。
- 新增 `_move_to_replaced` 冲突重命名测试（同名文件多次替换时生成 `xxx_2.jpg`、`xxx_3.jpg`）。

## 7. 待实现清单

- [ ] `bot/config.py` 新增 `MEMES_REPLACED_DIR`
- [ ] `bot/engine/index_manager.py` 新增 `replaced_dir` 参数、`_move_to_replaced`、`AddResult.archived_path`，并修改 `/add` 与 `/refresh` 的去重逻辑
- [ ] `docker-compose.yml` 新增 `memes_replaced` 卷
- [ ] `README.md`、`CONTEXT.md`、`docs/api/API.md` 同步更新
- [ ] 更新并补充单元测试
