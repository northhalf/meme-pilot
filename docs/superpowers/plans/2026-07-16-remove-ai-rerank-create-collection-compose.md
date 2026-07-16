# 删除 AI 精排、创建合集与镜像部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Dispatch a fresh implementation subagent for every numbered task, then run the skill's specification-review and code-quality-review gates before moving to the next task.

**Goal:** 删除 `/ai` 与 DeepSeek 精排链路，新增原子化 `/collection create <名称>`，并让默认 Docker Compose 拉取 `northhalf/meme-pilot:latest`，同时保留独立本地构建入口。

**Architecture:** `/collection` 插件只负责命令协议和错误映射；名称规则放在合集领域层，目录与 SQLite 的一致性由 `IndexManager` 通过现有 Write Worker 和写锁维护。AI 删除、合集创建、部署配置和当前文档按独立提交推进；共享 `index_manager.py` 的任务必须串行，避免 subagent 冲突。

**Tech Stack:** Python 3.12、NoneBot2、sqlite3、pathlib、现有 IndexManager Write Worker、pytest、ruff、ty、Docker Compose。

## Global Constraints

- 实施前必须从 `main` 创建新分支：`feat/remove-ai-add-collection-compose`。
- 用户已授权在该新分支执行 `git add` 和 `git commit`；禁止把该分支合并、rebase 或 fast-forward 到 `main`。
- 使用 fresh subagent 逐任务实施；每个任务完成后由主代理检查提交、运行目标测试，并执行 specification review → code quality review。
- 只有主代理可以调度代理；所有实施、修复和审查子代理禁止调用 Agent、Workflow 或启动任何下级子代理。
- 为尽快完成，任务按本文顺序连续执行；Task 1 与 Task 3 都修改 `bot/engine/index_manager.py`，严禁并行。
- Python 函数必须有类型标注；函数 docstring 使用中文 Google 风格。
- 不使用 `from __future__ import annotations`。
- 不新增 Python 第三方依赖、LSP、系统工具或格式化工具。
- Python 类型检查使用 `ty`；lint/格式化使用 `ruff`。
- 不修改数据库 Schema，不生成迁移脚本。
- `/ai` 完整删除；`/sim`、Embedding、OpenAI 兼容 OCR 保持不变。
- `/collection create <名称>` 仅限授权用户私聊、直接执行、无短命令、成功后不自动切换合集。
- 名称去除首尾空白；禁止空名、任何内部 Unicode 空白、`.`、`..`、隐藏名、`/`、`\`、NUL、`全局`、`全部合集`。
- 已有非符号链接普通目录可以登记；不扫描、不 OCR、不 Embedding，图片等待 `/refresh`。
- 默认 `docker-compose.yml` 必须使用 `northhalf/meme-pilot:latest` 和 `pull_policy: always`。
- 本地构建必须使用完整独立的 `docker-compose.build.yml`。
- 历史 `docs/superpowers/specs/` 与 `docs/superpowers/plans/` 文件不追溯修改；只提交本次新 spec 和 plan。

## Execution Protocol

1. 主代理完成 Task 0，创建分支并提交本次 spec/plan。
2. 从 Task 1 开始，每个任务调用 `superpowers:subagent-driven-development`，给 fresh subagent 传入该任务完整内容、当前分支名和最新提交 SHA。
3. 实施 subagent 只能修改当前任务列出的文件；发现跨任务需求时先报告，不擅自扩大范围。
4. subagent 完成并提交后，主代理检查 `git show --stat --oneline HEAD` 和 `git status --short`。
5. 依次运行 specification reviewer 和 code-quality reviewer；审查修复仍提交到当前 feature branch。
6. 所有任务结束后调用 `superpowers:requesting-code-review` 和 `verify`；不得调用任何合并到 `main` 的流程。

## File Structure Map

### 删除

- `bot/plugins/ai.py`：删除 `/ai` 命令。
- `bot/engine/ai_matcher.py`：删除 AI 候选和匹配器。
- `bot/engine/rerank_service.py`：删除 DeepSeek 精排客户端。
- `tests/unit/plugins/test_ai.py`
- `tests/unit/engine/test_ai_matcher.py`
- `tests/unit/engine/test_rerank_service.py`
- `tests/integration/test_ai_matcher_api.py`
- `tests/integration/test_rerank_service_api.py`

### 新增

- `bot/plugins/collection.py`：`/collection create` 命令协议。
- `tests/unit/plugins/test_collection.py`：插件权限、解析、回复和会话清理。
- `tests/unit/engine/test_index_manager_create_collection.py`：目录/SQLite 原子创建与补偿。
- `docker-compose.build.yml`：完整本地构建部署。
- `docker-compose.build.override.yml.example`：本地构建代理覆盖示例。

### 修改

- `bot/engine/collection_manager.py`：扩展现有 `InvalidCollectionNameError`，提供唯一的合集名称领域校验。
- `scripts/migrate_meme_collections.py`：删除脚本内重复校验，复用 engine 领域校验。
- `bot/engine/index_types.py`：创建结果、领域错误、WriteOp 和请求字段。
- `bot/engine/index_manager.py`：删除 AI 匹配入口；新增 Write Worker 创建合集操作。
- `bot/engine/__init__.py`：删除 AI/Rerank 导出；导出创建合集接口。
- `bot/engine/protocols.py`：删除 AIMatcher 过期说明。
- `bot/engine/openai_embedding.py`：把过期的 ai_matcher/AIMatcher 协议说明改为当前 `protocols.EmbeddingProvider` 调用者。
- `bot/bot.py`：删除 AI/Rerank 初始化和注入。
- `bot/app_state.py`：删除 AIMatcher 全局状态。
- `bot/plugins/_help_text.py`：删除 `/ai`，增加私聊 `/collection create`。
- `tests/unit/engine/test_collection_manager.py`：名称校验测试。
- `tests/unit/test_migrate_meme_collections.py`：迁移脚本复用统一名称校验的回归测试。
- `tests/unit/engine/test_index_manager.py`：移除 AIMatcher fixture 和测试。
- `tests/unit/test_app_state.py`：移除 AIMatcher 状态测试。
- `tests/unit/plugins/test_help.py`：更新精确帮助文本。
- `tests/unit/plugins/test_plain_text.py`：固定 `/ai` 删除后的未知命令行为。
- `docker-compose.yml`：默认拉取发布镜像。
- `.env.example`：删除 DeepSeek/Rerank 配置。
- `.github/workflows/ci.yml`：删除精排 Secret 和说明。
- `.gitignore`：忽略新的本地构建 override 文件。
- `README.md`、`README-containers.md`、`docs/PRD.md`、`CONTEXT.md`：同步当前行为和部署方式。

---

### Task 0: 创建 feature branch 并保存规格/计划

**Owner:** 主代理，不派发 subagent。

**Files:**
- Add: `docs/superpowers/specs/2026-07-16-remove-ai-rerank-create-collection-compose-design.md`
- Add: `docs/superpowers/plans/2026-07-16-remove-ai-rerank-create-collection-compose.md`

**Interfaces:**
- Consumes: 当前 `main` HEAD 与已经写入工作区的 spec/plan。
- Produces: feature branch `feat/remove-ai-add-collection-compose`，后续 subagent 只在此分支提交。

- [ ] **Step 1: 确认当前分支和工作区**

Run:

```bash
git branch --show-current
git status --short
```

Expected:

- 当前分支为 `main`；
- 仅本次 spec/plan 为未跟踪或未提交文件；如存在其他修改，停止并报告，不覆盖。

- [ ] **Step 2: 从当前 main 创建 feature branch**

Run:

```bash
git switch -c feat/remove-ai-add-collection-compose
```

Expected: 输出 `Switched to a new branch 'feat/remove-ai-add-collection-compose'`。

- [ ] **Step 3: 运行修改前单元测试基线**

Run:

```bash
uv run pytest tests/unit/ -q
```

Expected: 全部通过；若失败，保存完整失败输出并停止，不能把既有失败归因于本任务。

- [ ] **Step 4: 提交已批准的 spec 和 plan**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-16-remove-ai-rerank-create-collection-compose-design.md \
  docs/superpowers/plans/2026-07-16-remove-ai-rerank-create-collection-compose.md
git commit -m "docs: 记录删除精排与合集创建实施方案"
```

Expected: 生成一个仅包含两个文档的提交。

- [ ] **Step 5: 确认没有切回或修改 main**

Run:

```bash
test "$(git branch --show-current)" = "feat/remove-ai-add-collection-compose"
git status --short
```

Expected: 命令成功，工作区干净。

---

### Task 1: 删除 `/ai`、AIMatcher 与 DeepSeek 精排链路

**Owner:** fresh implementation subagent；完成后两阶段审查。

