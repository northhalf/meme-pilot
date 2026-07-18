# 产品需求文档 (PRD) — MemePilot

> 版本：v1.0
> 日期：2026-06-11
> 状态：v1.0 已实现

---

## 1. 产品概述

### 1.1 产品定位

MemePilot 是一个部署在 Docker 中的 QQ 私聊表情包机器人，帮助用户从本地表情包库中快速找到目标表情包。

### 1.2 核心价值

- 告别在文件夹中手动翻找表情包
- 通过关键词或自然语言快速定位
- 表情包图片始终本地存储；OCR 文本会按 `OCR_PROVIDER` 配置发送给对应服务，Embedding 由 `EMBEDDING_PROVIDER` 配置的服务生成
- sqlite3 元数据 + ChromaDB 向量索引，轻量可维护

### 1.3 目标用户

- 一个或多个授权用户。私聊支持所有命令；群聊中 @bot 支持 `/query`、`/rand`、`/sim`、`/info`、`/help`、`/switch` 和普通文本搜索，`/collection create`、`/collection delete`、`/collection rename`、`/add`、`/addtag`、`/del`、`/refresh`、`/edittext`、`/setspeaker`、`/move` 仅限私聊。

---

## 2. 系统架构

### 2.1 整体架构

```
┌──────────────────────────────────────────────────┐
│                 Docker Compose                     │
│                                                    │
│  ┌──────────────────┐     WebSocket               │
│  │  napcat          │────────────────────►        │
│  │  NapCatQQ 容器   │  反向 WebSocket / OneBot v11 │
│  └──────────────────┘                             │
│                                                    │
│  ┌───────────────────────────────────────────┐    │
│  │  bot (Python 3.12)                        │    │
│  │  ┌────────────┐  ┌────────────────────┐   │    │
│  │  │NoneBot2    │  │meme_engine         │   │    │
│  │  │ 框架+插件  │──│  搜索引擎          │   │    │
│  │  └────────────┘  └────────────────────┘   │    │
│  └───────────────────────────────────────────┘    │
│                                                    │
│  持久化卷:                                         │
│  ./memes/  → 表情包原文件；根目录可直接存放图片（归属“全局”，公开 ID 为 0.x），一级目录作为表情包合集，目录内可继续使用子目录   │
│  ./data/   → index.db（sqlite，含 meme、meme_tag、meme_collection、chat_collection_scope、schema_version）+ chroma/（向量库，向量记录含 collection_id 元数据）   │
│  ./napcat/ → NapCat 配置                           │
└──────────────────────────────────────────────────┘
```

### 2.2 技术栈

| 层 | 技术 | 版本/说明 |
|----|------|-----------|
| QQ 协议端 | NapCatQQ | 最新 Docker 镜像，OneBot v11 |
| Bot 框架 | NoneBot2 | Python，异步 |
| Bot 适配器 | nonebot-adapter-onebot | 反向 WebSocket 连接，NapCat 主动连接 Bot |
| 元数据存储 | sqlite3 | Python 标准库，存 `meme`（id/collection_id/local_id/image_path/text/speaker）、`meme_tag`、`meme_collection`、`chat_collection_scope` 和 `schema_version`；`collection_id=0` 表示根目录“全局”，普通合集使用正整数编号；ChatScope 的当前合集持久化保存 |
| 向量索引 | ChromaDB | PersistentClient，HNSW cosine collection（`memes`）；每条向量记录额外保存 `collection_id` 元数据，用于按合集过滤召回 |
| OCR 引擎 | RapidOCR（本地 ONNX，默认）/ PaddleOCR 云 API / OpenAI 兼容视觉 OCR / 百度智能云 OCR REST API | 视觉 OCR，返回按空白分割后以英文逗号拼接的文本；`OCR_PROVIDER=baidu` 支持 PP-OCRv6 及六种传统通用接口，默认 `BAIDU_OCR_TYPE=pp_ocrv6` |
| 模糊搜索 | pylcs | C++ 最长公共子序列算法库 |
| 图片压缩/转换 | Pillow | 支持 .jpg/.jpeg/.png/.webp/.gif；.bmp 跳过压缩；`CONVERT_TO_WEBP=true`（默认）时新增图转有损 WebP（q85），失败降级保留原格式 |
| Embedding | OpenAI 兼容 Embedding（默认）/ Google Embedding API | 为语义搜索与新增图片生成向量 |
| 依赖解耦 | `typing.Protocol` | engine 模块按消费者最小接口定义协议（如 `protocols.py.EmbeddingProvider`、`index_manager.OcrProvider`、`MetadataStoreProvider`、`MetadataStoreProtocol`、`VectorStoreProtocol`、`ImageOptimizerProtocol`），单模块使用的协议放模块内，多模块共用的协议放 `protocols.py`，便于测试用 mock 替换 |
| 容器编排 | Docker Compose | 2 容器 |

---

## 3. 功能需求

### 3.1 功能一：关键词搜索

> **注意：** 显式的 `/search` 斜杠命令（含短命令 `/s`）已删除。关键词搜索能力仍通过「普通文本兜底搜索」和 `/query` 命令提供，以下流程描述供历史参考。

#### 触发方式

授权用户私聊发送不以 `/` 开头的普通文本时，Bot 将其作为关键词搜索；或在私聊/群聊 @bot 中发送 `/query <关键词> ...`。

#### 流程

```
用户: 加班
        │
        ▼
Bot 接收 → 读取当前 ChatScope 的 CollectionSelection
        │
        ▼
调用 IndexManager.search(“加班”, collection_id=selection.search_filter)
        │
        ├── 在当前合集范围内使用「原始输入去所有空白、保留助词」的关键词做精确子串匹配
        │    ├── 命中 → 返回包含该子串的全部表情包（多结果每页 10 条分页）
        │    └── 未命中 → 回退到 jieba 去助词 + pylcs LCS 模糊匹配（>= 60）
        │
        ├── （回退路径）使用 jieba.posseg 对关键词做分词 + 词性标注，过滤助词（的、了、吗、呢、吧等）
        │    └── 去助词后为空 → “没有匹配到任何表情包 🙁”
        │    └── 去助词后用 pylcs LCS 对 sqlite 中的 OCR 文本做模糊匹配
        │    ├── 关键词是 OCR 文本的连续子串 → similarity = 100（精确命中）
        │    ├── 关键词与 OCR 文本部分重叠 → 按 LCS 长度与关键词长度的比值计算 similarity
        │    └── 过滤保留 similarity >= 60 的结果，按分数降序排列，返回全量匹配（多结果每页 10 条分页）
        │    └── 如果存在 similarity = 100 的结果，只返回 similarity = 100 的结果
        │
        ▼
        ├── 无结果 → “没有匹配到任何表情包 🙁”
        │
        ├── 唯一结果 → 直接发送对应表情包图片
        │
        └── 多个结果 (设为 N 条，每页 10 条)
              └── “找到多个匹配的表情包，请选择：\n”
                  “1. 当你的老板说今天要加班 -- 1.3, 新三国, 无, 100%\n”
                  “2. 加班到凌晨三点的我 -- 2.7, 旧三国, 小明, 吐槽, 加班, 100%\n”
                  “3. 周日晚上的加班通知 -- 0.5, 全局, 无, 通知, 加班, 100%\n”
                  “回复编号即可 (1-{N})”  (N 为当前页条数)
                  “回复 n 看下一页”  (仅当存在下一页)
                      │
                      ▼
              用户回复编号 (如 “2”) 或 “n” 翻下一页
                      │
                      ▼
              Bot 查 sqlite 中对应 image_path → 发送匹配图片，再发送文本消息 “<公开ID>, <合集>, <speaker>, <tags...>”
```

#### 交互约束

- 关键词搜索前先读取当前 ChatScope 的合集选择；`selection.search_filter=None` 时搜索全部合集，为整数时只搜索该合集。
- 关键词先做精确子串匹配（用去除所有空白、保留助词的原始输入；OCR 文本按英文逗号拼接存储，匹配时忽略逗号分隔符）；命中则只返回包含该子串的结果，否则回退到 jieba 去助词后的 LCS 模糊匹配。
- 等待用户选择时设置超时（默认 60 秒，由 `SESSION_EXPIRE_TIMEOUT` 控制），超时回复”选择已过期，请重新搜索”
- 选择超时后清理本次候选状态；用户迟到回复不再视为本次搜索选择
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；如果用户在选择前发起新的普通文本搜索或 `/add`，已有活跃会话则拒绝新命令，并提示”已有命令在处理中，请先 /cancel”
- 等待选择期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本（不影响等待状态）
- 多结果列表显示临时选择序号、OCR 文本、元数据 “公开ID, 合集, speaker, tags” 以及关键词相似度百分比（score 量纲，0–100）；speaker 缺失时显示 “无”，tags 为空时省略 tags 段；speaker 与 tags 同时为空时省略 “无” 占位仅展示公开 ID 与合集；用户回复的是临时选择序号
- 用户输入无效编号时回复”无效编号，请回复 1-{N} 之间的数字”
- 搜索返回全量匹配结果，多结果按每页 10 条分页展示；回复 `n` 查看下一页，末页回复 `n` 提示”没有更多结果了”并保持当前页；翻页重置选择超时（`SESSION_EXPIRE_TIMEOUT`）

