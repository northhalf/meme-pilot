"""全局路径常量。"""

from pathlib import Path

# bot/ 的上级目录即项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

MEMES_DIR = _PROJECT_ROOT / "memes"
