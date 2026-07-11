# CLAUDE.md

本文件为 Claude Code 提供项目开发指引。

## 严禁事项

- 禁止自行在**main分支**进行 `git add`、`git merge` 或 `git commit`，该分支上的提交和合并必须经用户审核。
- 撰写 specs 后不可跳过用户审阅步骤。

## 必读文档

- 修改需求、架构、命令交互、索引或权限逻辑前，先看 `docs/PRD.md`。
- 修改术语、领域概念或用户可见命名时，必须对照 `CONTEXT.md` 保持术语一致。
- 修改部署、环境变量或操作说明时，检查 `README.md`、`.env.example` 和 `docker-compose.yml`。
- 调用已有模块或新增模块交互前，优先查阅 `docs/api/API.md` 中的参数签名与返回值说明；仅在文档不准确或信息不足时再阅读源码。
- 每实现一个模块后，更新 `docs/api/API.md`（对外接口）。

## 代码风格

- Python 函数使用 Google 风格 docstring，内容用中文。
- 函数参数、返回值需类型标注。
- 保持现有中文注释和用户提示风格。
- 不使用 `from __future__ import annotations`，项目使用 Python 3.12，已原生支持类型标注延迟求值。

## 常用命令

### Docker

```bash
docker compose up -d
docker compose logs -f bot
docker compose down
docker compose build bot && docker compose up -d bot
```