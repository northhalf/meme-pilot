# CONTEXT.md — QQ 表情包机器人

## 术语表

### 核心概念

| 术语 | 定义 |
|------|------|
| **MemePilot** | 本项目英文名；一个 Docker Compose 部署的 QQ 私聊表情包机器人，用于从本地表情包库中搜索、匹配和添加表情包 |
| **表情包** | 存储在本地的图片文件（.jpg/.jpeg/.png/.gif/.webp/.bmp），带有搞笑/吐槽含义 |
| **索引** | 从表情包图片中 OCR 提取的文字和图片路径信息，存储在 sqlite `data/index.db` 中（`meme` 表 + `meme_tag` 关联表），向量存储在 ChromaDB `data/chroma/` 中 |
| **测试目录** | 仓库根目录 `tests/`；按 `unit/`、`integration/`、`fixtures/` 分层，当前只规划目录结构，不代表已经引入测试框架或固定测试命令 |
| **按文件名同步的增量刷新** | 启动和 `/refresh` 时使用的 v1.0 同步策略：阶段0 跨库一致性修复（对齐 sqlite ↔ chroma 的 id 集合，chroma 损坏/为空且 sqlite 有数据时全量重 embed `rebuild_all`）；阶段1 删除 `memes/` 已不存在图片的记录；阶段2 新增图片先按格式执行图片无损压缩，再追加索引记录；文件名仍存在的图片不重新 OCR，不检测同名覆盖；删除记录后保持其他已有 id 稳定，允许临时编号空洞；新增图片按文件名升序处理，并优先复用最小空洞 id；新增图片 OCR 后按「去除所有空白字符的去重键」去重，与已有条目或其他新图同键时保留已有/靠前者、删除重复新图；OCR 无文字的新图移至 `meme_no_text/` 不进索引 |
| **关键词搜索** | 功能一：用户输入关键词，先用「原始输入去所有空白、保留助词」的关键词对索引中的 OCR 文本做精确子串匹配，命中则只返回包含该子串的 Top 10 表情包；未命中时回退到 jieba.posseg 分词过滤助词后的关键词，用 pylcs LCS 模糊匹配（阈值统一 >= 60），按分数降序返回 Top 10；模糊回退阶段如果存在分数为 100 的结果，只返回分数为 100 的结果；不匹配文件名 |
| **AI 匹配** | 功能二：用户用自然语言描述，先用 `VectorStore.query` 从 ChromaDB 召回 Top 10（不设最低相似度阈值），再用 `MetadataStore.get_entry` 取 metadata 构候选，经 DeepSeek 精排后返回；若精排失败、解析失败或返回 `0`，fallback 到 embedding Top 1。`AIMatcher` 通过 `MetadataEntryProvider` + `VectorQueryProvider` 两个 Protocol 依赖两个 Store（见「依赖协议」） |
| **图片无损压缩** | 新增图片进入索引前的文件优化步骤；`/add`、启动同步和 `/refresh` 对新增的 `.jpg/.jpeg/.png/.webp/.gif` 尝试无损压缩，成功后覆盖原文件；`.bmp` 不压缩，其他扩展名不作为表情包处理 |
| **私聊** | v1.0 的基础会话形态：授权 QQ 用户与 Bot 一对一对话；支持所有命令（组 A：`/add`、`/ai`、`/refresh`；组 B：`/search`、`/help`、普通文本） |
| **授权用户** | v1.0 中允许使用 Bot 的 QQ 用户；可以配置一个或多个，`/help`、`/search`、`/ai`、`/add`、`/refresh` 都只对授权用户的私聊开放 |
| **授权用户列表** | 环境变量 `AUTHORIZED_USER_IDS` 声明的 QQ 号白名单，多个 QQ 号用英文逗号分隔，例如 `123456,987654` |
| **非授权用户** | 不在 `AUTHORIZED_USER_IDS` 中的 QQ 用户；v1.0 中其私聊消息会被静默忽略，只记录日志，不回复提示 |
| **群聊消息** | `/search`、`/help`、普通文本（组 B）可通过群聊中 @bot 的方式触发；`/add`、`/ai`、`/refresh`（组 A）群聊中 @bot 调用时回复"此命令仅限私聊使用"。非授权用户在群聊中@bot 发送任何消息时静默忽略。 |
| **去重键** | OCR 文本去除所有空白字符（含半角/全角空格、制表符、换行）后的纯文本；用于在 `/add` 和 `sync_with_filesystem` 新增阶段判定「是否完全相同的图片」，通过 `MetadataStore.get_id_by_text` 查询，实时计算不落盘；DB 层 `text` UNIQUE 约束兜底，冲突抛 `DuplicateEntryError` |
| **无文字目录** | `memes/` 同级的 `meme_no_text/` 目录；OCR 去除所有空白后为空的图片在此场景下不进入索引，被移动到该目录并由日志 warning 提示，本项目不处理该类表情包 |
| **entry_id** | 索引 id，类型为 `int`，全栈统一（sqlite `meme.id` 与 chroma 向量 id 一一对应）；删除记录后保持其他已有 id 稳定，允许临时编号空洞，新增时复用最小空洞 id |
| **image_path** | `memes/` 目录下相对路径（扁平结构下即文件名），存储在 sqlite `meme.image_path` 列；原 v1.0 早期称 `filename`，重构后改为相对路径语义 |
| **speaker** | 说话人字段，sqlite `meme.speaker` 列，`NULL` 允许；v1.0 可通过 /setspeaker 命令设置，供后续「按角色搜索」扩展 |
| **标记词** | 表情包的多值标签，存储在 sqlite `meme_tag` 关联表（`meme_id` + `tag`，`ON DELETE CASCADE`）；v1.0 预留不填充，供后续手动标注扩展 |

