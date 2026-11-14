# 轻量 API 镜像 ≤200 MB：合同与验收

## 用户确认与范围

用户选择：**主应用镜像 ≤200 MB，允许重型工具放独立 Worker 镜像**。门槛为 Docker `.Size` 累计未压缩层大小不超过 **200,000,000 bytes**（十进制 MB），不是 200 MiB，也不是压缩传输大小。完整部署仍包括 Worker、PostgreSQL、业务数据和题库，整体磁盘占用不承诺 200 MB。

对应账号、学生上传、FR-02 题库加工、FR-15 导出及部署交付。API/init 使用 Alpine 轻量镜像，保留图片/PDF 上传校验、数据库与管理员题库查询；Worker 使用现有 Debian 全功能镜像。沿用同一数据库任务队列、同一源码版本、数据卷与题库挂载，不改任务数据合同、不新增网络服务、不减少 Word/PDF/LaTeX/Chromium 能力。

## 交付合同与归属

- `Dockerfile --target api`：仅 API、初始化和账号 CLI；仅安装锁定的 api 依赖组，不含 LibreOffice、Chromium、Pandoc、TeX 或 Playwright。默认最后 stage `runtime` 保持完整 Worker 兼容性。
- 原 `TEASSISTANT_IMAGE` 保持完整 Worker 镜像引用；新增可选 `TEASSISTANT_API_IMAGE`。未设置时 API/init 回用旧完整镜像，保持旧配置可启动；新生成配置默认指定 `teassistant:api-local`，构建时显式构建 API 和 Worker 两种 target。
- Compose 的 worker 显式使用 runtime target；api/init 使用 `TEASSISTANT_API_TARGET`（新配置为 api，旧配置缺省为 runtime），避免旧配置的同一镜像名被不同构建目标覆盖。发布镜像提供匹配版本的 `api-*` 和既有完整镜像标签，两者都保留 AMD64/ARM64；轻量主镜像必须运行独立的体积门禁和与完整 Worker 配合的容器集成验证。
- 总集成负责 Compose、部署脚本、CI、大小门禁、集成检查与文档；Luna 实现者负责 Dockerfile、api 依赖组与 uv.lock；独立检查负责接口/导入与反例，不碰另一会话的容器冒烟修正。

## 验收要求

不得以缺失功能、运行时自动下载工具、空镜像或只计算应用源码达到目标。检查真实镜像 `.Size ≤200,000,000`；API 配合完整 Worker 跑登录、学生、图片和 PDF 上传、任务持久化、反馈及原有 Word/LaTeX/Chromium 冒烟。缺构建网络或 ARM 环境时明确尚未通过，不将估算记为实测。

## 执行证据

