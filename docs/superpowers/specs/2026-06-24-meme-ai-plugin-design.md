# /ai 命令插件设计

> 日期：2026-06-24
> 状态：待实现

## 概述

实现 `/ai <自然语言描述>` 命令插件（`bot/plugins/meme_ai.py`），通过 Embedding 语义搜索 + LLM 精排两阶段匹配表情包。

## app_state 扩展

### 变更

- `app_state.py` 新增 `_ai_matcher: AIMatcher | None` 模块级变量
- `init_app()` 新增 `ai_matcher: AIMatcher` 参数
- 新增 `get_ai_matcher() -> AIMatcher` 函数

### 依赖组装

```
bot.py 启动 →
  IndexManager(data_dir, memes_dir, ocr, embedding)
  EmbeddingService()
  RerankService()
  AIMatcher(IndexManager, EmbeddingService, RerankService)
  init_app(IndexManager, OcrService, EmbeddingService, ImageOptimizer, AIMatcher)
```

## 插件设计

### 文件

`bot/plugins/meme_ai.py`

### 注册

```python
ai_cmd = on_command("ai", rule=to_me(), priority=5, block=True)
```

### 流程

```
用户: /ai <自然语言描述>
  │
  ▼
handle_ai()
  ├── 授权校验 (is_authorized)
  ├── 获取 IndexManager 实例 (get_index_manager)
  ├── 检查索引锁 (index_manager.is_locked) — 占用则回复"索引正在更新，请稍后再试"
  ├── 获取 AIMatcher 实例 (get_ai_matcher)
  ├── 检查描述是否为空 — 空则回复用法提示
  ├── 检查索引是否为空 (index_manager.entry_count == 0) — 空则回复"表情包目录为空"
  ├── 回复"正在根据你的描述搜索表情包，请稍候..."
  ├── 调用 AIMatcher.match(description)
  │     ├── ValueError → "AI 服务暂时不可用，稍后重试"
  │     ├── Exception → "AI 服务暂时不可用，稍后重试"
  │     ├── None → "没有找到匹配的表情包 🙁"
  │     └── AIMatchResult → 发送图片
  └── 完成
```

### 异常处理

| 异常类型 | 触发场景 | 回复 |
|----------|---------|------|
| ValueError | embedding 无效（空/零向量） | "AI 服务暂时不可用，稍后重试" |
| Exception | API 网络故障等 | "AI 服务暂时不可用，稍后重试" |
| None 返回 | 无候选表情包 | "没有找到匹配的表情包 🙁" |

### 图片发送

使用 `MessageSegment.image(f"file:///{path.resolve()}")` 发送本地图片（OneBot V11 本地文件需 `file:///` URI）。

### 锁行为

- PRD 明确要求：索引更新锁占用期间，/ai 回复"索引正在更新，请稍后再试"
- /ai 是只读操作，使用 `index_manager.is_locked` 只读检查锁状态，不调用 `acquire_lock()`
- /ai 不持有锁，不阻塞其他命令

### 会话管理

- /ai 是单步命令，直接返回结果，不使用 session 管理
- 不注册会话，不参与跨命令会话覆盖

## 测试设计

### 文件

`tests/unit/plugins/test_meme_ai.py`

### Mock 策略

- `AIMatcher.match()` — mock 返回值/异常
- `app_state.get_ai_matcher()` — 返回 mock AIMatcher
- `app_state.get_index_manager()` — 返回 mock IndexManager（`is_locked` 属性）
- `auth.is_authorized()` — 模拟授权/非授权

### 覆盖场景

| # | 场景 | 预期行为 |
|---|------|---------|
| 1 | 非授权用户 | 静默忽略，不回复 |
| 2 | 索引锁占用 | 回复"索引正在更新，请稍后再试" |
| 3 | 描述为空 | 回复用法提示 `/ai <描述>` |
| 4 | 匹配成功 | 发送图片文件 |
| 5 | 无候选 (None) | 回复"没有找到匹配的表情包 🙁" |
| 6 | Embedding 异常 (ValueError) | 回复"AI 服务暂时不可用，稍后重试" |
| 7 | 通用异常 | 回复"AI 服务暂时不可用，稍后重试" |
| 8 | 索引/表情包目录为空 | 回复"表情包目录为空" |

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `bot/app_state.py` | 修改 | 新增 `_ai_matcher`、`get_ai_matcher()`，`init_app()` 新增参数 |
| `bot/plugins/meme_ai.py` | 新建 | /ai 命令插件 |
| `tests/unit/plugins/test_meme_ai.py` | 新建 | 单元测试 |
| `docs/api/API.md` | 修改 | 新增 meme_ai.md 和 app_state 更新 |
| `docs/api/bot/plugins/meme_ai.md` | 新建 | 插件 API 文档 |

## 参考

- PRD §3.2：AI 描述匹配
- CONTEXT.md：AI 匹配术语
- bot/engine/ai_matcher.py：AIMatcher 实现
- bot/engine/rerank_service.py：RerankService 实现
- bot/plugins/meme_add.py：现有插件模式参考