**Files:**
- Delete: `bot/plugins/ai.py`
- Delete: `bot/engine/ai_matcher.py`
- Delete: `bot/engine/rerank_service.py`
- Delete: `tests/unit/plugins/test_ai.py`
- Delete: `tests/unit/engine/test_ai_matcher.py`
- Delete: `tests/unit/engine/test_rerank_service.py`
- Delete: `tests/integration/test_ai_matcher_api.py`
- Delete: `tests/integration/test_rerank_service_api.py`
- Modify: `bot/bot.py:32-40,77-168`
- Modify: `bot/app_state.py:1-192`
- Modify: `bot/engine/__init__.py:9-45,97-143`
- Modify: `bot/engine/index_manager.py:21-22,115-209,443-486,628-654`
- Modify: `bot/engine/protocols.py:1-20`
- Modify: `bot/engine/openai_embedding.py:1-35`
- Modify: `tests/unit/engine/test_index_manager.py:1-20,432-470,2876-2895`
- Modify: `tests/unit/test_app_state.py:11-38,44-71,132-153`
- Modify: `tests/unit/plugins/test_help.py:76-93`
- Modify: `tests/unit/plugins/test_plain_text.py:70-90`
- Modify: `bot/plugins/_help_text.py:7-23`

**Interfaces:**
- Consumes: 现有 `/sim` 和 `SemanticSearcher`，不得修改其签名。
- Produces: 运行时代码中不再存在 `AIMatcher`、`AIMatchCandidate`、`AIMatchResult`、`RerankService`、`ai_match()` 或 `/ai` 命令注册。

- [ ] **Step 1: 先修改帮助测试，建立会失败的删除断言**

在 `tests/unit/plugins/test_help.py` 的精确行列表中删除：

```python
"/ai <自然语言描述>：按自然语言描述匹配表情包",
```

在 `tests/unit/plugins/test_plain_text.py::TestHandleUnknownSlashCommand` 增加：

```python
@pytest.mark.asyncio
@patch.object(plain_text, "is_authorized", return_value=True)
async def test_removed_ai_command_replies_unknown(
    self, mock_auth: MagicMock
) -> None:
    """删除后的 /ai 应进入未知命令兜底，帮助中不再展示 /ai。"""
    _reset_mocks()
    matcher = _make_matcher()

    await handle_plain_text(
        _make_bot(), _make_event("111", "/ai 加班心累"), matcher
    )

    matcher.finish.assert_awaited_once()
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "未知命令" in text
    assert "/ai <" not in text
```

- [ ] **Step 2: 运行帮助测试，确认先失败**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_help.py::TestHandleHelp::test_authorized_user_receives_help \
  tests/unit/plugins/test_plain_text.py::TestHandleUnknownSlashCommand::test_removed_ai_command_replies_unknown \
  -v
```

Expected: 至少帮助测试失败，因为生产帮助文本仍包含 `/ai`。

- [ ] **Step 3: 删除 AI/Rerank 文件和专用测试**

Run:

```bash
rm \
  bot/plugins/ai.py \
  bot/engine/ai_matcher.py \
  bot/engine/rerank_service.py \
  tests/unit/plugins/test_ai.py \
  tests/unit/engine/test_ai_matcher.py \
  tests/unit/engine/test_rerank_service.py \
  tests/integration/test_ai_matcher_api.py \
  tests/integration/test_rerank_service_api.py
```

Expected: 八个文件被删除。

- [ ] **Step 4: 从启动入口删除 AI/Rerank 构造**

将 `bot/bot.py` 的 engine import 保留为：

```python
from bot.engine import (
    CollectionManager,
    ImageOptimizer,
    IndexManager,
    KeywordSearcher,
    MetadataStore,
    VectorStore,
)
```

`_on_startup()` 中删除 `RerankService` 和 `AIMatcher` 创建，搜索器部分变为：

```python
# 5. 创建搜索服务（IndexManager 内部持锁后委托调用）
keyword_searcher = KeywordSearcher(metadata_store)
random_searcher = RandomSearcher(metadata_store, keyword_searcher)
semantic_searcher = SemanticSearcher(metadata_store, vector_store)
combined_searcher = CombinedSearcher(metadata_store, keyword_searcher)
```

`IndexManager(...)` 删除 `ai_matcher=...`，`init_app(...)` 删除 `ai_matcher=...`。同时把启动 docstring 中的 `Rerank`、`AIMatcher` 步骤改成当前实际流程。

- [ ] **Step 5: 从 app_state 删除 AIMatcher 状态**

`bot/app_state.py` 必须删除：

```python
AIMatcher
_ai_matcher
ai_matcher: AIMatcher | None = None
get_ai_matcher()
```

`init_app()` 的剩余参数顺序保持现状：

```python
def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    vector_store: VectorStore,
    ocr_service: OcrProvider,
    embedding_service: EmbeddingProvider,
    image_optimizer: ImageOptimizer | None = None,
    keyword_searcher: KeywordSearcher | None = None,
    random_searcher: RandomSearcher | None = None,
    semantic_searcher: SemanticSearcher | None = None,
    combined_searcher: CombinedSearcher | None = None,
    collection_manager: CollectionManager | None = None,
) -> None:
```

同步删除模块说明、global 声明和赋值中的 AIMatcher。

- [ ] **Step 6: 从 IndexManager 删除 AI 匹配入口**

在 `bot/engine/index_manager.py` 删除：

```python
from .ai_matcher import AIMatcher, AIMatchResult
```

从 `__init__()` 删除 `ai_matcher` 参数、docstring、`self._ai_matcher`。完整删除下列方法：

```python
_ai_match_locked()
ai_match()
ai_match_for_scope()
```

保留 `semantic_search()` 和 `semantic_search_for_scope()` 原样。`EmbeddingProvider` 仍由语义检索和索引写入使用，不能删除。

- [ ] **Step 7: 清理 engine 导出和协议说明**

`bot/engine/__init__.py` 删除 `ai_matcher`、`rerank_service` import 和以下 `__all__` 项：

```python
"AIMatcher"
"AIMatchCandidate"
"AIMatchResult"
"RerankService"
```

`bot/engine/protocols.py` 把提及 `AIMatcher` 的说明改成只描述当前调用者，例如：

```python
"""共享 provider 协议。

IndexManager、SemanticSearcher 等模块通过这些协议调用外部服务，
便于测试时注入 mock。
"""
```

不得删除 `EmbeddingProvider`。

- [ ] **Step 8: 清理现有测试 fixture 和 app_state 测试**

`tests/unit/engine/test_index_manager.py`：

- 删除 `from bot.engine.ai_matcher import AIMatcher`；
- fixture 中删除 `ai_matcher = AIMatcher(...)`；
- `IndexManager(...)` 删除 `ai_matcher=ai_matcher`；
- 删除 `test_ai_match_filters_by_collection`。

`tests/unit/test_app_state.py`：

- fixture 前后删除 `_ai_matcher = None`；
- `test_sets_all_globals` 删除 `ai` 构造、注入和断言；
- 完整删除 `TestGetAiMatcher`。

- [ ] **Step 9: 更新生产帮助文本**

`bot/plugins/_help_text.py` 私聊帮助中删除：

```text
/ai <自然语言描述>：按自然语言描述匹配表情包
```

本任务暂不加入 `/collection`；该命令由 Task 4 添加。

- [ ] **Step 10: 运行目标测试**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_help.py \
  tests/unit/plugins/test_plain_text.py \
  tests/unit/test_app_state.py \
  tests/unit/engine/test_index_manager.py \
  -q
```

Expected: 全部通过。

- [ ] **Step 11: 验证运行时符号完全删除**

Run:

```bash
if rg -n \
  'AIMatcher|AIMatchCandidate|AIMatchResult|RerankService|ai_match_for_scope|def ai_match|on_command\("ai"' \
  bot tests; then
  exit 1
fi
```

Expected: `rg` 无输出并以 1 退出，外层 `if` 整体成功。

- [ ] **Step 12: 运行单元测试并提交**

Run:

```bash
uv run pytest tests/unit/ -q
git add -A
git commit -m "refactor(ai): 删除 /ai 与 DeepSeek 精排链路"
```

Expected: 单元测试通过；提交只包含本任务列出的运行时代码和测试删除/修改。

---

### Task 2: 定义合集名称和创建操作领域契约

**Owner:** fresh implementation subagent；完成后两阶段审查。

**Files:**
- Modify: `bot/engine/collection_manager.py:37-43`
- Modify: `scripts/migrate_meme_collections.py:20-30,111-138`
- Modify: `bot/engine/index_types.py:1-126`
- Modify: `tests/unit/engine/test_collection_manager.py`
- Modify: `tests/unit/test_migrate_meme_collections.py:1-25,295-315`

