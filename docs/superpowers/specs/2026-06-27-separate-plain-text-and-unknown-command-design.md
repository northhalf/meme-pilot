# 设计文档：分离普通文本与未知斜杠命令处理

> 日期：2026-06-27
> 状态：待审阅

---

## 背景

当前 `meme_help.py` 的兜底处理器（`catch_all`）对普通文本和未知斜杠命令统一回复帮助摘要。需求是将两者分离：

- **未知斜杠命令**：保持现有行为（回复"未知命令" + 帮助摘要）
- **普通文本**：默认当作 `/search` 命令，整个文本作为关键词执行搜索

## 设计

### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `bot/plugins/_search_utils.py` | 新增 | 提取搜索核心逻辑，下划线开头避免 NoneBot2 自动加载 |
| `bot/plugins/meme_search.py` | 修改 | 提取 `execute_search` + `handle_selection` 到 `_search_utils`，原函数变为薄包装 |
| `bot/plugins/meme_help.py` | 修改 | 普通文本调用 `execute_search`，新增 `catch_all.got("selection")` |

### `_search_utils.py` 设计

```python
async def execute_search(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, keyword: str
) -> None:
    """核心搜索逻辑。
    
    流程：锁检查 → 索引空检查 → 执行搜索 → 结果分支。
    多结果时注册 session 并启动超时任务。
    """


def handle_selection(
    matcher: Matcher, candidates: list[SearchResult], text: str
) -> SearchResult | str:
    """处理用户选择编号。
    
    Returns:
        SearchResult: 选择成功。
        str: 错误消息（无效编号等）。
    """
```

### `meme_search.py` 变更

```python
from bot.plugins._search_utils import execute_search, handle_selection

# handle_search 变为薄包装：授权 → 会话覆盖 → 提取关键词 → execute_search

# got_selection 变为薄包装：会话检查 → handle_selection → 发送图片/reject
```

### `meme_help.py` 变更

```python
from bot.plugins._search_utils import execute_search, handle_selection
from bot.session import check_and_cancel, is_cancelled, cancel

@catch_all.handle()
async def handle_plain_text(bot, event, matcher):
    user_id = event.get_user_id()
    if not is_authorized(user_id):
        log_unauthorized(user_id, "plain_text")
        return

    text = event.get_plaintext().strip()
    if text.startswith("/"):
        await catch_all.finish(f"未知命令\n\n{_HELP_TEXT}")
    else:
        # 会话覆盖检查（与 /search 一致）
        hint = check_and_cancel(user_id, "search")
        if hint:
            await matcher.send(hint)
        await execute_search(bot, event, catch_all, text)

@catch_all.got("selection")
async def got_selection(bot, event, matcher, selection_msg):
    user_id = event.get_user_id()
    if is_cancelled(user_id):
        return
    candidates = matcher.state.get("candidates", [])
    if not candidates:
        # 非本 matcher 触发的搜索会话，静默忽略
        return
    result = handle_selection(matcher, candidates, selection_msg.extract_plain_text().strip())
    if isinstance(result, str):
        await catch_all.reject(result)
    else:
        cancel(user_id)
        image_path = MEMES_DIR / result.filename
        await catch_all.finish(MessageSegment.image("file://" + str(image_path.resolve())))
```

### 边界情况

| 场景 | 处理方式 |
|------|---------|
| 普通文本搜索无结果 | `execute_search` 内部发送"没有匹配到任何表情包 🙁" |
| 普通文本搜索唯一结果 | `execute_search` 内部直接发送图片 |
| 普通文本搜索多结果 | `execute_search` 发送列表 + 注册 session + 超时任务 |
| 用户回复编号 | `catch_all.got("selection")` 调用 `handle_selection` 处理 |
| 搜索时索引正在更新 | `execute_search` 内部发送"索引正在更新，请稍后再试" |
| 搜索时索引为空 | `execute_search` 内部发送"表情包目录为空"提示 |
| 普通文本触发时有旧会话 | `handle_plain_text` 中调用 `check_and_cancel` 取消旧会话并提示 |
| `/search` 的 got 等待期间用户回复不带 @bot 的编号 | `catch_all.got("selection")` 中 `candidates` 为空，静默忽略（`return`） |

## 不变更

- `session.py` — 无变更
- 其他插件（`meme_add.py`、`meme_ai.py`、`meme_refresh.py`）— 无变更
- `meme_search.py` 的 `search_cmd` 和 `got_selection` matcher 注册不变，仅内部实现改为薄包装

## PRD 同步

需更新 `docs/PRD.md` 3.4 节和 `CONTEXT.md` 术语表中 `/help` 的描述：
- 授权用户私聊发送普通文本时，等同执行 `/search`（而非 `/help`）
