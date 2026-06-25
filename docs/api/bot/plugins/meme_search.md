# meme_search 插件

`/search <关键词>` — 关键词模糊搜索表情包。

## 注册

```python
search_cmd = on_command("search", rule=to_me(), priority=5, block=True)
```

## 处理函数

```python
async def handle_search(bot: Bot, event: PrivateMessageEvent, matcher: Matcher) -> None
```

```python
async def got_selection(bot: Bot, event: PrivateMessageEvent, matcher: Matcher, selection_msg: Message = Arg("selection")) -> None
```

## 依赖

- `app_state.get_index_manager()` — 检查索引锁和条目数
- `app_state.get_keyword_searcher()` — 关键词搜索
- `auth.is_authorized()` — 授权校验
- `session.check_and_cancel()` / `register()` / `cancel()` / `is_cancelled()` — 会话管理
- `config.MEMES_DIR` — 图片路径

## 流程

### handle_search

1. 授权校验
2. 会话覆盖检查 (`check_and_cancel`)
3. 获取 IndexManager
4. 检查索引锁 (`index_manager.is_locked`) — 只读检查
5. 提取关键词（去除 `/search` 前缀）
6. 空关键词检查
7. 空索引检查
8. 获取 KeywordSearcher
9. 调用 `searcher.search(keyword)`
10. 结果分支：
    - 0 条 → 回复无匹配
    - 1 条 → 直接发送图片
    - N 条 → 格式化选择列表，注册会话，等待用户选择

### got_selection

1. 检查会话是否已取消
2. 检查候选列表是否为空（防御性）
3. 解析用户输入编号
4. 无效/越界 → reject 提示重输
5. 有效 → 发送对应图片，清理会话

## 选择列表格式

```
找到多个匹配的表情包，请选择：
1. 加班到心累
2. 加班使我快乐
回复编号即可 (1-2)
```

## 错误处理

| 场景 | 回复 |
|------|------|
| IndexManager 未初始化 | "服务未就绪，请稍后再试" |
| KeywordSearcher 未初始化 | "服务未就绪，请稍后再试" |
| 索引锁占用 | "索引正在更新，请稍后再试" |
| 空关键词 | "/search <关键词>" |
| 索引为空 | "表情包目录为空，请先添加图片并执行 /refresh" |
| search() 异常 | "搜索服务暂时不可用，稍后重试" |
| 无匹配 | "没有匹配到任何表情包 🙁" |
| 无效编号 | "无效编号，请回复 1-{N} 之间的数字" |
| candidates 为空 | "搜索状态异常，请重新搜索" |
| 会话超时 | NoneBot2 `SESSION_EXPIRE_TIMEOUT` 自动处理 |
