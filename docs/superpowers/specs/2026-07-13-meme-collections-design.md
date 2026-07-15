# 表情包合集设计规格

> 日期：2026-07-13  
> 状态：待用户审阅

## 1. 目标

MemePilot 允许 `memes/` 直接存放图片，也允许用一级目录组织表情包合集。合集目录内可以继续使用子目录。

```text
memes/
├── global.webp
├── 新三国/
│   ├── a.webp
│   └── 截图/
│       └── b.webp
└── 甄嬛传/
    └── c.webp
```

系统为普通合集分配数字编号，为每张表情包分配公开复合 ID：

```text
<合集编号>.<合集内编号>
```

根目录图片归属“全局”，合集编号为 `0`。`/switch 0` 代表“全部合集”搜索范围，不只搜索根目录图片。

本功能包含：

- 合集发现和稳定存储；
- `/switch` 搜索范围切换；
- 所有检索入口的合集过滤；
- 公开复合 ID 和当前合集短号；
- `/add` 按当前合集保存；
- `/mv` 跨合集移动；
- `/info` 和搜索结果的合集展示；
- 旧数据库升级及根目录迁移脚本。

## 2. 范围外事项

本次不实现：

- 合集重命名命令；
- 合集删除命令；
- `/mv` 创建合集；
- 同路径文件内容覆盖检测；
- 文件系统手动移动的身份识别；
- Web 管理界面；
- 十万级图库专项优化；
- Bot 启动时自动升级旧数据库。

管理员通过新建或删除一级目录管理合集，再运行 `/refresh`。手动把已索引文件移到其他目录时，刷新按旧路径删除和新路径新增处理。需要保留 OCR、标签、说话人、内部 ID 和向量时，管理员应使用 `/mv`。

## 3. 术语

| 术语 | 含义 |
|---|---|
| 内部 ID | `meme.id`，稳定整数主键，与 Chroma 向量 ID 一一对应，不向用户公开 |
| 合集编号 | 普通合集的正整数编号；根目录使用保留编号 `0` |
| 合集内编号 | `local_id`，在一个合集内独立分配的正整数 |
| 公开 ID | `{collection_id}.{local_id}`，例如 `1.3`、`0.42` |
| 全局 | `memes/` 根目录图片的存储归属，编号为 `0` |
| 全部合集 | `/switch 0` 对应的聚合搜索范围，包含根目录和所有普通合集 |
| 当前合集 | 一个 `ChatScope` 持久化保存的 `/switch` 选择 |

## 4. 数据模型

### 4.1 `meme` 表

保留现有内部主键 `id`，新增合集字段：

```sql
CREATE TABLE meme (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL CHECK (collection_id >= 0),
    local_id      INTEGER NOT NULL CHECK (local_id > 0),
    image_path    TEXT NOT NULL,
    text          TEXT NOT NULL,
    speaker       TEXT
);

CREATE UNIQUE INDEX idx_meme_image_path
ON meme(image_path);

CREATE UNIQUE INDEX idx_meme_collection_local
ON meme(collection_id, local_id);

CREATE UNIQUE INDEX idx_meme_collection_text
ON meme(collection_id, text);
```

`meme_tag` 保持现有结构，通过内部 ID 关联 `meme.id`。

约束语义：

- 每个合集独立分配 `local_id`；
- 删除或移出表情包后，系统复用该合集的最小局部空号；
- 同一合集内 OCR 文本唯一；
- 不同合集允许相同 OCR 文本；
- `/switch 0` 搜索时返回跨合集的所有同文本结果。

### 4.2 `meme_collection` 表

```sql
CREATE TABLE meme_collection (
    id   INTEGER PRIMARY KEY CHECK (id > 0),
    name TEXT NOT NULL UNIQUE
);
```

规则：

- 表内只保存普通合集；
- 编号 `0` 不写入表内；
- 新合集使用当前最小可用正整数编号；
- 合集目录消失后删除对应记录，编号可供新合集复用；
- 系统不保留合集历史记录；
- 已登记合集变为空目录时，只要目录仍存在，记录继续保留。

### 4.3 `chat_collection_scope` 表

