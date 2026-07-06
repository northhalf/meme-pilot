# API 参考目录 — MemePilot

> 本文档用于快速定位 `docs/api/` 下的接口说明。详细参数、返回值和行为说明见各模块文件。

## 目录结构

```text
api
├── API.md
└── bot
    ├── engine
    │   ├── ai_matcher.md
    │   ├── google_embedding.md
    │   ├── image_optimizer.md
    │   ├── index_manager.md
    │   ├── keyword_searcher.md
    │   ├── metadata_store.md
    │   ├── openai_embedding.md
    │   ├── openai_ocr.md
    │   ├── paddle_ocr.md
    │   ├── protocols.md
    │   ├── provider_factory.md
    │   ├── rapidocr_ocr.md
    │   ├── rerank_service.md
    │   ├── retry_config.md
    │   ├── vector_store.md
    ├── bot.md
    ├── config.md
    ├── logging_config.md
    ├── auth.md
    ├── app_state.md
    ├── session.md
    └── plugins
        ├── _help_text.md
        ├── _search_utils.md
        ├── meme_help.md
        ├── meme_refresh.md
        ├── meme_add.md
        ├── meme_edit.md
        ├── meme_setspeaker.md
        ├── meme_ai.md
        ├── meme_cancel.md
        ├── meme_plain_text.md
        └── meme_search.md
```

## API 文件索引

### `docs/api/bot/engine/protocols.md`

```python
class EmbeddingProvider(Protocol):  # 无 @runtime_checkable
    async def embed(self, text: str) -> list[float]  # 1024 维
```

### `docs/api/bot/engine/ai_matcher.md`

```python
@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

class RerankProvider(Protocol):
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int

@dataclass(frozen=True)
class AIMatchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    source: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

class AIMatcher:
    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None

    async def match_with_vector(
        self,
        description: str,
        query_vector: list[float],
    ) -> AIMatchResult | None
    # 调用方需保证 description 非空、query_vector 非零向量
```

### `docs/api/bot/engine/index_manager.md`

```python
def resolve_unique_filename(target_dir: Path, filename: str) -> Path

class IndexCorruptedError(Exception)
class CompressionError(RuntimeError)
class OcrError(RuntimeError)
class EmbeddingError(RuntimeError)
class RefreshInProgressError(RuntimeError)
class IndexAddCancelledError(RuntimeError)

class DuplicateTextError(RuntimeError)
# edit_text 要修改的文本已被其他条目使用

@dataclass
class EditTextResult:
    entry_id: int
    old_text: str
    new_text: str

@dataclass
class SetSpeakerResult:
    entry_id: int
    old_speaker: str | None
    new_speaker: str | None

class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str
    # 返回去除所有空白后的文本

@dataclass
class SyncResult:
    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)

@dataclass
class AddResult:
    entry_id: int | None
    reason: str
    text: str = ""
    replaced_image_path: str | None = None
    moved_to: str | None = None
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
    read_timeout: float
    add_user_timeout: float

    def __init__(
        self,
        metadata_store: MetadataStore,
        vector_store: VectorStore,
        memes_dir: str,
        no_text_dir: str | None = None,
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        optimizer: ImageOptimizer | None = None,
        keyword_searcher: KeywordSearcher | None = None,
        ai_matcher: AIMatcher | None = None,
    ) -> None

    def load(self) -> None

    async def search(self, keyword: str) -> list[SearchResult]
    # 持读锁调用 KeywordSearcher；空库返回 []；超时抛 asyncio.TimeoutError

    async def ai_match(self, description: str) -> AIMatchResult | None
    # 锁外 embed，持读锁调用 AIMatcher.match_with_vector()；超时抛 asyncio.TimeoutError

    async def add(self, filename: str, speaker: str | None = None, tags: list[str] | None = None) -> AddResult
    # 直接执行压缩-OCR-Embedding 管道后通过 Write Worker 串行写入；pipeline 期间抛 TOCTOU 异常

    async def refresh(self) -> SyncResult
    # Event drain 等待 Write Worker 排空后获取写锁；同步期间 _refresh_active=true 阻止新写入

    async def edit_text(self, entry_id: int, new_text: str) -> EditTextResult
    # 修改指定条目的 OCR 文本；锁外 embed，Write Worker 串行写入；
    # raises RefreshInProgressError, DuplicateTextError, ValueError, EmbeddingError, IndexAddCancelledError

    async def set_speaker(self, entry_id: int, speaker: str | None) -> SetSpeakerResult
    # 设置或清空指定条目的 speaker；仅更新 sqlite 元数据，无需 embed；
    # raises RefreshInProgressError, ValueError, IndexAddCancelledError

    async def close(self) -> None
    # 取消 workers，清空 pending，关闭两个 Store
```

