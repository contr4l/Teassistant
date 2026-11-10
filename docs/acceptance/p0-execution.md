# P0 首批执行与集成验收记录

日期：2026 年 11 月 10 日。结论：**P0 离线基础可运行，完整 P0 真实效果门槛尚未验收，MVP 尚未交付。**

本页为首次 P0 批次的历史证据。后续模型接入已按用户要求调整为 DeepSeek 业务接口与 Codex CLI 快速验证；新一轮接口与命令验收见 [模型适配器检查](model-adapters-review.md)。原始批次数字不重新标记为新供应商的真实调用结果。

## 范式与实际分工

先由 GPT-6.1 Sol / high 编写 `docs/engineering-workflow.md`，再由两个 GPT-6 Luna / high 分支并行实现。工具未提供 GPT-6.1 Luna，实际模型差异已在开始时告知；不将开发 Agent 的能力当成业务模型效果。

| 执行者 | 责任 | 文件 |
|---|---|---|
| Sol 总设计与独立验收 | 范式、独立反例、代码审查、缺陷复验 | engineering-workflow.md、p0-review.md、test_lead_acceptance.py |
| Luna A | 模型适配器、manifest 优先评测与合成 fixture | ai.py、evaluation.py、evaluate.py、对应测试及 fixture |
| Luna B | 数学导出、题答分离及导出测试 | exporting.py、export_demo.py、test_exporting.py |
| 集成负责人 | 共享合同、进度闭包、配额分配、环境、合同 Schema、集成和视觉验收 | contracts.py、curriculum.py、practice.py、项目配置、集成测试及本报告 |

执行遵循合同先行、分文件所有权、独立读代码和反例验证、修复复验、实际集成与产物核对。实现者完成不直接等同于验收通过。

## 本批完成范围

- DEV-003 离线部分：题目、来源区域、考点目录、学生进度、配额及练习快照合同，并生成 5 份 JSON Schema。
- DEV-002 工具部分：单题结构提取的模型适配器；固定元数据由调用方绑定，模型只提供内容字段；MockTransport 验证请求、错误和重试，不执行真实模型调用。
- DEV-001／002 工具部分：独立 manifest 与预测结果分开，区分合成与真实；失败不剔除、比例不超过 100%、未确定关联单列。
- DEV-004 离线部分：原生数学 DOCX、图片与分开答案，真实调用 Pandoc 与 LibreOffice 生成四份文件。
- FR-06、11～13 提前验证：默认每考点 10 题、进阶配额、传递前置知识过滤、原题排除、同题身份组去重、全局容量匹配、明确缺口；不代表 P4 已交付。

未实现网页、账号、数据库、生产文件存储或持久任务队列，不以离线脚本替代这些功能。

## 实际环境

Python 3.13.7，Pydantic 2.14.3，HTTPX 0.28.1，NetworkX 3.7，python-docx 1.2.0，Pillow 12.3.0，pytest 9.2.0，pytest-asyncio 1.4.1.post1，Ruff 0.17.4，pypandoc-binary 1.17，Pandoc 3.9。依赖记录在 `uv.lock`。

测试使用项目 `.venv`；LibreOffice 与 PDF 栅格化使用 Codex 随附运行时。当前机器用本地工作目录内的 Heiti 字体副本和显式 Fontconfig 配置完成中文检查；这不表示生产镜像字体已配置完成。

## 命令与结果

均从项目根目录执行，以下命令未读取真实凭据或发送真实试卷。

| 命令 | 结果 |
|---|---|
| `make check` | 退出 0；Ruff 通过，57 passed、1 skipped。跳过项为需显式配置真实转换器的集成测试 |
| 设置 `TEASSISTANT_SOFFICE`、`FONTCONFIG_FILE`、`SAL_FONTPATH` 后运行 `.venv/bin/pytest -q` | 退出 0；58 passed，无跳过。覆盖模拟 HTTP 响应 → 合同 → 组卷 → 真实 DOCX/PDF 转换 |
| `.venv/bin/python scripts/generate_schemas.py` | 退出 0；生成 `contracts/p0-v1/` 下 5 份 Schema |
| `.venv/bin/python scripts/selection_demo.py` | 退出 0；12 道合成候选中抽取 10 题，无缺口 |
| `.venv/bin/python scripts/evaluate.py --output work/evaluation.json` | 退出 0；结果如下节 |
| `scripts/export_demo.py --output work/export-reviewed`，显式指定 Pandoc 和随附 soffice 路径 | 退出 0；学生与教师 DOCX／PDF 各一份 |
| 随附 `pdftoppm` 对两份 PDF 栅格化 | 退出 0；各 1 页；集成负责人逐页查看完成 |

导出命令示例见 README。转换器与字体变量须在调用进程中设置，不把本机绝对路径写成应用默认值。

## 合成评测结果

| 项目 | 实际分子与分母 |
|---|---|
| 合成样本解析覆盖 | 2 / 2 |
| 预期大题覆盖 | 3 / 3 |
| 有独立完整标记的大题 | 2 / 3 |
| 已解析预测关联正确 | 2 / 3 |
| 预期关联召回 | 2 / 3 |
| 未确定关联 | 1 |
| 重复大题／额外题／错误关联 | 各 1 项 |
| 真实样本 | 0；真实指标为 null，未验收 |

fixture 故意包含错误与未确定项，证明计数器可暴露问题；这些比例不表示真实 OCR、数学诊断或题目难度质量。不同计数类别可能重叠，不能相加当作总题数。

## 实际导出与视觉证据

四份最终产物位于 `work/export-reviewed/`，均为明确合成的样张。产物摘要、字节数和 XML 核对记录在 `work/export-evidence.json`，页面截图在 `work/export-qa/reviewed-*.png`。这些本地文件由 `.gitignore` 排除，代码与运行脚本可重新生成。

- 学生 DOCX：43,553 字节，3 个 OMML 数学节点，1 张嵌入图片。
- 教师 DOCX：37,029 字节，4 个 OMML 数学节点。
- 两份 PDF 均有实际 PDF 头且各 1 页。
- 已核对中文、分式、根式、方程组、上下标、三角形及图形标记；题号与三个小问答案对应，无明显裁切。学生版不含独立答案内容。
- 标题采用黑色并去除模板继承的蓝色边框；分组显示考点名，不显示内部 ID。
- 尚未通过 Word 图形界面实际编辑，未测真实长题跨页及所有复杂公式。

## 独立缺陷修复

独立检查曾发现并复现：空教学进度仍能组卷、快照绕过原题排除、公式转换静默丢后半段、失败解析贡献成功、多个小问误计重复题、XML 节点类型不兼容、指纹混入小问 ID 与答案导致同题去重失效。已修复并进入回归测试；详细初次失败与复验记录见 `p0-review.md`。

集成阶段还修正了配额严格整数校验、合成样题答案、继承样式边框与图形标记字号。没有引入题库人工审核。

## 进入完整 P0 的条件

仍需真实试卷／批改答题卡／标准答案、实际模型账号环境、完整考点目录与难度锚题。拿到这些输入后执行真实识别和关联评测，记录覆盖、失败、耗时与用量，再用真实代表题核对 Word／PDF。未满足前，不宣布真实 P0 通过或越过门槛交付完整 MVP。
