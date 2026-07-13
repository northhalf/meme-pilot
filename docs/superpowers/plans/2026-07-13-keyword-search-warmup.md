# 关键词搜索冷启动预热 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Bot 对外可用前同步预加载 jieba 默认词典，使约 1,200 条索引下的首次模糊未命中搜索在目标环境中低于 50 ms。

**Architecture:** `KeywordSearcher` 提供同步生命周期方法 `warm_up()`，内部只调用 `jieba.initialize()` 并记录日志。`bot.bot::_on_startup()` 在创建 `KeywordSearcher` 后、构造依赖它的搜索器及注册 `app_state` 前调用该方法；初始化异常保持原类型向上传播并中止启动。

**Tech Stack:** Python 3.12、jieba 0.42.1+、pylcs、NoneBot2、pytest、ruff、ty。

---

## 实施约束

- 当前分支是 `main`。遵守项目规则，不执行 `git add`、`git commit` 或 `git merge`；每个任务完成后保留工作区差异供用户审核。
- 不新增 Python 包、LSP、格式化工具或系统工具。现有环境已提供 `jieba`、`pytest`、`ruff 0.15.21` 和 `ty 0.0.58`。
- 不修改搜索算法、阈值、结果排序、`limit` 行为或用户提示。
- 不为启动编排增加新的抽象层。`_on_startup()` 当前没有轻量依赖注入边界；若为一行 `warm_up()` 调用编写隔离测试，需要 mock OCR、Embedding、Store、IndexManager 和后台任务，超出本次设计范围。启动顺序由代码审查和真实启动验收覆盖。
- 设计依据：`docs/superpowers/specs/2026-07-13-keyword-search-warmup-design.md`。

## 文件结构

- Modify: `bot/engine/keyword_searcher.py`  
  增加 jieba 显式初始化依赖和 `KeywordSearcher.warm_up()`。
- Modify: `tests/unit/engine/test_keyword_searcher.py`  
  测试预热调用、成功日志和异常原样传播。
- Modify: `bot/bot.py`  
  在启动流程中同步预热关键词搜索器，并更新启动步骤说明。
- Modify: `docs/api/bot/engine/keyword_searcher.md`  
  记录 `warm_up()` 的公开 API 契约。
- Modify: `docs/api/bot/bot.md`  
  记录预热在应用注册前执行，以及失败时中止启动。
- Verify only: `docs/api/API.md`  
  根目录已经链接 `keyword_searcher.md` 和 `bot.md`，无需新增目录项。

### Task 1: 用测试驱动实现 `KeywordSearcher.warm_up()`

**Files:**
- Modify: `tests/unit/engine/test_keyword_searcher.py:1-8,71-83`
- Modify: `bot/engine/keyword_searcher.py:6-11,63-80`

- [ ] **Step 1: 在测试模块添加日志和 mock 依赖**

将 `tests/unit/engine/test_keyword_searcher.py` 顶部导入调整为：

```python
"""KeywordSearcher 单元测试。"""

import logging
from unittest.mock import Mock

import jieba
import pytest

from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import SearchResult
```

- [ ] **Step 2: 写出预热调用与成功日志的失败测试**

在 `TestInit` 后增加：

```python
class TestWarmUp:
    """KeywordSearcher.warm_up() 测试。"""

    def test_initializes_jieba_and_logs_success(
        self,
        searcher: KeywordSearcher,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """预热应初始化 jieba 并记录成功日志。"""
        initialize = Mock()
        monkeypatch.setattr(jieba, "initialize", initialize)

        with caplog.at_level(logging.INFO, logger="bot.engine.keyword_searcher"):
            searcher.warm_up()

        initialize.assert_called_once_with()
        assert "关键词搜索预热完成" in caplog.text
```

- [ ] **Step 3: 写出初始化异常原样传播的失败测试**

在同一测试类增加：

```python
    def test_propagates_initialization_error(
        self,
        searcher: KeywordSearcher,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """jieba 初始化失败时应原样传播异常。"""
        error = RuntimeError("词典加载失败")
        monkeypatch.setattr(jieba, "initialize", Mock(side_effect=error))

        with pytest.raises(RuntimeError) as exc_info:
            searcher.warm_up()

        assert exc_info.value is error
```

