# RerankService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `bot/engine/rerank_service.py`，封装 DeepSeek LLM 精排功能，实现 `ai_matcher.RerankProvider` 协议。

**Architecture:** 单文件实现，参考 `embedding_service.py` 和 `ocr_service.py` 的 OpenAI SDK 调用模式。支持任何 OpenAI 兼容服务，固定使用 PRD 中的 prompt 模板，5 秒超时。

**Tech Stack:** Python 3.12, openai SDK, pytest, pytest-mock

---

## File Structure

| 操作 | 文件路径 | 职责 |
|------|----------|------|
| Create | `bot/engine/rerank_service.py` | 精排服务实现 |
| Create | `tests/unit/engine/test_rerank_service.py` | 单元测试 |
| Modify | `docs/api/API.md` | 添加 RerankService 接口说明 |
| Modify | `docs/process.md` | 添加精排服务实现说明 |

---

### Task 1: 创建 rerank_service.py 基础结构

**Files:**
- Create: `bot/engine/rerank_service.py`

- [ ] **Step 1: 创建文件并添加模块文档和导入**

```python
"""精排服务模块 — DeepSeek LLM 精排封装。

通过 OpenAI 兼容的 chat completions API 调用 DeepSeek 模型，
从 embedding 阶段的 Top N 候选中精排出最终匹配的表情包。

实现 ai_matcher.RerankProvider 协议。
"""

import logging
import os
import re

from openai import AsyncOpenAI

from bot.engine.ai_matcher import AIMatchCandidate

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: 添加 prompt 常量**

```python
# 精排系统 prompt
_SYSTEM_PROMPT = "你是一个表情包匹配助手。"

# 精排用户 prompt 模板：{description} 为用户描述，{candidates} 为候选列表
_USER_PROMPT_TEMPLATE = """用户描述：{description}

以下是候选表情包的文字内容：
{candidates}

请选出最匹配的 1 个，返回序号即可。"""
```

- [ ] **Step 3: 添加辅助函数**

```python
def _build_candidates_text(candidates: list[AIMatchCandidate]) -> str:
    """构建候选列表文本。

    按照 PRD 要求，只发送 id 和 OCR 文本，不发送文件名。

    Args:
        candidates: 候选表情包列表。

    Returns:
        格式化的候选列表字符串。
    """
    lines: list[str] = []
    for candidate in candidates:
        lines.append(f"{candidate.rank}. {candidate.text}")
    return "\n".join(lines)


def _parse_rank(raw: str, max_rank: int) -> int | None:
    """解析 LLM 返回的序号。

    从响应文本中提取数字序号，支持以下格式：
    - 纯数字：如 "3"
    - 包含数字的文本：如 "最匹配的是 3" 或 "序号：3"

    Args:
        raw: LLM 原始响应文本。
        max_rank: 最大有效序号。

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

- [ ] **Step 4: 添加 RerankService 类**

```python
class RerankService:
    """DeepSeek 精排服务，通过 LLM 从候选中选出最佳匹配。

    实现 ai_matcher.RerankProvider 协议，
    可直接注入给 AIMatcher 使用。

    Attributes:
        _client: AsyncOpenAI 客户端。
        _model: 精排模型名称。
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
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._base_url = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )
        self._model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=5.0,
        )

    async def rerank(
        self,
        description: str,
        candidates: list[AIMatchCandidate],
    ) -> int:
        """从候选中选出最匹配的临时序号。

        通过 DeepSeek LLM 从 Top N 候选中精排出最佳匹配。
        按照 PRD 要求，只发送候选 id 和 OCR 文本，不发送文件名。

        Args:
            description: 用户自然语言描述。
            candidates: embedding 阶段 Top N 候选。

        Returns:
            1-based 临时候选序号；返回 0 表示放弃精排。

        Raises:
            ValueError: 候选列表为空。
            RuntimeError: API 调用失败或返回无法解析。
        """
        if not candidates:
            raise ValueError("候选列表不能为空")

        candidates_text = _build_candidates_text(candidates)
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            description=description,
            candidates=candidates_text,
        )

        logger.debug(
            "调用 DeepSeek 精排: model=%s, candidates=%d, desc_len=%d",
            self._model,
            len(candidates),
            len(description),
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=10,
            )
        except Exception as exc:
            raise RuntimeError(f"DeepSeek 精排 API 调用失败: {exc}") from exc

        raw = response.choices[0].message.content or ""
        rank = _parse_rank(raw, max_rank=len(candidates))

        if rank is None:
            logger.warning(
                "DeepSeek 精排返回无法解析: raw=%r，返回 0 放弃精排", raw
            )
            return 0

        logger.debug("DeepSeek 精排完成: rank=%d", rank)
        return rank
