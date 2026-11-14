# 镜像更新日志

本文件按镜像分别记录更新、版本引用和部署影响。镜像构建成功、整套服务部署成功和真实教学效果验收是不同结论。

## 未发布 · 共享题库读取优化

### API 镜像

- 汇总、列表、详情和教师库存查询复用已核验的进程内题库快照；每次复用检查来源、语义、引用证据和文件状态，变化时重新核验。
- 翻页、搜索和打开单题不再重复完整核验未变化的题库；无管理员调整时不再把全库转 JSON 并重新构建。
- 管理员调整仍逐次读取最新数据库状态；主动检查、导入发布及恢复版本强制完整核验，不回退旧成功结果。
- 缓存有容量限制并合并并发冷加载；返回独立副本，防止调用方修改污染其他请求。

### 业务 Worker 镜像

- 同一轮完整加载只读取一次相同 Word 原件，ZIP 与原生对象校验绑定到同一份已验证字节。
- 库存与组卷读取可复用未变化的基础题库；进度、配额、去重、版本与导出规则继续执行。

### Tools／PostgreSQL 镜像

本批不修改工具构建输入、工具镜像引用、数据库镜像或表结构。不需要重建 Tools，也不需要迁移数据卷。

本机真实 200 题库的热读取中位数：汇总约 0.43 秒、第一页约 0.42 秒、详情约 0.44 秒；先前单轮基线分别为 3.66、3.84、7.36 秒。首次请求仍需完整核验，结果保持 176 题可抽取、24 题受阻。上述不含真实 PostgreSQL／网络，不是 ROCK 5B 实测。证据和验证边界见 [本批验收记录](docs/acceptance/bank-read-performance-v1.md)。本节尚未发布。

## 2026-11-14 · API、业务 Worker 与 Tools 分离

