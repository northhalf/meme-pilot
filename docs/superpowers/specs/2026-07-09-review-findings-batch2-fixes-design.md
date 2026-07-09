# 代码审查意见修复设计（第二批）

- 日期：2026-07-09
- 范围：F3 / F6 / F8 / F10 / F11 / F13（共 6 条）
- 依据：2026-07-09 代码审查评估结论；第一批（F1/F2/F4/F5/F7/F12）已实现并审阅通过
- 决策来源：逐条 AskUserQuestion 由用户确认方案（见下文每项"方案"）
- **F9（容器以 root 运行）经用户决定删除，bot 继续以 root 运行，不在本批范围。**

## 0. 约束（沿用第一批）

- 不新建分支，直接在当前工作目录改；**禁止 git add / commit**（CLAUDE.md）。
- 实现用 subagent，main agent 与 subagent 均须用 sequential-thinking 推理。
- 不触碰既有未提交的 4 个插件文件（`meme_addtag.py` / `meme_delete.py` / `meme_edit.py` / `meme_setspeaker.py`）。
- 实现后更新 `docs/api/API.md`（如涉及对外接口变更：F8 VectorStore 新增 `get_all_ids`、F13 IndexInfo.status 语义变更）。

## 1. F8 - `_get_chroma_ids` 硬编码 1024（HIGH）

**根因**：`index_manager.py:1220` `hits = await self._vector_store.query([0.0] * 1024, n_results=n)`
用零向量召回全部 id。1024 与真实 embedding 维度强耦合：默认 `OPENAI_EMBEDDING_MODEL=embedding-3`（GLM，2048 维）、
`GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`（3072 维），1024 对两者均不符。chroma cosine collection 对维度不匹配的
query 会报错/异常 -> `_sync_phase0_consistency`（refresh 阶段0 跨库一致性）失败。仅 refresh 时触发，单测用
FakeVectorStore（不校验维度）掩盖--即记忆 T9。

**方案**（用户确认：改用 `collection.get()`）：用 chroma `collection.get(include=[])` 取全部 id（与维度无关），
在 VectorStore 暴露 `get_all_ids()`，`_get_chroma_ids` 改调它。`collection.get(include=[])` 返回 `{'ids': [...]}`
（context7 cookbook 确认：传空 include 仅取 id，高效），是标准 API，无需带网络验证即可确信正确。

**改动**：

1. `bot/engine/vector_store.py`：新增
   ```python
   def _get_all_ids_sync(self) -> set[int]:
       """同步取 collection 全部 id（内部持 _lock，id 转 int）。"""
       with self._lock:
           result = self._require_collection().get(include=[])
       return {int(i) for i in (result.get("ids") or [])}

   async def get_all_ids(self) -> set[int]:
       """返回 collection 中全部向量对应的 entry_id 集合；为空时返回空集。"""
       return await asyncio.to_thread(self._get_all_ids_sync)
   ```
2. `bot/engine/index_manager.py` `VectorStoreProtocol`（:240-258）：新增 `async def get_all_ids(self) -> set[int]: ...`。
   （勘误：`VectorStoreProtocol` 定义在 `index_manager.py`，非 `protocols.py`；`protocols.py` 仅含 EmbeddingProvider/
   MetadataEntryProvider/MetadataStoreProvider/VectorQueryProvider 四个 Protocol，F8 不改 `protocols.py`。因 `_vector_store`
   形参类型为 `VectorStoreProtocol`，须在此声明 `get_all_ids` 否则 pyright 报错。）
3. `bot/engine/index_manager.py:1210` `_get_chroma_ids`：整体替换为
   ```python
   async def _get_chroma_ids(self) -> set[int]:
       """获取 chroma 当前所有 id（用 collection.get() 取全量，与 embedding 维度无关）。

       Returns:
           chroma 中现存向量对应的 entry_id 集合；chroma 为空时返回空集。
       """
       return await self._vector_store.get_all_ids()
   ```
   （删除原 `count()==0` 短路与零向量 query--`get_all_ids` 内部对空 collection 自然返回空集。）

**测试增补**：
- `tests/unit/engine/test_vector_store.py`（真实 chroma）：`TestGetAllIds`
  - `test_get_all_ids_empty`：空 collection -> `set()`。
  - `test_get_all_ids_returns_all`：upsert id 1/2/42（任意短维度向量）-> `get_all_ids() == {1, 2, 42}`。
  - `test_get_all_ids_after_remove`：upsert 后 remove(2) -> `{1, 42}`。
