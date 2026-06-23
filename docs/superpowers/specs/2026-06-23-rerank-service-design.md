# RerankService 设计文档

> 日期：2026-06-23
> 状态：待实现

---

## 1. 概述

### 1.1 目标

实现 `bot/engine/rerank_service.py`，封装 DeepSeek LLM 精排功能，实现 `ai_matcher.RerankProvider` 协议。

### 1.2 背景

- `ai_matcher.py:68` 定义了 `RerankProvider` 协议
- PRD 第 141-148 行定义了精排逻辑和 prompt 模板
- `embedding_service.py` 和 `ocr_service.py` 提供了 OpenAI SDK 调用模式参考

### 1.3 需求来源

- 支持任何 OpenAI 兼容服务（通过 base_url 和 model 配置）
- 固定使用 PRD 中的 prompt 模板
- 5 秒超时设置
- 与现有服务一致的日志级别（DEBUG/WARNING）

---

## 2. 设计方案

### 2.1 方案选择

**选择方案**：单文件实现

**理由**：
1. 与 `embedding_service.py`、`ocr_service.py` 风格完全一致
2. 需求明确，不需要频繁扩展 prompt 或解析逻辑
3. 简单直接，符合项目整体风格和 YAGNI 原则

**拒绝的方案**：
- 方案 2（分离 prompt 和解析函数）：对于单一用途过度设计
- 方案 3（配置类封装）：增加复杂度，与现有服务风格不一致

---

## 3. 类结构与接口

### 3.1 文件位置

`bot/engine/rerank_service.py`

### 3.2 类定义

```python
class RerankService:
    """DeepSeek 精排服务，通过 LLM 从候选中选出最佳匹配。
    
    实现 ai_matcher.RerankProvider 协议，
    可直接注入给 AIMatcher 使用。
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        """初始化 RerankService。
        
        Args:
            api_key: DeepSeek API Key，默认从 DEEPSEEK_API_KEY 环境变量读取。
            base_url: API 地址，默认从 DEEPSEEK_BASE_URL 环境变量读取，
                      回退为 https://api.deepseek.com。
            model: 精排模型名，默认从 DEEPSEEK_MODEL 环境变量读取，
                   回退为 deepseek-v4-flash。
        """
    
    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int:
        """从候选中选出最匹配的临时序号。
        
        Args:
            description: 用户自然语言描述。
            candidates: embedding 阶段 Top N 候选。
        
        Returns:
            1-based 临时候选序号；返回 0 表示放弃精排。
        
        Raises:
            ValueError: 候选列表为空。
            RuntimeError: API 调用失败或返回无法解析。
        """
```

### 3.3 环境变量

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `DEEPSEEK_API_KEY` | 是 | - | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | API 地址 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` | 精排模型名 |

---

## 4. 核心方法实现

### 4.1 Prompt 模板

```python
_SYSTEM_PROMPT = "你是一个表情包匹配助手。"

_USER_PROMPT_TEMPLATE = """用户描述：{description}

以下是候选表情包的文字内容：
{candidates}

请选出最匹配的 1 个，返回序号即可。"""
```

### 4.2 候选列表格式化

```python
def _build_candidates_text(candidates: list[AIMatchCandidate]) -> str:
    """构建候选列表文本。
    
    按照 PRD 要求，只发送 id 和 OCR 文本，不发送文件名。
    """
    lines = []
    for candidate in candidates:
        lines.append(f"{candidate.rank}. {candidate.text}")
    return "\n".join(lines)
```

### 4.3 API 调用

```python
response = await self._client.chat.completions.create(
    model=self._model,
    messages=[
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ],
    temperature=0,  # 确保结果稳定
    max_tokens=10,  # 只需返回数字
)
```

### 4.4 结果解析

```python
def _parse_rank(raw: str, max_rank: int) -> int | None:
    """解析 LLM 返回的序号。
    
    支持以下格式：
    - 纯数字：如 "3"
    - 包含数字的文本：如 "最匹配的是 3" 或 "序号：3"
    
    Returns:
        有效的 1-based 序号；解析失败或越界时返回 None。
    """
    raw = raw.strip()
    if not raw:
        return None
    
    # 尝试直接解析为数字
    try:
        rank = int(raw)
        if 1 <= rank <= max_rank:
            return rank
        return None
    except ValueError:
        pass
    
    # 尝试从文本中提取第一个数字
    import re
    match = re.search(r"\d+", raw)
    if match:
        try:
            rank = int(match.group())
            if 1 <= rank <= max_rank:
                return rank
        except ValueError:
            pass
    
    return None
