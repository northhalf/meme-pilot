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
- 表情包图片始终本地存储；OCR 文本会按 `OCR_PROVIDER` 配置发送给对应服务，Embedding 由 `EMBEDDING_PROVIDER` 配置的服务生成，LLM 精排候选文本会发送给 DeepSeek
- sqlite3 元数据 + ChromaDB 向量索引，轻量可维护

### 1.3 目标用户

- 一个或多个授权用户。私聊支持所有命令；群聊中 @bot 支持 /search、/query、/rand、/sim、/info、/help 和普通文本搜索，/add、/addtag、/del、/refresh、/ai、/edittext、/setspeaker 仅限私聊。

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
│  ./memes/  → 表情包原文件                           │
│  ./data/   → index.db（sqlite）+ chroma/（向量库）   │
│  ./napcat/ → NapCat 配置                           │
└──────────────────────────────────────────────────┘
```

### 2.2 技术栈

| 层 | 技术 | 版本/说明 |
|----|------|-----------|
| QQ 协议端 | NapCatQQ | 最新 Docker 镜像，OneBot v11 |
| Bot 框架 | NoneBot2 | Python，异步 |
| Bot 适配器 | nonebot-adapter-onebot | 反向 WebSocket 连接，NapCat 主动连接 Bot |
| 元数据存储 | sqlite3 | Python 标准库，存 id/image_path/text/speaker + meme_tag 关联表 |
| 向量索引 | ChromaDB | PersistentClient，HNSW cosine collection（`memes`） |
| OCR 引擎 | RapidOCR（本地 ONNX，默认）/ PaddleOCR 云 API / OpenAI 兼容视觉 OCR | 视觉 OCR，返回去除所有空白后的文本 |
| 模糊搜索 | pylcs | C++ 最长公共子序列算法库 |
| 图片无损压缩 | 实现阶段选择具体工具或库 | 支持 .jpg/.jpeg/.png/.webp/.gif；.bmp 跳过压缩 |
| 大模型 API | DeepSeek | 兼容 OpenAI SDK |
| Embedding | OpenAI 兼容 Embedding（默认）/ Google Embedding API | 语义搜索；DeepSeek 不承担 embedding 生成 |
| 依赖解耦 | `typing.Protocol` | engine 模块按消费者最小接口定义协议（`EmbeddingProvider`/`MetadataEntryProvider`/`VectorQueryProvider`/`MetadataStoreProvider`/`MetadataStoreProtocol`/`VectorStoreProtocol`/`ImageOptimizerProtocol`/`RerankProvider`/`OcrProvider`），单模块用放模块内、多模块共用放 `protocols.py`，便于测试用 mock 替换 |
| 容器编排 | Docker Compose | 2 容器 |

---

## 3. 功能需求

### 3.1 功能一：关键词搜索

#### 触发方式

用户在私聊中发送命令：`/search <关键词>`（短命令 `/s`）

#### 流程

```
用户: /search 加班
        │
        ▼
Bot 接收 → 调用 KeywordSearcher.search("加班")
        │
        ├── 使用「原始输入去所有空白、保留助词」的关键词做精确子串匹配
        │    ├── 命中 → 返回包含该子串的全部表情包（多结果每页 10 条分页）
        │    └── 未命中 → 回退到 jieba 去助词 + pylcs LCS 模糊匹配（>= 60）
        │
        ├── （回退路径）使用 jieba.posseg 对关键词做分词 + 词性标注，过滤助词（的、了、吗、呢、吧等）
        │    └── 去助词后为空 → "没有匹配到任何表情包 🙁"
        │    └── 去助词后用 pylcs LCS 对 sqlite 中的 OCR 文本做模糊匹配
        │    ├── 关键词是 OCR 文本的连续子串 → similarity = 100（精确命中）
        │    ├── 关键词与 OCR 文本部分重叠 → 按 LCS 长度与关键词长度的比值计算 similarity
        │    └── 过滤保留 similarity >= 60 的结果，按分数降序排列，返回全量匹配（多结果每页 10 条分页）
        │    └── 如果存在 similarity = 100 的结果，只返回 similarity = 100 的结果
        │
        ▼
        ├── 无结果 → "没有匹配到任何表情包 🙁"
        │
        ├── 唯一结果 → 直接发送对应表情包图片
        │
        └── 多个结果 (设为 N 条，每页 10 条)
              └── "找到多个匹配的表情包，请选择：\n"
                  "1. 当你的老板说今天要加班 -- 12, 无, 100%\n"
                  "2. 加班到凌晨三点的我 -- 23, 小明, 吐槽, 加班, 100%\n"
                  "3. 周日晚上的加班通知 -- 45, 无, 通知, 加班, 100%\n"
                  "回复编号即可 (1-{N})"  (N 为当前页条数)
                  "回复 n 看下一页"  (仅当存在下一页)
                      │
                      ▼
              用户回复编号 (如 "2") 或 "n" 翻下一页
                      │
                      ▼
              Bot 查 sqlite 中对应 image_path → 发送匹配图片，再发送文本消息 "<id>, <speaker>, <tags...>"
