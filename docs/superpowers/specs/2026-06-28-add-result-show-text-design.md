# 设计文档：/add 命令结束时显示识别文字

> 日期：2026-06-28
> 状态：已定稿待实现

## 1. 目标

在 `/add` 命令成功添加或替换表情包时，向用户展示 OCR 识别到的文字内容，超出 50 字符时截断并标注总长度。

## 2. 改动范围

涉及 2 个文件：

- `bot/engine/index_manager.py` — `AddResult` 数据类增加 `text` 字段
- `bot/plugins/meme_add.py` — 回复消息中加入 OCR 文字展示

## 3. 设计

### 3.1 `AddResult` 新增 `text` 字段

```python
@dataclass
class AddResult:
    entry_id: str | None
    reason: str
    text: str = ""                    # OCR 识别文本，无文字时为空字符串
    replaced_filename: str | None = None
    moved_to: str | None = None
```

- 字段放在最后，默认 `""`，不破坏现有构造调用
- `no_text` 场景无需传参，保持默认值

### 3.2 `add_entry()` 返回时传入 `text`

两处改动，都在 `index_manager.py`：

**「replaced」分支（第 629-633 行附近）：**
```python
return AddResult(
    entry_id=old_id,
    reason="replaced",
    text=text,              # 新增
    replaced_filename=old_filename,
)
```

**「added」分支（第 651 行附近）：**
```python
return AddResult(
    entry_id=entry_id,
    reason="added",
    text=text,              # 新增
)
```

### 3.3 截断工具函数（`meme_add.py`）

```python
def _format_ocr_text(text: str, max_len: int = 50) -> str:
    """格式化 OCR 文本：过长时截断并标注总长度。

    Args:
        text: OCR 识别文本。
        max_len: 截断长度，默认 50。

    Returns:
        格式化后的文本。不超过 max_len 时原样返回；
        超过时截断为前 max_len 字并追加「...（总文本长度N）」。
    """
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}...（总文本长度{len(text)}）"
```

### 3.4 插件层回复改动（`meme_add.py` `got_image()`）

**原来：**
```python
if result.reason == "no_text":
    await matcher.finish("未识别到文字，已移至 meme_no_text/")
elif result.reason == "replaced":
    await matcher.finish("已成功添加（替换旧图）✅")
else:
    await matcher.finish("已成功添加表情包 ✅")
```

**改为：**
```python
if result.reason == "no_text":
    await matcher.finish("未识别到文字，已移至 meme_no_text/")
elif result.reason == "replaced":
    ocr_display = _format_ocr_text(result.text)
    await matcher.finish(f"替换旧图✅，识别到的文字为：{ocr_display}")
else:
    ocr_display = _format_ocr_text(result.text)
    await matcher.finish(f"新增表情包✅，识别到的文字为：{ocr_display}")
```

### 3.5 示例消息

| 场景 | 展示效果 |
|------|---------|
| 正常新增，短文字 | `新增表情包✅，识别到的文字为：心好累啊` |
| 正常新增，长文字 | `新增表情包✅，识别到的文字为：第一行文字第二行文字第三行文字第四行文字第五行文字第...（总文本长度120）` |
| 替换旧图，短文字 | `替换旧图✅，识别到的文字为：摸鱼中请勿打扰` |
| 替换旧图，长文字 | `替换旧图✅，识别到的文字为：这是一段很长的对话截图第一行第二行第三行第四行...（总文本长度200）` |
| 无文字图片 | `未识别到文字，已移至 meme_no_text/` |

## 4. 边界情况

| 场景 | 行为 |
|------|------|
| OCR 文本刚好 50 字 | 完整展示，不截断 |
| OCR 文本 51 字 | 截断为前 50 字 + `...（总文本长度51）` |
| 无文字图片 | `no_text` 分支不变，`result.text=""` |

## 5. 测试

### 现有测试不受影响

`AddResult.text` 默认 `""`，现有构造 `AddResult(entry_id="1", reason="added")` 不传 `text` 时行为不变。所有现有断言 `result.reason`、`result.entry_id` 的测试无需修改。

### 新增单元测试

为 `_format_ocr_text` 编写 3 个单元测试（在 `tests/unit/plugins/` 下）：

| 测试 | 输入 | 预期 |
|------|------|------|
| 短文本原样返回 | `_format_ocr_text("心好累啊")` | `"心好累啊"` |
| 超长文本截断 | `_format_ocr_text("a" * 60)` | `"a" * 50 + "...（总文本长度60）"` |
| 空字符串不截断 | `_format_ocr_text("")` | `""` |

## 6. 不需要的改动

- `add_single_file()` 接口不变 — 它内部调用 `add_entry()` 时 `text` 已在参数中
- `AddResult` 构造函数签名不变 — `text` 设默认值，所有现有调用自动兼容
- 不需要修改测试 — 现有测试验证的是 `AddResult.entry_id` / `AddResult.reason`，新增字段不影响
