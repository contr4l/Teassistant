# 容器与持续集成执行记录

2026-11-12，依据[合同](deployment-v1-contract.md)，对应 DEV-101/105/502、FR-15 和教师网页闭环。本轮完成可评审的部署工程与离线验证；用户在 registry 超时后明确要求“没有，先完成配置和离线检查”，据此收口。**生产镜像构建及容器运行尚未通过**，不能记为生产部署或 P5 出口通过。

总设计、集成和独立检查为 Sol/high；两路实施使用实际可用的 gpt-6-luna/high，未写作 6.1 Luna。本批未调用业务模型，未提交、推送、发布镜像或操作远程服务器，保留已有未提交业务和 LaTeX 改动。

## 交付内容

| 范围 | 文件与行为 |
| --- | --- |
| CI | `.github/workflows/ci.yml`：后端锁定依赖与真实导出工具测试、Node/pnpm 锁定前端检查、linux/amd64 镜像构建及合成容器冒烟、受信 GHCR 发布 |
| 镜像 | `Dockerfile`：前端、Python 依赖和运行期多阶段；LibreOffice、中文字体、XeLaTeX；UID/GID10001；OCI 来源/提交标签 |
| 构建上下文 | `.dockerignore`：排除原卷、学生资料、凭据、runtime/work/cache 等，精确保留三份受控 DOCX 打印模板 |
| 配置与运行 | `compose.yaml`、`.env.deploy.example`、`scripts/deploy.py`：生成私有随机配置，检查、构建、拉取、启动、状态、停止、交互管理员创建 |
| 入口与健康 | `container_entrypoint.py`、`container_healthcheck.py`：显式数据库与 DeepSeek 路由，参数数组 exec；API HTTP+DB，worker PID1身份+DB |
| 容器冒烟 | `container_smoke.py`、`container_smoke_fixture.py`：隔离项目/卷、无模型密钥、合成 HTTP/队列/反馈、Word 和 LaTeX 工具链实测入口 |
| 文档 | `docs/deployment.md`、README、网页说明和架构/计划同步；Makefile 提供对应便捷入口 |

默认单机为 PostgreSQL、API、独立数据库 Worker 三个常驻服务，init 先一次性建表并初始化默认提示词。API/Worker 等 init 完成，避免首次并发建表；Redis/Celery 保留为可选扩展代码，不再是默认部署依赖。数据卷、只读题库和镜像分别管理，默认回环监听，外网 HTTPS 仍需服务器实际配置验证。

`.env.deploy` 与开发 `.env` 分开。init 以 O_EXCL 和 0600 创建，数据库密码随机且 URL 安全；现有配置不覆盖。缺 DeepSeek 密钥/题库允许先验证账号和学生管理，识别/组卷对应功能不伪造成功。配置检查不输出 resolved Compose 或真实秘密；stop/status 在题库挂载丢失时仍能用于恢复操作。

## 实际检查与修复

- 最终 `make check`：**650 passed，32.05 秒**，ruff 通过；1 条已有 Starlette/httpx 弃用警告，日志 `make-check-final-v2.log`。首轮 648 项日志保留，最终新增两条嵌套私有路径反例。独立验收复跑本批 44 项，全部通过。
- `make check-web`：TypeScript 检查及 Vite 生产构建成功。
- 使用真实 Docker Compose 解析合成配置，13 条展开后的结构检查通过；配置辅助 CLI 的 init/check 也实际执行。测试配置和随机密码只留忽略目录。
- 使用 scratch 镜像执行实际构建上下文检查，不拉基础镜像、不启动容器。首轮只查根路径，漏过了 `backend/work` 内 189 个临时条目（含目录），原“零泄漏”结论撤回并保留失败 tar。补齐递归排除后，独立用任意路径组件审计重新验证：最终上下文 2.698 MB、219 文件/240 条目，零私有路径命中；8 个业务/前端源码及受控模板目标摘要一致。新证据 `review-context-corrected.json` 与 tar 独立保存，不覆盖旧失败。该 scratch 镜像**不是可部署的生产镜像**。
- 独立发现并修复模板被 `*.docx` 规则误排除、runtime 题库未从上下文排除、题库 symlink 宿主可见而容器不可见、冒烟 worker 继承 API 健康检查的问题；Root 最终强检查另发现嵌套 `backend/work` 缓存，递归排除并新增两条反例。容器冒烟也增加镜像内私有路径缺失断言；该容器断言尚未实际运行，不能冒称通过。
- 补充容器必须显式配置数据库 URL，避免裸运行时静默沿用开发 SQLite；显式 SQLite 仍可用于本地验证，生产 Compose 固定 PostgreSQL。

