"""bot.engine.utils 工具函数测试。"""

import os
from pathlib import Path

import pytest

import bot.engine.utils as engine_utils
from bot.engine.utils import (
    SecureMoveError,
    resolve_unique_filename,
    secure_move_file,
    vector_norm,
)


def test_vector_norm() -> None:
    assert vector_norm([3.0, 4.0]) == 5.0


class TestResolveUniqueFilename:
    def test_no_conflict(self, tmp_path: Path) -> None:
        assert resolve_unique_filename(tmp_path, "a.webp") == tmp_path / "a.webp"

    def test_appends_1(self, tmp_path: Path) -> None:
        (tmp_path / "a.webp").write_bytes(b"x")
        assert resolve_unique_filename(tmp_path, "a.webp") == tmp_path / "a_1.webp"

    def test_appends_2(self, tmp_path: Path) -> None:
        (tmp_path / "a.webp").write_bytes(b"x")
        (tmp_path / "a_1.webp").write_bytes(b"x")
        assert resolve_unique_filename(tmp_path, "a.webp") == tmp_path / "a_2.webp"

    def test_preserves_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "meme_abc.jpg").write_bytes(b"x")
        result = resolve_unique_filename(tmp_path, "meme_abc.jpg")
        assert result == tmp_path / "meme_abc_1.jpg"

    def test_can_start_suffix_from_two(self, tmp_path: Path) -> None:
        (tmp_path / "a.webp").write_bytes(b"existing")

        result = resolve_unique_filename(tmp_path, "a.webp", first_suffix=2)

        assert result == tmp_path / "a_2.webp"