```sql
CREATE TABLE chat_collection_scope (
    user_id                 INTEGER NOT NULL,
    chat_type               TEXT NOT NULL,
    chat_id                 INTEGER NOT NULL,
    selected_collection_id  INTEGER NOT NULL CHECK (selected_collection_id >= 0),
    PRIMARY KEY (user_id, chat_type, chat_id)
);
```

`chat_type` 使用现有 `ChatScope` 值：`private` 或 `group`。

规则：

- 私聊、群 A、群 B 分别保存当前合集；
- 首次访问默认选择 `0`；
- `/switch` 成功后立即写入 SQLite；
- Bot 重启后保留选择；
- 删除合集时，同一事务把引用该编号的所有记录更新为 `0`；
- 编号复用前不允许留下指向旧合集的选择。

### 4.4 Schema 版本

```sql
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);
```

`schema_version` 始终只保存一行；创建、迁移和加载时都校验这一约束。

`MetadataStore.load()` 只接受当前版本：

- 数据库不存在时创建当前 Schema；
- 当前版本正常加载；
- 旧版或无法识别的 Schema 抛出 `SchemaVersionError`，Bot 拒绝启动；
- 错误消息提示管理员停止 Bot 并执行离线迁移；
- 运行时代码不执行 `ALTER TABLE` 或旧数据转换。

## 5. 内部 ID 与公开 ID

每张表情包同时拥有内部 ID 和公开 ID。

| 内部 ID | 合集编号 | 合集内编号 | 公开 ID |
|---:|---:|---:|---|
| 42 | 0 | 42 | `0.42` |
| 85 | 1 | 1 | `1.1` |
| 86 | 2 | 1 | `2.1` |

内部 ID 继续用于：

- SQLite 主键；
- `meme_tag` 外键；
- Chroma 向量 ID；
- IndexManager 和 Store 之间的内部调用。

公开 ID 用于：

- 命令参数；
- 搜索候选；
- 确认消息；
- `/info`；
- 用户可见日志和结果。

`/mv` 只改变 `collection_id`、`local_id`、`image_path` 和 Chroma 的合集元数据。它不改变内部 ID，也不重新生成 Embedding。

## 6. 公开 ID 解析

建议定义值对象：

```python
@dataclass(frozen=True, slots=True)
class MemePublicId:
    collection_id: int
    local_id: int

    def __str__(self) -> str:
        return f"{self.collection_id}.{self.local_id}"
```

### 6.1 完整 ID

格式：

```text
<collection_id>.<local_id>
```

规则：

- 只接受 ASCII 十进制数字；
- 允许前导零；
- `collection_id >= 0`；
- `local_id >= 1`；
- 只允许一个点；
- 拒绝正负号、指数形式、全角数字、空数字段和多段 ID；
- 命令参数解析器去除首尾空白；
- 回复统一输出规范化整数形式。

示例：

| 输入 | 结果 |
|---|---|
| `01.002` | `1.2` |
| `000.003` | `0.3` |
| `1.0` | 非法 |
| `+1.2` | 非法 |
| `1.2.3` | 非法 |
| `１.２` | 非法 |

### 6.2 当前合集短号

普通合集模式允许裸局部编号：

```text
002
```

系统将其解析为当前合集的局部编号 `2`。

合集 `0` 表示聚合搜索范围，裸编号存在歧义，因此一律拒绝：

```text
全部合集模式下请使用完整 ID，例如 1.3
```

完整 ID 可跨当前合集访问任意有效条目。批量命令允许混用完整 ID 与短号，解析后按内部 ID 去重并保持输入顺序。

## 7. 合集名称与参数解析

合集名称来自一级目录名。系统按完整名称精确匹配并区分大小写。

纯数字参数使用以下顺序：

1. 按合集编号解析；
2. 编号不存在时，按纯数字合集名称精确匹配。

编号 `0` 固定表示“全部合集”或根目录写入目标，不作为普通合集名称处理。

迁移脚本创建合集时校验名称：

- 去除首尾空白后不能为空；
- 不能为 `.` 或 `..`；
- 不能包含 `/`、反斜杠或 NUL；
- 不能以 `.` 开头；
- 允许 Unicode、空格和纯数字。

