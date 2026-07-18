# 百度 OCR Provider 设计

> 日期：2026-07-18  
> 状态：待用户审阅

## 1. 背景与目标

MemePilot 当前通过 `OcrProvider` 协议支持 RapidOCR、本地 PaddleOCR 云客户端和 OpenAI 兼容视觉 OCR。此次新增 `OCR_PROVIDER=baidu`，直接调用百度智能云 OCR REST API，并支持以下 7 种模式：

| 模式 | API 路径 | 响应族 |
|---|---|---|
| `pp_ocrv6`（默认） | `/rest/2.0/ocr/v1/pp_ocrv5`（百度 PP-OCRv6 兼容路径） | `page_result[].lines` |
| `general_basic` | `/rest/2.0/ocr/v1/general_basic` | `words_result[].words` |
| `general` | `/rest/2.0/ocr/v1/general` | `words_result[].words` |
| `accurate_basic` | `/rest/2.0/ocr/v1/accurate_basic` | `words_result[].words` |
| `accurate` | `/rest/2.0/ocr/v1/accurate` | `words_result[].words` |
| `webimage` | `/rest/2.0/ocr/v1/webimage` | `words_result[].words` |
| `webimage_loc` | `/rest/2.0/ocr/v1/webimage_loc` | `words_result[].words` |

所有模式最终遵守现有 `OcrProvider` 契约：按 API 返回顺序提取文本，按空白分割后以英文逗号拼接，返回 `str`；无文字时返回空字符串。

本次同时删除两个依赖真实 DeepSeek/OpenAI OCR 的 pytest 联网测试，并提供独立、手动执行的百度 OCR 联网验证脚本。

## 2. 范围

### 2.1 包含

- 新增异步百度 OCR REST provider。
- 支持 7 种 OCR 模式，默认 `pp_ocrv6`。
- API Key/Secret Key 换取 access token。
- access token 缓存、提前刷新和并发刷新保护。
- PP-OCRv6 与传统六类接口的独立响应解析。
- 使用现有 `OCR_TEXT_SCORE` 尽量过滤低置信度文本行。
- 分类处理网络、鉴权、配额、限流和参数错误。
- 复用 `OCR_CONCURRENCY`，HTTP 超时固定为 60 秒。
- 离线单元测试。
- 独立手动联网测试脚本，支持单模式或全部模式。
- `.env.example`、Compose、README、PRD、CONTEXT 同步更新。
- 删除现有 DeepSeek/OpenAI OCR 联网测试文件。

### 2.2 不包含

- 选择题题号、题目和选项解析。
- KMeans 坐标重排。
- 把 OCR 坐标写入 sqlite 或 ChromaDB。
- 卡证、票据、交通、医疗、教育等专项结构化 OCR 接口。
- 百度 OCR 在线测试加入 pytest 或 CI。
- 新增第三方依赖。
- 修改 `OcrProvider` 协议或 `ImagePipeline` 的业务流程。

## 3. 配置

新增环境变量：

```dotenv
OCR_PROVIDER=baidu
BAIDU_API_KEY=
BAIDU_SECRET_KEY=
BAIDU_OCR_TYPE=pp_ocrv6
```

`BAIDU_OCR_TYPE` 合法值：

```text
pp_ocrv6
general_basic
general
accurate_basic
accurate
webimage
webimage_loc
```

配置规则：

- `OCR_PROVIDER` 新增合法值 `baidu`。
- `BAIDU_OCR_TYPE` 缺失或非法时回退 `pp_ocrv6`。
- `BAIDU_API_KEY` 或 `BAIDU_SECRET_KEY` 为空时，创建百度 provider 失败并给出明确配置错误。
- 百度 API 主机固定为 `https://aip.baidubce.com`，不增加 base URL 或完整 URL 覆盖项。
- 用户配置名保持 `pp_ocrv6`，底层固定使用百度官方 PP-OCRv6 文档给出的兼容路径 `/rest/2.0/ocr/v1/pp_ocrv5`。
- 并发继续使用 `OCR_CONCURRENCY`，默认 5。
- 置信度阈值继续使用 `OCR_TEXT_SCORE`，默认 0.9。
- HTTP 请求超时固定为 60 秒，不增加超时环境变量。

## 4. 架构

### 4.1 新模块

新增 `bot/engine/baidu_ocr.py`。

