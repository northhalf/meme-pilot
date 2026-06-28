# Embeddings 存储优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 embeddings.json 中 1024 维 float 数组的存储方式从 JSON 数组改为 struct+base64 字符串，压缩 82% 体积、99.6% 行数。

**Architecture:** 在 index_manager.py 中新增 encode_embedding/decode_embedding 模块级函数，修改 _load_embeddings 和 save_embeddings 两个方法。内存中 _embeddings 仍保持 list[float] 不变，仅在序列化/反序列化时做转换。新格式增加 version=2 顶层字段。

**Tech Stack:** Python struct + base64（标准库），零新依赖。

---

### Task 1: 编码解码工具函数

**Files:**
- Modify: `bot/engine/index_manager.py`（`encode_embedding` / `decode_embedding` 模块级函数）
- Create: `tests/unit/engine/test_embedding_codec.py`

- [ ] **Step 1: 写编码解码函数的测试**

```python
"""encode_embedding / decode_embedding 工具函数测试。"""

import struct
import base64

import pytest

from bot.engine.index_manager import encode_embedding, decode_embedding


class TestEncodeEmbedding:
    """encode_embedding 测试。"""

    def test_returns_string(self) -> None:
        """返回 base64 字符串。"""
        result = encode_embedding([0.1, 0.2, 0.3])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_single_value(self) -> None:
        """单值编码。"""
        result = encode_embedding([1.0])
        assert isinstance(result, str)

    def test_1024_dim(self) -> None:
        """1024 维向量编码。"""
        emb = [float(i) for i in range(1024)]
        result = encode_embedding(emb)
        # 1024 * 4 bytes = 4096 bytes → base64 = 5464 chars (含填充)
        assert len(result) == 5464


class TestDecodeEmbedding:
    """decode_embedding 测试。"""

    def test_decodes_to_list_of_floats(self) -> None:
        """解码为 float 列表。"""
        data = base64.b64encode(struct.pack("!3f", 0.1, 0.2, 0.3)).decode("ascii")
        result = decode_embedding(data)
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    def test_roundtrip(self) -> None:
        """编码再解码后与原始值完全一致。"""
        original = [0.1, -0.5, 3.14159, 0.0, -1.0, 999.0]
        encoded = encode_embedding(original)
        decoded = decode_embedding(encoded)
        assert decoded == original

    def test_roundtrip_1024_dim(self) -> None:
        """1024 维编码解码 roundtrip 精度无损。"""
        original = [float(i * 0.1 - 51.2) for i in range(1024)]
        encoded = encode_embedding(original)
        decoded = decode_embedding(encoded)
        # float32 精度下逐值比较
        for a, b in zip(original, decoded):
            assert abs(a - b) < 1e-6
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_embedding_codec.py -v`

Expected: 5 FAILED（ImportError: cannot import name 'encode_embedding' from 'bot.engine.index_manager'）

- [ ] **Step 3: 实现编码解码函数**

在 `bot/engine/index_manager.py` 的工具函数区域（约第 30 行的 `normalize_text` 之前或之后）添加：

```python
import base64
import struct


# ... 现有 import ...

def encode_embedding(embedding: list[float]) -> str:
    """将 float32 列表编码为 base64 字符串。

    使用 big-endian（网络字节序）确保跨平台一致性。
    float32 roundtrip 精度零误差。

    Args:
        embedding: float32 向量值列表。

    Returns:
        base64 编码的字符串。
    """
    packed = struct.pack(f"!{len(embedding)}f", *embedding)
    return base64.b64encode(packed).decode("ascii")


def decode_embedding(data: str) -> list[float]:
    """将 base64 字符串解码为 float32 列表。

    Args:
        data: base64 编码的 float32 二进制数据。

    Returns:
        float32 值列表。
    """
    packed = base64.b64decode(data)
    return list(struct.unpack(f"!{len(packed) // 4}f", packed))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_embedding_codec.py -v`

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_embedding_codec.py
git commit -m "feat(engine): 新增 embedding 编码解码工具函数