## 8. 组件边界

### 8.1 `MetadataStore`

`MetadataStore` 继续封装所有 SQLite 访问，并增加：

- 合集 CRUD；
- 最小可用合集编号分配；
- 每合集最小可用局部编号分配；
- 按公开 ID 查询内部条目；
- 按合集读取条目；
- ChatScope 当前合集读写；
- 删除合集与 ChatScope 回退事务；
- 合集维度缓存。

建议缓存：

```python
_entries: dict[int, MemeEntry]
_entries_by_collection: dict[int, dict[int, int]]
_collections: dict[int, MemeCollection]
_collection_name_to_id: dict[str, int]
```

其中 `_entries_by_collection[collection_id][local_id]` 保存内部 ID。现有 `_text_to_id` 改用 `(collection_id, text)` 作为键。所有写方法在同一锁内更新 SQLite 和缓存。

### 8.2 `CollectionManager`

新增合集领域组件，依赖 MetadataStore 的最小 Protocol。它负责：

- 编号或名称解析；
- ChatScope 当前合集查询和切换；
- 持久选择 `0` 到全库过滤条件的转换；
- 完整 ID 和短号解析；
- 合集列表、数量和当前标记；
- “全局”与“全部合集”的名称格式化。

`CollectionManager` 不扫描目录，也不移动文件。

### 8.3 `IndexManager`

`IndexManager` 增加：

- 结构化递归扫描；
- 合集发现和删除编排；
- 按合集执行所有搜索；
- 按当前合集添加图片；
- `/mv` 文件和跨存储写入；
- Chroma `collection_id` 元数据维护；
- 刷新阶段的合集一致性检查。

### 8.4 插件层

新增：

```text
bot/plugins/switch.py
bot/plugins/move.py
```

现有插件通过 `CollectionManager` 获取当前合集和解析公开 ID，不再直接对用户参数调用 `int()`。插件不向用户展示内部 ID。

### 8.5 跨层数据类型

`MemeEntry`、`SearchResult`、`AIMatchResult` 和写操作结果需要携带生成公开 ID 所需的 `collection_id`、`local_id` 与合集显示名称，或携带等价的不可变快照。插件不得在操作完成后重新查询已删除或已移动的旧状态。

删除结果等批量 DTO 同时返回用户可见 ID 快照。例如 `/del` 删除 SQLite 行后，插件仍能用删除前保存的 `MemePublicId` 输出成功列表。日志可同时记录内部 ID 和公开 ID，面向用户的文本只显示公开 ID。

## 9. 合集 0 的语义

内部搜索 API 使用：

```python
collection_filter: int | None
```

含义：

- `None`：搜索全部合集；
- 正整数：只搜索指定普通合集。

ChatScope 中：

- `selected_collection_id=0` 转换为 `collection_filter=None`；
- `selected_collection_id=N` 转换为 `collection_filter=N`。

写入时：

- `/add` 且当前选择 `0`：写入 `memes/`，归属 `collection_id=0`；
- `/add` 且当前选择普通合集：写入合集目录根部；
- `/mv ... 0`：移动到 `memes/` 根目录。

用户界面名称：

- 根目录图片归属：`全局`；
- `/switch 0` 搜索范围：`全部合集`。

## 10. 文件扫描

扫描器返回结构化快照：

```python
@dataclass(frozen=True, slots=True)
class MemeFileSnapshot:
    relative_path: str
    collection_name: str | None
```

示例：

```text
memes/a.webp
→ relative_path="a.webp"
→ collection_name=None

memes/新三国/截图/b.webp
→ relative_path="新三国/截图/b.webp"
→ collection_name="新三国"
```

规则：

- 根目录只接收直接存放的受支持图片；
- 每个非隐藏一级目录构成合集边界；
- 合集目录内递归扫描任意深度；
- 任一路径段以 `.` 开头时跳过整棵子树；
- 不跟随文件或目录符号链接；
- `image_path` 使用相对 `memes/` 的 POSIX 路径；
- 文件和新目录按名称升序处理，保证一次刷新内的顺序确定。

示例路径：

