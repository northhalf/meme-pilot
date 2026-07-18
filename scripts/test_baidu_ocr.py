"""手动调用百度 OCR 真实 API，验证单个或全部接口模式。

运行方式：
    uv run python -m scripts.test_baidu_ocr image.png
    uv run python -m scripts.test_baidu_ocr image.png --type webimage
    uv run python -m scripts.test_baidu_ocr image.png --all
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from bot.config import PROJECT_ROOT, read_ocr_text_score
from bot.engine.baidu_ocr import BAIDU_OCR_TYPES, BaiduOcrError, BaiduOcrService


def _resolve_modes(ocr_type: str | None, run_all: bool) -> tuple[str, ...]:
    """根据命令行参数确定待验证的 OCR 模式。

    Args:
        ocr_type: 指定的单个模式。
        run_all: 是否验证全部模式。

    Returns:
        按执行顺序排列的 OCR 模式。

    Raises:
        ValueError: 同时指定单模式和全部模式。
    """
    if ocr_type is not None and run_all:
        raise ValueError("--type 与 --all 不能同时使用")
    if run_all:
        return BAIDU_OCR_TYPES
    return (ocr_type or "pp_ocrv6",)


def _build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。

    Returns:
        配置了图片路径、单模式和全部模式参数的解析器。
    """
    parser = argparse.ArgumentParser(description="手动验证百度 OCR 真实 API")
    parser.add_argument("image", type=Path, help="待识别图片路径")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--type",
        choices=BAIDU_OCR_TYPES,
        dest="ocr_type",
        help="指定 OCR 模式，默认 pp_ocrv6",
    )
    group.add_argument(
        "--all",
        action="store_true",
        dest="run_all",
        help="顺序验证全部七种模式（消耗 7 次 OCR 额度）",
    )
    return parser


async def _run_mode(image_path: Path, ocr_type: str) -> bool:
    """调用一个百度 OCR 模式并打印结果。

    Args:
        image_path: 待识别图片路径。
        ocr_type: 百度 OCR 模式。

    Returns:
        调用成功时返回 True。
    """
    service: BaiduOcrService | None = None
    started = time.perf_counter()
    try:
        service = BaiduOcrService(
            ocr_type=ocr_type,
            text_score=read_ocr_text_score(),
        )
        result = await service.ocr(str(image_path))
    except BaiduOcrError as exc:
        elapsed = time.perf_counter() - started
        print(f"[{ocr_type}] 失败（{elapsed:.2f}s）")
        print(
            f"  error_code={exc.error_code}, error_msg={exc.error_msg}, "
            f"log_id={exc.log_id}"
        )
        return False
    except Exception as exc:
        elapsed = time.perf_counter() - started
        print(f"[{ocr_type}] 失败（{elapsed:.2f}s）：{exc}")
        return False
    finally:
        if service is not None:
            await service.close()

    elapsed = time.perf_counter() - started
    print(f"[{ocr_type}] 成功（{elapsed:.2f}s）")
    print(f"  {result or '<未识别到文字>'}")
    return True


async def _async_main(args: argparse.Namespace) -> int:
    """执行联网验证。

    Args:
        args: 命令行解析结果，包含图片路径和模式选择。

    Returns:
        全部模式成功时返回 0，任一模式失败时返回 1，图片不存在时返回 2。
    """
    image_path = args.image
    if not image_path.is_file():
        print(f"图片文件不存在: {image_path}", file=sys.stderr)
        return 2

    modes = _resolve_modes(args.ocr_type, args.run_all)
    if args.run_all:
        print("即将顺序调用全部七种模式，会消耗 7 次 OCR 额度。")

    results = [await _run_mode(image_path, mode) for mode in modes]
    return 0 if all(results) else 1


def main() -> int:
    """加载环境变量并运行命令行入口。

    Returns:
        联网验证进程退出码。
    """
    load_dotenv(PROJECT_ROOT / ".env")
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