```

- [ ] **Step 5: 验证语法**

Run: `uv run python -m compileall bot/engine/rerank_service.py`
Expected: `SyntaxCheck passed`

---

### Task 2: 创建单元测试

**Files:**
- Create: `tests/unit/engine/test_rerank_service.py`

- [ ] **Step 1: 创建测试文件并添加导入和 fixtures**

```python
"""rerank_service 单元测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.engine.ai_matcher import AIMatchCandidate
from bot.engine.rerank_service import RerankService, _build_candidates_text, _parse_rank


@pytest.fixture
def sample_candidates() -> list[AIMatchCandidate]:
    """创建测试用候选列表。"""
    return [
        AIMatchCandidate(rank=1, entry_id="1", filename="a.jpg", text="开心", similarity=0.9),
        AIMatchCandidate(rank=2, entry_id="2", filename="b.jpg", text="难过", similarity=0.8),
        AIMatchCandidate(rank=3, entry_id="3", filename="c.jpg", text="生气", similarity=0.7),
    ]


@pytest.fixture
def mock_openai(mocker):
    """Mock AsyncOpenAI client."""
    mock_client = AsyncMock()
    mocker.patch("bot.engine.rerank_service.AsyncOpenAI", return_value=mock_client)
    return mock_client
```

- [ ] **Step 2: 添加 _build_candidates_text 测试**

```python
class TestBuildCandidatesText:
    """_build_candidates_text 测试。"""

    def test_build_candidates_text(self, sample_candidates: list[AIMatchCandidate]) -> None:
        """测试候选列表格式化。"""
        result = _build_candidates_text(sample_candidates)
        expected = "1. 开心\n2. 难过\n3. 生气"
        assert result == expected

    def test_build_candidates_text_empty(self) -> None:
        """测试空候选列表。"""
        result = _build_candidates_text([])
        assert result == ""
```

- [ ] **Step 3: 添加 _parse_rank 测试**

```python
class TestParseRank:
    """_parse_rank 测试。"""

    def test_parse_rank_pure_number(self) -> None:
        """测试纯数字解析。"""
        assert _parse_rank("3", max_rank=5) == 3

    def test_parse_rank_number_in_text(self) -> None:
        """测试包含数字的文本解析。"""
        assert _parse_rank("最匹配的是 3", max_rank=5) == 3

    def test_parse_rank_colon_format(self) -> None:
        """测试冒号格式解析。"""
        assert _parse_rank("序号：2", max_rank=5) == 2

    def test_parse_rank_out_of_range(self) -> None:
        """测试越界序号。"""
        assert _parse_rank("15", max_rank=5) is None

    def test_parse_rank_zero(self) -> None:
        """测试零序号。"""
        assert _parse_rank("0", max_rank=5) is None

    def test_parse_rank_invalid_text(self) -> None:
        """测试无效文本。"""
        assert _parse_rank("没有匹配", max_rank=5) is None

    def test_parse_rank_empty_string(self) -> None:
        """测试空字符串。"""
        assert _parse_rank("", max_rank=5) is None

    def test_parse_rank_whitespace(self) -> None:
        """测试空白字符串。"""
        assert _parse_rank("   ", max_rank=5) is None
```

- [ ] **Step 4: 添加 RerankService 测试**

```python
class TestRerankService:
    """RerankService 测试。"""

    @pytest.mark.asyncio
    async def test_rerank_success(
        self,
        mock_openai: AsyncMock,
        sample_candidates: list[AIMatchCandidate],
    ) -> None:
        """测试精排成功返回。"""
        # Mock API 响应
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "2"
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        service = RerankService(api_key="test-key")
        rank = await service.rerank("开心的表情", sample_candidates)

        assert rank == 2

    @pytest.mark.asyncio
    async def test_rerank_api_failure(
        self,
        mock_openai: AsyncMock,
        sample_candidates: list[AIMatchCandidate],
    ) -> None:
        """测试 API 调用失败。"""
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=Exception("API Error")
        )

        service = RerankService(api_key="test-key")

        with pytest.raises(RuntimeError, match="DeepSeek 精排 API 调用失败"):
            await service.rerank("开心的表情", sample_candidates)

    @pytest.mark.asyncio
    async def test_rerank_unparseable_response(
        self,
        mock_openai: AsyncMock,
        sample_candidates: list[AIMatchCandidate],
    ) -> None:
        """测试无法解析的响应返回 0。"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "没有匹配"
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        service = RerankService(api_key="test-key")
        rank = await service.rerank("开心的表情", sample_candidates)

        assert rank == 0

    @pytest.mark.asyncio
    async def test_rerank_empty_candidates(self) -> None:
        """测试空候选列表抛出 ValueError。"""
        service = RerankService(api_key="test-key")

        with pytest.raises(ValueError, match="候选列表不能为空"):
            await service.rerank("开心的表情", [])
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/engine/test_rerank_service.py -v`
Expected: 所有测试通过

---

### Task 3: 更新 API 文档

**Files:**
- Modify: `docs/api/API.md`

- [ ] **Step 1: 在 embedding_service.md 后添加 rerank_service.md 说明**

在 `docs/api/API.md` 的目录结构中添加：

```text
api
├── API.md
└── bot
    ├── engine
    │   ├── ai_matcher.md
    │   ├── embedding_service.md
    │   ├── rerank_service.md    # 新增
    │   ├── index_manager.md
    │   ├── keyword_searcher.md
    │   └── ocr_service.md
    └── logging_config.md
