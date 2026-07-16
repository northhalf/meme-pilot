# 删除 AI 精排、创建合集与镜像部署设计

> 日期：2026-07-16  
> 状态：已批准，待实施计划  
> 范围：删除 `/ai` 与 DeepSeek 精排链路；新增 `/collection create`；默认 Compose 改为拉取发布镜像

## 1. 背景与目标

当前 `/ai` 命令先通过 Embedding 召回候选，再调用 DeepSeek LLM 精排。该链路增加了外部凭证、网络调用、启动依赖和维护成本。项目已有 `/sim` 提供纯 Embedding 语义检索，因此本次完整删除 `/ai` 命令及其专用匹配、精排实现。

合集能力目前支持扫描目录、切换合集和跨合集移动，但缺少在聊天中创建空合集的入口。本次新增仅限私聊的 `/collection create <名称>`，同时创建或登记 `memes/` 下的一级目录和 SQLite 合集记录。

默认 `docker-compose.yml` 当前从本地 Dockerfile 构建 Bot。本次改为拉取 `northhalf/meme-pilot:latest`，并另行提供完整独立的本地构建 Compose 文件。

## 2. 已确认决策

1. 完整删除 `/ai` 命令，不保留兼容别名，也不改造成 `/sim`。
2. 删除整个 `AIMatcher` 和 `RerankService` 运行时链路，不保留未使用代码。
3. `/sim`、Embedding 和 OpenAI 兼容 OCR 保持不变。
4. 创建命令采用 `/collection create <名称>`，无短命令。
5. 创建命令仅限授权用户私聊；群聊中 @bot 调用时回复“此命令仅限私聊使用”。
6. 创建操作直接执行，不需要二次确认。
7. 创建成功后保持当前合集不变，用户需要自行执行 `/switch`。
8. 已存在但尚未登记的安全普通目录可以直接登记；目录内图片留待 `/refresh` 建立索引。
9. 默认 `docker-compose.yml` 改为拉取发布镜像；新增完整独立的 `docker-compose.build.yml` 供本地构建。
10. 不增加第三方依赖，不修改数据库 Schema。

## 3. 删除 AI 精排与 `/ai`

### 3.1 删除范围

删除以下运行时能力：

- `/ai` NoneBot 插件及命令注册；
- AI matcher 的候选、结果和匹配逻辑；
- DeepSeek Rerank 服务、Prompt、响应解析、重试和并发控制；
- `IndexManager` 中的 `ai_match`、`ai_match_for_scope` 及相关内部方法和注入字段；
- `bot.py` 中 Rerank/AIMatcher 初始化和注入；
- `app_state.py` 中 AIMatcher 全局实例、初始化参数和 getter；
- engine 包对 AIMatcher、候选类型、结果类型和 RerankService 的导出；
- 对应单元测试和真实 API 集成测试；
- CI 中仅用于精排的 DeepSeek Secret 注入。

删除以下配置：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `RERANK_CONCURRENCY`

### 3.2 保留范围

- `/sim` 继续提供自然语言 Embedding 语义检索和候选选择。
- `EMBEDDING_PROVIDER` 及 OpenAI/Google Embedding provider 保持不变。
- `OCR_PROVIDER=deepseek` 仍表示 OpenAI 兼容视觉 OCR provider；它使用 `OPENAI_OCR_*` 配置，与被删除的 DeepSeek 精排配置无关。
- Python `openai` 依赖继续保留，因为 OpenAI 兼容 Embedding 和 OCR 仍使用该 SDK。
- ChromaDB、向量写入和合集过滤保持不变。

### 3.3 用户可见行为

- 私聊和群聊帮助文本均不再展示 `/ai`。
- 用户发送 `/ai ...` 时，由现有兜底插件作为未知命令处理，并返回“未知命令”及当前会话类型对应的帮助摘要。
- `/sim` 的命令格式、权限、分页和错误提示不变。

## 4. 创建合集命令

### 4.1 命令协议

```text
/collection create <名称>
```

约束：

- 仅授权用户私聊可用；
- 无短命令；
- 仅支持 `create` 子命令；
- `/collection` 缺少参数、未知子命令或缺少名称时回复：

```text
用法：/collection create <名称>
```

- `/collection` 不提供列表子命令；查看合集继续使用 `/switch`。
- 命令参与现有聊天会话互斥；已有活跃会话时回复“已有命令在处理中，请先 /cancel”。
- 命令在所有成功、失败和取消路径中清理会话状态。

### 4.2 名称校验

输入先去除首尾空白，再执行统一领域校验。合法名称必须满足：

