"""engine 包公共工具函数。"""

import itertools
import math
import os
import stat
from pathlib import Path

__all__ = ["resolve_unique_filename", "vector_norm"]


def vector_norm(vector: list[float]) -> float:
    """计算向量 L2 范数。

    Args:
        vector: 浮点数向量。

    Returns:
        向量的 L2 范数（欧几里得长度）。
    """
    return math.sqrt(sum(value * value for value in vector))


def resolve_unique_filename(
    target_dir: Path,
    filename: str,
    *,
    first_suffix: int = 1,
) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    Args:
        target_dir: 目标目录路径。
        filename: 期望文件名。
        first_suffix: 首个冲突后缀，默认从 1 开始。

    Returns:
        目标目录下不冲突的完整路径。
    """
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for n in itertools.count(first_suffix):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法解析不冲突的文件名")


def get_regular_file_identity(
    root_dir: Path,
    relative_path: Path,
) -> tuple[int, int, int]:
    """返回根目录内普通文件的设备号、inode 与元数据变更时间。

    纳秒级 ``st_ctime_ns`` 在文件被删除重建时必然变化，可弥补部分文件系统
    复用 inode 导致仅凭 ``(st_dev, st_ino)`` 无法识别替换的缺陷。

    Args:
        root_dir: 已验证的文件根目录。
        relative_path: 根目录内规范相对文件路径。

    Returns:
        普通文件的 ``(st_dev, st_ino, st_ctime_ns)``。

    Raises:
        OSError: 路径不规范、不存在、是符号链接或不是普通文件。
    """
    parts = relative_path.parts
    if (
        not parts
        or relative_path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise OSError("文件身份读取只接受根目录内的规范相对路径")
    path_stat = os.lstat(root_dir / relative_path)
    if not stat.S_ISREG(path_stat.st_mode):
        raise OSError("源图片路径必须是普通文件")
    return (path_stat.st_dev, path_stat.st_ino, path_stat.st_ctime_ns)