- `tests/unit/engine/test_index_manager.py`：`FakeVectorStore` 补 `get_all_ids`（返回其持有 id 集合）；
  新增 `test_get_chroma_ids_uses_get_all_ids`：放入若干 id，`_get_chroma_ids()` 返回该集合，且 `query` 未被调用
  （spy 抛 AssertionError 验证不再走零向量 query）。

## 2. F6 - wait_for 超时不取消入队写任务（HIGH）

**根因**：`meme_add.py:187` `await asyncio.wait_for(index_manager.add(...), timeout=add_user_timeout)`。
`add()`（`index_manager.py:518`）流程：`_ensure_write_worker()` -> `_write_queue.put(req)` -> `return await future`。
wait_for 超时取消的是 `add()` 协程（CancelledError 落在 `await future`），但 `_WriteRequest` 已入队、future 是独立对象--
取消"等待"不删队列项、也不 cancel future。后果：worker 仍取出 req 执行 `_write_entry`，而 `meme_add.py:236` 已
unlink 图片 -> 写入一条指向已删文件的孤儿 DB 条目（或 `_write_entry` 因文件缺失抛异常、future 无人听）。

**方案**（用户确认：放弃写入+删图，超时判定移入 IndexManager.add 内部统一处理）：

1. `bot/engine/index_manager.py` `add()`（:518）：用 `async with asyncio.timeout(self.add_user_timeout)` 包裹
   全程（pipeline + TOCTOU + enqueue + await future）；`await future` 包 try/except，超时/取消时 cancel future，
   worker 据此跳过：
   ```python
   async with asyncio.timeout(self.add_user_timeout):
       text, embedding = await self._process_image_pipeline(filename)
       if self._shutting_down:
           raise IndexAddCancelledError("Bot 正在关闭")
       if self._refresh_active:
           raise RefreshInProgressError("索引正在批量刷新，请稍后再试")
       self._ensure_write_worker()
       future = asyncio.get_running_loop().create_future()
       await self._write_queue.put(
           _WriteRequest(op=WriteOp.ADD, future=future, filename=filename,
                         text=text, speaker=speaker, tags=tags, embedding=embedding)
       )
       try:
           return await future
       except asyncio.CancelledError:
           if not future.done():
               future.cancel()
           raise
   ```
   - 超时发生在 pipeline 阶段：未创建 future，CancelledError 直出 `async with` -> 转 `TimeoutError`，无孤儿。
   - 超时发生在 `await future`：cancel future 后 re-raise -> `async with` 转 `TimeoutError`；worker 见 `future.done()` 跳过。
   - `RefreshInProgressError` / `IndexAddCancelledError` 非 CancelledError，正常透出 `async with`（既有 slow_pipeline
     测试在 10s 抛错，远早于 60s 默认超时，不受影响）。
2. `bot/engine/index_manager.py` `_write_worker_loop`（:836）：`req = await self._write_queue.get()` 之后、
   `async with self._rwlock.write():` 之前插入
   ```python
   if req.future.done():
       # 已被取消/放弃的请求，跳过不写，避免孤儿写入
       continue
   ```
3. `bot/plugins/meme_add.py:187-190`：移除外层 `asyncio.wait_for`，改为
   `result = await index_manager.add(filename, speaker=speaker, tags=tags)`。
   `except asyncio.TimeoutError`（:197）保留（add 内部超时抛出）；meme_add 仍删图（:236）。`import asyncio` 仍需
   （:89 create_task、:96/246 CancelledError）。

**范围说明**：F6 仅修 `/add`（唯一有图片落盘+删图、会产生孤儿的路径）。`edit_text`/`set_speaker`/`add_tag`/`delete`
仍用插件层 wait_for（`meme_addtag:154` 等），其超时不会产生孤儿（操作既有条目、无图片落盘），保留现状。

**残余风险（文档化，不修）**：超时触发时若 worker 已进入 `_write_entry`（写操作本身慢 >60s，越过 done 检查），
future 已 cancel 但写仍在进行--`_write_entry` 完成、`if not req.future.done()` 跳过 set_result，但条目已写入，
随后 meme_add 删图 -> 窄口孤儿。此为写操作自身卡顿的极端场景，靠 `/refresh` 阶段0/阶段2 跨库一致性兜底。
（与第一批 F2 残余同口径，用户已接受文档化残余。）

**测试增补**（`tests/unit/engine/test_index_manager.py`）：
- `test_add_timeout_cancels_enqueued_future`：设 `add_user_timeout=0.1`，monkeypatch `_write_entry` 挂在 Event 上
  不返回；`add("x.jpg")` 在 ~0.1s 抛 `asyncio.TimeoutError`；随后 `close()` 清理挂住 worker。
- `test_write_worker_skips_cancelled_future`：手动构造一个 `future` 已 `cancel()` 的 `_WriteRequest` 入队，
  再入一个正常 req；启动 worker；断言 `_write_entry`（spy）对 cancelled req 未被调用、对正常 req 被调用。
