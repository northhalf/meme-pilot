"""bot.engine.utils 工具函数测试。"""

from pathlib import Path

from bot.engine.utils import resolve_unique_filename, vector_norm


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
