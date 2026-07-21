"""百度智能云 OCR REST API 服务模块。"""

import asyncio
import base64
import logging
import os
import time
from pathlib import Path
from typing import Any, cast

import httpx

from bot.config import read_int_env
from bot.log_context import timed

from .retry_config import api_retry

logger = logging.getLogger(__name__)

BAIDU_API_BASE_URL = "https://aip.baidubce.com"
BAIDU_TOKEN_PATH = "/oauth/2.0/token"
BAIDU_OCR_TYPES: tuple[str, ...] = (
    "pp_ocrv6",
    "general_basic",
    "general",
    "accurate_basic",
    "accurate",
    "webimage",
    "webimage_loc",
)
BAIDU_OCR_ENDPOINTS: dict[str, str] = {
    # 百度 PP-OCRv6 产品沿用 pp_ocrv5 API 路径
    "pp_ocrv6": "/rest/2.0/ocr/v1/pp_ocrv5",
    "general_basic": "/rest/2.0/ocr/v1/general_basic",
    "general": "/rest/2.0/ocr/v1/general",
    "accurate_basic": "/rest/2.0/ocr/v1/accurate_basic",
    "accurate": "/rest/2.0/ocr/v1/accurate",
    "webimage": "/rest/2.0/ocr/v1/webimage",
    "webimage_loc": "/rest/2.0/ocr/v1/webimage_loc",
}

_TOKEN_ERROR_CODES = frozenset({110, 111})
_QUOTA_ERROR_CODES = frozenset({17, 19})
_TRANSIENT_ERROR_CODES = frozenset({18})
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0