- 既有 `test_meme_add.py:902`（mock `im.add` 抛 `TimeoutError`）无需改（仍验证 meme_add 捕获 TimeoutError + 删图）；
  `test_meme_add.py:78` `im.add_user_timeout = 60.0` 移除 wait_for 后成死赋值，可删（低优先）。

## 3. F13 - engine 反向依赖 `bot.session`（MEDIUM）

**根因**：`index_manager.py:18` `from bot.session import session_manager`，仅用于 `info()`（:790）
`elif session_manager.has_active_session(): status = "正在处理命令"`。engine（纯库层）反向 import 应用层 bot.session，
违反分层、增加单测隔离难度。

**方案**（用户确认：状态由插件层覆写）：

1. `bot/engine/index_manager.py`：删除 `:18` `from bot.session import session_manager`；`info()`（:788-793）
   状态分支改为
   ```python
   if self._refresh_active:
       status = "正在刷新索引"
   else:
       status = "空闲"
   ```
2. `bot/plugins/meme_info.py`：`info = await index_manager.info()` 之后、组装回复之前，插入应用层状态覆写
   ```python
   if info.status == "空闲" and session_manager.has_active_session():
       info.status = "正在处理命令"
   ```
   并补 `from bot.session import session_manager`（meme_info 当前未 import）。IndexInfo 是 `@dataclass`（:178），可变。

**测试调整**：
- `tests/unit/engine/test_index_manager_info.py:184` `test_info_status_processing`：engine 不再感知 session，
  改为 `test_info_status_decoupled_from_session`--激活会话后 `info().status == "空闲"`（证明解耦），deactivate 后仍 "空闲"。
  删除原对 "正在处理命令" 的断言。
- `tests/unit/plugins/test_meme_info.py`：新增 `test_info_overrides_status_when_session_active`--mock `info()` 返回
  `status="空闲"` + `session_manager` 有活跃会话 -> 回复含 "正在处理命令"；及 `status="空闲"` 无活跃会话 -> "空闲"。
  （既有 :159 mock `info()` 返回 "正在处理命令" 的用例：覆写条件 `status=="空闲"` 不触发，原值透传，仍通过。）

## 4. F3 - NapCat WebUI 默认暴露（MEDIUM）

**根因**：`docker-compose.yml:14` `ports: - "6099:6099"`（全接口暴露到宿主）；`entrypoint.sh` 生成 webui.json
`host:"::"`、`disableWebUI:false`、`accessControlMode:"none"`、`token:memepilot`（弱默认且在公开仓库
`.env.example:42`）。WebUI 可登录 QQ/改配置，暴露+弱 token=可被扫到后接管。

**方案**（用户确认：绑回环 127.0.0.1）：仅改 compose 端口绑定，远程管理走 SSH 隧道。

- `docker-compose.yml:13-14`：`- "6099:6099"` -> `- "127.0.0.1:6099:6099"`。
  说明：webui.json 内 `host:"::"` 是容器内监听，必须保留（否则端口转发到容器 eth0 不可达）；宿主侧绑 127.0.0.1
  即控制暴露面。token 弱默认因已仅本机可达，风险降级，不在本项范围（用户未选加固 token）。
- `README.md:243-246`：将 `http://服务器IP:6099/webui` 改为本机 `http://127.0.0.1:6099/webui`，并补注"远程访问请用
  SSH 端口转发 `ssh -L 6099:127.0.0.1:6099 服务器`"。

## 5. F10 - docker-compose 硬编码开发者代理（MEDIUM）

**根因**：`docker-compose.yml:27-29` build args `http_proxy=http://127.0.0.1:10808` / `https_proxy=...`
为开发者本机代理，换机器/他人 `docker compose build` 会走不可达代理失败（CI 不经 compose、不受影响）。

**方案**（用户确认：移到 override.yml，dev 专用、gitignore）：

- `docker-compose.yml`：删除 bot 服务的 `build.args`（http_proxy/https_proxy 两行）。
- 新增 `docker-compose.override.yml.example`（提交）：
  ```yaml
  # 开发者本地构建加速代理（复制为 docker-compose.override.yml 后按需修改/删除）
  # docker compose 会自动加载 docker-compose.override.yml
  services:
    bot:
      build:
        args:
          - http_proxy=http://127.0.0.1:10808
          - https_proxy=http://127.0.0.1:10808
  ```
- `.gitignore`：加 `docker-compose.override.yml`（个人 dev，不入库）。
- `README.md`：补"本地构建需代理时 `cp docker-compose.override.yml.example docker-compose.override.yml`"。

