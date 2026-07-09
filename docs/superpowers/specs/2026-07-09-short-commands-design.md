# 设计：命令短别名（Short Command Aliases）

> 日期：2026-07-09
> 状态：待用户审阅
> 范围：为 9 个现有命令增加等价短命令别名

---

## 1. 背景与目标

### 1.1 背景

MemePilot 现有命令均以完整英文单词注册（`/search`、`/help`、`/add`、`/addtag`、`/del`、`/edittext`、`/refresh`、`/setspeaker`、`/cancel`）。用户希望为这 9 个命令各提供一个等价短命令（如 `/search` 与 `/s` 作用相同），降低高频命令的输入成本。

### 1.2 目标

- 9 个命令各自支持一个短命令，长短命令行为完全等价。
- 短命令在 `/help` 输出与 README 中以行内括注形式展示，便于用户发现。
- 实现方式复用 NoneBot2 原生能力，不引入额外命令调度层。

### 1.3 非目标（YAGNI）

- 不为 `/rand`、`/sim`、`/ai`、`/info` 增加短命令（本次未要求）。
- 不支持短命令自定义或运行时配置。
- 不改变任何命令的业务逻辑、权限分组、会话互斥规则。
- 不改变命令的 `to_me()` 触发条件（私聊天然触发，群聊需 @bot）。

---

## 2. 命名映射

采用「极简首字母 + 语义双字母消歧」方案。用户示例 `/search -> /s` 确立了极简首字母风格；`add`/`addtag` 与 `search`/`setspeaker` 两处首字母冲突用语义双字母消歧。

| 长命令 | 短命令 | 命名依据 |
|--------|--------|----------|
| `/search` | `/s` | 首字母（用户指定） |
| `/help` | `/h` | 首字母 |
| `/add` | `/a` | 首字母 |
| `/addtag` | `/at` | add + tag，与 `/a` 体系一致 |
| `/del` | `/d` | 首字母 |
| `/edittext` | `/e` | edit 首字母 |
| `/refresh` | `/r` | 首字母 |
| `/setspeaker` | `/sp` | speaker 语义 |
| `/cancel` | `/c` | 首字母 |

**冲突校验**：NoneBot2 `on_command` 采用 `COMMAND_START` + 命令名**精确匹配**（非前缀匹配），故 `/s` 与 `/sp`、`/a` 与 `/at` 互不误触发。9 个短命令互不相同，且不与现有 `/ai`、`/sim`、`/rand`、`/info` 字面冲突。

---

## 3. 实现方案

### 3.1 命令注册：`aliases` 参数

NoneBot2 的 `on_command` 提供 `aliases: set[str] | None` 参数（已通过 Context7 确认 `/nonebot/nonebot2` 文档）。别名与主命令名触发**同一 matcher**，handler / got / 会话 / 权限 / 群聊拦截全部共享，长短命令行为完全等价。

**注意**：NoneBot2 命令名**不含前导 `/`**（`/` 属于 `COMMAND_START`），故 `aliases` 写 `"s"` 而非 `"/s"`。

9 个插件的注册行改动：

| 文件 | 改动 |
|------|------|
| `bot/plugins/meme_search.py` | `on_command("search", ..., aliases={"s"})` |
| `bot/plugins/meme_help.py` | `on_command("help", ..., aliases={"h"})` |
| `bot/plugins/meme_add.py` | `on_command("add", ..., aliases={"a"})` |
| `bot/plugins/meme_addtag.py` | `on_command("addtag", ..., aliases={"at"})` |
| `bot/plugins/meme_delete.py` | `on_command("del", ..., aliases={"d"})` |
| `bot/plugins/meme_edit.py` | `on_command("edittext", ..., aliases={"e"})` |
| `bot/plugins/meme_refresh.py` | `on_command("refresh", ..., aliases={"r"})` |
| `bot/plugins/meme_setspeaker.py` | `on_command("setspeaker", ..., aliases={"sp"})` |
| `bot/plugins/meme_cancel.py` | `on_command("cancel", ..., aliases={"c"})` |

`rule=to_me()`、`priority=5`、`block=True` 保持不变。

### 3.2 参数提取重构：`CommandArg()`