- 非空；
- 不包含任何内部 Unicode 空白字符；
- 不是 `.` 或 `..`；
- 不以 `.` 开头；
- 不包含 `/`、`\` 或 NUL；
- 不是保留显示名 `全局` 或 `全部合集`；
- 未被 SQLite 中的普通合集使用；
- 可以安全映射为 `memes/` 下的单层 Linux 目录名。

允许中文、英文字母、数字和其他满足上述约束的安全字符。不额外引入 ASCII-only 规则。

名称校验应放在 engine 领域层，由插件、创建流程和需要相同规则的后续代码复用；插件层不自行复制规则。

### 4.3 引擎边界

插件只负责：

1. 授权检查；
2. 私聊限制；
3. 会话激活；
4. 解析 `create` 子命令和原始名称；
5. 调用 `IndexManager.create_collection()`；
6. 将领域错误转换为用户提示；
7. 清理会话状态。

目录和 SQLite 的一致性由 `IndexManager` 负责。创建操作进入现有 Write Worker，并在写锁内与 `/refresh`、`/add`、`/move` 等写操作串行。

为此扩展现有写入模型：

- `WriteOp` 增加创建合集操作；
- `_WriteRequest` 增加合集名称字段，并让 future 类型包含创建结果；
- 增加不可变的创建结果数据类型，至少携带 `MemeCollection` 和“是否登记已有目录”标记。

### 4.4 原子创建流程

Write Worker 取得写锁后执行：

1. 再次校验规范化名称，避免排队期间状态变化。
2. 查询 SQLite：若名称已登记，抛出重名领域错误，并携带已有合集编号。
3. 以 `memes_dir / name` 计算目标路径，并检查路径类型：
   - 路径不存在：创建单层目录，并标记为“本次新建”；
   - 路径存在且为非符号链接的普通目录：允许登记，标记为“已有目录”；
   - 路径是文件、符号链接或其他类型：抛出路径冲突领域错误。
4. 调用 `MetadataStore.create_collection(name)`；继续使用现有“复用最小正整数空号”的编号规则。
5. SQLite 登记失败时执行补偿：
   - 仅对本次新建且仍为空的目录调用 `rmdir`；
   - 绝不删除用户原本已有的目录；
   - 清理失败时记录高优先级日志。此时 SQLite 没有合集记录，残留目录可在下次命令中被安全登记。
6. 返回创建结果。

该流程不扫描目录、不 OCR、不生成 Embedding，也不自动刷新。登记已有目录后，其中图片仍由后续 `/refresh` 按现有规则入库。

### 4.5 并发与取消

- Bot 关闭或刷新已开始时，创建请求沿用现有写操作错误语义。
- 创建请求通过 Write Worker 排队；刷新开始前会等待已排队写操作排空。
- 在 Write Worker 尚未开始处理前取消 future，可安全跳过。
- 实际目录和 SQLite 操作均为短同步操作；一旦开始，在事件循环重新取得控制前完成或补偿，不留下半完成状态。

### 4.6 成功回复

新建目录并登记：

```text
合集创建完成 ✅
编号：3
名称：新三国
```

登记已有目录：

```text
合集创建完成 ✅
编号：3
名称：新三国
已登记现有目录；目录中的图片请执行 /refresh 建立索引
```

创建成功后不修改当前 `ChatScope` 的合集选择。

### 4.7 错误回复

| 场景 | 用户提示 |
| --- | --- |
| 缺参数、未知子命令 | `用法：/collection create <名称>` |
| 名称非法 | 提示名称不能为空、不能包含空白或路径字符，且不能使用保留名 |
| 合集已登记 | `表情包合集已存在：<名称>（<编号>）` |
| 同名路径为文件、符号链接或其他类型 | `无法创建合集：同名路径不是可用目录` |
| 刷新占用写入 | `索引正在刷新，请稍后再试` |
| 服务尚未初始化 | `服务未就绪，请稍后再试` |
| 文件系统、SQLite 或补偿异常 | `合集创建失败，请检查日志后重试` |

所有底层异常记录完整日志，但不向用户暴露绝对路径、SQL 或堆栈。

## 5. Docker Compose 设计

### 5.1 默认发布部署

根目录 `docker-compose.yml` 作为发布部署入口。Bot 服务使用：

```yaml
image: northhalf/meme-pilot:latest
pull_policy: always
```

并执行以下调整：

- 删除 `build`；
- 保持 NapCat 服务、容器名、重启策略、依赖、卷、网络、内存限制和线程治理配置不变；
- 删除 `DEEPSEEK_*` 和 `RERANK_CONCURRENCY` 环境变量；
- 保持 OCR、Embedding、图片优化和超时相关变量不变。

当前 Docker Compose 文档说明，`latest` 标签即使在默认 `missing` 策略下也会被拉取。本项目仍显式设置 `pull_policy: always`，使“每次启动检查发布镜像”的运维意图明确。

默认启动命令保持：

```bash
docker compose up -d
```

### 5.2 本地构建部署

新增完整独立的 `docker-compose.build.yml`：

- 包含完整 NapCat 和 Bot 服务配置；
- Bot 保留当前 `build.context`、`build.network` 和 `bot/Dockerfile`；
- 本地镜像使用 `meme-pilot:local`；
- 不设置发布镜像的 `pull_policy`。

启动方式：

```bash
docker compose -f docker-compose.build.yml up -d --build
```

将现有 `docker-compose.override.yml.example` 重命名为 `docker-compose.build.override.yml.example`。需要代理时复制为 `docker-compose.build.override.yml`，并显式与构建版组合：

```bash
cp docker-compose.build.override.yml.example docker-compose.build.override.yml
docker compose \
  -f docker-compose.build.yml \
  -f docker-compose.build.override.yml \
  up -d --build
