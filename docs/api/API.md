# API 参考目录 — MemePilot

> 本文档用于快速定位 `docs/api/` 下的接口说明。详细参数、返回值和行为说明见各模块文件。

## 目录结构

```text
api
├── API.md
└── bot
    ├── engine
    │   ├── ai_matcher.md
    │   ├── embedding_service.md
    │   ├── rerank_service.md
    │   ├── index_manager.md
    │   ├── keyword_searcher.md
    │   ├── ocr_service.md
    │   ├── image_optimizer.md
    │   └── protocols.md
    ├── bot.md
    ├── config.md
    ├── logging_config.md
    ├── auth.md
    ├── app_state.md
    ├── session.md
    └── plugins
        ├── meme_help.md
        ├── meme_refresh.md
        ├── meme_add.md
        ├── meme_ai.md
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
class AIIndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]
    def get_embeddings(self) -> dict[str, dict[str, object]]

@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: str
    filename: str
    text: str
    similarity: float

class RerankProvider(Protocol):
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int

@dataclass(frozen=True)
class AIMatchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float
    source: str

class AIMatcher:
    def __init__(
        self,
        index_provider: AIIndexProvider,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None

    async def match(self, description: str) -> AIMatchResult | None
```

### `docs/api/bot/engine/index_manager.md`

```python
def normalize_text(text: str) -> str

def compute_text_hash(text: str) -> str

def dedup_key(text: str) -> str

def is_blank_text(text: str) -> bool

class IndexCorruptedError(Exception)

class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str

@dataclass
class SyncResult:
    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)

@dataclass
class AddResult:
    entry_id: str | None
    reason: str
    replaced_filename: str | None = None
    moved_to: str | None = None

class IndexManager:
    SUPPORTED_EXTENSIONS: frozenset[str]
    DEFAULT_SYNC_CONCURRENCY: int

    def __init__(
        self,
        data_dir: str = "data",
        memes_dir: str = "memes",
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        sync_concurrency: int | None = None,
        no_text_dir: str | None = None,
        optimizer: ImageOptimizer | None = None,
    ) -> None

    def load(self) -> None

    @staticmethod
    def validate_index(data: object) -> None

    def get_entries(self) -> dict[str, dict[str, str]]

    def get_embeddings(self) -> dict[str, dict[str, object]]

    def get_entry(self, entry_id: str) -> dict[str, str] | None

    def get_by_filename(self, filename: str) -> dict[str, str] | None

    @property
    def entry_count(self) -> int

    def save_index(self) -> None

    def save_embeddings(self) -> None

    def add_entry(
        self,
        filename: str,
        text: str,
        embedding: list[float],
    ) -> AddResult

    def remove_entry(self, entry_id: str) -> bool

    async def acquire_lock(self) -> bool

    def release_lock(self) -> None

    @property
    def is_locked(self) -> bool

    async def sync_with_filesystem(self) -> SyncResult

    async def add_single_file(self, filename: str) -> AddResult
    # Raises: CompressionError, OcrError, EmbeddingError

    async def _process_image_pipeline(self, filename: str) -> tuple[str, list[float]]
    # Raises: CompressionError, OcrError, EmbeddingError
```

**新增异常：**

```python
class CompressionError(RuntimeError)   # 图片压缩失败
class OcrError(RuntimeError)           # OCR 识别失败
class EmbeddingError(RuntimeError)     # Embedding 生成失败
```

**新增模块级函数：**

```python
def resolve_unique_filename(target_dir: Path, filename: str) -> Path
    # 原 _resolve_unique_filename，已公共化
```

### `docs/api/bot/engine/keyword_searcher.md`

```python
class IndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]

@dataclass
class SearchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float

class KeywordSearcher:
    def __init__(
        self,
        index_provider: IndexProvider,
        threshold: float = 60.0,
        limit: int = 10,
    ) -> None

    def search(self, keyword: str) -> list[SearchResult]
```

### `docs/api/bot/engine/embedding_service.md`

```python
class EmbeddingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None

    async def embed(self, text: str) -> list[float]  # 1024 维
```

### `docs/api/bot/engine/ocr_service.md`

```python
class DeepSeekOcrService:
    MIME_MAP: dict[str, str]
    OCR_PROMPT: str

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None

    async def ocr(self, image_path: str) -> str
```

### `docs/api/bot/engine/rerank_service.md`