```

---

## 5. 错误处理策略

### 5.1 异常类型

| 异常类型 | 触发条件 | 处理方式 |
|----------|----------|----------|
| `ValueError` | 候选列表为空 | 直接抛出，由调用方处理 |
| `RuntimeError` | API 调用失败 | 包装原始异常后抛出 |

### 5.2 处理流程

```python
async def rerank(self, description, candidates) -> int:
    if not candidates:
        raise ValueError("候选列表不能为空")
    
    try:
        response = await self._client.chat.completions.create(...)
    except Exception as exc:
        raise RuntimeError(f"DeepSeek 精排 API 调用失败: {exc}") from exc
    
    rank = _parse_rank(raw, max_rank=len(candidates))
    if rank is None:
        logger.warning("DeepSeek 精排返回无法解析: raw=%r，返回 0 放弃精排", raw)
        return 0
    
    return rank
```

### 5.3 与 AIMatcher 的集成

- `AIMatcher._rerank()` 已有完善的异常处理（`ai_matcher.py:195-217`）
- 精排失败时自动 fallback 到 embedding Top 1

### 5.4 超时设置

```python
self._client = AsyncOpenAI(
    api_key=self._api_key,
    base_url=self._base_url,
    timeout=5.0,  # 5 秒超时
)
```

---

## 6. 日志记录

### 6.1 日志级别

与现有服务（`embedding_service.py`、`ocr_service.py`）保持一致：

| 事件 | 级别 | 内容 |
|------|------|------|
| API 调用前 | DEBUG | model、candidates 数量、desc 长度 |
| API 调用后 | DEBUG | rank 值 |
| 解析失败 | WARNING | 原始响应内容 |

### 6.2 日志示例

```python
logger.debug(
    "调用 DeepSeek 精排: model=%s, candidates=%d, desc_len=%d",
    self._model,
    len(candidates),
    len(description),
)

logger.debug("DeepSeek 精排完成: rank=%d", rank)

logger.warning(
    "DeepSeek 精排返回无法解析: raw=%r，返回 0 放弃精排", raw
)
```

---

## 7. 测试策略

### 7.1 测试文件

`tests/unit/engine/test_rerank_service.py`

### 7.2 测试用例

**正常流程**：
- mock API 返回有效序号
- 验证返回正确的 rank

**解析边界**：
- 纯数字 "3" → 3
- 包含数字 "最匹配的是 3" → 3
- 无效文本 "没有匹配" → 0
- 越界序号 "15" → 0

**异常处理**：
- 候选列表为空 → ValueError
- API 调用失败 → RuntimeError

**集成测试**（可选）：
- 与 AIMatcher 集成，验证 fallback 行为

### 7.3 Mock 策略

```python
@pytest.fixture
def mock_openai(mocker):
    """Mock AsyncOpenAI client."""
    client = mocker.AsyncMock()
    mocker.patch("openai.AsyncOpenAI", return_value=client)
    return client
```

---

## 8. 依赖

### 8.1 代码依赖

- `openai`：AsyncOpenAI 客户端
- `bot.engine.ai_matcher`：AIMatchCandidate、RerankProvider

### 8.2 环境依赖

- Python 3.12+
- DEEPSEEK_API_KEY 环境变量

---

## 9. 文档更新

实现完成后需要更新：

1. `docs/api/API.md` - 添加 RerankService 接口说明
2. `docs/process.md` - 添加精排服务实现说明

---

## 10. 验收标准

1. `RerankService` 实现 `RerankProvider` 协议
2. 支持通过环境变量配置 API 地址和模型
3. 固定使用 PRD 中的 prompt 模板
4. 5 秒超时设置
5. 解析失败时返回 0（放弃精排）
6. 单元测试覆盖正常流程和边界情况
7. 与现有服务风格一致的日志记录
