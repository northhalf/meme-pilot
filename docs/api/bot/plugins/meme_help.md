# bot/plugins/meme_help.py — /help 命令插件

> NoneBot2 命令插件，无对外 Python API。本文档记录命令行为与依赖。

## 命令

| 命令 | 格式 | 说明 |
|------|------|------|
| `/help` | `/help` | 查看命令帮助 |

普通文本和未知斜杠命令的兜底处理已移入 `bot/plugins/meme_plain_text.py`。授权用户发送普通文本（私聊或群聊 @bot）时等同执行 `/search`，未知斜杠命令时回复"未知命令"并附帮助摘要。

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `is_authorized()` | `bot.auth` | 授权用户校验 |
| `HELP_TEXT` | `bot.plugins._help_text` | 共享的帮助文本常量 |

## 行为

1. 授权校验：非授权用户静默忽略（仅日志）
2. 回复帮助摘要（支持私聊和群聊 @bot 触发）

## 匹配器

| 匹配器 | 类型 | priority | block | 说明 |
|--------|------|----------|-------|------|
| `help_cmd` | `on_command("help")` | 5 | True | `/help` 命令 |

## 回复格式

**`/help`:**
```
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [目标命名]：通过聊天添加一张表情包
/refresh：扫描 memes/ 并增量更新索引
/cancel：取消当前正在执行的命令
```