def _is_number(value: object) -> bool:
    """判断值是否为非布尔数值。

    Args:
        value: 待判断的任意值。

    Returns:
        值为整数或浮点数且不是布尔值时返回 True，否则返回 False。
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _extract_pp_ocrv6_lines(data: object, text_score: float) -> list[str]:
    """从百度 PP-OCRv6 响应中提取文本行。

    Args:
        data: 百度 OCR JSON 响应。
        text_score: 文本行置信度阈值。

    Returns:
        保持页和行顺序的文本列表。
    """
    if not isinstance(data, dict):
        return []
    pages = data.get("page_result")
    if not isinstance(pages, list):
        return []

    result: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        lines = page.get("lines")
        if not isinstance(lines, list):
            continue
        probabilities = page.get("probability")
        scores = probabilities if isinstance(probabilities, list) else []
        for index, line in enumerate(lines):
            if not isinstance(line, str) or not line:
                continue
            if index < len(scores):
                score = scores[index]
                if _is_number(score) and cast(float, score) < text_score:
                    continue
            result.append(line)
    return result


def _extract_legacy_lines(data: object, text_score: float) -> list[str]:
    """从百度传统 OCR 响应中提取文本行。

    Args:
        data: 百度 OCR JSON 响应。
        text_score: 文本行置信度阈值。

    Returns:
        保持 API 返回顺序的文本列表。
    """
    if not isinstance(data, dict):
        return []
    words_result = data.get("words_result")
    if not isinstance(words_result, list):
        return []

    result: list[str] = []
    for item in words_result:
        if not isinstance(item, dict):
            continue
        words = item.get("words")
        if not isinstance(words, str) or not words:
            continue
        probability = item.get("probability")
        if isinstance(probability, dict):
            average = probability.get("average")
            if _is_number(average) and cast(float, average) < text_score:
                continue
        result.append(words)
    return result


class BaiduOcrError(RuntimeError):
    """百度 OCR API 基础异常。"""

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        error_msg: str | None = None,
        log_id: int | str | None = None,
        ocr_type: str | None = None,
    ) -> None:
        """初始化百度 OCR API 异常。

        Args:
            message: 面向调用方的异常描述。
            error_code: 百度 API 错误码。
            error_msg: 百度 API 原始错误消息。
            log_id: 百度请求日志 ID。
            ocr_type: 发生错误的 OCR 模式。
        """
        super().__init__(message)
        self.error_code = error_code
        self.error_msg = error_msg
        self.log_id = log_id
        self.ocr_type = ocr_type


class BaiduOcrAuthError(BaiduOcrError):
    """百度 OCR 鉴权失败。"""


class BaiduOcrInvalidRequestError(BaiduOcrError):
    """百度 OCR 请求或响应无效。"""


class BaiduOcrQuotaError(BaiduOcrError):
    """百度 OCR 调用额度耗尽。"""


class BaiduOcrTransientError(BaiduOcrError):
    """百度 OCR 瞬时故障，可由统一重试策略处理。"""


class BaiduOcrService:
    """百度智能云 OCR REST API 服务。"""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        ocr_type: str | None = None,
        text_score: float = 0.9,
        concurrency: int | None = None,
    ) -> None:
        """初始化百度 OCR 服务。

        Args:
            api_key: 百度 API Key，默认读取 ``BAIDU_API_KEY``。
            secret_key: 百度 Secret Key，默认读取 ``BAIDU_SECRET_KEY``。
            ocr_type: OCR 接口类型，默认读取 ``BAIDU_OCR_TYPE``。
            text_score: 文本行置信度阈值。
            concurrency: 并发上限，默认读取 ``OCR_CONCURRENCY``，回退为 5。

        Raises:
            ValueError: 凭证为空或显式 OCR 类型非法。
        """
        self._api_key = (
            api_key if api_key is not None else os.environ.get("BAIDU_API_KEY", "")
        )
        self._secret_key = (
            secret_key
            if secret_key is not None
            else os.environ.get("BAIDU_SECRET_KEY", "")
        )
        if not self._api_key:
            raise ValueError("必须提供 BAIDU_API_KEY")
        if not self._secret_key:
            raise ValueError("必须提供 BAIDU_SECRET_KEY")

        if ocr_type is None:
            from bot.config import read_baidu_ocr_type

            selected_type = read_baidu_ocr_type()
        else:
            selected_type = ocr_type.strip().lower()
            if selected_type not in BAIDU_OCR_ENDPOINTS:
                raise ValueError(f"不支持的百度 OCR 类型: {ocr_type}")
        self._ocr_type = selected_type
        self._text_score = text_score
        self._client = httpx.AsyncClient(timeout=60.0)
        self._semaphore = asyncio.Semaphore(
            concurrency
            if concurrency is not None
            else read_int_env("OCR_CONCURRENCY") or 5
        )
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    def _token_is_valid(self) -> bool:
        """判断当前缓存 token 是否仍可使用。

        Returns:
            token 存在且尚未到达提前刷新时间时返回 True，否则返回 False。
        """
        return bool(self._access_token) and time.monotonic() < self._token_expires_at

    async def _post_json(
        self,
        url: str,
        *,
        params: dict[str, str],
        data: dict[str, str] | None = None,
        auth_request: bool = False,
    ) -> dict[str, Any]:
        """发送脱敏的 POST 请求并解析 JSON。

        Args:
            url: 请求地址。
            params: URL 查询参数。
            data: 可选的表单请求体。
            auth_request: 是否为 access token 鉴权请求。

        Returns:
            解析后的 JSON 字典。

        Raises:
            asyncio.CancelledError: 当前协程被取消。
            BaiduOcrTransientError: 网络异常、限流或服务端暂时不可用。
            BaiduOcrAuthError: 鉴权请求失败或返回结构无效。
            BaiduOcrInvalidRequestError: OCR 请求失败或返回结构无效。
        """
        try:
            response = await self._client.post(
                url,
                params=params,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
        except asyncio.CancelledError:
            raise
        except httpx.RequestError as exc:
            raise BaiduOcrTransientError("百度 OCR 网络请求失败") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise BaiduOcrTransientError(
                f"百度 OCR 服务暂时不可用: HTTP {response.status_code}",
                ocr_type=self._ocr_type,
            )
        if response.status_code >= 400:
            error_type = (
                BaiduOcrAuthError if auth_request else BaiduOcrInvalidRequestError
            )
            raise error_type(
                f"百度 OCR 请求失败: HTTP {response.status_code}",
                ocr_type=self._ocr_type,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            error_type = (
                BaiduOcrAuthError if auth_request else BaiduOcrInvalidRequestError
            )
            raise error_type(
                "百度 OCR 返回了无效 JSON", ocr_type=self._ocr_type
            ) from exc
        if not isinstance(payload, dict):
            error_type = (
                BaiduOcrAuthError if auth_request else BaiduOcrInvalidRequestError
            )
            raise error_type("百度 OCR 返回结构无效", ocr_type=self._ocr_type)
        return cast(dict[str, Any], payload)

    async def _refresh_access_token(self, stale_token: str | None = None) -> str:
        """在锁内刷新 access token，避免并发重复请求。

        Args:
            stale_token: 触发刷新的旧 token；提供时若其他协程已完成刷新，
                则直接复用新 token。

        Returns:
            有效的 access token。

        Raises:
            BaiduOcrAuthError: token 响应缺少必要字段或有效期非法。
            BaiduOcrTransientError: token 请求遇到瞬时网络或服务端故障。
        """
        async with self._token_lock:
            if stale_token is None:
                if self._token_is_valid():
                    assert self._access_token is not None
                    return self._access_token
            elif (
                self._access_token is not None
                and self._access_token != stale_token
                and self._token_is_valid()
            ):
                return self._access_token

            payload = await self._post_json(
                f"{BAIDU_API_BASE_URL}{BAIDU_TOKEN_PATH}",
                params={
                    "grant_type": "client_credentials",
                    "client_id": self._api_key,
                    "client_secret": self._secret_key,
                },
                auth_request=True,
            )
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if not isinstance(token, str) or not token:
                description = payload.get("error_description")
                message = (
                    description
                    if isinstance(description, str) and description
                    else "百度 access_token 响应缺少 access_token"
                )
                raise BaiduOcrAuthError(message, ocr_type=self._ocr_type)
            if not _is_number(expires_in) or cast(float, expires_in) <= 0:
                raise BaiduOcrAuthError(
                    "百度 access_token 响应中的 expires_in 无效",
                    ocr_type=self._ocr_type,
                )

            self._access_token = token
            lifetime = float(cast(int | float, expires_in))
            self._token_expires_at = time.monotonic() + max(
                0.0, lifetime - _TOKEN_REFRESH_MARGIN_SECONDS
            )
            return token

    async def _get_access_token(self) -> str:
        """返回有效的缓存 token，必要时自动刷新。

        Returns:
            当前可用的 access token。

        Raises:
            BaiduOcrAuthError: 无法获取有效 token。
            BaiduOcrTransientError: token 请求遇到瞬时网络或服务端故障。
        """
        if self._token_is_valid():
            assert self._access_token is not None
            return self._access_token
        return await self._refresh_access_token()

    @staticmethod
    def _error_code(payload: dict[str, Any]) -> int | None:
        """读取百度响应错误码，兼容数字字符串。

        Args:
            payload: 百度 API JSON 响应。

        Returns:
            可解析的整数错误码；字段缺失或格式非法时返回 None。
        """
        raw = payload.get("error_code")
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return None
        return None

    def _raise_api_error(self, payload: dict[str, Any]) -> None:
        """按百度错误码抛出可分类异常。

        Args:
            payload: 百度 OCR JSON 响应。

        Raises:
            BaiduOcrTransientError: 返回 QPS 限制等瞬时错误。
            BaiduOcrQuotaError: 每日或总调用额度耗尽。
            BaiduOcrAuthError: access token 无效或过期。
            BaiduOcrInvalidRequestError: 其他请求或业务错误。
        """
        error_code = self._error_code(payload)
        if error_code is None or error_code == 0:
            return
        raw_message = payload.get("error_msg")
        error_msg = raw_message if isinstance(raw_message, str) else "未知错误"
        log_id = payload.get("log_id")
        details = {
            "error_code": error_code,
            "error_msg": error_msg,
            "log_id": log_id if isinstance(log_id, (int, str)) else None,
            "ocr_type": self._ocr_type,
        }
        message = f"百度 OCR 调用失败: [{error_code}] {error_msg}"
        if error_code in _TRANSIENT_ERROR_CODES:
            raise BaiduOcrTransientError(message, **details)
        if error_code in _QUOTA_ERROR_CODES:
            raise BaiduOcrQuotaError(message, **details)
        if error_code in _TOKEN_ERROR_CODES:
            raise BaiduOcrAuthError(message, **details)
        raise BaiduOcrInvalidRequestError(message, **details)

    async def _request_ocr(self, token: str, image_data: str) -> dict[str, Any]:
        """调用当前模式的 OCR endpoint。

        Args:
            token: 百度 access token。
            image_data: Base64 编码后的图片内容。

        Returns:
            百度 OCR JSON 响应。

        Raises:
            BaiduOcrTransientError: 请求遇到瞬时网络或服务端故障。
            BaiduOcrInvalidRequestError: HTTP 请求失败或响应结构无效。
        """
        form = {"image": image_data}
        if self._ocr_type == "pp_ocrv6":
            form.update(
                {
                    "useDocOrientationClassify": "false",
                    "useDocUnwarping": "false",
                    "useTextlineOrientation": "false",
                }
            )
        else:
            form["probability"] = "true"
        return await self._post_json(
            f"{BAIDU_API_BASE_URL}{BAIDU_OCR_ENDPOINTS[self._ocr_type]}",
            params={"access_token": token},
            data=form,
        )

    @api_retry(extra_exceptions=(BaiduOcrTransientError,))
    @timed(logger, "百度 OCR")
    async def ocr(self, image_path: str) -> str:
        """对图片执行百度 OCR 识别。

        Args:
            image_path: 图片文件路径。

        Returns:
            按空白分割后以中文逗号拼接的识别文本。

        Raises:
            FileNotFoundError: 图片不存在。
            BaiduOcrError: 百度 OCR 请求失败。
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        image_data = base64.b64encode(path.read_bytes()).decode("ascii")

        async with self._semaphore:
            token = await self._get_access_token()
            payload = await self._request_ocr(token, image_data)
            if self._error_code(payload) in _TOKEN_ERROR_CODES:
                token = await self._refresh_access_token(stale_token=token)
                payload = await self._request_ocr(token, image_data)
            self._raise_api_error(payload)

            if self._ocr_type == "pp_ocrv6":
                lines = _extract_pp_ocrv6_lines(payload, self._text_score)
            else:
                lines = _extract_legacy_lines(payload, self._text_score)
            full_text = "，".join(" ".join(lines).split())
            logger.debug(
                "百度 OCR 完成: type=%s, file=%s, chars=%d",
                self._ocr_type,
                path.name,
                len(full_text),
            )
            return full_text

    async def close(self) -> None:
        """关闭百度 OCR HTTP 客户端并释放连接池资源。"""
        await self._client.aclose()
        logger.debug("BaiduOcrService HTTP 会话已关闭")


def create_baidu_ocr_service() -> BaiduOcrService:
    """从环境变量创建百度 OCR 服务。

    Returns:
        使用当前百度凭证、OCR 类型、置信度和并发配置创建的服务实例。

    Raises:
        ValueError: 百度 API Key 或 Secret Key 未配置。
    """
    from bot.config import read_baidu_ocr_type, read_ocr_text_score

    return BaiduOcrService(
        ocr_type=read_baidu_ocr_type(),
        text_score=read_ocr_text_score(),
        concurrency=read_int_env("OCR_CONCURRENCY"),
    )
