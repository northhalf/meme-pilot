# meme_ai 插件

`/ai <自然语言描述>` — AI 描述匹配表情包。

## 注册

```python
ai_cmd = on_command("ai", rule=to_me(), priority=5, block=True)
```

## 处理函数

```python
async def handle_ai(bot: Bot, event: MessageEvent) -> None
```

## 辅助函数

```python
async def _do_match(ai_matcher: AIMatcher, description: str) -> AIMatchResult | str
```

执行 AI 匹配。成功返回 `AIMatchResult`，失败返回错误提示文本（`str`）。

## 依赖

- `app_state.get_index_manager()` — 检查索引锁和条目数
- `app_state.get_ai_matcher()` — AI 匹配
- `auth.is_authorized()` — 授权校验

## 流程

1. 授权校验
2. 群聊拦截：非 `"private"` 消息类型回复"此命令仅限私聊使用"
3. 检查索引锁 (`index_manager.is_locked`) — 只读检查
4. 提取描述（去除 `/ai` 前缀）
5. 检查索引是否为空
6. **并发**：`asyncio.gather()` 同时执行发送进度提示和 `_do_match()`
7. 根据 `_do_match()` 返回值发送结果图片或错误提示

## 错误处理

| 场景 | 回复 |
|------|------|
| 群聊中调用 | "此命令仅限私聊使用" |
| 索引锁占用 | "索引正在更新，请稍后再试" |
| 描述为空 | "/ai <自然语言描述>" |
| 索引为空 | "表情包目录为空，请先添加图片并执行 /refresh" |
| ValueError (embedding 异常) | "AI 服务暂时不可用，稍后重试" |
| 通用异常 | "AI 服务暂时不可用，稍后重试" |
| 无候选 | "没有找到匹配的表情包 🙁" |
