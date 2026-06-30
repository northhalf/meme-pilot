"""旧 JSON 索引 → sqlite + chroma 迁移脚本（手动运行）。

读取 data/index.json + data/embeddings.json，写入 data/index.db（sqlite）与
data/chroma（chromadb）。保留旧 id 数值，复用旧 embedding 向量（零 API 消耗）。

运行方式：
    uv run python scripts/migrate_json_to_db.py
    uv run python scripts/migrate_json_to_db.py --data-dir /path/to/data

幂等：若 sqlite meme 表已有数据，提示已迁移并退出。
"""

import argparse
import asyncio
import base64
import json
import logging
import struct
from pathlib import Path

from bot.config import PROJECT_ROOT
from bot.engine.metadata_store import MetadataStore
from bot.engine.vector_store import VectorStore

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


def _decode_embedding(data: str) -> list[float]:
    """将 v2 格式的 base64 float32 编码解码为浮点数列表（内联自旧 decode_embedding）。"""
    packed = base64.b64decode(data)
    return list(struct.unpack(f"!{len(packed) // 4}f", packed))


def _load_old_index(data_dir: Path) -> dict:
    """读取旧 index.json。"""
    index_path = data_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"未找到 {index_path}，无需迁移")
    return json.loads(index_path.read_text(encoding="utf-8"))


def _load_old_embeddings(data_dir: Path) -> dict:
    """读取旧 embeddings.json，返回 entries 字典（可能 v1 或 v2）。

    不存在时返回空 dict（迁移后由 sync 全量重建）。
    """
    emb_path = data_dir / "embeddings.json"
    if not emb_path.exists():
        return {"version": 1, "entries": {}}
    return json.loads(emb_path.read_text(encoding="utf-8"))


def _resolve_embedding(entry: dict, version: int) -> list[float] | None:
    """按 version 解析单条 embedding。

    v2: embedding 为 base64 字符串，需 decode。
    v1: embedding 为 list[float]，直接用。
    """
    raw = entry.get("embedding")
    if raw is None:
        return None
    if version == 2:
        if not isinstance(raw, str):
            return None
        try:
            return _decode_embedding(raw)
        except Exception as exc:
            logger.warning("embedding 解码失败，跳过: %s", exc)
            return None
    # v1 或无 version：直接是 list
    if isinstance(raw, list):
        return [float(x) for x in raw]
    return None


def run_migration(data_dir: str) -> None:
    """执行迁移。

    Args:
        data_dir: 数据目录路径（含 index.json/embeddings.json，输出 index.db/chroma）。
    """
    data_dir_path = Path(data_dir)
    db_path = str(data_dir_path / "index.db")
    chroma_path = str(data_dir_path / "chroma")

    metadata_store = MetadataStore(db_path)
    vector_store = VectorStore(chroma_path)
    metadata_store.load()
    vector_store.load()

    # 幂等检查
    if metadata_store.entry_count() > 0:
        print(f"已迁移：data/index.db 已有 {metadata_store.entry_count()} 条记录，跳过。")
        metadata_store.close()
        vector_store.close()
        return

    try:
        old_index = _load_old_index(data_dir_path)
    except FileNotFoundError as exc:
        print(str(exc))
        metadata_store.close()
        vector_store.close()
        return

    old_embeddings = _load_old_embeddings(data_dir_path)
    emb_version = old_embeddings.get("version", 1)
    emb_entries = old_embeddings.get("entries", {})

    index_entries = old_index.get("entries", {})
    migrated = 0
    skipped_blank = 0
    skipped_bad_id = 0
    skipped_bad_emb = 0
    pending: list[tuple[int, list[float]]] = []

    for id_str, entry in index_entries.items():
        # 非数字 id 防御
        try:
            old_id = int(id_str)
        except (ValueError, TypeError):
            print(f"跳过非数字 id: {id_str}")
            skipped_bad_id += 1
            continue

        # 去所有空白
        text_new = "".join(str(entry.get("text", "")).split())
        if not text_new:
            print(f"跳过去空格后为空的条目: id={id_str}, filename={entry.get('filename')}")
            skipped_blank += 1
            continue

        image_path = str(entry.get("filename", ""))

        # 解析旧 embedding
        emb_record = emb_entries.get(id_str, {})
        embedding = _resolve_embedding(emb_record, emb_version)
        if embedding is None or len(embedding) != EMBEDDING_DIM:
            print(f"跳过 embedding 缺失/维度异常: id={id_str}, dim={len(embedding) if embedding else 0}")
            skipped_bad_emb += 1
            continue

        # 写入 sqlite（保留旧 id），向量收集后批量写入 chroma（复用旧向量）
        new_id = metadata_store.add_with_id(
            entry_id=old_id,
            image_path=image_path,
            text=text_new,
            speaker=None,
            tags=[],
        )
        pending.append((new_id, embedding))
        migrated += 1

    # 单一事件循环批量写入所有向量，避免逐条 asyncio.run 反复创建/销毁事件循环
    async def _write_all() -> None:
        for new_id, emb in pending:
            await vector_store.upsert(new_id, emb)

    asyncio.run(_write_all())

    print(f"迁移完成：{migrated} 条记录写入 data/index.db 与 data/chroma/")
    print(
        "embedding 复用旧向量（基于含空格 text 生成）。如需与无空格 text 严格一致，"
        "可删除 data/chroma/ 后重启 Bot，后台同步会按 sqlite text 全量重建 embedding。"
    )
    print(f"旧文件 data/index.json、data/embeddings.json 已保留，可自行归档或删除。")
    print(f"统计：迁移 {migrated}，空文本跳过 {skipped_blank}，非数字 id 跳过 {skipped_bad_id}，embedding 异常跳过 {skipped_bad_emb}")

    metadata_store.close()
    vector_store.close()


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="旧 JSON 索引 → sqlite + chroma 迁移")
    parser.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / "data"),
        help="数据目录路径（默认 <项目根>/data）",
    )
    args = parser.parse_args()
    run_migration(args.data_dir)


if __name__ == "__main__":
    main()