**Interfaces:**
- Consumes: `GLOBAL_COLLECTION_NAME`、`ALL_COLLECTIONS_NAME`、`MemeCollection`。
- Produces:
  - `validate_collection_name(raw: str) -> str`
  - `InvalidCollectionNameError`
  - `CollectionAlreadyExistsError(collection: MemeCollection)`
  - `CollectionPathConflictError(name: str)`
  - `CollectionCreateError`
  - `CreateCollectionResult(collection: MemeCollection, registered_existing_directory: bool)`
  - `WriteOp.CREATE_COLLECTION`
  - `_WriteRequest.collection_name: str`

- [ ] **Step 1: 为名称校验编写失败测试**

在 `tests/unit/engine/test_collection_manager.py` 增加 import：

```python
from bot.engine.collection_manager import (
    InvalidCollectionNameError,
    validate_collection_name,
)
```

增加测试：

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("新三国", "新三国"),
        ("  新三国  ", "新三国"),
        ("collection-01", "collection-01"),
        ("合集_01", "合集_01"),
    ],
)
def test_validate_collection_name_accepts_safe_names(
    raw: str, expected: str
) -> None:
    assert validate_collection_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        ".",
        "..",
        ".隐藏",
        "新 三国",
        "新\t三国",
        "新\n三国",
        "新　三国",
        "a/b",
        r"a\b",
        "a\x00b",
        "全局",
        "全部合集",
    ],
)
def test_validate_collection_name_rejects_invalid_names(raw: str) -> None:
    with pytest.raises(InvalidCollectionNameError):
        validate_collection_name(raw)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_collection_manager.py::test_validate_collection_name_accepts_safe_names \
  tests/unit/engine/test_collection_manager.py::test_validate_collection_name_rejects_invalid_names \
  -v
```

Expected: collection error，函数和异常尚未定义。

- [ ] **Step 3: 实现名称校验**

`bot/engine/collection_manager.py` 已经定义 `InvalidCollectionNameError`，不得重复定义。保留现有 class，并在它后面加入：

```python
_RESERVED_COLLECTION_NAMES = frozenset(
    {GLOBAL_COLLECTION_NAME, ALL_COLLECTIONS_NAME}
)


def validate_collection_name(raw: str) -> str:
    """校验并规范化合集名称。

    Args:
        raw: 用户输入的合集名称。

    Returns:
        去除首尾空白后的合法名称。

    Raises:
        InvalidCollectionNameError: 名称为空、含内部空白、使用保留名，
            或不能映射为安全单层目录。
    """
    name = raw.strip()
    invalid = (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or name in _RESERVED_COLLECTION_NAMES
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or any(character.isspace() for character in name)
    )
    if invalid:
        raise InvalidCollectionNameError(raw)
    return name
```

确保文件从 `.types` 导入：

```python
ALL_COLLECTIONS_NAME
GLOBAL_COLLECTION_NAME
```

- [ ] **Step 4: 让迁移脚本复用 engine 领域校验**

在 `scripts/migrate_meme_collections.py` 中删除脚本内现有的 `InvalidCollectionNameError` 和 `validate_collection_name()` 定义，增加：

```python
from bot.engine.collection_manager import (
    InvalidCollectionNameError,
    validate_collection_name,
)
```

保留 `run_move_root()` 的调用方式不变。更新 `tests/unit/test_migrate_meme_collections.py`，确认迁移入口使用同一规则：

```python
@pytest.mark.parametrize("name", ["新 三国", "新\t三国", "新　三国", "全局", "全部合集"])
def test_validate_collection_name_rejects_domain_invalid_names(name: str) -> None:
    with pytest.raises(InvalidCollectionNameError):
        validate_collection_name(name)
```

Run:

```bash
uv run pytest \
  tests/unit/engine/test_collection_manager.py \
  tests/unit/test_migrate_meme_collections.py \
  -q
```

Expected: 两组测试使用同一个异常类型和 validator，全部通过。

- [ ] **Step 5: 运行名称测试确认通过**

Run:

```bash
uv run pytest tests/unit/engine/test_collection_manager.py -q
```

Expected: 全部通过。

- [ ] **Step 6: 在 index_types 定义创建结果与错误**

把 `bot/engine/index_types.py` 的类型 import 改为：

```python
from .types import CollectionSelection, MemeCollection, MemePublicId
```

在 `RefreshInProgressError` 后加入：

```python
class CollectionAlreadyExistsError(RuntimeError):
    """待创建名称已被普通合集使用。"""

    def __init__(self, collection: MemeCollection) -> None:
        """初始化重名错误。

        Args:
            collection: 已存在的合集快照。
        """
        self.collection = collection
        super().__init__(f"合集已存在: {collection.id}:{collection.name}")


class CollectionPathConflictError(RuntimeError):
    """同名文件系统路径不是可登记的普通目录。"""

    def __init__(self, name: str) -> None:
        """初始化路径冲突错误。

        Args:
            name: 已完成领域校验的合集名称。
        """
        self.name = name
        super().__init__(f"合集目录路径冲突: {name}")


class CollectionCreateError(RuntimeError):
    """创建合集失败且可能需要人工检查文件系统。"""


@dataclass(frozen=True, slots=True)
class CreateCollectionResult:
    """创建合集后的持久状态快照。

    Attributes:
        collection: 已登记的普通合集。
        registered_existing_directory: 是否登记了用户原本已有的目录。
    """

    collection: MemeCollection
    registered_existing_directory: bool
```

- [ ] **Step 7: 扩展 WriteOp 和 _WriteRequest**

`WriteOp` 改为：

```python
class WriteOp(Enum):
    """Write Worker 操作类型枚举。"""

    ADD = auto()
    EDIT_TEXT = auto()
    SET_SPEAKER = auto()
    ADD_TAG = auto()
    DELETE = auto()
    MOVE = auto()
    CREATE_COLLECTION = auto()
```

`_WriteRequest.future` 联合类型加入 `CreateCollectionResult`，并增加字段：

```python
future: "asyncio.Future[AddResult | EditTextResult | SetSpeakerResult | AddTagResult | DeleteResult | MoveResult | CreateCollectionResult]"
collection_name: str = ""
```

同步更新 `_WriteRequest` docstring 中的操作列表和 `collection_name` 含义。把 `IndexAddCancelledError` docstring 从仅 `/add` 改为通用写入任务：

```python
class IndexAddCancelledError(RuntimeError):
    """写入任务因刷新或关闭而被取消。"""
```

- [ ] **Step 8: 运行类型相关测试和静态检查**

Run:

```bash
uv run pytest tests/unit/engine/test_collection_manager.py -q
uv run ty check bot/engine/collection_manager.py bot/engine/index_types.py
uv run ruff check bot/engine/collection_manager.py bot/engine/index_types.py tests/unit/engine/test_collection_manager.py
```

Expected: 全部通过且无诊断。

- [ ] **Step 9: 提交领域契约**

Run:

```bash
git add \
  bot/engine/collection_manager.py \
  bot/engine/index_types.py \
  scripts/migrate_meme_collections.py \
  tests/unit/engine/test_collection_manager.py \
  tests/unit/test_migrate_meme_collections.py
git commit -m "feat(engine): 定义合集创建领域契约"
```

Expected: 提交不包含 IndexManager 实现或插件。

---

### Task 3: 通过 Write Worker 原子创建合集

**Owner:** fresh implementation subagent；完成后两阶段审查。

**Files:**
- Create: `tests/unit/engine/test_index_manager_create_collection.py`
- Modify: `bot/engine/index_manager.py:8-112,115-223,1390-1481`
- Modify: `bot/engine/__init__.py:15-45,97-143`

**Interfaces:**
- Consumes: Task 2 的 `validate_collection_name()`、`CreateCollectionResult` 和三类创建错误。
- Produces: `IndexManager.create_collection(raw_name: str) -> CreateCollectionResult`。

- [ ] **Step 1: 创建专用异步测试 fixture**

创建 `tests/unit/engine/test_index_manager_create_collection.py`：

```python
"""IndexManager 创建合集测试。"""

import sqlite3
from pathlib import Path
from typing import AsyncGenerator, cast
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from bot.engine.index_manager import (
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    IndexManager,
    RefreshInProgressError,
)
from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore
from bot.session import ChatScope


@pytest_asyncio.fixture
async def index_manager(
    tmp_path: Path,
) -> AsyncGenerator[IndexManager, None]:
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    metadata_store = MetadataStore(str(tmp_path / "data" / "index.db"))
    metadata_store.load()
    vector_store = cast(VectorStore, MagicMock(spec=VectorStore))
    manager = IndexManager(
        metadata_store=metadata_store,
        vector_store=vector_store,
        memes_dir=str(memes_dir),
    )
    yield manager
    await manager.close()
