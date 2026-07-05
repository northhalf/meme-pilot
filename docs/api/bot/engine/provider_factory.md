# bot/engine/provider_factory.py — Provider 工厂与注册表

> 维护 OCR 与 Embedding provider 的注册表，支持按名称创建实例。
> 依赖缺失的 provider 可在 `bot/engine/__init__.py` 加载时被标记为不可用，使用时会抛出明确错误。

## 类型别名

### `Factory`

```python
Factory: TypeAlias = Callable[[], OcrProvider]
```

无参工厂函数，返回 `OcrProvider` 实例。

### `EmbeddingFactory`

```python
EmbeddingFactory: TypeAlias = Callable[[], EmbeddingProvider]
```

无参工厂函数，返回 `EmbeddingProvider` 实例。

---

## 注册表

### `OCR_REGISTRY: dict[str, Factory]`

OCR provider 名称到工厂函数的映射。

### `EMBEDDING_REGISTRY: dict[str, EmbeddingFactory]`

Embedding provider 名称到工厂函数的映射。

### `_UNAVAILABLE_OCR_PROVIDERS: dict[str, str]`

被标记为不可用的 OCR provider 名称及其原因（模块内部使用）。

### `_UNAVAILABLE_EMBEDDING_PROVIDERS: dict[str, str]`

被标记为不可用的 Embedding provider 名称及其原因（模块内部使用）。

---

## 异常

### `ProviderNotAvailableError(ValueError)`

Provider 因依赖缺失、初始化失败等原因不可用时抛出。

---

## 注册函数

### `register_ocr(name: str, factory: Factory) -> None`

注册 OCR provider 工厂函数。

### `register_embedding(name: str, factory: EmbeddingFactory) -> None`

注册 Embedding provider 工厂函数。

### `mark_ocr_unavailable(name: str, reason: str) -> None`

标记 OCR provider 不可用并记录原因。

### `mark_embedding_unavailable(name: str, reason: str) -> None`

标记 Embedding provider 不可用并记录原因。

---

## 工厂函数

### `create_ocr_provider(name: str) -> OcrProvider`

按名称创建 OCR provider 实例。

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | provider 名称，如 `"paddle"`、`"deepseek"`、`"rapidocr"` |

| 返回 | 说明 |
|------|------|
| `OcrProvider` | 工厂创建的 OCR provider 实例 |

| 异常 | 说明 |
|------|------|
| `ProviderNotAvailableError` | provider 已被标记为不可用 |
| `ValueError` | 未知 provider 名称 |

### `create_embedding_provider(name: str) -> EmbeddingProvider`

按名称创建 Embedding provider 实例。

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | provider 名称，如 `"openai"`、`"google"` |

| 返回 | 说明 |
|------|------|
| `EmbeddingProvider` | 工厂创建的 Embedding provider 实例 |

| 异常 | 说明 |
|------|------|
| `ProviderNotAvailableError` | provider 已被标记为不可用 |
| `ValueError` | 未知 provider 名称 |

---

## 测试辅助

### `reset_provider_registries() -> None`

清空 OCR 与 Embedding 的注册表及不可用状态。

主要用于测试隔离，避免注册表状态污染。
