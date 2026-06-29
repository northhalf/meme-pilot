# Refactoring Design: 消除重复的 `got_selection` Handler

> 日期：2026-06-29
> 状态：已批准待实施

## 目标

消除 `meme_plain_text.py:76-128` 和 `meme_search.py:75-134` 中 50 行完全相同的 `got("selection")` handler，提取共享函数到 `_search_utils.py`。

## 方案

全量提取（方案 A）：将完整 handler body（含 try/except 异常处理）提取到 `_search_utils.py` 的新函数 `handle_got_selection()`，两个插件各保留一个 ~4 行的包装器。

## 变更文件

| 文件 | 操作 |
|------|------|
| `bot/plugins/_search_utils.py` | 新增 `handle_got_selection()` 函数 + 8 项 import |
| `bot/plugins/meme_search.py` | lines 75-134 → 4 行包装器；删除 10 项 import |
| `bot/plugins/meme_plain_text.py` | lines 76-128 → 4 行包装器；删除 10 项 import |
| `docs/api/bot/plugins/_search_utils.md` | 新增 `handle_got_selection` API 文档 |

## 函数签名

```python
async def handle_got_selection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    selection_msg: Message,
    error_label: str = "搜索",
) -> None:
```

## 不变量

- 日志行为不变：error_label 参数区分 `/search`/兜底搜索
- `HELP_TEXT` 在共享函数中通过 `got_intercept_bypass` 传入，行为不变
- `got` 装饰器生命周期不受影响，`Arg("selection")` 由包装器持有
