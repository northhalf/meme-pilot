# ai_matcher.py 设计

## 背景

`/ai` 需要根据自然语言描述返回 1 张表情包。PRD 规定流程为：先用 SiliconFlow embedding 做语义召回，取 Top 10；再由 DeepSeek LLM 精排；精排失败、解析失败或返回 `0` 时，回退到 embedding Top 1。

本次只实现 engine 层的 `bot/engine/ai_matcher.py`，并给 `IndexManager` 增加公开的向量读取接口。NoneBot 插件和 DeepSeek 服务封装不在本次范围内。

## 目标

- `AIMatcher.match(description)` 返回唯一匹配结果，或在无候选时返回 `None`。
- `AIMatcher` 通过协议依赖索引、embedding provider、rerank provider，不访问 `IndexManager._embeddings`。
- 单条坏向量不影响其他候选。
- 用户描述 embedding 失败时抛出异常，由插件层决定用户提示。
- 文档同步更新 `docs/process.md` 和 `docs/api/`。

## 不做的事

- 不实现 `/ai` 命令插件。
- 不在 `ai_matcher.py` 中直接调用 DeepSeek 或 OpenAI SDK。
- 不新增第三方依赖。
- 不改变 `embeddings.json` 文件结构。

## 接口设计

### `IndexManager.get_embeddings()`

新增公开方法：

```python
def get_embeddings(self) -> dict[str, dict[str, object]]:
    """返回 embeddings.json 中的向量索引。"""
```

方法返回当前内存向量索引的浅拷贝。调用方可以读取每个 `entry_id` 对应的 `text_hash` 和 `embedding`，但不应修改返回值后期待写回生效。

### `AIIndexProvider`

`AIMatcher` 使用协议而非具体 `IndexManager`：

```python
class AIIndexProvider(Protocol):
    def get_entries(self) -> dict[str, dict[str, str]]: ...
    def get_embeddings(self) -> dict[str, dict[str, object]]: ...
```

### `EmbeddingProvider`

复用现有语义：

```python
class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

`AIMatcher` 用它生成用户描述向量。调用异常向外抛。

### `RerankProvider`

本次只定义协议：

```python
class RerankProvider(Protocol):
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int: ...
```

返回值是候选临时序号，使用 1-based 编号。返回 `0` 表示放弃精排。后续 DeepSeek 服务只要实现该协议即可接入。

### 数据类

`AIMatchCandidate` 表示 embedding 阶段候选：

```python
@dataclass(frozen=True)
class AIMatchCandidate:
    rank: int
    entry_id: str
    filename: str
    text: str
    similarity: float
```

`AIMatchResult` 表示最终结果：

```python
@dataclass(frozen=True)
class AIMatchResult:
    entry_id: str
    filename: str
    text: str
    similarity: float
    source: str
```

`source` 取值：

- `"rerank"`：reranker 返回有效候选。
- `"embedding"`：未配置 reranker，或 reranker 失败后回退到 Top 1。

### `AIMatcher`

```python
class AIMatcher:
    def __init__(
        self,
        index_provider: AIIndexProvider,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
        limit: int = 10,
    ) -> None: ...

    async def match(self, description: str) -> AIMatchResult | None: ...
```

`limit` 控制 embedding 阶段传给 reranker 的候选数量，默认 10。

## 数据流

1. `match()` 对 `description` 执行 `strip()`。
2. 空描述返回 `None`。
3. 调用 `embedding_provider.embed(description)` 生成用户描述向量。
4. 读取 `get_entries()` 和 `get_embeddings()`。
5. 遍历 entries。每个条目需要同时满足：
   - `entry_id` 在 embeddings 中存在；
   - embedding 是非空数字列表；
   - embedding 维度与用户描述向量一致；
   - 当前条目的 OCR 文本非空。
6. 对有效候选计算余弦相似度。
7. 按相似度降序排序，取 Top `limit`。
8. 无候选返回 `None`。
9. 没有 reranker 时返回 Top 1，`source="embedding"`。
10. 有 reranker 时调用 `rerank(description, candidates)`。
11. reranker 返回有效临时序号时返回对应候选，`source="rerank"`。
12. reranker 异常、返回 `0`、返回非整数或越界时，返回 Top 1，`source="embedding"`。

## 排序规则

主排序键为相似度，分数高者在前。

相似度相同时，优先使用可转为整数的 `entry_id` 做升序排序。若 `entry_id` 不能转成整数，则按字符串排序。这样测试和线上结果保持稳定。

## 错误处理

- 用户描述 embedding provider 抛出的异常不在 `AIMatcher` 中吞掉。
- 用户描述向量为空、非数字或维度不可用时，`AIMatcher` 抛出 `ValueError`。
- 单条索引向量异常时，`AIMatcher` 记录 warning 并跳过该条。
- 单条索引向量为零向量时，`AIMatcher` 记录 warning 并跳过该条。
- reranker 失败或输出不可用时，`AIMatcher` 记录 warning 并回退到 embedding Top 1。

## 测试计划

新增 `tests/unit/engine/test_ai_matcher.py`，覆盖：

- 空描述返回 `None`。
- 无 entries 返回 `None`。
- 无 embeddings 返回 `None`。
- 正常余弦相似度排序返回 Top 1。
- `limit` 限制传给 reranker 的候选数量。
- reranker 返回有效序号时使用精排结果。
- reranker 抛异常时 fallback Top 1。
- reranker 返回 `0` 时 fallback Top 1。
- reranker 返回越界或非整数时 fallback Top 1。
- 坏索引向量被跳过。
- 用户描述 embedding provider 异常向外抛。
- 用户描述向量非法时抛 `ValueError`。

更新 `tests/unit/engine/test_index_manager.py`，覆盖 `get_embeddings()` 返回浅拷贝，不暴露 `_embeddings` 字典本身。

## 文档更新

- `docs/process.md`：补充 `bot/engine/ai_matcher.py` 完成情况。
- `docs/api/API.md`：增加 `ai_matcher.md` 索引；补充 `IndexManager.get_embeddings()`。
- `docs/api/bot/engine/index_manager.md`：记录 `get_embeddings()`。
- `docs/api/bot/engine/ai_matcher.md`：记录协议、数据类和 `AIMatcher` 行为。

## 依赖

不新增依赖。

已有开发命令：

```bash
uv run pytest tests/unit/engine/test_ai_matcher.py tests/unit/engine/test_index_manager.py
uv run python -m compileall bot tests
```