薄编排层：不直接写 SQL/Chroma，全部委托 `MetadataStore` + `VectorStore`。写入顺序统一「先 sqlite 后 chroma」，`upsert` 失败回滚 sqlite。去重键 = 去空白后的 `text`（经 `MetadataStore.get_id_by_text` 判定）。
所有写入操作（`ADD` / `EDIT_TEXT` / `SET_SPEAKER`）通过异步 FIFO Write Worker 串行处理，持有写锁后执行跨库写入。
新增 `RefreshInProgressError`、`IndexAddCancelledError`、`DuplicateTextError` 用于拒绝刷新/关闭期间的写入与冲突检测。

### `docs/api/bot/engine/metadata_store.md`

```python
class DuplicateEntryError(sqlite3.IntegrityError):
    # 写入/更新触发 UNIQUE/PRIMARY KEY 冲突时抛出
    conflicts: list[tuple[str, str]]  # (column, value) 列表，顺序 id→image_path→text

@dataclass
class MemeEntry:
    id: int
    image_path: str
    text: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

class MetadataStore:
    def __init__(self, db_path: str) -> None

    def load(self) -> None
    def close(self) -> None

    def get_all_entries(self) -> dict[int, MemeEntry]  # 实现 MetadataStoreProvider
    def get_entry(self, entry_id: int) -> MemeEntry | None
    def get_by_filename(self, image_path: str) -> MemeEntry | None
    def get_id_by_text(self, text: str) -> int | None
    def find_next_id(self) -> int
    def entry_count(self) -> int
    def get_all_text(self) -> list[tuple[int, str]]

    def add(
        self,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int  # 自动分配最小空洞 id；Raises DuplicateEntryError

    def add_with_id(
        self,
        entry_id: int,
        image_path: str,
        text: str,
        speaker: str | None = None,
        tags: list[str] | None = None,
    ) -> int  # 迁移专用：保留旧 id；Raises DuplicateEntryError

    def update(
        self,
        entry_id: int,
        *,
        image_path: str | None = _UNSET,
        text: str | None = _UNSET,
        speaker: str | None = _UNSET,
        tags: list[str] | None = None,
    ) -> bool  # _UNSET 表示不变，显式 None 表示清空；tags 非 None 时整体替换；Raises DuplicateEntryError

    def remove(self, entry_id: int) -> bool
```

基于 sqlite3。schema：`meme(id INTEGER PRIMARY KEY, image_path, text, speaker)` + `UNIQUE INDEX` on `image_path` 与 `text` + `meme_tag(meme_id, tag, FK ON DELETE CASCADE)`。`PRAGMA foreign_keys = ON`。`text` 与 `image_path` 均有 UNIQUE 约束，写入/更新冲突抛 `DuplicateEntryError`；`IndexManager` 仍用 `get_id_by_text` 在写入前去重。

### `docs/api/bot/engine/vector_store.md`

```python
@dataclass
class VectorHit:
    entry_id: int
    similarity: float  # = 1 - distance

class VectorStore:
    def __init__(self, chroma_path: str, collection_name: str = "memes") -> None

    def load(self) -> None
    def close(self) -> None

    async def upsert(self, entry_id: int, embedding: list[float]) -> None
    async def remove(self, entry_id: int) -> None              # 不存在静默
    async def remove_many(self, entry_ids: list[int]) -> None  # 不存在静默
    async def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
    ) -> list[VectorHit]
    async def rebuild_all(self, items: list[tuple[int, list[float]]]) -> None
    def count(self) -> int
```

