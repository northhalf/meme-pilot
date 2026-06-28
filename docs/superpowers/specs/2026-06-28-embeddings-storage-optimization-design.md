# Embeddings 存储优化设计

> 日期：2026-06-28
> 状态：待实施

## 问题

`embeddings.json` 使用带缩进的 JSON 数组存储 1024 维 float32 向量，每个 float 独占一行，导致文件体积膨胀：

| 指标 | 当前值（170 条） | 外推 1k 条 |
|------|:-:|:-:|
| 行数 | ~174,931 | ~1,030,000 |
| 磁盘体积 | ~4.9 MB | ~29 MB |

git diff 在每次重建时全量变化，且文件无法用编辑器查看。

## 方案

使用 Python 标准库 `struct` + `base64` 将 float32 数组序列化为紧凑字符串替代纯 JSON 数组。

### 新格式

```json
{
  "version": 2,
  "entries": {
    "1": {
      "text_hash": "sha256:2dd75161649fca27...",
      "embedding": "AACAyT+AAEA/gABAQH8AAEB/AA...=="
    }
  }
}
```

- `version: 2` — 标识新格式版本，便于后续格式演进
- `embedding` — 原为 `list[float]`，改为 base64 编码的 float32 二进制数据
- 顶层结构从 `Dict[str, Entry]` 改为 `{"version": 2, "entries": Dict[str, Entry]}`，与 `index.json` 保持一致

### 编码/解码

```python
import struct
import base64

def encode_embedding(embedding: list[float]) -> str:
    """将 float32 列表编码为 base64 字符串（big-endian）。"""
    packed = struct.pack(f'!{len(embedding)}f', *embedding)
    return base64.b64encode(packed).decode('ascii')

def decode_embedding(data: str) -> list[float]:
    """将 base64 字符串解码为 float32 列表。"""
    packed = base64.b64decode(data)
    return list(struct.unpack(f'!{len(packed) // 4}f', packed))
```

- 使用 `!`（etwork byte order / big-endiann）确保跨平台一致性
- 精度完全无损（float32 roundtrip 零误差，已验证）

### 预期效果

| 维度 | 优化前 | 优化后 | 变化 |
|------|:-:|:-:|:-:|
| 单条存储 | ~29 KB JSON | ~5.5 KB base64 | -82% |
| 170 条总大小 | 4.9 MB | ~0.9 MB | -82% |
| 170 条总行数 | 174,931 | ~700 | -99.6% |
| 1k 条外推 | ~29 MB | ~5.5 MB | -82% |

### 改动范围

仅涉及 `bot/engine/index_manager.py`：

1. **`_load_embeddings()`** — 加载后校验 `version` 字段：
   - 无 `version` 字段或 `version < 2` → 打印日志，清空 `_embeddings`，由后续 `sync_with_filesystem()` 全量重建
   - `version == 2` → 正常加载，对每个 entry 的 `embedding` 调用 `decode_embedding()` 解码为 `list[float]`

2. **`save_embeddings()`** — 输出新格式：
   - 对每个 entry 的 `embedding` 调用 `encode_embedding()` 编码为 base64 字符串
   - 顶层结构写入 `{"version": 2, "entries": ...}`

3. **`encode_embedding()` / `decode_embedding()`** — 新增模块级工具函数

4. 对外接口不变：
   - `get_embeddings()` 仍返回 `dict[str, dict[str, object]]`，其中 `embedding` 为 `list[float]`
   - 所有消费者无需修改

### 迁移步骤

部署新代码后重启 Bot，启动流程自动完成迁移：

1. `load()` → `_load_embeddings()` 加载旧 `embeddings.json`
2. 检测到无 `version` 字段 → 清空 `_embeddings`
3. 后台 `_background_sync()` 运行 `sync_with_filesystem()`
4. 重建阶段检测到 `_embeddings` 为空 → 全量重建所有 entry 的 embedding
5. `save_embeddings()` 写入新格式（version 2 + base64）

### 不支持的方案

| 方案 | 放弃原因 |
|------|---------|
| Compact JSON（无 indent）| 仅减 25% 体积，治标不治本 |
| NumPy .npy 独立文件 | 新增 ~10MB numpy 依赖，Docker 镜像膨胀 |
| 维持现状 | 问题随着表情包数量增长持续恶化 |