```text
meme_x.webp
新三国/meme_y.webp
新三国/截图/meme_z.webp
```

路径归属必须与 `collection_id` 一致。根目录路径使用 `0`，普通合集路径的首段必须对应合集名称。

## 11. `/refresh` 流程

刷新继续持有 IndexManager 独占写锁。

### 11.1 阶段 0：跨存储一致性

- 当前 SQLite Schema 不匹配时拒绝执行；
- 对齐 SQLite 和 Chroma 的内部 ID；
- 校验每条 Chroma 向量的 `collection_id`；
- 向量合集元数据不一致时，以 SQLite 为准修复；
- Chroma 为空且 SQLite 有数据时，沿用现有全量重建策略，并写入合集元数据。

这些操作修复当前索引，不承担旧 Schema 升级。

### 11.2 阶段 1：扫描

生成：

- 实际存在的非隐藏一级目录；
- 所有受支持图片及其相对路径；
- 每个一级目录是否含图片。

### 11.3 阶段 2：删除缺失图片

SQLite 中存在、文件快照中不存在的条目按现有顺序删除：先 SQLite，后 Chroma。

管理员手动跨目录移动文件时，旧路径在此阶段删除，目标路径在新增阶段处理。

### 11.4 阶段 3：删除消失合集

对于已登记但目录已经不存在的合集：

1. 确认其图片条目已清理；
2. 在一个 SQLite 事务中把引用该编号的 ChatScope 更新为 `0`；
3. 删除合集记录；
4. 释放合集编号。

事务失败时保留合集记录，不允许新合集复用该编号。

系统不向受影响 ChatScope 发送一次性通知。`/refresh` 摘要显示删除合集数和回退窗口数。

### 11.5 阶段 4：登记新合集

- 未登记目录必须递归包含至少一张受支持图片；
- 多个新合集按目录名升序处理；
- 每个新合集分配当前最小可用正整数；
- 已登记合集变空后，只要目录仍存在，记录继续保留；
- 目录改名等价于删除旧合集和登记新合集；
- 新合集可能复用旧合集刚释放的编号，但系统不把它视为原合集。

### 11.6 阶段 5：新增图片

每张新图根据相对路径确定合集，然后执行：

```text
优化/转换 → OCR → Embedding → 合集内去重 → SQLite → Chroma
```

规则：

- 根目录图使用 `collection_id=0`；
- 普通合集图使用对应编号；
- `local_id` 取该合集最小空号；
- 去重只检查同一合集；
- Chroma 向量写入 `collection_id` 元数据；
- 同合集重复新图沿用现有归档行为；
- 跨合集同文本图片分别入库。

### 11.7 刷新摘要

新增字段：

- 新增合集数；
- 删除合集数；
- 回退到 `0` 的 ChatScope 数量。

示例：

```text
索引刷新完成 ✅
新增合集：2
删除合集：1
回退窗口：3
新增图片：18
删除图片：4
去重归档：2
无文字移走：1
失败：1
```

单图处理失败不阻断其他图片。失败路径最多列出前 10 项。

## 12. 搜索过滤

当前合集过滤作用于：

- 普通文本；
- `/query`；
- `/rand`；
- `/sim`；
- `/ai`。

### 12.1 关键词、组合和随机搜索

MetadataStore 从缓存取得指定合集子集或全库快照。KeywordSearcher、CombinedSearcher 和 RandomSearcher 在该快照上工作。搜索过程不扫描文件系统。

### 12.2 语义和 AI 搜索

Chroma 每条记录增加：

```json
{"collection_id": 1}
```

VectorStore 查询接口增加可选过滤参数：

```python
async def query(
    query_embedding: list[float],
    n_results: int | None = 10,
    collection_id: int | None = None,
) -> list[VectorHit]:
    ...
```

普通合集使用：

```python
where={"collection_id": 1}
```

`/switch 0` 不传 `where`，召回全库。`/ai` 只把当前范围内的候选交给 LLM 精排。

## 13. `/switch`

命令：

```text
/switch [合集编号|名称]
```

权限和会话：

- 只允许授权用户；
- 私聊和群聊 @bot 均可用；
- 参与现有会话互斥；
- 当前窗口有活跃会话时要求先 `/cancel`。

