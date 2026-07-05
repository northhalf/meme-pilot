# bot/app_state.py — 共享实例管理 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀变量不在此列出。

## 模块级函数

### `init_app(index_manager, metadata_store, vector_store, ocr_service, embedding_service, image_optimizer=None, ai_matcher=None, keyword_searcher=None) -> None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `index_manager` | `IndexManager` | 索引管理器实例 |
| `metadata_store` | `MetadataStore` | 元数据存储实例 |
| `vector_store` | `VectorStore` | 向量存储实例 |
| `ocr_service` | `OcrProvider` | OCR 服务实例 |
| `embedding_service` | `EmbeddingProvider` | Embedding 服务实例 |
| `image_optimizer` | `ImageOptimizer \| None` | 图片压缩器实例，可选 |
| `ai_matcher` | `AIMatcher \| None` | AI 匹配器实例，可选 |
| `keyword_searcher` | `KeywordSearcher \| None` | 关键词搜索引擎实例，可选 |

| | 类型 | 说明 |
|--|------|------|
| **返回** | `None` | |
| **副作用** | 设置模块级全局单例 | 后续 `get_*()` 返回这些实例 |

由 `bot.py` 的 NoneBot2 startup hook 调用一次。重复调用会覆盖旧实例。

---

### `get_index_manager() -> IndexManager`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `IndexManager` | 已初始化的索引管理器实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_metadata_store() -> MetadataStore`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `MetadataStore` | 已初始化的元数据存储实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_vector_store() -> VectorStore`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `VectorStore` | 已初始化的向量存储实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_ocr_service() -> OcrProvider`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `OcrProvider` | 已初始化的 OCR 服务实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_embedding_service() -> EmbeddingProvider`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `EmbeddingProvider` | 已初始化的 Embedding 服务实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_image_optimizer() -> ImageOptimizer | None`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `ImageOptimizer \| None` | 已初始化的图片压缩器实例，或 None（未注入时） |

---

### `get_ai_matcher() -> AIMatcher`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `AIMatcher` | 已初始化的 AI 匹配器实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_keyword_searcher() -> KeywordSearcher`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `KeywordSearcher` | 已初始化的关键词搜索引擎实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |
