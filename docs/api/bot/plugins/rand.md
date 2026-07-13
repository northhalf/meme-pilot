# bot/plugins/rand.py — /rand 命令插件

> NoneBot2 命令插件，无对外 Python API。核心搜索逻辑委托 `IndexManager.random_search`，结果分发委托 `_search_utils.py`。

## 注册

```python
rand_cmd = on_command("rand", rule=to_me(), priority=5, block=True)
```

## 处理函数

```python
async def handle_rand(bot: Bot, event: MessageEvent, matcher: Matcher) -> None
```

```python
async def got_rand_selection(bot: Bot, event: MessageEvent, matcher: Matcher, selection_msg: Message = Arg("selection")) -> None
```

## 依赖

- `auth.is_authorized()` — 授权校验
- `app_state.get_index_manager()` — 获取 IndexManager 单例
- `IndexManager.random_search(keyword)` — 随机搜索入口，有关键词时在关键词搜索结果中随机，无关键词时全库随机
- `_search_utils.dispatch_search_results()` — 统一结果分发（空/单/多结果分支）
- `_search_utils.present_candidates()` — 展示候选列表并创建选择会话
- `_search_utils.resolve_selection()` — 解析用户选择编号
- `_search_utils.got_intercept_bypass()` — /help 和 /cancel 旁路拦截
- `bot.session.session_manager` — 会话管理（activate_chat / deactivate_chat）

## 流程

### handle_rand

1. 授权校验
2. 会话互斥检查（`session_manager.activate_chat`，不覆盖旧会话）
3. 提取关键词（去除 `/rand` 前缀；无关键词时 `keyword = None` 表示全库随机）
4. 获取 IndexManager
5. 调用 `IndexManager.random_search(keyword)` 执行随机搜索
6. 空结果分支：有关键词时回复"没有匹配到任何表情包 🙁"，无关键词时回复"表情包目录为空，请先添加图片并执行 /refresh"
7. 保存 `keyword` 到 `matcher.state`，供换一批复用
8. 调用 `dispatch_search_results()` 统一分发结果，prompt 后缀为"回复 0 换一批"

### got_rand_selection

1. `handler_context` 上下文管理器
2. `/help` 和 `/cancel` 旁路拦截
3. 选择会话时效性检查
4. **回复 `0`：换一批**
   - 从 `matcher.state["keyword"]` 复用关键词
   - 调用 `IndexManager.random_search(keyword)` 再次随机
   - 空时回复"没有更多表情包了 🙁"
   - 调用 `present_candidates()` 展示新一批
5. **非 `0`：解析选择编号**
   - 调用 `resolve_selection()` 解析编号
   - 发送对应表情包图片 + 元数据文本行
   - `session_manager.remove_selection()` 清理选择会话

## 选择列表格式

```
找到多个匹配的表情包，请选择：
1. 加班到凌晨三点的我 -- 23, 小明
...
10. 周日晚上的加班通知 -- 45, 无
回复编号即可 (1-10)
回复 0 换一批
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 非授权用户 | 静默忽略（仅日志） |
| 已有活跃会话 | 提示"已有命令在处理中，请先 /cancel" |
| IndexManager 未初始化 | 提示"服务未就绪，请稍后再试" |
| 读锁等待超时 | 提示"索引更新较慢，请稍后再试" |
| 随机搜索异常 | 提示"搜索服务暂时不可用，稍后重试"，记录异常日志 |
| 选择会话已过期 | 提示"选择已过期，请重新搜索" |
