# 日志滚动机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MemePilot bot 创建统一日志滚动机制：文件 `log/bot.log` (DEBUG) + stdout (INFO)，单文件 <= 1MB，保留 1 个备份。

**Architecture:** `bot/logging_config.py` 提供 `setup_logging()` 函数，使用 `RotatingFileHandler` + `StreamHandler`，通过 `logging.basicConfig` 挂载到 Root Logger。`bot.py` 入口调用一次即可。

**Tech Stack:** Python 标准库 `logging`、`logging.handlers.RotatingFileHandler`，零第三方依赖。

---

### Task 1: 编写单元测试

**Files:**
- Create: `tests/unit/test_logging_config.py`

**背景说明:** 测试需要覆盖设计文档第 8 节的 11 项用例。注意：`setup_logging()` 通过 `basicConfig` 修改 Root Logger 全局状态，测试间需用 `@pytest.fixture(autouse=True)` 在每个测试前重置 Root Logger handlers，避免互相干扰。另外 `setup_logging()` 内部创建 `log/` 目录，测试在项目根目录运行时会创建 `log/` 目录，测试结束后需清理（或至少确保不影响其他测试）。为避免测试间 handlers 累积，每个测试前清除 Root Logger 已有 handlers。

- [ ] **Step 1: 创建测试文件 `tests/unit/test_logging_config.py`**

```python
"""日志配置模块单元测试。

测试 setup_logging() 函数的行为：
handler 类型、级别、参数、日志写入能力。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.logging_config import setup_logging


@pytest.fixture(autouse=True)
def reset_logging() -> None:
    """每个测试前重置 Root Logger，隔离测试间状态。"""
    root = logging.getLogger()
    # 移除所有已有 handler
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.WARNING)


@pytest.fixture(autouse=True)
def cleanup_log_dir() -> None:
    """每个测试后清理 log/ 测试目录。"""
    yield
    log_dir = Path("log")
    if log_dir.exists():
        for f in log_dir.iterdir():
            f.unlink()
        log_dir.rmdir()


def _get_handlers_by_type(
    handler_type: type,
) -> list[logging.Handler]:
    """从 Root Logger 中获取指定类型的 handler。"""
    return [h for h in logging.getLogger().handlers if isinstance(h, handler_type)]


class TestSetupLogging:
    """setup_logging() 函数测试。"""

    def test_creates_log_directory(self) -> None:
        """调用 setup_logging() 后 log/ 目录应存在。"""
        # 确保目录初始不存在
        log_dir = Path("log")
        if log_dir.exists():
            for f in log_dir.iterdir():
                f.unlink()
            log_dir.rmdir()

        setup_logging()

        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_rotating_file_handler_added(self) -> None:
        """Root Logger 应包含 RotatingFileHandler。"""
        setup_logging()
        handlers = _get_handlers_by_type(RotatingFileHandler)
        assert len(handlers) == 1

    def test_stream_handler_added(self) -> None:
        """Root Logger 应包含 StreamHandler。"""
        setup_logging()
        handlers = _get_handlers_by_type(logging.StreamHandler)
        assert len(handlers) == 1

    def test_handlers_count(self) -> None:
        """Root Logger 恰好有 2 个 handler。"""
        setup_logging()
        assert len(logging.getLogger().handlers) == 2

    def test_root_logger_level_debug(self) -> None:
        """Root Logger level 应为 DEBUG。"""
        setup_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_file_handler_debug_level(self) -> None:
        """FileHandler level 应为 DEBUG。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.level == logging.DEBUG

    def test_stream_handler_info_level(self) -> None:
        """StreamHandler level 应为 INFO。"""
        setup_logging()
        sh = _get_handlers_by_type(logging.StreamHandler)[0]
        assert sh.level == logging.INFO

    def test_file_handler_max_bytes(self) -> None:
        """FileHandler maxBytes 应为 1_048_576 (1 MB)。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.maxBytes == 1_048_576

    def test_file_handler_backup_count(self) -> None:
        """FileHandler backupCount 应为 1。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.backupCount == 1

    def test_file_handler_encoding(self) -> None:
        """FileHandler encoding 应为 utf-8。"""
        setup_logging()
        fh = _get_handlers_by_type(RotatingFileHandler)[0]
        assert fh.encoding == "utf-8"

    def test_can_write_to_log_file(self) -> None:
        """写入一条 INFO 日志后，bot.log 中应包含目标字符串。"""
        setup_logging()

        test_logger = logging.getLogger("meme_bot_test")
        test_logger.info("测试日志写入")

        # 刷新 handler 确保写入磁盘
        for h in logging.getLogger().handlers:
            h.flush()

        log_file = Path("log") / "bot.log"
        assert log_file.exists()

        content = log_file.read_text(encoding="utf-8")
        assert "测试日志写入" in content

    def test_debug_not_in_stdout(self) -> None:
        """DEBUG 日志不应出现在 stdout（StreamHandler level 为 INFO）。"""
        setup_logging()
        sh = _get_handlers_by_type(logging.StreamHandler)[0]
        assert sh.level == logging.INFO
        # 验证 StreamHandler 过滤 DEBUG 消息
        assert sh.level > logging.DEBUG
```

