# 容器与持续集成合同

2026-11-12，用户要求整理 GitHub Actions、简化配置和部署、打包 Docker。对应 DEV-101/105/502、FR-15 及教师网页登录闭环，不改变模型、题库审核或产品终端范围。构建文件与发布工作流可在本批实现；本批不自动提交、推送、发布镜像或操作远程服务器。

最终范围调整：本机 registry 拉取超时后，用户明确回复“没有，先完成配置和离线检查”。因此本批以可评审配置、工作流、构建上下文与宿主离线集成为交付出口；生产镜像构建、真实 PostgreSQL 容器及镜像内 PDF/服务运行单列为未验证，不再尝试修改网络或代理。

## 归属与接口

- Root：Compose、部署示例配置、部署辅助 CLI（仅标准库）、README/部署说明、集成与证据。
- 实现者 A（实际 gpt-6-luna/high）：Dockerfile、.dockerignore、scripts/container_entrypoint.py、scripts/container_healthcheck.py 及独立对应测试；不改业务数据合同/模型供应商。
- 实现者 B（实际 gpt-6-luna/high）：.github/workflows/ci.yml、scripts/container_smoke.py 及对应测试。可请求 Root 调整 Makefile；不改 A/Root 文件。
- 独立验收者（实际 Sol/high）：独立验证容器、工作流权限/发布条件、秘密/私有资料排除、负例和证据；只拥有 docs/acceptance/deployment-v1-review.md 及新的独立验收测试文件。

共享设计：一个多阶段生产镜像包含构建后的前端、Python 锁定生产/export 依赖、LibreOffice、中文字体、XeLaTeX。非 root UID/GID=10001，/app/work 为持久可写目录；/bank 为只读外部资料，源码/凭据/真实资料不烘焙。容器执行 Python/管理命令均使用镜像内 .venv/bin，不运行时下载依赖。

默认单机 Compose 使用 postgres、一次性 init、api、worker（worker 复用现有数据库持久任务执行器 scripts/run_web_worker.py）；不要求 Redis/Celery/beat 才能首次部署。init 等数据库健康后初始化表与默认提示词，API/worker 等 init 完成，防止初次并发建表。Celery 代码保留为可选扩展，文档同步默认部署方式。API 端口默认127.0.0.1:8000，容器8000；用户可显式调整绑定，外网HTTPS由代理提供。API健康检查需检验HTTP及数据库；worker健康只证明进程身份/数据库连通，不冒充任务质量或租约健康。

Compose既支持指定预构建镜像（pull），也支持本机build，同一镜像用于init/api/worker。显式环境白名单，不让宿主机开发路径/空值意外覆盖镜像路径；生产通路固定DeepSeek，Codex禁用。DeepSeek缺钥允许首次登录配置，但识别任务明确不可运行。题库可暂不配置以先启动账号页面；配置后必须是来源库+对应语义目录，不能空目录充数。不得把合成题库烘焙为真实库存。

GitHub PR仅检查/构建/冒烟，不获取发布权限或模型密钥；默认分支push/版本tag或显式手动发布仅在检查与容器冒烟通过后向GHCR推送。固定commit SHA Action，最小GITHUB_TOKEN权限，不使用pull_request_target执行外部代码。发布可复用同次测试镜像；若多架构未验证则不得宣称已验证。CI 不真实调用模型或上传私有文件。

## 验收出口

1. 本机/CI真实构建、非root运行、前端HTTP、PostgreSQL初始化、登录/学生/队列/反馈的容器合成冒烟；队列由独立worker完成，不通过API进程假执行。若本机Docker不可用，先完成可复核文件与离线检查，并明确未运行容器，不伪造成功。
2. 容器中实际导出合成DOCX/PDF及LaTeX PDF，检查中文、数学与几何图；未知公式仍明确失败。构建不需要业务凭据，私有报告与真实题库不进镜像/CI制品。
3. 新服务器可按文档生成私有配置（不覆盖已有文件）、检查、拉取或构建、启动、交互创建管理员；说明题库挂载、更新与回退、数据库及文件一致性备份、HTTPS cookie设置、镜像标签/摘要固定及首次部署边界。
4. 运行 make check、前端类型检查/构建和适用容器检查；每项记录真实执行状态。不能把本机成功说成远程Actions已通过、镜像已发布或生产服务器已部署。
