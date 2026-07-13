# bot/plugins/query.py — /query 命令插件

> NoneBot2 命令插件，无对外 Python API。参数解析由本模块完成，组合检索与结果分发委托 `_search_utils.py`。

## 注册

```python
query_cmd = on_command(
    "query",
    rule=to_me(),
    priority=5,
    block=True,
    aliases={"q"},
)
```

## 处理函数

```python
async def handle_query(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None
```

```python
async def got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message = Arg("selection"),
) -> None
```

## 参数解析

```python
def _parse_args(text: str) -> tuple[str, list[str], list[str]]
```

- `#tag` 解析为标签，多个标签按 AND 匹配。
- `@speaker` 解析为说话人，多个说话人按 OR 匹配。
- 其余 token 以空格拼接为关键词。
- 单独的 `#` 或 `@` token 忽略。
- 关键词、说话人和标签均为空时回复 `/query <关键词> [@说话人] [#标签...]`。

## 展示选项

- 有关键词：`QUERY_KW_OPTIONS` 展示关键词相似度，使用 score 量纲并支持回复 `n` 翻页。
- 无关键词：`QUERY_FILTER_OPTIONS` 不展示相似度，支持回复 `n` 翻页。

## 依赖

- `auth.is_authorized()`：授权校验。
- `_search_utils.execute_combined_search()`：执行组合检索并分发结果。
- `_search_utils.handle_got_selection()`：处理候选编号和翻页输入。
- `bot.session.session_manager`：管理聊天作用域内的会话互斥与清理。

## 错误处理

| 场景 | 处理 |
|------|------|
| 非授权用户 | 静默忽略，仅记录日志 |
| 已有活跃会话 | 回复“已有命令在处理中，请先 /cancel” |
| 参数为空 | 回复命令用法并结束会话 |
| 任务取消 | 清理当前聊天作用域会话并结束 Matcher |
| 其他异常 | 记录异常日志、清理会话并继续抛出 |
