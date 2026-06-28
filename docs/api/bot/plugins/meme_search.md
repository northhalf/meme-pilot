# bot/plugins/meme_search.py — /search 命令插件（薄包装）

> NoneBot2 命令插件，无对外 Python API。核心搜索逻辑委托给 `_search_utils.py`。

## 注册

```python
search_cmd = on_command("search", rule=to_me(), priority=5, block=True)
```

## 处理函数

```python
async def handle_search(bot: Bot, event: MessageEvent, matcher: Matcher) -> None
```

```python
async def got_selection(bot: Bot, event: MessageEvent, matcher: Matcher, selection_msg: Message = Arg("selection")) -> None
```

## 依赖

- `auth.is_authorized()` — 授权校验
- `_search_utils.execute_search()` — 核心搜索逻辑（锁、索引空、搜索、结果分支、session 注册和超时）
- `_search_utils.handle_selection()` — 处理用户选择编号（返回 `SearchResult` 或错误消息字符串）
- `session.check_and_cancel()` / `cancel()` / `is_cancelled()` — 会话管理

## 流程

### handle_search

1. 授权校验
2. 会话覆盖检查 (`check_and_cancel`)
3. 提取关键词（去除 `/search` 前缀）
4. 空关键词检查
5. 调用 `execute_search(bot, event, matcher, keyword)` 委托核心逻辑

### got_selection

1. 检查会话是否已取消
2. 调用 `handle_selection(matcher, candidates, text)`：
   - 返回 `SearchResult` → 发送对应图片，清理会话
   - 返回 `str`（错误消息）→ reject 提示重输
3. `FinishedException` / `RejectedException` 透传不捕获

核心搜索逻辑（锁检查、索引空检查、KeywordSearcher 调用、结果分支、session 注册、超时任务）全部在 `_search_utils.execute_search()` 中实现。详见 `docs/api/bot/plugins/_search_utils.md`。

## 选择列表格式

```
找到多个匹配的表情包，请选择：
1. 加班到心累
2. 加班使我快乐
回复编号即可 (1-2)
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 非授权用户 | 静默忽略（仅日志） |
| 旧会话存在 | 标记取消并提示"已取消上一条未完成的操作" |
| 空关键词 | 回复 "/search <关键词>" |
| 核心搜索逻辑错误 | 委托 `_search_utils.execute_search` 处理 |
