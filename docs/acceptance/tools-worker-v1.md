# 业务 Worker 与工具服务分离：合同与验收

本批由用户确认采用两个 Compose service，目标是更新分析、抽题和导出编排时不重建重型工具镜像。范围对应 PRD FR-02、FR-07～15 与部署交付；不改变题库准入、AI 提供商或导出完整性要求。

## 冻结合同

- `worker`：数据库任务领取、租约、版本校验、DeepSeek 与计费、题库加工编排、组卷和产物发布。仍使用现有数据库任务表。
- `tools`：独立进程与镜像；只接收工具调用，无业务模块、数据库连接和模型凭据。Compose 不映射主机端口，仅连接内部 `tool-net`。两者共同挂载 `/app/work`；源题库 `/bank` 只挂载业务服务。
- HTTP `tools-v1`：`GET /health`；`POST /v1/word-pdf` 接受 `source,output_dir,profile_dir`；`/v1/tex-pdf` 接受 `source,output_dir`、执行一遍编译；`/v1/pandoc` 接受 `source,target`。转换返回 `version,returncode,stdout,stderr`。`/v1/screenshot` 接受 `html`，返回 `version,png_base64,width,height`。不提供任意命令或任意参数执行接口。
- 文件必须解析在工具共享根目录以内，拒绝穿越及逃逸符号链接；截图禁止访问网络与本地文件。命令超时、有界请求和输出，不自动重试可能已经产生文件的调用。
- `TEASSISTANT_TOOLS_URL` 显式开启远程调用；配置后服务不可用或合同不匹配必须失败，不回退本地工具。未配置时保留宿主 CLI 开发验证路径。
- PDF 签名、TeX 日志、公式 OMML、截图完整性、阶段目录及最终版本校验继续由原调用路径保证。工具服务不是任务事实来源；业务 Worker 更新可能中断当前调用，不承诺无损热升级。

## 构建和升级边界

工具镜像仅依赖 `Dockerfile.tools` 和显式 COPY 的 `tools/worker.py`、`tools/requirements.txt`，不复制业务源码、前端或业务依赖锁。内容摘要排除生成缓存，并拒绝新增未纳入摘要的 COPY 输入。业务 CI 在已发布工具镜像存在时复用；工具工作流仅由工具输入、引用计算脚本、工作流本身的变化或手动操作触发。业务 Worker/API 按业务提交发布，工具版本单独固定。更改基础系统、浏览器、TeX、工具协议或服务实现仍须重新构建并验收工具镜像。工具 Python 依赖均为根 uv.lock 已有包的明确版本，独立 requirements 固定工具环境，普通业务锁更新不自动改变它。

普通 `deploy.py build` 仅构建 `worker api`；`build-tools` 为首次源码部署或主动升级工具环境的独立步骤。`pull` 拉取配置中固定的工具引用以及业务镜像；工具引用与内容未变时不应导致工具容器重建。已有部署必须同步新 Compose、脚本和配置键，不覆盖原数据库密码、项目名及数据卷。

## 分工与验收

实际协作模型为 `gpt-6-luna/high`：工具服务、业务工具适配、镜像/CI 各有独立文件归属；主 Agent 负责合同、Compose、部署、文档及集成。独立复查必须执行路径穿越、错误协议、工具不可用和真实转换反例。

验收覆盖宿主全量检查、真实 HTTP 转换、Compose 网络/凭据边界及业务变化不改变工具摘要；标准 Dockerfile 双架构镜像及混合容器冒烟须另行实测。宿主/合成验证不得记为真实学生识别或老师价值验证。

## 本批执行记录（2026-11-14）