```

#### 交互约束

- 关键词先做精确子串匹配（用去除所有空白、保留助词的原始输入）；命中则只返回包含该子串的结果，否则回退到 jieba 去助词后的 LCS 模糊匹配。
- 等待用户选择时设置超时（默认 60 秒，由 `SESSION_EXPIRE_TIMEOUT` 控制），超时回复”选择已过期，请重新搜索”
- 选择超时后清理本次候选状态；用户迟到回复不再视为本次搜索选择
- 同一授权用户同一时间只保留一个待处理会话；如果用户在选择前发起新的 `/search` 或 `/add`，已有活跃会话则拒绝新命令，并提示”已有命令在处理中，请先 /cancel”
- 等待选择期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本（不影响等待状态）
- 多结果列表显示临时选择序号、OCR 文本、元数据 "id, speaker, tags" 以及关键词相似度百分比（score 量纲，0–100）；speaker 缺失时显示 "无"，tags 为空时省略 tags 段；用户回复的是临时选择序号
- 用户输入无效编号时回复"无效编号，请回复 1-{N} 之间的数字"
- 搜索返回全量匹配结果，多结果按每页 10 条分页展示；回复 `n` 查看下一页，末页回复 `n` 提示"没有更多结果了"并保持当前页；翻页重置选择超时（`SESSION_EXPIRE_TIMEOUT`）

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
IndexManager.search_combined（持读锁）
        ├── CombinedSearcher: 过滤 speaker∈["小明"](OR) AND "吐槽"∈tags(AND)
        ├── keyword 非空 -> KeywordSearcher.search_in(子集, "加班")
        └── 返回带 similarity 的结果
        │
        ▼
dispatch_search_results（复用 /search 的空/单/多结果分支）
        ├── 无结果 -> "没有匹配到任何表情包 🙁"
        ├── 单结果 -> 发图 + 元数据行
        └── 多结果 -> 每页 10 条分页，回复 n 翻页
```

#### 交互约束

- 有关键词时列表行展示关键词相似度百分比（score 0–100，同 `/search`）；无关键词纯过滤时不展示相似度（同 `/rand`）。
- 排序：无关键词时结果随机排序；有关键词时按相似度降序分组，同相似度组内随机排序（一次 `/query` 洗牌一次，翻页顺序稳定）。
- speaker 精确相等、区分大小写；tags 精确匹配、区分大小写、多个为 AND；多 speaker 为 OR。
- `#`/`@` 单独成 token（前缀后为空）忽略；三者皆空时回复用法提示。
- 权限属组 B（私聊 + 群聊 @bot）；与 `/search`、`/ai`、`/add` 等共用会话互斥与读锁。

### 3.3 功能二：AI 描述匹配

#### 触发方式

用户在私聊中发送命令：`/ai <自然语言描述>`

#### 流程

```
用户: /ai 给我一张表达心累的加班表情包
        │
        ▼
Bot 接收 → 调用 AIMatcher.match("加班心累")
        │
        ├── 阶段一：Embedding 语义搜索
        │   ├ 将用户描述向量化（`EMBEDDING_PROVIDER` 配置的 Embedding 服务）
        │   ├ 用 ChromaDB collection.query 从向量库召回 Top-N
        │   └ 不设最低相似度阈值，按相似度取 Top 10 候选
        │
        ├── 阶段二：DeepSeek LLM 精排
        │   ├ Prompt:
        │   │   "你是一个表情包匹配助手。用户描述：{描述}
        │   │    以下是候选表情包的文字内容：
        │   │    {Top 10 列表}
        │   │    请选出最匹配的 1 个，返回序号即可。"
        │   ├ Top 10 候选只发送 id 和 OCR 文本，不发送文件名
        │   └ 返回最匹配的序号
        │
        ├── 发送对应表情包图片
        └── 再发送文本消息 "<id>, <speaker>, <tags...>"（speaker 缺失显示"无"，空 tags 省略）
```

#### 交互约束

- 收到有效 `/ai` 请求后，先回复"正在根据你的描述搜索表情包，请稍候..."
- 直接返回唯一结果（无需用户选择）
- 如果 embedding 阶段没有候选，回复"没有找到匹配的表情包 🙁"
- 如果 DeepSeek LLM 精排调用失败、输出解析失败，或明确返回 `0`，都 fallback 到 embedding Top 1

### 3.4 功能三：聊天添加表情包

#### 触发方式

授权用户在私聊中发送命令：`/add [speaker <tags...>]`（短命令 `/a`）

`speaker` 为可选说话人，不写入 OCR 文本；`tags` 为可选标记词列表。`/add` 后的参数按空白切分，第一个词作为 `speaker`，剩余词作为 `tags`。不填参数时 `speaker` 为空，`tags` 为空列表。文件名始终由 Bot 自动生成。

#### 流程

```
授权用户: /add 小明 吐槽 加班
        │
        ▼
Bot 回复: "请发送图片，{SESSION_EXPIRE_TIMEOUT} 秒内有效"
        │
        ▼
授权用户发送一张图片
        │
        ▼
Bot 下载图片到 ./memes/
        │
        ├── 文件名按 `meme_<YYYYMMDDHHMMSS>_<hash8>` 规则自动生成
        ├── 对 .jpg/.jpeg/.png/.webp/.gif 执行无损压缩，成功后覆盖原文件
        ├── .bmp 不压缩，直接继续处理
        ├── 对图片执行 OCR
        ├── 调用配置的 Embedding 服务生成 embedding
        ├── 使用临时文件替换策略更新 index.db + chroma 向量库（先 sqlite 后 chroma，upsert 失败回滚 sqlite）
        │
        ▼
Bot 回复:
新增表情包✅，id：42，识别到的文字为：
「加班心累时的表情包」
42, 小明, 吐槽, 加班
```

#### 交互约束

