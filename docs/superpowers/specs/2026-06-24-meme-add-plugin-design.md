# /add 命令插件设计文档

> 日期：2026-06-24
> 状态：待实现
> 关联：PRD 3.3 节「聊天添加表情包」

## 1. 概述

实现 `/add [目标命名]` 命令，允许授权用户通过 QQ 私聊发送图片添加表情包到本地库。图片经过压缩、OCR、Embedding 后写入索引。

## 2. 模块结构

### 2.1 文件

- `bot/plugins/meme_add.py` — NoneBot2 命令插件（新建）
- `bot/session.py` — 共享会话管理模块（新建，/add 和 /search 共用）
- `bot/engine/index_manager.py` — 新增 `_process_image_pipeline()` 和 `add_single_file()` 方法；`_resolve_unique_filename()` 改为公共函数（修改）

### 2.2 插件依赖

| 依赖 | 用途 |
|------|------|
| `bot.app_state` | `get_index_manager` |
| `bot.auth` | `is_authorized`, `log_unauthorized` |
| `bot.session` | `PendingSession`, `check_and_cancel`, `register`, `cancel`, `is_cancelled` |
| `nonebot` | `on_command` |
| `nonebot.adapters.onebot.v11` | `Bot`, `MessageEvent`, `MessageSegment` |
| `nonebot.adapters.onebot.v11.helpers` | `extract_image_urls` |
| `nonebot.rule` | `to_me` |
| `nonebot.params` | `ArgPlainText` |
| `httpx` | 图片下载 |
| 标准库 | `re`, `hashlib`, `datetime`, `logging`, `pathlib` |

### 2.3 插件内部结构

```
常量区:
  DOWNLOAD_TIMEOUT = 30    # 图片下载超时（秒）

工具函数:
  _download_image(url) -> bytes
  _get_extension(url, response) -> str | None
  _sanitize_filename(name) -> str
  _auto_filename(image_data) -> str

Matcher:
  add_cmd = on_command("add", rule=to_me(), priority=5, block=True)

Handlers:
  handle_add(bot, event)        — 命令入口，调用 matcher.got() 等待图片
  got_image(bot, event)         — 图片接收处理，非图片时 reject 重新等待
```

**超时机制**：通过 `SESSION_EXPIRE_TIMEOUT=60` 全局配置。NoneBot2 会话过期后自动清理 matcher，无需自定义超时 handler。配置方式见 `.env` 或 `nonebot.init(session_expire_timeout=60)`。

## 3. 核心流程

### 3.1 Handler 链

```
用户: /add [目标命名]
  ↓
handle_add (priority=5, rule=to_me)
  ├─ 授权校验 → 非授权静默忽略
  ├─ 会话覆盖检查 → 发送"已取消上一条未完成的操作，开始新的 /add"
  ├─ 检查 IndexManager 可用性
  ├─ 检查索引锁 → 锁占用则回复"索引正在更新，请稍后再试"
  ├─ 注册 pending_sessions[user_id]
  └─ 结束（got_image 通过装饰器自动触发）

got_image (@add_cmd.got("image", prompt="请发送图片，60 秒内有效"))
  ├─ prompt 发送 "请发送图片，60 秒内有效"
  ├─ matcher.receive() 等待下一条消息
  ├─ 会话有效性检查（cancelled 标志）
  ├─ extract_image_urls(event.message) 提取图片 URL
  ├─ 无图片 → matcher.reject("请发送一张图片") → 重新等待
  ├─ 下载图片 → 确定扩展名 → 校验格式 → 文件名处理 → 保存到 memes/
  ├─ 调用 index_manager.add_single_file(filename)
  ├─ 根据 AddResult 回复用户
  └─ finally: 释放锁 + 清理会话
```

**超时处理**：`SESSION_EXPIRE_TIMEOUT=60`（全局配置）。会话过期后 NoneBot2 自动清理 matcher，无需自定义超时 handler。用户超时后重新发送 `/add` 即可。

