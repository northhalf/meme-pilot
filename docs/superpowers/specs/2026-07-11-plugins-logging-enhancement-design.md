# Plugins 日志输出增强与日志文件大小调整设计文档

## 1. 背景与目标

当前项目通过 `bot/logging_config.py` 配置顶层 `bot` logger，所有 `bot.plugins.*`、`bot.engine.*` 子 logger 通过继承关系复用同一套 handler。

用户提出两项明确需求：

1. **增加 plugins 下的日志输出**：在现有 plugins 命令关键流程中补充更多 `debug`/`info` 级别的运行日志。
2. **将日志文件大小调整为 10MB**：将 `RotatingFileHandler.maxBytes` 从 1MB 提升到 10MB，并同步将 `backupCount` 从 1 提升到 3。

## 2. 设计原则

- **最小改动**：不引入新依赖、不改动 logger 继承架构、不修改日志格式。
- **与现有 request_id 全链路追踪一致**：所有新增日志均在 `set_request_id()` 上下文中，自动带 `[req:xxx]` 前缀。
- **级别分明**：
  - `debug`：输入参数、中间状态、分支判断。
  - `info`：业务里程碑、完成结果、用户取消/超时摘要。
- **不增加日志文件数量上限之外的磁盘负担**：通过 10MB + 3 个备份的配置，总上限约 40MB。

## 3. 配置变更

### 3.1 文件位置

`bot/logging_config.py`

### 3.2 变更内容

```python
file_handler = RotatingFileHandler(
    _log_dir / "bot.log",
    maxBytes=10_485_760,  # 10 MB
    backupCount=3,
    encoding="utf-8",
)
```

| 配置项 | 当前值 | 新值 | 说明 |
|--------|--------|------|------|
| `maxBytes` | `1_048_576` | `10_485_760` | 单个日志文件 10MB |
| `backupCount` | `1` | `3` | 保留 3 个备份 |

formatter、handler 类型、日志级别、logger 继承关系保持不变。

## 4. Plugins 日志增强点

### 4.1 写操作类命令

#### `/add` (`bot/plugins/meme_add.py`)

- `debug`：解析到的 `speaker`、`tags`。
- `debug`：收到的图片 URL、解析后的扩展名、目标文件名。
- `info`：图片保存成功。
- `info`：`index_manager.add()` 返回结果，包括 `entry_id`、`reason`（`added`/`replaced`/`no_text`）。
- `info`：用户取消或超时的统一摘要。

#### `/del` (`bot/plugins/meme_delete.py`)

- `debug`：解析到的 `entry_ids`、`not_found_ids`。
- `info`：用户确认删除后，`delete()` 返回的 `deleted_ids` / `not_found_ids` / `failed_ids` 摘要。
- `info`：用户取消删除。

#### `/edittext` (`bot/plugins/meme_edit.py`)

- `debug`：目标 `entry_id`、新文本。
- `info`：编辑成功 / 用户取消 / 超时 / 未找到。

#### `/addtag` (`bot/plugins/meme_addtag.py`)

- `debug`：目标 `entry_id`、待添加 `tags`。
- `info`：标签追加成功 / 用户取消 / 超时 / 未找到。

#### `/setspeaker` (`bot/plugins/meme_setspeaker.py`)

- `debug`：目标 `entry_id`、新 `speaker`。
- `info`：设置成功 / 用户取消 / 超时 / 未找到。

#### `/refresh` (`bot/plugins/meme_refresh.py`)

- `info`：刷新完成及统计（新增/删除/替换数量，若 `SyncResult` 已提供）。
- `debug`：刷新拒绝原因（群聊、已有任务运行）。

### 4.2 搜索/查询类命令

#### `/search` (`bot/plugins/meme_search.py`)

- `debug`：解析到的关键词。
- `info`：搜索结果数量（0 / 1 / 多）及最终处理方式（直接发图 / 列表 / 无结果）。

#### `/query` (`bot/plugins/meme_query.py`)

- `debug`：解析到的 `keyword`、`speakers`、`tags`。
- `info`：组合检索结果数量及最终处理方式。

#### `/rand` (`bot/plugins/meme_rand.py`)

- `debug`：是否含关键词、随机结果数量。
- `info`：最终发送结果摘要。

#### `/sim` (`bot/plugins/meme_sim.py`)

- `debug`：语义描述文本。
- `info`：召回结果数量及最终处理方式。

#### `/ai` (`bot/plugins/meme_ai.py`)

- `debug`：用户描述文本。
- `info`：embedding 召回数、精排结果、最终命中 `entry_id`。

### 4.3 其他命令

#### `/info` (`bot/plugins/meme_info.py`)

- `debug`：返回的条目数、speaker 排行、资源占用。

#### `/cancel` (`bot/plugins/meme_cancel.py`)

- `info`：取消的会话类型与结果。

#### 普通文本 / 兜底 (`bot/plugins/meme_plain_text.py`)

- `debug`：命中帮助旁路、未知命令、兜底搜索的判断依据。

## 5. 日志级别与格式约定

- 继续使用 `logger = logging.getLogger(__name__)`，不新增 logger。
- `debug` 用于诊断信息；`info` 用于业务里程碑。
- 不修改现有 formatter：
  ```
  %(asctime)s - %(name)s - %(levelname)s - %(message)s
  ```
- 避免在循环内输出大对象，避免日志膨胀。

## 6. 影响范围

- `bot/logging_config.py`：2 处常量修改。
- `bot/plugins/*.py`：大部分文件新增若干日志语句，不改动业务逻辑和返回结果。
- 用户可见行为：无变更。
- 磁盘占用：日志总上限从约 2MB 提升到约 40MB。

## 7. 验证方式

1. 启动 Bot，确认 `log/bot.log` 正常写入。
2. 触发各命令，检查新增 `debug`/`info` 日志是否出现，并带有 `[req:xxx]` 前缀。
3. 通过脚本或长时间运行生成超过 10MB 日志，验证是否生成 `bot.log.1`、`bot.log.2`、`bot.log.3`。
4. 运行现有测试，确保 `bot/logging_config.py` 相关测试（如存在）不受影响。

## 8. 风险与应对

| 风险 | 应对 |
|------|------|
| 日志量增加导致磁盘占用上升 | 已通过 10MB + 3 备份限制总上限 |
| 新增日志影响性能 | 仅新增少量日志点，避免循环内高频输出 |
| 与现有 engine 日志增强冲突 | 本次只修改 plugins 与 logging_config，不动 engine 模块 |

## 9. 不引入新依赖

仅使用 Python 标准库 `logging`，不引入 `loguru` 等第三方日志库。