@pytest.mark.skipif(os.name != "posix", reason="安全移动依赖 POSIX dir_fd")
class TestSecureMoveFile:
    def test_unsupported_platform_fails_before_file_operations(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        root.mkdir()
        monkeypatch.setattr(engine_utils, "_SECURE_MOVE_SUPPORTED", False)

        with pytest.raises(SecureMoveError, match="不支持"):
            secure_move_file(root, Path("a.webp"), Path("."))

        assert list(root.iterdir()) == []

    def test_fifo_source_is_rejected_without_blocking(self, tmp_path: Path) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        os.mkfifo(source_dir / "pipe.webp")

        with pytest.raises(SecureMoveError, match="普通文件"):
            secure_move_file(
                root,
                Path("源/pipe.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert (source_dir / "pipe.webp").exists()
        assert list(target_dir.iterdir()) == []

    def test_moves_without_clobber_and_starts_from_two(self, tmp_path: Path) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        (source_dir / "a.webp").write_bytes(b"source")
        (target_dir / "a.webp").write_bytes(b"occupied")

        result = secure_move_file(
            root,
            Path("源/a.webp"),
            Path("目标"),
            first_suffix=2,
        )

        assert result.relative_path == Path("目标/a_2.webp")
        assert result.target_created
        assert result.source_removed
        assert not (source_dir / "a.webp").exists()
        assert (target_dir / "a.webp").read_bytes() == b"occupied"
        assert (target_dir / "a_2.webp").read_bytes() == b"source"

    def test_casefold_conflict_uses_next_suffix(self, tmp_path: Path) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        source = source_dir / "A.webp"
        source.write_bytes(b"source")
        source.chmod(0o640)
        original_mtime_ns = 1_700_000_000_123_456_789
        os.utime(source, ns=(original_mtime_ns, original_mtime_ns))
        (target_dir / "a.webp").write_bytes(b"casefold")

        result = secure_move_file(
            root,
            Path("源/A.webp"),
            Path("目标"),
            first_suffix=2,
        )

        target = target_dir / "A_2.webp"
        assert result.relative_path == Path("目标/A_2.webp")
        assert target.read_bytes() == b"source"
        assert target.stat().st_mode & 0o777 == 0o640
        assert target.stat().st_mtime_ns == original_mtime_ns
        assert (target_dir / "a.webp").read_bytes() == b"casefold"

    def test_restores_to_existing_nested_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "memes"
        source_dir = root / "目标"
        restore_dir = root / "源" / "截图"
        source_dir.mkdir(parents=True)
        restore_dir.mkdir(parents=True)
        (source_dir / "a.webp").write_bytes(b"source")

        result = secure_move_file(
            root,
            Path("目标/a.webp"),
            Path("源/截图"),
            target_filename="a.webp",
        )

        assert result.relative_path == Path("源/截图/a.webp")
        assert (restore_dir / "a.webp").read_bytes() == b"source"
        assert not (source_dir / "a.webp").exists()

    def test_expected_source_identity_rejects_replaced_target(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "目标"
        restore_dir = root / "源"
        source_dir.mkdir(parents=True)
        restore_dir.mkdir()
        source = source_dir / "a.webp"
        source.write_bytes(b"original")
        source_stat = source.stat()
        source.unlink()
        source.write_bytes(b"external")

        with pytest.raises(SecureMoveError, match="身份"):
            secure_move_file(
                root,
                Path("目标/a.webp"),
                Path("源"),
                target_filename="a.webp",
                expected_source_identity=(source_stat.st_dev, source_stat.st_ino),
            )

        assert source.read_bytes() == b"external"
        assert not (restore_dir / "a.webp").exists()

    def test_external_filename_race_uses_next_suffix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        (source_dir / "a.webp").write_bytes(b"source")
        original_open = os.open
        raced = False

        def racing_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal raced
            if (
                not raced
                and path == "a.webp"
                and flags & os.O_EXCL
                and dir_fd is not None
            ):
                raced = True
                (target_dir / "a.webp").write_bytes(b"external")
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", racing_open)

        result = secure_move_file(
            root,
            Path("源/a.webp"),
            Path("目标"),
            first_suffix=2,
        )

        assert raced
        assert result.relative_path == Path("目标/a_2.webp")
        assert (target_dir / "a.webp").read_bytes() == b"external"
        assert (target_dir / "a_2.webp").read_bytes() == b"source"

    def test_target_replaced_before_source_unlink_preserves_source(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        (source_dir / "a.webp").write_bytes(b"source")
        original_bound = engine_utils._directory_still_bound
        calls = 0

        def replace_before_unlink(root_fd, parts, expected):
            nonlocal calls
            calls += 1
            if calls == 2:
                target = target_dir / "a.webp"
                target.unlink()
                target.write_bytes(b"external")
            return original_bound(root_fd, parts, expected)

        monkeypatch.setattr(
            engine_utils,
            "_directory_still_bound",
            replace_before_unlink,
        )

        with pytest.raises(SecureMoveError, match="目标文件身份"):
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert (source_dir / "a.webp").read_bytes() == b"source"
        assert (target_dir / "a.webp").read_bytes() == b"external"

    def test_error_cleanup_does_not_delete_replacement_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        (source_dir / "a.webp").write_bytes(b"source")
        original_copy = engine_utils._copy_file_data

        def replace_after_copy(source_fd, target_fd, source_stat):
            original_copy(source_fd, target_fd, source_stat)
            target = target_dir / "a.webp"
            target.unlink()
            target.write_bytes(b"external")
            raise OSError("after replacement")

        monkeypatch.setattr(engine_utils, "_copy_file_data", replace_after_copy)

        with pytest.raises(SecureMoveError) as exc_info:
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert exc_info.value.target_created
        assert (source_dir / "a.webp").read_bytes() == b"source"
        assert (target_dir / "a.webp").read_bytes() == b"external"

    def test_source_dir_fsync_failure_after_unlink_preserves_target_when_name_recreated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        source = source_dir / "a.webp"
        source.write_bytes(b"original")
        original_unlink = os.unlink
        original_fsync = os.fsync
        source_parent_identity = (source_dir.stat().st_dev, source_dir.stat().st_ino)

        def unlink_then_recreate(path, *, dir_fd=None):
            original_unlink(path, dir_fd=dir_fd)
            if path == "a.webp" and dir_fd is not None:
                source.write_bytes(b"competitor")

        def fail_source_dir_fsync(fd: int) -> None:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) == source_parent_identity:
                raise OSError("source dir fsync failed")
            original_fsync(fd)

        monkeypatch.setattr(os, "unlink", unlink_then_recreate)
        monkeypatch.setattr(os, "fsync", fail_source_dir_fsync)

        with pytest.raises(SecureMoveError) as exc_info:
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert exc_info.value.source_removed
        assert exc_info.value.target_created
        assert source.read_bytes() == b"competitor"
        assert (target_dir / "a.webp").read_bytes() == b"original"

    def test_unlink_commit_then_raise_with_recreated_name_preserves_target(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        source = source_dir / "a.webp"
        source.write_bytes(b"original")
        original_unlink = os.unlink

        def unlink_recreate_then_raise(path, *, dir_fd=None):
            original_unlink(path, dir_fd=dir_fd)
            if path == "a.webp" and dir_fd is not None:
                source.write_bytes(b"competitor")
                raise OSError("unlink committed then failed")

        monkeypatch.setattr(os, "unlink", unlink_recreate_then_raise)

        with pytest.raises(SecureMoveError) as exc_info:
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert exc_info.value.source_removed
        assert exc_info.value.target_created
        assert source.read_bytes() == b"competitor"
        assert (target_dir / "a.webp").read_bytes() == b"original"

    def test_helper_never_calls_rmdir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        source_dir.mkdir(parents=True)
        (source_dir / "a.webp").write_bytes(b"source")

        def reject_rmdir(*args: object, **kwargs: object) -> None:
            raise AssertionError("secure move 不得删除目录")

        monkeypatch.setattr(os, "rmdir", reject_rmdir)

        result = secure_move_file(
            root,
            Path("源/a.webp"),
            Path("目标"),
            first_suffix=2,
        )

        assert result.relative_path == Path("目标/a.webp")
        assert (root / "目标/a.webp").read_bytes() == b"source"

    def test_copy_failure_keeps_new_empty_target_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        source_dir.mkdir(parents=True)
        (source_dir / "a.webp").write_bytes(b"source")

        def fail_copy(*args: object) -> None:
            raise OSError("copy failed")

        monkeypatch.setattr(engine_utils, "_copy_file_data", fail_copy)

        with pytest.raises(SecureMoveError) as exc_info:
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert not exc_info.value.target_created
        assert exc_info.value.target_dir_created
        assert (source_dir / "a.webp").read_bytes() == b"source"
        assert (root / "目标").is_dir()
        assert list((root / "目标").iterdir()) == []

    def test_failure_does_not_remove_competing_replacement_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        detached = root / "目标-old"
        source_dir.mkdir(parents=True)
        (source_dir / "a.webp").write_bytes(b"source")
        original_copy = engine_utils._copy_file_data

        def replace_directory_then_fail(source_fd, target_fd, source_stat):
            original_copy(source_fd, target_fd, source_stat)
            (target_dir / "a.webp").unlink()
            target_dir.rename(detached)
            target_dir.mkdir()
            (target_dir / "marker").write_bytes(b"competitor")
            raise OSError("directory replaced")

        monkeypatch.setattr(
            engine_utils,
            "_copy_file_data",
            replace_directory_then_fail,
        )

        with pytest.raises(SecureMoveError):
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert (source_dir / "a.webp").read_bytes() == b"source"
        assert (target_dir / "marker").read_bytes() == b"competitor"
        assert target_dir.is_dir()

    def test_replaced_target_directory_fails_without_writing_outside(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "memes"
        source_dir = root / "源"
        target_dir = root / "目标"
        outside = tmp_path / "outside"
        source_dir.mkdir(parents=True)
        target_dir.mkdir()
        outside.mkdir()
        (source_dir / "a.webp").write_bytes(b"source")
        original_open = os.open
        replaced = False

        def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal replaced
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            if not replaced and path == "目标" and flags & os.O_DIRECTORY:
                replaced = True
                target_dir.rmdir()
                target_dir.symlink_to(outside, target_is_directory=True)
            return fd

        monkeypatch.setattr(os, "open", replacing_open)

        with pytest.raises(SecureMoveError):
            secure_move_file(
                root,
                Path("源/a.webp"),
                Path("目标"),
                first_suffix=2,
            )

        assert replaced
        assert (source_dir / "a.webp").read_bytes() == b"source"
        assert not (outside / "a.webp").exists()
        assert not (outside / "a_2.webp").exists()
