"""百度 OCR 手动联网脚本参数辅助函数测试。"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.test_baidu_ocr import _resolve_modes

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_resolve_modes_defaults_to_pp_ocrv6() -> None:
    assert _resolve_modes(None, run_all=False) == ("pp_ocrv6",)


def test_resolve_modes_returns_selected_type() -> None:
    assert _resolve_modes("webimage", run_all=False) == ("webimage",)


def test_resolve_modes_returns_all_types() -> None:
    assert _resolve_modes(None, run_all=True) == (
        "pp_ocrv6",
        "general_basic",
        "general",
        "accurate_basic",
        "accurate",
        "webimage",
        "webimage_loc",
    )


def test_resolve_modes_rejects_type_with_all() -> None:
    with pytest.raises(ValueError, match="不能同时使用"):
        _resolve_modes("webimage", run_all=True)


def test_script_help_runs_as_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.test_baidu_ocr", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "手动验证百度 OCR" in result.stdout