- [ ] **Step 4: 运行测试并确认因接口缺失而失败**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_keyword_searcher.py::TestWarmUp::test_initializes_jieba_and_logs_success \
  tests/unit/engine/test_keyword_searcher.py::TestWarmUp::test_propagates_initialization_error \
  -v
```

Expected: 两个测试均 FAIL，失败原因为 `AttributeError: 'KeywordSearcher' object has no attribute 'warm_up'`。

- [ ] **Step 5: 实现最小预热接口**

在 `bot/engine/keyword_searcher.py` 的第三方导入区增加 `import jieba`：

```python
import jieba
import jieba.posseg as pseg
import pylcs
```

在 `KeywordSearcher.__init__()` 后增加：

```python
    @timed(logger, "关键词搜索预热")
    def warm_up(self) -> None:
        """预热关键词搜索依赖。

        在 Bot 启动阶段加载 jieba 默认词典，避免首次模糊搜索承担初始化耗时。

        Raises:
            Exception: jieba 默认词典初始化失败时原样传播。
        """
        jieba.initialize()
        logger.info("关键词搜索预热完成")
```

不要捕获异常，也不要执行虚拟搜索或读取元数据。

- [ ] **Step 6: 运行预热测试并确认通过**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_keyword_searcher.py::TestWarmUp::test_initializes_jieba_and_logs_success \
  tests/unit/engine/test_keyword_searcher.py::TestWarmUp::test_propagates_initialization_error \
  -v
```

Expected: `2 passed`。

- [ ] **Step 7: 运行整个关键词搜索测试模块**

Run:

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py -q
```

Expected: `44 passed`，现有 42 个搜索契约测试无回归。

- [ ] **Step 8: 检查本任务代码质量**

Run:

```bash
ruff check bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
ruff format --check bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
ty check
```

Expected: 三条命令均成功；ruff 输出 `All checks passed!` 和 `2 files already formatted`，ty 输出 `All checks passed!`。

- [ ] **Step 9: 停在审核点，不提交**

Run:

```bash
git diff -- bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git status --short
```

Expected: 只显示本任务的两个已修改文件，以及此前已批准但尚未提交的设计/计划文档；不执行 `git add` 或 `git commit`。

### Task 2: 将同步预热接入 Bot 启动流程

**Files:**
- Modify: `bot/bot.py:1-8,68-79,103-119`

- [ ] **Step 1: 更新模块级启动流程说明**

把 `bot/bot.py` 模块 docstring 的第 2 步改为：

```python
2. 注册 startup hook：初始化 engine 服务并预热关键词搜索，后台执行首次索引同步
```

- [ ] **Step 2: 更新 `_on_startup()` 的流程 docstring**

将步骤 4–7 改为：

```python
    4. 创建 AIMatcher / KeywordSearcher，预热关键词搜索
    5. 创建 IndexManager 并加载索引
    6. 注册到 app_state 供插件获取（Bot 立即可用）
    7. 后台执行 refresh()（不阻塞启动）
```

- [ ] **Step 3: 在依赖服务构造前同步预热**

将 `keyword_searcher` 创建处改为：

```python
    keyword_searcher = KeywordSearcher(metadata_store)
    keyword_searcher.warm_up()
    random_searcher = RandomSearcher(metadata_store, keyword_searcher)
    semantic_searcher = SemanticSearcher(metadata_store, vector_store)
    combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)
