# /search 命令插件设计

> 日期：2026-06-25
> 状态：已批准

## 概述

实现 `bot/plugins/meme_search.py`，注册 `/search` 命令。授权用户在私聊中发送 `/search <关键词>`，Bot 通过 `KeywordSearcher` 对索引 OCR 文本做模糊匹配，返回搜索结果。

## 模块结构与注册

**文件**：`bot/plugins/meme_search.py`

**注册**：
```python
search_cmd = on_command("search", rule=to_me(), priority=5, block=True)
```

**依赖**：
- `bot.app_state.get_index_manager` — 获取 IndexManager 单例
- `bot.app_state.get_keyword_searcher` — 获取 KeywordSearcher 单例
- `bot.auth.is_authorized` / `log_unauthorized` — 授权校验
- `bot.session.check_and_cancel` / `register` / `cancel` / `is_cancelled` — 会话管理
- `bot.config.MEMES_DIR` — 图片路径

## app_state 扩展

`app_state.py` 新增 `KeywordSearcher` 单例管理：

```python
_keyword_searcher: KeywordSearcher | None = None

def init_app(..., keyword_searcher: KeywordSearcher | None = None) -> None:
    ...
    global _keyword_searcher
    _keyword_searcher = keyword_searcher

def get_keyword_searcher() -> KeywordSearcher:
    if _keyword_searcher is None:
        raise RuntimeError("KeywordSearcher 尚未初始化，请先调用 init_app()")
    return _keyword_searcher
```

## 处理流程

### handle_search() 入口

```
1. 授权校验 → is_authorized(user_id)
2. 会话覆盖 → check_and_cancel(user_id, "search") → 有旧会话则提示
3. 获取 IndexManager → get_index_manager()
4. 锁检查 → index_manager.is_locked（只读，不获取锁）
5. 提取关键词 → removeprefix("/search").strip()
6. 空关键词 → 回复 "/search <关键词>"
7. 空索引 → 回复 "表情包目录为空，请先添加图片并执行 /refresh"
8. 获取 KeywordSearcher → get_keyword_searcher()
9. 调用 searcher.search(keyword)
10. 结果分支：
    ├── 0 条 → "没有匹配到任何表情包 🙁"
    ├── 1 条 → 直接发送图片
    └── N 条 → 格式化选择列表，register()，got("selection") 等待
```

### got_selection() 选择处理

```
1. is_cancelled(user_id) → 已取消则 return
2. 解析用户输入为 int
3. 无效 / 越界 → reject("无效编号，请回复 1-{N} 之间的数字")
4. 从 matcher.state["candidates"] 取对应条目
5. cancel(user_id) 清理会话
6. 发送图片 MessageSegment.image(f"file:///{path.resolve()}")
```

## 选择列表格式

```
找到多个匹配的表情包，请选择：
1. 当你的老板说今天要加班
2. 加班到凌晨三点的我
3. 周日晚上的加班通知
回复编号即可 (1-3)
```

- 临时序号从 1 开始，与 `entry_id` 无关
- 只显示序号和 OCR 文本，不显示索引 id

## 候选存储

- `matcher.state["candidates"]` 存储 `list[SearchResult]`
- `got_selection` 中通过索引取值（用户输入 - 1）

## 图片发送

```python
image_path = MEMES_DIR / candidates[idx].filename
await search_cmd.finish(MessageSegment.image(f"file:///{image_path.resolve()}"))
```

## 超时机制

使用 NoneBot2 全局配置 `SESSION_EXPIRE_TIMEOUT`（默认 60 秒），不需要自定义超时代码。

## 错误处理与边界情况

| 场景 | 处理 |
|------|------|
| IndexManager 未初始化 | `finish("服务未就绪，请稍后再试")` |
| KeywordSearcher 未初始化 | `finish("服务未就绪，请稍后再试")` |
| 索引正在更新 | `finish("索引正在更新，请稍后再试")`（只读检查 `is_locked`） |
| 空关键词 | `finish("/search <关键词>")` |
| 索引为空 | `finish("表情包目录为空，请先添加图片并执行 /refresh")` |
| 无匹配 | `finish("没有匹配到任何表情包 🙁")` |
| 唯一匹配 | 直接发送图片（无需选择步骤） |
| 无效编号 | `reject("无效编号，请回复 1-{N} 之间的数字")` |
| 会话超时 | NoneBot2 `SESSION_EXPIRE_TIMEOUT` 自动处理 |
| 新命令覆盖旧会话 | `check_and_cancel()` 标记旧会话取消，提示用户 |

## 与其他插件的一致性

- 注册方式：`on_command("search", rule=to_me(), priority=5, block=True)` — 与 /help、/ai、/add、/refresh 一致
- 授权校验：`is_authorized()` + `log_unauthorized()` — 与所有插件一致
- 会话管理：`check_and_cancel()` / `register()` / `cancel()` / `is_cancelled()` — 与 /add 一致
- 图片发送：`MessageSegment.image(f"file:///...")` — 与 /ai 一致
- 锁检查：只读 `is_locked` — 与 /ai 一致（/search 不修改索引）