- `/add` 与 `/help`、`/search`、`/ai`、`/edittext`、`/setspeaker`、`/refresh`、`/cancel` 使用同一组 `AUTHORIZED_USER_IDS` 白名单。
- 自动命名规则：生成 `meme_<YYYYMMDDHHMMSS>_<hash8>`；其中时间取 Bot 接收图片消息的本地时间，`hash8` 取图片内容 SHA-256 的前 8 位。
- `/add` 一次只支持添加一张图片；如果用户发送多张图片，v1.0 只处理第一张。
- `/add` 保存新增图片后，对 `.jpg/.jpeg/.png/.webp/.gif` 尝试无损压缩；压缩成功后直接覆盖 `memes/` 中的原图片文件。
- `.bmp` 图片不执行压缩，直接继续 OCR 和 embedding 流程。
- 不支持的图片扩展名不作为表情包处理。
- `/add` 中图片压缩失败时，删除刚下载的图片，不写入索引，并回复添加失败原因。
- Bot 提示”请发送图片”后等待超时（默认 60 秒，由 `SESSION_EXPIRE_TIMEOUT` 控制）；超时回复”发送图片超时，请重新 /add”。
- 同一授权用户同一时间只保留一个待处理会话；如果等待图片期间用户再次发送 `/add` 或 `/search`，已有活跃会话则拒绝新命令，并提示”已有命令在处理中，请先 /cancel”。用户也可通过 `/cancel` 手动取消当前添加，通过 `/help` 旁路查看帮助文本（不影响等待状态）。
- 文件扩展名优先使用消息或下载文件中的原始扩展名；如果缺失，则根据下载响应 `Content-Type` 推断；仍无法推断时拒绝添加。
- 添加过程中如果 OCR 或 embedding 失败，删除刚下载的图片，不写入索引，并回复添加失败原因。
- `/add` 写入前按 OCR 文本去重：以「去除所有空白字符后的文本」为去重键，若命中已有表情包，则将旧图片文件移动到 `memes/` 同级的 `memes_replaced/` 目录归档，并用新图替换（复用旧索引 ID，覆盖 `image_path` 与 embedding）；该机制默认认为去重键相同即为同一表情包，不额外校验图片内容。
- `/add` 若 OCR 结果去除所有空白后为空（无文字图片），则将该图片移动到 `memes/` 同级的 `meme_no_text/` 目录（不进索引），并回复"未识别到文字，已移至 meme_no_text/"。
- `/add` 与 `/refresh` 共用全局索引更新锁；锁占用期间触发 `/add` 时回复“索引正在刷新，请稍后再试”。

### 3.5 功能四：帮助命令

#### 触发方式

授权用户在私聊中发送命令：`/help`（短命令 `/h`）

授权用户在私聊中发送不以 `/` 开头的普通文本时，Bot 等同执行 `/search`。

#### 流程

```text
授权用户: /help
        │
        ▼
Bot 回复当前可用命令和简单用法：
/help (/h)：查看命令帮助
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足）
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>] (/a)：通过聊天添加一张表情包
/addtag <id> <tag>... (/at)：为指定表情包添加标签
/del <id>... (/d)：删除指定表情包（需确认）
/edittext <id> <新文本> (/e)：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人] (/sp)：设置或清空表情包的说话人
/refresh (/r)：扫描 memes/ 并增量更新索引
/info：查看机器人状态与统计信息
/cancel (/c)：取消当前正在执行的命令
```

#### 交互约束

- `/help` 与 `/search`、`/ai`、`/add`、`/edittext`、`/setspeaker`、`/refresh`、`/cancel` 使用同一组 `AUTHORIZED_USER_IDS` 白名单。
- 非授权用户私聊发送 `/help` 或普通文本时静默忽略，仅记录日志。
- 非授权用户在群聊中 @bot 发送任何消息时静默忽略，仅记录日志。
- 授权用户在群聊中 @bot 发送 `/help` 或普通文本时可正常触发命令。
- 授权用户在群聊中 @bot 发送 `/add`、`/addtag`、`/del`、`/ai`、`/refresh`、`/edittext`、`/setspeaker` 时回复”此命令仅限私聊使用”。
- 授权用户私聊发送未知斜杠命令时，回复”未知命令”并附帮助摘要。
- 授权用户群聊中 @bot 发送未知斜杠命令时，回复”未知命令”并附帮助摘要。
- 授权用户私聊发送已知命令但缺少必要参数时，回复该命令的用法提示，不直接执行完整 `/help`。

### 3.6 辅助功能：索引管理

#### 索引初始化与启动同步

Bot 启动时自动扫描 `./memes/` 目录，并在后台执行与 `/refresh` 相同的”按文件名同步”策略。索引同步在后台进行，Bot 启动后立即可用（用已有索引响应命令）；同步期间搜索命令会提示”索引更新较慢，请稍后再试”；启动期间通过日志输出进度。
1. 如果 `data/index.db` 不存在或为空，对全部图片执行 OCR + embed，写入 sqlite 与 chroma
2. 如果已有索引数据，自动处理新增图片和已删除图片
3. 新增图片先按格式执行无损压缩：`.jpg/.jpeg/.png/.webp/.gif` 尝试压缩并在成功后覆盖原文件；`.bmp` 不压缩；不支持的扩展名不作为表情包处理
4. 压缩成功或无需压缩后，对新增图片自动 OCR，并生成对应 embedding
5. 已删除图片对应记录会从 sqlite 与 chroma 中删除（先 sqlite 后 chroma）
6. 同步执行四阶段：阶段0 跨库一致性修复（对齐 sqlite ↔ chroma 的 id 集合）+ 阶段1 删除 + 阶段2 新增；chroma 损坏/为空且 sqlite 有数据时，阶段0 自动全量重 embed 并 `rebuild_all`

#### 增量更新

授权用户发送 `/refresh`（短命令 `/r`），执行与启动同步相同的“按文件名同步的增量刷新”：
1. 扫描 `./memes/` 并读取现有 sqlite 索引
2. 对新增图片先按格式执行无损压缩：`.jpg/.jpeg/.png/.webp/.gif` 尝试压缩并在成功后覆盖原文件；`.bmp` 不压缩；不支持的扩展名不作为表情包处理
3. 压缩成功或无需压缩后，对新增图片执行 OCR，生成新的 sqlite 条目
4. 对新增图片生成 embedding，写入 chroma 向量库
5. 对已经从 `./memes/` 删除的图片，从 sqlite、chroma 中删除对应记录；启动时也执行同样的删除清理（先 sqlite 后 chroma）
6. 删除记录后保持其他已有 id 稳定，不重新编号，允许 `1`、`3` 这种临时编号空洞
7. 多个新增图片按文件名升序处理；每张新增图片优先复用最小空洞 id，如果没有空洞，则使用当前最大 id + 1
8. 因为空洞 id 可被未来新增图片复用，v1.0 中 id 只表示当前索引内编号，不承诺作为永久图片身份
9. 对文件名仍存在的图片不重新 OCR，不重新生成 embedding
10. 新增图片压缩失败或 OCR 调用异常时跳过该图片，不写入索引；刷新继续处理其他图片，最终回复中汇总失败文件列表
11. 新增图片 OCR 成功但 embedding 生成失败时，该图片不写入 sqlite、chroma；刷新继续处理其他图片，最终回复中汇总失败文件列表
12. `/refresh` 完成后回复摘要：新增数量、删除数量、去重数量、无文字移走数量、失败数量；如有失败，最多列出前 10 个失败文件名
13. 新增图片 OCR 后按「去除所有空白字符后的文本」去重键判定：若与已有条目或其他新增图片去重键相同，则保留已有条目或文件名升序靠前的新图，将被判定为重复的新图文件移动到 `memes/` 同级的 `memes_replaced/` 目录归档，不写入索引；该去重在 `/refresh` 回复中以「去重数量」单独统计，不计入新增或删除。
14. 新增图片 OCR 结果去除所有空白后为空（无文字图片）时，移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引；sqlite 中本功能上线前已存在的「未识别到文字」占位条目不清理（sync 不重新 OCR 已有条目）。

