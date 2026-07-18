# CONTEXT.md — QQ 表情包机器人

## 术语表

### 核心概念

| 术语 | 定义 |
|------|------|
| **MemePilot** | 本项目英文名；一个 Docker Compose 部署的 QQ 私聊表情包机器人，用于从本地表情包库中搜索、匹配和添加表情包 |
| **表情包** | 存储在本地的图片文件（.jpg/.jpeg/.png/.gif/.webp/.bmp），带有搞笑/吐槽含义 |
| **索引** | 从表情包图片中 OCR 提取的文字和图片路径信息，存储在 sqlite `data/index.db` 中（`meme` 表 + `meme_tag` 关联表），向量存储在 ChromaDB `data/chroma/` 中 |
| **测试目录** | 仓库根目录 `tests/`；按 `unit/`、`integration/`、`fixtures/` 分层，当前只规划目录结构，不代表已经引入测试框架或固定测试命令 |
| **按文件名同步的增量刷新** | 启动和 `/refresh` 时使用的 v1.0 同步策略：阶段0 跨库一致性修复（对齐 sqlite ↔ chroma 的 id 集合，chroma 损坏/为空且 sqlite 有数据时全量重 embed `rebuild_all`）；阶段1 删除 `memes/` 已不存在图片的记录；阶段2 新增图片先执行图片压缩/转换（`CONVERT_TO_WEBP` 开启时转 WebP，关闭时同格式无损压缩），再追加索引记录；文件名仍存在的图片不重新 OCR，不检测同名覆盖；删除记录后保持其他已有 id 稳定，允许临时编号空洞；新增图片按文件名升序处理，并优先复用最小空洞 id；新增图片 OCR 后按「按英文逗号拼接的去重键」去重，与已有条目或其他新图同键时保留已有/靠前者、将重复新图归档到 `memes_replaced/`；OCR 无文字的新图移至 `meme_no_text/` 不进索引 |
| **关键词搜索** | 功能一：用户输入关键词，先用「原始输入去所有空白、保留助词」的关键词对索引中的 OCR 文本（按英文逗号拼接存储，匹配时忽略逗号分隔符）做精确子串匹配，命中则只返回包含该子串的全部表情包（多结果每页 10 条分页）；未命中时回退到 jieba.posseg 分词过滤助词后的关键词，用 pylcs LCS 模糊匹配（阈值统一 >= 60），按分数降序返回全量匹配（多结果每页 10 条分页）；模糊回退阶段如果存在分数为 100 的结果，只返回分数为 100 的结果；不匹配文件名 |
| **随机选择** | `/rand [关键词]` 命令的行为：有关键词时在关键词搜索结果中随机取 10 个，无关键词时全库随机；回复 `0` 换一批，每次独立抽样 |
| **语义选择** | `/sim <描述文本>` 命令的行为：基于 embedding 语义搜索全库召回候选供用户选择（多结果每页 10 条分页） |
| **创建合集** | `/collection create <名称>` 的行为：授权用户仅限私聊直接创建；名称去首尾空白但禁止内部空白、路径字符、隐藏名与保留名；命令通过 IndexManager Write Worker 在写锁内创建或登记 `memes/` 一级普通目录，并写入 `meme_collection`；创建成功后不自动切换，已有目录图片需 `/refresh` 入库。 |
| **删除合集** | `/collection delete <编号|名称>` 的行为：授权用户仅限私聊直接执行；通过 IndexManager Write Worker 在写锁内先 `rmdir` 空目录、后删 `meme_collection` 记录并回退引用它的所有 ChatScope 到 0；非空合集拒绝（回复“合集不为空，请先 /move 或 /del 清空后再删除”）；目录身份异常拒绝；rmdir 失败时 DB 未动；rmdir 成功但 SQLite 删除失败时补偿 `mkdir()` 恢复空目录；只动 `meme_collection` 与 `chat_collection_scope`，不碰 `meme`、`meme_tag` 与 chroma；`collection_id` 编号不回收。 |
| **重命名合集** | `/collection rename <旧编号|名称> <新名称>` 的行为：授权用户仅限私聊直接执行；通过 IndexManager Write Worker 在写锁内先改 `meme_collection.name` 与该合集所有 `meme.image_path` 首段、后 `Path.rename` 重命名 `memes/` 目录；`collection_id` 不变，chroma 与 ChatScope 不受影响（按编号引用，不随名称变化）；新名称走与 `create` 相同的 `validate_collection_name` 校验且必须未登记；目录 rename 失败时补偿调 `rename_collection` 回滚 SQLite 与缓存；不重新 embed、不动 chroma。 |
| **图片压缩/转换** | 新增图片进入索引前的文件优化步骤；`CONVERT_TO_WEBP=true`（默认）时 `/add`、启动同步和 `/refresh` 对新增的 `.jpg/.jpeg/.png/.gif/.bmp` 转为有损 WebP（q85），转换失败降级保留原格式；`CONVERT_TO_WEBP=false` 时对 `.jpg/.jpeg/.png/.webp/.gif` 执行同格式无损压缩，成功后覆盖原文件，`.bmp` 跳过；其他扩展名不作为表情包处理 |
| **私聊** | v1.0 的基础会话形态：授权 QQ 用户与 Bot 一对一对话；支持所有命令（组 A：`/collection`、`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/refresh`、`/move`；组 B：`/query`、`/rand`、`/sim`、`/help`、`/info`、`/switch`、普通文本；组 C：`/cancel`） |
| **授权用户** | v1.0 中允许使用 Bot 的 QQ 用户；可以配置一个或多个，所有当前命令和普通文本入口都只对授权用户开放 |
| **授权用户列表** | 环境变量 `AUTHORIZED_USER_IDS` 声明的 QQ 号白名单，多个 QQ 号用英文逗号分隔，例如 `123456,987654` |
| **非授权用户** | 不在 `AUTHORIZED_USER_IDS` 中的 QQ 用户；v1.0 中其私聊消息会被静默忽略，只记录日志，不回复提示 |
| **群聊消息** | `/query`、`/rand`、`/sim`、`/help`、`/info`、`/switch`、普通文本（组 B）可通过群聊中 @bot 的方式触发；`/collection`、`/add`、`/addtag`、`/del`、`/edittext`、`/setspeaker`、`/refresh`、`/move`（组 A）群聊中 @bot 调用时回复"此命令仅限私聊使用"。`/cancel`（组 C）私聊和群聊均可用；非授权用户在群聊中 @bot 发送任何消息时静默忽略。 |
| **去重键** | OCR 文本按空白分割后以英文逗号拼接的文本（空白含半角/全角空格、制表符、换行）；用于在 `/add` 和 `sync_with_filesystem` 新增阶段判定「是否完全相同的图片」，通过 `MetadataStore.get_id_by_text` 查询，实时计算不落盘；DB 层 `text` UNIQUE 约束兜底，冲突抛 `DuplicateEntryError` |
| **无文字目录** | `memes/` 同级的 `meme_no_text/` 目录；OCR 按英文逗号拼接后为空的图片在此场景下不进入索引，被移动到该目录并由日志 warning 提示，本项目不处理该类表情包 |
| **删除备份目录** | `memes/` 同级的 `memes_deleted/` 目录；被 `/del` 命令删除的表情包图片会移动到该目录备份，可手动恢复 |
| **替换归档目录** | `memes/` 同级的 `memes_replaced/` 目录；`/add` 去重替换旧图或 `/refresh` 去重归档重复新图时，被替换的图片文件会被移动到此目录，保留原文件名（冲突时追加 `_n` 序号），可手动恢复 |
| **迁移备份目录** | `memes/` 同级的 `memes_migrated_backup/` 目录；运行 `scripts/convert_memes_to_webp.py` 迁移脚本将存量图片批量转 WebP 时，原文件会移动到此目录备份，默认在 `memes` 同级创建，可通过 `--backup-dir` 自定义 |
| **entry_id** | 内部稳定 ID，类型为 `int`（sqlite `meme.id` 与 chroma 向量 id 一一对应）；仅供引擎内部和插件 state 执行读写，不作为用户可见标识；用户不再直接输入该值 |
| **公开 ID** | 用户可见的表情包复合 ID，格式为 `合集编号.局部编号`（如 `1.3`、`0.42`）；当前选择普通合集时可输入局部短号，全部合集模式必须输入完整 ID；搜索结果、管理命令确认和执行结果均展示公开 ID，不展示 `entry_id` |
| **表情包合集** | 由 `memes/` 下一级目录组织的表情包分组；普通合集由系统分配正整数编号，目录内可继续使用任意深度子目录 |
| **合集编号** | 普通合集的正整数编号；根目录使用保留编号 `0` |
| **合集内编号** | `local_id`，在一个合集内独立分配的正整数，与合集编号共同构成公开 ID |
| **全局** | `memes/` 根目录图片的存储归属，编号为 `0`，显示名称为 `全局` |
| **全部合集** | `/switch 0` 对应的聚合搜索范围，包含根目录和所有普通合集，显示名称为 `全部合集` |
| **当前合集** | 一个 `ChatScope` 持久化保存的 `/switch` 选择，决定该聊天窗口的搜索、添加和公开 ID 短号解析上下文 |
| **image_path** | `memes/` 目录下相对路径，可能是任意深度的嵌套路径；根目录图片路径即文件名，合集内路径首段为合集名称，存储在 sqlite `meme.image_path` 列；原 v1.0 早期称 `filename`，重构后改为相对路径语义 |
| **speaker** | 说话人字段，sqlite `meme.speaker` 列，`NULL` 允许；v1.0 可通过 /setspeaker 命令设置（短命令 `/sp`，与 `/setspeaker` 等价），供后续「按角色搜索」扩展 |
| **标记词 / 标签** | 表情包的多值标签，存储在 sqlite `meme_tag` 关联表（`meme_id` + `tag`，`ON DELETE CASCADE`）；v1.0 中可通过 `/add` 命令在添加时指定初始标签，也可通过 `/addtag` 命令后续追加；用于关键词搜索与元数据展示 |

