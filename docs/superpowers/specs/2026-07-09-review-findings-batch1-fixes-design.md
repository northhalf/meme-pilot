# 代码审查意见修复设计（第一批）

- 日期：2026-07-09
- 范围：F1 / F2 / F4 / F5 / F7 / F12（共 6 条）
- 依据：2026-07-09 代码审查评估结论（13 条发现逐条核实，全部真实）
- 第二批（F3 / F6 / F8 / F9 / F10 / F11 / F13）另行逐条确认，不在本 spec 范围

## 1. 背景与目标

一次代码审查提出 13 条发现，经源码与 PRD 逐条核实，全部为真实问题。本 spec 处理其中 6 条
"无决策、高确定"项；其余 7 条涉及部署 / 供应链 / 架构决策，留待第二批逐条确认。

修复目标：消除 F1 生产必崩、F2 用户锁死、F4 PRD 授权违规、F5 异常无回复、F7 关闭竞态、
F12 隐私泄漏，并补齐对应单元测试。

## 2. 修复项

### 2.1 F1 — 无文字图片在生产环境必定失败（HIGH）

**根因**：`_process_image_pipeline`（`bot/engine/index_manager.py:1498`）无条件调用
`embed(text)`；而 `OpenAIEmbeddingService.embed`（`openai_embedding.py:96-97`）与
`GoogleEmbeddingService.embed`（`google_embedding.py:80-81`）对空文本 `raise ValueError`。
故 OCR 返回空文本（无文字图）时 `embed("") -> ValueError -> EmbeddingError`，永远到不了：

- `_write_entry` 的 `if not text:` no_text 分支（`index_manager.py:1363`）；
- `_sync_phase2_add` 的 `if not text:` no_text 分支（`index_manager.py:1298`）。

后果：

- `/add` 无文字图：`EmbeddingError` 被 `meme_add.py:206` 捕获，回 "Embedding 服务不可用"
  并在 `:236` 删除图片，而非 PRD 要求的"未识别到文字，已移至 meme_no_text/"。
- `/refresh` 无文字图：`_sync_phase2_add` 用 `return_exceptions=True`（`:1278`），空文本图
  在 `:1285` 计入 `failed`，文件留在 `memes/`，每次刷新重复失败。

测试盲区：`MockEmbeddingProvider.embed`（`test_index_manager.py:195-196`）对空文本返回零向量
而非抛异常；`MockOcrProvider` 返回文件名 stem 永不为空；`test_add_result_no_text`（`:268`）
仅验 dataclass。no_text 路径既被掩盖也从未被测。

**修复**：在 `_process_image_pipeline` 的 embed 调用前插入空文本短路（位于去空白
`:1492-1494` 之后、provider 注入检查 `:1495-1496` 之前）：

```python
text = "".join(text.split())  # 现有：去除所有空白
if not text:
    return text, []  # 空文本不 embed，由下游 no_text 分支移图
if self._embedding_provider is None:
    raise EmbeddingError("Embedding 服务未注入")
embedding = await self._embedding_provider.embed(text)
```

安全性：两个 no_text 分支均只移图、不读取 embedding，`[]` 占位不会被使用。write worker 的
`if req.embedding is None` 检查（`:853`）对 `[]` 通过；`_write_entry` 在 `if not text:`
（`:1363`）即返回，`embedding` 形参未被解引用。

**测试增补**（`tests/unit/engine/test_index_manager.py`）：

1. 新增返回空串的 OCR provider（复用现有 `ConstantOcrProvider` 模式，传 `""`）。
2. `test_process_image_pipeline_empty_text`：空文本 -> 返回 `("", [])`，且 embed 不被调用
   （用抛 `AssertionError` 的 embed 间谍验证短路生效）。
3. `test_add_no_text_moves_file`：`add()` 空文本图 -> `AddResult(reason="no_text", moved_to=...)`，
   文件从 `memes/` 移入 `meme_no_text/`。
4. `test_refresh_no_text_moved`：`refresh()` 含一张无文字图 ->
   `SyncResult.no_text_moved == 1`，文件移入 `meme_no_text/`，不在 `failed`。

### 2.2 F2 — 搜索错误路径泄漏会话（HIGH，方案 B + 入口收口）

**根因**：`execute_search`（`bot/plugins/_search_utils.py:274`）三条错误分支在
`cmd_matcher.finish()` 前未 `deactivate_chat`：

- `:301` `get_index_manager()` 抛 `RuntimeError` -> "服务未就绪"
- `:309` `index_manager.search()` 抛 `asyncio.TimeoutError` -> "索引更新较慢"
  （读锁等满 30s 超时，`/refresh` 占写锁时的真实场景）
- `:313` 其它 `Exception` -> "搜索服务暂时不可用"

