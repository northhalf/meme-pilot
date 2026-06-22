"""索引增删改查模块。

管理 data/index.json 和 data/embeddings.json 两个索引文件，
支持加载校验、查询、原子写入、启动同步、增量刷新和单条增删。
"""

import asyncio
import hashlib
import itertools
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import ujson

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """规范化 OCR 文本。

    去除首尾空白，将连续空白字符（空格、制表符、换行等）
    合并为单个空格。

    Args:
        text: 原始 OCR 文本。

    Returns:
        规范化后的文本。
    """
    return " ".join(text.split())


def compute_text_hash(text: str) -> str:
    """计算规范化文本的 SHA-256 哈希。

    先对文本执行 normalize_text，再计算 SHA-256，
    返回格式为 "sha256:<64位十六进制>"。

    Args:
        text: 待哈希的文本。

    Returns:
        格式为 "sha256:<hex>" 的哈希字符串。
    """
    normalized = normalize_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def dedup_key(text: str) -> str:
    """计算 OCR 文本的去重键。

    去除所有空白字符（含半角/全角空格、制表符、换行等）后的纯文本。
    比 normalize_text 更严格：normalize_text 保留单词间单空格，
    dedup_key 完全去除空格，用于判定「是否完全相同的图片」。

    Args:
        text: 原始 OCR 文本。

    Returns:
        去除所有空白字符后的文本（可能为空字符串）。
    """
    return "".join(text.split())


def is_blank_text(text: str) -> bool:
    """判断 OCR 文本是否为「无文字」。

    去除所有空白后为空即判定无文字。

    Args:
        text: OCR 文本。

    Returns:
        True 表示无文字（需移到 meme_no_text/ 不进索引）。
    """
    return dedup_key(text) == ""


def _resolve_unique_filename(target_dir: Path, filename: str) -> Path:
    """在目标目录下解析不冲突的文件路径，冲突时追加序号。

    与 /add 文件名冲突策略一致：若 filename 已存在，
    在基名后追加 _2、_3... 直到不冲突。

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
    for n in itertools.count(2):
        candidate = target_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    # 理论上不会到达这里（itertools.count 无界），保留防御性代码以满足类型检查
    raise RuntimeError("无法解析不冲突的文件名")


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------


class IndexCorruptedError(Exception):
    """index.json 结构损坏或缺少必要字段时抛出。"""


# ---------------------------------------------------------------------------
# Protocol 接口
# ---------------------------------------------------------------------------


class OcrProvider(Protocol):
    """OCR 服务提供者协议。

    IndexManager 通过此协议调用 OCR 服务，
    由插件层注入具体实现。
    """

    async def ocr(self, image_path: str) -> str:
        """对图片执行 OCR 识别。

        Args:
            image_path: 图片文件路径。

        Returns:
            识别到的文本字符串。
        """
        ...


class EmbeddingProvider(Protocol):
    """Embedding 服务提供者协议。

    IndexManager 通过此协议调用 Embedding API，
    由插件层注入具体实现。
    """

    async def embed(self, text: str) -> list[float]:
        """对文本生成 embedding 向量。

        Args:
            text: 待向量化的文本。

        Returns:
            embedding 向量（浮点数列表）。
        """
        ...


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """sync_with_filesystem() 的返回结果。

    Attributes:
        added: 新增图片数量。
        deleted: 删除图片数量（memes/ 已不存在的图片）。
        deduped: 新图因去重键命中已有条目/其他新图而被删除的数量。
        no_text_moved: OCR 无文字被移到 meme_no_text/ 的数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    deduped: int = 0
    no_text_moved: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class AddResult:
    """add_entry() 的返回结果。

    Attributes:
        entry_id: 分配/复用的索引 ID；无文字移图场景为 None。
        reason: 结果类别，取值：
            "added"   - 正常新增；
            "replaced"- 去重命中已有条目，已复用旧 ID 覆盖；
            "no_text" - OCR 无文字，已移至 meme_no_text/ 不进索引。
        replaced_filename: reason="replaced" 时为被删旧图文件名，否则 None。
        moved_to: reason="no_text" 时为移入 meme_no_text/ 的完整路径，否则 None。
    """

    entry_id: str | None
    reason: str
    replaced_filename: str | None = None
    moved_to: str | None = None


