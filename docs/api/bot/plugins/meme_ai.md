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

## 依赖

- `app_state.get_index_manager()` — AI 匹配（`index_manager.ai_match()` 内部持读锁）
- `auth.is_authorized()` — 授权校验
- `bot.session.session_manager` — 会话管理（activate_chat / deactivate_chat）

## 流程

1. 授权校验
2. `session_manager.activate_chat()` 激活会话
3. 群聊拦截：非 `"private"` 消息类型回复"此命令仅限私聊使用"
4. 提取描述（去除 `/ai` 前缀）
5. **并发**：`asyncio.gather()` 同时执行发送进度提示和 `index_manager.ai_match()`
6. 根据 `ai_match()` 返回值发送结果图片或错误提示
7. 读锁等待超时时回复"索引更新较慢，请稍后再试"

## 错误处理

| 场景 | 回复 |
|------|------|
| 群聊中调用 | "此命令仅限私聊使用" |
| 描述为空 | "/ai <自然语言描述>" |
| 索引为空 | "表情包目录为空，请先添加图片并执行 /refresh" |
| ValueError (embedding 异常) | "AI 服务暂时不可用，稍后重试" |
| 通用异常 | "AI 服务暂时不可用，稍后重试" |
| 无候选 | "没有找到匹配的表情包 🙁" |