### 13.1 无参数

列出合集、数量和当前项：

```text
表情包合集：
* 0. 全部合集（共 520 张）
  1. 新三国（120 张）
  2. 甄嬛传（86 张）

当前合集：全部合集
使用 /switch <编号|名称> 切换
```

`0` 显示全库总数。普通合集显示自身数量，已登记空合集显示 `0 张`。

### 13.2 带参数

```text
/switch 新三国
/switch 1
/switch 001
```

名称精确匹配并区分大小写。命令把 `/switch` 后的完整剩余文本作为名称，允许名称包含空格。纯数字按编号优先、名称兜底。

切换操作持 IndexManager 读锁完成“解析、重新校验、持久化”，避免与删除合集的刷新事务并发。等待读锁超时时回复“索引更新较慢，请稍后再试”。切换成功后先写 SQLite，再更新内存缓存。

回复：

```text
已切换到合集：新三国（1）
```

或：

```text
已切换到：全部合集（0）
```

不存在时：

```text
未找到表情包合集：xxx
发送 /switch 查看可用合集
```

## 14. `/add`

命令参数保持不变：

```text
/add [speaker <tags...>]
```

当前选择决定保存目标：

- `0`：保存到 `memes/`，分配 `0.x`；
- 普通合集：保存到对应一级目录根部，分配该合集最小局部空号。

`/add` 不写入合集内深层目录。

插件在下载前解析当前合集和保存目录。IndexManager 在最终写锁内重新校验目标合集；如果刷新期间删除了该合集，添加失败并清理已下载文件，不把图片写到已失效路径。

同合集文本重复时沿用替换行为：内部 ID、公开 ID 保持不变，旧图归档到 `memes_replaced/`。其他合集中的同文本条目不受影响。

成功回复使用公开 ID 和合集名称：

```text
新增表情包✅，id：1.3，识别到的文字为：
「丞相何故发笑」
1.3, 新三国, 曹操, 吐槽
```

## 15. `/mv`

命令：

```text
/mv <表情包ID> <目标合集编号|名称>
```

示例：

```text
/mv 1.3 2
/mv 3 甄嬛传
/mv 01.003 002
/mv 1.3 0
/mv 1.3 合集 名称
```

插件把源 ID 后的完整剩余文本作为目标参数，因此合集名称可以包含空格。

权限和会话：

- 只允许授权用户；
- 仅限私聊；
- 与 `/refresh`、`/add`、`/del` 等写操作共享互斥；
- 等待确认期间支持 `/cancel` 和 `/help`；
- 超时回复“移动已取消（超时）”；
- 非确认回复“已取消移动”。

### 15.1 参数规则

- 源完整 ID 可跨当前合集访问；
- 普通合集模式允许源短号；
- 合集 `0` 模式拒绝源短号；
- 目标接受编号或精确名称；
- 目标 `0` 表示根目录；
- 目标普通合集必须已经登记；
- `/mv` 不创建合集；
- 源和目标相同则拒绝。

### 15.2 确认

命令入口不发送图片，只发送文字：

```text
确认移动表情包：
源合集：新三国（1）
目标合集：甄嬛传（2）
当前编号：1.3
预计新编号：2.5

回复“确认”、yes 或 y 执行
```

系统不预留预计编号。用户确认后，IndexManager 在写锁内重新分配目标合集最小局部空号。编号变化时继续移动，成功回复实际编号。

### 15.3 文件落点

源文件：

```text
memes/新三国/截图/a.webp
```

移动到甄嬛传后：

```text
memes/甄嬛传/a.webp
```

规则：

- 文件放入目标合集根部；
- 不保留源合集内的相对子目录；
- 目标为 `0` 时放到 `memes/`；
- 同名文件使用 `_2`、`_3` 后缀；
- 保留 OCR、speaker、tags、内部 ID 和 Embedding；
- 目标合集存在相同 OCR 文本时拒绝，并提示冲突公开 ID。

### 15.4 写入和补偿

IndexManager 在写锁内：