而 `handle_search`（`meme_search.py:76`）与 `handle_plain_text`（`meme_plain_text.py:78`）
的 `try` 仅 `except asyncio.CancelledError`，不捕 `FinishedException`。`activate_chat` 已在
`:61` / `:73` 置 `active=True`，超时任务仅在 `present_candidates`（多结果路径）创建，错误
路径不可达 -> 会话 `active=True` 残留、无超时任务 -> 后续命令均回"已有命令在处理中"，需
手动 `/cancel`。

范围确认：`execute_search` 仅被 `meme_search`、`meme_plain_text` 调用；`meme_sim` /
`meme_rand` 直接调 `dispatch_search_results` 且各自错误分支已正确 `deactivate`
（`meme_sim:87/94/99`、`meme_rand:87/92`），无泄漏。

**修复**（方案 B + 入口防御性收口，关闭残余泄漏）：

1. **源头修**（`_search_utils.py`）：`execute_search` 三条错误分支的 `finish` 前各加
   `session_manager.deactivate_chat(user_id)`。`_search_utils.py` 已 import `session_manager`
   （`:29`），`execute_search` 已有 `user_id`（`:294`）。与 `meme_sim` / `meme_rand` 的内联
   deactivate 模式一致。

   ```python
   except asyncio.TimeoutError:
       logger.info("用户 %s 的搜索等待读锁超时", user_id)
       session_manager.deactivate_chat(user_id)
       await cmd_matcher.finish("索引更新较慢，请稍后再试")
       return
   ```

2. **入口收口**（`meme_search.py` / `meme_plain_text.py`）：`handle_search` /
   `handle_plain_text` 的 `try` 在既有 `except asyncio.CancelledError` 之后追加两条，对齐
   `meme_add.py:240-256` 模式：

   ```python
   except FinishedException:
       session_manager.deactivate_chat(user_id)
       raise
   except Exception:
       logger.exception("用户 %s 的搜索处理异常", user_id)
       session_manager.deactivate_chat(user_id)
       raise
   ```

   覆盖 `dispatch_search_results` / `present_candidates` 在创建超时任务前抛非预期异常的残余
   泄漏。`FinishedException` 单独捕获（避免被 `except Exception` 当作错误记录日志），deactivate
   与源头修幂等；`except Exception` 捕获残余异常并记录、清理后 re-raise（NoneBot 正常收尾）。

**测试增补**：
- `tests/unit/plugins/test_search_utils.py`：mock `index_manager.search` 抛
  `asyncio.TimeoutError` -> 断言会话 `active=False`（源头修不泄漏）。
- `tests/unit/plugins/test_meme_search.py`：mock `dispatch_search_results` 抛非预期异常 ->
  断言会话 `active=False`（入口收口生效）。

### 2.3 F4 — /cancel 缺少授权校验（MEDIUM）

**根因**：`meme_cancel.py` `handle_cancel` 无 `is_authorized`，非授权用户收到"当前没有活跃的
会话"，违反 PRD §3.5（`:294` / `:474`）"非授权用户……静默忽略（仅记录日志，不回复提示）"。
无跨用户影响（按 user_id 隔离）。

**修复**：`handle_cancel` 开头加授权校验，对齐其它命令：

```python
if not is_authorized(user_id):
    log_unauthorized(user_id, "cancel")
    await matcher.finish(None)
    return
```

不影响 got handler 内的 `/cancel` 旁路（`got_intercept_bypass` -> `execute_cancel`，该路径已在
授权处理器内）。

**测试增补**（`tests/unit/plugins/test_meme_cancel.py`）：未授权用户 `/cancel` ->
`finish(None)` 静默、不调用 `execute_cancel`。

### 2.4 F5 — /info 未捕获异常（MEDIUM）

**根因**：`meme_info.py:53` `info = await index_manager.info()` 未包 try/except（仅
`get_index_manager()` 在 `:46-51` 被包）。`info()` 调 `get_all_entries`，DB 损坏 / 锁定时抛
异常 -> 无回复 + 日志刷栈。对比 `meme_refresh.py:67-79` 完整包裹。

**修复**：将 `:53` 包入 try/except，对齐 `meme_refresh` 模式。`/info` 无会话，不需
deactivate：

```python
try:
    info = await index_manager.info()
except Exception:
    logger.exception("获取索引信息失败")
    await matcher.finish("索引信息获取失败，请稍后再试")
    return
```

**测试增补**（`tests/unit/plugins/test_meme_info.py`）：mock `index_manager.info()` 抛异常 ->
回复"索引信息获取失败"，不抛未捕获异常。

### 2.5 F7 — _refresh_task 从不赋值，后台同步关闭时无法取消（MEDIUM，修复方案 ②）

