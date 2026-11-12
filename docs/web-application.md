# 教师网页与提示词管理

本轮实现教师网页登录、学生档案、教学进度、考试上传、识别分析与报告、组卷预览、题答文件下载及反馈。管理员可以创建教师账号，编辑六个阶段的提示词，查看历史并将旧内容追加为一个新的激活版本。老师不承担共享题库审核。

## 本地启动

先运行 `make setup`。前端使用 `pnpm --dir frontend install --frozen-lockfile` 和 `pnpm --dir frontend build`；FastAPI 会服务构建后的网页与同源 API。开发时也可启动 Vite，其 `/api` 代理指向本地 8000 端口。

从 `.env.example` 复制本地 `.env` 并配置题库、语义结果及转换器。已有本地资料可指向来源库与对应语义目录，不能只配置一个普通原题文件夹。题库与学生使用相同版本考点目录。未配置题库或转换器时界面显示错误，不生成占位文件。

`TEASSISTANT_DATABASE_URL` 默认 SQLite 文件仅用于本地开发，部署使用 PostgreSQL。私有上传、分析与导出放在 `TEASSISTANT_DATA_DIR`，不作为静态文件目录开放。运行命令时用 `uv run --env-file .env ...` 显式加载配置，或先将所需变量导入当前进程环境。

管理员通过下列命令创建，密码通过终端提示输入，无写死的初始密码。网页不开放公众注册；管理员登录后进入账号管理创建老师。

```bash
uv run --env-file .env python scripts/manage_web.py create-admin --username your-admin --display-name 管理员
```

API 入口：

```bash
PYTHONPATH=backend uv run --env-file .env uvicorn app.web_api:create_app --factory --host 127.0.0.1 --port 8000
```

独立本地任务进程：

```bash
uv run --env-file .env python scripts/run_web_worker.py
```

页面与 Worker 必须使用相同数据库和私有目录。关闭页面不停止后台任务；任务租约过期后可恢复，旧租约不能发布新结果。无 Worker 时任务保持排队，不能把排队显示为已分析。

## 模型与提示词

业务 Provider 只有 DeepSeek，管理员可在“管理设置 → 模型与额度”保存密钥及费率，检查账户余额、查看昨日用户估算排行并追加累计额度。环境密钥保留后备，管理员配置优先。新账号默认一次性发放10元额度，管理员可设置，旧用户不补发，每次请求前预占、用量返回后结算，未知用量保留待核实；操作及口径见 [管理员模型与额度](admin-model-billing.md)。Codex CLI 仅在 `TEASSISTANT_ALLOW_CODEX=1` 且显式指定 `CODEX_MODEL` 后用于开发验证；教师界面会标明开发通路。默认不启用，无自动回退。不要把 Codex 登录环境挂到生产容器中。

管理员可配置 layout、paper、reference、response、diagnosis、report 六段任务说明。共同数据边界、角色隔离、Schema、考点白名单和组卷确定性规则不属于可编辑提示词。每个任务保存提交时的完整提示词版本与内容摘要；修改和回滚只影响后续提交。报告通过独立 report 模型阶段生成，页面显示结构化结果，不使用写死的单份报告样例。

局部修正保留旧分析和基版本，生成新分析；旧报告在新分析发布后失效。重新识别时保留人工覆盖，无法安全转移到新来源时任务明确失败，保留已发布的老师修正版。

## 教师使用流程

侧栏“了解使用方式”可打开四步使用说明，并直接跳转学生档案、考试分析或组卷。界面文案、术语与样式约定见[教师界面设计约定](ui-guidelines.md)。支持关闭按钮、Escape和点击遮罩关闭；手机从左上角菜单进入，正文内部滚动，跳转不会自动创建学生或提交任务。

1. 登录并创建学生，标记已学考点。目录中的前置知识也须已学，库存显示进度过滤后的数量。
2. 创建考试并填写实际题数，分别上传原题、已批改作答、可选标准答案。手机可拍照上传，电脑打开同账号查看同一考试。
3. 提交识别任务，查看处理状态、逐题分析、整卷报告和原图证据。未知与模糊项目保留，老师可局部纠正。
4. 选择考点，每点默认 10 题；进阶模式设置各难度数量。缺题时展示缺口，保持进度限制，不重复填题。
5. 预览选题与答案，下载独立学生卷和教师答案的 Word/PDF。明确选择 LaTeX 排版或 Word 模板转换；LaTeX 转换不支持或未配置 XeLaTeX 时明确失败，不自动降级。文件仅在相应任务成功发布后授权下载；进度变化后需要重新生成。
6. 提交符合预期情况、质量分、自评节省分钟数及意见。节省时间允许零或负值，反馈不阻止下载。

本批预览以只读为先，单题替换、删除及重排若尚未实现不能计为 FR-14 通过。所选考试原题尚未与题库全部建立可靠共同身份时必须显示待核对，不宣称已完整排除原题；已明确的排除 ID 由程序保证。

## 单服务器部署

当前 `compose.yaml` 默认提供 PostgreSQL、一次性 init、API 和独立数据库任务 Worker。init 先完成表与提示词初始化，API/Worker 再启动；三个常驻服务即可单机运行，无须 Redis。现有 Celery/Redis 代码保留为扩展路径，默认部署不启动它们；长任务仍不在 API 的 BackgroundTasks 内运行。镜像包含前端、中文字体、LibreOffice 和 XeLaTeX。

部署使用独立 `.env.deploy`，通过 `python3 scripts/deploy.py init` 从 `.env.deploy.example` 生成私有数据库密码，再运行 `up --build` 和交互 `admin`。不复用开发机 `.env`。Compose API 默认仅绑定 `127.0.0.1:8000`，对外使用同源 HTTPS 反向代理，再设置 `TEASSISTANT_SECURE_COOKIES=1`。先在管理员终端初始化账号，再让老师用手机和桌面访问同一域名。不要把开发用 SQLite、Codex 登录或本机私有路径直接当生产环境。完整步骤见[Docker 部署指南](deployment.md)。

数据库、私有数据卷和原题库均需备份；恢复时保持数据库记录与文件目录的同一时间点。Compose 构建、生产 PostgreSQL/Redis、外部 HTTPS 和实际手机网络的验收与本地 SQLite/浏览器开发检查分别记录，不能混报。