使用 struct+base64 将 float32 数组压缩为紧凑字符串，
为 embeddings.json 存储优化做准备。零新依赖。"
```

---

### Task 2: 修改 _load_embeddings 支持 version 2 新格式

**Files:**
- Modify: `bot/engine/index_manager.py`（`_load_embeddings` 方法）
- Modify: `tests/unit/engine/test_index_manager.py`（新增 `_load_embeddings` 测试）

- [ ] **Step 1: 写 _load_embeddings 新格式加载测试**

在 `tests/unit/engine/test_index_manager.py` 末尾追加：

```python
class TestLoadEmbeddings:
    """_load_embeddings 方法测试。"""

    V2_DATA: dict[str, object] = {
        "version": 2,
        "entries": {
            "1": {
                "text_hash": "sha256:abc123",
                "embedding": "AAAAAEBAP4A=",  # b64 of [2.0, 1.0] as float32
            },
        },
    }

    def test_loads_v2_format(self, tmp_path: Path) -> None:
        """正确加载 version=2 的 embeddings 文件。"""
        from bot.engine.index_manager import decode_embedding

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        emb_path = data_dir / "embeddings.json"
        emb_path.write_text(ujson.dumps(self.V2_DATA, indent=2), encoding="utf-8")

        mgr = IndexManager(str(data_dir))
        mgr.load()
        embeddings = mgr.get_embeddings()
        assert "1" in embeddings
        entry = embeddings["1"]
        assert isinstance(entry["embedding"], list)
        assert all(isinstance(v, float) for v in entry["embedding"])

    def test_decoded_values_match(self, tmp_path: Path) -> None:
        """解码后的值与编码前一致。"""
        from bot.engine.index_manager import decode_embedding, encode_embedding

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        emb_path = data_dir / "embeddings.json"
        emb_path.write_text(ujson.dumps(self.V2_DATA, indent=2), encoding="utf-8")

        mgr = IndexManager(str(data_dir))
        mgr.load()
        embedding = mgr.get_embeddings()["1"]["embedding"]
        # [2.0, 1.0] as float32, packed in big-endian
        expected = list(struct.unpack("!2f", base64.b64decode("AAAAAEBAP4A=")))
        assert embedding == expected

    def test_old_format_without_version_is_cleared(self, tmp_path: Path) -> None:
        """旧格式（无 version 字段）加载后清空 _embeddings 待重建。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        emb_path = data_dir / "embeddings.json"
        old_data = {
            "1": {
                "text_hash": "sha256:old",
                "embedding": [0.1, 0.2, 0.3],
            },
        }
        emb_path.write_text(ujson.dumps(old_data, indent=2), encoding="utf-8")

        mgr = IndexManager(str(data_dir))
        mgr.load()
        embeddings = mgr.get_embeddings()
        assert embeddings == {}

    def test_corrupted_json_clears_embeddings(self, tmp_path: Path) -> None:
        """损坏的 JSON 保持清空行为。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        emb_path = data_dir / "embeddings.json"
        emb_path.write_text("{invalid json}", encoding="utf-8")

        mgr = IndexManager(str(data_dir))
        mgr.load()
        assert mgr.get_embeddings() == {}

    def test_missing_file_clears_embeddings(self, tmp_path: Path) -> None:
        """文件不存在时保持清空行为。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mgr = IndexManager(str(data_dir))
        mgr.load()
        assert mgr.get_embeddings() == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestLoadEmbeddings -v`

Expected: 5 FAILED（旧 _load_embeddings 不会识别 version 2 格式）

- [ ] **Step 3: 修改 _load_embeddings 方法**

将 `bot/engine/index_manager.py` 的 `_load_embeddings` 方法替换为：

```python
def _load_embeddings(self) -> None:
    """加载 embeddings.json，支持 version 2 格式。

    加载规则：
    - version=2：正常加载，对每条 embedding 执行 decode_embedding 解码。
    - 无 version 字段或 version<2：视为旧格式，清空 _embeddings，
      由 sync_with_filesystem() 重建。
    - 文件不存在或解析失败：清空 _embeddings。
    """
    emb_path = self._data_dir / "embeddings.json"

    if not emb_path.exists():
        logger.info("embeddings.json 不存在，置空 _embeddings 待重建")
        self._embeddings = {}
        return

    try:
        raw = emb_path.read_text(encoding="utf-8")
        data = ujson.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("embeddings.json 解析失败，置空 _embeddings 待重建: %s", exc)
        self._embeddings = {}
        return

    # 检查 version 字段
    if not isinstance(data, dict) or data.get("version") != 2:
        logger.info(
            "embeddings.json 格式过旧（无 version 或 version<2），"
            "置空 _embeddings 待重建"
        )
        self._embeddings = {}
        return

    entries = data.get("entries", {})
    decoded: dict[str, dict[str, object]] = {}
    for eid, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        text_hash = entry.get("text_hash", "")
        emb_b64 = entry.get("embedding", "")
        if isinstance(emb_b64, str) and emb_b64:
            try:
                embedding = decode_embedding(emb_b64)
                decoded[eid] = {
                    "text_hash": text_hash,
                    "embedding": embedding,
                }
            except Exception:
                logger.warning("embedding 解码失败: id=%s", eid)
                continue

    self._embeddings = decoded
    logger.info(
        "embeddings.json 加载成功（version=2），共 %d 条记录",
        len(self._embeddings),
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestLoadEmbeddings -v`

Expected: 5 PASSED

- [ ] **Step 5: 确认未损坏已有测试**

Run: `uv run pytest tests/unit/ -v`

Expected: 所有已有测试 PASSED（底层行为不变，旧格式直接被清空，不影响功能）

- [ ] **Step 6: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): _load_embeddings 支持 version 2 新格式

旧格式（无 version 字段）加载后自动清空，
由 sync_with_filesystem() 全量重建为新格式。"
```

---

### Task 3: 修改 save_embeddings 输出新格式

**Files:**
- Modify: `bot/engine/index_manager.py`（`save_embeddings` 方法）
- Modify: `tests/unit/engine/test_index_manager.py`（新增 `save_embeddings` 测试）

- [ ] **Step 1: 写 save_embeddings 测试**

在 `tests/unit/engine/test_index_manager.py` 末尾追加：

```python
class TestSaveEmbeddings:
    """save_embeddings 方法测试。"""

    def test_saves_v2_format(self, tmp_path: Path) -> None:
        """写入的 JSON 包含 version=2 字段。"""
        from bot.engine.index_manager import encode_embedding

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # 先写一个空 index.json，这样 load() 不会报错
        index_path = data_dir / "index.json"
        index_path.write_text(
            ujson.dumps({"version": 1, "entries": {}}, indent=2),
            encoding="utf-8",
        )

        mgr = IndexManager(str(data_dir))
        mgr.load()
        # 直接操作 _embeddings
        emb = [0.1, 0.2]
        mgr._embeddings["1"] = {"text_hash": "sha256:x", "embedding": emb}
        mgr.save_embeddings()

        emb_path = data_dir / "embeddings.json"
        assert emb_path.exists()
        raw = ujson.loads(emb_path.read_text(encoding="utf-8"))
        assert raw.get("version") == 2
        assert "entries" in raw

    def test_embedding_stored_as_string(self, tmp_path: Path) -> None:
        """embedding 字段存储为 base64 字符串而非数组。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        index_path = data_dir / "index.json"
        index_path.write_text(
            ujson.dumps({"version": 1, "entries": {}}, indent=2),
            encoding="utf-8",
        )

        mgr = IndexManager(str(data_dir))
        mgr.load()
        mgr._embeddings["1"] = {
            "text_hash": "sha256:x",
            "embedding": [0.1, 0.2],
        }
        mgr.save_embeddings()

        emb_path = data_dir / "embeddings.json"
        raw = ujson.loads(emb_path.read_text(encoding="utf-8"))
        entry = raw["entries"]["1"]
        assert isinstance(entry["embedding"], str)
        assert len(entry["embedding"]) > 0

    def test_roundtrip_save_load(self, tmp_path: Path) -> None:
        """save → load roundtrip 后 embedding 值不变。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        index_path = data_dir / "index.json"
        index_path.write_text(
            ujson.dumps({"version": 1, "entries": {}}, indent=2),
            encoding="utf-8",
        )

        mgr = IndexManager(str(data_dir))
        mgr.load()
        original_emb = [float(i * 0.1) for i in range(1024)]
        mgr._embeddings["1"] = {
            "text_hash": "sha256:abc",
            "embedding": original_emb,
        }
        mgr.save_embeddings()

        mgr2 = IndexManager(str(data_dir))
        mgr2.load()
        loaded = mgr2.get_embeddings()["1"]["embedding"]
        assert isinstance(loaded, list)
        for a, b in zip(original_emb, loaded):
            assert abs(a - b) < 1e-6
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSaveEmbeddings -v`

Expected: 3 FAILED（save_embeddings 仍写旧格式）

- [ ] **Step 3: 修改 save_embeddings 方法**

将 `bot/engine/index_manager.py` 的 `save_embeddings` 方法替换为：

```python
def save_embeddings(self) -> None:
    """原子写入 embeddings.json（version 2 格式）。

    将内存中的 _embeddings（embedding 为 list[float]）序列化为：
    - version=2
    - entries 中每条 embedding 编码为 base64 字符串
    """
    entries: dict[str, dict[str, object]] = {}
    for eid, entry in self._embeddings.items():
        text_hash = entry.get("text_hash", "")
        embedding = entry.get("embedding", [])
        if isinstance(embedding, list):
            emb_b64 = encode_embedding(embedding)
        else:
            emb_b64 = str(embedding)  # fallback
        entries[eid] = {
            "text_hash": text_hash,
            "embedding": emb_b64,
        }

    data: dict[str, object] = {
        "version": 2,
        "entries": entries,
    }
    emb_path = self._data_dir / "embeddings.json"
    self._atomic_write(emb_path, data)
    logger.info("embeddings.json 已保存（version=2），共 %d 条记录", len(entries))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/engine/test_index_manager.py::TestSaveEmbeddings -v`

Expected: 3 PASSED

- [ ] **Step 5: 确认未损坏已有测试**

Run: `uv run pytest tests/unit/ -v`

Expected: 全部 PASSED

- [ ] **Step 6: Commit**

```bash
git add bot/engine/index_manager.py tests/unit/engine/test_index_manager.py
git commit -m "feat(engine): save_embeddings 输出 version 2 新格式

写入时对每条 embedding 执行 encode_embedding，输出为 base64 字符串。
顶层增加 version=2 字段，与 _load_embeddings 的检测逻辑配合。"
```

---

### Task 4: 更新文档

**Files:**
- Modify: `docs/api/API.md`（更新 embeddings.json 格式说明）
- Modify: `docs/api/bot/engine/index_manager.md`（更新 encode/decode 函数说明，如存在）

- [ ] **Step 1: 更新 `docs/api/API.md` 中 embeddings.json 相关说明**

```
### `docs/api/bot/engine/index_manager.md`

```python
def encode_embedding(embedding: list[float]) -> str
def decode_embedding(data: str) -> list[float]
```

**新增模块级函数：**

- `encode_embedding` / `decode_embedding` — float32 向量与 base64 字符串间的编解码，使用 struct + base64（big-endian），精度无损。

**embeddings.json 格式变更：**

v1（旧）：
```json
{
  "1": {"text_hash": "sha256:...", "embedding": [0.1, 0.2, ...]}
}
```
v2（新）：
```json
{
  "version": 2,
  "entries": {
    "1": {"text_hash": "sha256:...", "embedding": "AAAAAEA/4D8..."}
  }
}
```

- `get_embeddings()` 返回的 `embedding` 仍为 `list[float]`，编解码对消费者透明。
```

- [ ] **Step 2: Commit**

```bash
git add docs/api/API.md
git commit -m "docs: 更新 embeddings.json version 2 格式说明"
```

---

### 验证

```bash
# 全量测试
uv run pytest -v

# 编译检查
uv run python -m compileall bot tests
```