构造函数接收可选的 `api_key`、`secret_key`、`ocr_type` 和 `concurrency`；显式参数优先于环境变量，便于单元测试和手动联网脚本选择模式。非法的显式 `ocr_type` 抛出 `ValueError`，环境变量中的非法值则由 `read_baidu_ocr_type()` 回退为 `pp_ocrv6`。

```text
BaiduOcrService
├── httpx.AsyncClient
├── OCR_CONCURRENCY 信号量
├── access token 缓存
├── token 刷新锁
├── 7 种模式 endpoint 映射
├── PP-OCRv6 解析器
├── 传统 words_result 解析器
└── close()
```

`BaiduOcrService` 实现现有 `bot.engine.protocols.OcrProvider`，只负责百度 OCR 网络调用及响应到纯文本的转换。

图片优化、无文字图片移动、Embedding、去重和索引写入继续由现有 `ImagePipeline` 和 IndexManager 协作者负责。

### 4.2 注册与创建

- `bot/config.py`
  - `_VALID_OCR_PROVIDERS` 加入 `baidu`。
  - 新增 `read_baidu_ocr_type()`。
- `bot/engine/__init__.py`
  - 导入 `BaiduOcrService` 和 `create_baidu_ocr_service()`。
  - 调用 `register_ocr("baidu", create_baidu_ocr_service)`。
  - 导入失败时沿用现有不可用 provider 标记机制。
- `bot/engine/provider_factory.py`
  - 不修改公开接口。
- `bot/engine/protocols.py`
  - 不修改 `OcrProvider`。
- `bot/index_manager/image_pipeline.py`
  - 不修改；继续执行中央文本归一化。

### 4.3 依赖

直接复用：

```text
httpx>=0.28.1
tenacity>=9.1.4
python-dotenv>=1.2.2
```

`pyproject.toml` 不增加依赖。

## 5. 请求数据流

```text
ocr(image_path)
  ├─ 校验图片存在
  ├─ 读取图片字节
  ├─ Base64 编码一次
  ├─ 获取 OCR_CONCURRENCY 信号量
  ├─ 获取或刷新 access token
  ├─ 按 BAIDU_OCR_TYPE 选择 endpoint 与表单参数
  ├─ POST application/x-www-form-urlencoded
  ├─ 检查 HTTP 状态和百度 JSON error_code
  ├─ 解析文本与置信度
  ├─ 过滤低置信度行
  └─ 按空白分割后以英文逗号拼接
```

service 生命周期内复用一个 `httpx.AsyncClient`。图片编码结果直接通过 `data={"image": base64_text}` 交给 httpx 做表单编码，不预先调用 `urllib.parse.quote_plus`，避免重复 URL 编码。

## 6. Access Token 生命周期

实例维护：

```text
_access_token
_token_expires_at
_token_lock
```

规则：

1. 第一次 OCR 时延迟请求 token，构造函数不执行网络 I/O。
2. OAuth 请求使用 `grant_type=client_credentials`、`client_id=BAIDU_API_KEY`、`client_secret=BAIDU_SECRET_KEY`。
3. 响应必须包含非空 `access_token` 和有效正数 `expires_in`。
4. 使用单调时钟计算过期时间。
5. 在真实过期前 60 秒把 token 视为失效。
6. 多个协程同时发现 token 失效时，通过 `_token_lock` 保证只刷新一次；取得锁后再次检查缓存，避免重复请求。
7. 百度 OCR 返回错误码 110 或 111 时，清除缓存并刷新 token，然后仅重发 OCR 一次。
8. 第二次仍返回 token 错误时抛出异常，不循环刷新。
9. token 只存内存，不持久化，不写日志。

## 7. 请求参数

### 7.1 共同参数

- 请求方法：POST。
- 主机：`https://aip.baidubce.com`。
- 查询参数：`access_token`。
- Header：`Content-Type: application/x-www-form-urlencoded`、`Accept: application/json`。
- Body：Base64 图片表单字段 `image`。

### 7.2 PP-OCRv6

默认关闭与现有 PaddleOCR provider 相同的预处理能力：

```text
useDocOrientationClassify=false
useDocUnwarping=false
useTextlineOrientation=false
```

### 7.3 传统六类

尽量请求：

```text
probability=true
```

含位置接口返回的位置、字符和多边形字段不进入 MemePilot 索引。