### 3.2 功能：组合检索

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/query <关键词> [@说话人] [#标签...]`（短命令 `/q`）

`#tag` 标记标签（可多个，AND 同时满足）；`@speaker` 标记说话人（可多个，OR 任一命中）；其余 token 为关键词。三者可单独或组合使用。

#### 流程

```
用户: /query 加班 @小明 #吐槽
        │
        ▼
Bot 解析: keyword="加班", speakers=["小明"], tags=["吐槽"]
        │
        ▼
读取当前 ChatScope 的 CollectionSelection
        │
        ▼
IndexManager.search_combined(keyword, speakers, tags, collection_id=selection.search_filter)（持读锁）
        ├── CombinedSearcher: 在当前合集子集上过滤 speaker∈["小明"](OR) AND "吐槽"∈tags(AND)
        ├── keyword 非空 -> KeywordSearcher.search_in(子集, "加班")
        └── 返回带 similarity 的结果
        │
        ▼
dispatch_search_results（复用关键词搜索的空/单/多结果分支）
        ├── 无结果 -> "没有匹配到任何表情包 🙁"
        ├── 单结果 -> 发图 + 元数据行（公开ID, 合集, speaker, tags）
        └── 多结果 -> 每页 10 条分页，回复 n 翻页
```

#### 交互约束

- 搜索前先读取当前 ChatScope 的合集选择；`collection_id=None` 时搜索全部合集，为整数时只搜索该合集。
- 有关键词时列表行展示关键词相似度百分比（score 0–100，同关键词搜索）；无关键词纯过滤时不展示相似度（同 `/rand`）。
- 排序：无关键词时结果随机排序；有关键词时按相似度降序分组，同相似度组内随机排序（一次 `/query` 洗牌一次，翻页顺序稳定）。
- speaker 精确相等、区分大小写；tags 精确匹配、区分大小写、多个为 AND；多 speaker 为 OR。
- `#`/`@` 单独成 token（前缀后为空）忽略；三者皆空时回复用法提示。
- 权限属组 B（私聊 + 群聊 @bot）；与 `/query`、`/sim`、`/add` 等共用会话互斥，索引查询持读锁。

### 3.3 功能：合集管理

授权用户在私聊中通过 `/collection <子命令>` 管理表情包合集，包含 `create`、`delete`、`rename` 三个子命令。三者均属组 A（仅私聊），校验通过后直接执行，不要求用户再次确认；群聊中 @bot 调用时回复“此命令仅限私聊使用”；刷新进行中时拒绝并回复“索引正在刷新，请稍后再试”；服务未就绪或正在关闭时返回对应失败提示。

#### 创建合集 `/collection create`

##### 触发方式

授权用户在私聊中发送命令：`/collection create <名称>`。

命令校验通过后直接执行，不要求用户再次确认。

##### 流程

```text
授权用户: /collection create 新三国
        │
        ▼
Bot 校验授权、私聊范围和命令参数
        │
        ▼
IndexManager.create_collection("新三国")
        │
        ├── 规范化名称：去除首尾空白并校验单层目录名规则
        ├── 将 CREATE_COLLECTION 请求加入 Write Worker 队列
        ├── Write Worker 串行取得全局写锁并重新校验名称
        ├── 已登记同名合集 → 返回重名错误
        ├── memes/新三国 不存在 → 创建普通目录
        ├── 已存在同名普通目录 → 保留目录，只登记合集
        ├── 同名路径是文件、符号链接或目录身份异常 → 拒绝登记
        └── 向 sqlite meme_collection 写入最小可用正整数编号
        │
        ▼
Bot 回复:
合集创建完成 ✅
编号：3
名称：新三国
```

如果登记的是已有普通目录，成功回复追加“已登记现有目录；目录中的图片请执行 /refresh 建立索引”。创建成功后不修改当前 `ChatScope`，用户需要显式执行 `/switch <编号|名称>` 才会切换。

##### 名称规则

- 允许中文；先去除名称首尾空白。
- 名称不能为空，不能包含内部空白、`/`、`\\` 或 NUL 字符。
- 名称不能是 `.`、`..`，不能以 `.` 开头。
- “全局”“全部合集”为保留名称，不能作为普通合集名称。
- 名称必须能安全映射为 `memes/` 下一级普通目录；不创建嵌套路径。

##### 一致性与补偿

- 创建合集通过 IndexManager Write Worker 排队，并在全局写锁内完成目录检查、目录创建和 SQLite 登记，避免与添加、编辑、删除、移动和刷新并发修改索引。
- 新目录创建成功但 SQLite 写入失败时，系统只在目录身份未变化且目录仍为空时删除本次创建的目录，再向用户返回失败。
- 如果回滚前目录身份变化、目录不为空或删除失败，系统不强制删除，记录高优先级日志，并回复“合集创建失败，请检查日志后重试”。
- 对命令执行前已经存在的普通目录，SQLite 写入失败时不删除用户原有目录。
- 创建成功只写 `meme_collection`，不扫描目录、不创建图片索引，也不自动切换当前合集。

##### 交互约束

- 只允许授权用户私聊使用；群聊中 @bot 调用时回复“此命令仅限私聊使用”。
- 参数格式不符合 `create <名称>` 时回复“用法：/collection create <名称>”。
- 名称非法时回复“合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称”。
- 名称已登记时回复“表情包合集已存在：<名称>（<编号>）”。
- 同名路径不是可用普通目录时回复“无法创建合集：同名路径不是可用目录”。
- 刷新进行中时拒绝新请求并回复“索引正在刷新，请稍后再试”。
- 服务未就绪、正在关闭或创建失败时返回对应失败提示；失败后不改变当前合集选择。

#### 删除合集 `/collection delete`

##### 触发方式

授权用户在私聊中发送命令：`/collection delete <编号|名称>`。

`<编号|名称>` 解析规则同 `/switch`：纯数字按编号优先、名称兜底；`0` 不被接受（全局不可删除）。命令校验通过后直接执行，不要求用户再次确认。

##### 流程

```text
授权用户: /collection delete 3
        │
        ▼
Bot 校验授权、私聊范围和命令参数
        │
        ▼
IndexManager.delete_collection("3")
        │
        ├── 解析目标合集（编号优先、名称兜底），不存在抛 CollectionNotFoundError
        ├── 将 DELETE_COLLECTION 请求加入 Write Worker 队列
        ├── Write Worker 串行取得全局写锁
        ├── 重新校验合集存在 + 空合集校验（meme 条目数 = 0）
        ├── 目录身份校验：memes/<名称> 必须是普通目录（非符号链接、非文件）
        ├── os.scandir 复核目录为空
        ├── 先 rmdir 空目录；失败抛 CollectionDeleteError，DB 未动
        └── 再调 metadata_store.delete_collection_and_reset_scopes：
            事务内回退所有引用该编号的 ChatScope 到 0 + 删 meme_collection 行
        │
        ▼
Bot 回复:
合集已删除 ✅
编号：3
名称：新三国
（若 reset_scope_count > 0，追加一行）
已把 N 个聊天窗口的合集选择回退到全部合集
```

只允许删除空合集；非空合集直接拒绝，不删除任何文件或 DB 记录。删除成功后，引用该合集的所有 `ChatScope` 选择被回退到 `0`（全部合集），不影响其他合集或表情包条目。

##### 一致性与补偿

- 删除合集通过 IndexManager Write Worker 排队，并在全局写锁内完成空合集校验、目录 rmdir 与 SQLite 删除，避免与添加、编辑、移动、刷新等并发修改索引。
- 顺序为**先 rmdir、后删 DB**：rmdir 失败时 DB 未动，无需回滚 SQLite；rmdir 成功后再在事务内回退 ChatScope + 删 `meme_collection` 行。
- 若 rmdir 已成功但 SQLite 删除失败（极少，sqlite 异常）：目录已删、DB 还在 -> 补偿 `mkdir()` 恢复空目录，记录 `logger.critical`，抛 `CollectionDeleteError`，回复“合集删除失败，请检查日志后重试”。
- 删除只动 `meme_collection` 与 `chat_collection_scope`；不碰 `meme`、`meme_tag` 与 chroma（空合集本就没有条目）。
- `collection_id` 编号不回收；下次 `/collection create` 仍分配最小可用正整数（可能与已删编号重用）。

##### 交互约束

- 只允许授权用户私聊使用；群聊中 @bot 调用时回复“此命令仅限私聊使用”。
- 参数格式不符合 `delete <编号|名称>` 时回复“用法：/collection delete <编号|名称>”。
- 目标合集不存在时回复“未找到表情包合集：{目标}\n发送 /switch 查看可用合集”。
- 合集非空时回复“合集不为空，请先 /move 或 /del 清空后再删除”。
- 同名路径不是可用普通目录（文件、符号链接或目录身份异常）时回复“无法删除合集：同名路径不是可用目录”。
- rmdir 失败或 SQLite 删除失败时回复“合集删除失败，请检查日志后重试”。
- 刷新进行中时拒绝新请求并回复“索引正在刷新，请稍后再试”。
- 服务未就绪、正在关闭时返回对应失败提示；失败后不改变当前合集选择。

#### 重命名合集 `/collection rename`

##### 触发方式

授权用户在私聊中发送命令：`/collection rename <旧编号|名称> <新名称>`。

`<旧编号|名称>` 解析规则同 `/switch`；`<新名称>` 走 `validate_collection_name` 校验（与 `create` 同规则），且必须是当前未登记的全新名称。命令校验通过后直接执行，不要求用户再次确认。

##### 流程

```text
授权用户: /collection rename 新三国 旧三国
        │
        ▼
Bot 校验授权、私聊范围和命令参数
        │
        ▼
IndexManager.rename_collection("新三国", "旧三国")
        │
        ├── 解析旧合集（编号优先、名称兜底），不存在抛 CollectionNotFoundError
        ├── validate_collection_name(new_name)，非法抛 InvalidCollectionNameError
        ├── 将 RENAME_COLLECTION 请求加入 Write Worker 队列
        ├── Write Worker 串行取得全局写锁
        ├── 重新校验旧合集存在 + 新名称未登记（重名抛 CollectionRenameTargetExistsError）
        ├── 目录身份校验：memes/<旧名> 必须是普通目录，memes/<新名> 必须不存在
        ├── 先改 SQLite（单事务）：
        │     UPDATE meme_collection SET name = <新名>
        │     批量 UPDATE meme.image_path 首段 <旧名> -> <新名>
        │     同步内存缓存（MemeCollection / _entries / _entries_by_collection）
        ├── 再重命名文件系统目录：memes/<旧名>.rename(memes/<新名>)
        │     失败 -> 补偿调 rename_collection 回滚 SQLite 与缓存，抛 CollectionCreateError
        └── 返回 RenameCollectionResult（新名、同 id、受影响条目数）
        │
        ▼
Bot 回复:
合集已重命名 ✅
编号：3
旧名称：新三国
新名称：旧三国
更新条目：12
```

重命名只改 `meme_collection.name`、该合集所有 `meme.image_path` 首段与 `memes/` 目录名；`collection_id` 不变，chroma 与 `ChatScope` 不受影响（`ChatScope.selected_collection_id` 按编号引用，不随名称变化）。

##### 一致性与补偿

- 重命名通过 IndexManager Write Worker 排队，并在全局写锁内完成新旧名称校验、目录身份校验、SQLite 更新与文件系统重命名，避免与添加、编辑、移动、刷新等并发修改索引。
- 顺序为**先 SQLite 后文件**：SQLite 单事务内改 `meme_collection.name` 与该合集所有 `meme.image_path` 首段；事务提交后再 `Path.rename` 重命名 `memes/` 目录。`memes/` 内部同盘，非跨 bind mount，`Path.rename` 安全。
- 若 SQLite 已提交但目录 `rename` 失败：补偿调 `metadata_store.rename_collection(collection_id, old_name)` 回滚 SQLite 与缓存；补偿失败记录 `logger.critical`，抛 `CollectionCreateError`，回复“合集重命名失败，请检查日志后重试”。
- `image_path` 全库 UNIQUE，重命名只改首段，仍唯一，不会冲突。
- 重命名不重新 embed、不操作 chroma（`collection_id` 不变，向量元数据无需更新）；不修改 `ChatScope`（按编号引用）。

##### 交互约束

- 只允许授权用户私聊使用；群聊中 @bot 调用时回复“此命令仅限私聊使用”。
- 参数格式不符合 `rename <旧编号|名称> <新名称>` 时回复“用法：/collection rename <旧编号|名称> <新名称>”。
- 旧合集不存在时回复“未找到表情包合集：{目标}\n发送 /switch 查看可用合集”。
- 新名称非法时回复“合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称”。
- 新名称已登记时回复“合集名称已存在：{name}（{id}）”。
- 目标名称对应路径已存在或旧目录身份异常时回复“无法重命名：目标名称对应路径不是可用目录”。
- 目录 `rename` 失败或补偿失败时回复“合集重命名失败，请检查日志后重试”。
- 刷新进行中时拒绝新请求并回复“索引正在刷新，请稍后再试”。
- 服务未就绪、正在关闭时返回对应失败提示；失败后不改变当前合集选择。

### 3.4 功能三：聊天添加表情包

#### 触发方式

授权用户在私聊中发送命令：`/add [speaker <tags...>]`（短命令 `/a`）

`speaker` 为可选说话人，不写入 OCR 文本；`tags` 为可选标记词列表。`/add` 后的参数按空白切分，第一个词作为 `speaker`，剩余词作为 `tags`。不填参数时 `speaker` 为空，`tags` 为空列表。文件名始终由 Bot 自动生成。

#### 流程

```
授权用户: /add 小明 吐槽 加班
        │
        ▼
Bot 读取当前 ChatScope 的 CollectionSelection
        │
        ▼
Bot 回复: "请发送图片，{SESSION_EXPIRE_TIMEOUT} 秒内有效"
        │
        ▼
授权用户发送一张图片
        │
        ▼
Bot 下载图片到当前合集目录：
        ├── 当前选择为 0（全部合集/全局）时保存到 ./memes/
        ├── 当前选择为普通合集时保存到 ./memes/{合集名称}/ 根目录
        ├── 文件名按 `meme_<YYYYMMDDHHMMSS>_<hash8>` 规则自动生成
        ├── `CONVERT_TO_WEBP=true`（默认）时将 .jpg/.jpeg/.png/.gif/.bmp 转为有损 WebP（q85），转换失败降级保留原格式
        ├── `CONVERT_TO_WEBP=false` 时对 .jpg/.jpeg/.png/.webp/.gif 执行同格式压缩，.bmp 跳过
        ├── 对图片执行 OCR
        ├── 调用配置的 Embedding 服务生成 embedding
        ├── 在当前合集内按 OCR 文本去重
        └── 使用临时文件替换策略更新 index.db + chroma 向量库（先 sqlite 后 chroma，upsert 失败回滚 sqlite）
        │
        ▼
Bot 回复:
新增表情包✅，id：1.3，合集：新三国，识别到的文字为：
「加班心累时的表情包」
1.3, 新三国, 小明, 吐槽, 加班
```

#### 交互约束

- `/add` 与 `/help`、`/collection create`、`/collection delete`、`/collection rename`、`/edittext`、`/setspeaker`、`/refresh`、`/cancel` 使用同一组 `AUTHORIZED_USER_IDS` 白名单。
- 自动命名规则：生成 `meme_<YYYYMMDDHHMMSS>_<hash8>`；其中时间取 Bot 接收图片消息的本地时间，`hash8` 取图片内容 SHA-256 的前 8 位。
- `/add` 一次只支持添加一张图片；如果用户发送多张图片，v1.0 只处理第一张。
- `/add` 保存新增图片时，目标目录由当前 ChatScope 的合集选择决定：`0` 保存到 `memes/` 根目录，普通合集保存到 `memes/{合集名称}/` 根目录，不写入合集内深层子目录。
- `/add` 保存新增图片后，按 `CONVERT_TO_WEBP` 开关执行图片压缩/转换：开关开启（默认）时将 `.jpg/.jpeg/.png/.gif/.bmp` 转为有损 WebP（q85）；开关关闭时对 `.jpg/.jpeg/.png/.webp/.gif` 执行同格式压缩，`.bmp` 跳过。转换/压缩成功后覆盖目标目录中的原图片文件（转 WebP 时生成新 `.webp` 文件并删除原文件）。
- `.bmp` 图片在开关关闭时不执行压缩，直接继续 OCR 和 embedding 流程；开关开启时同样转为 WebP。
- 不支持的图片扩展名不作为表情包处理。
- `/add` 中图片转换/压缩失败时降级保留原格式继续 OCR 与建索引（`_convert_image_to_webp` 内部已清理 `.webp` 孤儿，不删除图片、不中断流程）。
- Bot 提示”请发送图片”后等待超时（默认 60 秒，由 `SESSION_EXPIRE_TIMEOUT` 控制）；超时回复”发送图片超时，请重新 /add”。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；如果等待图片期间用户再次发送 `/add` 或普通文本搜索，已有活跃会话则拒绝新命令，并提示"已有命令在处理中，请先 /cancel"。用户也可通过 `/cancel` 手动取消当前添加，通过 `/help` 旁路查看帮助文本（不影响等待状态）。
- 文件扩展名优先使用消息或下载文件中的原始扩展名；如果缺失，则根据下载响应 `Content-Type` 推断；仍无法推断时拒绝添加。
- 添加过程中如果 OCR 或 embedding 失败，删除刚下载的图片，不写入索引，并回复添加失败原因。
- `/add` 写入前在当前合集内按 OCR 文本去重：以「按空白分割后以英文逗号拼接的文本」为去重键，若命中同一合集的已有表情包，则将旧图片文件移动到 `memes/` 同级的 `memes_replaced/` 目录归档，并用新图替换（复用旧索引 ID、旧 `local_id` 与旧公开 ID，覆盖 `image_path` 与 embedding）；跨合集的同文本条目互不影响。该机制默认认为去重键相同即为同一表情包，不额外校验图片内容。
- `/add` 若 OCR 结果按英文逗号拼接后为空（无文字图片），则将该图片移动到 `memes/` 同级的 `meme_no_text/` 目录（不进索引），并回复"未识别到文字，已移至 meme_no_text/"。
- `/add` 与 `/refresh` 共用全局索引更新锁；锁占用期间触发 `/add` 时回复“索引正在刷新，请稍后再试”。
- 最终写入前 IndexManager 会重新校验目标合集是否存在；若刷新期间该合集被删除，添加失败并清理已下载文件，不把图片写入已失效路径。

### 3.5 功能四：帮助命令

#### 触发方式

授权用户在私聊中发送命令：`/help`（短命令 `/h`）

授权用户在私聊中发送不以 `/` 开头的普通文本时，Bot 等同执行关键词搜索（原 `/search` 命令已删除）。

#### 流程

```text
授权用户: /help
        │
        ▼
Bot 回复当前可用命令和简单用法：
/help (/h)：查看命令帮助
直接发送关键词：按关键词检索表情包（结果过多时支持翻页）
/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足；结果过多时支持翻页）
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包（结果过多时支持翻页）
/add [speaker <tags...>] (/a)：通过聊天添加一张表情包
/addtag <公开ID> <tag>... (/at)：为指定表情包添加标签
/del <公开ID>... (/d)：删除指定表情包（需确认）
/edittext <公开ID> <新文本> (/e)：修改指定表情包的 OCR 文本
/setspeaker <公开ID> [说话人] (/sp)：设置或清空表情包的说话人
/collection create <名称>：创建表情包合集
/collection delete <编号|名称>：删除空合集
/collection rename <旧编号|名称> <新名称>：重命名合集
/switch [合集编号|名称]：查看或切换表情包合集
/move <公开ID> <目标合集编号|名称>：移动表情包（需确认）
/refresh (/r)：扫描 memes/ 并增量更新索引
/info [公开ID]：查看机器人状态与统计信息，或查看指定表情包详情
/cancel (/c)：取消当前正在执行的命令
```

#### 交互约束

- `/help` 与 `/collection create`、`/collection delete`、`/collection rename`、`/add`、`/edittext`、`/setspeaker`、`/refresh`、`/cancel` 使用同一组 `AUTHORIZED_USER_IDS` 白名单。
- 非授权用户私聊发送 `/help` 或普通文本时静默忽略，仅记录日志。
- 非授权用户在群聊中 @bot 发送任何消息时静默忽略，仅记录日志。
- 授权用户在群聊中 @bot 发送 `/help`、普通文本、`/query`、`/rand`、`/sim`、`/info`、`/switch` 时可正常触发命令。
- 授权用户在群聊中 @bot 发送 `/collection create`、`/collection delete`、`/collection rename`、`/add`、`/addtag`、`/del`、`/refresh`、`/edittext`、`/setspeaker`、`/move` 时回复”此命令仅限私聊使用”。
- 授权用户私聊发送未知斜杠命令时，回复”未知命令”并附帮助摘要。
- 授权用户群聊中 @bot 发送未知斜杠命令时，回复”未知命令”并附帮助摘要。
- 授权用户私聊发送已知命令但缺少必要参数时，回复该命令的用法提示，不直接执行完整 `/help`。

### 3.6 辅助功能：索引管理

#### Schema 版本与启动校验

`MetadataStore.load()` 在启动时校验 `schema_version`。当前 Schema 版本为 `2`：数据库不存在时自动创建当前 Schema；已是当前版本时正常加载；旧版、未知版本、缺少 `schema_version` 表或关键结构不完整时抛出 `SchemaVersionError`，Bot 拒绝启动，并提示管理员停止 Bot 后运行：

```bash
uv run python -m scripts.migrate_meme_collections upgrade-schema
```

运行时代码不执行自动迁移、`ALTER TABLE` 或旧数据转换。

#### 目录结构

`memes/` 支持两种存放方式：

- 根目录直接存放图片，归属“全局”（`collection_id=0`），公开 ID 为 `0.x`；
- 一级目录作为表情包合集，目录名即合集名称，系统分配正整数合集编号；合集目录内可继续嵌套任意深度子目录，所有图片都归属该合集。

规则：

- 只识别 `memes/` 根目录下的直接图片和一级目录；二级及以上目录不作为合集边界；
- 一级目录名以 `.` 开头时跳过，不跟随文件或目录符号链接；
- 空一级目录（不含受支持图片）不会被登记为合集；
- 已登记合集变空后，只要目录仍存在，记录继续保留；
- 已登记合集的目录消失后，`/refresh` 会删除该合集记录，并把引用它的所有 `ChatScope` 选择回退到 `0`。

示例：

```text
memes/
├── root.webp              # 全局，公开 ID 0.1
├── 新三国/
│   ├── a.webp             # 新三国，公开 ID 1.1
│   └── 截图/
│       └── b.webp         # 新三国，公开 ID 1.2
└── 甄嬛传/
    └── c.webp             # 甄嬛传，公开 ID 2.1
```

#### 索引初始化与启动同步

Bot 启动时自动递归扫描 `./memes/` 目录，并在后台执行与 `/refresh` 相同的增量同步策略。索引同步在后台进行，Bot 启动后立即可用（用已有索引响应命令）；同步期间搜索命令会提示”索引更新较慢，请稍后再试”；启动期间通过日志输出进度。

1. 启动时先校验 `data/index.db` Schema 版本；旧 Schema 拒绝启动。
2. 如果 `data/index.db` 不存在或为空，对全部图片执行 OCR + embed，写入 sqlite 与 chroma；根目录图片写入 `collection_id=0`，合集图片按目录写入对应 `collection_id`。
3. 如果已有索引数据，自动处理新增图片和已删除图片。
4. 新增图片先执行图片压缩/转换：`CONVERT_TO_WEBP=true`（默认）时将 `.jpg/.jpeg/.png/.gif/.bmp` 转为有损 WebP（q85），转换失败降级保留原格式；`CONVERT_TO_WEBP=false` 时对 `.jpg/.jpeg/.png/.webp/.gif` 执行同格式压缩，`.bmp` 跳过；不支持的扩展名不作为表情包处理。
5. 压缩成功或无需压缩后，对新增图片自动 OCR，并生成对应 embedding。
6. 已删除图片对应记录会从 sqlite 与 chroma 中删除（先 sqlite 后 chroma）。
7. 同步执行以下阶段：
   - 阶段0：跨库一致性修复（对齐 sqlite ↔ chroma 的内部 id 集合；校验并修复每条向量的 `collection_id` 元数据；chroma 损坏/为空且 sqlite 有数据时自动全量重 embed 并 `rebuild_all`）。
   - 阶段1：删除 `memes/` 中已不存在的图片记录。
   - 阶段2：删除目录已消失的已登记合集，并在同一 SQLite 事务把引用该编号的所有 `ChatScope` 选择更新为 `0`。
   - 阶段3：登记包含受支持图片的新合集。
   - 阶段4：新增图片根据相对路径确定合集，按合集内最小局部编号分配 `local_id`，写入 sqlite 与 chroma（向量记录附带 `collection_id` 元数据）。

#### 增量更新

授权用户发送 `/refresh`（短命令 `/r`），执行与启动同步相同的增量刷新：
1. 递归扫描 `./memes/`，识别根目录图片、一级合集目录及其内部任意深度子目录中的受支持图片；跳过隐藏目录和符号链接。
2. 对新增图片先执行图片压缩/转换：`CONVERT_TO_WEBP=true`（默认）时将 `.jpg/.jpeg/.png/.gif/.bmp` 转为有损 WebP（q85），转换失败降级保留原格式；`CONVERT_TO_WEBP=false` 时对 `.jpg/.jpeg/.png/.webp/.gif` 执行同格式压缩，`.bmp` 跳过；不支持的扩展名不作为表情包处理。
3. 压缩成功或无需压缩后，对新增图片执行 OCR，生成新的 sqlite 条目。
4. 对新增图片生成 embedding，写入 chroma 向量库（附带 `collection_id` 元数据）。
5. 对已经从 `./memes/` 删除的图片，从 sqlite、chroma 中删除对应记录；启动时也执行同样的删除清理（先 sqlite 后 chroma）。
6. 删除记录后保持其他已有内部 id 稳定，不重新编号，允许临时编号空洞。每个合集独立分配 `local_id`，删除或移出表情包后复用该合集最小局部空号。
7. 多个新增图片按相对路径升序处理；每张新增图片优先复用最小空洞内部 id，如果没有空洞，则使用当前最大 id + 1。
8. 对文件名仍存在的图片不重新 OCR，不重新生成 embedding。
9. 新增图片压缩失败或 OCR 调用异常时跳过该图片，不写入索引；刷新继续处理其他图片，最终回复中汇总失败文件列表。
10. 新增图片 OCR 成功但 embedding 生成失败时，该图片不写入 sqlite、chroma；刷新继续处理其他图片，最终回复中汇总失败文件列表。
11. `/refresh` 完成后回复摘要：新增合集数、删除合集数、回退窗口数、新增图片数、删除图片数、去重归档数、无文字移走数、失败数量；如有失败，最多列出前 10 个失败文件路径。
12. 新增图片 OCR 后在所属合集内按「按空白分割后以英文逗号拼接的文本」去重键判定：若与同一合集的已有条目或其他新增图片去重键相同，则保留已有条目或相对路径升序靠前的新图，将被判定为重复的新图文件移动到 `memes/` 同级的 `memes_replaced/` 目录归档，不写入索引；该去重在 `/refresh` 回复中以「去重归档」单独统计，不计入新增或删除。跨合集同文本图片分别入库。
13. 新增图片 OCR 结果按英文逗号拼接后为空（无文字图片）时，移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引；sqlite 中本功能上线前已存在的「未识别到文字」占位条目不清理（sync 不重新 OCR 已有条目）。

不检测同名覆盖：如果用户用新图片覆盖了旧图片但文件名不变，`/refresh` 不会重新 OCR，该限制需要在使用说明中明确。

权限约束：`/help`、`/query`、`/rand`、`/sim`、`/collection create`、`/collection delete`、`/collection rename`、`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/refresh`、`/info`、`/cancel`、`/switch`、`/move` 使用同一组 `AUTHORIZED_USER_IDS` 白名单；非授权用户的私聊/群聊消息不触发任何业务命令，并静默忽略（仅记录日志，不回复提示）。群聊行为按命令分组：
- 组 A（仅私聊）：`/collection create`、`/collection delete`、`/collection rename`、`/add`、`/addtag`、`/del`、`/refresh`、`/edittext`、`/setspeaker`、`/move` — 授权用户群聊中 @bot 调用时回复"此命令仅限私聊使用"
- 组 B（私聊 + 群聊@）：`/query`、`/rand`、`/sim`、`/info`、`/help`、普通文本、`/switch` — 授权用户群聊中 @bot 时可正常触发
- 组 C（私聊 + 群聊@）：`/cancel` — 授权用户私聊或群聊中 @bot 均可正常触发

并发约束：`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/move`、`/collection create`、`/collection delete`、`/collection rename` 由 IndexManager Write Worker 串行处理，并在执行时持有同一个全局写锁；`/refresh` 也使用该写锁，同一时间只允许一个索引写入任务运行。刷新进行中时，后续授权用户触发这些写命令会收到"索引正在刷新，请稍后再试"。`/query`、`/rand`、`/sim`、`/switch` 与 `/info <公开ID>` 通过读锁访问索引，写锁占用期间等待超时后回复"索引更新较慢，请稍后再试"。`/info` 总体信息不等待索引锁；`/info <公开ID>` 持读锁查询，超时时同样回复"索引更新较慢，请稍后再试"。`/cancel` 和 `/help` 在有活跃会话时可旁路触发：`/help` 回复帮助文本后等待继续，`/cancel` 取消当前会话。

写入约束：索引更新统一「先 sqlite 后 chroma」的写入顺序。新增/替换条目时先写 sqlite，再 `VectorStore.upsert` 写 chroma（写入 `collection_id` 元数据）；若 chroma upsert 失败，回滚 sqlite 写入（删除刚写入的行或恢复旧 `image_path`），保证两库一致。`/edittext` 修改文本时同步更新 sqlite 与 chroma（重新 embed）。`/setspeaker` 仅修改 sqlite `speaker` 字段，不操作 chroma。`/addtag` 仅追加 sqlite `meme_tag`，不操作 chroma。`/del` 先将图片归档到 `memes_deleted/`，再先 sqlite 后 chroma 删除索引；移图失败时索引原样保留（仅记失败），避免文件残留 `memes/` 在下次 `/refresh` 被重新入库（已删表情包复活）。`/move` 在写锁内移动文件，先更新 sqlite 的 `image_path`、`collection_id` 与 `local_id`，再更新 Chroma 的 `collection_id` 元数据；任一失败时执行补偿，恢复文件、sqlite 原字段与 Chroma 原元数据。同步阶段0 检测到 chroma 为空且 sqlite 有数据时，自动全量重 embed 并 `rebuild_all`；检测到 sqlite 有而 chroma 无的 id 时补 embed `upsert`；检测到 chroma 有而 sqlite 无的 id 时删孤儿向量。

#### 索引文件格式

**`data/index.db`（sqlite3 元数据库）**：

```sql
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE meme_collection (
    id INTEGER PRIMARY KEY CHECK (id > 0),
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL CHECK (collection_id >= 0),
    local_id INTEGER NOT NULL CHECK (local_id > 0),
    image_path TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT
);
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
CREATE UNIQUE INDEX idx_meme_collection_local
    ON meme(collection_id, local_id);
CREATE UNIQUE INDEX idx_meme_collection_text
    ON meme(collection_id, text);

CREATE TABLE meme_tag (
    meme_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (meme_id, tag),
    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
);
CREATE INDEX idx_meme_tag_tag ON meme_tag(tag);

CREATE TABLE chat_collection_scope (
    user_id INTEGER NOT NULL,
    chat_type TEXT NOT NULL CHECK (chat_type IN ('private', 'group')),
    chat_id INTEGER NOT NULL,
    selected_collection_id INTEGER NOT NULL CHECK (selected_collection_id >= 0),
    PRIMARY KEY (user_id, chat_type, chat_id)
);
```

`meme` 表保留内部主键 `id`（`INTEGER PRIMARY KEY`，手动分配最小空洞 id，不用 `AUTOINCREMENT`）；`collection_id=0` 表示根目录“全局”，普通合集使用正整数；`local_id` 为合集中独立分配的最小空洞编号；`image_path` 为 `memes/` 下相对路径（可能是根目录文件名或合集内嵌套路径）；`text` 为 OCR 按空白分割后以英文逗号拼接的文本；`speaker` 为说话人。`meme_collection` 保存普通合集注册表；`chat_collection_scope` 按 `(user_id, chat_type, chat_id)` 持久化当前合集选择；`schema_version` 必须恰好一行且值为 `2`。

`meme_tag` 关联表存多值标记词，`ON DELETE CASCADE` 随 `meme` 行删除。`PRAGMA foreign_keys = ON`。`image_path` 全库唯一；`(collection_id, local_id)` 与 `(collection_id, text)` 为联合唯一约束；`IndexManager` 仍通过 `get_id_by_text(..., collection_id=...)` 在写入前去重，DB 层 UNIQUE 作为兜底，冲突抛 `DuplicateEntryError`。

**`data/chroma/`（ChromaDB 向量库）**：

ChromaDB `PersistentClient` 数据目录，包含一个 collection（默认名 `memes`，HNSW `cosine` 距离）。每条向量保存 `id`（内部转 `str`，与 sqlite `meme.id` 一一对应）、`embedding`（1024 维 float32）以及 `collection_id` 元数据，用于按合集过滤召回。`similarity = 1 - distance`。向量库由系统自动维护，不建议手动编辑。

---

### 3.7 功能五：OCR 文本编辑

#### 触发方式

授权用户在私聊中发送命令：`/edittext <公开ID> <新文本>`（短命令 `/e`）

`公开ID` 为完整复合 ID（如 `1.3`），或在当前普通合集模式下使用局部短号（如 `003`）；全部合集模式下拒绝短号。

#### 流程

```text
授权用户: /edittext 1.3 新的OCR文字
        │
        ▼
Bot 按当前 ChatScope 解析公开 ID 并校验条目存在 → 发送确认消息
        │
        ▼
授权用户回复: 确认
        │
        ▼
Bot 更新 sqlite 中该条目的 text 字段，并重新生成 embedding 写入 chroma
        │
        ▼
Bot 回复: 文本已修改 ✅
```

#### 交互约束

- 仅限私聊；群聊 @bot 调用时回复"此命令仅限私聊使用"。
- `<公开ID>` 格式错误或当前为全部合集时使用短号，回复对应公开 ID 领域提示；不存在时回复"未找到 ID 为 {公开ID} 的表情包"。
- 修改前 Bot 发送确认消息，用户回复"确认"后执行修改。
- 新文本与同一合集中已有条目 OCR 文本冲突时，回复"该文本已被其他表情包使用"（`DuplicateTextError`）。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"修改已取消（超时）"；用户回复非"确认"时回复"已取消修改"。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.8 功能六：说话人设置

#### 触发方式

授权用户在私聊中发送命令：`/setspeaker <公开ID> [说话人]`（短命令 `/sp`）

`公开ID` 为完整复合 ID（如 `1.3`），或在当前普通合集模式下使用局部短号；全部合集模式下拒绝短号。`[说话人]` 为可选参数；缺省时清空该条目的 `speaker` 字段。

#### 流程

```text
授权用户: /setspeaker 1.3 小明
        │
        ▼
Bot 按当前 ChatScope 解析公开 ID 并校验条目存在 → 发送对应表情包图片 → 发送确认消息（旧说话人 → 新说话人）
        │
        ▼
授权用户回复: 确认  或  yes/y
        │
        ▼
Bot 更新 sqlite 中该条目的 speaker 字段（不操作 chroma）
        │
        ▼
Bot 回复: 说话人已设置 ✅
```

#### 交互约束

- 仅限私聊；群聊 @bot 调用时回复"此命令仅限私聊使用"。
- `<公开ID>` 格式错误或当前为全部合集时使用短号，回复对应公开 ID 领域提示；不存在时回复"未找到 ID 为 {公开ID} 的表情包"。
- 修改前 Bot 发送图片和确认消息，用户回复"确认"、"yes"或"y"后执行修改。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"说话人设置已取消（超时）"；用户回复非确认内容时回复"已取消"。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.9 功能七：随机选择

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/rand [关键词]`

`[关键词]` 为可选参数；有关键词时先在当前合集范围内按关键词搜索，再在命中结果中随机取 10 个；无关键词时在当前合集范围内随机取 10 个。

#### 流程

```text
授权用户: /rand 加班
        │
        ▼
Bot 读取当前 ChatScope 的 CollectionSelection
        │
        ▼
Bot 在当前合集范围内（collection_id=selection.search_filter）随机取 10 个，列出候选：
    1. 加班到凌晨三点的我 -- 1.3, 新三国, 小明
    ...
    10. 周日晚上的加班通知 -- 0.5, 全局, 无
    回复编号即可 (1-10)
    回复 0 换一批
        │
        ▼
授权用户: 0  -> Bot 重新独立抽样 10 个，再次列出候选（换一批）
授权用户: 2  -> Bot 发送对应表情包，并附元数据行（公开ID, 合集, speaker, tags）
```

#### 交互约束

- 私聊与群聊 @bot 均可触发（组 B）。
- 搜索范围由当前 ChatScope 的合集选择决定；`selection.search_filter=None` 时搜索全部合集，为整数时只搜索该合集。
- 通过读锁访问索引；写锁占用期间等待超时后回复"索引更新较慢，请稍后再试"。
- 空库时无关键词回复"表情包目录为空，请先添加图片并执行 /refresh"，有关键词但无命中回复"没有匹配到任何表情包 🙁"。
- 候选每页 10 条；回复 `0` 换一批（重新独立抽样），回复编号发送对应表情包。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；等待选择期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。
- 等待选择期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"选择已过期，请重新搜索"。

---

### 3.10 功能八：语义选择

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/sim <描述文本>`

`<描述文本>` 为必填的自然语言描述；Bot 对其生成 embedding 后按当前合集范围做语义搜索召回（`limit=None` 全库召回，按相似度降序），直接向用户展示结果。

#### 流程

```text
授权用户: /sim 一张表达心累的加班表情包
        │
        ▼
Bot 读取当前 ChatScope 的 CollectionSelection
        │
        ▼
Bot 对描述生成 embedding（锁外），按当前合集范围（collection_id=selection.search_filter）语义搜索召回并按相似度降序列出：
    1. 加班到凌晨三点的我 -- 1.3, 新三国, 小明, 吐槽, 加班, 82%
    2. 心累的打工人 -- 0.5, 全局, 无, 76%
    回复编号即可 (1-2)
    回复 n 看下一页
        │
        ▼
授权用户: 1
        │
        ▼
Bot 发送对应表情包，并附元数据行（公开ID, 合集, speaker, tags）
```

#### 交互约束

- 私聊与群聊 @bot 均可触发（组 B）。
- embedding 生成在锁外进行，语义搜索持读锁；写锁占用期间等待超时后回复"索引更新较慢，请稍后再试"。
- 搜索范围由当前 ChatScope 的合集选择决定；`selection.search_filter=None` 时搜索全部合集，为整数时只搜索该合集，使用 Chroma `where={"collection_id": N}` 过滤。
- `<描述文本>` 缺省时回复"/sim <描述文本>"。
- 列表行展示语义相似度百分比（`similarity = 1 - distance`，按比例换算）；多结果每页 10 条，回复 `n` 看下一页。
- 用户描述 embedding 为零向量时回复"AI 服务暂时不可用，稍后重试"。
- 空库或无结果时回复"没有找到匹配的表情包 🙁"。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；等待选择期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.11 功能九：标签添加

#### 触发方式

授权用户在私聊中发送命令：`/addtag <公开ID> <tag> [<tag>...]`（短命令 `/at`）

第一个参数为 `公开ID`（完整 ID 或当前普通合集短号），其后为待追加的标签列表（按空白切分，过滤空串）。

#### 流程

```text
授权用户: /addtag 1.3 心累 深夜
        │
        ▼
Bot 按当前 ChatScope 解析公开 ID 并校验条目存在 -> 发送确认消息（当前 OCR 文本、当前标签、新增标签）
        │
        ▼
授权用户: 确认  或  yes/y
        │
        ▼
Bot 向 sqlite meme_tag 追加新标签（去重，不操作 chroma）
        │
        ▼
Bot 回复: 标签已添加 ✅
         本次新增：心累, 深夜
         全部标签：吐槽, 加班, 心累, 深夜
```

#### 交互约束

- 仅限私聊；群聊 @bot 调用时回复"此命令仅限私聊使用"。
- `<公开ID>` 格式错误或当前为全部合集时使用短号，回复对应公开 ID 领域提示；不存在时回复"未找到 ID 为 {公开ID} 的表情包"。
- 至少需要 `<公开ID>` 与一个 `<tag>`；参数不足回复"用法：/addtag <公开ID> <tag> [<tag>...]"。
- 修改前 Bot 发送确认消息（纯文本，不发送图片），用户回复"确认"、"yes"或"y"后执行追加。
- 仅追加 sqlite `meme_tag`，不操作 chroma；已存在的标签不重复添加，回复中"本次新增"为实际新增项（可能为"无"）。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"标签添加已取消（超时）"；用户回复非确认内容时回复"已取消"。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.12 功能十：删除表情包

#### 触发方式

授权用户在私聊中发送命令：`/del <公开ID>...`（短命令 `/d`）

支持一次删除多个表情包，公开 ID 间以空格分隔；支持完整 ID 与当前普通合集短号混用，按内部 ID 去重并保持顺序。

#### 流程

```text
授权用户: /del 1.3 0.5
        │
        ▼
Bot 按当前 ChatScope 解析每个公开 ID，发送待删除条目摘要（公开ID + 截断后的 OCR 文本），未找到的 ID 一并提示
        │
        ▼
授权用户: 确认  或  yes/y
        │
        ▼
Bot 逐条删除：先将图片归档到 memes_deleted/，再先 sqlite 后 chroma 删除索引
        │
        ▼
Bot 回复: 删除结果如下:
         成功：1.3、0.5
         （未找到 / 失败的 ID 分别列出）
```

#### 交互约束

- 仅限私聊；群聊 @bot 调用时回复"此命令仅限私聊使用"。
- `<公开ID>` 格式错误或当前为全部合集时使用短号，回复对应公开 ID 领域提示；全部不存在时回复"未找到任何表情包"。
- 修改前 Bot 发送摘要确认消息，用户回复"确认"、"yes"或"y"后执行删除。
- 删除顺序：先将图片归档到 `memes_deleted/`（同名冲突追加序号），再先 sqlite 后 chroma 删除索引。移图失败时索引原样保留，仅将该 ID 记为失败，避免文件残留 `memes/` 在下次 `/refresh` 被重新入库（已删表情包复活）；用户可重试。
- 文件本就不在 `memes/` 时跳过移动，直接删除索引。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"删除已取消（超时）"；用户回复非确认内容时回复"已取消删除"。
- 同一授权用户在同一聊天窗口内同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.13 功能十一：状态信息

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/info [公开ID]`

- 无参数：返回索引统计、当前合集范围、当前状态、系统内存、进程内存与 CPU 占用。
- 带公开 ID：返回指定表情包的详细信息（公开 ID、合集、OCR 文本、文件名、大小、说话人、标签）。

#### 流程（总体信息）

```text
授权用户: /info
        │
        ▼
Bot 读取当前 ChatScope 的 CollectionSelection，再读取索引统计 + 本机硬件信息 + 当前进程 RSS，组装回复：
    表情包总数：128
    当前合集：新三国（30 张）
    普通合集数：4
    当前合集说话人排行（前 10）：
      1. 小明 45
      2. 无 32
      3. 小红 28
    当前机器人状态：空闲
    内存占用：512 MiB / 2048 MiB (25%)
    进程内存：123 MiB
    CPU占用：12%
```

#### 流程（公开ID 详情）

```text
授权用户: /info 1.3
        │
        ▼
Bot 按当前 ChatScope 解析公开 ID，持读锁查询条目，读取文件大小，组装回复：
    id：1.3
    合集：新三国
    文本：加班心累
    文件名：新三国/截图/meme_xxx.webp
    大小：123.45 KiB
    说话人：小明
    标签：吐槽, 加班
```

#### 交互约束

- 私聊与群聊 @bot 均可触发（组 B）。
- `/info` 总体信息不等待索引锁；即使索引正在刷新也能返回统计。
- `/info <公开ID>` 持 `IndexRwLock` 读锁查询，刷新期间等待读锁超时时回复"索引更新较慢，请稍后再试"。
- 总体回复包含：表情包总数、当前合集及该合集数量、普通合集数、当前合集 speaker 使用频率排行（前 10，`speaker` 为空显示"无"）、当前状态、系统内存占用、进程内存占用、CPU 占用。
- 公开 ID 详情回复包含：公开 ID、合集名称、OCR 文本、文件名、文件大小（自动 B/KB/MB）、说话人（空显示“无”）、标签（空显示“无”）；speaker 与 tags 同时为空时省略这两项。
- 当前状态取值：`空闲`、`正在处理命令`（引擎空闲但有活跃命令会话时由插件层覆写）、`正在刷新索引`（refresh 进行中）。
- 硬件/进程内存/文件大小读取失败时对应字段显示"获取失败"或"文件不存在"，不影响其他字段。
- 公开 ID 格式错误、当前为全部合集时使用短号、或 ID 不存在时回退到总体信息输出。
- 索引信息获取异常时回复"索引信息获取失败，请稍后重试"。

---

### 3.14 功能十二：合集切换 `/switch`

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/switch [合集编号|名称]`（无短命令）

#### 流程

```text
授权用户: /switch
        │
        ▼
Bot 列出当前可用的表情包合集：
    表情包合集：
    * 0. 全部合集（共 520 张）
      1. 新三国（120 张）
      2. 甄嬛传（86 张）

    当前合集：全部合集
    使用 /switch <编号|名称> 切换
```

```text
授权用户: /switch 新三国
        │
        ▼
Bot 解析目标合集 → 持久化保存到当前 ChatScope → 回复：
    已切换到合集：新三国（1）
```

```text
授权用户: /switch 0
        │
        ▼
Bot 回复：已切换到：全部合集（0）
```

#### 交互约束

- 只允许授权用户；私聊和群聊 @bot 均可用；参与现有聊天会话互斥，已有活跃会话时回复"已有命令在处理中，请先 /cancel"。
- 无参数时列出全部合集入口与普通合集数量；`0` 显示全库总数，普通合集显示自身数量，空合集显示 `0 张`。
- 带参数时把 `/switch` 后的完整剩余文本作为名称，允许名称包含空格；纯数字按编号优先、名称兜底；`0` 与任意长度全零字符串固定表示"全部合集"。
- 切换成功后立即写入 SQLite；Bot 重启后同一聊天窗口仍保持该选择。
- 合集不存在时回复"未找到表情包合集：{目标}\n发送 /switch 查看可用合集"。
- 等待读锁超时时回复"索引更新较慢，请稍后再试"。

### 3.15 功能十三：跨合集移动 `/move`

#### 触发方式

授权用户在私聊中发送命令：`/move <公开ID> <目标合集编号|名称>`（别名 /mv）

目标参数取源 ID 后的完整剩余文本，因此合集名称可以包含空格。目标 `0` 表示移动到 `memes/` 根目录（全局）。

#### 流程

```text
授权用户: /move 1.3 甄嬛传
        │
        ▼
Bot 解析源公开 ID → 解析目标合集 → 预览移动：
    确认移动表情包：
    源合集：新三国（1）
    目标合集：甄嬛传（2）
    当前编号：1.3
    预计新编号：2.5

    回复“确认”、yes 或 y 执行
        │
        ▼
授权用户: 确认
        │
        ▼
Bot 在写锁内重新分配目标合集最小局部编号、移动文件、更新 SQLite、更新 Chroma collection_id
        │
        ▼
Bot 回复：
    移动完成 ✅
    原编号：1.3
    新编号：2.6
    目标合集：甄嬛传
```

#### 交互约束

- 只允许授权用户；仅限私聊；群聊 @bot 调用时回复"此命令仅限私聊使用"。
- 与 `/refresh`、`/add`、`/del` 等写操作共享全局索引更新锁；等待写锁超时时回复"索引正在刷新，请稍后再试"。
- 源完整 ID 可跨当前合集访问；普通合集模式下允许源短号；全部合集模式下拒绝源短号。
- 目标接受编号或精确名称；目标 `0` 表示根目录；目标必须已登记，`/move` 不创建新合集；源与目标相同则拒绝。
- 目标合集已存在相同 OCR 文本时拒绝，并提示冲突条目的公开 ID。
- 系统不预留预计编号；用户确认后重新计算目标合集最小局部编号，实际编号可能与预览不同。
- 文件落点：源文件放入目标合集根目录，不保留源合集内子目录；目标为 `0` 时放到 `memes/`；同名文件使用 `_2`、`_3` 后缀。
- 移动保留 OCR、speaker、tags、内部 ID 和 Embedding，只变更 `collection_id`、`local_id`、`image_path` 与 Chroma 元数据。
- 失败补偿：文件移动失败时 SQLite 和 Chroma 不变；SQLite 失败时把文件移回源路径；Chroma 失败时恢复 SQLite 原字段、文件路径和 Chroma 原元数据；补偿失败时记录高优先级日志，用户收到"移动失败，索引将在下次刷新时检查一致性"。
- 等待确认期间支持 `/cancel` 和 `/help` 旁路；超时回复"移动已取消（超时）"；非确认回复"已取消移动"。

## 4. 非功能需求

### 4.1 性能

| 指标 | 要求 |
|------|------|
| OCR 首次建索引 | 100 张图 < 10 分钟（使用 `OCR_PROVIDER=deepseek` 等云端 OCR 时约 3s/张；使用 `rapidocr` 本地推理时受 CPU 性能影响） |
| 关键词搜索 | < 1 秒（pylcs LCS 对几千行 < 50ms；合集过滤从内存缓存取子集） |
| 创建合集 | 除写锁排队外 < 100 毫秒（创建或登记一级目录并写入 SQLite） |
| 图片发送 | NapCat 发送延迟 < 2 秒 |
| 合集切换 | < 100 毫秒（不扫描文件系统，只读写 SQLite 与内存缓存） |
| 跨合集移动 | < 1 秒（不调用 OCR 或 Embedding，只移动文件并更新元数据） |
| 离线迁移 | 面向数千张根目录图片，顺序执行即可 |

### 4.2 部署

- 默认 `docker-compose.yml` 拉取 `northhalf/meme-pilot:latest`，每次启动检查发布镜像
- 本地源码构建使用独立的 `docker-compose.build.yml`，不影响默认镜像部署
- 支持 x86_64 Linux 服务器
- Bot 端口 `BOT_PORT` 仅供 Docker 网络内 NapCat 反向 WebSocket 连接，不映射到宿主机
- 最低配置：1 核 CPU / 2GB RAM / 20GB 磁盘

### 4.3 安全

- 表情包图片仅存储在本地；OCR 文本会发送给 `OCR_PROVIDER` 配置的 OCR 服务，Embedding 由 `EMBEDDING_PROVIDER` 配置的服务生成
- .env 文件管理敏感配置（QQ 账号 / 授权用户列表 / 各 provider 对应 API Key）
- 授权用户列表通过 `AUTHORIZED_USER_IDS` 配置，多个 QQ 号用英文逗号分隔
- 通用运行时必填环境变量：`QQ_ACCOUNT`、`AUTHORIZED_USER_IDS`
- Bot 运行时只使用 `EMBEDDING_PROVIDER` 选中的凭证；但当前 `docker-compose.yml` 与 `docker-compose.build.yml` 都通过 `:?` 插值要求 `OPENAI_EMBEDDING_API_KEY` 非空。使用 Google Embedding 时仍需保留非空占位值以通过 Compose 校验，Bot 不会调用该值。
- 按 provider 使用的真实凭证：
  - `EMBEDDING_PROVIDER=openai`（默认）时使用 `OPENAI_EMBEDDING_API_KEY`
  - `EMBEDDING_PROVIDER=google` 时使用 `GOOGLE_API_KEY`
  - `OCR_PROVIDER=paddle` 时使用 `PADDLEOCR_ACCESS_TOKEN`
  - `OCR_PROVIDER=deepseek` 时使用 `OPENAI_OCR_API_KEY`
  - `OCR_PROVIDER=baidu` 时使用 `BAIDU_API_KEY` 与 `BAIDU_SECRET_KEY`；`BAIDU_OCR_TYPE` 选择 `pp_ocrv6`（默认）、`general_basic`、`general`、`accurate_basic`、`accurate`、`webimage` 或 `webimage_loc`
  - `OCR_PROVIDER=rapidocr`（默认）无需 API Key
- 可选环境变量：`NAPCAT_WEBUI_TOKEN`、`BOT_HOST`、`BOT_PORT`、`OPENAI_EMBEDDING_BASE_URL`、`OPENAI_EMBEDDING_MODEL`、`EMBEDDING_PROVIDER`、`GOOGLE_EMBEDDING_MODEL`、`GOOGLE_BASE_URL`、`OPENAI_OCR_BASE_URL`、`OPENAI_OCR_MODEL`、`PADDLEOCR_BASE_URL`、`BAIDU_OCR_TYPE`、`OCR_PROVIDER`、`OCR_TEXT_SCORE`、`READ_LOCK_TIMEOUT`、`ADD_COMMAND_TIMEOUT`、`SESSION_EXPIRE_TIMEOUT`（会话超时，默认 60 秒）、`EMBEDDING_CONCURRENCY`（Embedding 并发上限，默认 5）、`OCR_CONCURRENCY`（OCR 并发上限，默认 5）、`COMPRESS_CONCURRENCY`（图片压缩并发上限，默认 5）、`CONVERT_TO_WEBP`（图片转 WebP 开关，默认 true）
- .env 不纳入版本控制

### 4.4 维护

- `data/index.db` 为 sqlite 数据库，可用 `sqlite3` CLI 查看（`sqlite3 data/index.db "SELECT * FROM meme;"`）；`data/chroma/` 由 ChromaDB 管理，不建议手动编辑
- 支持通过 `/add` 在 QQ 私聊中添加单张表情包
- 支持手动向 memes/ 目录添加图片后 `/refresh` 更新
- 新增图片压缩/转换成功后会直接覆盖 `memes/` 中的原图片文件（转 WebP 时生成新 `.webp` 文件并删除原文件）
- 日志通过 `logging_config.py` 中的 `setup_logging()` 统一配置，同时输出到 stdout（`docker compose logs` 可查看）和文件 `log/bot.log`
- 文件日志采用滚动机制：`bot.log` 为当前文件，`bot.log.1` / `bot.log.2` / `bot.log.3` 为备份；单个文件上限 10 MB，由 Python 标准库 `RotatingFileHandler` 管理
- stdout 日志级别为 INFO，文件日志级别为 DEBUG
- `log/` 目录通过 Docker 卷 `./log:/app/log` 挂载到宿主机，`log/` 不纳入版本控制

---

## 5. 边界情况

| 场景 | 预期行为 |
|------|---------|
| memes/ 目录为空 | Bot 正常启动并在日志中 warning；普通文本搜索和 `/refresh` 回复"表情包目录为空，请先添加图片并执行 /refresh"；仍可创建空合集 |
| 图片 OCR 成功但识别不到文字 | 移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引，日志 warning |
| 新增图片 OCR 文本去重键命中已有条目或另一新增图片 | `/add` 用新图替换旧图（旧图移动到 `memes_replaced/` 归档、复用旧 ID）；`/refresh` 保留已有/靠前者，重复新图移动到 `memes_replaced/` 归档 |
| 单张新增图片 OCR 调用异常 | 跳过该图片，不写入索引；刷新继续处理其他图片，最终回复汇总失败文件列表 |
| OpenAI 兼容 OCR API 调用失败 | Bot 打印错误日志，回复"OCR 服务不可用"；本次刷新不更新索引文件 |
| 百度 OCR token 过期或失效（110/111） | 清除内存 token 缓存并刷新后重试一次；再次失败则按 OCR 调用失败处理 |
| 百度 OCR 网络/5xx/QPS 限制 | 复用统一指数退避策略最多尝试 3 次；每日或总额度耗尽、鉴权和参数错误不重试 |
| Embedding API 网络异常 | 刷新新增图片时，受影响图片不写入索引；`/sim` 生成用户描述 embedding 失败时回复"AI 服务暂时不可用，稍后重试" |
| 授权用户私聊发送普通文本 | 等同执行关键词搜索（原 `/search` 已删除），按关键词搜索表情包 |
| 授权用户私聊发送未知斜杠命令 | 回复”未知命令”并附帮助摘要 |
| 授权用户群聊中 @bot 发送未知斜杠命令 | 回复”未知命令”并附帮助摘要 |
| 无活跃会话时发送 /cancel | 回复”当前没有活跃的会话” |
| /add 等待图片时发送 /cancel | 取消添加流程，清理会话，回复”已取消 ✅” |
| 普通文本搜索等待选择时发送 /cancel | 取消搜索选择流程，清理选择会话，回复”已取消 ✅” |
| 异频道 /cancel（私聊发起取消群聊中的会话） | 支持：`execute_cancel` 按 user_id 查找并跨 task 取消 |
| 授权用户群聊 @bot 发送 `/collection create`、`/collection delete`、`/collection rename`、`/add`、`/addtag`、`/del`、`/refresh`、`/edittext`、`/setspeaker`、`/move` | 回复”此命令仅限私聊使用” |
| 授权用户群聊 @bot 发送 `/query`、`/rand`、`/sim`、`/info`、`/help`、普通文本 | 正常执行对应命令 |
| 授权用户群聊 @bot 发送 /cancel | 正常执行取消（`/cancel` 无私聊限制） |
| 非授权用户私聊发送任何内容 | 静默忽略，仅记录日志 |
| 非授权用户群聊 @bot 发送任何内容 | 静默忽略，仅记录日志 |
| 普通文本搜索无匹配 | 回复"没有匹配到任何表情包 🙁" |
| 普通文本搜索、`/sim`、兜底搜索多结果回复 `n` | 翻到下一页（每页 10 条），重置选择超时 |
| 多结果末页回复 `n` | 回复"没有更多结果了"，保持当前页，选择会话不变 |
| 用户选编号超时 | 回复"选择已过期，请重新搜索" |
| /add 等待图片超时 | 回复"发送图片超时，请重新 /add" |
| /add 收到非图片消息 | 提示"请发送一张图片"，继续等待直到超时（默认 60 秒，由 `SESSION_EXPIRE_TIMEOUT` 控制） |
| /add 收到多张图片 | v1.0 只处理第一张图片 |
| /add 无法判断图片扩展名 | 拒绝添加，回复"无法识别图片格式" |
| 新增图片转 WebP 或同格式压缩失败 | 降级保留原格式继续 OCR 与建索引；`_convert_image_to_webp` 内部已清理 `.webp` 孤儿 |
| `CONVERT_TO_WEBP=false` | 新增图片按传输格式同格式压缩（`.bmp` 跳过），不转 WebP |
| 新增 `.bmp` 图片 | `CONVERT_TO_WEBP=true` 时转为 WebP；`CONVERT_TO_WEBP=false` 时不执行压缩，继续 OCR 和建索引 |
| 新增不支持扩展名文件 | 不作为表情包处理，不写入索引 |
| /add OCR 或 embedding 失败 | 删除刚下载的图片，不写入索引，回复添加失败原因 |
| 图片文件被删除但索引还在 | sync 阶段1 删除：先 sqlite 后 chroma 删除对应记录；启动时与 `/refresh` 均执行 |
| 文件名包含特殊字符 | `image_path` 作为 sqlite `TEXT` 存储，不使用自定义分隔符解析 |
| `data/index.db` 损坏或非 sqlite 格式 | `MetadataStore.load()` 抛 `sqlite3.DatabaseError`（`IndexManager` 归并为 `IndexCorruptedError`），拒绝启动或刷新，要求用户先修复数据库 |
| chroma 损坏/与 sqlite 不一致 | sync 阶段0 跨库一致性修复：chroma 为空且 sqlite 有数据 → 全量重 embed 并 `rebuild_all`；sqlite 有、chroma 无的 id → 补 embed `upsert`；chroma 有、sqlite 无的 id → 删孤儿向量 |
| `/edittext <公开ID>` 的 ID 不存在 | 回复"未找到 ID 为 {公开ID} 的表情包" |
| `/edittext` 在索引刷新中 | 回复"索引正在刷新，请稍后再试" |
| `/edittext` 新文本与同一合集中已有条目冲突 | 回复"该文本已被其他表情包使用" |
| `/edittext` 等待确认超时 | 回复"修改已取消（超时）" |
| `/edittext` 用户回复非"确认" | 回复"已取消修改" |
| `/setspeaker <公开ID>` 的 ID 不存在 | 回复"未找到 ID 为 {公开ID} 的表情包" |
| `/setspeaker` 在索引刷新中 | 回复"索引正在刷新，请稍后再试" |
| `/setspeaker` 缺省 [说话人] | 清空 sqlite 中该条目的 speaker 字段 |
| `/setspeaker` 等待确认超时 | 回复"说话人设置已取消（超时）" |
| `/setspeaker` 用户回复非"确认/yes/y" | 回复"已取消" |
| 授权用户私聊/群聊@发送 /query | 按 keyword/speaker/tags 组合检索 |
| /query 无参数 | 回复 "/query <关键词> [@说话人] [#标签...]" |
| /query 仅 @speaker 或 #tag | 纯过滤，随机排序返回，不展示相似度 |
| /query 多 @speaker | OR 任一命中 |
| /query 多 #tag | AND 同时满足 |
| /query 关键词含 # 或 @ | 被前缀解析吞掉，不作为关键词搜索 |
| /rand 无关键词且空库 | 回复“表情包目录为空，请先添加图片并执行 /refresh” |
| /rand 有关键词但无命中 | 回复“没有匹配到任何表情包 🙁” |
| /rand 回复 0 | 重新独立抽样 10 个，再次列出候选（换一批） |
| /sim 缺省描述文本 | 回复“/sim <描述文本>” |
| /sim 描述 embedding 为零向量 | 回复“AI 服务暂时不可用，稍后重试” |
| /sim 空库或无结果 | 回复“没有找到匹配的表情包 🙁” |
| /addtag <公开ID> 的 ID 不存在 | 回复“未找到 ID 为 {公开ID} 的表情包” |
| /addtag 参数不足 | 回复“用法：/addtag <公开ID> <tag> [<tag>...]” |
| /addtag 在索引刷新中 | 回复“索引正在刷新，请稍后再试” |
| /addtag 等待确认超时 | 回复“标签添加已取消（超时）” |
| /addtag 用户回复非确认 | 回复“已取消” |
| /addtag 标签已存在 | 不重复添加，回复“本次新增：无”并列出全部标签 |
| /del <公开ID> 全部不存在 | 回复“未找到任何表情包” |
| /del 在索引刷新中 | 回复“索引正在刷新，请稍后再试” |
| /del 等待确认超时 | 回复“删除已取消（超时）” |
| /del 用户回复非确认 | 回复“已取消删除” |
| /del 移图失败（磁盘满/权限等） | 索引原样保留，该公开 ID 记为失败；文件仍在 memes/，可重试 |
| /info 总体信息索引刷新中 | 不等待锁，正常返回统计（状态显示“正在刷新索引”） |
| /info <公开ID> 等待读锁超时 | 回复"索引更新较慢，请稍后再试" |
| /info 硬件/进程内存/文件大小读取失败 | 对应字段显示"获取失败"/"文件不存在"，其他字段正常返回 |
| 当前为全部合集（`0`）时使用短号 | 回复“全部合集模式下请使用完整 ID，例如 1.3” |
| 公开 ID 格式错误 | 回复“表情包 ID 格式错误，请使用'合集编号.局部编号'，例如 1.3” |
| 旧版 `data/index.db`（无 `schema_version` 或版本号不是 2） | Bot 启动时拒绝启动，提示停止 Bot 并运行 `scripts.migrate_meme_collections upgrade-schema` |
| `/collection create` 名称为空、含内部空白/路径字符、以 `.` 开头或使用保留名 | 回复合集名称无效，不创建目录或数据库记录 |
| `/collection create` 名称已登记 | 回复已有合集名称和编号，不改变现有目录或当前合集 |
| `/collection create` 遇到同名普通目录 | 登记该目录并提示执行 `/refresh`；不扫描目录、不自动切换 |
| `/collection create` 遇到同名文件或符号链接 | 回复“无法创建合集：同名路径不是可用目录” |
| `/collection create` 新建目录后 SQLite 写入失败 | 校验目录身份后删除本次创建的空目录；已有目录不删除 |
| `/collection create` SQLite 失败且目录补偿失败 | 保留现场并记录高优先级日志，回复“合集创建失败，请检查日志后重试” |
| `/collection delete` 合集非空 | 回复“合集不为空，请先 /move 或 /del 清空后再删除”，不删除任何文件或 DB 记录 |
| `/collection delete` 目标合集不存在 | 回复“未找到表情包合集：{目标}\n发送 /switch 查看可用合集” |
| `/collection delete` 同名路径不是可用目录（文件/符号链接/身份异常） | 回复“无法删除合集：同名路径不是可用目录” |
| `/collection delete` rmdir 失败 | DB 未动，回复“合集删除失败，请检查日志后重试” |
| `/collection delete` rmdir 成功但 SQLite 删除失败 | 补偿 `mkdir()` 恢复空目录，记录 `logger.critical`，回复“合集删除失败，请检查日志后重试” |
| `/collection delete` 成功且 `reset_scope_count > 0` | 回复“合集已删除 ✅”并追加“已把 N 个聊天窗口的合集选择回退到全部合集” |
| `/collection rename` 旧合集不存在 | 回复“未找到表情包合集：{目标}\n发送 /switch 查看可用合集” |
| `/collection rename` 新名称非法 | 回复“合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称” |
| `/collection rename` 新名称已登记 | 回复“合集名称已存在：{name}（{id}）”，不动 DB 或目录 |
| `/collection rename` 目标路径已存在或旧目录身份异常 | 回复“无法重命名：目标名称对应路径不是可用目录” |
| `/collection rename` 目录 rename 失败 | 回滚 SQLite 到旧名（name + image_path 首段 + 缓存），回复“合集重命名失败，请检查日志后重试” |
| `/collection rename` 目录 rename 失败且 SQLite 回滚亦失败 | 记录 `logger.critical`，回复“合集重命名失败，请检查日志后重试” |
| `/collection delete`、`/collection rename` 群聊 @bot | 回复“此命令仅限私聊使用” |
| `/collection delete`、`/collection rename` 索引刷新中 | 回复“索引正在刷新，请稍后再试” |
| `/switch` 目标合集不存在 | 回复“未找到表情包合集：{目标}\n发送 /switch 查看可用合集” |
| `/switch` 当前有活跃会话 | 回复“已有命令在处理中，请先 /cancel” |
| `/move` 目标与源合集相同 | 回复“表情包已属于目标合集” |
| `/move` 目标合集已存在相同 OCR 文本 | 回复“目标合集已存在相同内容的表情包：{冲突公开ID}” |
| `/move` 补偿失败 | 回复“移动失败，索引将在下次刷新时检查一致性” |
| 手动把已索引文件跨目录移动后 `/refresh` | 旧路径在阶段1删除，新路径在阶段4作为新增处理；如需保留身份，应使用 `/move` |
| 删除合集目录后 `/refresh` | 阶段2删除该合集记录，并把引用它的所有 ChatScope 选择回退到 `0` |

---

## 6. 项目结构

```
meme-pilot/
├── docker-compose.yml         # 默认拉取 northhalf/meme-pilot:latest
├── docker-compose.build.yml   # 从当前源码构建本地镜像
├── docker-compose.build.override.yml.example # 本地构建代理覆盖示例
├── .env.example               # 环境变量模板
├── .env                       # 敏感配置（不提交 Git）
├── .gitignore
├── README.md
├── napcat/
│   └── config/                # NapCatQQ 配置挂载卷
├── memes/                     # 表情包图片目录；根目录存放全局图片，一级目录作为表情包合集
├── meme_no_text/             # OCR 无文字图片存放目录（不进索引，Docker 卷挂载）
├── data/                      # 索引数据目录
│   ├── index.db               # sqlite 元数据：meme、meme_tag、meme_collection、chat_collection_scope、schema_version
│   └── chroma/                # ChromaDB 向量库（collection memes，cosine；记录含 collection_id）
├── log/                       # 日志目录（不纳入版本控制，Docker 卷挂载）
│   ├── bot.log                # 当前日志文件（<= 10MB）
│   ├── bot.log.1              # 上一份日志备份
│   ├── bot.log.2              # 上二份日志备份
│   └── bot.log.3              # 上三份日志备份
├── scripts/
│   ├── convert_memes_to_webp.py   # 存量图片批量转 WebP + 更新 index.db 迁移脚本
│   ├── migrate_meme_collections.py # 合集 Schema 升级与根目录迁移脚本（upgrade-schema / move-root）
│   └── test_baidu_ocr.py          # 百度 OCR 单模式/七模式手动联网验证脚本
├── tests/                     # 测试目录规划
│   ├── unit/                  # 单元测试
│   │   ├── engine/            # engine 模块单元测试
│   │   └── plugins/           # 命令插件单元测试
│   ├── integration/           # 集成流程测试
│   └── fixtures/              # 测试样本和基准数据
│       ├── memes/
│       ├── data/
│       └── images/
└── bot/
    ├── Dockerfile
    ├── bot.py                 # NoneBot2 入口
    ├── config.py              # 配置读取
    ├── app_state.py           # 共享实例管理（模块级单例）
    ├── auth.py                # 授权校验模块（AUTHORIZED_USER_IDS 白名单）
    ├── session.py             # 共享会话管理（交互命令会话互斥与超时）
    ├── logging_config.py      # 日志滚动配置（RotatingFileHandler + StreamHandler）
    ├── plugins/
    │   ├── __init__.py
    │   ├── query.py        # /query 命令
    │   ├── rand.py         # /rand 命令
    │   ├── sim.py          # /sim 命令
    │   ├── collection.py   # /collection create/delete/rename 命令
    │   ├── add.py          # /add 命令
    │   ├── addtag.py       # /addtag 命令
    │   ├── delete.py       # /del 命令
    │   ├── edit.py         # /edittext 命令
    │   ├── setspeaker.py   # /setspeaker 命令
    │   ├── switch.py       # /switch 合集切换命令
    │   ├── move.py         # /move 跨合集移动命令
    │   ├── refresh.py      # /refresh 命令
    │   ├── info.py         # /info 命令
    │   ├── help.py         # /help 命令
    │   ├── cancel.py       # /cancel 命令
    │   ├── plain_text.py   # 兜底：普通文本/未知命令
    │   ├── _collection_utils.py # 合集与公开 ID 插件适配（共享模块）
    │   ├── _help_text.py        # 帮助文本常量（共享模块）
    │   └── _search_utils.py     # 搜索核心逻辑（共享模块）
    └── engine/
        ├── __init__.py
        ├── protocols.py         # 多模块共用 Protocol（EmbeddingProvider）
        ├── provider_factory.py  # OCR/Embedding provider 注册表与工厂函数
        ├── retry_config.py      # 统一 tenacity 网络请求重试配置
        ├── image_optimizer.py   # 图片压缩/转换（含 WebP 转换）
        ├── openai_ocr.py        # OpenAI 兼容 OCR 封装（原 deepseek_ocr.py）
        ├── baidu_ocr.py         # 百度智能云 OCR REST API（PP-OCRv6 + 六种传统接口）
        ├── paddle_ocr.py        # PaddleOCR 云 API 封装
        ├── rapidocr_ocr.py      # RapidOCR 本地 ONNX OCR 封装
        ├── openai_embedding.py  # OpenAI 兼容 Embedding 封装（原 embedding_service.py）
        ├── google_embedding.py  # Google Embedding API 封装
        ├── metadata_store.py    # sqlite3 元数据存储（MemeEntry + MetadataStore）
        ├── vector_store.py      # chromadb 向量存储（VectorHit + VectorStore）
        ├── collection_manager.py # 表情包合集、公开 ID 与 ChatScope 选择解析
        ├── index_manager.py     # 索引薄编排（MetadataStoreProtocol + VectorStoreProtocol 协议依赖两个 Store）
        ├── rwlock.py            # 读写锁（写者优先，IndexManager 持锁编排）
        ├── types.py             # 共享数据类型（SearchResult、MemePublicId 等）
        ├── utils.py             # 共享工具（vector_norm、resolve_unique_filename 等）
        ├── keyword_searcher.py  # 模糊搜索（MetadataStoreProvider 协议依赖）
        ├── random_searcher.py   # 随机取样搜索
        ├── semantic_searcher.py # 语义搜索（VectorStore 召回 + 相似度排序）
        └── combined_searcher.py # 组合检索（keyword + speaker + tag 过滤）
```

---

## 7. 依赖清单

### bot 容器

依赖由 `pyproject.toml` 管理，通过 `uv sync --no-dev` 安装：

```toml
[project]
dependencies = [
    "nonebot2[fastapi]>=2.5.0",
    "nonebot-adapter-onebot>=2.4.6",
    "pylcs>=0.1.1",              # 关键词模糊匹配（LCS 算法）
    "httpx>=0.28.1",
    "openai>=2.43.0",            # OpenAI 兼容 OCR 与 Embedding SDK
    "pillow>=12.2.0",            # 图片压缩/转换（含 WebP 转换）
    "pydantic>=2.13.4",
    "python-dotenv>=1.2.2",
    "jieba>=0.42.1",
    "chromadb>=1.5.9",           # 向量索引（HNSW cosine PersistentClient）
    "tenacity>=9.1.4",           # 统一网络请求重试
    "rapidocr>=3.9.1",           # 本地 ONNX OCR 引擎
    "paddleocr>=3.7.0",          # PaddleOCR 云 API 客户端
    "google-genai>=2.10.0",      # Google GenAI SDK（Google Embedding）
    "onnxruntime>=1.27.0",       # RapidOCR 本地推理依赖
]
```

### 系统依赖（Dockerfile 中安装）

- `g++` — C++ 编译器，`pylcs` 等包需要 C++11 编译

---

## 8. 部署步骤

### 8.1 准备

```bash
# 1. 克隆项目
git clone <repo-url> meme-pilot
cd meme-pilot

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 QQ_ACCOUNT、AUTHORIZED_USER_IDS，并按所选 provider 填写真实凭证
# Compose 始终校验 OPENAI_EMBEDDING_API_KEY 非空；google 模式可填 unused-for-google

# 3. 放入表情包
# 将你的 .jpg/.jpeg/.png/.gif/.webp/.bmp 放入 memes/ 目录

# 4. 启动
docker compose up -d
```

默认 `docker-compose.yml` 使用 `northhalf/meme-pilot:latest`，并在每次启动时检查发布镜像。

本地源码构建不使用代理时，使用独立入口：

```bash
docker compose -f docker-compose.build.yml up -d --build
```

需要构建代理时，按以下单一流程处理覆盖文件：旧版 `docker-compose.override.yml` 存在时先迁移；否则在新文件不存在时从示例创建。无论覆盖文件来自迁移还是示例，最后都显式组合两个 Compose 文件启动：

```bash
if [ -f docker-compose.override.yml ]; then
  mv docker-compose.override.yml docker-compose.build.override.yml
elif [ ! -f docker-compose.build.override.yml ]; then
  cp docker-compose.build.override.yml.example docker-compose.build.override.yml
fi

# 按需编辑 docker-compose.build.override.yml 中的代理地址
docker compose \
  -f docker-compose.build.yml \
  -f docker-compose.build.override.yml \
  up -d --build
```

迁移后不再保留 `docker-compose.override.yml`，避免默认 `docker compose up -d` 自动加载本地构建覆盖。

### 8.2 验证

```bash
# 查看日志
docker compose logs -f bot

# Bot 启动后会自动扫描 memes/ 建索引
# 完成后向你的 QQ 发送普通文本或 /query 测试
```

### 8.3 更新

```bash
# 添加新表情包到 memes/
# 然后 QQ 上发 /refresh
```

---

## 9. 后续可扩展

| 功能 | 说明 |
|------|------|
| 群聊支持 | 增加群聊命令白名单 |
| Web 管理界面 | 可视化上传/搜索/管理表情包 |
| 表情包推荐 | 基于使用频率的自动推荐 |
| 社区资源包 | 参考 MemeMeow 的社区共享机制 |
| 以图搜图 | 发一张图找到类似表情包 |
