"""引擎层共用数据类型。"""

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """单条关键词搜索结果。

    Attributes:
        entry_id: 索引 id（int）。
        image_path: memes/ 目录下相对路径。
        text: OCR 文本（无空格）。
        similarity: 相似度分数，0-100。
        speaker: 说话人，可能为 None。
        tags: 标记词列表。
    """

    entry_id: int
    image_path: str
    text: str
    similarity: float
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)