## 8. 响应解析

### 8.1 PP-OCRv6

解析结构：

```text
page_result[]
  ├─ lines[]
  └─ probability[]
```

新增独立纯解析函数，参考当前 `paddle_ocr.py::_extract_text()` 的设计风格：输入类型宽泛、逐层检查、异常结构降级为空或跳过、无网络副作用、便于独立测试。

规则：

1. `page_result` 不是列表时返回无文本。
2. 按页顺序和行顺序处理。
3. 每页 `lines` 不是列表时跳过该页。
4. 文本行必须是非空字符串。
5. 对应 `probability[i]` 是有效数值且低于 `OCR_TEXT_SCORE` 时过滤该行。
6. 置信度缺失、类型错误或数量不足时保留该文本行。
7. 忽略 `rec_polys`、`rec_boxes` 和可选 `words` 字段。

不直接复用现有 PaddleOCR `_extract_text()`，因为现有函数解析 `pruned_result.rec_texts/rec_scores`，百度 REST PP-OCRv6 解析 `page_result.lines/probability`；二者只复用函数边界和防御性解析风格。

### 8.2 传统六类

统一解析：

```text
words_result[]
  ├─ words
  └─ probability.average（可选）
```

规则：

1. `words_result` 不是列表时返回无文本。
2. 数组项必须是字典。
3. `words` 必须是非空字符串。
4. `probability.average` 是有效数值且低于 `OCR_TEXT_SCORE` 时过滤该行。
5. `probability` 缺失或结构异常时保留文字。
6. 忽略 `location`、`chars`、`poly_location`、`vertexes_location` 等字段。

### 8.3 文本归一化

所有模式最终执行等价于：

```python
",".join(" ".join(lines).split())
```

示例：

```text
[" 加 班 ", "心\t累"] → "加,班,心,累"
```

该结果仍会经过 `ImagePipeline` 的中央归一化，保证 provider 与入库契约一致。

## 9. 异常、重试与日志

### 9.1 异常类型

在百度模块内部定义以下 `RuntimeError` 异常层次：

```text
BaiduOcrError
├── BaiduOcrAuthError
├── BaiduOcrInvalidRequestError
├── BaiduOcrQuotaError
└── BaiduOcrTransientError
```

异常保留：

- `error_code`
- `error_msg`
- `log_id`
- `ocr_type`

最终由现有 `ImagePipeline` 归并为索引流程的 `OcrError`，不改变用户可见异常边界。

### 9.2 可重试情况

复用现有 `api_retry()`，最多尝试 3 次并指数退避：

- httpx 网络、连接和超时异常。
- HTTP 429。
- HTTP 5xx。
- 百度错误码 18（QPS 超限）。

底层 httpx 异常先包装为脱敏的 `BaiduOcrTransientError`，避免现有重试日志输出含 token 或 secret 的请求 URL。

### 9.3 不重试情况

- 错误码 17：每日额度耗尽。
- 错误码 19：总额度耗尽。
- 错误码 100：请求/token 参数非法。
- 其他鉴权、图片格式、尺寸、Base64 或请求参数错误。
- 未明确属于瞬时故障的百度业务错误。

错误码 110/111 使用第 6 节的单次 token 刷新机制，不计为普通无限重试。

### 9.4 日志脱敏

允许记录：

- OCR 模式和图片文件名。
- 调用耗时。
- 识别行数和过滤行数。
- 百度 `error_code`、`error_msg`、`log_id`。
- 重试次数。

禁止记录：

- API Key、Secret Key、access token。
- 含凭证的完整请求 URL。
- Base64 图片和完整表单。

`asyncio.CancelledError` 原样传播，不包装、不重试。

## 10. 测试设计

### 10.1 离线单元测试

新增 `tests/unit/engine/test_baidu_ocr.py`，使用 mock HTTP 行为，不访问真实百度 API。

覆盖：

- PP-OCRv6 多页、多行顺序。
- 传统 `words_result` 顺序。
- 两类置信度过滤。
- 缺失、错型、长度不一致的响应字段。
- 空响应和空白归一化。
- 7 种模式 endpoint 和请求参数。
- 图片 Base64 仅编码一次并由 httpx 表单编码。
- token 首次获取、复用、提前刷新和并发单次刷新。
- 110/111 刷新后重发一次。
- token 响应缺字段或字段非法。
- 网络、429、5xx、错误码 18 的重试。
- 17/19、鉴权和参数错误不重试。
- 重试错误文本不泄露凭证。
- `CancelledError` 原样传播。
- `close()` 关闭 HTTP client。

