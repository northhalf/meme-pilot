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

# 精排系统 prompt
_SYSTEM_PROMPT = "你是一个表情包匹配助手。只返回数字序号，不要输出任何其他内容。"

# 精排用户 prompt 模板：{description} 为用户描述，{candidates} 为候选列表
_USER_PROMPT_TEMPLATE = """用户描述：{description}

以下是候选表情包的文字内容：
{candidates}

请选出最匹配的 1 个，返回序号即可。"""


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
            )
        except Exception as exc:
            raise RuntimeError(f"DeepSeek 精排 API 调用失败: {exc}") from exc

        raw = response.choices[0].message.content or ""
        rank = _parse_rank(raw, max_rank=len(candidates))

        if rank is None:
            logger.warning("DeepSeek 精排返回无法解析: raw=%r，返回 0 放弃精排", raw)
            return 0

        logger.debug("DeepSeek 精排完成: rank=%d", rank)
        return rank