- 最终 `make check`：Ruff 通过，**905 passed**，1 条既有 Starlette/httpx 弃用 warning，131.91 秒；日志 `work/tools-worker/make-check-final.log`。最后新增/调整的发布边界、冒烟 fixture 和 CI 合同另跑 17 项通过；最终 Ruff、`git diff --check` 和 `UV_CACHE_DIR=.cache/uv uv lock --offline --check` 通过。前端源文件与业务依赖锁未改变。
- 两个 workflow YAML 解析及 32 段 Bash `bash -n` 通过，工具 workflow 内嵌 Python 可编译。工具索引发布保留推送前后的 descriptor 映射并比较；已有内容标签只复验、不重推。独立验收者只读检查并运行服务/客户端/引用边界 18 项通过，另实际执行下列 Bash 反例。
- 通过实际 `docker compose config --format json` 校验生产拓扑：tools 没有 ports、数据库/模型环境或 `/bank` 挂载；仅加入 internal 工具网络；业务 Worker 同时连接默认网络和工具网络，并等待 tools 健康。解析配置仅使用合成密码，报告在忽略目录 `work/tools-worker/`。
- 通过真实 localhost HTTP 连接现有生产转换函数与独立服务，调用实际 LibreOffice、Pandoc、XeLaTeX，以及新临时 profile 的官方 Google Chrome。Word PDF、原生 OMML、LaTeX PDF、完整 PNG 分片和外部请求拒绝共 5 项通过。没有调用 DeepSeek，没有使用真实学生资料。受限沙箱内 Chrome 启动失败，随后经执行审批在沙箱外完成验证；未使用已有浏览器 profile。
- 进一步运行 CI 原有三组 fixture 的远程分支：学生/教师 DOCX 保留原生数学，学生卷保留几何图；两份 Word PDF 含预期中文且各 1 页；LaTeX PDF 含中文、公式和几何图，1 页，无缺字/超行；截图包含行内 MathML、表格和嵌入图像，输出合同保持不变。实际 PDF 经 PDFium 读回和渲染检查，合成产物留在忽略目录。
- 真实联调发现并修复 XeLaTeX 符号链接问题：不能把 `xelatex` 解析成 `xetex` 再执行，必须保留入口名。另补充以 `./文件名` 传入 TeX 输入，防止以 `-shell-escape` 开头的文件名被解释为选项。已有 HTTP 反例测试覆盖。
- 独立验收者运行提取出的实际 CI Bash，使用临时 fake docker/git 检查：已有镜像复用；DNS/权限等错误中止；已确认缺失且工具输入变化时才本地构建；未变化却缺失则中止；发布已有内容标签时只校验、不 create/push；首版缺失则先双架构校验再发布；dispatch 可上传唯一 staging 标签。fork PR 明确在尝试私有仓库前选择隔离源码构建，不将网络错误解释为缺镜像。
- 私有 GHCR 拉取使用步骤级临时 DOCKER_CONFIG，结束即删除；构建和冒烟不继承 registry 登录文件。业务正式发布前验证工具内容标签已具备 AMD64/ARM64；首次工具工作流尚未完成时有界等待，超时明确要求等待工具发布后重跑业务流水线。工具工作流首次直接推送唯一 staging 标签创建包，再发布内容标签，避免不存在的新 GHCR 包被认证错误阻断。

标准 `Dockerfile.tools` 构建已尝试，但当前 Docker daemon 拉取 `python:3.13-slim-bookworm` 时无法解析 registry-1.docker.io（DNS 127.0.0.53），未修改网络/代理。因而本轮新双架构镜像、API 200 MB 门禁、新三服务容器联合冒烟、GitHub 发布与 ROCK 5B 升级均尚未实测；宿主真实工具 HTTP 验证不能代替这些检查。

用户随后明确要求提交推送、交由 GitHub 编译。发布前再次执行 Ruff、diff 检查及相关 34 项测试通过；上述 905 项全量检查为同批集成证据。提交推送只启动云端验证，镜像可用性以本次 Tools image 与 CI 实际运行结果为准；没有在本批执行板上升级。与本批无关的本地旧升级笔记不纳入提交。
