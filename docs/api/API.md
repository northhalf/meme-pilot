# API 参考目录 — MemePilot

> 本文档用于快速定位 `docs/api/` 下的接口说明。详细参数、返回值和行为说明见各模块文件。

## 目录结构

```text
api
├── API.md
└── bot
    ├── engine
    │   ├── index_manager.md
    │   ├── keyword_searcher.md
    │   └── ocr_service.md
    └── logging_config.md
```

## API 文件索引

### `docs/api/bot/engine/index_manager.md`

```python
def normalize_text(text: str) -> str

def compute_text_hash(text: str) -> str

def dedup_key(text: str) -> str

def is_blank_text(text: str) -> bool

class IndexCorruptedError(Exception)

class OcrProvider(Protocol):
    async def ocr(self, image_path: str) -> str

class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]

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

### `docs/api/bot/logging_config.md`

```python
def setup_logging(log_dir: str = "log") -> None
```