### 技术组件

| 术语 | 定义 |
|------|------|
| **NapCatQQ** | QQ 协议端，基于 NTQQ 的 OneBot v11 实现，负责收发 QQ 消息 |
| **NoneBot2** | Python 异步聊天机器人框架，负责业务逻辑 |
| **DeepSeek-OCR** | 硅基流动上的视觉 OCR 模型（`deepseek-ai/DeepSeek-OCR`），通过 chat completions API 调用，用于从图片中提取文字；返回去除所有空白后的文本 |
| **index.db** | 业务索引数据库，sqlite3 格式，存于 `data/index.db`；`meme` 表保存每个 id 对应的 `image_path`、OCR `text`（去空白后）、`speaker`，`meme_tag` 关联表保存多值标记词；`UNIQUE INDEX` 加在 `image_path` 与 `text` 上，`PRAGMA foreign_keys = ON` |
| **原子索引更新** | 更新 sqlite 与 chroma 时统一「先 sqlite 后 chroma」写入顺序，`VectorStore.upsert` 失败时回滚 sqlite 写入，保证两库一致；失败时保留旧索引 |
| **chroma 向量库** | AI 匹配必需的向量索引，存于 `data/chroma/`，ChromaDB `PersistentClient`，collection 默认 `memes`，HNSW `cosine` 距离；每条向量仅存 `id`（与 sqlite `meme.id` 一一对应，内部转 `str`）+ 1024 维 `embedding`；`similarity = 1 - distance`；首次建索引和 `/refresh` 时由 `VectorStore` 维护，sync 阶段0 负责跨库一致性修复 |
| **pylcs** | C++ 实现的最长公共子序列/子串算法库，用于关键词的非精确匹配 |
| **DeepSeek** | 大模型 API 提供商，用于 AI 匹配中的候选精排，不用于生成 embedding |
| **SiliconFlow** | Embedding API 提供商，用于生成用户描述和索引文本的向量；v1.0 默认模型为 `BAAI/bge-m3` |
| **依赖协议（Protocol）** | engine 模块用 `typing.Protocol` 解耦依赖的约定：消费者按自身需要定义**最小接口**协议（接口隔离），不依赖具体 Store 实现，便于测试用 mock 替换。**放置规则**：只被一个模块用的 Protocol 定义在该模块内，多模块共用的放 `bot/engine/protocols.py`。现有协议：`protocols.py.EmbeddingProvider`（`embed`，IndexManager 与 AIMatcher 共用）；`keyword_searcher.MetadataStoreProvider`（`get_all_entries`）；`ai_matcher.MetadataEntryProvider`（`get_entry`）+ `ai_matcher.VectorQueryProvider`（`count` + `async query`）+ `ai_matcher.RerankProvider`（`async rerank`）；`index_manager.MetadataStoreProtocol`（全 CRUD 子集）+ `index_manager.VectorStoreProtocol`（全 CRUD 子集）+ `index_manager.ImageOptimizerProtocol`（`async optimize`）+ `index_manager.OcrProvider`（`async ocr`）。`MetadataEntryProvider`（按 id 取单条）与 `MetadataStoreProvider`（取全量）与 `MetadataStoreProtocol`（全 CRUD）命名不同因接口需求不同，不复用。生产代码 `bot.py` 传真实 `MetadataStore`/`VectorStore`/`ImageOptimizer` 实例，结构子类型天然满足协议。 |
| **授权校验模块** | `bot/auth.py`，从 `AUTHORIZED_USER_IDS` 环境变量读取白名单，提供 `is_authorized()` / `log_unauthorized()` 供各插件统一调用 |
| **全局路径与配置** | `bot/config.py`，通过 `Path(__file__).resolve().parent.parent` 定位项目根目录，导出 `PROJECT_ROOT`、`MEMES_DIR`、`DATA_DIR`、`INDEX_DB_PATH`、`CHROMA_DIR` 路径常量和 `read_session_timeout()`、`read_ocr_provider()` 配置读取函数 |

