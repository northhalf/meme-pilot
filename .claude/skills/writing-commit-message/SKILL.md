---
name: writing-commit-message
description: Use when the user requests to write a git commit message for the meme-pilot project. Load this skill to generate the commit message and write it to .tmp/commit_message.md
---

# Git Commit Message 规范

## 标题格式

```
<type>(<scope>): <中文摘要>
```

- **type**：`feat` | `fix` | `perf` | `refactor` | `docs` | `test`
- **scope**：`engine` | `plugins` | `docs` | `config` | `integration`
- **摘要**：一句话说明做了什么，动词开头，中文

示例：
- `feat(engine): 实现 image_optimizer 图片无损压缩模块`
- `refactor(engine): 提取共享协议、包级导出与相对导入重构`
- `docs: 修正跨文档不一致（模型名、环境变量、Python 版本）`

## 正文结构

正文按以下段落组织，每段以加粗标签开头：

### 功能/修复说明（首段）

简要说明本次提交的目的和核心变更，2-4 句。

### 工程变更

以 `工程变更：` 开头，列出所有新增/修改/删除的文件及具体改动：

```
工程变更：
- 新增 bot/engine/image_optimizer.py：ImageOptimizer 类 + OptimizeResult
  数据类；JPEG 去 EXIF 元数据 + 高质量重编码（quality=95）...
- 修改 bot/engine/__init__.py：导出 ImageOptimizer 和 OptimizeResult
- 修改 bot/engine/index_manager.py：__init__ 新增 optimizer 参数
```

### 测试

以 `测试：` 开头，说明测试结果：

```
测试：
- 新增 tests/unit/engine/test_image_optimizer.py：11 个单元测试
- uv run pytest：237 passed（215 unit + 22 integration）
```

纯文档变更可写：
```
测试：
- 仅文档变更，未运行代码测试
```

### 文档

以 `文档：` 开头，列出更新的文档文件。

**排除规则**：`docs/superpowers/` 目录下的文档变更不写入此段落（属于 skill 插件文档，非项目文档）。

```
文档：
- 更新 docs/process.md：追加 image_optimizer.py 完成记录
```

## 完整示例

```
feat(engine): 实现 image_optimizer 图片无损压缩模块

实现 bot/engine/image_optimizer.py，对表情包图片执行无损压缩，
在进入索引前减小文件体积。使用 Pillow 库处理 JPEG/PNG/WebP/GIF
四种格式，BMP 跳过。

工程变更：
- 新增 bot/engine/image_optimizer.py：ImageOptimizer 类 + OptimizeResult
  数据类；JPEG 去 EXIF 元数据 + 高质量重编码（quality=95,
  optimize=True, progressive=True），PNG optimize=True 真正无损，
  WebP lossless 模式（quality=80, method=6），GIF 保留动画属性
  （duration/loop/transparency）去除冗余元数据；BMP 跳过返回
  skipped=True；原子写入（.tmp + os.replace），压缩后反而变大时
  保留原文件返回 skipped=True
- 新增 Pillow 生产依赖（uv add Pillow, v12.2.0）
- 修改 bot/engine/__init__.py：导出 ImageOptimizer 和 OptimizeResult

测试：
- 新增 tests/unit/engine/test_image_optimizer.py：11 个单元测试，
  覆盖 OptimizeResult 创建/frozen/skipped、不支持格式抛 ValueError、
  BMP 跳过、文件不存在、JPEG 压缩/去 EXIF、PNG/WebP/GIF 压缩、
  GIF 动画保留
- uv run pytest：237 passed（215 unit + 22 integration）

文档：
- 更新 docs/process.md：追加完成记录
```

## 完整流程（强制）

写 commit message 前必须按以下顺序执行：

### 1. 阅读项目文档了解上下文

读取 `CLAUDE.md` 中"必读文档"指定的文件，理解当前项目结构和规范：

- `docs/PRD.md` — 需求与功能边界
- `CONTEXT.md` — 术语与领域概念
- `README.md` / `.env.example` / `docker-compose.yml` — 部署与环境

### 2. 查看历史提交作为参考

```bash
git log -3 --format="%H%n%s%n%b%n---"   # 最近 3 次完整提交信息
```

参考历史提交的 type/scope 选择、摘要风格、正文结构。

### 3. 用 git diff 确定变更范围

```bash
git diff --name-only          # 工作区变更
git diff --cached --name-only # 暂存区变更
```

分类变更文件：代码（`bot/`、`tests/`）、配置（根目录）、文档（`docs/`）。

### 4. 分析变更在项目中的作用

- 判断变更类型：feat / fix / perf / refactor / docs / test
- 理解变更在整体项目架构中的位置和作用（属于哪个模块、解决什么问题、带来什么改进）

### 5. 生成 commit message

基于以上分析，按本规范生成提交信息。

## 输出方式（强制）

生成提交信息后，**必须**写入 `.tmp/commit_message.md` 文件，**覆盖写入**（不追加）。

流程：
1. `mkdir -p .tmp`（确保目录存在）
2. 用 Write 工具将完整提交信息写入 `.tmp/commit_message.md`（每次覆盖）

**禁止**：不在终端输出提交信息、不直接调用 `git commit`。只写文件。

用户自行审核 `.tmp/commit_message.md` 并提交。

## 禁止事项

- 禁止自行 `git add` 或 `git commit`，提交必须经用户审核
- 禁止在标题中使用英文（除 type/scope 外）
- 禁止省略正文结构段落标签
