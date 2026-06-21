# 项目当前完成情况

- [x] `bot/engine/keyword_searcher.py` — 关键词模糊搜索模块（partial_ratio 单阶段匹配，阈值 >= 60，Top 10）
- [x] `bot/logging_config.py` — 日志滚动配置模块（RotatingFileHandler + StreamHandler，文件 DEBUG、控制台 INFO，单文件 <= 1MB 保留 1 备份）
- [x] `bot/engine/index_manager.py` — 索引增删改查模块（ujson 解析、原子写入、空洞 ID 复用、text_hash 一致性校验、文件系统同步、asyncio 锁管理；`sync_with_filesystem` 已改造为三阶段并行：①删除已不存在的图片 ②重建阶段——对 text_hash 不一致或 embedding 缺失的已有条目用当前 text 并行重建 embedding（统一覆盖「用户改 text」增量重建与「embeddings.json 损坏」全量重建），不重新 OCR ③新增图片并行 OCR+embed 后按文件名升序串行三分类——无文字移至 `meme_no_text/`、去重键命中已有条目或靠前新图时删除重复新图、正常新增；去重基于「去除所有空白字符的去重键」实时计算不落盘，`winner_keys` 赢家集合增量判定；并发上限由 `sync_concurrency`/`SYNC_CONCURRENCY` 控制，默认 5；`add_entry` 返回 `AddResult`，内联无文字移图与去重覆盖（复用旧 ID、删旧图）；`SyncResult` 新增 `deduped`/`no_text_moved` 字段；`_embeddings_stale` 标志由重建阶段消费清除）
- [x] `bot/engine/ocr_service.py` — DeepSeek-OCR 封装（硅基流动 vision API，base64 图片输入，异步 OCR）