```

- [ ] **Step 2: 添加 RerankService 接口说明**

在 `embedding_service.md` 部分之后添加：

```markdown
### `docs/api/bot/engine/rerank_service.md`

\`\`\`python
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
\`\`\`
```

---

### Task 4: 更新 process.md

**Files:**
- Modify: `docs/process.md`

- [ ] **Step 1: 添加精排服务实现说明**

在 `docs/process.md` 中添加：

```markdown
## 精排服务 (rerank_service.py)

### 实现说明

- 实现 `ai_matcher.RerankProvider` 协议
- 通过 OpenAI 兼容的 chat completions API 调用 DeepSeek 模型
- 支持任何 OpenAI 兼容服务（通过 base_url 和 model 配置）
- 固定使用 PRD 中的 prompt 模板
- 5 秒超时设置

### 环境变量

- `DEEPSEEK_API_KEY`（必填）
- `DEEPSEEK_BASE_URL`（可选，默认 https://api.deepseek.com）
- `DEEPSEEK_MODEL`（可选，默认 deepseek-v4-flash）

### 依赖

- openai SDK
```

---

### Task 5: 运行全量测试并提交

- [ ] **Step 1: 运行全量测试**

Run: `uv run pytest`
Expected: 所有测试通过

- [ ] **Step 2: 语法检查**

Run: `uv run python -m compileall bot tests`
Expected: `SyntaxCheck passed`

- [ ] **Step 3: 提交代码**

```bash
git add bot/engine/rerank_service.py tests/unit/engine/test_rerank_service.py
git commit -m "feat(engine): 实现 RerankService，DeepSeek 精排封装

- 实现 ai_matcher.RerankProvider 协议
- 支持任何 OpenAI 兼容服务（通过 base_url 和 model 配置）
- 固定使用 PRD 中的 prompt 模板
- 5 秒超时设置
- 完整单元测试覆盖"
```

- [ ] **Step 4: 提交文档更新**

```bash
git add docs/api/API.md docs/process.md
git commit -m "docs(api): 添加 RerankService 接口说明"
```
