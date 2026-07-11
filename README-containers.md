# MemePilot

MemePilot 是一个部署在 Docker 中的 QQ 表情包机器人，帮你从本地表情包库中快速找到想要的表情包。

> 本页为 Docker Hub 仓库的精简说明。完整文档（功能示例、架构、全部配置项、测试等）见 [GitHub 仓库 README](https://github.com/northhalf/meme-pilot#readme)。

## 命令速查

| 命令 | 短命令 | 说明 | 群聊 |
| --- | --- | --- | --- |
| `/help` | `/h` | 查看命令帮助 | ✅ |
| `/query <关键词> [@说话人] [#标签]` | `/q` | 关键词/说话人/标签组合检索 | ✅ |
| `/rand [关键词]` | - | 随机 10 个，回复 0 换一批 | ✅ |
| `/sim <描述>` | - | 按语义相似度全库召回 | ✅ |
| `/info [id]` | - | 查看状态统计或指定表情包详情 | ✅ |
| `/cancel` | `/c` | 取消当前操作 | ✅ |
| `/ai <描述>` | - | 按自然语言描述匹配 | 私聊 |
| `/add [speaker <tags>]` | `/a` | 聊天添加一张表情包 | 私聊 |
| `/addtag <id> <tag...>` | `/at` | 为表情包添加标签 | 私聊 |
| `/del <id>` | `/d` | 删除表情包 | 私聊 |
| `/edittext <id>` | `/e` | 编辑 OCR 文本 | 私聊 |
| `/setspeaker <id>` | `/sp` | 设置说话人 | 私聊 |
| `/refresh` | `/r` | 增量更新索引 | 私聊 |

群聊命令支持 @bot 触发；`/cancel` 私聊与群聊均可。

## 快速部署

```bash
git clone https://github.com/northhalf/meme-pilot.git
cd meme-pilot
cp .env.example .env
# 编辑 .env 填入 QQ_ACCOUNT、AUTHORIZED_USER_IDS、DEEPSEEK_API_KEY 等
# 把表情包图片（.jpg/.png/.gif/.webp/.bmp）放入 memes/ 目录（不存在时启动会自动创建）
docker compose up -d
docker compose logs -f bot
```

首次启动自动扫描 `memes/` 目录建立索引；启动后访问 NapCat WebUI（`http://127.0.0.1:6099`）扫码登录 QQ。

完整配置项（Embedding/OCR provider 切换、并发上限、超时等）见 `.env.example` 与 [GitHub README](https://github.com/northhalf/meme-pilot#readme)。

## 隐私说明

- 表情包图片始终本地存储。
- OCR 文本按 `OCR_PROVIDER` 发送：默认 `rapidocr` 本地推理无需联网；`paddle` 调百度云 API；`deepseek` 调 OpenAI 兼容视觉 OCR 服务。
- Embedding 由 `EMBEDDING_PROVIDER` 指定服务（默认 `openai`，OpenAI 兼容 API）；LLM 精排调用 DeepSeek。

## 镜像标签

- `northhalf/meme-pilot:latest`：最新 main 分支构建
- `northhalf/meme-pilot:sha-<commit>`：对应提交的构建

---

源码、Issue、贡献指南：<https://github.com/northhalf/meme-pilot>
