# meme_add.py 锁释放模式重构设计

> 日期：2026-06-26
> 范围：`bot/plugins/meme_add.py`

---

## 1. 背景与问题

`meme_add.py` 的 `got_image` 函数中，`_release_lock_safe(index_manager)` 被调用 **10 次**，分布在每个异常分支和成功路径。同时 `cancel(user_id)` 也重复出现 8 次。这种散乱的资源释放模式：

- 增加遗漏风险（新增分支可能忘记释放）
- 降低代码可读性（核心业务逻辑被清理代码淹没）
- 与 `meme_refresh.py` 的 `try/finally` 模式不一致

### 当前重复分布

| 位置 | `_release_lock_safe` | `cancel` |
|------|:---:|:---:|
| `handle_add` 超时回调 (L104) | ✅ | — |
| `got_image` 下载失败 (L155) | ✅ | ✅ |
| `got_image` 扩展名不支持 (L163) | ✅ | ✅ |
| `got_image` 保存失败 (L181) | ✅ | ✅ |
| `got_image` CompressionError (L192) | ✅ | ✅ |
| `got_image` OcrError (L199) | ✅ | ✅ |
| `got_image` EmbeddingError (L206) | ✅ | ✅ |
| `got_image` 通用 Exception (L213) | ✅ | ✅ |
| `got_image` 成功路径 (L219) | ✅ | ✅ |
| `got_image` 最外层 except (L234) | ✅ | ✅ |

### 对比：meme_refresh.py 已有正确模式

```python
# meme_refresh.py:52-60
try:
    await bot.send(event, "正在刷新索引，请稍候...")
    result = await index_manager.sync_with_filesystem()
except Exception:
    logger.exception("sync_with_filesystem 执行失败")
    await refresh_cmd.finish("索引刷新失败，请查看日志")
    return
finally:
    index_manager.release_lock()
```

---

## 2. 约束

1. **锁跨函数持有**：锁在 `handle_add` 中通过 `acquire_lock()` 获取，在 `got_image` 中释放。两者是 NoneBot2 matcher 流程中的独立函数。
2. **reject 不释放锁**：`got_image` 中 `extract_image_urls` 返回空时执行 `reject`（等待用户重新发图片），此时锁必须保持持有。
3. **超时回调需要释放锁**：`handle_add` 中的 `timeout_session` 回调在用户超时未响应时释放锁，此时 `got_image` 可能尚未执行到 finally。
4. **cancel(user_id) 也需要统一清理**：与锁释放配对出现，应一并收拢。

---

## 3. 方案对比

### 方案 A：asynccontextmanager

将锁封装为异步上下文管理器。

**不可行原因**：锁的获取（`handle_add`）和释放（`got_image`）分布在两个独立函数中，`asynccontextmanager` 无法跨函数作用。若将获取移到 `got_image`，会改变锁的持有范围——当前锁从 `handle_add` 开始持有，确保等待用户发图片期间索引不被 `/refresh` 修改。

### 方案 B：阶段分离 + try/finally（✅ 推荐）

将 `got_image` 拆为两个阶段：
- 阶段 1：图片验证 + reject（不涉及锁释放）
- 阶段 2：处理流程（`try/except/finally` 统一释放锁）

**优点**：最小改动、与 `meme_refresh.py` 风格一致、不改变锁语义。
**缺点**：无。

### 方案 C：IndexManager 级别封装

在 `IndexManager` 上新增 `acquire_lock_context()` 异步上下文管理器。

**不采用原因**：`meme_refresh.py` 已用 `try/finally` 模式且仅一处调用，问题集中在 `meme_add.py`。为一个插件的问题引入公共 API 改动，属于过度设计。

---

## 4. 最终设计

### 4.1 got_image 重构结构

```python
async def got_image(bot, event, matcher, image_msg):
    user_id = event.get_user_id()
    index_manager = None

    try:
        # 会话有效性检查
        if is_cancelled(user_id):
            return

        index_manager = get_index_manager()

        # ── 阶段 1：图片验证（不涉及锁释放）──
        urls = extract_image_urls(image_msg)
        if not urls:
            await matcher.reject("请发送一张图片")
            return  # reject 后锁保持持有，会话继续等待

        # ── 阶段 2：处理流程（finally 统一释放锁）──
        image_url = urls[0]
        target_name = str(matcher.state.get("target_name", ""))

        try:
            image_data, response = await _download_image(image_url)
        except Exception as exc:
            logger.error("图片下载失败: %s", exc)
            await matcher.finish("图片下载失败")
            return

        ext = _get_extension(image_url, response)
        if ext is None or ext.lower() not in SUPPORTED_EXTENSIONS:
            await matcher.finish(f"不支持的图片格式: {ext or '未知'}")
            return

        filename = _build_filename(target_name, image_data, ext)
        filepath = resolve_unique_filename(MEMES_DIR, filename)
        filename = filepath.name

        MEMES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            filepath.write_bytes(image_data)
        except OSError as exc:
            logger.error("保存图片失败: %s", exc)
            await matcher.finish("图片保存失败")
            return

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
        logger.exception("用户 %s 的 /add 处理异常", user_id)
        raise
    finally:
        cancel(user_id)
        if index_manager is not None:
            _release_lock_safe(index_manager)
```

### 4.2 关于 finish() 的说明

原代码中错误分支使用 `await matcher.finish("...")`。`finish()` 是 NoneBot2 的终止动作，内部抛出 `FinishedException` 阻止后续代码执行。重构后保留 `finish()`，其后的 `return` 是死代码但显式表达控制流意图，便于阅读。

关键点：`FinishedException` 不会被外层 `except Exception` 捕获（它是 `BaseException` 子类），因此 finally 块中的清理逻辑不会被 `finish()` 干扰。

### 4.3 不变部分

| 组件 | 原因 |
|------|------|
| `_release_lock_safe` 函数 | 保留，finally 中需要吞异常保证清理不中断 |
| `handle_add` 超时回调 | 保留 `_release_lock_safe`，超时时 got_image 可能未执行到 finally |
| `handle_add` 锁获取逻辑 | 不变，锁的生命周期语义不变 |

### 4.4 消除的重复

| 项目 | 重构前 | 重构后 |
|------|--------|--------|
| `_release_lock_safe` 调用 | 10 处 | 2 处（finally + 超时回调） |
| `cancel(user_id)` 调用 | 8 处 | 1 处（finally） |

---

## 5. 变更清单

| 文件 | 变更 |
|------|------|
| `bot/plugins/meme_add.py` | 重构 `got_image` 函数，使用 `try/except/finally` 统一释放锁和清理会话 |

不涉及其他文件变更。

---

## 6. 测试要点

| 场景 | 预期 |
|------|------|
| 正常添加成功 | 锁释放、会话清理、回复成功消息 |
| 图片下载失败 | 锁释放、会话清理、回复失败消息 |
| 不支持的扩展名 | 锁释放、会话清理、回复格式错误 |
| 图片保存失败 | 锁释放、会话清理、回复保存失败 |
| CompressionError | 锁释放、会话清理、回复压缩失败、图片已删除 |
| OcrError | 锁释放、会话清理、回复 OCR 不可用、图片已删除 |
| EmbeddingError | 锁释放、会话清理、回复 Embedding 不可用、图片已删除 |
| 未知异常 | 锁释放、会话清理、回复添加失败 |
| reject（非图片消息） | 锁**不释放**、会话继续等待 |
| 用户超时未发图片 | 超时回调释放锁、清理会话、回复超时提示 |
| 会话被新命令覆盖 | `is_cancelled` 检查生效，提前返回 |