### 交互协议

| 术语 | 定义 |
|------|------|
| **OneBot v11** | 标准 QQ 机器人通信协议，NapCatQQ 与 NoneBot2 通过此协议通信 |
| **反向 WebSocket** | v1.0 的 OneBot 连接方式：NapCatQQ 主动连接 NoneBot2，NoneBot2 在 bot 容器中监听连接 |
| **/help** | 帮助命令；授权用户私聊或群聊中 @bot 发送 `/help` 时，Bot 返回当前命令和简单用法（含 `/cancel`）；授权用户私聊或群聊 @bot 发送未知斜杠命令时，Bot 回复”未知命令”并附帮助摘要；授权用户私聊或群聊 @bot 发送普通文本时，Bot 等同执行 `/search`。`/help` 在有活跃会话时仍可正常触发（旁路），回复帮助文本后继续等待原会话。
| **/search** | 关键词搜索命令，后接关键词；支持私聊和群聊中 @bot 触发；同一授权用户同一时间只保留一个待处理会话，新 `/search` 或 `/add` 会覆盖旧状态，并向用户提示已取消上一条未完成操作；等待选择期间支持 `/cancel` 取消和 `/help` 旁路查看帮助 |
| **/ai** | AI 描述匹配命令，后接自然语言描述 |
| **/add** | 添加表情包命令，格式 `/add [目标命名]`；目标命名作为保存到 `memes/` 的文件名基名，不写入 OCR 文本；未提供目标命名时按发送时间和图片内容 hash 自动生成文件名；同一授权用户同一时间只保留一个待处理会话，新 `/add` 或 `/search` 会覆盖旧状态，并向用户提示已取消上一条未完成操作；等待图片期间支持 `/cancel` 取消和 `/help` 旁路查看帮助 |
| **/edittext** | OCR 文本编辑命令，格式 `/edittext <entry_id> <新文本>`；授权用户在私聊中发送，Bot 发送图片和确认消息，用户回复「确认」或「yes」后执行修改；修改同步更新 sqlite 元数据与 chroma 向量库；权限属组 A（仅私聊）；等待确认期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；超时自动取消 |
| **/refresh** | 增量更新索引命令；v1.0 使用全局索引更新锁，同一时间只允许一个索引写入任务运行；新增图片会先按格式执行无损压缩；锁占用期间 `/help`、`/search`、`/ai`、`/add`、`/refresh` 暂时拒绝服务 |
| **/cancel** | 取消命令；授权用户在私聊或群聊中发送 `/cancel` 时取消当前正在执行的命令（如 `/add` 等待图片或 `/search` 等待选择）；支持同频道取消（got 等待中）和异频道取消（私聊/群聊分离）；无活跃会话时回复"当前没有没有活跃的会话"；`/cancel` 本身在任意状态下均可触发 |
