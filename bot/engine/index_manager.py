"""索引增删改查模块。

管理 data/index.json 和 data/embeddings.json 两个索引文件，
支持加载校验、查询、原子写入、启动同步、增量刷新和单条增删。
"""

from __future__ import annotations

import hashlib
import logging
import os
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


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------


class IndexCorruptedError(Exception):
    """index.json 结构损坏或缺少必要字段时抛出。"""


class IndexLockedError(Exception):
    """索引更新锁被占用时尝试写入操作抛出。"""


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
        deleted: 删除图片数量。
        failed: 处理失败的文件名列表。
    """

    added: int = 0
    deleted: int = 0
    failed: list[str] = field(default_factory=list)


class IndexManager:
    """索引增删改查管理器。

    管理 data/index.json 和 data/embeddings.json 两个索引文件。
    支持加载校验、查询、原子写入、启动同步、增量刷新和单条增删。

    实现 keyword_searcher.IndexProvider 协议，
    可直接注入给 KeywordSearcher。

    Attributes:
        _data_dir: 索引文件目录路径。
        _memes_dir: 表情包图片目录路径。
        _entries: 内存中的 index entries。
        _embeddings: 内存中的 embedding 数据。
        _embeddings_stale: embedding 是否需要重建。
        _lock: 写操作异步锁。
        index_version: 索引版本号。
    """

    def __init__(
        self,
        data_dir: str = "data",
        memes_dir: str = "memes",
        ocr_provider: OcrProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        """初始化 IndexManager。

        Args:
            data_dir: 索引文件目录路径，默认 "data"。
            memes_dir: 表情包图片目录路径，默认 "memes"。
            ocr_provider: OCR 服务提供者，未注入时无法执行 OCR。
            embedding_provider: Embedding 服务提供者，未注入时无法生成 embedding。
        """
        import asyncio

        self._data_dir = Path(data_dir)
        self._memes_dir = Path(memes_dir)
        self._ocr_provider = ocr_provider
        self._embedding_provider = embedding_provider

        self._entries: dict[str, dict[str, str]] = {}
        self._embeddings: dict[str, dict[str, object]] = {}
        self._embeddings_stale: bool = False
        self._lock = asyncio.Lock()
        self._locked: bool = False
        self.index_version: int = 1

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
        6. 加载 embeddings.json，损坏或不存在时标记 _embeddings_stale。

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
            return

        try:
            raw = index_path.read_text(encoding="utf-8")
            data = ujson.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise IndexCorruptedError(
                f"index.json 解析失败: {exc}"
            ) from exc

        self.validate_index(data)

        self.index_version = data["version"]
        entries = data["entries"]

        for entry_id, entry in entries.items():
            if not isinstance(entry, dict):
                raise IndexCorruptedError(
                    f"entry '{entry_id}' 不是有效的字典对象"
                )
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
        logger.info("index.json 加载成功，共 %d 条记录", len(self._entries))

        inconsistent_ids = self._check_text_hash_consistency()
        if inconsistent_ids:
            logger.warning(
                "检测到 %d 条 text_hash 不一致，已自动修复: %s",
                len(inconsistent_ids),
                inconsistent_ids,
            )
            self._embeddings_stale = True

    def _load_embeddings(self) -> None:
        """加载 embeddings.json。"""
        emb_path = self._data_dir / "embeddings.json"

        if not emb_path.exists():
            logger.info("embeddings.json 不存在，标记为待重建")
            self._embeddings = {}
            self._embeddings_stale = True
            return

        try:
            raw = emb_path.read_text(encoding="utf-8")
            self._embeddings = ujson.loads(raw)
            logger.info(
                "embeddings.json 加载成功，共 %d 条记录",
                len(self._embeddings),
            )
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("embeddings.json 解析失败，标记为待重建: %s", exc)
            self._embeddings = {}
            self._embeddings_stale = True

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
        logger.info(
            "embeddings.json 已保存，共 %d 条记录", len(self._embeddings)
        )
        self._embeddings_stale = False

    # ------------------------------------------------------------------
    # 单条增删
    # ------------------------------------------------------------------

    def add_entry(
        self,
        filename: str,
        text: str,
        embedding: list[float],
    ) -> str:
        """添加单条索引记录。

        自动分配可用 ID，计算 text_hash，
        同时写入 _entries 和 _embeddings，
        并原子写入磁盘。

        Args:
            filename: 表情包文件名。
            text: OCR 识别文本。
            embedding: embedding 向量。

        Returns:
            分配的索引 ID。
        """
        entry_id = self._find_next_id()
        text_hash = compute_text_hash(text)

        self._entries[entry_id] = {
            "filename": filename,
            "text": text,
            "text_hash": text_hash,
        }
        self._embeddings[entry_id] = {
            "text_hash": text_hash,
            "embedding": embedding,
        }

        self.save_index()
        self.save_embeddings()

        logger.info("已添加索引记录: id=%s, filename=%s", entry_id, filename)
        return entry_id

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
        del self._entries[entry_id]
        self._embeddings.pop(entry_id, None)

        self.save_index()
        self.save_embeddings()

        logger.info("已删除索引记录: id=%s, filename=%s", entry_id, filename)
        return True

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

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    )

    async def sync_with_filesystem(self) -> SyncResult:
        """按文件名同步索引与 memes/ 目录。

        1. 扫描 memes/ 获取当前图片文件列表。
        2. 对比 index entries 找出已删除的文件，移除对应记录。
        3. 找出新增文件，按文件名升序处理：
           - 调用 OCR 识别文本
           - 调用 embedding 生成向量
           - 写入索引
        4. 单个图片处理失败不影响其他图片。
        5. 原子写入更新后的索引文件。

        Returns:
            SyncResult(added, deleted, failed)
        """
        self._memes_dir.mkdir(parents=True, exist_ok=True)

        existing_files: set[str] = {
            f.name
            for f in self._memes_dir.iterdir()
            if f.is_file()
            and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
        }

        # 构建文件名 → id 映射
        filename_to_id: dict[str, str] = {}
        for eid, entry in self._entries.items():
            fn = entry.get("filename", "")
            if fn:
                filename_to_id[fn] = eid

        # 1. 删除已不存在的图片
        deleted_count = 0
        for filename, eid in list(filename_to_id.items()):
            if filename not in existing_files:
                logger.info("图片已删除，移除索引: id=%s, filename=%s", eid, filename)
                del self._entries[eid]
                self._embeddings.pop(eid, None)
                del filename_to_id[filename]
                deleted_count += 1

        # 2. 找出新增图片（按文件名升序）
        new_files = sorted(
            f for f in existing_files if f not in filename_to_id
        )

        added_count = 0
        failed: list[str] = []

        for filename in new_files:
            image_path = self._memes_dir / filename
            try:
                # OCR
                if self._ocr_provider is None:
                    raise RuntimeError("OCR 服务未注入")
                text = await self._ocr_provider.ocr(str(image_path))

                # Embedding
                if self._embedding_provider is None:
                    raise RuntimeError("Embedding 服务未注入")
                embedding = await self._embedding_provider.embed(text)

                # 写入索引
                entry_id = self._find_next_id()
                text_hash = compute_text_hash(text)
                self._entries[entry_id] = {
                    "filename": filename,
                    "text": text,
                    "text_hash": text_hash,
                }
                self._embeddings[entry_id] = {
                    "text_hash": text_hash,
                    "embedding": embedding,
                }
                added_count += 1
                logger.info("新增图片已加入索引: id=%s, filename=%s", entry_id, filename)

            except Exception as exc:
                logger.error("处理图片失败: filename=%s, error=%s", filename, exc)
                failed.append(filename)

        # 3. 原子写入
        if added_count > 0 or deleted_count > 0:
            self.save_index()
            if added_count > 0:
                self.save_embeddings()

        logger.info(
            "索引同步完成: 新增=%d, 删除=%d, 失败=%d",
            added_count,
            deleted_count,
            len(failed),
        )
        return SyncResult(added=added_count, deleted=deleted_count, failed=failed)

    # ------------------------------------------------------------------
    # is_locked 属性
    # ------------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        """索引是否处于锁定状态。"""
        return self._locked