1. 重新读取源条目和目标合集；
2. 检查目标合集文本冲突；
3. 分配实际局部编号；
4. 生成唯一目标文件名；
5. 移动文件；
6. 更新 SQLite 路径、合集和局部编号；
7. 更新 Chroma `collection_id`；
8. 返回实际公开 ID。

失败处理：

- 文件移动失败：SQLite 和 Chroma 不变；
- SQLite 失败：把文件移回源路径；
- Chroma 失败：恢复 SQLite 原字段、文件路径和 Chroma 原元数据；
- 补偿失败：记录高优先级日志，用户收到“移动失败，索引将在下次刷新时检查一致性”；
- 失败时不返回新编号。

成功回复：

```text
移动完成 ✅
原编号：1.3
新编号：2.5
目标合集：甄嬛传
```

## 16. 现有命令适配

以下命令使用统一公开 ID 解析器：

```text
/info [id]
/del <id>...
/edittext <id> <新文本>
/setspeaker <id> [说话人]
/addtag <id> <tag>...
```

规则：

- 完整 ID 可跨当前合集操作；
- 普通合集允许短号；
- 合集 `0` 禁止短号；
- `/del` 可混用完整 ID 和短号；
- 输入允许前导零；
- 确认和结果使用规范化公开 ID；
- 插件把公开 ID 转成内部 ID 后调用 IndexManager。

## 17. 搜索结果和元数据展示

列表前缀继续表示本次临时选择序号。元数据改用公开 ID 和合集名称：

```text
1. 丞相何故发笑 -- 1.3, 新三国, 曹操, 吐槽, 100%
2. 我从未见过如此厚颜无耻之人 -- 2.7, 旧三国, 诸葛亮, 100%
```

根目录条目：

```text
0.42, 全局, 无, 吐槽
```

图片路径继续用 `MEMES_DIR / image_path` 解析，支持嵌套相对路径。

## 18. `/info`

### 18.1 条目详情

```text
id：1.3
合集：新三国
文本：丞相何故发笑
文件名：新三国/截图/a.webp
大小：123.45 KiB
说话人：曹操
标签：吐槽
```

### 18.2 总体信息

总体信息增加：

- 全库表情包总数；
- 当前搜索范围和该范围数量；
- 有效普通合集数量；
- 当前搜索范围内的 speaker 排行。

当前为普通合集：

```text
表情包总数：520
当前合集：新三国（120 张）
普通合集数：4
当前合集说话人排行：
...
```

当前为 `0`：

```text
当前合集：全部合集（520 张）
```

此时 speaker 排行统计全库。

## 19. 帮助、权限和会话

帮助文本新增：

```text
/switch [合集编号|名称]：查看或切换表情包合集
/mv <id> <目标合集编号|名称>：移动表情包（需确认）
```

权限：

- `/switch`：授权用户私聊或群聊 @bot；
- `/mv`：授权用户私聊；
- 非授权用户保持静默。

`/switch` 参与会话互斥。`/mv` 使用与 `/del` 相同的确认超时机制，并允许 `/help`、`/cancel` 旁路。

## 20. Chroma 元数据

每条向量记录保存：

```json
{"collection_id": 1}
```

VectorStore 增加：

- 带 `where` 的查询；
- 不改变向量值的合集元数据更新；
- 全量重建时写入合集元数据；
- 一致性检查所需的 ID 和元数据读取接口。

日常操作：

- 新增图片时写入所属合集；
- `/mv` 只更新合集元数据；
- 删除时删除向量；
- `/refresh` 以 SQLite 为权威修复元数据漂移。

## 21. 离线迁移脚本

新增：

```text
scripts/migrate_meme_collections.py
```

脚本提供两个子命令。运行时输出：

```text
请确保 Bot 已停止运行；脚本不会自动检测 Bot 进程。
```

公共参数：

```text
--memes-dir PATH
--db-path PATH
--chroma-dir PATH
--dry-run
-v / --verbose
```

默认路径使用 `MEMES_DIR`、`INDEX_DB_PATH` 和 `CHROMA_DIR`。

### 21.1 `upgrade-schema`

```bash
uv run python -m scripts.migrate_meme_collections upgrade-schema
uv run python -m scripts.migrate_meme_collections upgrade-schema --dry-run
```

