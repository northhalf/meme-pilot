# API 参考目录 — MemePilot

> 本文档用于快速定位 `docs/api/` 下的接口说明。详细参数、返回值和行为说明见各模块文件。

## 目录结构

```text
api
├── API.md
└── bot
    ├── engine
    │   ├── ai_matcher.md
    │   ├── deepseek_ocr.md
    │   ├── embedding_service.md
    │   ├── image_optimizer.md
    │   ├── index_manager.md
    │   ├── keyword_searcher.md
    │   ├── paddle_ocr.md
    │   ├── protocols.md
    │   ├── rerank_service.md
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
    text: str = ""
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
    # 输出 version 2 格式，embedding 自动编码为 base64

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

**embeddings.json 格式变更（v2）：**

version=1（旧格式，自动清空重建）：
```json
{
  "1": {"text_hash": "sha256:...", "embedding": [0.1, 0.2, ...]}
}
```

version=2（新格式）：
```json
{
  "version": 2,
  "entries": {
    "1": {"text_hash": "sha256:...", "embedding": "AAAAAEA/4D8..."}
  }
}
```

**新增异常：**

```python
class CompressionError(RuntimeError)   # 图片压缩失败
class OcrError(RuntimeError)           # OCR 识别失败
class EmbeddingError(RuntimeError)     # Embedding 生成失败
```

**新增模块级函数：**

```python
def encode_embedding(embedding: list[float]) -> str
    # struct.pack + base64，big-endian，float32 roundtrip 零误差

def decode_embedding(data: str) -> list[float]
    # base64 解码为 float32 向量

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

### `docs/api/bot/engine/deepseek_ocr.md`

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
    ) -> None

    async def ocr(self, image_path: str) -> str
    async def close(self) -> None
```

- `access_token` 默认从 `PADDLEOCR_ACCESS_TOKEN` 环境变量读取
- `base_url` 默认从 `PADDLEOCR_BASE_URL` 环境变量读取
- `model` 默认 `Model.PP_OCRV6`
- `text_rec_score_thresh` 置信度阈值（0~1），低于此值的文本行被过滤；设为 0 关闭过滤
- `ocr()` 返回识别文本（空字符串表示无结果）；支持新版 API dict 格式（`rec_texts`）与旧版格式自动适配
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
- Startup hook：`_on_startup()` — 创建 engine 服务、注册到 `app_state`、后台执行索引同步；根据 `OCR_PROVIDER` 环境变量选择 OCR 引擎（`paddle`/`deepseek`）
- Shutdown hook：`_on_shutdown()` — 释放 OCR 服务的 HTTP 会话（如适用）
- `_background_sync()` — 后台同步任务，`acquire_lock()` 获取锁，同步完成/失败后释放
- 同步期间 `is_locked = True`，插件层自动回复"索引正在更新"；同步失败时记录日志，Bot 继续运行
- 环境变量：`BOT_HOST`（默认 `0.0.0.0`）、`BOT_PORT`（默认 `8080`，无效值回退 8080）、`SYNC_CONCURRENCY`（默认 5）

### `docs/api/bot/logging_config.md`

```python
def setup_logging(log_dir: str = "log") -> None
```

### `docs/api/bot/app_state.md`

```python
def init_app(
    index_manager: IndexManager,
    ocr_service: DeepSeekOcrService | PaddleOcrClientService,
    embedding_service: EmbeddingService,
    image_optimizer: ImageOptimizer | None = None,
    ai_matcher: AIMatcher | None = None,
    keyword_searcher: KeywordSearcher | None = None,
) -> None

def get_index_manager() -> IndexManager
def get_ocr_service() -> DeepSeekOcrService | PaddleOcrClientService
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
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"

### `bot/plugins/_search_utils.py`

搜索核心逻辑模块，以下划线开头避免 NoneBot2 自动加载为插件。

```python
async def execute_search(
    bot: Bot, event: MessageEvent, cmd_matcher: Matcher, keyword: str
) -> None
# 核心搜索逻辑：锁检查 → 索引空检查 → 执行搜索 → 结果分支
# 多结果时注册 session 并启动超时任务

def handle_selection(
    matcher: Matcher, candidates: list[SearchResult], text: str
) -> SearchResult | str
# 处理用户选择编号，返回 SearchResult 或错误消息字符串

async def handle_got_selection(
    bot: Bot, event: MessageEvent, matcher: Matcher, selection_msg: Message, error_label: str = "搜索",
) -> None
# got 选择编号共享逻辑（旁路拦截 → 会话检查 → handle_selection → 发送图片）
```

- 依赖：`app_state.get_index_manager()`、`app_state.get_keyword_searcher()`、`bot.session`（`activate_chat`、`create_selection`、`deactivate_chat`、`get_selection`、`got_intercept_bypass`、`remove_selection`、`timeout_session`）、`bot.config.MEMES_DIR`、`bot.plugins._help_text.HELP_TEXT`

### `bot/plugins/_help_text.py`

帮助文本常量模块，下划线开头避免 NoneBot2 自动加载为插件。