```

调用必须位于：

- `KeywordSearcher(metadata_store)` 之后；
- `RandomSearcher` 和 `CombinedSearcher` 构造之前；
- `IndexManager.load()`、`init_app()` 和 `asyncio.create_task()` 之前。

不要添加 `try/except`。`warm_up()` 抛出的异常应直接中止 `_on_startup()`。

- [ ] **Step 4: 检查启动文件的语法、格式和类型**

Run:

```bash
uv run python -m compileall -q bot/bot.py
ruff check bot/bot.py
ruff format --check bot/bot.py
ty check
```

Expected: 所有命令退出码为 0；ruff 和 ty 均报告通过。

- [ ] **Step 5: 检查调用顺序**

Run:

```bash
rg -n -C 4 "keyword_searcher =|warm_up|init_app|create_task" bot/bot.py
```

Expected: 输出顺序为创建 `KeywordSearcher`、调用 `warm_up()`、创建依赖搜索器、`init_app()`、`asyncio.create_task()`。

- [ ] **Step 6: 运行相关回归测试**

Run:

```bash
uv run pytest tests/unit/engine/test_keyword_searcher.py tests/unit/test_bot.py -q
```

Expected: `55 passed`。

- [ ] **Step 7: 停在审核点，不提交**

Run:

```bash
git diff -- bot/bot.py bot/engine/keyword_searcher.py tests/unit/engine/test_keyword_searcher.py
git status --short
```

Expected: 启动流程只增加一次同步预热调用和对应说明；不执行 `git add` 或 `git commit`。

### Task 3: 同步更新公开 API 文档

**Files:**
- Modify: `docs/api/bot/engine/keyword_searcher.md:14-26`
- Modify: `docs/api/bot/bot.md:49-64`
- Verify only: `docs/api/API.md:5-35`

- [ ] **Step 1: 在关键词搜索 API 中记录 `warm_up()`**

在 `docs/api/bot/engine/keyword_searcher.md` 的 `__init__` 与 `search` 之间增加：

```markdown
### `warm_up() -> None`

在 Bot 启动阶段显式加载 jieba 默认词典，避免首次进入 LCS 模糊回退时承担词典惰性初始化耗时。该方法不读取元数据，也不执行关键词搜索。

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | jieba 默认词典初始化完成 |
| **异常** | `Exception` | jieba 初始化异常保持原类型向上传播，调用方应中止启动 |

预热使用 `@timed(logger, "关键词搜索预热")` 记录耗时；成功时记录“关键词搜索预热完成”。

---
```

- [ ] **Step 2: 更新 Bot 启动时序文档**

将 `docs/api/bot/bot.md` 中 `_on_startup()` 的步骤 3–6 改为：

```markdown
3. 创建 `MetadataStore(str(INDEX_DB_PATH))` 与 `VectorStore(str(CHROMA_DIR))`，再创建 `AIMatcher(metadata_store, vector_store, embedding_provider, rerank_provider)` 与 `KeywordSearcher(metadata_store)`
4. 调用 `KeywordSearcher.warm_up()` 同步加载 jieba 默认词典；失败时异常向上传播，Bot 不注册全局状态、不启动后台同步
5. 创建 `RandomSearcher`、`SemanticSearcher`、`CombinedSearcher` 和 `IndexManager(...)`，并调用 `IndexManager.load()`
6. `app_state.init_app(...)` 注册全局单例，然后通过 `asyncio.create_task(_background_sync(index_manager))` 启动后台索引同步
```

在该节行为表增加：

```markdown
| **关键词预热** | — | `init_app()` 前同步完成；首个模糊搜索不再加载 jieba 词典 |
| **预热失败** | — | 异常向上传播并中止启动，不进入降级模式 |
```

- [ ] **Step 3: 确认 API 根目录无需修改**

Run:

```bash
rg -n '\[`bot`|\[`keyword_searcher`' docs/api/API.md
```

Expected: `docs/api/API.md` 已包含 `bot/bot.md` 和 `bot/engine/keyword_searcher.md` 的链接。不要新增重复目录项。

- [ ] **Step 4: 检查文档格式和内容一致性**

Run:

```bash
rg -n -C 3 "warm_up|关键词预热|预热失败" \
  docs/api/bot/engine/keyword_searcher.md \
  docs/api/bot/bot.md

git diff --check
```

Expected: 两份模块文档描述同一启动顺序和失败策略；`git diff --check` 无输出并以 0 退出。

- [ ] **Step 5: 停在审核点，不提交**

Run:

```bash
git diff -- \
  docs/api/bot/engine/keyword_searcher.md \
  docs/api/bot/bot.md \
  docs/api/API.md
