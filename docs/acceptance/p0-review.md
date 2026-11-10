# P0 离线基础独立检查

历史批次记录：后续模型接入调整为 DeepSeek 与 Codex CLI，当前接口验收见 [模型适配器检查](model-adapters-review.md)。本页保留当时的缺陷复现与测试证据。

检查日期：2026-11-10。检查者：`gpt-6.1-sol`，`high`。实现者为集成负责人及两位 `gpt-6-luna` / `high`。本报告只记录实际执行和代码检查，不作为真实 P0 出口或 MVP 上线结论。

## 范围与依据

依据 PRD FR-02～04、FR-06、FR-11～15，以及 DEV-001～004 的离线基础部分。提前验证组卷硬约束，不表示 P4 完成。不增加题库人工审核流程。

检查共享合同、考点前置闭包、容量匹配与身份组去重、业务模型适配器、评测器及实际数学导出；独立测试在 `backend/tests/test_lead_acceptance.py`。

**最终结论：本批 P0 离线基础验收通过。独立全量执行为 58 passed，实际转换器集成测试已运行，没有跳过；lint 通过。真实试卷与真实业务模型调用均为 0，完整 P0 出口尚未验收。**

## 实际执行记录

执行目录：项目根目录 `/Users/ftan/personal/Teassistant`。环境使用项目 `.venv`；具体版本见最终集成报告。

| 命令 | 结果 | 说明 |
|---|---|---|
| `.venv/bin/python -m pytest backend/tests/test_lead_acceptance.py -q` | 13 passed，2 failed；退出 1 | 检出未设置进度仍生成快照、快照允许选入配置排除的原题 |
| 同命令，扩展评测与实际 Pandoc 反例后 | 16 passed，3 failed；退出 1 | 失败解析仍计成功、小问关联误计重复、公式第二段丢失 |
| `.venv/bin/python -m pytest backend/tests/test_lead_acceptance.py -q`，补充实际导出入口边界后 | 21 passed；退出 0 | 五项已复现规则缺陷全部修复；进度撤回和嵌套篡改在转换前拒绝 |
| `.venv/bin/python -m pytest -q`，首次全量独立运行 | 52 passed，2 failed；退出 1 | 剩余失败为转换器缺失异常类型及真实 OMML 插入类型不兼容 |
| `.venv/bin/python scripts/evaluate.py --output /tmp/teassistant-lead-eval.json` | 退出 0 | 实际运行离线 CLI，未读取凭据、未调用业务模型 |
| `.venv/bin/python scripts/evaluate.py --output /tmp/teassistant-lead-eval-final.json` | 退出 0 | 最终 fixture 重跑；结果与下方最终分母一致 |
| 最终带真实转换器的全量测试，完整命令见下 | **58 passed in 3.18s，退出 0** | 包括 HTTP MockTransport → 合同 → 组卷 → 实际 DOCX/PDF 的集成路径，未发真实模型请求 |
| `.venv/bin/python -m ruff check backend scripts` | **All checks passed，退出 0** | 最终代码静态检查 |

通过的独立边界包括：全局匹配避免贪心假缺题；传递同题身份组容量为 1；排除同组原题；共享容量冲突返回真实缺口；目录传递前置知识与多标签进度限制；进阶配额严格整数及总数；评分版本一致；未知前置知识与环检测；题目及请求深复制；当前进度撤回与学生归属；快照超配额和未报告缺口拒绝。

## 问题与复验

| 编号 | 触发与影响 | 状态 |
|---|---|---|
| R-01 | `learned_point_ids=[]` 时 `build_practice` 返回缺题快照，未按 FR-06 拒绝未设置进度 | 集成负责人修复；同一命令复验通过 |
| R-02 | 反序列化快照可同时包含已选题与 `request.excluded_question_ids` 中同 ID，绕过原题排除 | 集成负责人修复；同一命令复验通过，补充 canonical ID 排除测试 |
| R-03 | 真实 Pandoc 对公式 `x$$\n\n$$y` 产生多个数学节点，导出只提取第一个，成功返回时 `y` 已丢失 | 导出实现者修复，独立反例已明确拒绝并复验通过 |
| R-04 | `invalid_json` 的预测仍带 items 时可贡献题目覆盖和关联成功 | 评测实现者修复，矛盾失败输出在合同校验时拒绝，独立复验通过 |
| R-05 | 同一大题两个不同小问的关联记录被当作重复大题 | 评测实现者修复，独立复验通过 |
| R-06 | Pandoc 返回标准库 XML Element，但 python-docx 只能插入 lxml Element；实际含公式 DOCX 构造抛出 TypeError | 导出实现者修复，真实公式构造及完整转换路径复验通过 |
| R-07 | 相同题干的小问 ID 或答案格式改变后，指纹随之改变，同题不同来源可绕过去重 | 独立 HTTP MockTransport 回归实际复现；实现者改为有序题干与小问题干指纹，最终复验通过 |