```python
_HELP_TEXT: str  # 命令帮助摘要文本
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
- 依赖：`auth.is_authorized()`、`bot.session.execute_cancel()`
- 行为：授权用户私聊或群聊 @bot 调用 → `execute_cancel()` 取消活跃会话；无活跃会话时回复"当前没有活跃的会话"
- 旁路：`/cancel` 在 `got` 等待阶段可通过 `got_intercept_bypass` 旁路触发，不受会话互斥影响

### `bot/plugins/meme_plain_text.py`

兜底消息插件，处理普通文本和未知斜杠命令。

- 注册：`on_message(rule=to_me(), priority=99, block=False)`
- 普通文本：等同执行 `/search`，调用 `_search_utils.execute_search`（支持私聊和群聊 @bot）
- 未知斜杠命令：回复"未知命令"并附帮助摘要（支持私聊和群聊 @bot）
- got：`catch_all.got("selection")` 薄包装，委托 `_search_utils.handle_got_selection()` 处理搜索多结果选择
- 依赖：`auth.is_authorized()`、`_search_utils.execute_search`、`_search_utils.handle_got_selection`、`bot.plugins._help_text.HELP_TEXT`、`bot.session.activate_chat`

### `bot/session.py`

共享会话管理模块，管理聊天会话（ChatSession）和选择会话（SelectionSession）。

- `ChatSession(session_id, active=False, command_type=None, matcher=None, current_task=None)` — 聊天会话数据类
- `SelectionSession(selection_id, timeout_task=None)` — 选择会话数据类
- `chat_sessions: dict[str, ChatSession]` — 用户聊天会话字典
- `selection_sessions: dict[str, SelectionSession]` — 用户选择会话字典
- `get_or_create_chat(user_id) -> ChatSession` — 获取或创建聊天会话
- `activate_chat(user_id, command_type, matcher) -> bool` — 激活会话（返回 False 表示已有活跃会话）
- `deactivate_chat(user_id) -> None` — 重置会话为空闲
- `create_selection(user_id, selection_id, timeout_task) -> None` — 创建选择会话
- `remove_selection(user_id) -> SelectionSession | None` — 移除选择会话
- `get_selection(user_id) -> SelectionSession | None` — 查询选择会话
- `execute_cancel(user_id, message="当前会话已取消") -> bool` — 取消逻辑（自取消保护、跨 task 取消、选择会话清理）
- `got_intercept_bypass(user_id, matcher, text, HELP_TEXT) -> bool` — got handler 入口拦截 /help 和 /cancel
- `timeout_session(bot, event, user_id, selection_id, message, *, on_cleanup, timeout)` — 会话超时检查任务

### `bot/plugins/meme_add.py`

NoneBot2 命令插件，注册 `/add` 命令。

- 依赖：`app_state.get_index_manager()`、`auth.is_authorized()`、`bot.session`（`activate_chat`/`deactivate_chat`/`got_intercept_bypass`/`create_selection`/`timeout_session`）、`bot.config.read_session_timeout()`
- 锁：只读检查 `IndexManager.is_locked`；管道并发由 `IndexManager._add_sem` 控制
- 管道：`IndexManager.add_single_file() -> AddResult`
- 图片下载：`httpx.AsyncClient`，30s 超时
- 文件名：`_sanitize_filename()` 安全化 / `_auto_filename()` 自动生成
- 文件冲突：`resolve_unique_filename()`
- 超时：`handle_add` 中创建 selection_id 并注册 `timeout_session` 超时任务；`got` prompt 由 `read_session_timeout()` 动态生成
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"
- `/cancel` 和 `/help` 在 got 等待阶段可旁路触发（`got_intercept_bypass`）
- 错误处理：`try/except/else` 模式，异常统一集中处理

### `bot/plugins/meme_ai.py`

NoneBot2 命令插件，注册 `/ai` 命令。

- 依赖：`app_state.get_ai_matcher()`、`app_state.get_index_manager()`、`auth.is_authorized()`
- 锁：只读检查 `IndexManager.is_locked`
- 匹配：`_do_match()` 封装异常处理，`asyncio.gather()` 并发执行 send 与 match
- 图片：`MessageSegment.image("file://" + str(image_path.resolve()))`
- 群聊：授权用户群聊 @bot 调用时回复"此命令仅限私聊使用"

### `bot/plugins/meme_search.py`

NoneBot2 命令插件，注册 `/search` 命令（薄包装，核心逻辑委托 `_search_utils`）。

- 依赖：`auth.is_authorized()`、`_search_utils.execute_search`、`_search_utils.handle_got_selection`、`bot.session`（`activate_chat`、`deactivate_chat`）
- 流程：`handle_search` — 授权校验 → 会话检查 → 提取关键词 → `execute_search`
- 选择：`got_selection` — 薄包装，委托 `_search_utils.handle_got_selection()`

### `bot/config.py`

全局路径常量与配置读取模块，详见 `docs/api/bot/config.md`。

- `PROJECT_ROOT: Path` — 项目根目录，绝对路径
- `MEMES_DIR: Path` — 表情包图片目录，绝对路径 `<项目根>/memes`
- `read_session_timeout() -> int` — 从 `SESSION_EXPIRE_TIMEOUT` 环境变量读取会话超时秒数，支持纯数字和 `HH:MM:SS` 格式（pydantic 解析），默认 60
- `read_ocr_provider() -> str` — 从 `OCR_PROVIDER` 环境变量读取 OCR 引擎类型，默认 `"paddle"`，有效值：`"deepseek"`、`"paddle"`
