# engine 包协议/类型重构计划

## 目标
- 消除 `semantic_searcher` 从 `keyword_searcher` 导入协议/数据类的跨模块依赖
- 统一 `VectorQueryProvider` 重复定义（`ai_matcher` + `semantic_searcher` 各一份）
- 共用协议集中到 `protocols.py`，共用数据类集中到新建 `types.py`
- 同包导入统一用相对导入（`from .xxx`）

## 决策（用户已确认）
- **范围**：聚焦核心痛点。`MemeEntry`/`VectorHit`/`AIMatchCandidate` 等核心实体留各自定义模块（其他模块相对导入），不移通用文件。
- **通用文件**：协议留 `protocols.py`，新建 `types.py` 放共用数据类。

## 改动清单

### 1. 新建 `bot/engine/types.py`
- `SearchResult` dataclass（从 `keyword_searcher` 移出，`@dataclass` 非 frozen，字段：entry_id/image_path/text/similarity/speaker|None/tags）
- 仅 `from dataclasses import dataclass, field`，不 import engine 其他模块（无循环）

### 2. `bot/engine/protocols.py`（添加共用协议）
- 新增 `MetadataStoreProvider`（从 `keyword_searcher` 移）：`get_all_entries() -> dict[int, MemeEntry]`
- 新增 `VectorQueryProvider`（统一 `ai_matcher` 版，含 `count()` + `query()`）：消除 `ai_matcher`/`semantic_searcher` 重复定义
- 新增 `from .vector_store import VectorHit`（VectorQueryProvider 返回类型引用）

### 3. `bot/engine/keyword_searcher.py`
- 移除 `MetadataStoreProvider`（23-28）+ `SearchResult`（31-49）定义
- 移除不再用的 `from typing import Protocol`、`from dataclasses import dataclass, field`
- 添加 `from .protocols import MetadataStoreProvider`、`from .types import SearchResult`
- `from bot.engine.metadata_store import MemeEntry` -> `from .metadata_store import MemeEntry`（绝对改相对）

### 4. `bot/engine/semantic_searcher.py`
- 移除 `VectorQueryProvider`（12-17）定义
- 移除 `from typing import Protocol`
- 改为：`from .protocols import MetadataStoreProvider, VectorQueryProvider`、`from .types import SearchResult`、`from .vector_store import VectorHit`（绝对改相对）

### 5. `bot/engine/ai_matcher.py`
- 移除 `VectorQueryProvider`（41-64）定义
- `from .protocols import EmbeddingProvider, MetadataEntryProvider` -> 加 `VectorQueryProvider`
- 保留 `from typing import Protocol`（`RerankProvider` 仍用）

### 6. `bot/engine/index_manager.py`
- `from .keyword_searcher import KeywordSearcher, SearchResult` -> 拆为 `from .keyword_searcher import KeywordSearcher` + `from .types import SearchResult`

### 7. `bot/engine/__init__.py`
- `from .keyword_searcher import KeywordSearcher, SearchResult` -> 拆为 `from .keyword_searcher import KeywordSearcher` + `from .types import SearchResult`

### 8. `bot/engine/random_searcher.py`
- `from bot.engine.keyword_searcher import (KeywordSearcher, MetadataStoreProvider, SearchResult)` -> `from .keyword_searcher import KeywordSearcher` + `from .protocols import MetadataStoreProvider` + `from .types import SearchResult`

### 9. `bot/engine/rerank_service.py`
- `from bot.engine.ai_matcher import AIMatchCandidate` -> `from .ai_matcher import AIMatchCandidate`

### 10. `bot/plugins/_search_utils.py`
- `from bot.engine.keyword_searcher import SearchResult` -> `from bot.engine.types import SearchResult`（跨包保持绝对）

### 11. 测试（6 文件）
`test_keyword_searcher.py`、`test_meme_plain_text.py`、`test_meme_rand.py`、`test_meme_search.py`、`test_meme_sim.py`、`test_search_utils.py`
- `from bot.engine.keyword_searcher import SearchResult` -> `from bot.engine.types import SearchResult`
- `test_keyword_searcher.py` 额外：`from bot.engine.keyword_searcher import KeywordSearcher, SearchResult` 拆分

### 12. 文档
- `docs/api/API.md`：`SearchResult` 定义位置 keyword_searcher -> types；`MetadataStoreProvider`/`VectorQueryProvider` -> protocols
- `docs/api/bot/engine/`：`keyword_searcher.md`（移除 SearchResult/MetadataStoreProvider 定义）、`semantic_searcher.md`（VectorQueryProvider 从 protocols 导入）、`ai_matcher.md`（VectorQueryProvider 从 protocols 导入）、`protocols.md`（新增 MetadataStoreProvider/VectorQueryProvider）、新增 `types.md`（SearchResult）
- `CONTEXT.md`：若提及 SearchResult/MetadataStoreProvider/VectorQueryProvider 位置则同步

## 循环导入分析（已验证安全）
- `types.py`：仅 dataclass，不 import engine 其他模块
- `protocols.py`：`from .metadata_store import MemeEntry` + `from .vector_store import VectorHit`；两者均不反向 import protocols/types
- `keyword_searcher`/`semantic_searcher`/`ai_matcher`：从 protocols/types 导入，不被反向导入

## VectorQueryProvider 统一说明
`ai_matcher` 版含 `count()` + `query()`（AIMatcher 用 count 判空库），`semantic_searcher` 版仅 `query()`。统一为含 `count` + `query` 的完整版（放 protocols.py）。`semantic_searcher` 使用此 Protocol（即使不用 count），`VectorStore` 实现满足完整接口。消除重复定义。

## 验证
- `uv run pytest` 全量（预期 602 passed）
- `uv run python -m compileall bot tests`
- pyright（若可用，确认类型契约无回归；项目对 pyright 敏感）
- ⚠️ 不提交 git（main 分支提交须经用户审核）

## 执行方式
subagent 驱动 + sequential-thinking，分步实施 + spec/quality 审查。