初读时发现的 `by_difficulty` 类型强制转换问题已由集成负责人修正，独立的 `True`、`"1"`、`1.0` 三项拒绝测试通过。

首次修复后复验命令相同，结果 `15 passed in 0.23s`，退出 0；实际 Python 为 `3.13.7`。随后为原题排除增加 canonical ID 参数覆盖，最终计数见后续执行记录。

## 独立评测结果与环境

最终 CLI 使用仓库固定的合成 manifest：2 个已提交样本、3 个预期大题、3 个预期关联。题目覆盖为 3/3；独立完整标记为 2/3；已确定预测关联中正确 2/3，关联召回为 2/3 个预期关联。未确定关联 1 项、错误关联 1 项、重复大题 1 项、额外题 1 项；这些计数类别可能重叠，不能相加当作总题数。真实样本分母为 0，比例显示 null。该 fixture 故意保留错误与未确定结果，不是模型输出。

相同正确关联重复出现仅贡献一个正确关联，预测分母仍保留重复项；未确定关联不能贡献成功，另报数量并在预期关联召回分母中保留。这只是计数器行为，无真实识别质量含义。未提交样本单独列数，不进入质量分母；已提交但失败或缺少预测的样本仍保留在分母。

实际版本：Python 3.13.7、Pydantic 2.14.3、NetworkX 3.7、HTTPX 0.28.1、python-docx 1.2.0、Pillow 12.3.0、pytest 9.2.0、Pandoc 3.9。版本由项目 `.venv` 的 `importlib.metadata.version` 与 `pypandoc.get_pandoc_version()` 实际读取，不代表其他平台兼容性已经验证。

最终独立全量命令：

```sh
TEASSISTANT_SOFFICE=/Users/ftan/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice \
FONTCONFIG_FILE=/Users/ftan/personal/Teassistant/work/fonts.conf \
.venv/bin/python -m pytest -q
```

转换器实际版本为 `LibreOfficeDev 26.8.0.0.alpha0 2c87e51eeaa2b413ff4ae097b2705eea1995d8e5`，属于本地验证环境，尚未锁定生产容器转换器及字体。

## 实际导出独立检查

集成负责人生成四份文件，独立检查者读取 `work/export-reviewed/{student,teacher}.{docx,pdf}`，查看 `work/export-qa/reviewed-student-1.png` 与 `reviewed-teacher-1.png` 的完整两页渲染。学生 PDF 与教师 PDF 经 `pdfinfo` 确认为各 1 页，PDF 1.7；大小分别为 58,635 和 46,274 字节。

已独立观察：中文与考点名称可读；分式、根式、方程组、上下标均显示；三角形及 A/B/C/h 标记完整可读；三个小问与答案一一对应；学生版不含教师答案；标题蓝线已去除，未见明显裁切或内容重叠。合成题的方程答案为 x=5，面积为 12，方程组为 x=3、y=2，正数 a₁=3，内部一致。

同时直接读取 DOCX ZIP/XML：学生版有 3 个原生 `m:oMath` 节点和 1 张嵌入图片，教师版有 4 个原生数学节点；题号和小问文本与视觉产物对应。另检查了实际 PDF 文件头 `%PDF-1.7`。这些证明本样张的结构与渲染保留，不证明 Word 图形界面编辑体验；没有进行 Word GUI 编辑、真实卷面、长题跨页、打印或多平台字体核对。

## 完整 P0 仍需满足的条件

- 收集真实且获准研发使用的试题、批改作答/独立答题卡与答案，建立可回到原页的固定核对索引；现有 2 个合成样本不能替代 DEV-001 的代表性样本目标。
- 在实际账号地域探测可用视觉与文本模型，固定模型、提示词和评分版本，真实执行页面识别、跨页拆题、小问与作答关联；记录失败、漏检、覆盖、正确率、耗时和用量。当前 HTTP MockTransport 测试不证明服务接口或模型效果可用。
- 建立完整湖南初中考点目录草案并与目标老师教学范围核对一次，准备难度锚题和前置知识覆盖检查；本批合成目录不能证明实际目录完整性，也不构成逐题人工审核。
- 用真实代表题核对 Word 可编辑文字与公式、四份文件题号对应、中文字体、几何图形、长题跨页和打印裁切，明确不支持公式的失败或回退策略；本批合成导出不替代 DEV-004 的真实代表题验收。
- 按上述证据更新技术选型并说明剩余误差。开发计划中 95% 为候选工程门槛，不能由合成样例达标或只统计成功项宣称真实 P0 已通过。

## 证据边界

本批真实试卷样本为 0，真实业务模型调用为 0；合成与确定性规则测试不证明真实卷面识别、答案关联、难度校准或老师备课价值。模型适配器仅在 HTTP MockTransport 下验证合同、重试、白名单和元数据；评测 CLI、实际 Pandoc/LibreOffice 四份文件转换和合成样张视觉核对均已执行。权限、持久队列、完整页面识别流水线、老师真实备课闭环均不在本批完成范围。近似扫描重复识别及真实图形题指纹稳定性仍未验证。
