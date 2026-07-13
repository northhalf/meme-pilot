# 关键词搜索冷启动预热设计（修订版）

## 背景

当前关键词搜索采用两层匹配：

1. 使用去除所有空白、保留助词的原始关键词执行精确子串匹配；
2. 精确层无命中时，使用 `jieba.posseg` 去除助词，再通过 `pylcs` 对全部 OCR 文本执行 LCS 模糊匹配。

生产日志显示，在约 1,200 条元数据下：

- 精确子串命中耗时约 0.75 ms；
- 进程内首次模糊未命中搜索耗时约 610–785 ms；
- 随后重复模糊搜索降至几毫秒。

由此确认，主要延迟来自首次调用 `jieba.posseg.cut()` 时对默认词典的惰性初始化，而不是元数据读取、精确扫描或热态 LCS 扫描。精确匹配会提前返回，因此无法顺便初始化 jieba，首位触发模糊回退的用户承担了冷启动成本。

## 目标与范围

### 目标

- 在 Bot 对外可用前完成 jieba 默认词典初始化。
- 在约 1,200 条索引的目标部署环境中，首次模糊未命中搜索耗时低于 50 ms。
- 保持关键词搜索的输入、输出、匹配、排序和异常契约不变。
- 预热失败时中止 Bot 启动，立即暴露部署错误。

### 非目标

- 不优化已经只有几毫秒的热态 LCS 扫描。
- 不增加查询缓存、倒排索引、候选剪枝或新的搜索数据结构。
- 不替换 jieba 或 pylcs。
- 不改变精确优先、去助词、阈值 60、100 分过滤或结果排序规则。
- 不新增第三方依赖、系统工具或配置项。

## 方案比较

### 方案 A：由 `KeywordSearcher` 暴露预热接口，在 `_background_sync` 中与首次索引刷新并发执行（采用）

在 `KeywordSearcher` 中增加同步生命周期方法 `warm_up()`，内部调用 `jieba.initialize()`。`_background_sync()` 改为同时执行 `warm_up()` 与 `index_manager.refresh()`；`_on_startup()` 在 `init_app()` 注册全局状态后 `await` 该任务完成，再记录启动完成。

优点：

- jieba 依赖继续封装在关键词搜索模块内；
- 启动入口不依赖具体分词实现；
- 能单独测试调用及异常传播；
- 预热与首次索引刷新并发，总等待时间取两者最大值而非求和；
- Bot 对外可用前已完成预热和首次索引刷新。

### 方案 B：由 `KeywordSearcher` 暴露预热接口，启动流程同步调用（已废弃）

在 `_on_startup()` 注册 `app_state` 前同步调用 `warm_up()`。该方案已被修订版替代，因为它会阻塞事件循环并延长顺序启动时间。

### 方案 C：后台异步预热但不等待（不采用）

不会阻塞启动，但 Bot 启动后立即收到的查询仍可能撞上初始化过程，无法稳定满足首次模糊搜索低于 50 ms 的目标。

## 组件设计

### `KeywordSearcher.warm_up()`

在 `bot/engine/keyword_searcher.py` 中增加以下公开同步方法：

```python
def warm_up(self) -> None:
    """预热关键词搜索依赖，提前加载 jieba 默认词典。"""
```

职责：

- 调用 `jieba.initialize()`，显式加载默认词典；
- 使用项目已有的 `@timed` 装饰器记录完整预热耗时；
- 不访问元数据；
- 不构造或执行虚拟关键词查询；
- 不执行 LCS 扫描；
- 不捕获、包装或吞掉初始化异常。

该接口不接收参数，成功时返回 `None`。

### 启动编排

调整 `bot/bot.py::_on_startup()` 的顺序：

1. 配置日志并创建外部服务；
2. 创建 `MetadataStore` 与 `VectorStore`；
3. 创建 `KeywordSearcher`、`RandomSearcher`、`SemanticSearcher`、`CombinedSearcher` 和 `IndexManager`；
4. 加载 `IndexManager`；
5. 通过 `init_app()` 注册全局状态（Bot 内部可获取服务，但 `_on_startup` 尚未返回，外部请求尚未进入）；
6. 在 `_background_sync()` 中并发执行 `keyword_searcher.warm_up()` 与 `index_manager.refresh()`；
7. `_on_startup()` `await _background_sync(index_manager)`，等待两者完成；
8. 记录启动完成。

