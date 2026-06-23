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
    │   └── protocols.md
    ├── logging_config.md
    ├── auth.md
    ├── app_state.md
    └── plugins
        ├── meme_help.md
        └── meme_refresh.md
```

## API 文件索引

### `docs/api/bot/engine/protocols.md`

```python
class EmbeddingProvider(Protocol):
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

    def acquire_lock(self) -> bool

    def release_lock(self) -> None

    @property
    def is_locked(self) -> bool

    async def sync_with_filesystem(self) -> SyncResult
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
) -> None

def get_index_manager() -> IndexManager
def get_ocr_service() -> DeepSeekOcrService
def get_embedding_service() -> EmbeddingService
```

### `bot/auth.py`

共享授权校验模块，从 `AUTHORIZED_USER_IDS` 环境变量读取白名单。

- `AUTHORIZED_USER_IDS: frozenset[str]` — 授权用户白名单
- `is_authorized(user_id: str) -> bool` — 校验用户是否在白名单中
- `log_unauthorized(user_id: str, command: str) -> None` — 记录非授权访问日志

### `bot/plugins/meme_refresh.py`

NoneBot2 命令插件，注册 `/refresh` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`
- 锁：`IndexManager.acquire_lock()` / `release_lock()`
- 同步：`IndexManager.sync_with_filesystem() -> SyncResult`

### `bot/plugins/meme_help.py`

NoneBot2 命令插件，注册 `/help` 命令及兜底消息处理。

- 注册：`on_command("help", rule=to_me(), priority=5, block=True)`
- 兜底：`on_message(rule=to_me(), priority=99, block=False)` 处理纯文本和未知斜杠命令
- 依赖：`auth.is_authorized()`
- 无外部依赖，不获取 IndexManager 实例
