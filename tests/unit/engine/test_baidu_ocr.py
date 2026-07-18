"""BaiduOcrService 单元测试。"""

import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.engine.baidu_ocr import (
    BAIDU_OCR_ENDPOINTS,
    BAIDU_OCR_TYPES,
    BaiduOcrQuotaError,
    BaiduOcrService,
    BaiduOcrTransientError,
    _extract_legacy_lines,
    _extract_pp_ocrv6_lines,
)


@pytest.fixture
def _no_async_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 tenacity 异步等待置空，避免重试测试耗时。"""

    async def _instant(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


def _response(data: object, status_code: int = 200) -> httpx.Response:
    """构造带 JSON 的 httpx 响应。"""
    return httpx.Response(
        status_code,
        json=data,
        request=httpx.Request("POST", "https://aip.baidubce.com/test"),
    )


def _make_service(
    client: MagicMock,
    *,
    ocr_type: str = "pp_ocrv6",
    text_score: float = 0.9,
) -> BaiduOcrService:
    """使用 mock HTTP client 创建服务。"""
    with patch("bot.engine.baidu_ocr.httpx.AsyncClient", return_value=client):
        return BaiduOcrService(
            api_key="test-api-key",
            secret_key="test-secret-key",
            ocr_type=ocr_type,
            text_score=text_score,
        )


class TestExtractPpOcrV6Lines:
    def test_keeps_order_and_filters_low_score(self) -> None:
        data = {
            "page_result": [
                {
                    "lines": ["第一页", "低分行"],
                    "probability": [0.99, 0.2],
                },
                {"lines": ["第二页"], "probability": [0.95]},
            ]
        }

        assert _extract_pp_ocrv6_lines(data, 0.9) == ["第一页", "第二页"]

    def test_missing_or_invalid_scores_keep_text(self) -> None:
        data = {
            "page_result": [
                {
                    "lines": ["无分数", "错型分数", "数组不足"],
                    "probability": [None, "bad"],
                }
            ]
        }

        assert _extract_pp_ocrv6_lines(data, 0.9) == [
            "无分数",
            "错型分数",
            "数组不足",
        ]

    def test_malformed_data_returns_empty(self) -> None:
        assert _extract_pp_ocrv6_lines({"page_result": "bad"}, 0.9) == []
        assert _extract_pp_ocrv6_lines(None, 0.9) == []


class TestExtractLegacyLines:
    def test_extracts_words_and_filters_low_score(self) -> None:
        data = {
            "words_result": [
                {"words": "保留", "probability": {"average": 0.99}},
                {"words": "过滤", "probability": {"average": 0.2}},
                {"words": "无分数", "location": {"top": 1}},
            ]
        }

        assert _extract_legacy_lines(data, 0.9) == ["保留", "无分数"]

    def test_ignores_malformed_items(self) -> None:
        data = {
            "words_result": [
                None,
                "bad",
                {"words": ""},
                {"words": 123},
                {"words": "有效", "probability": "bad"},
            ]
        }

        assert _extract_legacy_lines(data, 0.9) == ["有效"]


class TestBaiduOcrServiceInit:
    def test_requires_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BAIDU_API_KEY", raising=False)
        monkeypatch.delenv("BAIDU_SECRET_KEY", raising=False)

        with pytest.raises(ValueError, match="BAIDU_API_KEY"):
            BaiduOcrService()

    def test_rejects_invalid_explicit_type(self) -> None:
        with pytest.raises(ValueError, match="不支持的百度 OCR 类型"):
            BaiduOcrService(
                api_key="key",
                secret_key="secret",
                ocr_type="unknown",
            )

    def test_all_seven_types_are_mapped(self) -> None:
        assert BAIDU_OCR_TYPES == (
            "pp_ocrv6",
            "general_basic",
            "general",
            "accurate_basic",
            "accurate",
            "webimage",
            "webimage_loc",
        )
        assert set(BAIDU_OCR_ENDPOINTS) == set(BAIDU_OCR_TYPES)

    def test_pp_ocrv6_uses_documented_compatibility_endpoint(self) -> None:
        assert BAIDU_OCR_ENDPOINTS["pp_ocrv6"] == "/rest/2.0/ocr/v1/pp_ocrv5"


class TestBaiduOcrRequests:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("ocr_type", BAIDU_OCR_TYPES)
    async def test_calls_expected_endpoint_and_returns_normalized_text(
        self,
        tmp_path: Path,
        ocr_type: str,
    ) -> None:
        image = tmp_path / "test.png"
        image.write_bytes(b"image-data")
        client = MagicMock()
        if ocr_type == "pp_ocrv6":
            result_data = {
                "page_result": [
                    {"lines": ["加 班", "心\t累"], "probability": [0.99, 0.99]}
                ]
            }
        else:
            result_data = {
                "words_result": [
                    {"words": "加 班", "probability": {"average": 0.99}},
                    {"words": "心\t累", "probability": {"average": 0.99}},
                ]
            }
        client.post = AsyncMock(
            side_effect=[
                _response({"access_token": "token-1", "expires_in": 3600}),
                _response(result_data),
            ]
        )
        client.aclose = AsyncMock()
        service = _make_service(client, ocr_type=ocr_type)

        result = await service.ocr(str(image))

        assert result == "加,班,心,累"
        token_call, ocr_call = client.post.call_args_list
        assert token_call.args[0].endswith("/oauth/2.0/token")
        assert ocr_call.args[0].endswith(BAIDU_OCR_ENDPOINTS[ocr_type])
        assert ocr_call.kwargs["params"] == {"access_token": "token-1"}
        assert ocr_call.kwargs["data"]["image"] == base64.b64encode(
            b"image-data"
        ).decode("ascii")
        if ocr_type == "pp_ocrv6":
            assert ocr_call.kwargs["data"]["useDocOrientationClassify"] == "false"
            assert ocr_call.kwargs["data"]["useDocUnwarping"] == "false"
            assert ocr_call.kwargs["data"]["useTextlineOrientation"] == "false"
        else:
            assert ocr_call.kwargs["data"]["probability"] == "true"

    @pytest.mark.asyncio
    async def test_reuses_cached_token(self, tmp_path: Path) -> None:
        image = tmp_path / "test.png"
        image.write_bytes(b"x")
        client = MagicMock()
        success = _response({"page_result": [{"lines": ["文字"]}]})
        client.post = AsyncMock(
            side_effect=[
                _response({"access_token": "cached", "expires_in": 3600}),
                success,
                success,
            ]
        )
        client.aclose = AsyncMock()
        service = _make_service(client)

        assert await service.ocr(str(image)) == "文字"
        assert await service.ocr(str(image)) == "文字"
        assert client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_concurrent_token_requests_refresh_once(self) -> None:
        client = MagicMock()
        started = asyncio.Event()
        release = asyncio.Event()

        async def token_post(*args: object, **kwargs: object) -> httpx.Response:
            started.set()
            await release.wait()
            return _response({"access_token": "shared", "expires_in": 3600})

        client.post = AsyncMock(side_effect=token_post)
        client.aclose = AsyncMock()
        service = _make_service(client)

        first = asyncio.create_task(service._get_access_token())
        await started.wait()
        second = asyncio.create_task(service._get_access_token())
        release.set()

        assert await first == "shared"
        assert await second == "shared"
        assert client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_token_error_refreshes_and_retries_once(self, tmp_path: Path) -> None:
        image = tmp_path / "test.png"
        image.write_bytes(b"x")
        client = MagicMock()
        client.post = AsyncMock(
            side_effect=[
                _response({"access_token": "old", "expires_in": 3600}),
                _response({"error_code": 111, "error_msg": "expired"}),
                _response({"access_token": "new", "expires_in": 3600}),
                _response({"page_result": [{"lines": ["成功"]}]}),
            ]
        )
        client.aclose = AsyncMock()
        service = _make_service(client)

        assert await service.ocr(str(image)) == "成功"
        assert client.post.call_count == 4
        assert client.post.call_args_list[-1].kwargs["params"] == {
            "access_token": "new"
        }

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_qps_error_is_retried(self, tmp_path: Path) -> None:
        image = tmp_path / "test.png"
        image.write_bytes(b"x")
        client = MagicMock()
        client.post = AsyncMock(
            side_effect=[
                _response({"access_token": "token", "expires_in": 3600}),
                _response({"error_code": 18, "error_msg": "qps limit"}),
                _response({"error_code": 18, "error_msg": "qps limit"}),
                _response({"page_result": [{"lines": ["成功"]}]}),
            ]
        )
        client.aclose = AsyncMock()
        service = _make_service(client)

        assert await service.ocr(str(image)) == "成功"
        assert client.post.call_count == 4

    @pytest.mark.asyncio
    async def test_quota_error_is_not_retried(self, tmp_path: Path) -> None:
        image = tmp_path / "test.png"
        image.write_bytes(b"x")
        client = MagicMock()
        client.post = AsyncMock(
            side_effect=[
                _response({"access_token": "token", "expires_in": 3600}),
                _response(
                    {"error_code": 17, "error_msg": "daily limit", "log_id": 123}
                ),
            ]
        )
        client.aclose = AsyncMock()
        service = _make_service(client)

        with pytest.raises(BaiduOcrQuotaError) as exc_info:
            await service.ocr(str(image))

        assert exc_info.value.error_code == 17
        assert exc_info.value.log_id == 123
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_async_retry_sleep")
    async def test_http_500_is_retried_and_exhausted(self, tmp_path: Path) -> None:
        image = tmp_path / "test.png"
        image.write_bytes(b"x")
        client = MagicMock()
        client.post = AsyncMock(
            side_effect=[
                _response({"access_token": "token", "expires_in": 3600}),
                _response({"message": "bad"}, 500),
                _response({"message": "bad"}, 500),
                _response({"message": "bad"}, 500),
            ]
        )
        client.aclose = AsyncMock()
        service = _make_service(client)

        with pytest.raises(BaiduOcrTransientError):
            await service.ocr(str(image))

        assert client.post.call_count == 4

    @pytest.mark.asyncio
    async def test_file_not_found_does_not_call_api(self) -> None:
        client = MagicMock()
        client.post = AsyncMock()
        client.aclose = AsyncMock()
        service = _make_service(client)

        with pytest.raises(FileNotFoundError, match="图片文件不存在"):
            await service.ocr("/not/found.png")

        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_closes_http_client(self) -> None:
        client = MagicMock()
        client.post = AsyncMock()
        client.aclose = AsyncMock()
        service = _make_service(client)

        await service.close()

        client.aclose.assert_awaited_once()