源码提交：[`4713341`](https://github.com/contr4l/Teassistant/commit/4713341335f611cfa8075b3baf0d613fbabac211)。本次在 `d715f8e` 的轻量 API／完整 Worker 分拆基础上，将业务 Worker 与重型工具服务进一步分开。

### 发布状态

以下为编写本文件时查询到的状态，后续以对应 Actions 运行结果为准。

| 镜像 | Compose 服务 | 构建与发布状态 |
| --- | --- | --- |
| API | `api`、一次性 `init` | [业务 CI 成功](https://github.com/contr4l/Teassistant/actions/runs/43568727893)，镜像已发布 |
| 业务 Worker | `worker` | 与 API 由同一业务 CI 验证和发布成功 |
| Tools | `tools` | [独立流水线成功](https://github.com/contr4l/Teassistant/actions/runs/43568727816)，可先拉取 |
| PostgreSQL | `postgres` | 沿用官方镜像，本次未修改数据库版本声明 |

### 1. API 镜像

镜像仓库：`ghcr.io/contr4l/teassistant`。

本次已发布版本引用：

```text
ghcr.io/contr4l/teassistant:api-4713341335f611cfa8075b3baf0d613fbabac211
```

- 继续承载前端网页、登录、学生管理、材料上传、查询、管理员设置和反馈接口。
- 前端构建产物位于镜像内，由 API 提供页面及静态资源，与后端接口共用一个端口；无需单独运行 Node 或前端容器。
- 保留 Alpine 轻量依赖组、PDF 上传校验和未压缩大小不超过 **200,000,000 bytes** 的 CI 门禁。本次实际镜像大小须以本轮 CI 结果为准。
- 本次没有调整前端 UI 或教师使用流程；主要变化是配合独立业务 Worker／Tools 的构建和集成验证。

环境变量：`TEASSISTANT_API_IMAGE`；本地构建目标为 `TEASSISTANT_API_TARGET=api`。

### 2. 业务 Worker 镜像

镜像仓库：`ghcr.io/contr4l/teassistant`。

本次已发布版本引用：

```text
ghcr.io/contr4l/teassistant:4713341335f611cfa8075b3baf0d613fbabac211
```

- 从原来的完整 Worker 中分离业务执行与重型系统工具，新增 Docker `worker` 构建目标。
- 保留数据库任务领取、租约与版本校验、DeepSeek 调用、计费、学情分析、题库加工编排、抽题规则和最终产物发布。
- 保留业务 Python 依赖及公式处理模块；Chromium、LibreOffice、Pandoc 和 XeLaTeX 的工具执行通过内部 HTTP 交给 Tools。
- 通过共享工作卷交接文档、图片、临时文件和转换结果，保留既有公式、PDF、图像及排版校验。
- 显式配置工具服务后，连接失败、协议不匹配或转换失败均明确报错，不自动回退到本地工具。宿主开发环境未配置远程工具时继续支持原有本地路径。
- 普通业务代码、流程或排版编排变更不改变工具镜像的内容标签；业务 Worker 自身仍需更新，不能据此承诺在途任务无损热升级。

环境变量：`TEASSISTANT_IMAGE`；本地构建目标为 `TEASSISTANT_WORKER_TARGET=worker`。Compose 为该服务设置 `TEASSISTANT_TOOLS_URL=http://tools:8001`。

**兼容性变化：**本次既有的无前缀 SHA 标签指向业务 Worker，而不是旧的全工具运行环境。首次升级必须同步新版 Compose，不能仅在旧 Compose 中替换镜像标签。

### 3. Tools 镜像

镜像仓库：`ghcr.io/contr4l/teassistant-tools`。

已成功发布的版本引用：

```text
ghcr.io/contr4l/teassistant-tools:sha256-78fd48f3f8efb3a2c6a3787cc33b0e9c06ab7e7887e05e464777173bca9598e7
```

- 新增独立工具服务，保留 Chromium、中文字体、LibreOffice、Pandoc、XeLaTeX 和 Playwright 运行环境。
- 提供 `tools-v1` 接口：Word 转 PDF、LaTeX 编译、公式转 Word 和 HTML 截图。
- 不包含业务代码、前端、业务数据库连接或 DeepSeek 配置；生产 Compose 中仅连接内部工具网络，不映射主机端口。
- 约束文件访问范围，拒绝路径逃逸；截图禁止外部网络及本地文件访问；执行使用固定工具参数、有界请求及输出和超时控制。
- 修复 XeLaTeX 符号链接执行时丢失入口名的问题，并防止以连字符开头的 TeX 文件名被当成命令选项。
- 独立 Dockerfile 和依赖版本文件；镜像标签由实际工具构建输入的内容摘要生成，排除 Python 缓存等生成文件。
- 独立流水线构建并验证 AMD64／ARM64，通过真实转换及截图冒烟后发布多架构索引；已有内容标签只校验，不重新推送。

环境变量：`TEASSISTANT_TOOLS_IMAGE`。当前正式版本使用上述内容标签，没有 `latest` 或 `v1` 短标签；`sha256-…` 是 tag，不是 `@sha256:…` 格式的镜像 digest。

可提前下载，不会重启现有服务：

```bash
docker pull ghcr.io/contr4l/teassistant-tools:sha256-78fd48f3f8efb3a2c6a3787cc33b0e9c06ab7e7887e05e464777173bca9598e7
```

### 4. PostgreSQL 官方镜像

继续使用 `postgres:17-bookworm`。本次没有更改该声明，也没有增加 Redis 服务；持久任务仍保存在 PostgreSQL 中。不应以清理旧镜像或迁移部署为由删除数据库数据卷。

### 构建和部署变化

| 更新内容 | API／业务 Worker | Tools |
| --- | --- | --- |
| 普通业务源码或前端更新 | 由业务 CI 构建、验证并发布 | 复用已发布的固定内容版本 |
| 工具代码、工具依赖或 Dockerfile.tools 更新 | 业务发布前验证对应 Tools 版本可用 | 独立构建、验证并发布新内容标签 |
| 管理员在网页修改提示词、模型密钥或额度 | 运行时配置，无需重建镜像 | 无需重建镜像 |

API 和业务 Worker 按同一提交配套升级；Tools 独立固定版本。所有正式发布索引均要求 Linux AMD64 与 ARM64。fork PR 为避免使用私有仓库凭据，会明确在隔离构建环境中从源码构建工具镜像进行检查。

已有部署升级顺序：

1. 等待 Tools 与业务 CI 均成功；首次工具发布和业务发布并行时，以最终成功结果为准。
2. 同步新版 `compose.yaml` 和部署脚本。旧压缩包安装目录不一定有 `.git`，不能直接假定支持 `git pull`。
3. 在原 `.env.deploy` 中设置配套的 API、业务 Worker 和 Tools 镜像引用；保留原项目名、数据库密码、端口、题库路径及数据卷。
4. 执行下面命令，拉取并启动预编译镜像；不需要在板子编译。

```bash
python3 scripts/deploy.py check
python3 scripts/deploy.py pull
python3 scripts/deploy.py up
python3 scripts/deploy.py status
```

首次源码构建时，`python3 scripts/deploy.py build-tools` 单独构建工具镜像；普通 `build`、`up --build` 和 `bootstrap --build` 只构建业务 Worker／API。日常业务升级保留 `TEASSISTANT_TOOLS_IMAGE`，不重复构建工具环境。

前端默认监听宿主 `127.0.0.1:8000`。局域网访问可在原配置中设置 `TEASSISTANT_BIND=0.0.0.0` 和所需的 `TEASSISTANT_PORT`，执行 `up` 后通过 `http://<服务器IP>:<端口>` 访问。密钥可在管理员界面配置，或通过部署环境变量提供；网页设置优先。

### 验证和限制

- 本地全量 `make check`：**905 项测试通过**，1 条既有弃用 warning；发布前相关 34 项复查通过。
- 已通过真实 HTTP 调用实际工具，验证中文 Word／PDF、原生数学、LaTeX、几何图、截图及外部请求拒绝；使用的是明确标记的合成材料。
- Tools 的双架构构建、原生工具冒烟和发布已由上述独立流水线成功完成。
- API／业务 Worker 的云端大小门禁、AMD64／ARM64 混合容器冒烟与正式发布均已由对应业务 CI 成功完成；这不等同于后续“未发布”改动也已完成云端验证。
- 本文件不表示 ROCK 5B 已升级，也不表示真实学生识别质量或老师教学价值已通过验收。

相关说明：[部署指南](docs/deployment.md)、[ROCK 5B 指南](docs/rock5b-deployment.md)、[本批合同与验收记录](docs/acceptance/tools-worker-v1.md)。