**第 9 步变更说明:** `test_debug_not_in_stdout` 替代原有的 `test_can_write_to_log_file` 重复验证 — 但两者互补：一个验证文件写入，一个验证 stdout 过滤。同时在 `reset_logging` fixture 中增加 `h.close()` 避免文件句柄泄漏。

实际执行时由于 mock 的存在和测试环境，部分测试可能需要根据实际运行情况微调（如 `test_can_write_to_log_file` 需在 CWD 可写且 `log/` 目录可创建的环境下运行）。

- [ ] **Step 2: 运行测试验证全部失败（模块不存在）**

```bash
cd /home/northhalf/tmp/meme-pilot && uv run python -m pytest tests/unit/test_logging_config.py -v 2>&1 || true
```

预期：`ModuleNotFoundError: No module named 'bot.logging_config'`

- [ ] **Step 3: 提交测试文件**

```bash
git add tests/unit/test_logging_config.py
git commit -m "test: 添加日志配置模块单元测试（11 项，期望失败）"
```

---

### Task 2: 实现 bot/logging_config.py

**Files:**
- Create: `bot/logging_config.py`

- [ ] **Step 4: 创建 `bot/logging_config.py`**

```python
"""日志配置模块。

通过 setup_logging() 配置全局日志：
- RotatingFileHandler：写入 log/bot.log，DEBUG 级别，单文件 <= 1MB，保留 1 个备份。
- StreamHandler：输出到 stdout，INFO 级别。
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> None:
    """配置全局日志滚动机制。

    日志同时输出到：
    - stdout（INFO 级别及以上）
    - log/bot.log（DEBUG 级别及以上，单文件 <= 1MB，保留 1 个备份）

    格式：时间 - 模块名 - 级别 - 消息
    """
    LOG_DIR = Path("log")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"

    file_handler = RotatingFileHandler(
        LOG_DIR / "bot.log",
        maxBytes=1_048_576,
        backupCount=1,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[stream_handler, file_handler],
    )
```

- [ ] **Step 5: 运行测试验证全部通过**

```bash
cd /home/northhalf/tmp/meme-pilot && uv run python -m pytest tests/unit/test_logging_config.py -v
```

预期：11 项全部 PASS。

- [ ] **Step 6: 验证模块导入无语法错误**

```bash
cd /home/northhalf/tmp/meme-pilot && uv run python -c "from bot.logging_config import setup_logging; print('import ok')"
```

预期输出：`import ok`

- [ ] **Step 7: 提交**

```bash
git add bot/logging_config.py
git commit -m "feat(engine): 实现日志滚动配置模块 logging_config"
```

---

### Task 3: 更新 docs/process.md 并最终提交

- [ ] **Step 8: 在 `docs/process.md` 中追加日志模块完成记录**

```markdown
- [x] `bot/logging_config.py` — 日志滚动配置模块（RotatingFileHandler, 文件 DEBUG + stdout INFO, 单文件 <= 1MB 保留 1 备份）
- [x] `tests/unit/test_logging_config.py` — 11 项单元测试覆盖
```

操作：Edit `docs/process.md`，在现有内容末尾追加以上两行。

- [ ] **Step 9: 运行全部测试最终验证**

```bash
cd /home/northhalf/tmp/meme-pilot && uv run python -m pytest tests/ -v
```

预期：全部测试通过（含已有的 `test_keyword_searcher.py` 21 项 + 新增 11 项）。

- [ ] **Step 10: 最终提交**

```bash
git add docs/process.md
git commit -m "docs: 记录日志配置模块完成状态"
```
