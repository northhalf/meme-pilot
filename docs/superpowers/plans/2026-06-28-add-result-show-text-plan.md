# /add 显示识别文字 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/add` 命令成功添加或替换表情包时，向用户展示 OCR 识别到的文字内容，超长时截断。

**Architecture:** 在 `AddResult` 数据类中新增 `text` 字段，在 `add_entry()` 的两个分支（added/replaced）中传入 OCR 文字；在 `meme_add.py` 中新增截断工具函数并修改回复消息；更新测试用例。

**Tech Stack:** Python 3.12, pytest

---

### Task 1: `AddResult` 增加 `text` 字段 + `add_entry()` 传入文字

**Files:**
- Modify: `bot/engine/index_manager.py:188-205` — `AddResult` 数据类
- Modify: `bot/engine/index_manager.py:630-633` — `add_entry()` replaced 分支
- Modify: `bot/engine/index_manager.py:650-651` — `add_entry()` added 分支

- [ ] **Step 1: 为 `AddResult` 增加 `text` 字段**

  在 `replaced_filename` 之前插入 `text: str = ""`：

  ```python
  @dataclass
  class AddResult:
      entry_id: str | None
      reason: str
      text: str = ""                    # OCR 识别文本，无文字时为空字符串
      replaced_filename: str | None = None
      moved_to: str | None = None
  ```

- [ ] **Step 2: `add_entry()` 的 "replaced" 分支传入 `text`**

  第 629-633 行附近：

  ```python
  return AddResult(
      entry_id=old_id,
      reason="replaced",
      text=text,              # ← 新增
      replaced_filename=old_filename,
  )
  ```

- [ ] **Step 3: `add_entry()` 的 "added" 分支传入 `text`**

  第 651 行附近：

  ```python
  return AddResult(
      entry_id=entry_id,
      reason="added",
      text=text,              # ← 新增
  )
  ```

- [ ] **Step 4: 运行现有测试确认不被 break**

  ```bash
  uv run pytest tests/unit/engine/test_index_manager.py -v --tb=short
  ```

  预期：全部通过（现有测试构造 `AddResult(entry_id=..., reason=...)` 不传 `text`，默认 `""`）。

---

### Task 2: 截断工具函数 + 修改回复消息

**Files:**
- Modify: `bot/plugins/meme_add.py:217-223` — 回复消息分支
- Modify: `bot/plugins/meme_add.py` — 新增 `_format_ocr_text()` 函数

- [ ] **Step 1: 新增 `_format_ocr_text()` 函数**

  在 `_release_lock_safe()` 函数之前增加：

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

- [ ] **Step 2: 修改成功回复消息的三条分支**

  原来（第 218-223 行）：

  ```python
  if result.reason == "no_text":
      await matcher.finish("未识别到文字，已移至 meme_no_text/")
  elif result.reason == "replaced":
      await matcher.finish("已成功添加（替换旧图）✅")
  else:
      await matcher.finish("已成功添加表情包 ✅")
  ```

  改为：

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

- [ ] **Step 3: 编译检查**

  ```bash
  uv run python -m compileall bot/plugins/meme_add.py
  ```

  预期：无错误。

---

### Task 3: 更新现有测试 + 新增 `_format_ocr_text` 测试

**Files:**
- Modify: `tests/unit/plugins/test_meme_add.py` — 更新 existing assertion + 新增测试类

- [ ] **Step 1: 更新 `test_success` 的 mock 和断言**

  原来（第 427-446 行附近）：

  ```python
  im.add_single_file = AsyncMock(
      return_value=AddResult(entry_id="1", reason="added")
  )
  ...
  matcher.finish.assert_awaited_once()
  assert "已成功添加" in matcher.finish.call_args[0][0]
  ```

  改为传入 `text` 并验证新消息格式：

  ```python
  im.add_single_file = AsyncMock(
      return_value=AddResult(entry_id="1", reason="added", text="加班心好累")
  )
  ...
  matcher.finish.assert_awaited_once()
  assert "新增表情包✅" in matcher.finish.call_args[0][0]
  ```

- [ ] **Step 2: 更新 `test_success_with_target_name`**

  同样，mock 加入 `text="加班心好累"`，断言改为 `"新增表情包✅" in matcher.finish.call_args[0][0]`。

- [ ] **Step 3: 更新 `test_lock_released_on_success`**

  mock 加入 `text="加班心好累"`（断言不变，仍是检查锁释放）。

- [ ] **Step 4: 更新 `test_session_cancelled_on_success`**

  mock 加入 `text="加班心好累"`（断言不变，仍是检查会话清理）。

- [ ] **Step 5: 新增 `TestFormatOcrText` 测试类**

  在 `test_meme_add.py` 的 `TestReleaseLockSafe` 之后（第 201 行后）：

  ```python
  class TestFormatOcrText:
      """_format_ocr_text 测试。"""

      def test_short_text_returned_as_is(self) -> None:
          """短于等于 50 字的文本原样返回。"""
          assert meme_add._format_ocr_text("心好累啊") == "心好累啊"

      def test_exactly_50_chars(self) -> None:
          """刚好 50 字不截断。"""
          text = "a" * 50
          assert meme_add._format_ocr_text(text) == text

      def test_long_text_truncated(self) -> None:
          """超过 50 字截断并标注总长度。"""
          text = "a" * 60
          expected = "a" * 50 + "...（总文本长度60）"
          assert meme_add._format_ocr_text(text) == expected

      def test_empty_string(self) -> None:
          """空字符串不截断。"""
          assert meme_add._format_ocr_text("") == ""
  ```

- [ ] **Step 6: 运行全部测试确认通过**

  ```bash
  uv run pytest tests/unit/plugins/test_meme_add.py -v
  ```

  预期：全部 PASS。
