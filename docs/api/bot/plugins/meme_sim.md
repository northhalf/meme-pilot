# bot/plugins/meme_sim.py — /sim 命令插件

> NoneBot2 命令插件，无对外 Python API。核心搜索逻辑委托 `IndexManager.semantic_search`，结果分发委托 `_search_utils.py`。

## 注册

```python
sim_cmd = on_command("sim", rule=to_me(), priority=5, block=True)
```

## 处理函数

```python
async def handle_sim(bot: Bot, event: MessageEvent, matcher: Matcher) -> None
```

```python
async def got_sim_selection(bot: Bot, event: MessageEvent, matcher: Matcher, selection_msg: Message = Arg("selection")) -> None
```

## 依赖

- `auth.is_authorized()` — 授权校验
- `app_state.get_index_manager()` — 获取 IndexManager 单例
- `IndexManager.semantic_search(description)` — 语义搜索入口（锁外 embed，持读锁查询 VectorStore）
- `_search_utils.dispatch_search_results()` — 统一结果分发（空/单/多结果分支）
- `_search_utils.handle_got_selection()` — got 选择编号共享逻辑
- `bot.session.session_manager` — 会话管理（activate_chat / deactivate_chat）

## 流程

### handle_sim

1. 授权校验
2. 会话互斥检查（`session_manager.activate_chat`，不覆盖旧会话）
3. 提取描述文本（去除 `/sim` 前缀）
4. 空描述文本检查：回复 "/sim <描述文本>"
5. 获取 IndexManager
6. 调用 `IndexManager.semantic_search(description)` 执行语义搜索（锁外 embed，内部持读锁）
7. 空结果分支：回复"没有找到匹配的表情包 🙁"
8. 调用 `dispatch_search_results()` 统一分发结果

### got_sim_selection

薄包装，委托 `_search_utils.handle_got_selection(bot, event, matcher, selection_msg, "/sim")` 处理。详见 `docs/api/bot/plugins/_search_utils.md`。

## 选择列表格式

```
找到多个匹配的表情包，请选择：
1. 加班到凌晨三点的我 -- 23, 小明, 吐槽, 加班
...
回复编号即可 (1-10)
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 非授权用户 | 静默忽略（仅日志） |
| 已有活跃会话 | 提示"已有命令在处理中，请先 /cancel" |
| 缺少描述文本 | 回复 "/sim <描述文本>" |
| IndexManager 未初始化 | 提示"服务未就绪，请稍后再试" |
| 读锁等待超时 | 提示"索引更新较慢，请稍后再试" |
| embedding 零向量 | 提示"AI 服务暂时不可用，稍后重试" |
| 语义搜索异常 | 提示"AI 服务暂时不可用，稍后重试"，记录异常日志 |
