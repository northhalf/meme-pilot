# 权限调整设计文档

> **日期**：2026-06-28
> **状态**：待实施

## 1. 需求概述

调整 MemePilot 命令的权限控制策略，将命令分为两组：

- **组 A（仅私聊授权）**：`/add`、`/refresh`、`/ai`
- **组 B（私聊授权 + 群聊@授权）**：`/search`、`/help`、普通文本

## 2. 权限判断链

```
收到消息 → to_me() 匹配
  → handler 入口 → is_authorized() 检查
    ├── 非授权 → 静默忽略（无论来源）
    └── 授权 → event.message_type 判断
          ├── 组 A + 群聊 → finish("此命令仅限私聊使用")
          ├── 组 A + 私聊 → 正常执行业务逻辑
          └── 组 B + 任何 → 正常执行业务逻辑
```

## 3. 修改清单

### 3.1 代码文件修改

#### 组 A — 仅私聊

| 文件 | 修改内容 |
|------|----------|
| `bot/plugins/meme_add.py` | `handle_add()` 开头（授权校验后）加 `event.message_type != "private"` 拦截；`got_image()` 同步加 |
| `bot/plugins/meme_refresh.py` | import `PrivateMessageEvent` → `MessageEvent`；`handle_refresh()` 开头加私聊检查 |
| `bot/plugins/meme_ai.py` | import `PrivateMessageEvent` → `MessageEvent`；`handle_ai()` 开头加私聊检查 |

#### 组 B — 私聊 + 群聊@

| 文件 | 修改内容 |
|------|----------|
| `bot/plugins/meme_search.py` | import `PrivateMessageEvent` → `MessageEvent`；`handle_search()`、`got_selection()` 参数类型更新 |
| `bot/plugins/meme_help.py` | import `PrivateMessageEvent` → `MessageEvent`；`handle_help()` 参数类型更新 |
| `bot/plugins/meme_plain_text.py` | import `PrivateMessageEvent` → `MessageEvent`；`handle_plain_text()`、`got_selection()` 参数类型更新 |
| `bot/plugins/_search_utils.py` | `execute_search()` 参数 `PrivateMessageEvent` → `MessageEvent` |

### 3.2 文档修改

| 文件 | 修改内容 |
|------|----------|
| `docs/PRD.md` | 更新「群聊消息」权限描述；区分组 A 组 B；更新边界情况表 |
| `CONTEXT.md` | 更新「群聊消息」「/help」「/search」术语定义 |
| `README.md` | 功能描述中补充群聊@支持说明 |

## 4. 边界情况

| 场景 | 行为 |
|------|------|
| 授权用户群聊 @bot /add | 回复"此命令仅限私聊使用" |
| 授权用户群聊 @bot /refresh | 回复"此命令仅限私聊使用" |
| 授权用户群聊 @bot /ai | 回复"此命令仅限私聊使用" |
| 授权用户群聊 @bot /search | 正常搜索 |
| 授权用户群聊 @bot /help | 回复帮助文本 |
| 授权用户群聊 @bot 普通文本 | 等同执行 /search |
| 授权用户群聊 @bot 未知命令 | 回复"未知命令"附帮助摘要 |
| 授权用户群聊 @bot 搜索多结果选编号 | 正常发送图片到群 |
| 非授权用户群聊 @bot 任何命令 | 静默忽略 |
| 非授权用户私聊任何命令 | 静默忽略 |

## 5. 不受影响的部分

- `bot/session.py` — 按 `user_id` 索引，无需改动
- `bot/auth.py` — 授权校验逻辑不变
- `bot/engine/` 层全部模块 — 不涉及消息事件类型
- `bot/config.py`、`bot/bot.py`、`bot/logging_config.py` — 不涉及权限