### 技术组件

| 术语 | 定义 |
|------|------|
| **NapCatQQ** | QQ 协议端，基于 NTQQ 的 OneBot v11 实现，负责收发 QQ 消息 |
| **NoneBot2** | Python 异步聊天机器人框架，负责业务逻辑 |
| **OpenAI 兼容 OCR** | `OCR_PROVIDER=deepseek` 时使用的 OpenAI 兼容视觉 OCR 服务；原模块 `bot/engine/deepseek_ocr.py` 已重命名为 `openai_ocr.py`，实现 `index_manager.OcrProvider` 协议，返回按空白分割后以英文逗号拼接的文本；示例模型为 `deepseek-ai/DeepSeek-OCR` |
| **index.db** | 业务索引数据库，sqlite3 格式，存于 `data/index.db`；`meme` 表保存每个 id 对应的 `image_path`、OCR `text`（按英文逗号拼接）、`speaker`，`meme_tag` 关联表保存多值标记词；`UNIQUE INDEX` 加在 `image_path` 与 `text` 上，`PRAGMA foreign_keys = ON` |
| **原子索引更新** | 更新 sqlite 与 chroma 时统一「先 sqlite 后 chroma」写入顺序，`VectorStore.upsert` 失败时回滚 sqlite 写入，保证两库一致；失败时保留旧索引 |
| **chroma 向量库** | 服务 `/sim`、Embedding 索引和新增图片向量化的向量存储，位于 `data/chroma/`；使用 ChromaDB `PersistentClient`，collection 默认 `memes`，HNSW `cosine` 距离；每条向量保存与 sqlite `meme.id` 一一对应的字符串 `id`、1024 维 `embedding` 和 `collection_id` 元数据；`similarity = 1 - distance`；首次建索引和 `/refresh` 时由 `VectorStore` 维护，sync 阶段0负责跨库一致性修复 |
| **pylcs** | C++ 实现的最长公共子序列/子串算法库，用于关键词的非精确匹配 |
| **RandomSearcher** | 随机搜索器，`bot/engine/random_searcher.py`；从 `KeywordSearcher` 搜索结果或全库 `MetadataStore` 条目中随机取样返回，由 `IndexManager.random_search` 持读锁调用；所有结果的 `similarity` 固定为 0.0 |
| **CombinedSearcher** | 组合搜索器，`bot/engine/combined_searcher.py`；先按 `speakers`(OR) + `tags`(AND) 过滤 `MetadataStore.get_all_entries` 子集，再委托 `KeywordSearcher.search_in` 在子集上跑关键词匹配；无关键词时包装 `similarity=0.0` 随机排序，有关键词时同相似度组内随机排序（组间仍按相似度降序，由模块级 `_shuffle_within_similarity_groups` 用 `itertools.groupby` 实现）；`IndexManager.search_combined` 持读锁调用；依赖 `MetadataStoreProvider` + `KeywordSearcher` |
| **SemanticSearcher** | 语义搜索器，`bot/engine/semantic_searcher.py`；基于 embedding 向量从 `VectorStore` 召回候选（`limit=None` 全库召回），通过 `MetadataStoreProvider`（`get_all_entries`）批量映射 metadata；`IndexManager.semantic_search` 锁外 embed 后持读锁调用 |
| **PresentOptions** | 候选展示选项，`bot/plugins/_search_utils.py` 中的 `@dataclass(frozen=True)`；控制列表行是否展示相似度（`show_similarity`）、相似度量纲（`similarity_scale`：`ratio`=0–1 / `score`=0–100）、是否支持翻页（`next_trigger`，`None`=不支持，如 `/rand`）、每页条数（`page_size`，默认 `PAGE_SIZE`）。`/sim` 用 `show_similarity=True, similarity_scale="ratio", next_trigger="n"`；`/query` 与兜底搜索用 `show_similarity=True, similarity_scale="score", next_trigger="n"`；`/rand` 用默认值（不展示相似度、不翻页） |
| **PAGE_SIZE** | 每页展示的候选条数常量，`bot/plugins/_search_utils.py` 中硬编码为 `10`；`PresentOptions.page_size` 默认引用此常量 |
| **NEXT_PAGE_TRIGGER** | 下一页触发词常量，`bot/plugins/_search_utils.py` 中硬编码为 `"n"`；用户在多结果列表中回复 `n` 翻到下一页，末页回复 `n` 提示"没有更多结果了"并保持当前页 |
| **OpenAI 兼容 Embedding** | OpenAI 兼容 Embedding API 提供商；当 `EMBEDDING_PROVIDER=openai`（默认）时可用于生成用户描述和索引文本的向量；`.env.example` 示例默认使用 GLM，模型为 `embedding-3` |
| **依赖协议（Protocol）** | engine 模块用 `typing.Protocol` 解耦依赖的约定：消费者按自身需要定义**最小接口**协议（接口隔离），不依赖具体 Store 实现，便于测试用 mock 替换。**放置规则**：只被一个模块使用的 Protocol 定义在该模块内，多模块共用的放 `bot/engine/protocols.py`；多模块共用的数据类型放 `bot/engine/types.py`（如 `SearchResult`）。现有协议包括 `protocols.py.EmbeddingProvider`、`index_manager.OcrProvider`、`MetadataStoreProvider`、`MetadataStoreProtocol`、`VectorStoreProtocol`、`ImageOptimizerProtocol` 等；生产代码 `bot.py` 传入真实 `MetadataStore`、`VectorStore`、`ImageOptimizer` 和 provider 实例，结构子类型天然满足协议。 |
| **Provider 工厂** | `bot/engine/provider_factory.py` 维护的 OCR 与 Embedding provider 注册表，提供 `register_ocr()` / `register_embedding()` 注册函数、`create_ocr_provider()` / `create_embedding_provider()` 工厂函数，以及 `ProviderNotAvailableError`；`bot/engine/__init__.py` 在导入时自动注册所有可用 provider，依赖缺失的 provider 会被标记为不可用 |
| **RapidOCR** | 本地 ONNX OCR 引擎；`OCR_PROVIDER=rapidocr` 时由 `bot/engine/rapidocr_ocr.py` 调用，无需联网即可从图片中提取文字，返回按空白分割后以英文逗号拼接的文本；与 PaddleOCR 共用 `OCR_TEXT_SCORE` 置信度阈值 |
| **Google Embedding** | `EMBEDDING_PROVIDER=google` 时使用的文本向量服务，基于 `google-genai` SDK 调用 Google GenAI API，固定输出 1024 维向量，示例默认模型 `gemini-embedding-001`，由 `bot/engine/google_embedding.py` 实现 `protocols.EmbeddingProvider` |
| **psutil** | 系统资源监控库；`/info` 命令通过它读取本机内存、CPU 占用以及当前进程 RSS，纯本地调用，不依赖网络 |
| **OCR_TEXT_SCORE** | OCR 文本置信度阈值，环境变量，默认 `0.9`；PaddleOCR 与 RapidOCR 共用此阈值过滤低置信度识别结果 |
| **tenacity 重试** | `bot/engine/retry_config.py` 提供的统一网络请求重试装饰器 `api_retry()`；默认对 `httpx` 网络/连接/超时异常、Python 内置 `ConnectionError` / `TimeoutError` 以及调用方指定的额外异常（如 OpenAI / Google API 异常）进行最多 3 次指数退避重试，本地业务异常（如 `ValueError`、`FileNotFoundError`）不重试 |
| **授权校验模块** | `bot/auth.py`，从 `AUTHORIZED_USER_IDS` 环境变量读取白名单，提供 `is_authorized()` / `log_unauthorized()` 供各插件统一调用 |
| **全局路径与配置** | `bot/config.py`，通过 `Path(__file__).resolve().parent.parent` 定位项目根目录，导出 `PROJECT_ROOT`、`MEMES_DIR`、`MEMES_DELETED_DIR`、`DATA_DIR`、`INDEX_DB_PATH`、`CHROMA_DIR` 路径常量和 `read_session_timeout()`、`read_ocr_provider()`、`read_embedding_provider()`、`read_ocr_text_score()`、`read_convert_to_webp()` 等配置读取函数 |
| **CONVERT_TO_WEBP** | 环境变量，控制新增图片是否转为 WebP 存储，默认 `true`；`"false"`/`"0"`/`"no"` 为关闭（按传输格式同格式压缩），其余无效值回退 `true`；由 `bot/config.py` 的 `read_convert_to_webp()` 读取，`bot.py` startup 注入 `ImageOptimizer(should_convert_to_webp=...)` |

