# CONTEXT.md — QQ 表情包机器人

## 术语表

### 核心概念

| 术语 | 定义 |
|------|------|
| **MemePilot** | 本项目英文名；一个 Docker Compose 部署的 QQ 私聊表情包机器人，用于从本地表情包库中搜索、匹配和添加表情包 |
| **表情包** | 存储在本地的图片文件（.jpg/.jpeg/.png/.gif/.webp/.bmp），带有搞笑/吐槽含义 |
| **索引** | 从表情包图片中 OCR 提取的文字和文件名信息，存储在 `index.json` 中 |
| **测试目录** | 仓库根目录 `tests/`；按 `unit/`、`integration/`、`fixtures/` 分层，当前只规划目录结构，不代表已经引入测试框架或固定测试命令 |
| **按文件名同步的增量刷新** | 启动和 `/refresh` 时使用的 v1.0 同步策略：对新增图片先按格式执行图片无损压缩，再追加索引记录；删除已不存在图片的索引记录；文件名仍存在的图片不重新 OCR，不检测同名覆盖；删除记录后保持其他已有 id 稳定，允许临时编号空洞；新增图片按文件名升序处理，并优先复用最小空洞 id；新增图片 OCR 后按「去除所有空白字符的去重键」去重，与已有条目或其他新图同键时保留已有/靠前者、删除重复新图；OCR 无文字的新图移至 `meme_no_text/` 不进索引 |
| **关键词搜索** | 功能一：用户输入关键词，使用 rapidfuzz partial_ratio 对索引中的 OCR 文本做子串模糊匹配（阈值 >= 60），按分数降序返回 Top 10 表情包；不匹配文件名 |
| **AI 匹配** | 功能二：用户用自然语言描述，先通过 SiliconFlow Embedding API 做语义搜索，不设最低相似度阈值并取 Top 10，再经 DeepSeek 精排后返回；若精排失败、解析失败或返回 `0`，fallback 到 embedding Top 1 |
| **图片无损压缩** | 新增图片进入索引前的文件优化步骤；`/add`、启动同步和 `/refresh` 对新增的 `.jpg/.jpeg/.png/.webp/.gif` 尝试无损压缩，成功后覆盖原文件；`.bmp` 不压缩，其他扩展名不作为表情包处理 |
| **私聊** | v1.0 唯一支持的会话形态：授权 QQ 用户与 Bot 一对一对话；群聊消息不在 v1.0 范围内 |
| **授权用户** | v1.0 中允许使用 Bot 的 QQ 用户；可以配置一个或多个，`/help`、`/search`、`/ai`、`/add`、`/refresh` 都只对授权用户的私聊开放 |
| **授权用户列表** | 环境变量 `AUTHORIZED_USER_IDS` 声明的 QQ 号白名单，多个 QQ 号用英文逗号分隔，例如 `123456,987654` |
| **非授权用户** | 不在 `AUTHORIZED_USER_IDS` 中的 QQ 用户；v1.0 中其私聊消息会被静默忽略，只记录日志，不回复提示 |
| **群聊消息** | v1.0 不支持的会话形态；无论发送者是否在授权用户列表中，群聊消息都静默忽略，只记录日志 |
| **去重键** | OCR 文本去除所有空白字符（含半角/全角空格、制表符、换行）后的纯文本；用于在 `/add` 和 `sync_with_filesystem` 新增阶段判定「是否完全相同的图片」，实时计算不落盘 |
| **无文字目录** | `memes/` 同级的 `meme_no_text/` 目录；OCR 去除所有空白后为空的图片在此场景下不进入索引，被移动到该目录并由日志 warning 提示，本项目不处理该类表情包 |

### 技术组件

| 术语 | 定义 |
|------|------|
| **NapCatQQ** | QQ 协议端，基于 NTQQ 的 OneBot v11 实现，负责收发 QQ 消息 |
| **NoneBot2** | Python 异步聊天机器人框架，负责业务逻辑 |
| **DeepSeek-OCR** | 硅基流动上的视觉 OCR 模型（`deepseek-ai/DeepSeek-OCR`），通过 chat completions API 调用，用于从图片中提取文字 |
| **index.json** | 业务索引文件，采用 JSON 对象结构，保存每个当前索引 id 对应的文件名、OCR 文本和 `text_hash` |
| **原子索引更新** | 更新 `index.json`、`embeddings.json` 时先写临时文件，全部成功后再替换正式文件；失败时保留旧索引 |
| **embeddings.json** | AI 匹配必需的向量索引文件，采用 id 映射对象结构，保存每条索引文本对应的 embedding；首次建索引和 `/refresh` 时维护；`text_hash` 使用规范化 OCR 文本的 SHA-256 |
| **rapidfuzz** | Python 模糊字符串匹配库，用于关键词的非精确匹配 |
| **DeepSeek** | 大模型 API 提供商，用于 AI 匹配中的候选精排，不用于生成 embedding |
| **SiliconFlow** | Embedding API 提供商，用于生成用户描述和索引文本的向量；v1.0 默认模型为 `BAAI/bge-m3` |
| **授权校验模块** | `bot/auth.py`，从 `AUTHORIZED_USER_IDS` 环境变量读取白名单，提供 `is_authorized()` / `log_unauthorized()` 供各插件统一调用 |
| **全局路径与配置** | `bot/config.py`，通过 `Path(__file__).resolve().parent.parent` 定位项目根目录，导出 `PROJECT_ROOT`、`MEMES_DIR` 路径常量和 `read_session_timeout()` 会话超时读取函数 |

### 交互协议

| 术语 | 定义 |
|------|------|
| **OneBot v11** | 标准 QQ 机器人通信协议，NapCatQQ 与 NoneBot2 通过此协议通信 |
| **反向 WebSocket** | v1.0 的 OneBot 连接方式：NapCatQQ 主动连接 NoneBot2，NoneBot2 在 bot 容器中监听连接 |
| **/help** | 帮助命令；授权用户私聊发送 `/help` 或不以 `/` 开头的普通文本时，Bot 返回当前命令和简单用法；授权用户发送未知斜杠命令时，Bot 回复“未知命令”并附帮助摘要 |
| **/search** | 关键词搜索命令，后接关键词；同一授权用户同一时间只保留一个待处理会话，新 `/search` 或 `/add` 会覆盖旧状态，并向用户提示已取消上一条未完成操作 |
| **/ai** | AI 描述匹配命令，后接自然语言描述 |
| **/add** | 添加表情包命令，格式 `/add [目标命名]`；目标命名作为保存到 `memes/` 的文件名基名，不写入 OCR 文本；未提供目标命名时按发送时间和图片内容 hash 自动生成文件名；同一授权用户同一时间只保留一个待处理会话，新 `/add` 或 `/search` 会覆盖旧状态，并向用户提示已取消上一条未完成操作 |
| **/refresh** | 增量更新索引命令；v1.0 使用全局索引更新锁，同一时间只允许一个索引写入任务运行；新增图片会先按格式执行无损压缩；锁占用期间 `/help`、`/search`、`/ai`、`/add`、`/refresh` 暂时拒绝服务 |