### 3.2 IndexManager 新增方法

**自定义异常**（`bot/engine/index_manager.py`）：

```python
class CompressionError(RuntimeError):
    """图片压缩失败。"""

class OcrError(RuntimeError):
    """OCR 识别失败。"""

class EmbeddingError(RuntimeError):
    """Embedding 生成失败。"""
```

**`_process_image_pipeline(filename)`** — 提取共享的「压缩 → OCR → Embedding」管道，各步骤 catch 后 re-raise 为对应异常类型：

```python
async def _process_image_pipeline(
    self, filename: str
) -> tuple[str, list[float]]:
    """图片处理管道：压缩 → OCR → Embedding。

    Args:
        filename: memes/ 下的文件名。

    Returns:
        (ocr_text, embedding) 元组。

    Raises:
        CompressionError: 图片压缩失败。
        OcrError: OCR 服务未注入或调用失败。
        EmbeddingError: Embedding 服务未注入或调用失败。
    """
    image_path = self._memes_dir / filename

    # 压缩（可压缩格式）/ 跳过（.bmp）
    if self._optimizer is not None:
        try:
            await self._optimizer.optimize(str(image_path))
        except Exception as exc:
            raise CompressionError(f"图片压缩失败: {filename}") from exc

    # OCR
    if self._ocr_provider is None:
        raise OcrError("OCR 服务未注入")
    try:
        text = await self._ocr_provider.ocr(str(image_path))
    except Exception as exc:
        raise OcrError(f"OCR 调用失败: {filename}") from exc

    # Embedding
    if self._embedding_provider is None:
        raise EmbeddingError("Embedding 服务未注入")
    try:
        embedding = await self._embedding_provider.embed(text)
    except Exception as exc:
        raise EmbeddingError(f"Embedding 调用失败: {filename}") from exc

    return text, embedding
```

**`add_single_file(filename)`** — 单张图片添加入口，调用管道后写入索引：

```python
async def add_single_file(self, filename: str) -> AddResult:
    """处理单张已保存的图片：管道处理 → add_entry。

    Args:
        filename: memes/ 下的文件名。

    Returns:
        AddResult 描述添加结果。

    Raises:
        CompressionError: 图片压缩失败。
        OcrError: OCR 服务未注入或调用失败。
        EmbeddingError: Embedding 服务未注入或调用失败。
    """
    text, embedding = await self._process_image_pipeline(filename)
    return self.add_entry(filename, text, embedding)
```

**`_process_new_file()` 重构**：原有方法改为调用 `_process_image_pipeline()`，返回值保持 `(filename, text, embedding)` 不变。

### 3.3 resolve_unique_filename() 公共化

将 `_resolve_unique_filename()` 从私有函数改为公共函数 `resolve_unique_filename()`，插件可直接调用解决文件名冲突。`_move_to_no_text()` 内部调用同步更新。

## 4. 会话管理（bot/session.py）

### 4.1 模块接口

```python
# bot/session.py

@dataclass
class PendingSession:
    """待处理会话。"""
    matcher: Matcher
    cancelled: bool = False
    type: str = "add"  # "add" | "search"

# 模块级会话字典
pending_sessions: dict[str, PendingSession] = {}

def check_and_cancel(user_id: str, new_type: str) -> str | None:
    """检查旧会话并标记取消。返回提示文本，无旧会话返回 None。"""

def register(user_id: str, matcher: Matcher, type: str) -> None:
    """注册新会话。"""

def cancel(user_id: str) -> None:
    """移除会话。"""

def is_cancelled(user_id: str) -> bool:
    """检查会话是否已被取消。"""
```

### 4.2 插件中的使用

在 `handle_add` 中：

```python
from bot.session import check_and_cancel, register

hint = check_and_cancel(user_id, "add")
if hint:
    await matcher.send(hint)
register(user_id, matcher, "add")
```