基于 chromadb `PersistentClient`，HNSW cosine collection（默认 `memes`）。`id` 内部转 `str`，对外保持 `int`，与 sqlite 一一对应。`load/close/count` 同步，其余 async（内部 `asyncio.to_thread`）。

### `docs/api/bot/engine/keyword_searcher.md`

```python
class MetadataStoreProvider(Protocol):
    def get_all_entries(self) -> dict[int, MemeEntry]

@dataclass
class SearchResult:
    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)

class KeywordSearcher:
    def __init__(
        self,
        metadata_store: MetadataStoreProvider,
        threshold: float = 60.0,
        limit: int = 10,
    ) -> None

    def search(self, keyword: str) -> list[SearchResult]
```

### `docs/api/bot/engine/openai_embedding.md`

```python
class OpenAIEmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None

    async def embed(self, text: str) -> list[float]  # 1024 维
    async def close(self) -> None


def create_openai_embedding_service() -> OpenAIEmbeddingService
```

实现 `protocols.EmbeddingProvider` 协议。`.env.example` 示例默认使用 GLM 模型 `embedding-3`，输出 1024 维向量；`embed()` 装饰有 `@api_retry(...)` 重试。

并发控制：使用 `asyncio.Semaphore` 限制 `embed()` 并发数，`concurrency` 默认读取 `EMBEDDING_CONCURRENCY` 环境变量，回退为 5。

### `docs/api/bot/engine/openai_ocr.md`

```python
class OpenAIOcrService:
    MIME_MAP: dict[str, str]
    OCR_PROMPT: str
    concurrency: int  # OCR 并发上限，默认读取 OCR_CONCURRENCY 环境变量，回退为 5

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None

    async def ocr(self, image_path: str) -> str
    async def close(self) -> None


def create_openai_ocr_service() -> OpenAIOcrService
```

OpenAI 兼容 OCR 服务，示例默认调用硅基流动 `deepseek-ai/DeepSeek-OCR`；`ocr()` 装饰有 `@api_retry(...)` 重试。

### `docs/api/bot/engine/rapidocr_ocr.md`

```python
class RapidOcrService:
    def __init__(
        self,
        text_score: float = 0.9,
        concurrency: int | None = None,
    ) -> None

    async def ocr(self, image_path: str) -> str
    async def close(self) -> None


def create_rapidocr_service() -> RapidOcrService
```

RapidOCR 本地 OCR provider，使用本地 ONNX 模型；实现 `index_manager.OcrProvider` 协议。

### `docs/api/bot/engine/google_embedding.md`

```python
class GoogleEmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None

    async def embed(self, text: str) -> list[float]  # 1024 维
    async def close(self) -> None


def create_google_embedding_service() -> GoogleEmbeddingService
```

Google Embedding provider，通过 Google GenAI SDK 生成 1024 维向量；实现 `protocols.EmbeddingProvider` 协议，`embed()` 装饰有 `@api_retry(...)` 重试。

### `docs/api/bot/engine/provider_factory.md`

```python
OCR_REGISTRY: dict[str, Factory]
EMBEDDING_REGISTRY: dict[str, EmbeddingFactory]

class ProviderNotAvailableError(ValueError): ...

def register_ocr(name: str, factory: Factory) -> None
def register_embedding(name: str, factory: EmbeddingFactory) -> None
def mark_ocr_unavailable(name: str, reason: str) -> None
def mark_embedding_unavailable(name: str, reason: str) -> None
def create_ocr_provider(name: str) -> OcrProvider
def create_embedding_provider(name: str) -> EmbeddingProvider
def reset_provider_registries() -> None
```

OCR / Embedding provider 注册表与工厂函数。依赖缺失的 provider 可在 `bot/engine/__init__.py` 中被标记为不可用，调用 `create_*_provider()` 时抛出 `ProviderNotAvailableError`。

### `docs/api/bot/engine/retry_config.md`