6 个带参数命令当前用 `raw_text.removeprefix("/name").removeprefix("name").strip()` 提取参数。该写法对短命令失效：`"/s 加班".removeprefix("/search")` 不生效，关键词会变成 `/s 加班` 而非 `加班`。

因此短命令能正确工作的**必要条件**是把参数提取改为 NoneBot2 的 `CommandArg()` 依赖注入（已通过 Context7 确认）。`CommandArg()` 返回命令后的参数 `Message`，**仅在命令触发时的 handler 中有效**（got 后续流程中取值不同--我们的参数提取都在 `@handle()` 入口，符合此约束）。

改造模式（以 `/search` 为例）：

```python
from nonebot.params import Arg, CommandArg
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

@search_cmd.handle()
async def handle_search(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
) -> None:
    ...
    keyword = args.extract_plain_text().strip()
    if not keyword:
        ...
```

6 个带参数命令的提取改动：

| 文件 | 旧 | 新 |
|------|----|----|
| `meme_search.py` | `raw_text.removeprefix("/search").removeprefix("search").strip()` | `args.extract_plain_text().strip()` |
| `meme_add.py` | `raw_text.removeprefix("/add").removeprefix("add").strip()` | `args.extract_plain_text().strip()` |
| `meme_addtag.py` | `raw.removeprefix("/addtag").removeprefix("addtag").strip()` | `args.extract_plain_text().strip()` |
| `meme_delete.py` | `raw.removeprefix("/del").removeprefix("del").strip()` | `args.extract_plain_text().strip()` |
| `meme_edit.py` | `raw.removeprefix("/edittext").removeprefix("edittext").strip()` | `args.extract_plain_text().strip()` |
| `meme_setspeaker.py` | `raw.removeprefix("/setspeaker").removeprefix("setspeaker").strip()` | `args.extract_plain_text().strip()` |

各 handler 函数签名增加 `args: Message = CommandArg()` 参数；`Message` 已在各文件 import，仅需补 `from nonebot.params import CommandArg`（多数文件已 import `Arg`，合并到同一行）。

`/help`、`/refresh`、`/cancel` 无参数提取，仅需加 `aliases`，不动 handler 逻辑。

### 3.3 为何顺带重构 removeprefix

`removeprefix` 重构不是无关改动，而是短命令能工作的必要条件：短命令名是长命令名的前缀子集，`removeprefix("/长命令")` 无法剥离短命令前缀。改用 `CommandArg()` 后，参数提取与命令名解耦，任意别名都能正确取参。这属于「服务于当前目标的目标性改进」。

### 3.4 行为收敛点（需知情）

NoneBot2 命令解析要求命令名与参数间有分隔符（`COMMAND_SEP`，默认空白）。改用 `CommandArg` 后：

- **不受影响**：`/del 12`、`/search 加班`、`/add 小明 吐槽` 等带空格的正常命令（与 PRD 所有示例一致）。
- **行为变化**：无空格连写（如 `/del12`、`/search加班`）不再被解析为「命令 + 参数」，而是被当作未知命令名。当前 `removeprefix` 实现下这类连写可工作。

此收敛符合标准命令语义，且与 PRD/CONTEXT 中所有命令格式描述（均带空格）一致，属可接受标准化。若后续确需保留连写兼容，可通过 `on_command` 的 `force_whitespace` 参数调控，但本次不引入。

---

## 4. 影响面盘点

短命令与长命令共享同一 matcher 与 handler，以下机制均**不受影响**：

| 机制 | 说明 |
|------|------|
| 会话互斥 | `session_manager.activate_chat(user_id, "search", matcher)` 的 `command_type` 仍为长命令名（`"search"` 等），短命令触发的同一 matcher 不改变标识，互斥逻辑不变 |
| 群聊拦截 | 组 A 命令（`add`/`addtag`/`del`/`edittext`/`setspeaker`/`refresh`）的 `event.message_type != "private"` 检查在共享 handler 内，短命令同样被拦截并回复「此命令仅限私聊使用」 |
| 权限校验 | `is_authorized` 在共享 handler 入口，短命令同样校验 |
| got 旁路拦截 | `got_intercept_bypass` 按文本匹配 `/help`、`/cancel`，与命令注册名无关，短命令等待期间旁路行为不变 |
| 兜底插件 | `meme_plain_text` 对未知斜杠命令回复「未知命令」；短命令已被 `on_command` 注册捕获，不会落到兜底 |
| `to_me()` | 私聊天然触发，群聊需 @bot；短命令同样适用 |

