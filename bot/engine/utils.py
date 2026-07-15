"""engine 包公共工具函数。"""

import itertools
import math
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_SECURE_MOVE_SUPPORTED = (
    os.name == "posix"
    and all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"))
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.unlink in os.supports_dir_fd
    and os.listdir in os.supports_fd
    and os.utime in os.supports_fd
    and hasattr(os, "fchmod")
    and hasattr(os, "fsync")
)

__all__ = ["resolve_unique_filename", "vector_norm"]


@dataclass(frozen=True, slots=True)
class SecureMoveResult:
    """安全文件移动的最终状态。"""

    relative_path: Path
    target_created: bool
    source_removed: bool
    target_dir_created: bool
    target_identity: tuple[int, int, int]


class SecureMoveError(OSError):
    """安全文件移动失败，并携带可供补偿的最终状态。"""

    def __init__(
        self,
        message: str,
        *,
        relative_path: Path | None = None,
        target_created: bool = False,
        source_removed: bool = False,
        target_dir_created: bool = False,
        target_identity: tuple[int, int, int] | None = None,
    ) -> None:
        """初始化安全移动异常。

        Args:
            message: 错误说明。
            relative_path: 已认领目标在根目录下的相对路径。
            target_created: 当前任务是否创建了目标目录项。
            source_removed: 源目录项最终是否已移除。
            target_dir_created: 当前任务是否创建了目标目录。
            target_identity: 当前任务创建目标文件的设备号、inode 与元数据变更时间。
        """
        self.relative_path = relative_path
        self.target_created = target_created
        self.source_removed = source_removed
        self.target_dir_created = target_dir_created
        self.target_identity = target_identity
        super().__init__(message)


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


def _require_secure_move_platform() -> None:
    """校验运行平台具备安全移动所需的 POSIX 原语。

    Raises:
        SecureMoveError: 当前平台不支持基于目录 FD 的安全文件移动。
    """
    if not _SECURE_MOVE_SUPPORTED:
        raise SecureMoveError("当前平台不支持基于目录 FD 的安全文件移动")


def _directory_flags() -> int:
    """返回不跟随符号链接的只读目录打开标志。

    Returns:
        用于 os.open 的只读目录标志（O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC）。
    """
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_directory_chain(root_fd: int, parts: tuple[str, ...]) -> int:
    """从已绑定根目录 FD 安全打开目录链。

    Args:
        root_fd: 已绑定根目录的文件描述符。
        parts: 根目录下的各级子目录名，空序列表示根目录本身。

    Returns:
        链路末端目录的文件描述符。

    Raises:
        OSError: 任一级目录不存在或无法安全打开。
    """
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(part, _directory_flags(), dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    """判断两个 stat 快照是否指向同一文件系统对象。

    Args:
        left: 第一个文件的 stat 快照。
        right: 第二个文件的 stat 快照。

    Returns:
        设备号与 inode 均相同时返回 True，否则 False。
    """
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _stat_identity(stat_result: os.stat_result) -> tuple[int, int, int]:
    """返回 stat 快照的文件身份（设备号、inode、元数据变更纳秒时间）。

    纳秒级 ``st_ctime_ns`` 在文件被删除重建时必然变化，可弥补部分文件系统
    复用 inode 导致仅凭 ``(st_dev, st_ino)`` 无法识别替换的缺陷。

    Args:
        stat_result: 文件的 stat 快照。

    Returns:
        ``(st_dev, st_ino, st_ctime_ns)`` 三元组。
    """
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_ctime_ns)


def _directory_still_bound(
    root_fd: int,
    parts: tuple[str, ...],
    expected: os.stat_result,
) -> bool:
    """重新安全遍历路径并确认仍指向已绑定目录。

    Args:
        root_fd: 已绑定根目录的文件描述符。
        parts: 根目录下的各级子目录名。
        expected: 期望的目录 stat 快照。

    Returns:
        重新打开的末端目录与 expected 指向同一对象时返回 True；路径无法打开时返回 False。
    """
    try:
        current_fd = _open_directory_chain(root_fd, parts)
    except OSError:
        return False
    try:
        return _same_inode(os.fstat(current_fd), expected)
    finally:
        os.close(current_fd)


def _original_entry_removed(
    dir_fd: int,
    filename: str,
    original: os.stat_result,
) -> bool:
    """判断原目录项 inode 是否已被移除或替换。

    Args:
        dir_fd: 目录的文件描述符。
        filename: 目录内的文件名。
        original: 原目录项的 stat 快照。

    Returns:
        目录项不存在或 inode 与 original 不同时返回 True，仍为同一对象时返回 False。
    """
    try:
        current = os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return not _same_inode(current, original)