git status --short
```

Expected: `docs/api/API.md` 无差异，两个模块文档存在预期差异；不执行 `git add` 或 `git commit`。

### Task 4: 完整验证与目标环境性能验收

**Files:**
- Verify: `bot/engine/keyword_searcher.py`
- Verify: `bot/bot.py`
- Verify: `tests/unit/engine/test_keyword_searcher.py`
- Verify: `docs/api/bot/engine/keyword_searcher.md`
- Verify: `docs/api/bot/bot.md`

- [ ] **Step 1: 运行全部单元测试**

Run:

```bash
uv run pytest tests/unit/ -q
```

Expected: 全部测试通过，无失败、错误或跳过新增。

- [ ] **Step 2: 运行项目静态检查**

Run:

```bash
ruff check bot tests
ruff format --check bot tests
ty check
uv run python -m compileall -q bot tests
```

Expected:

- `ruff check` 输出 `All checks passed!`；
- `ruff format --check` 报告所有文件已格式化；
- `ty check` 输出 `All checks passed!`；
- `compileall` 退出码为 0。

- [ ] **Step 3: 用独立进程执行约 1,200 条数据的预热后模糊搜索烟雾测试**

Run:

```bash
uv run python - <<'PY'
from time import perf_counter

from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry


class SmokeMetadataStore:
    def __init__(self) -> None:
        self._entries = {
            entry_id: MemeEntry(
                id=entry_id,
                image_path=f"smoke_{entry_id}.jpg",
                text=f"示例文本{entry_id}君子之交淡如水",
            )
            for entry_id in range(1, 1201)
        }

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


searcher = KeywordSearcher(SmokeMetadataStore())
start = perf_counter()
searcher.warm_up()
warm_up_ms = (perf_counter() - start) * 1000

start = perf_counter()
results = searcher.search("不耻下问")
search_ms = (perf_counter() - start) * 1000

print(f"warm_up_ms={warm_up_ms:.2f}")
print(f"search_ms={search_ms:.2f}")
print(f"result_count={len(results)}")
PY
```

Expected:

- 首次进程启动中的 `warm_up_ms` 吸收原先约 600–800 ms 的一次性初始化成本；
- `search_ms` 明显低于未预热基线 610–785 ms，开发机上应接近既有热态的几毫秒；
- `result_count=0`。

此烟雾测试不以开发机时间作为 CI 硬断言。`< 50 ms` 只在目标部署环境验收。

- [ ] **Step 4: 审查最终差异**

Run:

```bash
git diff --check
git diff --stat
git diff -- \
  bot/engine/keyword_searcher.py \
  bot/bot.py \
  tests/unit/engine/test_keyword_searcher.py \
  docs/api/bot/engine/keyword_searcher.md \
  docs/api/bot/bot.md
git status --short
```

Expected: 只有获批范围内的源码、测试和 API 文档发生变化，外加设计与计划文档；无搜索算法、配置或依赖变更。

- [ ] **Step 5: 经用户授权后部署到目标环境**

该步骤会重建并重启 Bot，执行前先取得用户确认。授权后运行：

```bash
docker compose build bot && docker compose up -d bot
docker compose logs --since=2m bot
```

Expected:

- Bot 日志先出现“关键词搜索预热完成”；
- 随后出现 `关键词搜索预热 完成，耗时 ... ms`；
- `MemePilot 启动完成` 出现在预热日志之后；
- 无 jieba 初始化异常。

- [ ] **Step 6: 在目标环境验收首个用户查询**

Bot 重启后，由用户依次发送：

1. 精确不命中的“不耻下问”；
2. 精确命中的“君子”。

检查日志：

```bash
docker compose logs --since=5m bot | rg "关键词搜索预热|keyword='不耻下问'|keyword='君子'|关键词搜索 完成"
```

Expected:

- “不耻下问”是本次启动后的首次模糊回退，`关键词搜索 完成，耗时 < 50 ms`；
- 搜索结果与改动前一致；
- “君子”仍走精确子串路径，结果不变且保持毫秒级；
- 用户请求日志中不再出现 600–800 ms 的 jieba 冷启动延迟。

- [ ] **Step 7: 向用户提交验证结果，不提交 Git 变更**

报告以下证据：

- 单元测试、ruff、ty、compileall 的实际结果；
- 本地烟雾测试的 `warm_up_ms`、`search_ms` 和结果数量；
- 若用户授权部署，报告目标环境首次模糊搜索和精确搜索的实际耗时；
- 若未授权部署，明确标记目标环境 `< 50 ms` 验收尚未执行；
- 当前 `git status --short`。

不要执行 `git add`、`git commit` 或 `git merge`，等待用户审核工作区差异。
