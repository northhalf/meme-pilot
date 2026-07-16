"""帮助文本常量。

供 help.py、plain_text.py 与 _search_utils.py 共享。
下划线开头避免 NoneBot2 自动加载为插件。
"""

HELP_TEXT = """\
/help (/h)：查看命令帮助
直接发送关键词：按关键词检索表情包（结果过多时支持翻页）
/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足；结果过多时支持翻页）
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包（结果过多时支持翻页）
/add [speaker <tags...>] (/a)：通过聊天添加一张表情包
/addtag <id> <tag>... (/at)：为指定表情包添加标签
/del <id>... (/d)：删除指定表情包（需确认）
/edittext <id> <新文本> (/e)：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人] (/sp)：设置或清空表情包的说话人
/collection create <名称>：创建表情包合集
/switch [合集编号|名称]：查看或切换表情包合集
/move <id> <目标合集编号|名称> (/mv)：移动表情包（需确认）
/refresh (/r)：扫描 memes/ 并增量更新索引
/info [id]：查看机器人状态与统计信息，或查看指定表情包详情
/cancel (/c)：取消当前正在执行的命令"""

HELP_TEXT_GROUP = """\
/help (/h)：查看命令帮助
直接发送关键词：按关键词检索表情包（结果过多时支持翻页）
/query <关键词> [@说话人] [#标签...] (/q)：按关键词/说话人/标签组合检索（多说话人任一、多标签同时满足；结果过多时支持翻页）
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包（结果过多时支持翻页）
/info [id]：查看机器人状态与统计信息，或查看指定表情包详情
/switch [合集编号|名称]：查看或切换表情包合集
/cancel (/c)：取消当前正在执行的命令"""


def help_text_for(message_type: str) -> str:
    """根据会话类型返回帮助文本。

    群聊中仅暴露组 B 指令（私聊独有命令不在群聊帮助中展示），
    私聊返回完整帮助文本。

    Args:
        message_type: OneBot 事件消息类型（"private" 或 "group"）。

    Returns:
        私聊返回完整帮助文本；群聊返回仅含组 B 指令的精简版。
    """
    if message_type == "private":
        return HELP_TEXT
    return HELP_TEXT_GROUP
