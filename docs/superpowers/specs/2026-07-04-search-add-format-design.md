# 设计文档 — 搜索回复与 /add 命令格式调整

> 日期：2026-07-04
> 主题：search-add-format
> 状态：已评审，待实现

## 1. 背景与目标

当前 `/search` 返回多结果时只显示序号和 OCR 文本，发送图片时不带索引元数据。`/add` 命令使用可选的「目标命名」作为文件名基名，且成功回复不包含索引 id。

本设计调整：

1. 搜索结果列表和发送图片时附带 `id`、`speaker`、`tags` 元数据。
2. `/add` 命令改为接收可选的 `speaker` 和 `tags`，文件名完全由 Bot 自动生成。
3. `/add` 成功回复中追加索引 `id`。

## 2. 搜索结果回复格式

### 2.1 多结果列表

单条文字消息：

```text
找到多个匹配的表情包，请选择：
1. 当你的老板说今天要加班 -- 3, 无, 吐槽, 加班
2. 加班到凌晨三点的我 -- 7, 小明
3. 周日晚上的加班通知 -- 12, 无
回复编号即可 (1-3)
```

格式规则：

- 每行：`{序号}. {OCR文本} -- {id}, {speaker或"无"}, {tags...}`
- `speaker` 缺失或为空时，固定显示 `无`。
- `tags` 为空时，省略整个 tags 段（包括其前的一个逗号），例如 `7, 小明`。

### 2.2 单结果 / 用户选择后发送

分两条消息发送：

1. 第一条：表情包图片。
2. 第二条：元数据行，例如 `7, 小明`。

### 2.3 /ai 命中后发送

同样分两条消息：

1. 第一条：表情包图片。
2. 第二条：元数据行，例如 `7, 小明`。

## 3. `/add` 命令新语法

### 3.1 语法

```text
/add [speaker <tags...>]
```

- 文件名完全由 Bot 自动生成（`meme_<YYYYMMDDHHMMSS>_<hash8>`），不再使用用户输入作为文件名基名。
- 参数按空白切分，**第一个词作为 speaker**，剩余词作为 tags。
- 无参数时，`speaker=None`，`tags=[]`。

示例：

| 用户输入 | speaker | tags |
|---|---|---|
| `/add` | `None` | `[]` |
| `/add 小明` | `"小明"` | `[]` |
| `/add 小明 吐槽 加班` | `"小明"` | `["吐槽", "加班"]` |
| `/add 吐槽 加班` | `"吐槽"` | `["加班"]` |

### 3.2 成功回复

在现有回复基础上追加 `id：{id}` 字段：

```text
新增表情包✅，id：42，识别到的文字为：
「加班心累」
```

替换旧图时同样追加 id：

```text
替换旧图✅，id：42，识别到的文字为：
「加班心累」
```

OCR 无文字时保持现有提示（不进索引，无 id 可展示）：

```text
未识别到文字，已移至 meme_no_text/
```

## 4. 数据模型与管道调整

### 4.1 搜索结果对象

`bot/engine/keyword_searcher.py` 中的 `SearchResult` 增加字段：

```python
@dataclass
class SearchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

`KeywordSearcher` 从 `MemeEntry` 中直接带出 `speaker` 和 `tags`。

### 4.2 AI 匹配结果对象

`bot/engine/ai_matcher.py` 中的 `AIMatchCandidate` 与 `AIMatchResult` 增加字段：

```python
@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class AIMatchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
```

`_build_candidates()` 从 `MemeEntry` 中直接带出 `speaker` 和 `tags`。精排 prompt 保持不变，仍只使用 `text`。

### 4.3 /add 写入管道

`bot/engine/index_manager.py` 调整：

- `AddResult` 增加 `speaker: str | None` 和 `tags: list[str]`（仅用于测试/日志确认，不影响核心逻辑）。
- `IndexManager.add(filename, speaker=None, tags=None)` 透传 speaker/tags。
- `_WriteRequest` 增加 `tags` 字段；`speaker` 字段已存在（用于 `SET_SPEAKER`），`ADD` 复用该字段存储新增条目的 speaker。
- `_write_entry()` 调用 `MetadataStore.add(image_path, text, speaker, tags)`。
- 去重替换分支中，调用 `MetadataStore.update(old_id, image_path=filename, speaker=speaker, tags=tags)`，确保新 speaker/tags 覆盖旧值。

`MetadataStore.add()` 与 `MetadataStore.update()` 已支持 `speaker` 和 `tags`，无需修改。

## 5. 共享格式化函数

在 `bot/plugins/_search_utils.py` 中新增：

```python
def format_metadata_line(entry_id: int, speaker: str | None, tags: list[str]) -> str:
    """格式化表情包的元数据行。

    输出格式：id, speaker, tag1, tag2, ...
    speaker 缺失时显示为"无"；tags 为空时省略 tags 段。

    Args:
        entry_id: 索引 id。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。

    Returns:
        格式化后的元数据行字符串。
    """
    parts = [str(entry_id), speaker if speaker else "无"]
    parts.extend(tags)
    return ", ".join(parts)