**根因**：`_refresh_task`（`index_manager.py:399`）仅初始化为 `None`，从未赋值。
`bot.py:150` `asyncio.create_task(_background_sync(...))` 丢弃 task 引用。`close()`
（`:1103`）读取 `_refresh_task` 但永远为 `None` -> 不取消在跑的 refresh -> 随后关闭 stores，
refresh 仍持写锁操作已关闭的 sqlite / chroma -> 关闭竞态（日志报错，非损坏）。

**修复**（方案 ②，refresh 自注册任务）：`refresh()` 入口记录当前 task，`finally` 清空。
`refresh()` 已有 `try/finally`（`:816-824`）重置 `_refresh_active`，扩展为：

```python
self._refresh_active = True
self._refresh_task = asyncio.current_task()
try:
    ...
finally:
    self._refresh_active = False
    if self._refresh_task is asyncio.current_task():
        self._refresh_task = None
```

覆盖后台同步与 `/refresh` 两种调用场景（`current_task()` 分别为 `_background_sync` 任务与
NoneBot handler 任务）。`close()` 即可取消任意在跑 refresh，关闭竞态消除。`bot.py` 无需改动。

**测试增补**（`tests/unit/engine/test_index_manager.py`）：`test_close_cancels_running_refresh`
- monkeypatch 让 `refresh` 挂住 -> `close()` -> 断言 refresh task 被 cancel、stores 正常关闭
无异常。

### 2.6 F12 — .gitignore 漏掉运行时图片目录（MEDIUM）

**根因**：`.gitignore` 忽略 `memes/` 但漏 `memes_deleted/`、`memes_replaced/`、
`meme_no_text/`；三者均为 docker 卷挂载（`docker-compose.yml:66-68`），运行后 `git add .`
会把用户删除 / 替换 / 无文字的表情包提交进仓库（隐私泄漏）。

**修复**：`.gitignore` 追加：

```
memes_deleted/
memes_replaced/
meme_no_text/
```

## 3. 涉及文件

| 文件 | 修复项 | 改动 |
|---|---|---|
| `bot/engine/index_manager.py` | F1, F7 | 管道空文本短路；refresh 自注册 task |
| `bot/plugins/_search_utils.py` | F2 | execute_search 错误分支 deactivate |
| `bot/plugins/meme_search.py` | F2 | handle_search 追加 FinishedException/Exception 清理 |
| `bot/plugins/meme_plain_text.py` | F2 | handle_plain_text 追加 FinishedException/Exception 清理 |
| `bot/plugins/meme_cancel.py` | F4 | 加授权校验 |
| `bot/plugins/meme_info.py` | F5 | info() 包 try/except |
| `.gitignore` | F12 | 追加 3 个目录 |
| `tests/unit/engine/test_index_manager.py` | F1, F7 | 空文本路径 + close 取消 refresh 测试 |
| `tests/unit/plugins/test_search_utils.py` | F2 | 超时不泄漏会话测试 |
| `tests/unit/plugins/test_meme_search.py` | F2 | dispatch 异常入口收口测试 |
| `tests/unit/plugins/test_meme_cancel.py` | F4 | 未授权静默测试 |
| `tests/unit/plugins/test_meme_info.py` | F5 | info 异常不泄漏测试 |

## 4. 验证

- `uv run pytest tests/unit/ -v`（全量单元测试）
- `uv run python -m compileall bot tests`（语法检查）
- 仅 F12 为配置变更，其余涉代码均需测试通过。

## 5. 不在本 spec 范围（第二批，待逐条确认）

- F3 NapCat WebUI 默认暴露（部署上下文决策）
- F6 wait_for 超时不取消入队写任务（超时策略决策）
- F8 `_get_chroma_ids` 硬编码 1024（需带网络验证真实行为）
- F9 容器以 root 运行（UID / 卷权限决策）
- F10 docker-compose 硬编码开发者代理（代理去留决策）
- F11 镜像不固定 + CD 无审批 + Actions 非 SHA（供应链门禁决策）
- F13 engine 反向依赖 `bot.session`（解耦方案决策）

## 6. 风险与回滚

- **F1** 改动管道返回值（空文本返回 `[]`）：已核实两个 no_text 分支不读 embedding，无下游
  破坏；测试将显式验证 embed 不被调用。
- **F7** 改动 refresh task 生命周期：仅影响 `close()` 取消行为，正常路径不变。
- 其余为局部加 try / 授权 / 配置，风险低。
- **回滚**：每条修复独立，可按文件单独 revert。

## 7. 提交策略

- 按项目规则（`CLAUDE.md`）：禁止在 `main` 分支自行 `git add` / `commit`；本批修复在专用
  feature 分支上进行，提交经用户审核。
- 工作区当前已有 4 个插件文件（`meme_addtag.py` / `meme_delete.py` / `meme_edit.py` /
  `meme_setspeaker.py`）的既有未提交改动，与本批 6 项无文件重叠；提交时仅暂存本批相关文件，
  不触及既有改动。