CI 中 PR 无 packages 写权限和模型凭据，不使用 pull_request_target。发布 job 依赖后端、前端及容器门禁，并在同一次发布构建实际冒烟后才登录 GHCR/推送。默认分支或受保护的 semver 标签可发布，手动发布仅允许默认分支；Action 固定 SHA，独立核准上游提交存在。版本标签需要仓库 Ruleset 保护，不在本地擅自变更仓库策略。CI 仅承诺验证 linux/amd64 的流程配置，尚未实际运行远程 Actions 或声称 ARM 生产镜像通过。

## 宿主机集成证据

为先发现容器冒烟脚本自身的接口错误，在隔离工作目录执行宿主机适配验证，没有使用现有 18072 预览的用户数据：

1. SQLite、回环 Uvicorn 和真实独立 Worker `--once`：管理员登录、学生创建、两张合成图片上传、分析任务入持久队列、无密钥后由 Worker 安全失败、合成预置练习的反馈写入/读回。没有把预置反馈样例说成已成功生成练习，也没有把无密钥失败说成识别成功。
2. 合成 Word 四件：学生/教师 DOCX 含原生 OMML，学生卷含几何媒体；LibreOffice 实际生成两份中文 PDF，各 1 页。教师答案不要求包含原题几何图，避免错误门禁。
3. XeLaTeX 实际编译显式合成的中文/公式/几何图 TeX，生成 1 页 PDF，无缺字/溢出日志。该项检验工具链，不冒称真实全库转码或完整业务 LaTeX 组卷。
4. Root 渲染并查看上述三个实际 PDF 页面，中文、数学和学生/LaTeX 几何图可见。私有 `host-exports.json` 明确标记 host-only，未写作容器通过。

## 生产镜像构建阻塞及未验收项

本机启用了已有 Colima Docker 环境，只对本项目新建标签/上下文取证，没有部署远程服务。两次实际生产 Docker build 均在 `FROM node:24-bookworm-slim` 拉取 Docker Hub 阶段超时；单独拉取 Dockerfile 指定的公开 uv GHCR 镜像也 TCP 超时，尚未执行项目镜像编译。

只读检查确认 daemon 未配置代理或 mirror；宿主及 VM 的 curl 可访问公开 registry，但对域名不同动态解析地址有可达与超时差异。此为当前出口问题的诊断线索，不将其当作 Dockerfile 已正确构建的证据。未静态固定 registry IP、变更主机网络、daemon 代理或镜像源；已询问用户可用的既有 Docker 出口配置。

仍需在出口可用的 Docker 环境真实完成：生产镜像构建；非 root 的新持久卷可写；PostgreSQL 首次初始化、API/Worker 共享数据及任务消费；镜像内 Word/LaTeX PDF；停止/重启后持久化；实际服务器 HTTPS、手机网络与备份恢复。可执行入口已经准备好，但远程 Actions、GHCR 发布和服务器部署本批均为**未执行**。

私有日志、合成图片/PDF、随机配置与上下文证据位于 `work/deployment-v1/`，不提交源码。详细独立结果见[验收](deployment-v1-review.md)，操作见[部署指南](../deployment.md)。