更新配置和 provider 测试：

- `OCR_PROVIDER=baidu`。
- 七种 `BAIDU_OCR_TYPE`。
- 缺失或非法类型回退 `pp_ocrv6`。
- provider 工厂创建百度 service。

### 10.2 删除联网 pytest

删除：

```text
tests/integration/test_openai_ocr_api.py
tests/integration/test_index_manager_api.py
```

保留使用 `AsyncMock` 的 `tests/unit/engine/test_openai_ocr.py`。

### 10.3 手动联网测试脚本

新增 `scripts/test_baidu_ocr.py`，不由 pytest/CI 收集。

命令：

```bash
# 默认 PP-OCRv6
uv run python -m scripts.test_baidu_ocr image.png

# 指定模式
uv run python -m scripts.test_baidu_ocr image.png --type webimage

# 顺序测试全部七种模式
uv run python -m scripts.test_baidu_ocr image.png --all
```

行为：

1. 使用 `python-dotenv` 加载项目根目录 `.env`。
2. 校验图片与百度凭证。
3. 默认模式 `pp_ocrv6`。
4. `--type` 只接受七种合法值。
5. `--all` 顺序调用七种模式，运行前提示会消耗 7 次 OCR 额度。
6. 每种模式打印模式、成功/失败、耗时和标准化文本。
7. 失败时打印脱敏后的错误码、消息和日志 ID。
8. 任一模式失败时最终退出码非 0。
9. 始终关闭 service。

## 11. 文档与部署更新

需要更新：

- `.env.example`
  - 百度凭证、默认模式和七种合法值。
- `docker-compose.yml`
- `docker-compose.build.yml`
  - 透传三个百度环境变量。
- `README.md`
  - provider 列表、配置说明和手动联网验证命令。
- `docs/PRD.md`
  - OCR 技术栈、配置、安全、边界和测试说明。
- `CONTEXT.md`
  - 百度 OCR、PP-OCRv6、`BAIDU_OCR_TYPE` 等术语。

## 12. 验证标准

实现完成后运行：

```bash
uv run pytest tests/unit/engine/test_baidu_ocr.py -v
uv run pytest
uv run ty check
uv run ruff check .
uv run ruff format --check .
```

验收条件：

- 七种模式均有离线请求和解析覆盖。
- 现有测试全部通过。
- ty 无错误。
- ruff lint 和格式检查通过。
- pytest 不再包含 DeepSeek/OpenAI OCR 真实联网测试。
- 百度联网脚本存在但不自动执行。
- 用户可使用真实凭证分别验证单模式或全部七种模式。

## 13. 官方资料与已知不一致

参考：

- [百度 OCR 官方文档目录](https://ai.baidu.com/ai-doc/index/OCR)
- [PP-OCRv6](https://ai.baidu.com/ai-doc/OCR/6mncwkr9c)
- [通用文字识别标准版](https://ai.baidu.com/ai-doc/OCR/zk3h7xz52)
- [通用文字识别标准含位置版](https://ai.baidu.com/ai-doc/OCR/vk3h7y58v)
- [通用文字识别高精度版](https://ai.baidu.com/ai-doc/OCR/1k3h7y3db)
- [通用文字识别高精度含位置版](https://ai.baidu.com/ai-doc/OCR/tk3h7y2aq)
- [网络图片文字识别](https://ai.baidu.com/ai-doc/OCR/Sk3h7xyad)
- [网络图片文字识别含位置版](https://ai.baidu.com/ai-doc/OCR/Nkaz574we)

截至 2026-07-18，PP-OCRv6 官方页面标题和产品描述为 PP-OCRv6，但实际 endpoint 仍为 `/pp_ocrv5`，且字段表对 `lines`、`probability` 的类型标注与 JSON 示例不一致。真实探测中 `/pp_ocrv6` 返回 `error_code=3 Unsupported openapi method`，而 `/pp_ocrv5` 能识别 POST 路由。因此用户配置名保持 `pp_ocrv6`，底层调用 `/pp_ocrv5`；响应解析以实际 JSON 的数组结构为准。