v1.0 不检测同名覆盖：如果用户用新图片覆盖了旧图片但文件名不变，`/refresh` 不会重新 OCR，该限制需要在使用说明中明确。

权限约束：`/help`、`/search`、`/query`、`/rand`、`/sim`、`/ai`、`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/refresh`、`/info`、`/cancel` 使用同一组 `AUTHORIZED_USER_IDS` 白名单；非授权用户的私聊/群聊消息不触发任何业务命令，并静默忽略（仅记录日志，不回复提示）。群聊行为按命令分组：
- 组 A（仅私聊）：`/add`、`/addtag`、`/del`、`/ai`、`/refresh`、`/edittext`、`/setspeaker` — 授权用户群聊中 @bot 调用时回复"此命令仅限私聊使用"
- 组 B（私聊 + 群聊@）：`/search`、`/query`、`/rand`、`/sim`、`/info`、`/help`、普通文本 — 授权用户群聊中 @bot 时可正常触发
- 组 C（私聊 + 群聊@）：`/cancel` — 授权用户私聊或群聊中 @bot 均可正常触发

并发约束：`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker` 与 `/refresh` 使用同一个全局索引更新锁（写锁），同一时间只允许一个索引写入任务运行；如果索引更新任务正在执行，后续授权用户触发 `/refresh`、`/add`、`/addtag`、`/del`、`/edittext` 或 `/setspeaker` 时，会触发 `RefreshInProgressError` 并回复"索引正在刷新，请稍后再试"。`/search`、`/query`、`/rand`、`/sim` 与 `/ai` 通过读锁访问索引，写锁占用期间等待超时后回复"索引更新较慢，请稍后再试"。`/info` 与 `/help` 不等待索引锁。`/cancel` 和 `/help` 在有活跃会话时可旁路触发：`/help` 回复帮助文本后等待继续，`/cancel` 取消当前会话。

写入约束：索引更新统一「先 sqlite 后 chroma」的写入顺序。新增/替换条目时先写 sqlite，再 `VectorStore.upsert` 写 chroma；若 chroma upsert 失败，回滚 sqlite 写入（删除刚写入的行或恢复旧 `image_path`），保证两库一致。`/edittext` 修改文本时同步更新 sqlite 与 chroma（重新 embed）。`/setspeaker` 仅修改 sqlite `speaker` 字段，不操作 chroma。`/addtag` 仅追加 sqlite `meme_tag`，不操作 chroma。`/del` 先将图片归档到 `memes_deleted/`，再先 sqlite 后 chroma 删除索引；移图失败时索引原样保留（仅记失败），避免文件残留 `memes/` 在下次 `/refresh` 被重新入库（已删表情包复活）。同步阶段0 检测到 chroma 为空且 sqlite 有数据时，自动全量重 embed 并 `rebuild_all`；检测到 sqlite 有而 chroma 无的 id 时补 embed `upsert`；检测到 chroma 有而 sqlite 无的 id 时删孤儿向量。

#### 索引文件格式

**`data/index.db`（sqlite3 元数据库）**：

```sql
CREATE TABLE meme (
    id INTEGER PRIMARY KEY,
    image_path TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT
);
CREATE UNIQUE INDEX idx_meme_image_path ON meme(image_path);
CREATE UNIQUE INDEX idx_meme_text ON meme(text);

CREATE TABLE meme_tag (
    meme_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (meme_id, tag),
    FOREIGN KEY (meme_id) REFERENCES meme(id) ON DELETE CASCADE
);
CREATE INDEX idx_meme_tag_tag ON meme_tag(tag);
```

`meme` 表以 `id` 为主键（`INTEGER PRIMARY KEY`，手动分配最小空洞 id，不用 `AUTOINCREMENT`），`image_path` 为 `memes/` 下相对路径（扁平结构下即文件名），`text` 为 OCR 去除所有空白后的文本，`speaker` 为说话人（v1.0 可通过 `/setspeaker` 设置，允许 `NULL`）。`meme_tag` 关联表存多值标记词，`ON DELETE CASCADE` 随 `meme` 行删除。`PRAGMA foreign_keys = ON`。`text` 与 `image_path` 均加 `UNIQUE INDEX` 约束；`IndexManager` 仍通过 `get_id_by_text` 在写入前去重，DB 层 UNIQUE 作为兜底，冲突抛 `DuplicateEntryError`。

**`data/chroma/`（ChromaDB 向量库）**：

ChromaDB `PersistentClient` 数据目录，包含一个 collection（默认名 `memes`，HNSW `cosine` 距离）。每条向量仅存 `id`（内部转 `str`，与 sqlite `meme.id` 一一对应）+ `embedding`（1024 维 float32）。`similarity = 1 - distance`。向量库由系统自动维护，不建议手动编辑。

---

### 3.7 功能五：OCR 文本编辑

#### 触发方式

授权用户在私聊中发送命令：`/edittext <id> <新文本>`（短命令 `/e`）

#### 流程

