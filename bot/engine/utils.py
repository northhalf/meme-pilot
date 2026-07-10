"""engine 包公共工具函数。"""

import itertools
import math
from pathlib import Path

__all__ = ["vector_norm", "resolve_unique_filename"]


def vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))


def resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。

    Returns:
        目标目录下不冲突的完整路径。
    """
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for n in itertools.count(1):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")