在 `got_image` 中：

```python
from bot.session import cancel, is_cancelled

if is_cancelled(user_id):
    return  # 已被新命令覆盖
# ... 处理完成后
cancel(user_id)
```

### 4.3 跨命令共享

`/search` 实现时直接 `from bot.session import ...` 使用同一套接口，无需重复实现会话管理逻辑。

## 5. 文件名处理

### 5.1 安全化规则

1. 去除首尾空白
2. `/` `\` `<` `>` `:` `"` `|` `?` `*` → `_`
3. 合并连续空白为单个 `_`
4. 最大 80 字符，截断后去除首尾 `_`
5. 安全化后为空 → 进入自动命名

### 5.2 自动命名

格式：`meme_<YYYYMMDDHHMMSS>_<hash8>`

- 时间：Bot 接收图片消息的本地时间（`datetime.now()`）
- hash8：图片内容 SHA-256 前 8 位

### 5.3 文件名冲突

保存到 `memes/` 时，若同名文件已存在，追加 `_2`、`_3`... 直到不冲突。调用 `IndexManager.resolve_unique_filename()` 公共函数。

### 5.4 扩展名确定

优先级：
1. 消息/下载文件中的原始扩展名
2. 下载响应 `Content-Type` 推断
3. 无法推断 → 拒绝添加，回复"无法识别图片格式"

## 6. 错误处理

| 场景 | 插件行为 | IndexManager 行为 |
|------|---------|------------------|
| 索引锁占用 | 回复"索引正在更新，请稍后再试" | — |
| 无图片消息 | 回复"请发送一张图片"，`matcher.reject()` 重新等待 | — |
| 超时（60s 无响应） | NoneBot2 自动清理 matcher（`SESSION_EXPIRE_TIMEOUT=60`） | — |
| 不支持扩展名 | 回复"不支持的图片格式: {ext}" | — |
| 下载失败 | 回复"图片下载失败" | — |
| 压缩失败 | 回复"图片压缩失败"，删除已下载文件 | 抛出 CompressionError |
| OCR 失败 | 回复"OCR 服务不可用"，删除已下载文件 | 抛出 OcrError |
| Embedding 失败 | 回复"Embedding 服务不可用"，删除已下载文件 | 抛出 EmbeddingError |
| OCR 无文字 | 回复"未识别到文字，已移至 meme_no_text/" | `add_entry` 返回 `reason="no_text"` |
| 去重命中 | 回复"已成功添加（替换旧图）✅" | `add_entry` 返回 `reason="replaced"` |
| 正常新增 | 回复"已成功添加表情包 ✅" | `add_entry` 返回 `reason="added"` |

关键原则：压缩/OCR/Embedding 任一环节失败 → 删除已下载图片 → 不写入索引 → 回复具体失败原因（分别 catch `CompressionError`/`OcrError`/`EmbeddingError`）。

## 7. 锁机制

### 7.1 锁获取时机

在 `handle_add` 中，确认 IndexManager 可用后、注册会话前获取锁。

### 7.2 锁释放

- `got_image` 的 `finally` 块中释放
- 会话超时（`SESSION_EXPIRE_TIMEOUT`）时，matcher 被清理，锁通过会话覆盖逻辑释放（新 `/add` 到来时 `check_and_cancel` 触发清理）

### 7.3 超时场景的锁处理

会话超时后 matcher 被 NoneBot2 自动清理。若用户后续发送新 `/add`，`handle_add` 中的 `check_and_cancel` 会检查旧会话并释放锁。若用户不再发送命令，锁随 matcher 清理自动释放（NoneBot2 会话机制保证）。

## 8. 不在范围内

- `/search` 命令（仅预留会话管理接口）
- 多图片批量添加（v1.0 只处理第一张）
- 图片内容 hash 去重（v1.0 仅 OCR 文本去重）
- 群聊支持