```

- [ ] **Step 2: 编写成功、已有目录和选择不变的失败测试**

继续加入：

```python
@pytest.mark.asyncio
async def test_create_collection_creates_directory_and_row(
    index_manager: IndexManager,
) -> None:
    result = await index_manager.create_collection("  新三国  ")

    assert result.collection.id == 1
    assert result.collection.name == "新三国"
    assert result.registered_existing_directory is False
    assert (index_manager._memes_dir / "新三国").is_dir()
    assert index_manager._metadata_store.get_collection_by_name("新三国") == (
        result.collection
    )


@pytest.mark.asyncio
async def test_create_collection_registers_existing_directory_without_refresh(
    index_manager: IndexManager,
) -> None:
    target = index_manager._memes_dir / "新三国"
    target.mkdir()
    (target / "existing.webp").write_bytes(b"not-indexed-yet")

    result = await index_manager.create_collection("新三国")

    assert result.registered_existing_directory is True
    assert target.joinpath("existing.webp").exists()
    assert index_manager._metadata_store.collection_entry_count(
        result.collection.id
    ) == 0


@pytest.mark.asyncio
async def test_create_collection_does_not_change_selected_scope(
    index_manager: IndexManager,
) -> None:
    existing = index_manager._metadata_store.create_collection("已有")
    scope = ChatScope(user_id=1, chat_type="private", chat_id=1)
    index_manager._metadata_store.set_selected_collection(scope, existing.id)

    await index_manager.create_collection("新建")

    assert index_manager._metadata_store.get_selected_collection(scope) == existing.id
```

- [ ] **Step 3: 编写冲突和补偿失败测试**

继续加入：

```python
@pytest.mark.asyncio
async def test_create_collection_rejects_registered_duplicate(
    index_manager: IndexManager,
) -> None:
    existing = index_manager._metadata_store.create_collection("新三国")

    with pytest.raises(CollectionAlreadyExistsError) as caught:
        await index_manager.create_collection("新三国")

    assert caught.value.collection == existing


@pytest.mark.asyncio
@pytest.mark.parametrize("path_kind", ["file", "symlink"])
async def test_create_collection_rejects_non_directory_path(
    index_manager: IndexManager, path_kind: str
) -> None:
    target = index_manager._memes_dir / "冲突"
    if path_kind == "file":
        target.write_text("file", encoding="utf-8")
    else:
        source = index_manager._memes_dir / "source"
        source.mkdir()
        target.symlink_to(source, target_is_directory=True)

    with pytest.raises(CollectionPathConflictError):
        await index_manager.create_collection("冲突")


