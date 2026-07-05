"""bot.config 全局路径常量与配置读取测试。"""

from pathlib import Path

import pytest

from bot.config import (
    CHROMA_DIR,
    INDEX_DB_PATH,
    MEMES_DIR,
    PROJECT_ROOT,
    _parse_timeout_seconds,
    read_add_command_timeout,
    read_embedding_provider,
    read_ocr_provider,
    read_ocr_text_score,
    read_read_lock_timeout,
    read_session_timeout,
)


def test_index_db_path_under_data() -> None:
    """INDEX_DB_PATH 位于 <项目根>/data/index.db。"""
    assert INDEX_DB_PATH == PROJECT_ROOT / "data" / "index.db"


def test_chroma_dir_under_data() -> None:
    """CHROMA_DIR 位于 <项目根>/data/chroma。"""
    assert CHROMA_DIR == PROJECT_ROOT / "data" / "chroma"


class TestParseTimeoutSeconds:
    def test_empty_returns_default(self) -> None:
        assert _parse_timeout_seconds("", 30) == 30

    def test_number_returns_int(self) -> None:
        assert _parse_timeout_seconds("45", 30) == 45

    def test_zero_or_negative_returns_default(self) -> None:
        assert _parse_timeout_seconds("0", 30) == 30
        assert _parse_timeout_seconds("-1", 30) == 30

    def test_hhmmss_returns_seconds(self) -> None:
        assert _parse_timeout_seconds("00:01:00", 30) == 60
        assert _parse_timeout_seconds("00:00:30", 30) == 30

    def test_invalid_returns_default(self) -> None:
        assert _parse_timeout_seconds("abc", 30) == 30


class TestReadTimeoutEnv:
    def test_read_read_lock_timeout_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("READ_LOCK_TIMEOUT", raising=False)
        assert read_read_lock_timeout() == 30

    def test_read_read_lock_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("READ_LOCK_TIMEOUT", "00:00:45")
        assert read_read_lock_timeout() == 45

    def test_read_add_command_timeout_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ADD_COMMAND_TIMEOUT", raising=False)
        assert read_add_command_timeout() == 60

    def test_read_add_command_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADD_COMMAND_TIMEOUT", "90")
        assert read_add_command_timeout() == 90


class TestReadSessionTimeout:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SESSION_EXPIRE_TIMEOUT", raising=False)
        assert read_session_timeout() == 60

    def test_number_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SESSION_EXPIRE_TIMEOUT", "120")
        assert read_session_timeout() == 120

    def test_hhmmss_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SESSION_EXPIRE_TIMEOUT", "00:02:00")
        assert read_session_timeout() == 120

    def test_invalid_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SESSION_EXPIRE_TIMEOUT", "not_a_timeout")
        assert read_session_timeout() == 60

    def test_zero_or_negative_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SESSION_EXPIRE_TIMEOUT", "0")
        assert read_session_timeout() == 60
        monkeypatch.setenv("SESSION_EXPIRE_TIMEOUT", "-10")
        assert read_session_timeout() == 60


class TestReadOcrProvider:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCR_PROVIDER", raising=False)
        assert read_ocr_provider() == "rapidocr"

    def test_valid_paddle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "paddle")
        assert read_ocr_provider() == "paddle"

    def test_valid_deepseek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "deepseek")
        assert read_ocr_provider() == "deepseek"

    def test_valid_rapidocr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "rapidocr")
        assert read_ocr_provider() == "rapidocr"

    def test_valid_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "Paddle")
        assert read_ocr_provider() == "paddle"
        monkeypatch.setenv("OCR_PROVIDER", "DeepSeek")
        assert read_ocr_provider() == "deepseek"
        monkeypatch.setenv("OCR_PROVIDER", "  RAPIDOCR  ")
        assert read_ocr_provider() == "rapidocr"

    def test_invalid_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_PROVIDER", "tesseract")
        assert read_ocr_provider() == "rapidocr"


class TestReadEmbeddingProvider:
    def test_default_is_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        assert read_embedding_provider() == "openai"

    def test_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "google")
        assert read_embedding_provider() == "google"

    def test_invalid_fallback_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "invalid")
        assert read_embedding_provider() == "openai"


class TestReadOcrTextScore:
    def test_default_is_0_9(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCR_TEXT_SCORE", raising=False)
        assert read_ocr_text_score() == 0.9

    def test_valid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_TEXT_SCORE", "0.75")
        assert read_ocr_text_score() == 0.75

    def test_invalid_fallback_to_0_9(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCR_TEXT_SCORE", "abc")
        assert read_ocr_text_score() == 0.9