```

删除“默认自动加载 override”的说明，避免默认发布部署意外混入 `build`。

## 6. 文档与配置同步

更新以下当前文档和配置：

- `README.md`
- `README-containers.md`
- `docs/PRD.md`
- `CONTEXT.md`
- `.env.example`
- `docker-compose.yml`
- 新增 `docker-compose.build.yml`
- 本地构建代理示例及其使用说明
- `.github/workflows/ci.yml`
- 私聊帮助文本及对应测试

文档调整包括：

- 删除 `/ai`、AI 匹配、LLM 精排、DeepSeek 精排凭证和 Rerank 并发说明；
- 新增 `/collection create` 的命令说明、权限、名称规则和已有目录行为；
- 默认部署改为拉取 Docker Hub 镜像；
- 补充独立本地构建命令；
- 隐私说明不再声称候选 OCR 文本发送给 DeepSeek；
- 集成测试 Secret 列表删除 `DEEPSEEK_API_KEY`。

历史 specs 和 plans 是过去决策记录，不回写修改。

## 7. 测试设计

### 7.1 删除链路验证

- 删除 RerankService 单元测试和真实 API 测试；
- 删除 AIMatcher 单元测试和集成测试；
- 删除 `/ai` 插件测试；
- 更新 `IndexManager`、`app_state`、帮助文本等测试，不再构造或断言 AIMatcher；
- 使用文本搜索确认运行时代码、当前文档、CI 和 Compose 中不存在被删除的精排配置；
- 确认 OpenAI 兼容 Embedding/OCR 测试仍保留并通过。

### 7.2 名称校验测试

覆盖：

- 中文、英文、数字及安全字符；
- 首尾空白被去除；
- 空名；
- 内部 ASCII/Unicode 空白；
- `.`、`..`、隐藏名；
- `/`、`\`、NUL；
- `全局`、`全部合集`；
- 已登记重名。

### 7.3 IndexManager 创建测试

覆盖：

- 不存在目录：建目录并登记；
- 已存在普通目录：只登记，不删除、不刷新；
- 同名文件、符号链接和特殊路径类型：拒绝；
- SQLite 创建失败：删除本次新建的空目录；
- SQLite 创建失败：已有目录保持不变；
- 补偿清理失败：记录异常并返回通用失败；
- 编号复用最小空洞；
- 当前合集选择不变；
- 与刷新和其他 Write Worker 操作串行；
- 关闭状态和刷新状态返回现有领域错误。

### 7.4 插件测试

覆盖：

- 授权用户私聊成功；
- 非授权用户静默忽略；
- 群聊调用返回仅限私聊；
- 缺参数、未知子命令、非法名称；
- 新目录和已有目录的成功回复；
- 重名、路径冲突、刷新占用、未初始化和通用异常；
- 不调用切换合集；
- 成功和异常路径均清理会话；
- 私聊帮助新增 `/collection create`，群聊帮助不展示该命令；
- `/ai` 进入未知命令兜底。

### 7.5 项目验证命令

```bash
uv run pytest tests/unit/ -v
uv run ruff check .
uv run ty check
uv run python -m compileall bot tests
docker compose --env-file .env.example -f docker-compose.yml config
docker compose --env-file .env.example -f docker-compose.build.yml config
```

实现完成后还需运行项目端到端验证，实际驱动受影响的命令路径，而不只依赖静态检查和单元测试。

## 8. 性能与依赖

- 创建合集不执行目录递归扫描或网络调用。
- 除 Write Worker 排队时间外，目标完成时间约为 100 ms 以内。
- 不新增 Python 第三方库、LSP、系统工具或格式化工具。
- 继续使用项目现有 Python 3.12、`pytest`、`ruff` 和 `ty` 环境。
- 默认部署要求当前 Docker Compose 插件支持 `pull_policy`。

## 9. 非目标

本次不实现：

- 合集重命名；
- 合集删除；
- 创建后自动切换；
- 创建后自动 `/refresh`；
- `/collection list`；
- Web 管理界面；
- 数据库 Schema 变更；
- OCR、Embedding 或 Chroma 架构重构；
- 对历史设计和计划文档的追溯修改。

## 10. 验收标准

1. Bot 启动不再要求任何 DeepSeek 精排凭证。
2. `/ai` 不再注册，并按未知命令处理。
3. `/sim` 及 Embedding/OCR 功能保持正常。
4. 授权用户可在私聊中通过 `/collection create <名称>` 创建或登记合集。
5. 合集目录和 SQLite 记录在成功、失败和并发场景下保持一致。
6. 创建成功后当前合集选择不变。
7. 默认 `docker compose up -d` 拉取并运行 `northhalf/meme-pilot:latest`。
8. 开发者可通过 `docker-compose.build.yml` 完整本地构建并启动。
9. 当前用户文档、配置模板、CI 和帮助文本不再包含被删除的精排功能。
10. 单元测试、ruff、ty、compileall 和两个 Compose 配置校验全部通过。