```text
授权用户: /edittext 42 新的OCR文字
        │
        ▼
Bot 校验 id 存在 → 发送对应表情包图片 → 发送确认消息
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
- `<id>` 必须为数字；不存在时回复"未找到 id 为 {id} 的表情包"。
- 修改前 Bot 发送图片和确认消息，用户回复"确认"后执行修改。
- 新文本与已有条目 OCR 文本冲突时，回复"该文本已被其他表情包使用"（`DuplicateTextError`）。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"修改已取消（超时）"；用户回复非"确认"时回复"已取消修改"。
- 同一授权用户同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.8 功能六：说话人设置

#### 触发方式

授权用户在私聊中发送命令：`/setspeaker <id> [说话人]`（短命令 `/sp`）

`[说话人]` 为可选参数；缺省时清空该条目的 `speaker` 字段。

#### 流程

```text
授权用户: /setspeaker 42 小明
        │
        ▼
Bot 校验 id 存在 → 发送对应表情包图片 → 发送确认消息（旧说话人 → 新说话人）
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
- `<id>` 必须为数字；不存在时回复"未找到 id 为 {id} 的表情包"。
- 修改前 Bot 发送图片和确认消息，用户回复"确认"、"yes"或"y"后执行修改。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"说话人设置已取消（超时）"；用户回复非确认内容时回复"已取消"。
- 同一授权用户同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.9 功能七：随机选择

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/rand [关键词]`

`[关键词]` 为可选参数；有关键词时先按关键词搜索，再在命中结果中随机取 10 个；无关键词时全库随机取 10 个。

#### 流程

```text
授权用户: /rand 加班
        │
        ▼
Bot 在关键词命中结果中随机取 10 个，列出候选：
    1. 加班到凌晨三点的我 -- 23, 小明
    ...
    10. 周日晚上的加班通知 -- 45, 无
    回复编号即可 (1-10)
    回复 0 换一批
        │
        ▼
授权用户: 0  -> Bot 重新独立抽样 10 个，再次列出候选（换一批）
授权用户: 2  -> Bot 发送对应表情包，并附元数据行（id, speaker, tags）
```

#### 交互约束

- 私聊与群聊 @bot 均可触发（组 B）。
- 通过读锁访问索引；写锁占用期间等待超时后回复"索引更新较慢，请稍后再试"。
- 空库时无关键词回复"表情包目录为空，请先添加图片并执行 /refresh"，有关键词但无命中回复"没有匹配到任何表情包 🙁"。
- 候选每页 10 条；回复 `0` 换一批（重新独立抽样），回复编号发送对应表情包。
- 同一授权用户同一时间只保留一个待处理会话；等待选择期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。
- 等待选择期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"选择已过期，请重新搜索"。

---

### 3.10 功能八：语义选择

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/sim <描述文本>`

`<描述文本>` 为必填的自然语言描述；Bot 对其生成 embedding 后做全库语义搜索召回（`limit=None` 全库召回，按相似度降序），不调用 LLM 精排。

#### 流程

```text
授权用户: /sim 一张表达心累的加班表情包
        │
        ▼
Bot 对描述生成 embedding（锁外），全库语义搜索召回并按相似度降序列出：
    1. 加班到凌晨三点的我 -- 23, 小明, 吐槽, 加班, 82%
    2. 心累的打工人 -- 45, 无, 76%
    回复编号即可 (1-2)
    回复 n 看下一页
        │
        ▼
授权用户: 1
        │
        ▼
Bot 发送对应表情包，并附元数据行（id, speaker, tags）
```

#### 交互约束

- 私聊与群聊 @bot 均可触发（组 B）。
- embedding 生成在锁外进行，语义搜索持读锁；写锁占用期间等待超时后回复"索引更新较慢，请稍后再试"。
- `<描述文本>` 缺省时回复"/sim <描述文本>"。
- 列表行展示语义相似度百分比（`similarity = 1 - distance`，按比例换算）；多结果每页 10 条，回复 `n` 看下一页。
- 用户描述 embedding 为零向量时回复"AI 服务暂时不可用，稍后重试"。
- 空库或无结果时回复"没有找到匹配的表情包 🙁"。
- 同一授权用户同一时间只保留一个待处理会话；等待选择期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.11 功能九：标签添加

#### 触发方式

授权用户在私聊中发送命令：`/addtag <id> <tag> [<tag>...]`（短命令 `/at`）

第一个参数为 `entry_id`，其后为待追加的标签列表（按空白切分，过滤空串）。

#### 流程

```text
授权用户: /addtag 42 心累 深夜
        │
        ▼
Bot 校验 id 存在 -> 发送确认消息（当前 OCR 文本、当前标签、新增标签）
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
- `<id>` 必须为数字；不存在时回复"未找到 id 为 {id} 的表情包"。
- 至少需要 `<id>` 与一个 `<tag>`；参数不足回复"用法：/addtag <entry_id> <tag> [<tag>...]"。
- 修改前 Bot 发送确认消息（纯文本，不发送图片），用户回复"确认"、"yes"或"y"后执行追加。
- 仅追加 sqlite `meme_tag`，不操作 chroma；已存在的标签不重复添加，回复中"本次新增"为实际新增项（可能为"无"）。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"标签添加已取消（超时）"；用户回复非确认内容时回复"已取消"。
- 同一授权用户同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.12 功能十：删除表情包

#### 触发方式

授权用户在私聊中发送命令：`/del <id>...`（短命令 `/d`）

支持一次删除多个表情包，id 间以空格分隔；会自动去重并保持顺序。

#### 流程

```text
授权用户: /del 12 42
        │
        ▼
Bot 校验 id，发送待删除条目摘要（id + 截断后的 OCR 文本），未找到的 id 一并提示
        │
        ▼
授权用户: 确认  或  yes/y
        │
        ▼
Bot 逐条删除：先将图片归档到 memes_deleted/，再先 sqlite 后 chroma 删除索引
        │
        ▼
Bot 回复: 删除结果如下:
         成功：12、42
         （未找到 / 失败的 id 分别列出）
