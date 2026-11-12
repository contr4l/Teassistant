# 容器与持续集成独立验收

依据 `deployment-v1-contract.md`，对应 DEV-101/105/502、FR-15。实际独立验收为 `gpt-6.1-sol/high`，实施为 `gpt-6-luna/high`。最终结论：**用户已明确选择的配置与离线检查范围通过；生产镜像构建、PostgreSQL 与容器运行出口未验证**。本机 Docker daemon 访问 Docker Hub/GHCR 超时，尚未获得生产镜像，不将上下文检查或宿主机成功代替容器部署成功。本轮已修复包括嵌套私有目录在内的已发现缺陷，不再进行网络尝试。

独立验收不启动/停止 Docker、Colima、Compose 服务或本机已有网页服务，不提交、推送、发布镜像或访问远程服务器。实际服务生命周期和构建由 Root 负责，已有未提交业务代码与私有资料保持原位。

## 已提交的合同意见

- 默认长期服务为 PostgreSQL、API、数据库任务 Worker；一次性 init 只建表和默认提示词，须成功后才启动 API/Worker。管理员账号另行交互创建，没有默认凭据。
- API/Worker/init 使用同一镜像、同一数据库及持久私有卷，外部题库与语义目录只读。缺模型密钥仍可登录，不能伪装识别可运行。健康探针只声明它实际检查的 HTTP/进程/数据库条件，队列消费须由独立任务冒烟证明。
- 构建复制实际工作区的授权业务源码，含尚未提交文件；不能以只含 tracked 文件的 Git archive 代替。根目录和嵌套的凭据、原始资料、私有报告、Git、虚拟环境、node_modules、pnpm 及 Python 缓存均须排除。最终以实际构建上下文与镜像清单为证，不能只读 ignore 文件。
- 非 root UID/GID 10001 与首次持久卷权限须真实验证。部署配置新建使用排他创建及私有权限；预检不得输出展开后的数据库密码或模型密钥。数据库口令的 DSN 编码须明确，特殊字符不可造成静默连错库。
- PR 无发布权限、不读取业务密钥；发布必须依赖检查和容器冒烟通过。固定 Action SHA、发布事件/ref 范围及 tag 校验须能核对，不把不可信 ref 直接插进 shell 源码。构建与发布的平台、镜像身份及实际运行范围分别说明。

## 已运行的独立证据

- `.venv/bin/pytest backend/tests/test_deployment_acceptance.py -q`：初版 19 通过、1 失败，复现宿主目录外的符号链接被错误判为可用；Root 修复后独立重跑 **20 passed in 0.30s**。部署配置现在分别检查题库目录及必需文件的真实挂载范围，并拒绝配置路径链中的符号链接。反例还验证排他创建、0600、已有符号链接目标不覆盖、错误配置/DSN不泄漏、失败预检阻止后续命令、管理员调用使用参数数组而不传密码、非法模型路由不执行进程、数据库连接与语句双重超时、错误进程身份不健康。
- `.venv/bin/ruff check backend/tests/test_deployment_acceptance.py` 与 `ruff format --check`：均通过。测试所有可能启动服务的 subprocess/exec 均被替换，仅健康数据库错误测试使用假 engine；没有启动应用或访问模型。
- 首次实际 scratch `COPY . /context` 检查发送上下文 **3.16 MB、286 个文件**，8 个源码/模板目标与工作区逐字相同；但当时只按根目录前缀检查私有路径，**漏掉了 backend/work，因此原“零泄漏”结论撤回**。旧 `work/deployment-v1/review-context.json`、`review-context-image.tar` 原样保留，不能作为最终私有资料排除通过证据。最初 `--output` 方式因 legacy builder 不支持而失败，随后 image save 方式成功；两者均未启动容器。
- 真实 `docker compose ... config --format json` 在独立生成的私有配置上退出 0，检查 13 条结构条件全部通过：精确四个服务、同镜像/DSN/私有卷、PG健康后init、init成功后API与Worker、题库只读、生产DeepSeek/Codex关闭、默认回环绑定、PG不公开端口、Worker精确命令、init不重启、无任意 env_file 透传。展开内容仅在内存中检查，不写出含密码的 JSON；结果为 `work/deployment-v1/review-compose.json`。

已修复的构建上下文问题：广泛 DOCX 排除最初也排除了三份运行模板，现仅白名单 `templates/exam-print-v1/*.docx`；默认真实题库挂载 `runtime/` 最初未排除，现已排除。Root 随后发现 `backend/work` 中 LibreOffice 临时 profile/XML 仍会进入上下文；原 DOCX/PDF 虽按扩展名排除，不能据此接受其余私有运行资料。Root 已补 `**/work`、`**/outputs` 及嵌套 `.git/.venv/.cache/evals/private` 排除。

对此新增两项持久独立反例：审计器检查**任意路径组件**，合成嵌套 work/outputs/.git/.venv/.cache/evals/private/runtime/node_modules 必须被识别；同时检查 Docker ignore 的对应递归排除。用同一审计器重新读取旧 tar，确实识别 **189 个嵌套 work 条目（含目录）**，证明旧探针漏检，而不是简单更改报告措辞。