职责：

- 检查 SQLite 和 Chroma；
- 使用 SQLite Backup API 备份数据库；
- 在事务中创建新 Schema；
- 将旧记录映射为 `collection_id=0`、`local_id=原内部 ID`；
- 保留 image_path、text、speaker、tags 和内部 ID；
- 把全局文本唯一约束改为合集内联合唯一；
- 为现有 Chroma 向量补 `collection_id=0`；
- 不重新 OCR 或生成 Embedding；
- 完成后写入目标 Schema 版本。

补偿流程：

1. 备份 SQLite；
2. 启动 SQLite 事务，不提交版本；
3. 记录 Chroma 待修改 ID 和原元数据；
4. 更新 Chroma 元数据；
5. Chroma 失败时恢复已修改元数据并回滚 SQLite；
6. 全部成功后提交 SQLite 和版本。

`--dry-run` 只检查和输出计划，不生成备份，不修改 SQLite 或 Chroma。

重复执行：

- 已是目标版本且 Chroma 完整：报告无需迁移，返回 `0`；
- SQLite 已是目标版本但 Chroma 元数据缺失：补齐元数据；
- Schema 无法识别：拒绝修改并返回非零。

### 21.2 `move-root`

```bash
uv run python -m scripts.migrate_meme_collections move-root 新三国
uv run python -m scripts.migrate_meme_collections move-root "合集 名称"
uv run python -m scripts.migrate_meme_collections move-root 2
uv run python -m scripts.migrate_meme_collections move-root 新三国 --dry-run
```

职责：

- 要求 SQLite 已完成 `upgrade-schema`；
- 只处理 `memes/` 根目录直接存放、SQLite 中已有记录的受支持图片；
- 未索引图片保留在根目录并列入跳过报告；
- 不处理子目录或非图片文件；
- 目标接受编号或名称；
- CLI 把目标位置参数作为一个字符串；含空格名称需使用 shell 引号；
- 名称不存在时创建目录和合集；
- 编号不存在时报错；
- 新合集分配最小可用合集编号；
- 每张图分配目标合集最小可用局部编号；
- 更新路径、合集、局部编号和 Chroma 合集元数据；
- 不重新 OCR 或生成 Embedding；
- 文件重名时追加后缀；
- 目标合集已有相同 OCR 文本时，保留源 `0.x` 条目并跳过，报告目标冲突公开 ID；
- 文本冲突计入跳过，不计失败；
- 对可捕获的文件、SQLite 或 Chroma 异常执行单文件补偿，恢复源文件和原记录后继续；
- 不提供强制终止、进程崩溃或断电恢复日志；这类中断依靠执行前 SQLite 备份和人工检查恢复；
- 任一失败时最终返回 `1`；
- 全部成功、全部跳过或无事可做时返回 `0`。

根目录没有可迁移的已索引图片且目标不存在时，脚本不创建目录或合集。脚本本次创建目标合集后，如果成功迁移数量为 `0`，脚本删除本次创建的合集记录；仅当目标目录仍为空且也由本次脚本创建时删除该目录，并释放合集编号。

预演示例：

```text
目标合集：新三国（预计编号 1）
待迁移已索引图片：125
跳过未索引图片：3
文件名冲突：3
不会修改文件、SQLite 或 Chroma
```

## 22. 错误类型与用户提示

建议新增：

```python
class SchemaVersionError(RuntimeError): ...
class CollectionNotFoundError(ValueError): ...
class InvalidCollectionNameError(ValueError): ...
class InvalidPublicIdError(ValueError): ...
class MemeNotFoundError(ValueError): ...
class DuplicateMemeInCollectionError(RuntimeError): ...
class MemeMoveError(RuntimeError): ...
```

典型提示：

| 场景 | 提示 |
|---|---|
| ID 格式错误 | `表情包 ID 格式错误，请使用“合集编号.局部编号”，例如 1.3` |
| 合集 0 使用短号 | `全部合集模式下请使用完整 ID，例如 1.3` |
| 条目不存在 | `未找到 ID 为 1.3 的表情包` |
| 合集不存在 | `未找到表情包合集：xxx` |
| `/mv` 同目标 | `表情包已属于目标合集` |
| `/mv` 文本冲突 | `目标合集已存在相同内容的表情包：2.4` |
| `/mv` 锁冲突 | `索引正在刷新，请稍后再试` |
| `/mv` 补偿失败 | `移动失败，索引将在下次刷新时检查一致性` |