```python
def api_retry(
    *,
    max_attempts: int = 3,
    wait_min: float = 1,
    wait_max: float = 10,
    multiplier: float = 1,
    extra_exceptions: ExceptionTuple = (),
)
```

tenacity 通用网络请求重试装饰器工厂。默认对 `httpx.NetworkError`、`httpx.ConnectError`、`httpx.TimeoutException`、`ConnectionError`、`TimeoutError` 及调用方传入的额外异常进行最多 3 次指数退避重试。

### `docs/api/bot/engine/paddle_ocr.md`

```python
class PaddleOcrClientService:
    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        model: Model | str | None = None,
        request_timeout: float = 300.0,
        poll_timeout: float = 600.0,
        text_rec_score_thresh: float = 0.9,
        concurrency: int | None = None,
    ) -> None

    async def ocr(self, image_path: str) -> str
    async def close(self) -> None
```

- `access_token` 默认从 `PADDLEOCR_ACCESS_TOKEN` 环境变量读取
- `base_url` 默认从 `PADDLEOCR_BASE_URL` 环境变量读取
- `model` 默认 `Model.PP_OCRV6`
- `concurrency` OCR 并发上限，默认读取 `OCR_CONCURRENCY` 环境变量，回退为 5
- `text_rec_score_thresh` 置信度阈值（0~1），低于此值的文本行被过滤；设为 0 关闭过滤
- `ocr()` 返回识别文本（已去除所有空白字符，空字符串表示无结果）；支持新版 API dict 格式（`rec_texts`）与旧版格式自动适配
- `close()` 释放 HTTP 会话
- 异常：`RuntimeError`（API 调用失败）

### `docs/api/bot/engine/rerank_service.md`

```python
class RerankService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ) -> None

    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int  # 1-based 序号，0 表示放弃精排
```

并发控制：使用 asyncio.Semaphore 限制 rerank() 并发数，默认读取 RERANK_CONCURRENCY 环境变量，回退为 5。

### `docs/api/bot/engine/image_optimizer.md`

```python
COMPRESSIBLE: frozenset[str]   # {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PASS_THROUGH: frozenset[str]   # {".bmp"}

@dataclass(frozen=True, slots=True)
class OptimizeResult:
    original_size: int
    optimized_size: int
    saved: int
    skipped: bool = False

class ImageOptimizer:
    def __init__(
        self,
        jpeg_quality: int = 85,
        webp_quality: int = 80,
        concurrency: int | None = None,
    ) -> None

    async def optimize(self, image_path: str | Path) -> OptimizeResult
    # Raises: FileNotFoundError, ValueError, RuntimeError
```

并发控制：使用 asyncio.Semaphore 限制 optimize() 并发数，默认读取 COMPRESS_CONCURRENCY 环境变量，回退为 5。

### `docs/api/bot/bot.md`

NoneBot2 应用入口，详见 `docs/api/bot/bot.md`。

- 启动：`main()` — 初始化 NoneBot2（`driver="~fastapi"`），注册 OneBot V11 适配器，加载插件，启动驱动器
- Startup hook：`_on_startup()` — 通过 `provider_factory.create_*_provider()` 创建 OCR/Embedding 服务，同时创建 `RerankService` 与 `ImageOptimizer`；创建 `MetadataStore(INDEX_DB_PATH)` + `VectorStore(CHROMA_DIR)` 并注入 `IndexManager`，`load()` 后注册到 `app_state`、后台执行索引同步；根据 `OCR_PROVIDER` 选择 OCR 引擎（`paddle`/`deepseek`/`rapidocr`），根据 `EMBEDDING_PROVIDER` 选择 Embedding 引擎（`openai`/`google`）
- Shutdown hook：`_on_shutdown()` — 关闭 OCR 服务 HTTP 会话，并 `close()` 两个 Store（sqlite 连接 + chroma PersistentClient）
- `_background_sync()` — 后台同步任务，调用 `IndexManager.refresh()` 以独占写锁执行同步；同步失败时记录日志，Bot 继续运行
- 环境变量：`BOT_HOST`（默认 `0.0.0.0`）、`BOT_PORT`（默认 `8080`，无效值回退 8080）、`READ_LOCK_TIMEOUT`（默认 `00:00:30`）、`ADD_COMMAND_TIMEOUT`（默认 `00:01:00`）、`EMBEDDING_CONCURRENCY`（默认 5）、`OCR_CONCURRENCY`（默认 5）、`RERANK_CONCURRENCY`（默认 5）、`COMPRESS_CONCURRENCY`（默认 5）

