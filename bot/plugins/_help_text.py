"""帮助文本常量。

供 meme_help.py 和 meme_plain_text.py 共享。
下划线开头避免 NoneBot2 自动加载为插件。
"""

HELP_TEXT = """\
/help：查看命令帮助
/search <关键词>：按 OCR 文本关键词搜索表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>]：通过聊天添加一张表情包
/edittext <id> <新文本>：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人]：设置或清空表情包的说话人
/refresh：扫描 memes/ 并增量更新索引
/cancel：取消当前正在执行的命令"""
