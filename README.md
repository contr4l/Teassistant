# Teassistant

面向湖南省初中数学教培老师的智能备课平台。通过考试材料识别、错题考点分析及结构化题库，生成符合学生教学进度的个性化练习。

首期交付形态为 **手机网页上传＋桌面网页备课**。手机拍照上传材料，电脑完成分析修正、组卷与导出；全流程手机备课、小程序和原生 App 暂不纳入。

当前已实现本地题库、组卷和数学导出、学生考卷分析，以及教师与管理员网页。网页覆盖登录、学生与进度、考试上传、分析报告、组卷预览、下载和反馈；管理员可编辑六个阶段的提示词并回滚。学生分析已用一份真实手写答卷通过 Codex CLI 试跑，尚未完成系统性的识别准确率与教师价值验证。DeepSeek API 是唯一生产模型 Provider；Codex CLI 仅供开发者显式快速验证。本地检查不代表生产部署或完整 MVP 已验收。

## 项目文档

- [产品需求 V0.1](PRD.md)：产品范围、业务规则和 MVP use case。
- [模块与架构设计](docs/architecture.md)：模块边界、数据结构和处理流程。
- [技术选型](docs/technology.md)：首期技术方案、替代方案与验证事项。
- [开发计划](docs/development-plan.md)：阶段任务、依赖和验收标准。
- [开发与验收范式](docs/engineering-workflow.md)：合同、分工、独立检查及交付证据。
- [单题模型快速验证](docs/model-validation.md)：DeepSeek API 和 Codex CLI 的显式验证方法与边界。
- [当前模型适配器验收](docs/acceptance/model-adapters-review.md)：DeepSeek 迁移、CLI 进程检查与本轮测试证据。
- [本批独立检查](docs/acceptance/p0-review.md)：Sol 独立运行发现的问题与复验记录。
- [首批执行报告](docs/acceptance/p0-execution.md)：实际环境、集成测试、评测分母与导出证据。
- [DOCX 题库导入](docs/docx-bank.md)：批量配对原卷与解析，生成本地检索页面、SQLite 和题目目录。
- [试题录入工作流](docs/question-ingestion-workflow.md)：来源保存、AI 标记评分、程序校验和可组卷发布的阶段及实施顺序。
- [试题库 PRD 支持度分析](docs/acceptance/question-bank-prd-review.md)：现有能力、真实数据状态、合成反例及发布前缺口。
- [来源录入 V2 修复记录](docs/acceptance/ingestion-hardening-execution.md)：独立清点、关联确认、公式及段内分割、文件副本处理与本批实测结果。
- [DOCX 题库独立验收](docs/acceptance/docx-bank-review.md)：本批真实来源、公式图片及导入完整性检查。
- [真实题库本地组卷与导出](docs/bank-practice.md)：库存、学生进度、默认/进阶配额、原生 Word 与 PDF 的开发验证入口。
- [试卷专用版式](docs/exam-layout.md)：同题单作答版/紧凑版、教师答案模板及排版比较命令。
- [学生考卷与学情分析](docs/exam-analysis.md)：材料处理、手写识别、题答关联、局部修正、近期考试汇总及私有报告入口。
- [教师网页与提示词管理](docs/web-application.md)：本地启动、初始化管理员、持久任务、手机上传及单服务器部署。
- [管理员模型与累计额度](docs/admin-model-billing.md)：网页设置 DeepSeek、昨日用户估算排行、追加额度与未知预占核实。
- [管理员题库工作流](docs/admin-question-bank.md)：原始Word上传、自动加工、搜索预览、考点难度调整、启停、程序检查与版本恢复。

## Docker 单机部署

ROCK 5B（ARM64）环境确认、板上原生构建和预设管理员/教师账号见 [ROCK 5B 部署指南](docs/rock5b-deployment.md)。准备私有配置与账号文件后可运行 `python3 scripts/deploy.py bootstrap --build --accounts-file .accounts.deploy.json`；不将当前amd64流水线或未推送工作区称为板上已验证版本。

服务器准备 Docker Engine、Compose v2 和 Python 3（仅标准库），不需要在宿主机安装 Node、uv、中文字体或 LaTeX。部署配置与开发配置分开：

