# meme_add.py 锁释放模式重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `got_image` 中 10 处 `_release_lock_safe` 重复调用重构为 `try/finally` 统一释放，消除样板代码。

**Architecture:** 阶段分离 — 阶段 1（图片验证 + reject）在 try/finally 之外，阶段 2（处理流程）在 try/except/finally 中，finally 统一执行 `cancel` + `_release_lock_safe`。

**Tech Stack:** Python 3.12, NoneBot2, pytest

**Spec:** `docs/superpowers/specs/2026-06-26-meme-add-lock-refactor-design.md`

---

### Task 1: 重构 got_image 函数

**Files:**
- Modify: `bot/plugins/meme_add.py:109-235`

- [ ] **Step 1: 运行现有测试确认基线**

Run: `uv run pytest tests/unit/plugins/test_meme_add.py -v`
Expected: 全部 PASS（16 个测试）

- [ ] **Step 2: 重构 got_image 函数**

将 `got_image`（第 109-235 行）替换为阶段分离 + try/finally 结构：

```python
@add_cmd.got("image", prompt="请发送图片，60 秒内有效")
async def got_image(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    image_msg: Message = Arg("image"),
) -> None:
    """接收图片并处理。

    非图片消息时 reject 重新等待；图片消息执行完整添加流程。
    会话超时时清理 session 状态并提示用户。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        image_msg: got("image") 接收到的消息。
    """
    user_id = event.get_user_id()
    index_manager: IndexManager | None = None

    try:
        # 会话有效性检查
        if is_cancelled(user_id):
            return

        # 获取 IndexManager
        try:
            index_manager = get_index_manager()
        except RuntimeError:
            return

        # ── 阶段 1：图片验证（不涉及锁释放）──
        urls = extract_image_urls(image_msg)
        if not urls:
            await matcher.reject("请发送一张图片")
            return  # reject 后锁保持持有，会话继续等待

        # ── 阶段 2：处理流程（finally 统一释放锁）──
        image_url = urls[0]
        target_name = str(matcher.state.get("target_name", ""))

        # 下载图片
        try:
            image_data, response = await _download_image(image_url)
        except Exception as exc:
            logger.error("图片下载失败: %s", exc)
            await matcher.finish("图片下载失败")
            return

        # 确定扩展名
        ext = _get_extension(image_url, response)
        if ext is None or ext.lower() not in SUPPORTED_EXTENSIONS:
            await matcher.finish(f"不支持的图片格式: {ext or '未知'}")
            return

        # 文件名处理
        filename = _build_filename(target_name, image_data, ext)

        # 检查文件名冲突
        filepath = resolve_unique_filename(MEMES_DIR, filename)
        filename = filepath.name

        # 保存图片
        MEMES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            filepath.write_bytes(image_data)
        except OSError as exc:
            logger.error("保存图片失败: %s", exc)
            await matcher.finish("图片保存失败")
            return

        # 调用 IndexManager 处理
        try:
            result = await index_manager.add_single_file(filename)
        except CompressionError as exc:
            logger.error("图片压缩失败: %s", exc)
            filepath.unlink(missing_ok=True)
            await matcher.finish("图片压缩失败")
            return
        except OcrError as exc:
            logger.error("OCR 失败: %s", exc)
            filepath.unlink(missing_ok=True)
            await matcher.finish("OCR 服务不可用")
            return
        except EmbeddingError as exc:
            logger.error("Embedding 失败: %s", exc)
            filepath.unlink(missing_ok=True)
            await matcher.finish("Embedding 服务不可用")
            return
        except Exception as exc:
            logger.exception("添加表情包异常")
            filepath.unlink(missing_ok=True)
            await matcher.finish("添加失败，请查看日志")
            return

        # 成功：回复结果
        if result.reason == "no_text":
            await matcher.finish("未识别到文字，已移至 meme_no_text/")
        elif result.reason == "replaced":
            await matcher.finish("已成功添加（替换旧图）✅")
        else:
            await matcher.finish("已成功添加表情包 ✅")

    except Exception:
        # 未预期异常
        logger.exception("用户 %s 的 /add 处理异常", user_id)
        raise
    finally:
        if not is_cancelled(user_id):
            cancel(user_id)
        if index_manager is not None:
            _release_lock_safe(index_manager)
```

- [ ] **Step 3: 运行测试验证重构正确性**

Run: `uv run pytest tests/unit/plugins/test_meme_add.py -v`
Expected: 全部 PASS（16 个测试）

- [ ] **Step 4: 语法检查**

Run: `uv run python -m compileall bot/plugins/meme_add.py`
Expected: 无错误

- [ ] **Step 5: 提交**

```bash
git add bot/plugins/meme_add.py
git commit -m "refactor(meme_add): got_image 使用 try/finally 统一释放锁和清理会话

将 10 处 _release_lock_safe + cancel 重复调用收拢到 finally 块。
阶段分离：reject 在 try/finally 之外，处理流程在 try/except/finally 中。
与 meme_refresh.py 的 try/finally 模式保持一致。"
```
