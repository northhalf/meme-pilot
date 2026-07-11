# bot/plugins/_help_text.py — 帮助文本常量模块

> 以下划线开头避免 NoneBot2 自动加载为插件。供 `meme_help.py` 和 `meme_plain_text.py` 共享。

## 常量

| 常量 | 类型 | 说明 |
|------|------|------|
| `HELP_TEXT` | `str` | 命令帮助摘要文本 |

### `HELP_TEXT` 内容

```
/help：查看命令帮助
/query <关键词> [@说话人] [#标签...]：按关键词/说话人/标签组合检索
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>]：通过聊天添加一张表情包
/addtag <id> <tag>...：为指定表情包添加标签
/del <id>...：删除指定表情包（需确认）
/edittext <id> <新文本>：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人]：设置或清空表情包的说话人
/refresh：扫描 memes/ 并增量更新索引
/info [id]：查看机器人状态与统计信息，或查看指定表情包详情
/cancel：取消当前正在执行的命令
```