- 本地官方 ARM64 `python:3.13-alpine3.23` 镜像经宿主 HTTPS 获取，逐层校验下载 SHA256、解压层 diff_id 及配置摘要后导入 Docker；UV 同样获取锁定 0.9.9 版本。本机 Docker VM 的外部 DNS 不可用，未改宿主或 Docker 网络设置。
- 按 api 组导出锁定且带 hash 的 requirements，选择 CPython 3.13/musllinux aarch64 对应的 **27 个 wheel**，逐个按 uv.lock 校验 SHA256；Alpine 官方签名 APK 离线安装 ca-certificates/libgcc/libstdc++，未允许不受信签名。前端执行 `pnpm --dir frontend build` 成功。
- 基于实际 Dockerfile 的 api 阶段制作离线验证上下文：前端用本地构建 dist 替代联网 Node 构建，依赖安装用 `uv venv`＋带 hash 的同组锁定 requirements 离线安装替代联网 `uv sync`，APK 预先复制后离线安装；运行阶段源码、依赖版本、环境、用户和入口保持对应。该验证镜像额外含预取 APK 的层，不能冒称它是未经改写的标准联网 Dockerfile 实建。
- `teassistant:api-slim-validation` 构建成功：镜像 ID `sha256:ec3135cbf440a53c5d353abb1b7134de23968cbc544058e99667a8bbbbb17592`，Docker `.Size` **158,017,930 bytes**（158.02 MB／150.70 MiB），满足本地验证镜像的 200,000,000 bytes 门槛。记录在忽略目录 `work/lean-api/image-metadata.json` 与 `size-report.md`。不是压缩下载量，也不是整套部署大小。
- 同步最终修正的冒烟脚本后，重建 `teassistant:api-slim-final`：ID `sha256:31905355a2effb6d5c1cffd9375abf060eb75fb49d3b2ecc6cb7d534c0607d17`，**158,017,873 bytes**，体积门禁通过，默认 entrypoint `--check-config` 通过；再次在该最终验证镜像中实际验证登录、创建、PNG/PDF 上传通过，结果在 `work/lean-api/result-final.json`。仅用于本地验证，尚未发布。
- 容器 `--network none --rm` 使用 UID 10001 实际导入 API、PDFium、psycopg、Pillow、lxml、networkx、olefile 成功；无 Playwright、pypandoc、mathtypejx。独立检查者在该镜像中以合成账号及临时 SQLite 实际验证登录 200、学生与考试创建 201、PNG 和合法单页 PDF 上传 201。不会用缺失 PDFium 后拒收合法 PDF 来满足体积目标。
- 独立检查发现并修复：空 Worker 镜像配置触发 Compose 的默认值回退时，仍需拒绝 API 与 Worker 同 tag；CI 在登录推 staging 前严格验证受保护 semver tag；远端发布后的 manifest descriptor 映射必须与发布前保存的映射相同。两类镜像均要求 amd64/arm64 正确，API 超限阻止发布。
- 原有容器冒烟子进程的严格 6 字段响应合同保持。初次全量检查曾因新增 PNG/PDF 计数字段与既有测试响应不兼容产生 `1 failed, 859 passed`；修正为子进程仍返回原合同、逐次强校验两种上传 HTTP 201，父报告新增 `uploaded_asset_types`，保留旧调用方与严格字段/类型校验。最终 `make check` **Ruff 通过、863 passed**，1 条既有 Starlette/httpx 弃用警告，用时 110.13 秒；前端 TypeScript/Vite 构建也通过。日志在忽略目录 `work/lean-api/make-check-final.log`、`frontend-build.log`。
- 协作实际模型 `gpt-6-luna/high`；Dockerfile/依赖、CI、混合冒烟分别归属不同实现者，并交叉检查；未将其记为不存在的 GPT-6.1 Luna。原先的网络冒烟修复保留，未覆盖另一会话工作。
- 独立真实 PostgreSQL 验证通过：最终 ARM64 API 镜像配合缓存的 ARM64 `postgres:17.2`，在无宿主端口映射的独立内部网络中实际连接 psycopg/PostgreSQL，完成合成管理员登录、学生/考试创建、PNG 与单页 PDF 上传及内容读回、分析任务持久入队和反馈持久化；PDF 上传实际调用 musl PDFium。结果与脚本在 `work/lean-api/independent-postgres-result.json`、`independent-postgres-smoke.py`，不含密码/DSN。随机容器、数据卷和网络均由任务 finally 清理成功；未动现有服务。

尚未实测标准网络 Dockerfile 构建、AMD64 新镜像、GitHub Actions 发布，以及轻量 API＋完整新 Worker 的 Word/LaTeX/Chromium 联合实机冒烟；Worker 功能实现保留不代表本轮全套镜像已验收。未运行真实 DeepSeek，未部署到 ROCK 5B，未提交推送。

## 本轮合并与发布前检查（2026-11-14）

用户随后明确要求将精简一并提交、推送。集成基于已通过双架构全功能镜像构建与发布的 `4f5134c`，保留隔离网络内 HTTP 客户端、named volume 产物清理、成功报告提交时序、Chromium 受限诊断及 `libatomic1`/驱动预检查；本批同时纳入初始全功能镜像清理和后续轻量 API 分拆。

- 当前最终工作区 `make check`：Ruff 通过，875 passed、1 条既有 Starlette/httpx 弃用 warning，146.24 秒；日志位于忽略目录 `work/lean-release/make-check.log`。
- `UV_CACHE_DIR=.cache/uv uv lock --offline --check` 与 `pnpm --dir frontend typecheck` 均通过。前端源文件本批没有变化，原有 Vite 构建结果沿用前述证据，云端仍重新构建。
- 实际 GPT-6 Luna / high 独立只读复查并执行相关 64 项反例通过；workflow YAML 解析及 22 段 run 脚本 `bash -n` 通过。核对旧配置回退、API 200,000,000 bytes 门禁、配套双架构镜像的本地/远端 descriptor 映射校验，以及重型工具与已完成修复均保留，未发现阻塞。
- 补齐部署说明：已有单镜像部署首次切换，先同步本次 Compose 和部署脚本，再向原配置补充 API 镜像键；保留原账号、密码、项目名和数据卷。

本节是提交前集成证据；875 项宿主检查与本地 ARM64 验证镜像体积不代替标准 Dockerfile 的双架构云端构建。新发布须以本次 Actions 的体积门禁、混合容器冒烟及最终索引校验全部成功为准，ROCK 5B 尚未执行这次升级。
