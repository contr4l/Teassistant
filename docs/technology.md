# 技术选型

版本：V0.1。查证日期：2026 年 11 月 10 日。状态：DeepSeek API 是唯一生产模型 Provider；Codex CLI 仅用于开发者快速验证。真实模型识别、标记与评分质量尚未验证。

## 1 选型结论

采用 React 与 TypeScript 前端、Python FastAPI 后端、PostgreSQL 主库、Celery 与 Redis 后台队列。生产业务模型仅适配 DeepSeek API：`https://api.deepseek.com`，模型名 `deepseek-flash`（官方滚动模型别名）。Codex CLI 可供开发者快速验证同一单题合同，不作为生产 Provider；两种路径之间不自动回退。题目内容通过结构化数据保存，导出使用 Pandoc 与 LibreOffice。

这些选择以 5 位老师试用、图片文档处理和快速形成备课闭环为依据，不构成已完成的性能、准确率或费用承诺。

## 2 技术清单与取舍

| 领域 | 首期选择 | 选择理由 | 替代方案与启用条件 |
|---|---|---|---|
| 网页 | React、TypeScript、Vite | 适合登录后的表单、上传及对照工作台；无需搜索引擎页面渲染 | Next.js：出现公开内容与 SSR 需求时再考虑 |
| 界面 | Ant Design、React Router、TanStack Query | 统一表单、列表及请求状态；手机网页上传与桌面分析共用接口 | 原生 App、小程序暂不纳入 |
| 数学与卷面预览 | KaTeX、浏览器原生图片区域覆盖层 | 公式与原图证据并排查看；不引入复杂画布编辑器 | 超出 KaTeX 支持时显示明确错误及原图，不静默丢公式 |
| 业务 API | Python、FastAPI、Pydantic | 图像、文档处理与业务在同一语言；结构校验与 OpenAPI 合同 | Django：需要大量通用后台管理时有优势；当前不用双框架 |
| 数据访问 | SQLAlchemy、Alembic、psycopg | 显式事务和迁移，支持后端与 Worker 复用模型 | 首期不再叠加另一套 ORM |
| 数据库 | PostgreSQL | 关系约束支持权限、进度、配额与快照，JSONB 保存内容树 | SQLite 仅能做局部原型，不作为试用环境事实库 |
| 配额分配 | NetworkX 最大流最小费用匹配 | 优先满足可行配额，再按难度距离选题，避免自己实现求解器 | 后续有更复杂教学约束再考虑专用优化器 |
| 后台任务 | Celery、Redis；PostgreSQL 保存任务状态与 outbox | 识别和导出任务需跨请求执行、失败重试与独立限流 | Temporal 等工作流平台在流程复杂和规模增长后再评估 |
| 文件 | Storage 接口；本地目录实现用于开发，私有对象存储用于试用 | 原卷、图形与导出不塞入数据库；不绑定单一存储供应商 | 对象存储提供商在部署时确认；不为首期自建存储集群 |
| 页面处理 | pypdfium2、Pillow | PDF 页面栅格化、旋转、缩放和区域裁剪 | 专用 OCR 在多模态识别实测不足时接入 |
| 生产模型调用 | DeepSeek API、HTTPX 与内部 Provider 接口 | 统一超时、限流、有界重试和 Pydantic 校验；`deepseek-flash` 同时用于图片题面抽取与文本标记、评分的首轮验证 | 不加入其他生产 Provider 或自动 fallback；Codex CLI 仅用于开发者本地快速验证 |
| 开发者模型验证 | Codex CLI `codex exec` | 复用单题输入和输出合同，read-only sandbox、临时工作目录、ephemeral 会话和 JSON Schema 输出 | 不调用 Codex CLI 作为生产后端；模型可选显式指定，否则记录默认模型未解析 |
| DOCX 与 PDF | Pandoc、python-docx、LibreOffice | 原生数学结构、固定模板、图形插入及统一 PDF 转换 | 复杂公式失败时提供明确可见的局部图像回退；不把整份练习变成截图 |
| 测试 | pytest、真实 PostgreSQL/Redis 集成测试；Vitest、Playwright | 重点保护权限、任务重放、组卷硬约束与完整教学流程 | 不以单一 mock 测试代替队列与导出验证 |
| 工具链 | uv、pnpm；Python 3.12、Node.js 24 LTS 作为候选运行时 | 固定依赖和统一开发环境 | P1 验证平台兼容性后锁定实际版本，不使用浮动 latest 镜像 |
| 部署 | Docker Compose；反向代理提供 HTTPS | 首期便于运维，API 与 Worker 可独立调整 | 暂不引入 Kubernetes；需要高可用时再拆基础设施 |