def get_regular_file_identity(
    root_dir: Path,
    relative_path: Path,
) -> tuple[int, int, int]:
    """通过安全目录 FD 返回根目录内普通文件的设备号、inode 与元数据变更时间。

    Args:
        root_dir: 已验证的文件根目录。
        relative_path: 根目录内规范相对文件路径。

    Returns:
        普通文件的 ``(st_dev, st_ino, st_ctime_ns)``。

    Raises:
        SecureMoveError: 平台不支持、路径不安全、经过符号链接或不是普通文件。
    """
    _require_secure_move_platform()
    parts = relative_path.parts
    if (
        not parts
        or relative_path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise SecureMoveError("文件身份读取只接受根目录内的规范相对路径")
    root_fd = parent_fd = file_fd = -1
    try:
        root_fd = os.open(root_dir, _directory_flags())
        parent_fd = _open_directory_chain(root_fd, parts[:-1])
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SecureMoveError("源图片路径必须是普通文件")
        return _stat_identity(file_stat)
    except SecureMoveError:
        raise
    except OSError as exc:
        raise SecureMoveError(f"无法安全读取源文件身份: {exc}") from exc
    finally:
        for fd in (file_fd, parent_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def _copy_file_data(
    source_fd: int, target_fd: int, source_stat: os.stat_result
) -> None:
    """从已绑定源 FD 复制内容并保留 shutil.move 的核心元数据语义。

    Args:
        source_fd: 已打开的源文件描述符。
        target_fd: 已创建的目标文件描述符。
        source_stat: 源文件的 stat 快照，用于恢复权限与时间戳。

    Raises:
        OSError: 读写、同步权限/时间戳或 fsync 失败。
    """
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(target_fd, view)
            view = view[written:]
    os.fchmod(target_fd, stat.S_IMODE(source_stat.st_mode))
    os.utime(target_fd, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    os.fsync(target_fd)


def _candidate_names(
    filename: str,
    first_suffix: int,
    *,
    allow_suffix: bool,
) -> Iterator[str]:
    """依次生成原名和可选的冲突后缀名。

    Args:
        filename: 期望的文件名。
        first_suffix: 首个冲突后缀数字。
        allow_suffix: 是否在原名之后继续生成带后缀的候选名。

    Yields:
        候选文件名；首项为原名，随后为 ``{stem}_{N}{suffix}`` 形式的候选名。
    """
    yield filename
    if not allow_suffix:
        return
    path = Path(filename)
    for number in itertools.count(first_suffix):
        yield f"{path.stem}_{number}{path.suffix}"


def secure_move_file(
    root_dir: Path,
    source_relative_path: Path,
    target_relative_dir: Path,
    *,
    first_suffix: int = 1,
    target_filename: str | None = None,
    expected_source_identity: tuple[int, int, int] | None = None,
) -> SecureMoveResult:
    """在绑定目录 FD 下原子认领目标并安全移动普通文件。

    本函数面向项目的 Linux/Docker 部署平台。目标通过 ``O_EXCL`` 原子创建，
    不会覆盖并发产生的文件；源和目标目录均通过 ``O_NOFOLLOW`` 的目录 FD 绑定。
    文件内容复制并 fsync 后才删除源目录项，因此同一实现天然支持跨文件系统。

    Args:
        root_dir: 已验证的移动根目录。
        source_relative_path: 根目录下的源文件相对路径。
        target_relative_dir: 根目录下的目标目录相对路径；``Path('.')`` 表示根。
        first_suffix: 文件名冲突后的首个数字后缀。
        target_filename: 指定唯一目标文件名时不再尝试其他后缀，供补偿恢复使用。
        expected_source_identity: 要求源普通文件匹配的设备号、inode 与元数据变更时间。

    Returns:
        安全移动结果及最终状态。

    Raises:
        SecureMoveError: 平台不支持、路径不安全、目录身份变化或文件操作失败。
    """
    _require_secure_move_platform()
    if first_suffix < 1:
        raise ValueError("first_suffix 必须为正整数")
    source_parts = source_relative_path.parts
    target_parts = () if target_relative_dir == Path(".") else target_relative_dir.parts
    if (
        not source_parts
        or source_relative_path.is_absolute()
        or target_relative_dir.is_absolute()
        or any(part in {"", ".", ".."} for part in source_parts)
        or any(part in {"", ".", ".."} for part in target_parts)
    ):
        raise SecureMoveError("安全移动只接受根目录内的规范相对路径")

    root_fd = source_parent_fd = target_fd = source_fd = -1
    target_file_fd = -1
    target_name: str | None = None
    target_created = source_removed = target_dir_created = False
    relative_path: Path | None = None
    target_identity: tuple[int, int, int] | None = None
    source_stat: os.stat_result | None = None
    try:
        root_fd = os.open(root_dir, _directory_flags())
        source_parent_parts = source_parts[:-1]
        source_parent_fd = _open_directory_chain(root_fd, source_parent_parts)
        source_parent_stat = os.fstat(source_parent_fd)
        source_name = source_parts[-1]
        source_fd = os.open(
            source_name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=source_parent_fd,
        )
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise SecureMoveError("源图片路径必须是普通文件")
        source_identity = _stat_identity(source_stat)
        if (
            expected_source_identity is not None
            and source_identity != expected_source_identity
        ):
            raise SecureMoveError("源文件身份与预期移动目标不一致")

        if target_parts:
            try:
                target_fd = _open_directory_chain(root_fd, target_parts)
            except FileNotFoundError:
                if len(target_parts) != 1:
                    raise SecureMoveError(
                        "缺失的目标目录只能创建根目录下的单层目录"
                    ) from None
                os.mkdir(target_parts[0], dir_fd=root_fd)
                target_dir_created = True
                target_fd = _open_directory_chain(root_fd, target_parts)
        else:
            target_fd = os.dup(root_fd)
        target_stat = os.fstat(target_fd)

        existing_casefold = {name.casefold() for name in os.listdir(target_fd)}
        requested_filename = source_name if target_filename is None else target_filename
        for candidate in _candidate_names(
            requested_filename,
            first_suffix,
            allow_suffix=target_filename is None,
        ):
            if candidate.casefold() in existing_casefold:
                continue
            target_name = candidate
            relative_path = target_relative_dir / candidate
            try:
                target_file_fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    stat.S_IMODE(source_stat.st_mode),
                    dir_fd=target_fd,
                )
            except FileExistsError:
                target_name = None
                relative_path = None
                existing_casefold.add(candidate.casefold())
                continue
            target_created = True
            created_stat = os.fstat(target_file_fd)
            target_identity = _stat_identity(created_stat)
            break
        if target_name is None or relative_path is None:
            raise SecureMoveError("无法原子认领不冲突的目标文件名")

        if not _directory_still_bound(root_fd, target_parts, target_stat):
            raise SecureMoveError("目标目录身份在文件提交前发生变化")
        _copy_file_data(source_fd, target_file_fd, source_stat)
        target_file_stat = os.fstat(target_file_fd)
        target_identity = _stat_identity(target_file_stat)
        os.close(target_file_fd)
        target_file_fd = -1

        casefold_matches = [
            name
            for name in os.listdir(target_fd)
            if name.casefold() == target_name.casefold()
        ]
        if casefold_matches != [target_name]:
            raise SecureMoveError("目标目录出现大小写不敏感文件名冲突")
        if not _directory_still_bound(root_fd, target_parts, target_stat):
            raise SecureMoveError("目标目录身份在源删除前发生变化")
        current_target = os.stat(
            target_name,
            dir_fd=target_fd,
            follow_symlinks=False,
        )
        if (
            target_identity is None
            or _stat_identity(current_target) != target_identity
        ):
            raise SecureMoveError("目标文件身份在源删除前发生变化")
        if not _directory_still_bound(
            root_fd,
            source_parent_parts,
            source_parent_stat,
        ):
            raise SecureMoveError("源目录身份在文件提交期间发生变化")
        current_source = os.stat(
            source_name,
            dir_fd=source_parent_fd,
            follow_symlinks=False,
        )
        if not _same_inode(current_source, source_stat):
            raise SecureMoveError("源文件目录项在文件提交期间发生变化")
        os.fsync(target_fd)
        os.unlink(source_name, dir_fd=source_parent_fd)
        source_removed = True
        os.fsync(source_parent_fd)
        return SecureMoveResult(
            relative_path=relative_path,
            target_created=True,
            source_removed=True,
            target_dir_created=target_dir_created,
            target_identity=target_identity,
        )
    except BaseException as exc:
        if target_file_fd >= 0:
            os.close(target_file_fd)
            target_file_fd = -1
        if (
            not source_removed
            and source_parent_fd >= 0
            and source_parts
            and source_stat is not None
        ):
            source_removed = _original_entry_removed(
                source_parent_fd,
                source_parts[-1],
                source_stat,
            )
        if target_created and target_fd >= 0 and target_name is not None:
            try:
                current_target = os.stat(
                    target_name,
                    dir_fd=target_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                target_created = False
            else:
                owns_current_target = (
                    target_identity is not None
                    and _stat_identity(current_target) == target_identity
                )
                if owns_current_target and not source_removed:
                    try:
                        os.unlink(target_name, dir_fd=target_fd)
                        target_created = False
                    except OSError:
                        pass
        if isinstance(exc, SecureMoveError):
            message = str(exc)
        else:
            message = f"安全移动文件失败: {exc}"
        raise SecureMoveError(
            message,
            relative_path=relative_path,
            target_created=target_created,
            source_removed=source_removed,
            target_dir_created=target_dir_created,
            target_identity=target_identity,
        ) from exc
    finally:
        for fd in (target_file_fd, source_fd, target_fd, source_parent_fd, root_fd):
            if fd >= 0:
                os.close(fd)