```python
class RerankService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None

    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int  # 1-based 序号，0 表示放弃精排
```

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
        jpeg_quality: int = 95,
        webp_quality: int = 80,
    ) -> None

    async def optimize(self, image_path: str | Path) -> OptimizeResult
    # Raises: FileNotFoundError, ValueError, RuntimeError
```

### `docs/api/bot/bot.md`

NoneBot2 应用入口，详见 `docs/api/bot/bot.md`。

- 启动：`main()` — 初始化 NoneBot2（`driver="~fastapi"`），注册 OneBot V11 适配器，加载插件，启动驱动器
- Startup hook：`_on_startup()` — 创建 engine 服务、执行首次索引同步、注册到 `app_state`
- 同步失败时抛 `RuntimeError` 阻止 Bot 启动
- 环境变量：`BOT_HOST`（默认 `0.0.0.0`）、`BOT_PORT`（默认 `8080`，无效值回退 8080）、`SYNC_CONCURRENCY`（默认 5）

### `docs/api/bot/logging_config.md`

```python
def setup_logging(log_dir: str = "log") -> None
```

### `docs/api/bot/app_state.md`

```python
def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService,
    embedding_service: EmbeddingService,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None

def get_index_manager() -> IndexManager
def get_ocr_service() -> DeepSeekOcrService
def get_embedding_service() -> EmbeddingService
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
- 锁：`await IndexManager.acquire_lock()` / `IndexManager.release_lock()`
- 同步：`IndexManager.sync_with_filesystem() -> SyncResult`

### `bot/plugins/meme_help.py`

NoneBot2 命令插件，注册 `/help` 命令及兜底消息处理。

- 注册：`on_command("help", rule=to_me(), priority=5, block=True)`
- 兜底：`on_message(rule=to_me(), priority=99, block=False)` 处理纯文本和未知斜杠命令
- 依赖：`auth.is_authorized()`
- 无外部依赖，不获取 IndexManager 实例

### `bot/session.py`

共享会话管理模块，管理 /add、/search 等命令的待处理会话。

- `PendingSession` — 待处理会话数据类（matcher, cancelled, type）
- `pending_sessions: dict[str, PendingSession]` — 模块级会话字典
- `check_and_cancel(user_id, new_type) -> str | None` — 检查旧会话并标记取消
- `register(user_id, matcher, type) -> None` — 注册新会话
- `cancel(user_id) -> None` — 移除会话
- `is_cancelled(user_id) -> bool` — 检查会话是否已取消

### `bot/plugins/meme_add.py`

NoneBot2 命令插件，注册 `/add` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`、`bot.session`
- 锁：`await IndexManager.acquire_lock()` / `IndexManager.release_lock()`
- 管道：`IndexManager.add_single_file() -> AddResult`
- 图片下载：`httpx.AsyncClient`，30s 超时
- 文件名：`_sanitize_filename()` 安全化 / `_auto_filename()` 自动生成
- 文件冲突：`resolve_unique_filename()`

### `bot/plugins/meme_ai.py`

NoneBot2 命令插件，注册 `/ai` 命令。

- 依赖：`app_state.get_ai_matcher()`、`app_state.get_index_manager()`、`auth.is_authorized()`
- 锁：只读检查 `IndexManager.is_locked`
- 匹配：`_do_match()` 封装异常处理，`asyncio.gather()` 并发执行 send 与 match
- 图片：`MessageSegment.image(f"file:///{path.resolve()}")`

### `bot/plugins/meme_search.py`

NoneBot2 命令插件，注册 `/search` 命令。

- 依赖：`app_state.get_keyword_searcher()`、`app_state.get_index_manager()`、`auth.is_authorized()`、`bot.session`
- 锁：只读检查 `IndexManager.is_locked`
- 搜索：`KeywordSearcher.search(keyword) -> list[SearchResult]`，异常保护
- 多结果：`got("selection")` 等待用户选择，候选存入 `matcher.state["candidates"]`
- 图片：`MessageSegment.image(f"file:///{path.resolve()}")`

### `bot/config.py`

全局路径常量模块，详见 `docs/api/bot/config.md`。

- `PROJECT_ROOT: Path` — 项目根目录，绝对路径
- `MEMES_DIR: Path` — 表情包图片目录，绝对路径 `<项目根>/memes`