以上为方案选择；具体软件版本、镜像摘要和锁文件在 P1 生成，不在纯文档阶段声称已安装或验证兼容。

基础能力参考：[Vite React TypeScript 模板与运行环境](https://vite.dev/guide/)、[Node.js 发布周期](https://github.com/nodejs/Release)、[PostgreSQL JSONB](https://www.postgresql.org/docs/current/datatype-json.html)、[NetworkX 流算法](https://networkx.org/documentation/stable/reference/algorithms/flow.html)。选用这些能力不代表项目已通过兼容性测试。

## 3 AI 方案

### 3.1 DeepSeek 生产 Provider

首轮由同一 `deepseek-flash` 模型验证图片题面识别、考点标记与难度评分。DeepSeek API 使用 OpenAI 兼容 Chat Completions 格式，base URL 为 `https://api.deepseek.com`。`deepseek-flash` 是官方滚动模型别名，具体服务版本可能更新。[模型列表](https://api-docs.deepseek.com/api/list-models/)、[API 接入](https://api-docs.deepseek.com/)。

图片通过 user message 的 image content block 提交。结构化响应使用 `response_format: {"type":"json_object"}`，提示词包含 JSON 指示和所需结构；请求显式设置 `max_tokens=8192`。返回内容仍可能为空、截断或无法解析，因此必须进行本地 Pydantic 合同校验及考点 ID 白名单检查。[视觉输入](https://api-docs.deepseek.com/guides/vision/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。默认关闭 thinking mode，在 OpenAI 兼容请求中设置 `thinking: {"type":"disabled"}`；直接 HTTP 请求使用该字段，OpenAI Python SDK 需将其传在 `extra_body` 中。[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。

DeepSeek 是唯一生产模型 Provider。配置 `DEEPSEEK_API_KEY` 才允许显式调用；仅设置环境变量不会触发完整开发端到端流程。当前适配器仍是单题验证接口，真实识别、标记和评分质量未测。

### 3.2 Codex CLI 开发快速验证

开发者可用本机 `codex exec` 对同一单题抽取合同做快速验证。CLI 复用现有登录，不传入 `DEEPSEEK_API_KEY`；通过 `--ignore-user-config` 不加载用户 `config.toml`，不会自动继承其中的自定义 provider、MCP 或 hooks。请求交给所选或默认模型服务，不代表本地推理。执行使用临时工作目录、`--ephemeral`、`--sandbox read-only`、JSON Schema 输出及有限超时；这些设置不构成完整文件或网络隔离。提示经 stdin 输入，图片在临时目录解码后传入 CLI，最终结果仍经共享合同校验；失败不改由 DeepSeek 重试。[Codex CLI 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。

Codex CLI 只为开发者快速试验，不作为平台的生产 Provider，也不能替代真实样本评测或完整教学流程验证。未指定 `CODEX_MODEL` 时，结果标为 CLI 默认模型且不声称已解析实际模型名。

AI 负责转写与解释；“不超纲”“每考点几题”“同卷不重复”由程序实现。AI 自报置信度不作为唯一判定依据，结合题号覆盖、必填字段、答案链接和页面完整性判断是否处理成功。

### 3.3 不纳入当前方案的方式

- 自部署开源大模型：首期增加 GPU、模型服务和调参运维，尚无实测成本理由。
- 本地 OCR 加文本模型作为唯一通路：手写作答、公式与几何图形须先实测，保留为改进或降本选项。
- 多 Provider 并行评审或自动 fallback：当前不实现，避免输出冲突或把本地快速试验误作生产服务。
- 向量检索或知识图谱数据库：当前匹配规则是确定的考点与难度，关系表足以表达；统一目录不需要图数据库。

### 3.4 难度规则草案

建议保存 0～100 分，再映射基础、提高、挑战。初始候选分段为 0～39、40～69、70～100，属于待试样校准的技术参数，不是客观测量标准。评分提示词依据知识综合程度、解题步骤、推理要求和运算量，使用固定锚题约束尺度。

先以一套题目集固定规则，同题库和原卷使用相同评分版本。重评分产生新结果，不静默改变已有组卷。无需线上人工审核；研发阶段的样本核对用于模型选型与规则验证。

## 4 队列方案的关键限制

FastAPI 官方建议将较重且可独立执行的后台工作交给 Celery 等工具，因此不使用 API 进程内 BackgroundTasks 承担试卷主流程。[FastAPI 后台任务](https://fastapi.tiangolo.com/tutorial/background-tasks/)。

Celery 的延迟确认依赖幂等任务；Redis visibility timeout 可能导致未确认任务重复交付。因此设置阶段级超时、租约、幂等写入、数据库派发补偿和有界重试；不假定消息只交付一次。visibility timeout 应长于单个阶段的硬超时，而不是整份资料的总处理时长。[Celery 任务语义](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[Redis 队列限制](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)。

AI 调用和导出使用不同队列；优先保证老师考试处理，批量题库入库限流执行。初始并发通过 P0 的耗时与用量结果设置，不根据老师人数推定识别容量。

## 5 数学内容与导出方案

统一内容树产生前端视图、题目 DOCX 和答案 DOCX。公式从 LaTeX 经 Pandoc 写入 OMML，python-docx 调整模板、留白及页内布局；再由 LibreOffice 将各 DOCX 转成 PDF。Pandoc 官方支持 DOCX 数学输出为 OMML，并支持参考文档样式；这不保证所有试卷公式和复杂排版均无损，需 P0 验证。[Pandoc 数学与 DOCX 文档](https://pandoc.org/MANUAL.html)。

MVP 要求文字与支持的公式可编辑、几何图形为清晰图片。必要的公式图像回退必须可追踪，不能静默省略。固定随容器发布的中文字体、导出模板、转换器版本；禁止依赖部署机器临时字体。

四份产物来自相同快照：学生 DOCX、学生 PDF、教师 DOCX、教师 PDF。对照检查题号、公式、图形、页边界与答案一致性。

## 6 部署与成本边界

初期应用可在一台普通 Linux 机器运行 web、api、派发器、AI Worker 与导出 Worker；PostgreSQL 和 Redis 可先用持久卷，定期备份并验证恢复。试用使用私有对象存储保存资料。主机大小、存储供应商和实际费用待样本与部署地域确定，不继承其他项目容量要求。

上传文件设大小、页数与像素上限；网页直传对象存储时服务端签发授权并校验文件完成状态。模型采用限并发与每资料包费用上限。记录页数、模型调用次数、输入输出用量、重试次数、等待时间及处理时间；有准确价格配置后再计算费用。

本次未购买服务、开通账号、上传真实学生材料或调用付费模型。

## 7 选型生效条件

React、FastAPI、PostgreSQL、独立 Worker 与 DeepSeek API 单一生产 Provider 为当前基线。Codex CLI 只用于开发者快速验证。模型真实质量、难度分段和导出细节仍需验证；若证据表明方案不适合，再更新本文并记录迁移原因，不改变 PRD 的产品目标。
