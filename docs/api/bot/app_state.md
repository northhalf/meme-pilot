# bot/app_state.py — 共享实例管理 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀变量不在此列出。

## 模块级函数

### `init_app(index_manager, ocr_service, embedding_service) -> None`

| 参数 | 类型 | 说明 |
|------|------|------|
| `index_manager` | `IndexManager` | 索引管理器实例 |
| `ocr_service` | `DeepSeekOcrService` | OCR 服务实例 |
| `embedding_service` | `EmbeddingService` | Embedding 服务实例 |

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

### `get_ocr_service() -> DeepSeekOcrService`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `DeepSeekOcrService` | 已初始化的 OCR 服务实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |

---

### `get_embedding_service() -> EmbeddingService`

| | 类型 | 说明 |
|--|------|------|
| **返回** | `EmbeddingService` | 已初始化的 Embedding 服务实例 |
| **异常** | `RuntimeError` | 尚未调用 `init_app()` 初始化 |