### 交互协议

| 术语 | 定义 |
|------|------|
| **OneBot v11** | 标准 QQ 机器人通信协议，NapCatQQ 与 NoneBot2 通过此协议通信 |
| **反向 WebSocket** | v1.0 的 OneBot 连接方式：NapCatQQ 主动连接 NoneBot2，NoneBot2 在 bot 容器中监听连接 |
| **/help** | 帮助命令；授权用户私聊或群聊中 @bot 发送 `/help` 时，Bot 返回当前命令和简单用法（含 `/collection create`、`/collection delete`、`/collection rename`、`/cancel`、`/switch`、`/move`）；授权用户私聊或群聊 @bot 发送未知斜杠命令时，Bot 回复”未知命令”并附帮助摘要；授权用户私聊发送普通文本时，Bot 等同执行关键词搜索（原 `/search` 命令已删除）。`/help` 在有活跃会话时仍可正常触发（旁路），回复帮助文本后继续等待原会话；短命令 `/h`，与 `/help` 等价。
| **/search** | 已删除。原 `/search <关键词>`（短命令 `/s`）不再作为独立斜杠命令提供；授权用户私聊或群聊 @bot 发送普通文本时，仍按关键词搜索并返回 `PresentOptions`（参见「兜底搜索」）。 |
| **/query** | 组合检索命令，格式 `/query <关键词> [@说话人] [#标签...]`；`#tag` 标记标签（可多个，AND）、`@speaker` 标记说话人（可多个，OR）、其余为关键词；speaker 精确相等区分大小写、tag 区分大小写；有关键词时展示关键词相似度（score，同相似度组内随机）、无关键词纯过滤时不展示相似度（随机排序）；权限属组 B（私聊+群聊@bot）；等待选择期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；短命令 `/q`，与 `/query` 等价 |
| **/rand** | 随机选择命令，格式 `/rand [关键词]`；有关键词时在关键词搜索结果中随机取 10 个，无关键词时全库随机；回复 `0` 换一批，每次独立抽样；支持私聊和群聊中 @bot 触发；等待选择期间支持 `/cancel` 取消和 `/help` 旁路查看帮助 |
| **/sim** | 语义选择命令，格式 `/sim <描述文本>`；基于 embedding 语义搜索全库召回候选供用户选择；列表行展示语义相似度百分比（ratio 量纲，0–1 归一为 0–100%），多结果按每页 10 条分页，回复 `n` 看下一页；支持私聊和群聊中 @bot 触发；等待选择期间支持 `/cancel` 取消和 `/help` 旁路查看帮助 |
| **/collection create** | 创建合集命令，格式 `/collection create <名称>`；仅授权用户私聊可用，校验通过后直接执行，不需要确认；通过 IndexManager Write Worker 在写锁内创建或登记 `memes/` 一级普通目录并写入 `meme_collection`；已有普通目录只登记，图片需执行 `/refresh` 建立索引；成功后不自动切换当前合集 |
| **/collection delete** | 删除合集命令，格式 `/collection delete <编号|名称>`；仅授权用户私聊可用，校验通过后直接执行，不需要确认；`<编号|名称>` 解析规则同 `/switch`（编号优先、名称兜底），`0` 不被接受（全局不可删除）；通过 IndexManager Write Worker 在写锁内先 `rmdir` 空目录、后删 `meme_collection` 记录并回退引用它的所有 ChatScope 到 0；非空合集拒绝；权限属组 A（仅私聊）；rmdir 失败时 DB 未动，目录 rename 失败时补偿回滚 SQLite |
| **/collection rename** | 重命名合集命令，格式 `/collection rename <旧编号|名称> <新名称>`；仅授权用户私聊可用，校验通过后直接执行，不需要确认；`<旧编号|名称>` 解析规则同 `/switch`，`<新名称>` 走与 `create` 相同的 `validate_collection_name` 校验且必须未登记；通过 IndexManager Write Worker 在写锁内先改 `meme_collection.name` 与该合集所有 `meme.image_path` 首段、后 `Path.rename` 重命名 `memes/` 目录；`collection_id` 不变，chroma 与 ChatScope 不受影响；目录 rename 失败时补偿调 `rename_collection` 回滚 SQLite 与缓存；权限属组 A（仅私聊） |
| **/switch** | 合集切换命令，格式 `/switch [合集编号|名称]`；无参数时列出全部合集（`0` 为全部合集）和当前选择，有参数时按编号或精确名称切换当前 `ChatScope` 的搜索范围；`0` 表示全部合集，普通合集下可使用局部短号；权限属组 B（私聊 + 群聊 @bot）；参与现有聊天会话互斥；等待处理期间支持 `/cancel` 取消和 `/help` 旁路查看帮助 |
| **/move** | 跨合集移动命令，格式 `/move <公开ID> <目标合集编号|名称>`；仅限私聊，需要用户回复「确认」/「yes」/「y」后执行；目标 `0` 表示移动到 `memes/` 根目录；移动保留内部 ID、OCR、speaker、tags 和 Embedding，只变更合集归属与文件落点；权限属组 A（仅私聊）；等待确认期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；超时自动取消；别名 `/mv`，与 `/move` 等价 |
| **/add** | 添加表情包命令，格式 `/add [speaker <tags...>]`；`speaker` 为可选说话人，`tags` 为可选标记词列表，均不写入 OCR 文本；文件名始终由 Bot 按 `meme_<YYYYMMDDHHMMSS>_<hash8>` 规则自动生成；新增图片按 `CONVERT_TO_WEBP` 开关执行压缩/转换（开启时转 WebP，失败降级保留原格式）；同一授权用户同一时间只保留一个待处理会话，新 `/add` 会覆盖旧状态，并向用户提示已取消上一条未完成操作；等待图片期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；短命令 `/a`，与 `/add` 等价 |
| **/addtag** | 标签追加命令，格式 `/addtag <公开ID> <tag> [<tag>...]`；授权用户在私聊中发送，Bot 发送当前 OCR 文本、当前标签和新增标签的确认消息，用户回复「确认」/「yes」/「y」后执行追加；仅更新 sqlite 元数据，无需重新 embed；权限属组 A（仅私聊）；等待确认期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；超时自动取消；短命令 `/at`，与 `/addtag` 等价 |
| **/del** | 删除表情包命令，格式 `/del <公开ID>...`；支持完整公开 ID 与当前普通合集短号混用，按内部 ID 去重并保持顺序；授权用户确认后执行删除，结果按成功、未找到、失败三类使用删除前公开 ID 快照汇总；删除时先将图片移动到 `memes_deleted/` 目录备份，再删除 sqlite 记录与 chroma 向量；权限属组 A（仅私聊）；等待确认期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；超时自动取消；短命令 `/d`，与 `/del` 等价 |
| **/edittext** | OCR 文本编辑命令，格式 `/edittext <公开ID> <新文本>`；授权用户在私聊中发送，Bot 发送确认消息，用户回复「确认」/「yes」/「y」后执行修改；修改同步更新 sqlite 元数据与 chroma 向量库；权限属组 A（仅私聊）；等待确认期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；超时自动取消；短命令 `/e`，与 `/edittext` 等价 |
| **/setspeaker** | 说话人设置命令，格式 `/setspeaker <公开ID> [说话人]`；省略说话人时清空字段；授权用户确认后仅更新 sqlite 说话人字段，用户提示使用公开 ID；权限属组 A（仅私聊）；等待确认期间支持 `/cancel` 取消和 `/help` 旁路查看帮助；超时自动取消；短命令 `/sp`，与 `/setspeaker` 等价 |
| **/refresh** | 增量更新索引命令；v1.0 使用全局索引更新锁，同一时间只允许一个索引写入任务运行；新增图片会先执行图片压缩/转换（`CONVERT_TO_WEBP` 开启时转 WebP）；刷新期间新的写命令（含 `/collection create`、`/collection delete`、`/collection rename`、`/add`）会被拒绝，搜索命令等待读锁超时后提示稍后再试；短命令 `/r`，与 `/refresh` 等价 |
| **/info** | 状态信息命令；授权用户在私聊或群聊中 @bot 发送 `/info [公开ID]`，无参数时 Bot 返回当前索引条目数、speaker 使用频率排行（前 10）、当前状态（空闲/刷新中/处理命令）、本机内存/CPU 占用以及当前 Bot 进程 RSS；带公开 ID 时返回该表情包详情（公开 ID、合集、OCR 文本、文件名、文件大小、说话人、标签；speaker 与 tags 同时为空时省略「说话人」「标签」两行），非法或不存在时返回公开 ID 领域提示；权限属组 B（私聊和群聊 @bot 均可） |
| **/cancel** | 取消命令；授权用户在私聊或群聊中发送 `/cancel` 时取消当前正在执行的命令（如 `/add` 等待图片或普通文本搜索等待选择）；支持同频道取消（got 等待中）和异频道取消（私聊/群聊分离）；无活跃会话时回复"当前没有没有活跃的会话"；`/cancel` 本身在任意状态下均可触发；短命令 `/c`，与 `/cancel` 等价 |
