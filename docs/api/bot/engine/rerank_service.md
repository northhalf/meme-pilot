# bot/engine/rerank_service.py — 精排服务 API

> 本文档只记录模块对外接口。模块内部 `_` 前缀函数和方法不在此列出。

## 类

### `RerankService`

DeepSeek 精排服务，通过 LLM 从候选中选出最佳匹配。

实现 `ai_matcher.RerankProvider` 协议，可直接注入给 `AIMatcher` 使用。

支持任何兼容 OpenAI chat completions API 的服务商（如 DeepSeek、OpenAI 等），只需配置 `base_url` 和 `model` 即可。

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
    ) -> int
```

---

## 构造函数

### `__init__(api_key=None, base_url=None, model=None) -> None`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API Key，默认从 `DEEPSEEK_API_KEY` 环境变量读取 |
| `base_url` | `str \| None` | `None` | API 地址，默认从 `DEEPSEEK_BASE_URL` 环境变量读取，回退为 `https://api.deepseek.com` |
| `model` | `str \| None` | `None` | 精排模型名，默认从 `DEEPSEEK_MODEL` 环境变量读取，回退为 `deepseek-v4-flash` |

参数优先级：构造参数 > 环境变量 > 默认值。

---

## 方法

### `rerank(description: str, candidates: list[AIMatchCandidate]) -> int`

从候选中选出最匹配的临时序号。

| 参数 | 类型 | 说明 |
|------|------|------|
| `description` | `str` | 用户自然语言描述 |
| `candidates` | `list[AIMatchCandidate]` | embedding 阶段 Top N 候选 |

| 返回 | 说明 |
|------|------|
| `int` | 1-based 临时候选序号；返回 0 表示放弃精排 |

| 异常 | 说明 |
|------|------|
| `ValueError` | 候选列表为空 |
| `RuntimeError` | API 调用失败或返回无法解析 |

通过 DeepSeek LLM 从 Top N 候选中精排出最佳匹配。按照 PRD 要求，只发送候选 id 和 OCR 文本，不发送文件名。

---

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DEEPSEEK_API_KEY` | API Key | `""` |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-v4-flash` |