`_background_sync()` 内部使用 `asyncio.gather()` 并发运行 `warm_up()`（通过 `asyncio.to_thread()` 包装）和 `index_manager.refresh()`。`gather(return_exceptions=False)` 确保任一失败时异常立即向上传播，`_on_startup()` 不会完成，Bot 不对外可用。

### 运行时数据流

预热只改变依赖初始化时机，查询流程保持不变：

```text
用户查询
  ├─ 精确子串命中 → 直接返回
  └─ 精确未命中
       ├─ jieba.posseg 去助词（默认词典已加载）
       ├─ pylcs 扫描 OCR 文本
       └─ 阈值过滤、排序并返回
```

以下契约全部保持不变：

- `search()` 和 `search_in()` 的参数、返回值及同步调用方式；
- 空关键词、全空白关键词和空索引返回空列表；
- 精确层命中后不执行模糊层；
- 模糊层最低相似度阈值为 60；
- 存在 100 分结果时只保留 100 分结果；
- `limit` 截断语义；
- `SearchResult` 字段和排序语义；
- `/query`、`/rand` 和普通文本兜底搜索的用户可见行为。

## 错误处理

`warm_up()` 不捕获 `jieba.initialize()` 的异常。词典损坏、权限错误或依赖异常会沿 `_background_sync()` → `_on_startup()` 向上传播并导致 Bot 启动失败。

`index_manager.refresh()` 失败同样通过 `asyncio.gather` 向上传播。

这样可以在部署阶段立即暴露错误，避免 Bot 看似正常运行后才由首位用户触发故障。本次不增加精确搜索降级模式、运行时禁用状态或新的用户提示。

## 日志与可观测性

`warm_up()` 使用已有 `@timed(logger, "关键词搜索预热")` 记录完整函数耗时。成功时记录预热完成信息；失败时依赖启动框架输出异常栈，不在方法内部重复记录后再抛出，以免产生重复错误日志。

日志不包含用户输入、词典内容或其他敏感信息。

## 测试设计

### `KeywordSearcher` 单元测试

在 `tests/unit/engine/test_keyword_searcher.py` 增加：

1. **预热调用测试**
   - mock `jieba.initialize()`；
   - 调用 `KeywordSearcher.warm_up()`；
   - 断言初始化函数恰好调用一次。

2. **异常传播测试**
   - mock `jieba.initialize()` 抛出代表性异常；
   - 断言 `warm_up()` 原样向上传播异常；
   - 证明方法没有吞掉或转换异常。

现有关键词搜索测试继续验证匹配与排序契约不变。

### 启动编排验证

如果现有测试结构能以小范围 mock 覆盖 `_background_sync()`，则验证：

- `warm_up()` 与 `index_manager.refresh()` 被并发调用；
- 任一失败时 `_background_sync()` 向上传播异常；
- `_on_startup()` 在 `init_app()` 之后 `await` 该任务。

如果覆盖 `_background_sync()` 必须搭建大量与本次改动无关的外部服务 mock，则不为启动编排改动新增脆弱的大型测试。此时使用代码顺序审查、`warm_up()` 单元测试和真实启动验收覆盖。

## 性能验收

在目标部署环境执行：

1. 重启 Bot；
2. 确认日志出现关键词搜索预热完成及耗时记录；
3. 首次发送一个精确子串不命中的关键词，例如“不耻下问”；
4. 在约 1,200 条索引下确认 `关键词搜索 完成，耗时` 低于 50 ms；
5. 确认查询结果与改动前一致；
6. 执行精确命中查询“君子”，确认结果无回归且仍为毫秒级。

`< 50 ms` 不作为普通共享 CI 环境中的硬性时间断言，以免机器负载波动造成不稳定测试；它是目标部署环境的集成验收指标。

## 文档同步

实现时同步更新 `docs/api/API.md` 中的对应模块文档：

- `docs/api/bot/engine/keyword_searcher.md`：记录 `KeywordSearcher.warm_up()` 的职责、参数、返回值和异常行为。
- `docs/api/bot/bot.md`：记录 `_background_sync()` 并发执行预热与首次索引刷新，以及 `_on_startup()` 注册后等待完成的行为。

无需修改 `docs/PRD.md`：用户可见搜索行为未改变，现有关键词搜索低于 1 秒的非功能要求也未改变。

## 依赖与环境

- Python 3.12；
- 复用项目现有 `jieba>=0.42.1`；
- 复用现有日志计时装饰器；
- 不需要额外的 Python 包、LSP、格式化工具或系统工具。