class IndexManager:
    """索引增删改查管理器。

    管理 data/index.json 和 data/embeddings.json 两个索引文件。
    支持加载校验、查询、原子写入、启动同步、增量刷新和单条增删。

    实现 keyword_searcher.IndexProvider 协议，
    可直接注入给 KeywordSearcher。

    Attributes:
        _data_dir: 索引文件目录路径。
        _memes_dir: 表情包图片目录路径。
        _no_text_dir: 无文字图存放目录路径。
        _entries: 内存中的 index entries。
        _dedup_index: 去重键到 entry_id 的反向索引，加速 _find_entry_by_dedup_key。
        _embeddings: 内存中的 embedding 数据。
        _lock: 写操作异步锁。
        _sync_semaphore: 文件系统同步并发上限信号量。
        _sync_concurrency: 当前同步并发上限值。
        index_version: 索引版本号。

    Class Attributes:
        SUPPORTED_EXTENSIONS: 支持的图片扩展名集合。
        DEFAULT_SYNC_CONCURRENCY: 并行同步默认并发上限。
    """

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )

    # 并行同步默认并发上限：避免一次性发起大量请求触发 API 限流
    DEFAULT_SYNC_CONCURRENCY: int = 5

    def __init__(
        self,
        data_dir: str = "data",
        memes_dir: str = "memes",
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        sync_concurrency: int | None = None,
        no_text_dir: str | None = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            data_dir: 索引文件目录路径，默认 "data"。
            memes_dir: 表情包图片目录路径，默认 "memes"。
            ocr_provider: OCR 服务提供者，未注入时无法执行 OCR。
            embedding_provider: Embedding 服务提供者，未注入时无法生成 embedding。
            sync_concurrency: sync_with_filesystem() 并行处理新增图片时的
                最大并发数。None 或非正数时使用 DEFAULT_SYNC_CONCURRENCY。
                建议由插件层从 SYNC_CONCURRENCY 环境变量读取后注入。
            no_text_dir: 无文字图存放目录；None 时取 memes_dir 同级的
                meme_no_text/（即 Path(memes_dir).parent / "meme_no_text"）。
                插件层无需显式传入。
        """
        self._data_dir = Path(data_dir)
        self._memes_dir = Path(memes_dir)
        if no_text_dir is not None:
            self._no_text_dir = Path(no_text_dir)
        else:
            self._no_text_dir = Path(memes_dir).parent / "meme_no_text"
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider

        self._entries: dict[str, dict[str, str]] = {}
        self._dedup_index: dict[str, str] = {}
        self._embeddings: dict[str, dict[str, object]] = {}
        self._locked: bool = False
        self.index_version: int = 1

        # 并发上限：约束 sync_with_filesystem 同时发起的 OCR/embedding 任务数
        concurrency = (
            sync_concurrency
            if isinstance(sync_concurrency, int) and sync_concurrency > 0
            else self.DEFAULT_SYNC_CONCURRENCY
        )
        self._sync_semaphore = asyncio.Semaphore(concurrency)
        self._sync_concurrency = concurrency

    # ------------------------------------------------------------------
    # 加载 / 校验
    # ------------------------------------------------------------------

    def load(self) -> None:
        """加载 index.json 和 embeddings.json 并执行校验。

        加载流程：
        1. 确保 data_dir 存在。
        2. 如果 index.json 不存在，初始化为空 index。
        3. 解析 JSON，调用 validate_index() 校验结构。
        4. 校验每条 entry 含 filename、text、text_hash。
        5. 校验 text_hash 一致性并自动修复不一致项。
        6. 加载 embeddings.json，损坏或不存在时置空 _embeddings
           （由 sync_with_filesystem() 重建阶段全量重建）。

        Raises:
            IndexCorruptedError: index.json 结构损坏或缺少必要字段。
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()
        self._load_embeddings()

    def _load_index(self) -> None:
        """加载并校验 index.json。"""
        index_path = self._data_dir / "index.json"

        if not index_path.exists():
            logger.info("index.json 不存在，初始化为空索引")
            self._entries = {}
            self.index_version = 1
            self._rebuild_dedup_index()
            return

        try:
            raw = index_path.read_text(encoding="utf-8")
            data = ujson.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise IndexCorruptedError(f"index.json 解析失败: {exc}") from exc

        self.validate_index(data)

        self.index_version = data["version"]
        entries = data["entries"]

        for entry_id, entry in entries.items():
            if not isinstance(entry, dict):
                raise IndexCorruptedError(f"entry '{entry_id}' 不是有效的字典对象")
            for field in ("filename", "text", "text_hash"):
                if field not in entry:
                    raise IndexCorruptedError(
                        f"entry '{entry_id}' 缺少必要字段 '{field}'"
                    )
                if not isinstance(entry[field], str):
                    raise IndexCorruptedError(
                        f"entry '{entry_id}' 的 '{field}' 字段必须是字符串"
                    )

        self._entries = entries
        self._rebuild_dedup_index()
        logger.info("index.json 加载成功，共 %d 条记录", len(self._entries))

        inconsistent_ids = self._check_text_hash_consistency()
        if inconsistent_ids:
            logger.warning(
                "检测到 %d 条 text_hash 不一致，已自动修复: %s",
                len(inconsistent_ids),
                inconsistent_ids,
            )

    def _load_embeddings(self) -> None:
        """加载 embeddings.json。"""
        emb_path = self._data_dir / "embeddings.json"

        if not emb_path.exists():
            logger.info("embeddings.json 不存在，置空 _embeddings 待重建")
            self._embeddings = {}
            return

        try:
            raw = emb_path.read_text(encoding="utf-8")
            self._embeddings = ujson.loads(raw)
            logger.info(
                "embeddings.json 加载成功，共 %d 条记录",
                len(self._embeddings),
            )
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("embeddings.json 解析失败，置空 _embeddings 待重建: %s", exc)
            self._embeddings = {}

    @staticmethod
    def validate_index(data: object) -> None:
        """校验 index.json 顶层结构。

        检查是否存在 version（整数）和 entries（字典）字段。

        Args:
            data: 解析后的 JSON 数据。

        Raises:
            IndexCorruptedError: 结构不完整。
        """
        if not isinstance(data, dict):
            raise IndexCorruptedError("index.json 必须是一个 JSON 对象")

        if "version" not in data:
            raise IndexCorruptedError("index.json 缺少 'version' 字段")
        if not isinstance(data["version"], int):
            raise IndexCorruptedError("'version' 字段必须是整数")

        if "entries" not in data:
            raise IndexCorruptedError("index.json 缺少 'entries' 字段")
        if not isinstance(data["entries"], dict):
            raise IndexCorruptedError("'entries' 字段必须是 JSON 对象（字典）")

    # ------------------------------------------------------------------
    # 查询（实现 IndexProvider 协议）
    # ------------------------------------------------------------------

    def get_entries(self) -> dict[str, dict[str, str]]:
        """返回全部索引条目。

        实现 keyword_searcher.IndexProvider 协议。

        Returns:
            key 为索引 id，value 为包含 filename、text、text_hash 的字典。
        """
        return self._entries

    def get_embeddings(self) -> dict[str, dict[str, object]]:
        """返回全部 embedding 条目。

        Returns:
            key 为索引 id，value 为包含 text_hash、embedding 的字典。
        """
        return self._embeddings.copy()

    def get_entry(self, entry_id: str) -> dict[str, str] | None:
        """按 ID 查询单条记录。

        Args:
            entry_id: 索引 ID，如 "1"。

        Returns:
            包含 filename、text、text_hash 的字典，不存在时返回 None。
        """
        return self._entries.get(entry_id)

    def get_by_filename(self, filename: str) -> dict[str, str] | None:
        """按文件名查询单条记录。

        Args:
            filename: 表情包文件名，如 "cat.jpg"。

        Returns:
            包含 filename、text、text_hash 的字典，不存在时返回 None。
        """
        for entry in self._entries.values():
            if entry.get("filename") == filename:
                return entry
        return None

    @property
    def entry_count(self) -> int:
        """当前索引条目总数。"""
        return len(self._entries)

    def _check_text_hash_consistency(self) -> list[str]:
        """校验所有条目的 text_hash 一致性。

        对每条 entry，以当前 text 重新计算 text_hash。
        与存储值不一致时自动更新 hash。
        不一致的条目其 embedding 需要重建。

        Returns:
            text_hash 不一致（已被修复）的 ID 列表。
        """
        inconsistent: list[str] = []
        for entry_id, entry in self._entries.items():
            text = entry.get("text", "")
            stored_hash = entry.get("text_hash", "")
            computed_hash = compute_text_hash(text)
            if stored_hash != computed_hash:
                logger.debug(
                    "text_hash 不一致: id=%s, 旧=%s, 新=%s",
                    entry_id,
                    stored_hash,
                    computed_hash,
                )
                entry["text_hash"] = computed_hash
                inconsistent.append(entry_id)
        return inconsistent

    # ------------------------------------------------------------------
    # ID 管理
    # ------------------------------------------------------------------

    def _find_next_id(self) -> str:
        """查找下一个可用 ID。

        优先复用最小空洞 ID（已删除留下的编号空缺），
        无空洞时返回当前最大 ID + 1。
        ID 格式为数字字符串：'1', '2', '3' ...

        Returns:
            下一个可用的索引 ID。
        """
        if not self._entries:
            return "1"

        existing_ids = {int(eid) for eid in self._entries.keys()}
        max_id = max(existing_ids)

        for i in range(1, max_id + 2):
            if i not in existing_ids:
                return str(i)

        # 理论上不会到达这里，但保留防御性代码
        return str(max_id + 1)

    # ------------------------------------------------------------------
    # 原子写入
    # ------------------------------------------------------------------

    def _atomic_write(self, filepath: Path, data: object) -> None:
        """原子写入 JSON 文件。

        先将数据序列化写入 filepath.tmp，
        写入成功后通过 os.replace() 原子替换正式文件。
        失败时不破坏现有文件。

        Args:
            filepath: 目标文件路径。
            data: 待序列化为 JSON 的数据。
        """
        tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        try:
            tmp_path.write_text(
                ujson.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, filepath)
        except Exception:
            # 清理残留 tmp 文件
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    def save_index(self) -> None:
        """原子写入 index.json。

        将当前内存中的 _entries 和 index_version
        序列化为符合规范的 index.json 格式并原子写入磁盘。
        """
        data: dict[str, object] = {
            "version": self.index_version,
            "entries": self._entries,
        }
        index_path = self._data_dir / "index.json"
        self._atomic_write(index_path, data)
        logger.info("index.json 已保存，共 %d 条记录", len(self._entries))

    def save_embeddings(self) -> None:
        """原子写入 embeddings.json。

        将当前内存中的 _embeddings 序列化并原子写入磁盘。
        """
        emb_path = self._data_dir / "embeddings.json"
        self._atomic_write(emb_path, self._embeddings)
        logger.info("embeddings.json 已保存，共 %d 条记录", len(self._embeddings))

    # ------------------------------------------------------------------
    # 单条增删
    # ------------------------------------------------------------------

    def add_entry(
        self,
        filename: str,
        text: str,
        embedding: list[float],
    ) -> AddResult:
        """添加单条索引记录，处理无文字与 OCR 文本去重。

        三分支：
        1. 无文字（去所有空白后为空）→ 移图到 meme_no_text/，不进索引，
           返回 AddResult(entry_id=None, reason="no_text", moved_to=...)。
        2. 去重键命中已有条目 → 删旧图文件，复用旧 ID 覆盖记录与 embedding，
           返回 AddResult(entry_id=旧id, reason="replaced",
                         replaced_filename=旧文件名)。
        3. 正常新增 → 分配新 ID 写入，返回 AddResult(entry_id, reason="added")。

        Args:
            filename: 表情包文件名。
            text: OCR 识别文本。
            embedding: embedding 向量。

        Returns:
            描述本次结果的 AddResult。
        """
        # 1. 无文字 → 移图，不进索引
        if is_blank_text(text):
            moved_to = self._move_to_no_text(filename)
            logger.info("OCR 无文字，已移至无文字目录，不入索引: filename=%s", filename)
            return AddResult(
                entry_id=None,
                reason="no_text",
                moved_to=moved_to,
            )

        # 2. 去重键命中已有条目 → 删旧图，复用旧 ID 覆盖
        key = dedup_key(text)
        old_id = self._find_entry_by_dedup_key(key)
        if old_id is not None:
            old_filename = self._entries[old_id].get("filename", "")
            old_path = self._memes_dir / old_filename
            old_path.unlink(missing_ok=True)

            # 覆盖前移除旧 key（新 text 的 dedup_key 可能与旧 text 不同）
            self._dedup_index_remove(old_id)
            text_hash = compute_text_hash(text)
            self._entries[old_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._dedup_index_add(old_id)
            self._embeddings[old_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            self.save_index()
            self.save_embeddings()
            logger.info(
                "检测到重复 OCR 文本，已用新图替换: id=%s, 旧=%s, 新=%s",
                old_id,
                old_filename,
                filename,
            )
            return AddResult(
                entry_id=old_id,
                reason="replaced",
                replaced_filename=old_filename,
            )

        # 3. 正常新增
        entry_id = self._find_next_id()
        text_hash = compute_text_hash(text)
        self._entries[entry_id] = {
            "filename": filename,
            "text": text,
            "text_hash": text_hash,
        }
        self._dedup_index_add(entry_id)
        self._embeddings[entry_id] = {
            "text_hash": text_hash,
            "embedding": embedding,
        }
        self.save_index()
        self.save_embeddings()
        logger.info("已添加索引记录: id=%s, filename=%s", entry_id, filename)
        return AddResult(entry_id=entry_id, reason="added")

    def remove_entry(self, entry_id: str) -> bool:
        """删除单条索引记录。

        同时从 _entries 和 _embeddings 中删除，
        并原子写入磁盘。允许产生 ID 空洞。

        Args:
            entry_id: 待删除的索引 ID。

        Returns:
            True 表示删除成功，False 表示 ID 不存在。
        """
        if entry_id not in self._entries:
            logger.warning("尝试删除不存在的记录: id=%s", entry_id)
            return False

        filename = self._entries[entry_id].get("filename", "")
        self._dedup_index_remove(entry_id)
        del self._entries[entry_id]
        self._embeddings.pop(entry_id, None)

        self.save_index()
        self.save_embeddings()

        logger.info("已删除索引记录: id=%s, filename=%s", entry_id, filename)
        return True

    def _rebuild_dedup_index(self) -> None:
        """根据当前 _entries 全量重建去重键反向索引。

        在 _load_index 加载完成后调用一次，建立 dedup_key → entry_id 映射。
        空 key（无文字）条目不会进入 _entries，故不会出现空字符串键。

        Returns:
            无返回值，就地重建 self._dedup_index。
        """
        self._dedup_index = {
            dedup_key(entry.get("text", "")): entry_id
            for entry_id, entry in self._entries.items()
        }

    def _dedup_index_add(self, entry_id: str) -> None:
        """将单条条目的去重键加入反向索引。

        在 _entries[entry_id] 赋值之后调用（需读取其 text 计算 key）。
        空 key 跳过（无文字条目不入索引）。信任去重键唯一不变式，
        直接赋值不做冲突检查。

        Args:
            entry_id: 新增/覆盖条目的 ID。

        Returns:
            无返回值，就地更新 self._dedup_index。
        """
        key = dedup_key(self._entries[entry_id].get("text", ""))
        if key:
            self._dedup_index[key] = entry_id

    def _dedup_index_remove(self, entry_id: str) -> None:
        """从反向索引移除单条条目的去重键。

        在 del _entries[entry_id] 之前调用（需读取其 text 计算 key）。
        空 key 跳过。pop 使用默认值，对空 key 与未建索引条目均安全。

        Args:
            entry_id: 待删除条目的 ID。

        Returns:
            无返回值，就地更新 self._dedup_index。
        """
        key = dedup_key(self._entries[entry_id].get("text", ""))
        if key:
            self._dedup_index.pop(key, None)

    def _find_entry_by_dedup_key(self, key: str) -> str | None:
        """按去重键查找已有条目 ID。

        通过 _dedup_index 反向索引 O(1) 查找，返回该去重键对应的条目 ID。
        正常情况下去重键唯一（add/sync 已保证不引入重复键）。

        Args:
            key: dedup_key 计算结果。

        Returns:
            匹配的条目 ID，无匹配返回 None。
        """
        return self._dedup_index.get(key)

    def _move_to_no_text(self, filename: str) -> str:
        """将无文字图片移动到 meme_no_text/ 目录。

        自动创建 meme_no_text/ 目录；目标同名时追加序号。
        shutil.move 在跨设备时会自动回退为复制+删除。

        Args:
            filename: memes/ 下的源文件名。

        Returns:
            移入 meme_no_text/ 后的完整路径字符串。
        """
        src = self._memes_dir / filename
        self._no_text_dir.mkdir(parents=True, exist_ok=True)
        dst = _resolve_unique_filename(self._no_text_dir, filename)
        shutil.move(str(src), str(dst))
        logger.warning(
            "OCR 未识别到文字，已移至无文字目录: %s -> %s",
            filename,
            dst,
        )
        return str(dst)

    # ------------------------------------------------------------------
    # 锁管理
    # ------------------------------------------------------------------

    def acquire_lock(self) -> bool:
        """非阻塞尝试获取索引更新锁。

        同一时间只允许一个索引写入任务运行。
        如果锁已被占用，返回 False；
        调用方应回复"索引正在更新，请稍后再试"。

        Returns:
            True 表示成功获取锁，False 表示锁已被占用。
        """
        if self._locked:
            return False
        self._locked = True
        logger.debug("索引更新锁已获取")
        return True

    def release_lock(self) -> None:
        """释放索引更新锁。"""
        if self._locked:
            self._locked = False
            logger.debug("索引更新锁已释放")

    # ------------------------------------------------------------------
    # 文件系统同步
    # ------------------------------------------------------------------

    async def sync_with_filesystem(self) -> SyncResult:
        """按文件名同步索引与 memes/ 目录。

        三阶段并行同步：

        1. 删除阶段：扫描 memes/，移除已不存在的图片对应记录。
        2. 重建阶段（embedding 过期修复 + 全量重建）：
           - 对文件仍存在的已有条目，比较 _entries[id].text_hash 与
             _embeddings[id].text_hash（或 _embeddings 缺该 id）；
           - 不一致则用当前 text 重建对应 embedding，覆盖 _embeddings[id]。
           - 该判定同时覆盖两类 PRD 要求：
             a) 用户手动编辑 index.json 的 text 导致 text_hash 不一致
                （load 阶段已按新 text 修复 _entries[id].text_hash）；
             b) embeddings.json 缺失/损坏导致 _embeddings 为空，全部条目
               触发全量重建。
        3. 新增阶段：对新增图片按文件名升序并行 OCR→embed，再串行三分类
           （无文字移图 / 去重删新图 / 正常新增）。去重基于 winner_keys 赢家
           集合增量判定：现有条目与靠前新图赢，靠后/重复新图被删。
           结果收集后按文件名升序统一分配 ID 写入索引（复用最小空洞 id）。

        各阶段内部并行，阶段间串行（先完成全部重建再开始新增）。
        单个图片失败不影响其他图片，记入 failed。全部处理完成后统一原子写入。

        Returns:
            SyncResult(added, deleted, deduped, no_text_moved, failed)。
            重建数量仅在日志中输出，不计入 SyncResult。
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)

        existing_files = self._scan_meme_files()
        filename_to_id = self._build_filename_to_id()

        # 1. 删除已不存在的图片
        deleted_count = self._sync_deletions(existing_files, filename_to_id)

        # 2. 重建过期/缺失的 embedding（仅在删除后剩余的条目中）
        failed: list[str] = []
        rebuild_count = await self._sync_rebuilds(failed)

        # 3. 新增图片并行 OCR + embedding，再三分类
        added_count, deduped_count, no_text_count = await self._sync_additions(
            existing_files, filename_to_id, failed
        )

        # 4. 全部完成后统一原子写入
        self._persist_sync_results(added_count, deleted_count, rebuild_count)

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 去重=%d, 无文字移走=%d, 重建=%d, 失败=%d",
            added_count,
            deleted_count,
            deduped_count,
            no_text_count,
            rebuild_count,
            len(failed),
        )
        return SyncResult(
            added=added_count,
            deleted=deleted_count,
            deduped=deduped_count,
            no_text_moved=no_text_count,
            failed=failed,
        )

    # ------------------------------------------------------------------
    # 文件系统同步 — 私有阶段方法
    # ------------------------------------------------------------------

    def _scan_meme_files(self) -> set[str]:
        """扫描 memes/ 目录，返回受支持扩展名的文件名集合。

        Returns:
            memes/ 下所有 SUPPORTED_EXTENSIONS 内的文件名集合。
        """
        return {
            f.name
            for f in self._memes_dir.iterdir()
            if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        }

    def _build_filename_to_id(self) -> dict[str, str]:
        """根据当前 _entries 构建 filename → id 映射。

        Returns:
            文件名到索引 ID 的映射字典。
        """
        filename_to_id: dict[str, str] = {}
        for eid, entry in self._entries.items():
            fn = entry.get("filename", "")
            if fn:
                filename_to_id[fn] = eid
        return filename_to_id

    def _sync_deletions(
        self,
        existing_files: set[str],
        filename_to_id: dict[str, str],
    ) -> int:
        """删除阶段：移除 memes/ 中已不存在的图片对应索引记录。

        会就地修改 _entries、_embeddings 和传入的 filename_to_id。

        Args:
            existing_files: memes/ 当前文件名集合。
            filename_to_id: filename → id 映射（就地修改，删除时同步移除）。

        Returns:
            本次删除的条目数量。
        """
        deleted_count = 0
        for filename, eid in list(filename_to_id.items()):
            if filename not in existing_files:
                logger.info("图片已删除，移除索引: id=%s, filename=%s", eid, filename)
                # del 前移除：_dedup_index_remove 需读取 _entries[eid].text 计算 key
                self._dedup_index_remove(eid)
                del self._entries[eid]
                self._embeddings.pop(eid, None)
                del filename_to_id[filename]
                deleted_count += 1
        return deleted_count

    async def _sync_rebuilds(self, failed: list[str]) -> int:
        """重建阶段：为过期/缺失 embedding 的已有条目并行重建。

        判定：_embeddings 缺该 id，或其 text_hash 与 _entries[id].text_hash
        不一致 → 用当前 text 调用 embed 重建，不重新 OCR。该判定同时覆盖
        「用户改 text」增量重建与「embeddings.json 损坏/缺失」全量重建。

        Args:
            failed: 失败文件名收集列表（就地追加重建失败的 filename）。

        Returns:
            成功重建的条目数量。
        """
        rebuild_targets: list[str] = [
            eid
            for eid in self._entries
            if eid not in self._embeddings
            or self._embeddings[eid].get("text_hash")
            != self._entries[eid].get("text_hash")
        ]
        if not rebuild_targets:
            return 0

        logger.info(
            "开始并行重建 %d 条过期 embedding，并发上限 %d",
            len(rebuild_targets),
            self._sync_concurrency,
        )

        rebuild_results = await asyncio.gather(
            *(self._rebuild_one_embedding(eid) for eid in rebuild_targets),
            return_exceptions=True,
        )

        rebuild_count = 0
        for eid, result in zip(rebuild_targets, rebuild_results):
            if isinstance(result, BaseException):
                filename = self._entries[eid].get("filename", eid)
                logger.error(
                    "重建 embedding 失败: id=%s, filename=%s, error=%s",
                    eid,
                    filename,
                    result,
                )
                failed.append(filename)
            else:
                rebuild_count += 1
                logger.info(
                    "已重建 embedding: id=%s, filename=%s",
                    eid,
                    self._entries[eid].get("filename", ""),
                )
        return rebuild_count

    async def _rebuild_one_embedding(self, eid: str) -> str:
        """为单条已有索引重建 embedding。

        使用 _entries[eid] 中当前的 text 调用 embed，不重新 OCR（text 已存在）。
        回写 embedding 与最新 text_hash，保持两者绑定一致。

        Args:
            eid: 需重建的索引 ID。

        Returns:
            重建成功的索引 ID。

        Raises:
            RuntimeError: embedding 服务未注入。
            Exception: embedding 调用失败时向上抛出，由调用方捕获。
        """
        text = self._entries[eid].get("text", "")
        if self._embedding_provider is None:
            raise RuntimeError("Embedding 服务未注入")
        async with self._sync_semaphore:
            embedding = await self._embedding_provider.embed(text)
        self._embeddings[eid] = {
            "text_hash": self._entries[eid].get("text_hash", ""),
            "embedding": embedding,
        }
        return eid

    async def _sync_additions(
        self,
        existing_files: set[str],
        filename_to_id: dict[str, str],
        failed: list[str],
    ) -> tuple[int, int, int]:
        """新增阶段：并行 OCR→embed，再按文件名升序串行三分类。

        三分类（基于 winner_keys 赢家集合增量判定）：
        1. 无文字（去所有空白为空）→ _move_to_no_text 移图，no_text_moved++。
        2. 去重键命中 winner_keys（已有条目或本轮更靠前的保留新图）
           → 删新图文件，deduped++。现有条目/靠前图赢。
        3. 正常新增 → 分配 ID 写入，winner_keys 加入该键，added++。

        winner_keys 初始 = 已有条目的去重键（现有条目天然是赢家），
        每张保留的新图将其键加入，从而让后续同键新图被判重。

        Args:
            existing_files: memes/ 当前文件名集合。
            filename_to_id: filename → id 映射（用于判断哪些是新增）。
            failed: 失败文件名收集列表（就地追加新增失败的 filename）。

        Returns:
            (added, deduped, no_text_moved) 三元组。
        """
        new_files = sorted(f for f in existing_files if f not in filename_to_id)
        if not new_files:
            return (0, 0, 0)

        logger.info(
            "开始并行处理 %d 张新增图片，并发上限 %d",
            len(new_files),
            self._sync_concurrency,
        )

        raw_results = await asyncio.gather(
            *(self._process_new_file(fn) for fn in new_files),
            return_exceptions=True,
        )

        # 成功项以 filename 为 key 收集
        success_by_name: dict[str, tuple[str, list[float]]] = {}
        for filename, result in zip(new_files, raw_results):
            if isinstance(result, BaseException):
                logger.error("处理图片失败: filename=%s, error=%s", filename, result)
                failed.append(filename)
            else:
                _, text, embedding = result
                success_by_name[filename] = (text, embedding)

        # 赢家集合：初始 = 已有条目的去重键（现有条目天然是赢家）
        winner_keys: set[str] = {
            dedup_key(entry.get("text", "")) for entry in self._entries.values()
        }

        added = deduped = no_text_moved = 0

        # 按文件名升序串行分类，决定新图互重时的赢家
        for filename in sorted(success_by_name.keys()):
            text, embedding = success_by_name[filename]

            # 1. 无文字 → 移图，不进索引
            if is_blank_text(text):
                self._move_to_no_text(filename)
                no_text_moved += 1
                continue

            # 2. 去重键命中赢家 → 删新图，不进索引
            key = dedup_key(text)
            if key in winner_keys:
                new_path = self._memes_dir / filename
                new_path.unlink(missing_ok=True)
                logger.info("新图与已有索引去重，删除新图: filename=%s", filename)
                deduped += 1
                continue

            # 3. 正常新增
            entry_id = self._find_next_id()
            text_hash = compute_text_hash(text)
            self._entries[entry_id] = {
                "filename": filename,
                "text": text,
                "text_hash": text_hash,
            }
            self._dedup_index_add(entry_id)
            self._embeddings[entry_id] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
            winner_keys.add(key)
            added += 1
            logger.info("新增图片已加入索引: id=%s, filename=%s", entry_id, filename)

        return (added, deduped, no_text_moved)

    async def _process_new_file(self, filename: str) -> tuple[str, str, list[float]]:
        """处理单张新增图片：OCR → Embed。

        受 _sync_semaphore 约束，并发上限内执行。

        Args:
            filename: 表情包文件名。

        Returns:
            (filename, ocr_text, embedding) 三元组。

        Raises:
            RuntimeError: OCR 或 embedding 服务未注入。
            Exception: OCR 或 embedding 调用失败时向上抛出，由调用方捕获。
        """
        image_path = self._memes_dir / filename
        async with self._sync_semaphore:
            if self._ocr_provider is None:
                raise RuntimeError("OCR 服务未注入")
            text = await self._ocr_provider.ocr(str(image_path))

            if self._embedding_provider is None:
                raise RuntimeError("Embedding 服务未注入")
            embedding = await self._embedding_provider.embed(text)

        return filename, text, embedding

    def _persist_sync_results(
        self,
        added_count: int,
        deleted_count: int,
        rebuild_count: int,
    ) -> None:
        """同步完成后统一原子写入磁盘。

        rebuild_count>0 时也需 save_index：用户手改 text 的情况下，
        load 阶段修复的 _entries[id].text_hash 需落盘。
        三类计数均为 0 时不写盘。

        Args:
            added_count: 新增图片数。
            deleted_count: 删除图片数。
            rebuild_count: 重建 embedding 数。
        """
        if added_count > 0 or deleted_count > 0 or rebuild_count > 0:
            self.save_index()
            if added_count > 0 or rebuild_count > 0:
                self.save_embeddings()

    # ------------------------------------------------------------------
    # is_locked 属性
    # ------------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        """索引是否处于锁定状态。"""
        return self._locked