---

## 5. 文档同步

按 CLAUDE.md「每实现一个模块后更新 API.md」「修改命令交互先看 PRD」要求，同步以下文档：

### 5.1 `bot/plugins/_help_text.py`

9 行命令加行内括注（`/rand`、`/sim`、`/ai`、`/info` 不加）：

```text
/help (/h)：查看命令帮助
/search <关键词> (/s)：按 OCR 文本关键词搜索表情包
/rand [关键词]：随机给出 10 个表情包，回复 0 换一批
/sim <描述文本>：按语义相似度给出前 10 个表情包
/ai <自然语言描述>：按自然语言描述匹配表情包
/add [speaker <tags...>] (/a)：通过聊天添加一张表情包
/addtag <id> <tag>... (/at)：为指定表情包添加标签
/del <id>... (/d)：删除指定表情包（需确认）
/edittext <id> <新文本> (/e)：修改指定表情包的 OCR 文本
/setspeaker <id> [说话人] (/sp)：设置或清空表情包的说话人
/refresh (/r)：扫描 memes/ 并增量更新索引
/info：查看机器人状态与统计信息
/cancel (/c)：取消当前正在执行的命令
```

### 5.2 其他文档

| 文件 | 改动 |
|------|------|
| `README.md` | 功能演示区命令清单同步加 `(/短命令)` 括注 |
| `CONTEXT.md` | 交互协议表各命令定义补充短命令别名 |
| `docs/api/API.md` | 各插件 `on_command` 注册说明补 `aliases`；带参数命令补 `CommandArg` 提取说明 |
| `docs/PRD.md` | 3.x 各功能「触发方式」补充短命令（命令交互变更，按 CLAUDE.md 同步） |

---

## 6. 测试计划

### 6.1 新增单元测试

- 短命令触发：`/s`、`/h`、`/a`、`/at`、`/d`、`/e`、`/r`、`/sp`、`/c` 各自触发对应 matcher。
- 参数提取：`/s 加班` 提取关键词为 `加班`；`/del 12 42` 提取 `[12, 42]`；`/add 小明 吐槽` 提取 speaker=`小明`、tags=`["吐槽"]` 等。
- 会话互斥：短命令触发的 `command_type` 仍为长命令名，互斥逻辑不变。
- 组 A 群聊拦截：`/a`、`/at`、`/d`、`/e`、`/sp`、`/r` 在群聊 @bot 时回复「此命令仅限私聊使用」。
- 组 B/C 群聊触发：`/s`、`/h`、`/c` 在群聊 @bot 时正常执行。

### 6.2 更新现有测试

- 现有针对 `removeprefix` 参数提取的测试改为针对 `CommandArg` 提取（mock `CommandArg` 注入或构造命令事件）。

### 6.3 校验命令

```bash
uv run pytest
uv run python -m compileall bot tests
```

---

## 7. 验收标准

1. 9 个短命令各自与对应长命令行为完全等价（参数提取、权限、会话、群聊拦截、got 交互）。
2. `/help` 输出与 README 命令清单按 5.1 格式展示短命令括注。
3. `bot/plugins/_help_text.py`、`README.md`、`CONTEXT.md`、`docs/api/API.md`、`docs/PRD.md` 均同步更新。
4. `uv run pytest` 全量通过；`uv run python -m compileall bot tests` 无语法错误。
5. 无空格连写（如 `/del12`）不再解析为命令参数的行为收敛已在本文档记录，且与 PRD 示例一致。

---

## 8. 参考资料

- NoneBot2 `on_command` `aliases` 参数：https://nonebot.dev/docs/advanced/matcher
- NoneBot2 `CommandArg()` 依赖注入：https://nonebot.dev/docs/advanced/dependency
- 通过 Context7 库 ID `/nonebot/nonebot2` 确认 API 行为