## 6. F11 - CD 无审批门（MEDIUM）

**根因**：`cd.yml` push-to-main 即自动构建推送 Docker Hub，无人工审批。镜像/Actions 锁定不在本项范围
（用户确认：仅加 CD 审批）。

**方案**（用户确认：仅加 CD 审批）：

- `.github/workflows/cd.yml` `publish` job（:58）加 `environment: deploy`：
  ```yaml
  publish:
    needs: test
    runs-on: ubuntu-latest
    environment: deploy        # 新增：发布需审批（须在 GH 仓库 Settings>Environments 配置 required reviewers）
  ```
  `test` job 不加（测试自动跑）。
- `README.md`：补一次性配置说明--在 GitHub 仓库 Settings -> Environments -> New environment "deploy" ->
  Required reviewers 添加审批人；此后 push 到 main 触发 test，publish 须人工 approve 才发版。
- 注意如实告知：`environment: deploy` 仅在 GH 后台配了 required reviewers 时才生效；未配则等同无门（仅打标签）。

## 7. 涉及文件

| 文件 | 修复项 | 改动 |
|---|---|---|
| `bot/engine/vector_store.py` | F8 | 新增 `get_all_ids` / `_get_all_ids_sync` |
| `bot/engine/index_manager.py` | F8, F6, F13 | `VectorStoreProtocol` 加 `get_all_ids` 声明；`_get_chroma_ids` 改 `get_all_ids`；`add()` 内部超时+cancel future；`_write_worker_loop` 跳过 done future；`info()` 去耦合 + 删 `bot.session` import |
| `bot/plugins/meme_add.py` | F6 | 移除外层 wait_for |
| `bot/plugins/meme_info.py` | F13 | 加 session_manager import + 状态覆写 |
| `docker-compose.yml` | F3, F10 | 6099 绑回环；删代理 build args |
| `docker-compose.override.yml.example` | F10 | 新增（提交） |
| `.gitignore` | F10 | 加 `docker-compose.override.yml` |
| `.github/workflows/cd.yml` | F11 | `publish` 加 `environment: deploy` |
| `README.md` | F3, F10, F11 | WebUI 本机访问/SSH 隧道；override 用法；CD 审批配置 |
| `docs/api/API.md` | F8, F13 | VectorStore.get_all_ids 接口；IndexInfo.status 语义（engine 仅刷新中/空闲，命令态由插件层覆写） |
| `tests/unit/engine/test_vector_store.py` | F8 | `TestGetAllIds`（真实 chroma） |
| `tests/unit/engine/test_index_manager.py` | F6, F8 | FakeVectorStore.get_all_ids；`_get_chroma_ids` 用 get_all_ids；add 超时 cancel；worker 跳过 done |
| `tests/unit/engine/test_index_manager_info.py` | F13 | 改 `test_info_status_processing` 为解耦断言 |
| `tests/unit/plugins/test_meme_info.py` | F13 | 新增状态覆写测试 |

## 8. 验证

- `uv run pytest tests/unit/ -v`（全量单元测试）
- `uv run python -m compileall bot tests`（语法检查）
- F8 真实 chroma 测试在 `test_vector_store.py` 覆盖（无网络也可，chromadb 本地 PersistentClient）。
- F3/F10/F11 为部署/配置变更，无单测；`docker compose config` 校验 compose 合法性（如环境允许）。

## 9. 风险与回滚

- **F6** 改 `add()` 超时语义：既有 slow_pipeline 测试（10s 抛错 < 60s）不受影响；test_meme_add 全用 mock add 直抛，
  不依赖真实 wait_for。残余窄口孤儿已文档化。回滚：还原 `add()` + 删 worker done 检查 + 恢复 meme_add wait_for。
- **F8** `_get_chroma_ids` 改 `get_all_ids`：`get(include=[])` 为标准 API，行为确定；FakeVectorStore 须同步补方法。
- **F13** 删 engine 对 bot.session 依赖：仅 `/info` 状态显示语义迁移到插件层；engine 测试须同步改。
- **F3/F10/F11** 均可单文件 revert。
- 回滚均不涉及 git 提交（本批不提交）。

## 10. 实现策略

- subagent 并行：按文件不相交分组（F8 跨 vector_store.py 与 index_manager.py 两文件——`VectorStoreProtocol` 与 `_get_chroma_ids`
  均在 `index_manager.py`，非 `protocols.py`，可拆；
  F6+F8+F13 均改 `index_manager.py`，须由同一 subagent 串行处理避免冲突）。
- 每个 subagent 用 sequential-thinking 推理后再改。
- main agent 汇总后跑 `compileall` + `pytest` 验证，再交用户审阅（不提交）。
