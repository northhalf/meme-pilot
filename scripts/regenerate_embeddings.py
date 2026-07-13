"""使用 Google GenAI SDK 批量重新生成全部文本向量。

读取 data/index.db 中所有 meme 文本，直接调用 Google GenAI SDK 的 embed_content，
一次传入多条文本（contents 列表）批量获取向量，避免短时间大量请求被拒，
然后通过 VectorStore.rebuild_all 全量重建 ChromaDB 向量索引。

运行方式：
    uv run python scripts/regenerate_embeddings.py
    uv run python scripts/regenerate_embeddings.py --batch-size 100
    uv run python scripts/regenerate_embeddings.py --sleep 60
    uv run python scripts/regenerate_embeddings.py --dry-run

注意：
- 仅支持 Google Embedding provider。
- 执行前请确保已设置 GOOGLE_API_KEY（以及可选的 GOOGLE_BASE_URL、GOOGLE_EMBEDDING_MODEL）。
- 空文本条目会被跳过，不会写入向量库。
- 重建过程会清空原有 chroma collection，请确认无其他进程正在写入索引。
- gemini-embedding-001 免费额度约为每分钟 100 条生成调用；若单批大小接近该上限，
  建议使用 --sleep 控制两次 API 调用之间的间隔，避免触发限流。
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

# 支持从项目根目录或 scripts/ 目录直接运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import CHROMA_DIR, INDEX_DB_PATH  # noqa: E402
from bot.engine.metadata_store import MetadataStore  # noqa: E402
from bot.engine.vector_store import VectorStore  # noqa: E402

logger = logging.getLogger(__name__)

# Google Embedding 输出维度，须与 ChromaDB collection 维度一致
_EMBEDDING_DIMENSIONALITY = 1024


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="重新生成所有 meme 文本的 Embedding 向量。"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="每次 embed_content 调用传入的文本条数（默认 100）。",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="两次 API 调用之间的等待秒数（默认 0）。当批量大小接近免费额度 RPM 上限时，"
        "可设为 60 以控制速率。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要处理的条目数，不实际调用 API 或写入向量库。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出 DEBUG 级别日志。",
    )
    return parser.parse_args()


def _create_genai_client() -> tuple[genai.Client, str]:
    """从环境变量创建 Google GenAI Client。

    Returns:
        (Client 实例, 模型名) 元组。

    Raises:
        ValueError: 未配置 GOOGLE_API_KEY 或 GOOGLE_EMBEDDING_MODEL。
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise ValueError("未设置 GOOGLE_API_KEY 环境变量")

    model = os.environ.get("GOOGLE_EMBEDDING_MODEL")
    if not model:
        raise ValueError("未设置 GOOGLE_EMBEDDING_MODEL 环境变量")

    base_url = os.environ.get("GOOGLE_BASE_URL")
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["http_options"] = {"base_url": base_url}

    return genai.Client(**client_kwargs), model


async def _embed_batch(
    client: genai.Client,
    model: str,
    batch: list[tuple[int, str]],
) -> list[tuple[int, list[float]]]:
    """批量生成一组文本的向量。

    Args:
        client: Google GenAI Client。
        model: Embedding 模型名。
        batch: (entry_id, text) 列表。

    Returns:
        (entry_id, vector) 列表，顺序与输入 batch 一致；失败时返回空列表。
    """
    entry_ids = [entry_id for entry_id, _ in batch]
    texts = [text for _, text in batch]

    logger.info("批量生成 %d 条向量: ids=%s", len(batch), entry_ids)
    try:
        response = await asyncio.to_thread(
            client.models.embed_content,
            model=model,
            contents=texts,  # type: ignore[arg-type]
            config=types.EmbedContentConfig(
                output_dimensionality=_EMBEDDING_DIMENSIONALITY
            ),
        )
    except Exception as exc:
        logger.error("批量生成向量失败: ids=%s, error=%s", entry_ids, exc)
        return []

    embeddings = response.embeddings
    if not embeddings or len(embeddings) != len(batch):
        logger.error(
            "返回向量数量与请求不一致: 请求 %d 条，返回 %s 条",
            len(batch),
            len(embeddings) if embeddings else "None",
        )
        return []

    batch_embeddings: list[tuple[int, list[float]]] = []
    for entry_id, embedding in zip(entry_ids, embeddings):
        values = embedding.values
        if values is None:
            logger.error("返回的 embedding 无 values: id=%s", entry_id)
            continue
        batch_embeddings.append((entry_id, list(values)))
    return batch_embeddings


async def _regenerate(batch_size: int, sleep_seconds: float, dry_run: bool) -> None:
    """执行重新生成逻辑。

    Args:
        batch_size: 每批传入 embed_content 的文本条数。
        sleep_seconds: 两次 API 调用之间的等待秒数。
        dry_run: 为 True 时只统计不实际执行。
    """
    metadata_store = MetadataStore(str(INDEX_DB_PATH))
    metadata_store.load()

    entries = metadata_store.get_all_entries()
    if not entries:
        logger.warning("MetadataStore 中没有记录，无需重建。")
        metadata_store.close()
        return

    # 过滤有效文本
    items: list[tuple[int, str]] = []
    for entry_id, entry in entries.items():
        text = entry.text.strip()
        if text:
            items.append((entry_id, text))
        else:
            logger.warning(
                "跳过空文本条目: id=%s, image_path=%s", entry_id, entry.image_path
            )

    logger.info("共 %d 条记录，其中 %d 条有有效文本。", len(entries), len(items))
    if dry_run:
        logger.info("dry-run 模式，不调用 API 或写入向量库。")
        metadata_store.close()
        return

    client, model = _create_genai_client()
    vector_store = VectorStore(str(CHROMA_DIR))
    vector_store.load()

    try:
        embeddings: list[tuple[int, list[float]]] = []
        total_batches = (len(items) + batch_size - 1) // batch_size
        for batch_idx, i in enumerate(range(0, len(items), batch_size), start=1):
            batch = items[i : i + batch_size]
            batch_embeddings = await _embed_batch(client, model, batch)
            embeddings.extend(batch_embeddings)
            logger.info(
                "已完成 %d/%d 条向量生成（第 %d/%d 批）。",
                min(i + batch_size, len(items)),
                len(items),
                batch_idx,
                total_batches,
            )
            # 非最后一批时等待，避免触发 RPM 限制
            if sleep_seconds > 0 and batch_idx < total_batches:
                logger.info("等待 %.1f 秒后继续下一批...", sleep_seconds)
                await asyncio.sleep(sleep_seconds)

        if embeddings:
            logger.info("写入 %d 条向量到 ChromaDB...", len(embeddings))
            await vector_store.rebuild_all(embeddings)
            logger.info("向量重建完成。")
        else:
            logger.warning("没有成功生成任何向量。")
    finally:
        await asyncio.to_thread(client.close)
        vector_store.close()
        metadata_store.close()


def main() -> None:
    """脚本入口。"""
    # 加载项目根目录 .env，确保环境变量可用
    load_dotenv(PROJECT_ROOT / ".env")

    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        asyncio.run(_regenerate(args.batch_size, args.sleep, args.dry_run))
    except Exception as exc:
        logger.exception("重新生成向量失败: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