@pytest.mark.asyncio
async def test_database_failure_removes_new_empty_directory(
    index_manager: IndexManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = index_manager._memes_dir / "失败合集"

    def fail_create(name: str) -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(
        index_manager._metadata_store, "create_collection", fail_create
    )

    with pytest.raises(sqlite3.OperationalError):
        await index_manager.create_collection("失败合集")

    assert not target.exists()


@pytest.mark.asyncio
async def test_database_failure_preserves_existing_directory(
    index_manager: IndexManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = index_manager._memes_dir / "已有目录"
    target.mkdir()

    def fail_create(name: str) -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(
        index_manager._metadata_store, "create_collection", fail_create
    )

    with pytest.raises(sqlite3.OperationalError):
        await index_manager.create_collection("已有目录")

    assert target.is_dir()
```

- [ ] **Step 4: 编写刷新拒绝与补偿清理失败测试**

继续加入：

```python
@pytest.mark.asyncio
async def test_create_collection_rejected_while_refresh_active(
    index_manager: IndexManager,
) -> None:
    index_manager._refresh_active = True

    with pytest.raises(RefreshInProgressError):
        await index_manager.create_collection("新三国")


@pytest.mark.asyncio
async def test_cleanup_failure_raises_collection_create_error(
    index_manager: IndexManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = index_manager._memes_dir / "失败合集"

    def fail_create(name: str) -> None:
        target.joinpath("race.webp").write_bytes(b"added externally")
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(
        index_manager._metadata_store, "create_collection", fail_create
    )

    with pytest.raises(CollectionCreateError):
        await index_manager.create_collection("失败合集")

    assert target.is_dir()
    assert index_manager._metadata_store.get_collection_by_name("失败合集") is None
```

该测试同时使用 `caplog` 断言存在 `CRITICAL` 级别且包含“目录回滚失败”的日志。

- [ ] **Step 5: 补齐编号、特殊路径、关闭、取消和串行测试**

继续增加以下测试，覆盖 spec 4.5 与 7.3 的并发契约：

```python
@pytest.mark.asyncio
async def test_create_collection_reuses_smallest_collection_id_hole(
    index_manager: IndexManager,
) -> None:
    first = index_manager._metadata_store.create_collection("一")
    second = index_manager._metadata_store.create_collection("二")
    index_manager._metadata_store.create_collection("三")
    assert first.id == 1
    assert second.id == 2
    index_manager._metadata_store.delete_collection_and_reset_scopes(second.id)

    result = await index_manager.create_collection("复用")

    assert result.collection.id == 2


@pytest.mark.asyncio
async def test_create_collection_rejects_fifo_path(
    index_manager: IndexManager,
) -> None:
    target = index_manager._memes_dir / "管道"
    os.mkfifo(target)
    try:
        with pytest.raises(CollectionPathConflictError):
            await index_manager.create_collection("管道")
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_create_collection_rejected_while_shutting_down(
    index_manager: IndexManager,
) -> None:
    index_manager._shutting_down = True

    with pytest.raises(IndexAddCancelledError, match="Bot 正在关闭"):
        await index_manager.create_collection("新三国")


@pytest.mark.asyncio
async def test_cancelled_queued_create_is_skipped(
    index_manager: IndexManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_worker = index_manager._ensure_write_worker
    monkeypatch.setattr(index_manager, "_ensure_write_worker", lambda: None)
    task = asyncio.create_task(index_manager.create_collection("取消合集"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    monkeypatch.setattr(index_manager, "_ensure_write_worker", start_worker)
    start_worker()
    await asyncio.sleep(0.05)

    assert not (index_manager._memes_dir / "取消合集").exists()
    assert index_manager._metadata_store.get_collection_by_name("取消合集") is None
```

另增加 `test_create_collection_waits_behind_existing_write_request` 与 `test_refresh_waits_for_queued_create_collection`：前者用事件阻塞一个 ADD 请求并断言 CREATE_COLLECTION 未越过它；后者在创建请求已排队时启动 `refresh()`，断言 refresh 在创建完成前不结束。事件释放后，两项操作都必须成功且顺序确定。

测试文件增加：

```python
import asyncio
import logging
import os
```

并从 `bot.engine.index_manager` 导入 `IndexAddCancelledError`。

- [ ] **Step 6: 运行新测试确认失败**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager_create_collection.py -v
```

Expected: collection error，`IndexManager.create_collection` 尚未定义。

- [ ] **Step 7: 导入 Task 2 契约并导出公共类型**

`bot/engine/index_manager.py` 从 `collection_manager` 导入：

```python
from .collection_manager import (
    CollectionNotFoundError,
    validate_collection_name,
)
```

从 `index_types` 增加 import：

```python
CollectionAlreadyExistsError
CollectionCreateError
CollectionPathConflictError
CreateCollectionResult
```

并把这四个名字加入 `__all__`。`bot/engine/__init__.py` 从 `index_manager` 导入并加入 `__all__`，供插件使用。

- [ ] **Step 8: 实现公共 create_collection 入队方法**

在 `IndexManager` 写操作入口区域加入：

```python
async def create_collection(self, raw_name: str) -> CreateCollectionResult:
    """创建或登记空合集目录。

    Args:
        raw_name: 用户输入的合集名称。

    Returns:
        已持久化的合集创建结果。

    Raises:
        InvalidCollectionNameError: 合集名称非法。
        CollectionAlreadyExistsError: 名称已被登记。
        CollectionPathConflictError: 同名路径不是安全普通目录。
        CollectionCreateError: SQLite 失败后目录补偿失败。
        RefreshInProgressError: 索引刷新正在执行。
        IndexAddCancelledError: Bot 正在关闭或写入 worker 被取消。
    """
    if self._shutting_down:
        raise IndexAddCancelledError("Bot 正在关闭")
    if self._refresh_active:
        raise RefreshInProgressError("索引正在刷新，请稍后再试")

    collection_name = validate_collection_name(raw_name)
    self._ensure_write_worker()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[CreateCollectionResult] = loop.create_future()
    await self._write_queue.put(
        _WriteRequest(
            op=WriteOp.CREATE_COLLECTION,
            future=future,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            collection_name=collection_name,
        )
    )
    return await future
```

如果 `ty` 在扩展 future union 后不再需要 ignore，删除该 ignore；不要保留无效抑制。

- [ ] **Step 9: 在 Write Worker 分派创建操作**

在 `_write_worker_loop()` 的 `MOVE` 分支之后、未知操作之前加入：

```python
elif req.op is WriteOp.CREATE_COLLECTION:
    result = self._execute_create_collection(req.collection_name)
```

同步更新 Write Worker docstring/日志中的操作列表。

- [ ] **Step 10: 实现目录与 SQLite 补偿逻辑**

在其他 `_execute_*` 方法附近加入：

```python
def _execute_create_collection(
    self, raw_name: str
) -> CreateCollectionResult:
    """在写锁内创建目录并登记合集。

    Args:
        raw_name: 已解析的合集名称；仍会重新执行领域校验。

    Returns:
        已持久化的合集创建结果。

    Raises:
        CollectionAlreadyExistsError: 名称已被登记。
        CollectionPathConflictError: 同名路径不是安全普通目录。
        CollectionCreateError: SQLite 失败且新目录无法回滚。
    """
    name = validate_collection_name(raw_name)
    existing = self._metadata_store.get_collection_by_name(name)
    if existing is not None:
        raise CollectionAlreadyExistsError(existing)

    target = self._memes_dir / name
    created_directory = False
    registered_existing_directory = False

    if target.is_symlink():
        raise CollectionPathConflictError(name)
    try:
        target.mkdir()
        created_directory = True
    except FileExistsError:
        if target.is_symlink() or not target.is_dir():
            raise CollectionPathConflictError(name) from None
        registered_existing_directory = True
    except OSError:
        logger.exception("创建合集目录失败: name=%r", name)
        raise

    try:
        collection = self._metadata_store.create_collection(name)
    except Exception as exc:
        if created_directory:
            try:
                target.rmdir()
            except OSError as cleanup_exc:
                logger.critical(
                    "创建合集数据库失败且目录回滚失败: name=%r",
                    name,
                    exc_info=True,
                )
                raise CollectionCreateError(
                    "创建合集失败且目录回滚失败"
                ) from cleanup_exc
        raise

    logger.info(
        "合集创建完成: id=%d, name=%r, existing_directory=%s",
        collection.id,
        collection.name,
        registered_existing_directory,
    )
    return CreateCollectionResult(
        collection=collection,
        registered_existing_directory=registered_existing_directory,
    )
```

注意：补偿失败时异常链必须保留日志；不得删除已有目录，也不得递归删除非空目录。

- [ ] **Step 11: 运行新测试并修正类型**

Run:

```bash
uv run pytest tests/unit/engine/test_index_manager_create_collection.py -q
uv run ty check bot/engine/index_manager.py bot/engine/index_types.py
uv run ruff check \
  bot/engine/index_manager.py \
  bot/engine/index_types.py \
  tests/unit/engine/test_index_manager_create_collection.py
```

Expected: 全部通过且无诊断。

- [ ] **Step 12: 运行 IndexManager 回归测试**

Run:

```bash
uv run pytest \
  tests/unit/engine/test_index_manager.py \
  tests/unit/engine/test_index_manager_create_collection.py \
  tests/unit/engine/test_metadata_store.py \
  -q
```

Expected: 全部通过。

- [ ] **Step 13: 提交原子创建实现**

Run:

```bash
git add \
  bot/engine/index_manager.py \
  bot/engine/index_types.py \
  bot/engine/__init__.py \
  tests/unit/engine/test_index_manager_create_collection.py
git commit -m "feat(engine): 原子创建表情包合集"
```

Expected: 提交不包含插件、Compose 或文档。

---

### Task 4: 添加 `/collection create` 插件与帮助文本

**Owner:** fresh implementation subagent；完成后两阶段审查。

**Files:**
- Create: `bot/plugins/collection.py`
- Create: `tests/unit/plugins/test_collection.py`
- Modify: `bot/plugins/_help_text.py:7-33`
- Modify: `tests/unit/plugins/test_help.py:76-93`

**Interfaces:**
- Consumes: `IndexManager.create_collection()` 和 Task 2/3 导出的创建错误、`CreateCollectionResult`。
- Produces: NoneBot 命令 `/collection create <名称>`；私聊帮助新增命令，群聊帮助不展示。

- [ ] **Step 1: 创建插件失败测试骨架**

创建 `tests/unit/plugins/test_collection.py`：

```python
"""/collection 命令插件单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.engine import (
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    CreateCollectionResult,
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.engine.collection_manager import InvalidCollectionNameError
from bot.engine.types import MemeCollection
from bot.session import ChatScope
from tests.conftest import extract_message_text

_mock_cmd = MagicMock()
_mock_cmd.handle.return_value = lambda fn: fn
_on_command = MagicMock(return_value=_mock_cmd)

with patch("nonebot.on_command", _on_command):
    from bot.plugins import collection
    from bot.plugins.collection import handle_collection


def _event(
    user_id: str = "12345", *, message_type: str = "private"
) -> MagicMock:
    event = MagicMock()
    event.get_user_id.return_value = user_id
    event.message_type = message_type
    event.message_id = 88
    if message_type == "group":
        event.group_id = 98765
    return event


def _args(text: str) -> MagicMock:
    message = MagicMock()
    message.extract_plain_text.return_value = text
    return message


def _matcher() -> MagicMock:
    matcher = MagicMock()
    matcher.finish = AsyncMock()
    return matcher


def _bot() -> MagicMock:
    return MagicMock()


def _scope(user_id: str = "12345") -> ChatScope:
    return ChatScope(
        user_id=int(user_id), chat_type="private", chat_id=int(user_id)
    )


def _result(*, existing_directory: bool = False) -> CreateCollectionResult:
    return CreateCollectionResult(
        collection=MemeCollection(3, "新三国"),
        registered_existing_directory=existing_directory,
    )


def test_collection_matcher_registration_has_no_aliases() -> None:
    args, kwargs = _on_command.call_args
    assert args == ("collection",)
    assert kwargs["priority"] == 5
    assert kwargs["block"] is True
    assert "aliases" not in kwargs
```

该注册测试确认命令无短命令；现有 `to_me()` 规则仍由注册调用传入。

- [ ] **Step 2: 编写成功和命令协议测试**

继续加入：

```python
@pytest.mark.asyncio
async def test_create_success_keeps_current_selection() -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(return_value=_result())
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(
            collection.session_manager, "activate_chat", return_value=True
        ),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(
            _bot(), _event(), matcher, _args("create 新三国")
        )

    manager.create_collection.assert_awaited_once_with("新三国")
    text = extract_message_text(matcher.finish.await_args.args[0])
    assert text == "合集创建完成 ✅\n编号：3\n名称：新三国"
    deactivate.assert_called_once_with(_scope())
    assert not hasattr(manager, "switch_collection") or not manager.switch_collection.called


@pytest.mark.asyncio
async def test_existing_directory_success_adds_refresh_hint() -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(
        return_value=_result(existing_directory=True)
    )
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(
            collection.session_manager, "activate_chat", return_value=True
        ),
        patch.object(collection.session_manager, "deactivate_chat"),
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(
            _bot(), _event(), matcher, _args("create 新三国")
        )

    text = extract_message_text(matcher.finish.await_args.args[0])
    assert "已登记现有目录" in text
    assert "/refresh" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "create", "delete 新三国"])
async def test_invalid_subcommand_replies_usage(raw: str) -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(
            collection.session_manager, "activate_chat", return_value=True
        ),
        patch.object(collection.session_manager, "deactivate_chat"),
    ):
        await handle_collection(_bot(), _event(), matcher, _args(raw))

    assert extract_message_text(matcher.finish.await_args.args[0]) == (
        "用法：/collection create <名称>"
    )
```

- [ ] **Step 3: 编写权限、会话和错误映射测试**

继续加入：

```python
@pytest.mark.asyncio
async def test_unauthorized_user_is_silently_ignored() -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=False),
        patch.object(collection, "get_index_manager") as get_manager,
    ):
        await handle_collection(
            _bot(), _event("999"), matcher, _args("create 新三国")
        )

    matcher.finish.assert_awaited_once_with(None)
    get_manager.assert_not_called()


@pytest.mark.asyncio
async def test_group_chat_rejected_before_activation() -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(collection.session_manager, "activate_chat") as activate,
    ):
        await handle_collection(
            _bot(), _event(message_type="group"), matcher, _args("create 新三国")
        )

    activate.assert_not_called()
    reply = matcher.finish.await_args.args[0]
    assert isinstance(reply, Message)
    assert extract_message_text(reply) == "此命令仅限私聊使用"


@pytest.mark.asyncio
async def test_active_session_rejects_new_command() -> None:
    matcher = _matcher()
    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(
            collection.session_manager, "activate_chat", return_value=False
        ),
        patch.object(collection, "get_index_manager") as get_manager,
    ):
        await handle_collection(
            _bot(), _event(), matcher, _args("create 新三国")
        )

    assert "已有命令在处理中" in extract_message_text(
        matcher.finish.await_args.args[0]
    )
    get_manager.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            InvalidCollectionNameError("坏 名称"),
            "合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称",
        ),
        (
            CollectionAlreadyExistsError(MemeCollection(2, "新三国")),
            "表情包合集已存在：新三国（2）",
        ),
        (
            CollectionPathConflictError("新三国"),
            "无法创建合集：同名路径不是可用目录",
        ),
        (
            RefreshInProgressError("raw"),
            "索引正在刷新，请稍后再试",
        ),
        (
            IndexAddCancelledError("Bot 正在关闭"),
            "服务正在关闭，请稍后再试",
        ),
        (
            CollectionCreateError("internal path"),
            "合集创建失败，请检查日志后重试",
        ),
    ],
)
async def test_domain_errors_have_fixed_messages_and_cleanup(
    error: Exception, expected: str
) -> None:
    manager = MagicMock()
    manager.create_collection = AsyncMock(side_effect=error)
    matcher = _matcher()

    with (
        patch.object(collection, "is_authorized", return_value=True),
        patch.object(
            collection.session_manager, "activate_chat", return_value=True
        ),
        patch.object(collection.session_manager, "deactivate_chat") as deactivate,
        patch.object(collection, "get_index_manager", return_value=manager),
    ):
        await handle_collection(
            _bot(), _event(), matcher, _args("create 新三国")
        )

    assert extract_message_text(matcher.finish.await_args.args[0]) == expected
    assert "internal path" not in expected
    deactivate.assert_called_once_with(_scope())
```

另加一例 `get_index_manager()` 抛 `RuntimeError` 时回复 `服务未就绪，请稍后再试` 并清理会话。

- [ ] **Step 4: 运行插件测试确认失败**

Run:

```bash
uv run pytest tests/unit/plugins/test_collection.py -v
```

Expected: collection error，`bot.plugins.collection` 尚不存在。

- [ ] **Step 5: 实现 collection 插件**

创建 `bot/plugins/collection.py`：

```python
"""/collection 命令插件 — 创建表情包合集。"""

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import to_me

from bot import reply as reply_utils
from bot.app_state import get_index_manager
from bot.auth import is_authorized, log_unauthorized
from bot.engine import (
    CollectionAlreadyExistsError,
    CollectionCreateError,
    CollectionPathConflictError,
    CreateCollectionResult,
    IndexAddCancelledError,
    RefreshInProgressError,
)
from bot.engine.collection_manager import InvalidCollectionNameError
from bot.log_context import generate_request_id, set_request_id
from bot.session import ChatScope, session_manager

logger = logging.getLogger(__name__)
_USAGE = "用法：/collection create <名称>"
_INVALID_NAME = (
    "合集名称无效：不能为空、不能包含空白或路径字符，也不能使用保留名称"
)

collection_cmd = on_command(
    "collection", rule=to_me(), priority=5, block=True
)


def _format_success(result: CreateCollectionResult) -> str:
    """格式化合集创建成功回复。

    Args:
        result: 已持久化的合集创建结果。

    Returns:
        用户可见的成功文本。
    """
    collection = result.collection
    lines = [
        "合集创建完成 ✅",
        f"编号：{collection.id}",
        f"名称：{collection.name}",
    ]
    if result.registered_existing_directory:
        lines.append("已登记现有目录；目录中的图片请执行 /refresh 建立索引")
    return "\n".join(lines)


@collection_cmd.handle()
async def handle_collection(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
) -> None:
    """处理合集创建命令。

    Args:
        bot: OneBot V11 Bot 实例。
        event: 私聊或群聊消息事件。
        matcher: NoneBot2 Matcher 实例。
        args: 命令后的完整参数消息。
    """
    user_id = event.get_user_id()
    scope = ChatScope.from_event(event)
    request_id = generate_request_id()
    with set_request_id(request_id):
        if not is_authorized(user_id):
            log_unauthorized(user_id, "collection")
            await matcher.finish(None)
            return

        if event.message_type != "private":
            await reply_utils.finish(
                event, matcher, "此命令仅限私聊使用"
            )
            return

        if not session_manager.activate_chat(scope, "collection", matcher):
            await reply_utils.finish(
                event, matcher, "已有命令在处理中，请先 /cancel"
            )
            return

        try:
            text = args.extract_plain_text().strip()
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or parts[0] != "create":
                await reply_utils.finish(event, matcher, _USAGE)
                return

            try:
                index_manager = get_index_manager()
            except RuntimeError:
                logger.error("IndexManager 尚未初始化")
                await reply_utils.finish(
                    event, matcher, "服务未就绪，请稍后再试"
                )
                return

            try:
                result = await index_manager.create_collection(parts[1])
            except InvalidCollectionNameError:
                await reply_utils.finish(event, matcher, _INVALID_NAME)
                return
            except CollectionAlreadyExistsError as exc:
                existing = exc.collection
                await reply_utils.finish(
                    event,
                    matcher,
                    f"表情包合集已存在：{existing.name}（{existing.id}）",
                )
                return
            except CollectionPathConflictError:
                await reply_utils.finish(
                    event, matcher, "无法创建合集：同名路径不是可用目录"
                )
                return
            except RefreshInProgressError:
                await reply_utils.finish(
                    event, matcher, "索引正在刷新，请稍后再试"
                )
                return
            except IndexAddCancelledError:
                await reply_utils.finish(
                    event, matcher, "服务正在关闭，请稍后再试"
                )
                return
            except CollectionCreateError:
                logger.exception("合集创建和目录补偿失败")
                await reply_utils.finish(
                    event, matcher, "合集创建失败，请检查日志后重试"
                )
                return
            except Exception:
                logger.exception("合集创建失败")
                await reply_utils.finish(
                    event, matcher, "合集创建失败，请检查日志后重试"
                )
                return

            await reply_utils.finish(event, matcher, _format_success(result))
        finally:
            session_manager.deactivate_chat(scope)
```

若 ruff 报告 `bot` 参数未使用，按项目现有插件风格保留签名，并在函数内写 `_ = bot`；不要删除框架参数。

- [ ] **Step 6: 更新帮助文本与精确帮助测试**

`bot/plugins/_help_text.py` 私聊帮助在 `/switch` 前加入：

```text
/collection create <名称>：创建表情包合集
```

群聊 `HELP_TEXT_GROUP` 不加入该命令。

`tests/unit/plugins/test_help.py` 精确列表在 `/switch` 前加入：

```python
"/collection create <名称>：创建表情包合集",
```

并在群聊帮助测试增加：

```python
assert "/collection create" not in text
```

- [ ] **Step 7: 运行插件和帮助测试**

Run:

```bash
uv run pytest \
  tests/unit/plugins/test_collection.py \
  tests/unit/plugins/test_help.py \
  tests/unit/plugins/test_plain_text.py \
  -q
uv run ty check bot/plugins/collection.py
uv run ruff check \
  bot/plugins/collection.py \
  bot/plugins/_help_text.py \
  tests/unit/plugins/test_collection.py \
  tests/unit/plugins/test_help.py
```

Expected: 全部通过且无诊断。

- [ ] **Step 8: 提交插件**

Run:

```bash
git add \
  bot/plugins/collection.py \
  bot/plugins/_help_text.py \
  tests/unit/plugins/test_collection.py \
  tests/unit/plugins/test_help.py
git commit -m "feat(plugins): 添加合集创建命令"
```

Expected: 提交不包含 Compose 或用户文档。

---

### Task 5: 默认拉取发布镜像并保留独立本地构建

**Owner:** fresh implementation subagent；完成后两阶段审查。

**Files:**
- Modify: `docker-compose.yml:23-80`
- Create: `docker-compose.build.yml`
- Rename: `docker-compose.override.yml.example` → `docker-compose.build.override.yml.example`
- Modify: `.gitignore:33`
- Modify: `.env.example:1-88`
- Modify: `.github/workflows/ci.yml:52-95`

**Interfaces:**
- Consumes: 当前 Compose 的 NapCat、卷、网络、内存和线程配置。
- Produces:
  - 默认 `docker compose up -d` 拉取 `northhalf/meme-pilot:latest`；
  - `docker compose -f docker-compose.build.yml up -d --build` 本地构建；
  - DeepSeek 精排配置不再是部署前提。

- [ ] **Step 1: 先写配置检查命令并确认当前默认仍是 build**

Run:

```bash
rg -n '^    build:|northhalf/meme-pilot:latest|DEEPSEEK_|RERANK_' \
  docker-compose.yml .env.example .github/workflows/ci.yml
```

Expected: 当前 `docker-compose.yml` 命中 `build:`，并命中 DeepSeek/Rerank 配置；尚未命中发布镜像。

- [ ] **Step 2: 把默认 Compose 改为发布镜像**

`docker-compose.yml` 的 Bot 服务头部改为：

```yaml
  bot:
    image: northhalf/meme-pilot:latest
    pull_policy: always
    container_name: qq-meme-bot
    restart: always
```

完整删除：

```yaml
    build:
      context: .
      network: host
      dockerfile: bot/Dockerfile
    image: meme-pilot
```

并从 `environment` 删除：

```yaml
      - DEEPSEEK_API_KEY=...
      - DEEPSEEK_BASE_URL=...
      - DEEPSEEK_MODEL=...
      - RERANK_CONCURRENCY=...
```

其他服务、卷、网络、资源限制和环境变量逐行保持现状。

- [ ] **Step 3: 创建完整本地构建 Compose**

以修改后的默认文件为基础创建 `docker-compose.build.yml`，但 Bot 服务头部必须是：

```yaml
  bot:
    build:
      context: .
      network: host
      dockerfile: bot/Dockerfile
    image: meme-pilot:local
    container_name: qq-meme-bot
    restart: always
```

该文件不得包含 `pull_policy`，不得包含任何 `DEEPSEEK_*` 或 `RERANK_CONCURRENCY`。NapCat、env_file、其余环境变量、卷、网络和内存配置必须与默认文件一致。

- [ ] **Step 4: 重命名代理示例并更新 ignore**

Run:

```bash
git mv \
  docker-compose.override.yml.example \
  docker-compose.build.override.yml.example
```

新文件内容保持为：

```yaml
# 本地构建加速代理；仅与 docker-compose.build.yml 组合使用
services:
  bot:
    build:
      args:
        - http_proxy=http://127.0.0.1:10808
        - https_proxy=http://127.0.0.1:10808
```

`.gitignore` 同时保留旧本地文件忽略，并增加新文件：

```gitignore
docker-compose.override.yml
docker-compose.build.override.yml
```

旧 `docker-compose.override.yml` 会被 Docker Compose 自动加载。后续 README 必须加入升级迁移命令，防止旧文件继续把 build 混入默认发布部署：

```bash
if [ -f docker-compose.override.yml ]; then
  mv docker-compose.override.yml docker-compose.build.override.yml
fi
```

迁移后，本地构建必须显式同时传入 `docker-compose.build.yml` 和 `docker-compose.build.override.yml`；默认 `docker compose up -d` 不再受旧 override 影响。

- [ ] **Step 5: 删除环境模板和 CI 中的精排配置**

`.env.example` 删除：

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
RERANK_CONCURRENCY
```

把读锁注释从：

```text
search/ai_match
```

改成：

```text
搜索和信息查询
```

`.github/workflows/ci.yml`：

- 集成测试说明改为 `OpenAI 兼容 Embedding+OCR / PaddleOCR / Google Embedding`；
- 删除 `DEEPSEEK_API_KEY` env；
- 保留 `OPENAI_OCR_MODEL=deepseek-ai/DeepSeek-OCR`，因为这是 OCR 模型名，不是被删除的精排配置。

- [ ] **Step 6: 校验两个 Compose 文件**

Run:

```bash
docker compose \
  --env-file .env.example \
  -f docker-compose.yml \
  config > /tmp/meme-pilot-compose-pull.yml

docker compose \
  --env-file .env.example \
  -f docker-compose.build.yml \
  config > /tmp/meme-pilot-compose-build.yml
```

Expected: 两条命令均以 0 退出。

Run:

```bash
rg -n 'image: northhalf/meme-pilot:latest|pull_policy: always' \
  /tmp/meme-pilot-compose-pull.yml
rg -n 'dockerfile: bot/Dockerfile|image: meme-pilot:local' \
  /tmp/meme-pilot-compose-build.yml
if rg -n 'DEEPSEEK_|RERANK_' \
  docker-compose.yml docker-compose.build.yml .env.example .github/workflows/ci.yml; then
  exit 1
fi
```

Expected: 默认配置命中发布镜像与 always；构建配置命中 Dockerfile 与 local 镜像；精排变量搜索无输出。

继续生成 JSON 并比较除发布/构建字段之外的完整配置：

```bash
docker compose --env-file .env.example -f docker-compose.yml \
  config --format json > /tmp/meme-pilot-compose-pull.json
docker compose --env-file .env.example -f docker-compose.build.yml \
  config --format json > /tmp/meme-pilot-compose-build.json
uv run python - <<'PY'
import json
from pathlib import Path

pull = json.loads(Path("/tmp/meme-pilot-compose-pull.json").read_text())
build = json.loads(Path("/tmp/meme-pilot-compose-build.json").read_text())
pull_bot = pull["services"]["bot"]
build_bot = build["services"]["bot"]
for key in ("image", "pull_policy"):
    pull_bot.pop(key, None)
for key in ("image", "build"):
    build_bot.pop(key, None)
assert pull == build, "两个 Compose 除 image/build/pull_policy 外必须完全一致"
PY
```

Expected: Python 以 0 退出；任何遗漏的环境变量、卷、网络、依赖、内存或线程配置都会触发断言。

- [ ] **Step 7: 提交部署配置**

Run:

```bash
git add \
  docker-compose.yml \
  docker-compose.build.yml \
  docker-compose.build.override.yml.example \
  .gitignore \
  .env.example \
  .github/workflows/ci.yml
git commit -m "feat(config): 默认拉取发布镜像并保留本地构建"
```

Expected: 提交不包含 README、PRD 或 CONTEXT。

---

### Task 6: 更新当前产品文档和领域术语

**Owner:** fresh implementation subagent；完成后两阶段审查。

**Files:**
- Modify: `README.md`
- Modify: `README-containers.md`
- Modify: `docs/PRD.md`
- Modify: `CONTEXT.md`

**Interfaces:**
- Consumes: Tasks 1、4、5 的最终命令和部署行为。
- Produces: 当前用户文档不再描述 `/ai` 或 LLM 精排；完整描述 `/collection create` 和两个 Compose 入口。

- [ ] **Step 1: 记录修改前待删除术语清单**

Run:

```bash
rg -n 'DEEPSEEK_|RERANK_|/ai|AI 匹配|LLM 精排|候选精排|RerankService|AIMatcher' \
  README.md README-containers.md docs/PRD.md CONTEXT.md
```

Expected: 命中当前过期说明；保存输出用于逐项清理。

- [ ] **Step 2: 更新 README 命令和功能说明**

`README.md` 执行以下明确变更：

- 开头权限说明删除 `/ai`，加入仅私聊 `/collection create`；
- 隐私说明删除“LLM 精排调用 DeepSeek”；
- 帮助示例删除 `/ai`，在 `/switch` 前加入：

```text
/collection create <名称>：创建表情包合集
```

- 完整删除“AI 描述匹配 `/ai`”示例；
- 在合集相关命令区域加入：

```markdown
### 📁 创建合集 `/collection create`

```text
授权用户: /collection create 新三国
Bot: 合集创建完成 ✅
     编号：3
     名称：新三国
```

`/collection create <名称>` 仅限私聊，创建后不会自动切换。
名称允许中文，但不能为空、不能包含内部空白或路径分隔符，
也不能使用“全局”“全部合集”等保留名称。
如果同名普通目录已经存在，命令只登记该目录；目录中的图片需执行
`/refresh` 后建立索引。
```

- 前置条件删除 DeepSeek 精排 Key；
- 环境变量示例删除 `DEEPSEEK_*` 和 `RERANK_CONCURRENCY`；
- CI Secret 列表删除 `DEEPSEEK_API_KEY`；
- 架构图、项目结构和依赖列表删除 `ai.py`、`ai_matcher.py`、`rerank_service.py`；
- 保留 `deepseek-ai/DeepSeek-OCR` OCR 模型说明。

- [ ] **Step 3: 更新 README 默认部署和本地构建说明**

默认部署说明明确：

```bash
docker compose up -d
docker compose logs -f bot
```

并说明默认 Compose 使用 `northhalf/meme-pilot:latest` 且每次启动检查发布镜像。

增加本地构建段落：

```bash
docker compose -f docker-compose.build.yml up -d --build
```

代理说明先处理旧版本可能残留且会被默认自动加载的文件：

```bash
if [ -f docker-compose.override.yml ]; then
  mv docker-compose.override.yml docker-compose.build.override.yml
fi
```

随后使用新的显式构建覆盖文件：

```bash
cp docker-compose.build.override.yml.example docker-compose.build.override.yml
docker compose \
  -f docker-compose.build.yml \
  -f docker-compose.build.override.yml \
  up -d --build
```

删除旧的“`docker-compose.override.yml` 自动加载”说明。

- [ ] **Step 4: 更新 Docker Hub 精简 README**

`README-containers.md`：

- 命令表删除 `/ai`；
- 增加 `/collection create <名称>`，群聊列为“私聊”；
- 快速部署不再要求 `DEEPSEEK_API_KEY`；
- 隐私说明删除 LLM 精排；
- 保留镜像标签说明。

- [ ] **Step 5: 用创建合集替换 PRD 的 AI 功能章节**

`docs/PRD.md`：

- 将原 `3.3 功能二：AI 描述匹配` 整节替换为 `3.3 功能：创建合集`；
- 章节必须写明命令、名称规则、已有目录登记、Write Worker/写锁、补偿、成功/失败回复和不自动切换；
- 后续章节编号保持现有连续结构，不留下 3.3 空洞；
- 技术栈删除“大模型 API | DeepSeek”；
- 性能表删除 AI 匹配 `< 5 秒`，增加合集创建“除写锁排队外 < 100 ms”；
- 安全、边界条件、权限组、并发约束和项目结构删除 `/ai`/Rerank/AIMatcher；
- 组 A 加入 `/collection`；
- 添加以下边界条件：非法名称、重名、已有普通目录、同名文件/符号链接、SQLite 失败补偿、补偿失败；
- 部署章节说明默认拉取镜像和独立本地构建；
- 保留 OpenAI 兼容 OCR 中的 DeepSeek-OCR 模型名称。

- [ ] **Step 6: 更新 CONTEXT 术语表**

`CONTEXT.md`：

- 删除“AI 匹配”“DeepSeek 精排”“/ai”术语；
- 把 Chroma 描述改为服务 `/sim`、Embedding 索引和新增图片向量化；
- 加入：

```markdown
| **创建合集** | `/collection create <名称>` 的行为：授权用户仅限私聊直接创建；名称去首尾空白但禁止内部空白、路径字符、隐藏名与保留名；命令通过 IndexManager Write Worker 在写锁内创建或登记 `memes/` 一级普通目录，并写入 `meme_collection`；创建成功后不自动切换，已有目录图片需 `/refresh` 入库。 |
```

- 私聊、授权用户、群聊、刷新和帮助术语同步加入 `/collection`、删除 `/ai`；
- Provider/Protocol 列表删除 AIMatcher/Rerank 专用描述。

- [ ] **Step 7: 扫描当前文档残留**

Run:

```bash
if rg -n \
  'DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|DEEPSEEK_MODEL|RERANK_CONCURRENCY|RerankService|AIMatcher|/ai([[:space:]<]|$)|LLM 精排|候选精排' \
  README.md README-containers.md docs/PRD.md CONTEXT.md; then
  exit 1
fi
```

Expected: 无输出。

以下 DeepSeek-OCR 内容允许保留：

```bash
rg -n 'deepseek-ai/DeepSeek-OCR|OCR_PROVIDER=deepseek' \
  README.md README-containers.md docs/PRD.md CONTEXT.md
```

Expected: 只命中 OCR provider/模型说明。

- [ ] **Step 8: 检查新命令和部署文档齐全**

Run:

```bash
rg -n '/collection create|northhalf/meme-pilot:latest|docker-compose.build.yml' \
  README.md README-containers.md docs/PRD.md CONTEXT.md
```

Expected: README、PRD、CONTEXT 均包含对应的新行为；Docker Hub README 至少包含新命令和 latest 镜像说明。

- [ ] **Step 9: 提交文档**

Run:

```bash
git add README.md README-containers.md docs/PRD.md CONTEXT.md
git commit -m "docs: 更新合集创建与镜像部署说明"
```

Expected: 提交只包含当前产品文档，不修改历史 specs/plans。

---

### Task 7: 全量验证、端到端检查与最终审查

**Owner:** 主代理协调验证 subagent、review subagent；不得合并到 main。

**Files:**
- Modify only if verification finds a confirmed defect; fixes must stay on feature branch and receive a separate commit.

**Interfaces:**
- Consumes: Tasks 1-6 的所有提交。
- Produces: 已验证、工作区干净、未合并的 feature branch。

- [ ] **Step 1: 确认分支和提交历史**

Run:

```bash
test "$(git branch --show-current)" = "feat/remove-ai-add-collection-compose"
git status --short
git log --oneline --decorate -8
```

Expected: 当前 feature branch；工作区干净；能看到每个任务的独立提交。

- [ ] **Step 2: 运行完整单元测试**

Run:

```bash
uv run pytest tests/unit/ -v
```

Expected: 全部通过，无 warning 导致的 task 泄漏。

- [ ] **Step 3: 运行静态检查**

Run:

```bash
uv run ruff check .
uv run ty check
uv run python -m compileall bot tests
```

Expected: 三条命令均以 0 退出。

- [ ] **Step 4: 校验两个 Compose 文件**

Run:

```bash
docker compose \
  --env-file .env.example \
  -f docker-compose.yml \
  config > /tmp/meme-pilot-compose-pull.yml
docker compose \
  --env-file .env.example \
  -f docker-compose.build.yml \
  config > /tmp/meme-pilot-compose-build.yml
```

Expected: 均以 0 退出。

Run:

```bash
rg -n 'image: northhalf/meme-pilot:latest|pull_policy: always' \
  /tmp/meme-pilot-compose-pull.yml
rg -n 'image: meme-pilot:local|dockerfile: bot/Dockerfile' \
  /tmp/meme-pilot-compose-build.yml
```

Expected: 分别命中发布和本地构建配置。

- [ ] **Step 5: 扫描已删除运行时和当前文档残留**

Run:

```bash
if rg -n \
  'AIMatcher|AIMatchCandidate|AIMatchResult|RerankService|RERANK_CONCURRENCY|DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|DEEPSEEK_MODEL|on_command\("ai"|/ai([[:space:]<]|$)' \
  bot README.md README-containers.md docs/PRD.md CONTEXT.md \
  .env.example docker-compose.yml docker-compose.build.yml \
  .github/workflows/ci.yml; then
  exit 1
fi

if rg -n \
  'AIMatcher|AIMatchCandidate|AIMatchResult|RerankService|RERANK_CONCURRENCY|DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|DEEPSEEK_MODEL|on_command\("ai"' \
  tests; then
  exit 1
fi

rg -n '"/ai 加班心累"' tests/unit/plugins/test_plain_text.py
```

Expected: 运行时代码、当前文档和配置无删除项；测试中只保留 `/ai` 未知命令回归输入。不要把历史 `docs/superpowers/specs` 和 `docs/superpowers/plans` 纳入扫描。

- [ ] **Step 6: 运行端到端项目验证 skill**

Invoke: `verify`

要求验证至少覆盖：

1. 用临时 `memes/` 和 SQLite 启动 engine；
2. 调用 `IndexManager.create_collection("新三国")`；
3. 观察目录和 SQLite 行同时存在；
4. 再次创建得到重名错误；
5. 预建普通目录后创建，观察 `registered_existing_directory=True`；
6. 通过插件 handler mock 驱动 `/collection create` 成功回复；
7. 验证 `/ai` 未注册且帮助不展示。

Expected: verify 报告实际观察结果；不能只复述单元测试。

- [ ] **Step 7: 请求最终代码审查**

Invoke: `superpowers:requesting-code-review`

审查范围：从 Task 0 文档提交的父提交到当前 HEAD。重点要求 reviewer 检查：

- 目录/SQLite 补偿是否可能误删用户目录；
- symlink 和路径竞争是否安全；
- Write Worker future/cancellation 类型是否正确；
- `/collection` 是否在所有退出路径清理会话；
- AI 删除是否误伤 `/sim`、Embedding 或 OCR；
- 默认 Compose 是否真的不触发本地 build；
- 当前文档和配置是否一致。

- [ ] **Step 8: 修复确认问题并重新验证**

若 reviewer 有确认问题：

1. 为每个行为缺陷先添加失败测试；
2. 实施最小修复；
3. 重跑受影响测试和 Step 2-5；
4. 提交：

```bash
git add <仅本次修复文件>
git commit -m "fix: 修复合集创建审查问题"
```

若无确认问题，不创建空提交。

- [ ] **Step 9: 最终状态确认，明确不合并**

Run:

```bash
git status --short
git branch --show-current
git log --oneline --decorate main..HEAD
```

Expected:

- 工作区干净；
- 当前分支仍为 `feat/remove-ai-add-collection-compose`；
- `main..HEAD` 列出本任务提交；
- 不执行 `git switch main`、`git merge`、`git rebase main`、`git push` 或创建 PR，除非用户随后明确要求。

## Completion Report

最终向用户报告：

- feature branch 名称；
- 实际提交列表；
- 删除的 AI/Rerank 能力；
- `/collection create` 的最终行为；
- 两个 Compose 文件的使用命令；
- pytest、ruff、ty、compileall、Compose config 和端到端 verify 的真实结果；
- 明确说明“未合并到 main”。