```

#### 交互约束

- 仅限私聊；群聊 @bot 调用时回复"此命令仅限私聊使用"。
- `<id>` 必须为数字；全部不存在时回复"未找到任何表情包"。
- 修改前 Bot 发送摘要确认消息，用户回复"确认"、"yes"或"y"后执行删除。
- 删除顺序：先将图片归档到 `memes_deleted/`（同名冲突追加序号），再先 sqlite 后 chroma 删除索引。移图失败时索引原样保留，仅将该 id 记为失败，避免文件残留 `memes/` 在下次 `/refresh` 被重新入库（已删表情包复活）；用户可重试。
- 文件本就不在 `memes/` 时跳过移动，直接删除索引。
- 索引刷新期间调用时回复"索引正在刷新，请稍后再试"。
- 等待确认期间受 `SESSION_EXPIRE_TIMEOUT` 控制，超时回复"删除已取消（超时）"；用户回复非确认内容时回复"已取消删除"。
- 同一授权用户同一时间只保留一个待处理会话；等待确认期间可通过 `/cancel` 取消，`/help` 旁路查看帮助文本。

---

### 3.13 功能十一：状态信息

#### 触发方式

授权用户在私聊或群聊中 @bot 发送命令：`/info`

无参数。返回索引统计、当前状态以及本机内存/CPU 占用。

#### 流程

```text
授权用户: /info
        │
        ▼
Bot 读取索引统计 + 本机硬件信息，组装回复：
    表情包数量：128
    排行（前 10）：
      1. 小明 45
      2. 无 32
      3. 小红 28
    当前机器人状态：空闲
    内存占用：512 MB / 2048 MB (25%)
    CPU占用：12%