```bash
python3 scripts/deploy.py init
# 编辑 .env.deploy；DeepSeek 密钥可启动后在管理员网页填写，题库可后续挂载。
python3 scripts/deploy.py up --build
python3 scripts/deploy.py admin --username your-admin
```

初始化自动生成私有数据库密码，拒绝覆盖已有配置。默认访问 `http://127.0.0.1:8000`；API、独立 Worker 和 PostgreSQL 使用持久数据卷。管理员密码通过终端交互输入。缺密钥或题库仍可先建立账号和学生档案，相应识别与组卷功能保持不可用。

GitHub Actions 配置执行后端测试、前端构建、镜像构建及容器合成冒烟，受信默认分支/版本标签通过检查后可发布 GHCR 镜像；PR 不发布。镜像发布后可在 `.env.deploy` 指定镜像标签或摘要，再运行 `python3 scripts/deploy.py pull` 和 `python3 scripts/deploy.py up`，无需服务器重新构建。

完整配置、题库挂载、HTTPS、备份、更新与回退见 [部署指南](docs/deployment.md)。本机容器检查与远程 Actions/实际服务器验收分别记录，不能混作已部署。

## 离线开发

需要 Python 3.12 或 3.13，以及 uv。当前环境实际使用 Python 3.13.7，依赖固定于 `uv.lock`。

```bash
make setup
make check
make setup-web
make check-web
make schemas
make selection
make evaluate
```

`make selection` 使用合成题目生成 `work/selection-snapshot.json`；`make evaluate` 读取独立 manifest 和预置预测，不调用模型。评测中的真实样本数为零时，真实质量保持未测。

数学导出还需要 LibreOffice、中文字体和 Pandoc；`make setup` 的 export 依赖组提供 Pandoc 二进制。使用显式可执行路径运行：

```bash
.venv/bin/python scripts/export_demo.py --output work/export-demo \
  --pandoc /absolute/path/to/pandoc --soffice /absolute/path/to/soffice
```

输出目录必须不存在。该脚本只导出合成题样张；生产字体、转换器镜像和真实卷面导出仍须后续验证。

### 单题模型快速验证

生产业务模型只有 DeepSeek API；Codex CLI 仅供开发者快速验证，复用登录但不加载用户 `config.toml`，使用所选或默认模型服务，不是本地推理或生产 Provider。两种路径都要显式选择，不会自动互相回退。学生考卷入口要求显式指定 Codex 模型；一次真实试跑不构成总体识别质量结论。

配置项、命令和输入限制见[单题模型快速验证](docs/model-validation.md)。

## 实施顺序

先验证真实试卷识别、答题卡关联和公式导出，再搭建基础工程、打通题库与学生分析，最后完成组卷、导出及老师试用。具体任务见开发计划。

- [关联核验 V3 执行记录](docs/acceptance/association-v3-execution.md)与[独立验收](docs/acceptance/association-v3-review.md)：固定 200 题已确认 193 题（96.5%），剩余 7 题保留未确认。

- [统一考点与难度评级试跑](docs/semantic-bank.md)：200题已完成AI初评（基础123／提高55／挑战22），93点工程目录；[实际执行与限制](docs/acceptance/semantic-v1-live-execution.md)。

- [真实题库组卷与导出执行记录](docs/acceptance/bank-practice-v1-execution.md)：176题可作为本地候选，默认/进阶各10题实跑，原生公式题答Word/PDF四件输出；358项回归通过。使用的是合成学生进度，尚无手机/桌面业务页面及真实学生诊断闭环。

- [试卷版式执行记录](docs/acceptance/exam-layout-v1-execution.md)：同一10题生成作答版4页、紧凑版3页、教师答案5页，399项回归通过；[独立检查](docs/acceptance/exam-layout-v1-review.md)覆盖原生对象及全部实际PDF页面，教师评价仍待试用。

- [LaTeX 打印导出](docs/latex-export.md)：将支持的原生数学结构转成 LaTeX 后通过 XeLaTeX 排版，统一公式字体与基线；保留独立 Word 导出及原几何图，未知结构明确失败。覆盖范围与实际结果见该批验收记录。