### `docs/api/bot/logging_config.md`

```python
def setup_logging(log_dir: str = "log") -> None
```

### `docs/api/bot/app_state.md`

```python
def init_app(
    index_manager: IndexManager,
    metadata_store: MetadataStore,
    vector_store: VectorStore,
    ocr_service: OcrProvider,
    embedding_service: EmbeddingProvider,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None

def get_index_manager() -> IndexManager
def get_metadata_store() -> MetadataStore
def get_vector_store() -> VectorStore
def get_ocr_service() -> OcrProvider
def get_embedding_service() -> EmbeddingProvider
def get_image_optimizer() -> ImageOptimizer | None
def get_ai_matcher() -> AIMatcher
def get_keyword_searcher() -> KeywordSearcher
```

### `bot/auth.py`

共享授权校验模块，从 `AUTHORIZED_USER_IDS` 环境变量读取白名单。

- `AUTHORIZED_USER_IDS: frozenset[str]` — 授权用户白名单
- `is_authorized(user_id: str) -> bool` — 校验用户是否在白名单中
- `log_unauthorized(user_id: str, command: str) -> None` — 记录非授权访问日志

### `bot/plugins/meme_refresh.py`

NoneBot2 命令插件，注册 `/refresh` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`
- 同步：`IndexManager.refresh() -> SyncResult`
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"

### `bot/plugins/_search_utils.py`

搜索核心逻辑模块，以下划线开头避免 NoneBot2 自动加载为插件。

```python
def format_metadata_line(
    entry_id: int, speaker: str | None, tags: list[str]
) -> str
# 格式化表情包元数据行：id, 无/说话人, tag1, tag2, ...；speaker 缺失显示"无"，空 tags 省略

async def execute_search(
    bot: Bot, event: MessageEvent, cmd_matcher: Matcher, keyword: str
) -> None
# 核心搜索逻辑：执行搜索 → 结果分支
# 单结果/选择命中时先发送图片，再发送 format_metadata_line() 元数据文本消息
# 多结果时注册 session 并启动超时任务
# 读锁等待超时时回复"索引更新较慢，请稍后再试"

def handle_selection(
    matcher: Matcher, candidates: list[SearchResult], text: str
) -> SearchResult | str
# 处理用户选择编号，返回 SearchResult 或错误消息字符串

async def handle_got_selection(
    bot: Bot, event: MessageEvent, matcher: Matcher, selection_msg: Message, error_label: str = "搜索",
) -> None
# got 选择编号共享逻辑（旁路拦截 → 会话检查 → handle_selection → 发送图片 → 发送元数据行）