```

#### 交互约束

- 私聊与群聊 @bot 均可触发（组 B）。
- 不等待索引锁；即使索引正在刷新也能返回统计。
- 回复包含：表情包数量、speaker 使用频率排行（前 10，`speaker` 为空显示"无"）、当前状态、内存占用、CPU 占用。
- 当前状态取值：`空闲`、`正在处理命令`（引擎空闲但有活跃命令会话时由插件层覆写）、`正在刷新索引`（refresh 进行中）。
- 硬件信息读取失败时对应字段显示"获取失败"，不影响其他字段。
- 索引信息获取异常时回复"索引信息获取失败，请稍后重试"。

---

## 4. 非功能需求

### 4.1 性能

| 指标 | 要求 |
|------|------|
| OCR 首次建索引 | 100 张图 < 10 分钟（使用 `OCR_PROVIDER=deepseek` 等云端 OCR 时约 3s/张；使用 `rapidocr` 本地推理时受 CPU 性能影响） |
| 关键词搜索 | < 1 秒（pylcs LCS 对几千行 < 50ms） |
| AI 匹配 | < 5 秒（embedding + LLM API 网络延迟） |
| 图片发送 | NapCat 发送延迟 < 2 秒 |

### 4.2 部署

- Docker Compose 一键部署
- 支持 x86_64 Linux 服务器
- Bot 端口 `BOT_PORT` 仅供 Docker 网络内 NapCat 反向 WebSocket 连接，不映射到宿主机
- 最低配置：1 核 CPU / 2GB RAM / 20GB 磁盘

### 4.3 安全

- 表情包图片仅存储在本地；OCR 文本会发送给 `OCR_PROVIDER` 配置的 OCR 服务，Embedding 由 `EMBEDDING_PROVIDER` 配置的服务生成，Top 10 候选文本会发送给 DeepSeek 做 LLM 精排
- .env 文件管理敏感配置（QQ 账号 / 授权用户列表 / DeepSeek API Key / 各 provider 对应 API Key）
- 授权用户列表通过 `AUTHORIZED_USER_IDS` 配置，多个 QQ 号用英文逗号分隔
- 通用必填环境变量：`QQ_ACCOUNT`、`AUTHORIZED_USER_IDS`、`DEEPSEEK_API_KEY`
- 按 provider 必填：
  - `EMBEDDING_PROVIDER=openai`（默认）时必填 `OPENAI_EMBEDDING_API_KEY`
  - `EMBEDDING_PROVIDER=google` 时必填 `GOOGLE_API_KEY`
  - `OCR_PROVIDER=paddle` 时必填 `PADDLEOCR_ACCESS_TOKEN`
  - `OCR_PROVIDER=deepseek` 时必填 `OPENAI_OCR_API_KEY`
  - `OCR_PROVIDER=rapidocr`（默认）无需 API Key
- 可选环境变量：`BOT_HOST`、`BOT_PORT`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`OPENAI_EMBEDDING_BASE_URL`、`OPENAI_EMBEDDING_MODEL`、`EMBEDDING_PROVIDER`、`GOOGLE_EMBEDDING_MODEL`、`GOOGLE_BASE_URL`、`OPENAI_OCR_BASE_URL`、`OPENAI_OCR_MODEL`、`OCR_PROVIDER`、`OCR_TEXT_SCORE`、`EMBEDDING_CONCURRENCY`（Embedding 并发上限，默认 5）、`OCR_CONCURRENCY`（OCR 并发上限，默认 5）、`RERANK_CONCURRENCY`（LLM 精排并发上限，默认 5）、`COMPRESS_CONCURRENCY`（图片压缩并发上限，默认 5）、`SESSION_EXPIRE_TIMEOUT`（会话超时，默认 60 秒）
- .env 不纳入版本控制

### 4.4 维护

- `data/index.db` 为 sqlite 数据库，可用 `sqlite3` CLI 查看（`sqlite3 data/index.db "SELECT * FROM meme;"`）；`data/chroma/` 由 ChromaDB 管理，不建议手动编辑
- 支持通过 `/add` 在 QQ 私聊中添加单张表情包
- 支持手动向 memes/ 目录添加图片后 `/refresh` 更新
- 新增图片无损压缩成功后会直接覆盖 `memes/` 中的原图片文件
- 日志通过 `logging_config.py` 中的 `setup_logging()` 统一配置，同时输出到 stdout（`docker compose logs` 可查看）和文件 `log/bot.log`
- 文件日志采用滚动机制：`bot.log` 为当前文件，`bot.log.1` 为上一份备份；单个文件上限 1 MB，由 Python 标准库 `RotatingFileHandler` 管理
- stdout 日志级别为 INFO，文件日志级别为 DEBUG
- `log/` 目录通过 Docker 卷 `./log:/app/log` 挂载到宿主机，`log/` 不纳入版本控制

---

## 5. 边界情况

| 场景 | 预期行为 |
|------|---------|
| memes/ 目录为空 | Bot 正常启动并在日志中 warning；`/search`、`/ai`、`/refresh` 回复"表情包目录为空，请先添加图片并执行 /refresh" |
| 图片 OCR 成功但识别不到文字 | 移动到 `memes/` 同级的 `meme_no_text/` 目录，不进入索引，日志 warning |
| 新增图片 OCR 文本去重键命中已有条目或另一新增图片 | `/add` 用新图替换旧图（旧图移动到 `memes_replaced/` 归档、复用旧 ID）；`/refresh` 保留已有/靠前者，重复新图移动到 `memes_replaced/` 归档 |
| 单张新增图片 OCR 调用异常 | 跳过该图片，不写入索引；刷新继续处理其他图片，最终回复汇总失败文件列表 |
| OpenAI 兼容 OCR API 调用失败 | Bot 打印错误日志，回复"OCR 服务不可用"；本次刷新不更新索引文件 |
| Embedding API 网络异常 | 刷新新增图片时，受影响图片不写入索引；`/ai` 生成用户描述 embedding 失败时回复"AI 服务暂时不可用，稍后重试" |
| DeepSeek API 网络异常 | `/ai` 精排失败时 fallback 到 embedding Top 1；如果没有 embedding 候选，则回复"AI 服务暂时不可用，稍后重试" |
| 授权用户私聊发送普通文本 | 等同执行 `/search`，按关键词搜索表情包 |
| 授权用户私聊发送未知斜杠命令 | 回复”未知命令”并附帮助摘要 |
| 授权用户群聊中 @bot 发送未知斜杠命令 | 回复”未知命令”并附帮助摘要 |
| 无活跃会话时发送 /cancel | 回复”当前没有活跃的会话” |
| /add 等待图片时发送 /cancel | 取消添加流程，清理会话，回复”已取消 ✅” |
| /search 等待选择时发送 /cancel | 取消搜索选择流程，清理选择会话，回复”已取消 ✅” |
| 异频道 /cancel（私聊发起取消群聊中的会话） | 支持：`execute_cancel` 按 user_id 查找并跨 task 取消 |
| 授权用户群聊 @bot 发送 /add、/addtag、/del、/ai、/refresh、/edittext、/setspeaker | 回复”此命令仅限私聊使用” |
| 授权用户群聊 @bot 发送 /search、/query、/rand、/sim、/info、/help、普通文本 | 正常执行对应命令 |
| 授权用户群聊 @bot 发送 /cancel | 正常执行取消（`/cancel` 无私聊限制） |
| 非授权用户私聊发送任何内容 | 静默忽略，仅记录日志 |
| 非授权用户群聊 @bot 发送任何内容 | 静默忽略，仅记录日志 |
| /search 无匹配 | 回复"没有匹配到任何表情包 🙁" |
| /search、/sim、兜底搜索多结果回复 `n` | 翻到下一页（每页 10 条），重置选择超时 |
| 多结果末页回复 `n` | 回复"没有更多结果了"，保持当前页，选择会话不变 |
| 用户选编号超时 | 回复"选择已过期，请重新搜索" |
| /add 等待图片超时 | 回复"发送图片超时，请重新 /add" |
| /add 收到非图片消息 | 提示"请发送一张图片"，继续等待直到超时（默认 60 秒，由 `SESSION_EXPIRE_TIMEOUT` 控制） |
| /add 收到多张图片 | v1.0 只处理第一张图片 |
| /add 无法判断图片扩展名 | 拒绝添加，回复"无法识别图片格式" |
| 新增 `.jpg/.jpeg/.png/.webp/.gif` 图片压缩失败 | `/add` 删除刚下载的图片并回复失败；启动同步或 `/refresh` 跳过该文件并汇总失败 |
| 新增 `.bmp` 图片 | 不执行压缩，继续 OCR 和建索引 |
| 新增不支持扩展名文件 | 不作为表情包处理，不写入索引 |
| /add OCR 或 embedding 失败 | 删除刚下载的图片，不写入索引，回复添加失败原因 |
| 图片文件被删除但索引还在 | sync 阶段1 删除：先 sqlite 后 chroma 删除对应记录；启动时与 `/refresh` 均执行 |
| 文件名包含特殊字符 | `image_path` 作为 sqlite `TEXT` 存储，不使用自定义分隔符解析 |
| `data/index.db` 损坏或非 sqlite 格式 | `MetadataStore.load()` 抛 `sqlite3.DatabaseError`（`IndexManager` 归并为 `IndexCorruptedError`），拒绝启动或刷新，要求用户先修复数据库 |
| chroma 损坏/与 sqlite 不一致 | sync 阶段0 跨库一致性修复：chroma 为空且 sqlite 有数据 → 全量重 embed 并 `rebuild_all`；sqlite 有、chroma 无的 id → 补 embed `upsert`；chroma 有、sqlite 无的 id → 删孤儿向量 |
| `/edittext <id>` 的 id 不存在 | 回复"未找到 id 为 {id} 的表情包" |
| `/edittext` 在索引刷新中 | 回复"索引正在刷新，请稍后再试" |
| `/edittext` 新文本与已有条目冲突 | 回复"该文本已被其他表情包使用" |
| `/edittext` 等待确认超时 | 回复"修改已取消（超时）" |
| `/edittext` 用户回复非"确认" | 回复"已取消修改" |
| `/setspeaker <id>` 的 id 不存在 | 回复"未找到 id 为 {id} 的表情包" |
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
| /addtag <id> 的 id 不存在 | 回复“未找到 id 为 {id} 的表情包” |
| /addtag 参数不足 | 回复“用法：/addtag <entry_id> <tag> [<tag>...]” |
| /addtag 在索引刷新中 | 回复“索引正在刷新，请稍后再试” |
| /addtag 等待确认超时 | 回复“标签添加已取消（超时）” |
| /addtag 用户回复非确认 | 回复“已取消” |
| /addtag 标签已存在 | 不重复添加，回复“本次新增：无”并列出全部标签 |
| /del <id> 全部不存在 | 回复“未找到任何表情包” |
| /del 在索引刷新中 | 回复“索引正在刷新，请稍后再试” |
| /del 等待确认超时 | 回复“删除已取消（超时）” |
| /del 用户回复非确认 | 回复“已取消删除” |
| /del 移图失败（磁盘满/权限等） | 索引原样保留，该 id 记为失败；文件仍在 memes/，可重试 |
| /info 索引刷新中 | 不等待锁，正常返回统计（状态显示“正在刷新索引”） |
| /info 硬件信息读取失败 | 对应字段显示“获取失败”，其他字段正常返回 |

---

## 6. 项目结构

```
meme-pilot/
├── docker-compose.yml         # 容器编排
├── .env.example               # 环境变量模板
├── .env                       # 敏感配置（不提交 Git）
├── .gitignore
├── README.md
├── napcat/
│   └── config/                # NapCatQQ 配置挂载卷
├── memes/                     # 表情包图片目录
├── meme_no_text/             # OCR 无文字图片存放目录（不进索引，Docker 卷挂载）
├── data/                      # 索引数据目录
│   ├── index.db               # sqlite 元数据：id、image_path、text、speaker + meme_tag
│   └── chroma/                # ChromaDB 向量库（collection memes，cosine）
├── log/                       # 日志目录（不纳入版本控制，Docker 卷挂载）
│   ├── bot.log                # 当前日志文件（<= 1MB）
│   └── bot.log.1              # 上一份日志备份
├── scripts/
│   └── migrate_json_to_db.py  # 旧版 index.json/embeddings.json → sqlite+chroma 迁移脚本
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
    │   ├── meme_search.py       # /search 命令
    │   ├── meme_query.py        # /query 命令
    │   ├── meme_rand.py         # /rand 命令
    │   ├── meme_sim.py          # /sim 命令
    │   ├── meme_ai.py           # /ai 命令
    │   ├── meme_add.py          # /add 命令
    │   ├── meme_addtag.py       # /addtag 命令
    │   ├── meme_delete.py       # /del 命令
    │   ├── meme_edit.py         # /edittext 命令
    │   ├── meme_setspeaker.py   # /setspeaker 命令
    │   ├── meme_refresh.py      # /refresh 命令
    │   ├── meme_info.py         # /info 命令
    │   ├── meme_help.py         # /help 命令
    │   ├── meme_cancel.py       # /cancel 命令
    │   ├── meme_plain_text.py   # 兜底：普通文本/未知命令
    │   ├── _help_text.py        # 帮助文本常量（共享模块）
    │   └── _search_utils.py     # 搜索核心逻辑（共享模块）
    └── engine/
        ├── __init__.py
        ├── protocols.py         # 多模块共用 Protocol（EmbeddingProvider、OcrProvider 等）
        ├── provider_factory.py  # OCR/Embedding provider 注册表与工厂函数
        ├── retry_config.py      # 统一 tenacity 网络请求重试配置
        ├── image_optimizer.py   # 图片无损压缩
        ├── openai_ocr.py        # OpenAI 兼容 OCR 封装（原 deepseek_ocr.py）
        ├── paddle_ocr.py        # PaddleOCR 云 API 封装
        ├── rapidocr_ocr.py      # RapidOCR 本地 ONNX OCR 封装
        ├── openai_embedding.py  # OpenAI 兼容 Embedding 封装（原 embedding_service.py）
        ├── google_embedding.py  # Google Embedding API 封装
        ├── rerank_service.py    # DeepSeek 精排封装（实现 RerankProvider）
        ├── metadata_store.py    # sqlite3 元数据存储（MemeEntry + MetadataStore）
        ├── vector_store.py      # chromadb 向量存储（VectorHit + VectorStore）
        ├── index_manager.py     # 索引薄编排（MetadataStoreProtocol + VectorStoreProtocol 协议依赖两个 Store）
        ├── rwlock.py            # 读写锁（写者优先，IndexManager 持锁编排）
        ├── types.py             # 共享数据类型（SearchResult 等）
        ├── utils.py             # 共享工具（vector_norm 等）
        ├── keyword_searcher.py  # 模糊搜索（MetadataStoreProvider 协议依赖）
        ├── random_searcher.py   # 随机取样搜索
        ├── semantic_searcher.py # 语义搜索（VectorStore 召回 + 相似度排序）
        ├── combined_searcher.py # 组合检索（keyword + speaker + tag 过滤）
        └── ai_matcher.py        # AI 语义匹配（VectorStore 召回 + 可选精排）
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
    "openai>=2.43.0",            # OpenAI 兼容 SDK（DeepSeek / OpenAI 兼容 OCR 与 Embedding）
    "pillow>=12.2.0",            # 图片无损压缩
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
# 编辑 .env 填入 QQ_ACCOUNT, AUTHORIZED_USER_IDS, DEEPSEEK_API_KEY，以及按所选 provider 需要填写的 API Key（如 OPENAI_EMBEDDING_API_KEY / OPENAI_OCR_API_KEY 等）

# 3. 放入表情包
# 将你的 .jpg/.jpeg/.png/.gif/.webp/.bmp 放入 memes/ 目录

# 4. 启动
docker compose up -d
```

### 8.2 验证

```bash
# 查看日志
docker compose logs -f bot

# Bot 启动后会自动扫描 memes/ 建索引
# 完成后向你的 QQ 发送 /search 测试
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