随后独立执行 `docker build --file - --tag teassistant-deployment-review-context-corrected:local .`（stdin 仍为 scratch + COPY），保存全新 `review-context-corrected-image.tar`，未覆盖旧证据。最终发送上下文 **2.698 MB**，**219 文件、240 全部条目**；所有路径组件检查零命中，8 个当前业务源码/模板目标仍逐字 SHA 一致。证据 `work/deployment-v1/review-context-corrected.json`，tar SHA-256 `a2f751e9676848a7d616857e7ae871baa9389d7560dcea837781e31cb4c00382`。只创建新 scratch 镜像，没有启动容器或拉取外部镜像。

## CI、宿主证据与容器限制

已读工作流：全局及检查任务仅 contents:read，PR 不进入发布任务；发布依赖 backend/frontend/image，默认分支 push、受保护版本 tag 或默认分支手动执行才可进入。发布任务重新构建并冒烟，在登录注册表前验证严格版本 tag，推送同一份本地冒烟镜像；没有使用 pull_request_target。三个 Action 的 40 位固定提交已核实确实存在：[checkout](https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1)、[setup-python](https://github.com/actions/setup-python/commit/ece7cb06caefa5fff74198d8649806c4678c61a1)、[setup-node](https://github.com/actions/setup-node/commit/820762786026740c76f36085b0efc47a31fe5020)。受保护 tag 的判定依据 [GitHub contexts](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)，仍需仓库实际配置对应保护规则；没有运行远程 Actions。

追加独立反例已复现：临时冒烟 Compose 的 Worker 最初继承镜像默认 API HTTP 健康探针，会使独立 Worker 在 `up --wait` 时不健康；生产 Compose 无此问题。B 已为 Worker 指定角色探针、禁用一次性 init 的探针。独立运行 `.venv/bin/pytest backend/tests/test_deployment_acceptance.py backend/tests/test_container_runtime.py backend/tests/test_deploy.py backend/tests/test_container_smoke.py -q`，结果 **39 passed in 0.29s**（其中独立反例 21 项）。CI 亦已显式设置 `TEASSISTANT_SOFFICE=/usr/bin/libreoffice`，避免安装转换器却跳过 opt-in 集成。

Root 后续增加两项部署边界：容器必须显式设置 DATABASE_URL，不能继承开发 SQLite 默认；stop/status 在题库挂载丢失时仍可执行恢复操作。独立阅读后再次运行上述四个测试文件，最终 **42 passed in 0.35s**，独立文件 Ruff/format 均通过。非法路由反例亦补显式数据库配置，确保不是被缺数据库条件提前拒绝而假通过。未增加测试数量或削弱断言。

Root 的 `work/deployment-v1/make-check-final.log` 已直接核读：**648 passed、1 warning、34.59s**；既有 Starlette/httpx 弃用警告。`frontend-check-final.log` 显示前端类型检查与构建通过。此处为 Root 执行证据复核，本验收者没有重复执行昂贵全量测试。

末次递归上下文补验后，独立运行 `.venv/bin/pytest backend/tests/test_deployment_acceptance.py backend/tests/test_container_smoke.py -q`：**28 passed in 0.21s**（23 独立反例、5 冒烟脚本单测）；Ruff 与 format 检查通过。随后冻结代码，独立重跑全部四个部署测试文件：**44 passed in 0.25s**。Root 还在冒烟脚本加入镜像内私有构建路径不存在断言；实际生产镜像仍未构建，因此此运行时断言尚未在生产容器执行。

最终再次直接核读 `work/deployment-v1/make-check-final-v2.log`：Root 全量 **650 passed、1 已有 warning、32.05s**，Ruff 通过，已包含末次两个递归上下文反例。前端此后未变，沿用已通过的类型检查/构建。用户明确选择的配置与离线检查已完成；生产镜像/容器及远程发布仍按上述未验证边界保留。

Root 已在宿主机完成 localhost HTTP、SQLite、独立 worker --once 与合成导出验证；`host-exports.json` 明确标记 **host-only adapter; not a container check**。两份 DOCX 均含原生数学，学生版含几何图；两份 Word PDF 和合成 LaTeX PDF 均为一页，LaTeX 使用 no-shell-escape 并检查缺字/溢出。上述结果只证明宿主程序与样例工具可运行，不证明镜像内依赖、中文显示、首次卷权限或 PostgreSQL 路径。独立验收者未冒充逐页检查容器 PDF，因容器产物根本尚未生成。

`docker-build.log`/`docker-build-retry.log` 显示基础镜像阶段 Docker Hub 注册表连接超时，生产构建未完成；Root 同时报告 daemon 连接 GHCR 失败，本批未修改代理或主机网络绕过。轻量 scratch 检查无需拉取外部镜像，故它成功与生产构建失败并不矛盾。

后续首次实际部署仍须补全：生产镜像构建；镜像内 UID/GID 10001 及新私有卷写权限；真实 PostgreSQL 首次 init；API/独立 Worker 同库同卷启动与队列终态；镜像内 Word、LaTeX 的中文/数学/几何实际导出及 PDF 检查。Worker 健康仅证明精确进程和数据库连通，不声明队列消费或模型质量。远程 Actions、GHCR 发布、多架构、外网 HTTPS、备份恢复演练和生产服务器部署均未验证。本批没有真实模型调用、没有发布操作，没有将合成队列失败或合成导出解释为教学价值验收。