async def got_intercept_bypass(
    user_id: str, matcher: Matcher, text: str, HELP_TEXT: str,
) -> bool
# Got handler 入口统一拦截 /help 和 /cancel
# /cancel 委托给 session_manager.execute_cancel()
# /help 通过 reject(HELP_TEXT) 发送帮助文本并继续等待
```

- 依赖：`app_state.get_index_manager()`、`bot.session.session_manager`、`bot.plugins._search_utils.got_intercept_bypass`、`bot.config.MEMES_DIR`、`bot.plugins._help_text.HELP_TEXT`

### `bot/plugins/_help_text.py`

帮助文本常量模块，下划线开头避免 NoneBot2 自动加载为插件。

```python
HELP_TEXT: str  # 命令帮助摘要文本
```

- 供 `meme_help.py` 和 `meme_plain_text.py` 共享

### `bot/plugins/meme_help.py`

NoneBot2 命令插件，注册 `/help` 命令。

- 注册：`on_command("help", rule=to_me(), priority=5, block=True)`
- 依赖：`auth.is_authorized()`
- 群聊：支持群聊 @bot 触发

### `bot/plugins/meme_cancel.py`

NoneBot2 命令插件，注册 `/cancel` 命令。

- 注册：`on_command("cancel", rule=to_me(), priority=5, block=True)`
- 依赖：`auth.is_authorized()`、`bot.session.session_manager`
- 行为：授权用户私聊或群聊 @bot 调用 → `execute_cancel()` 取消活跃会话；无活跃会话时回复"当前没有活跃的会话"
- 旁路：`/cancel` 在 `got` 等待阶段可通过 `got_intercept_bypass` 旁路触发，不受会话互斥影响

### `bot/plugins/meme_plain_text.py`

兜底消息插件，处理普通文本和未知斜杠命令。

- 注册：`on_message(rule=to_me(), priority=99, block=False)`
- 普通文本：等同执行 `/search`，调用 `_search_utils.execute_search`（支持私聊和群聊 @bot）
- 未知斜杠命令：回复"未知命令"并附帮助摘要（支持私聊和群聊 @bot）
- got：`catch_all.got("selection")` 薄包装，委托 `_search_utils.handle_got_selection()` 处理搜索多结果选择
- 依赖：`auth.is_authorized()`、`_search_utils.execute_search`、`_search_utils.handle_got_selection`、`bot.plugins._help_text.HELP_TEXT`、`bot.session.session_manager`

### `bot/session.py`

共享会话管理模块，管理聊天会话（ChatSession）和选择会话（SelectionSession）。

- `ChatSession(session_id, active=False, command_type=None, matcher=None, current_task=None)` — 聊天会话数据类
- `SelectionSession(selection_id, timeout_task=None)` — 选择会话数据类
- `session_manager: SessionManager` — 模块级 SessionManager 单例
- `SessionManager` 类方法：
  - `get_or_create_chat(user_id) -> ChatSession` — 获取或创建聊天会话
  - `activate_chat(user_id, command_type, matcher) -> bool` — 激活会话（返回 False 表示已有活跃会话）
  - `deactivate_chat(user_id) -> None` — 重置会话为空闲，同时删除选择会话
  - `create_selection(user_id, selection_id, timeout_task) -> None` — 创建选择会话
  - `remove_selection(user_id) -> SelectionSession | None` — 移除选择会话
  - `get_selection(user_id) -> SelectionSession | None` — 查询选择会话
  - `set_current_task(user_id, task) -> None` — 显式设置用户的 current_task
  - `reset_current_task(user_id) -> None` — 快速将 current_task 设为 None
  - `handler_context(user_id, matcher)` — 上下文管理器，got handler 入口使用（with 语句）
  - `execute_cancel(user_id, message="当前会话已取消") -> bool` — 取消逻辑（自取消保护、跨 task 取消、选择会话清理）
- `timeout_session(bot, event, user_id, selection_id, message, *, on_cleanup, timeout)` — 会话超时检查任务（模块级函数）

### `bot/plugins/meme_add.py`

NoneBot2 命令插件，注册 `/add` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`、`bot.session.session_manager`、`bot.session.timeout_session`、`bot.plugins._search_utils.got_intercept_bypass`、`bot.config.read_session_timeout()`
- 管道：`IndexManager.add(filename, speaker=speaker, tags=tags) -> AddResult`
- 图片下载：`httpx.AsyncClient`，30s 超时
- 参数解析：`/add` 后的 token 第一个作为 `speaker`，剩余作为 `tags`，存入 `matcher.state`
- 文件名：`_auto_filename()` 自动生成 `meme_<YYYYMMDDHHMMSS>_<hash8>`，不再使用用户输入作为文件名基名
- 文件冲突：`resolve_unique_filename()`
- 超时：`handle_add` 中创建 selection_id 并注册 `timeout_session` 超时任务；`got` prompt 由 `read_session_timeout()` 动态生成
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"
- `/cancel` 和 `/help` 在 got 等待阶段可旁路触发（`got_intercept_bypass`）
- 错误处理：`try/except/else` 模式，异常统一集中处理

### `bot/plugins/meme_edit.py`