```

## 6. 插件层改动

### 6.1 `_search_utils.py`

- 多结果列表：使用 `format_metadata_line(r.entry_id, r.speaker, r.tags)` 拼接每一行。
- 单结果命中：先 `await matcher.send(image)`，再 `await matcher.finish(metadata_line)`。
- 选择后发送：先发送图片，再 `finish` 元数据行。

### 6.2 `meme_ai.py`

命中结果发送时，先发送图片，再发送元数据行。

### 6.3 `meme_add.py`

- 解析命令参数：
  - 移除 `/add` 前缀后 strip。
  - 按空白切分：`parts = raw_text.split()`。
  - `speaker = parts[0] if parts else None`
  - `tags = parts[1:] if len(parts) > 1 else []`
- 将 `speaker` 和 `tags` 存入 matcher state，在 `got_image` 中取出并传给 `index_manager.add(filename, speaker=speaker, tags=tags)`。
- 文件名处理简化：移除目标命名逻辑，保存图片时直接使用 `_auto_filename(image_data) + ext`，例如 `meme_20260704120000_a1b2c3d4.png`。
- 成功回复中追加 `id：{result.entry_id}`。

## 7. 边界情况

| 场景 | 行为 |
|---|---|
| `/add` 无参数 | speaker=None，tags=[]，文件名自动生成。 |
| `/add` 只有 speaker 无 tags | tags=[]，元数据行只显示 `id, speaker`。 |
| `/add` 第一个词带特殊字符 | 仅作为 speaker 字符串存储，不影响文件名。 |
| 搜索结果 tags 为空 | 省略 tags 段，例如 `7, 小明`。 |
| 搜索结果 speaker 为空 | 显示 `无`，例如 `7, 无, 吐槽`。 |
| `/add` 触发去重替换 | 保留旧 id，新 speaker/tags 覆盖旧值。 |
| `/add` OCR 无文字 | 移图，回复保持原样，不追加 id。 |

## 8. 文档更新

命令交互格式变更，需要同步更新以下文档：

- `bot/plugins/_help_text.py`：将 `/add` 帮助文本更新为 `/add [speaker <tags...>]`。
- `README.md`：`/add` 命令说明、示例和参数解释需同步更新。
- `docs/PRD.md`：3.3 节 `/add` 功能需求、3.4 节帮助命令示例、边界情况表格中涉及 `/add` 目标命名的描述需同步更新。
- `docs/api/API.md` 及 `docs/api/bot/engine/*.md`：更新 `SearchResult`、`AIMatchCandidate`、`AIMatchResult`、`AddResult`、`IndexManager.add()` 的签名说明。

## 9. 测试影响

需要同步更新以下测试：

- `SearchResult` 构造的地方：增加 `speaker`/`tags` 字段或使用默认值。
- `AIMatchCandidate` / `AIMatchResult` 构造的地方：同上。
- `AddResult` 构造的地方：同上。
- `_search_utils.py` 的格式化断言：验证多结果列表包含元数据、单结果/选择后分两条消息发送。
- `meme_ai.py` 的断言：验证分两条消息发送。
- `meme_add.py` 的解析断言：验证 speaker/tags 拆分、文件名不再使用目标命名。
- 帮助文本测试：验证 `/add` 帮助文本已更新。
- `KeywordSearcher` 和 `AIMatcher` 单元测试：验证 speaker/tags 正确带出。

## 10. 不提交说明

根据项目 `CLAUDE.md` 规定，**禁止在 main 分支自行执行 `git add` / `git commit`**。因此本设计文档写入工作树后暂不提交，待用户审阅并确认进入实现阶段后，由用户决定提交方式。