底层异常写入日志。插件不向用户发送数据库路径、栈信息或原始服务错误。

## 23. 性能要求

- 数千张图片时关键词搜索保持 `<1 秒`；
- 合集过滤从内存缓存取条目；
- `/switch` 不扫描文件系统；
- `/sim` 和 `/ai` 使用 Chroma `where`；
- `/mv` 不调用 OCR 或 Embedding；
- 迁移脚本面向数千张根目录图片，顺序执行即可。

## 24. 依赖和开发工具

实现不需要新增运行时依赖：

- `pathlib`、`os.scandir`：路径和扫描；
- `shutil`：文件移动；
- `sqlite3`：事务和备份；
- `argparse`：CLI；
- 现有 `chromadb`：向量元数据。

验证命令：

```bash
uv run pytest
uv run ty check
uv run ruff check .
uv run ruff format --check .
```

项目继续使用 `ty` 做 Python 类型检查和 LSP，使用 `ruff` 做检查与格式化。

## 25. 测试策略

### 25.1 MetadataStore

覆盖：

- 新 Schema 和版本创建；
- 旧 Schema 拒绝加载；
- 每合集局部编号分配和最小空洞复用；
- 跨合集同文本；
- 同合集文本冲突；
- 公开 ID 到内部 ID 查询；
- 合集编号最小空洞复用；
- 删除合集与 ChatScope 回退事务；
- SQLite 与缓存一致。

### 25.2 CollectionManager

覆盖：

- 完整 ID、短号和前导零；
- 非法 ID；
- 合集 `0` 禁止短号；
- 完整 ID 跨合集；
- 批量混合解析和去重；
- 编号优先、名称兜底；
- ChatScope 持久化；
- 合集列表、数量和当前标记。

### 25.3 扫描与刷新

覆盖：

- 根目录图片；
- 多个一级合集；
- 任意深度子目录；
- 各层隐藏目录；
- 文件和目录符号链接；
- 空目录不登记；
- 已登记合集变空后保留；
- 目录删除、ChatScope 回退和编号复用；
- 手动跨目录移动按删除加新增处理；
- 合集内去重和跨合集重复；
- POSIX 相对路径。

### 25.4 搜索

覆盖普通文本、`/query`、`/rand`、`/sim`、`/ai` 的当前合集过滤，合集 `0` 全库搜索，Chroma `where` 参数，以及跨合集同文本全部返回。

### 25.5 插件

覆盖：

- `/switch` 私聊、群聊和会话互斥；
- `/add` 当前合集目标；
- `/mv` 私聊限制、确认、取消和超时；
- 预计编号变化后返回实际编号；
- 目标同文本冲突；
- 现有命令的完整 ID、短号和前导零；
- 搜索结果及 `/info` 的公开 ID 和合集名称。

### 25.6 迁移脚本

覆盖：

- `upgrade-schema --dry-run` 无副作用；
- SQLite 备份；
- 旧 ID 转为 `0.N`；
- tags、speaker、路径保留；
- Chroma 元数据补齐；
- Chroma 失败时 SQLite 回滚；
- 幂等重跑；
- `move-root` 名称和编号目标；
- 名称自动创建和校验；
- 文件重名；
- 单文件失败回滚并继续；
- 未索引图片跳过；
- 目标同文本条目跳过并报告冲突 ID；
- 空迁移不创建合集；
- 新建目标但零成功时清理合集和空目录；
- `--dry-run` 无副作用。

## 26. 文档更新

实现同步更新：

- `docs/PRD.md`；
- `CONTEXT.md`；
- `README.md`；
- `docs/api/API.md`；
- 所有受影响模块的 API 文档；
- `/help` 文本；
- 项目结构和迁移命令说明。

本功能不新增环境变量、挂载目录或依赖服务，因此不修改 `.env.example` 和 `docker-compose.yml`。