NoneBot2 命令插件，注册 `/edittext` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`、`bot.session.session_manager`、`bot.session.timeout_session`、`bot.plugins._search_utils.got_intercept_bypass`
- 管道：`IndexManager.edit_text() -> EditTextResult`
- 流程：`/edittext <id> <新文本>` → 发送确认消息（含图片） → 用户回复「确认」后执行修改 → 更新 sqlite 元数据、chroma 向量、关键词搜索索引
- 错误处理：索引刷新中抛 `RefreshInProgressError`，文本冲突抛 `DuplicateTextError`，id 不存在抛 `ValueError`
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"

### `bot/plugins/meme_setspeaker.py`

NoneBot2 命令插件，注册 `/setspeaker` 命令。

- 依赖：`app_state.get_index_manager()`、`app_state.get_metadata_store()`、`auth.is_authorized()`、`bot.session.session_manager`、`bot.session.timeout_session`、`bot.plugins._search_utils.got_intercept_bypass`、`bot.config.read_session_timeout()`
- 管道：`IndexManager.set_speaker()`
- 流程：`/setspeaker <id> [说话人]` → 发送图片与确认消息 → 用户回复「确认/yes」后执行修改 → 更新 sqlite 元数据；`[说话人]` 缺省时清空字段
- 错误处理：索引刷新中抛 `RefreshInProgressError`，id 不存在抛 `ValueError`
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"

### `bot/plugins/meme_ai.py`

NoneBot2 命令插件，注册 `/ai` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`
- 匹配：`index_manager.ai_match()` 内部持读锁；`asyncio.gather()` 并发执行 send 与 match
- 命中后先 `matcher.send(...)` 发送图片，再 `matcher.finish(format_metadata_line(...))` 发送文本消息 `id, 无/说话人, tag1, tag2, ...`
- 错误处理：读锁等待超时回复"索引更新较慢，请稍后再试"
- 图片：`MessageSegment.image("file://" + str(image_path.resolve()))`
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"

### `bot/plugins/meme_search.py`

NoneBot2 命令插件，注册 `/search` 命令（薄包装，核心逻辑委托 `_search_utils`）。

- 依赖：`auth.is_authorized()`、`_search_utils.execute_search`、`_search_utils.handle_got_selection`、`bot.session.session_manager`
- 流程：`handle_search` — 授权校验 → 会话检查 → 提取关键词 → `execute_search`
- 选择：`got_selection` — 薄包装，委托 `_search_utils.handle_got_selection()`；命中后先发送图片，再发送 `format_metadata_line()` 元数据文本消息

### `bot/config.py`

全局路径常量与配置读取模块，详见 `docs/api/bot/config.md`。

- `PROJECT_ROOT: Path` — 项目根目录，绝对路径
- `MEMES_DIR: Path` — 表情包图片目录，绝对路径 `<项目根>/memes`
- `DATA_DIR: Path` — 索引数据目录，绝对路径 `<项目根>/data`
- `INDEX_DB_PATH: Path` — sqlite 元数据数据库文件，绝对路径 `<项目根>/data/index.db`
- `CHROMA_DIR: Path` — chroma 向量库数据目录，绝对路径 `<项目根>/data/chroma`
- `read_session_timeout() -> int` — 从 `SESSION_EXPIRE_TIMEOUT` 环境变量读取会话超时秒数，支持纯数字和 `HH:MM:SS` 格式（pydantic 解析），默认 60
- `read_ocr_provider() -> str` — 从 `OCR_PROVIDER` 环境变量读取 OCR 引擎类型，默认 `"rapidocr"`，有效值：`"deepseek"`、`"paddle"`、`"rapidocr"`
- `read_embedding_provider() -> str` — 从 `EMBEDDING_PROVIDER` 环境变量读取 Embedding 引擎类型，默认 `"openai"`，有效值：`"openai"`、`"google"`
- `read_ocr_text_score() -> float` — 从 `OCR_TEXT_SCORE` 环境变量读取 OCR 文本置信度阈值，默认 `0.9`，无效值回退为 `0.9`
