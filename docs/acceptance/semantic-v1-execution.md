# 考点统一与难度评级 V1 执行记录

日期：2026-11-11。对应 FR-03/04、DEV-003/202；遵循[合同](semantic-v1-contract.md)，独立检查见[验收报告](semantic-v1-review.md)。用户明确选择 Codex CLI 做本轮开发验证。

## 结论与固定分母

**完整输入和批处理已准备，真实评级尚未完成。** 当前 Codex 推理服务对试用模型均返回 `HTTP 400 / model_not_found`。没有填入规则占位分数、将来源词条当成正式标签，也没有改用未授权供应商。

| 实际指标 | 数量与口径 |
|---|---:|
| 来源题目固定分母 | 200 |
| 来源关联确认 / 未确认 | 193 / 7 |
| 完整渲染题目 / PNG 分片 | 200 / 221 |
| 非合成工程目录稳定 ID | 90 |
| 预先固定 pilot / 覆盖来源对 | 12 / 8 |
| 已尝试评级 / 未尝试 | 12 / 188 |
| pilot CLI 批次启动 / 成功模型响应 | 3 / 0 |
| 有效统一标签与难度 / 技术失败 | 0 / 12 |
| 新增真实难度分数 / 正式 ready | 0 / 0 |
| 独立标签 gold、老师锚题或实测得分率 | 未提供，准确率与校准未测 |

12 题分层样本在读取模型输出前冻结，覆盖无来源词条、表格、几何、综合解答及未确认关联。全部 200 题仍在结果文件中；失败不移出分母。0/200 为有效评级产出覆盖，不能解释成标签或难度准确率为 0%。

## 实际输入与规则

来源库为 `evals/private/question-bank-v3/`，bank SHA-256 `6422f96a33ae72d373070c6dc7b1bdee912e45a4bf82afea5bba05ee2f40a58f`。语义输入为 `evals/private/semantic-v1-inputs/inputs.json`，SHA-256 `434a309a530fa2dba6dd07a428622d43364ff304610c36a2f41f339b9812afcf`。

渲染保留原题的公式、图形和表格，长图分片不缩小，记录原图总尺寸及起止坐标。独立全量检查了所有 PNG 摘要、尺寸、覆盖和重叠像素，全部通过；视觉抽查看过统计表、无来源词条题和跨片综合题。193 题可使用已关联答案及解析；7 题仅含原卷题干与配图，没有把未确认解析混入评级依据。

目录在 `data/curricula/`，含 90 个稳定 ID、来源元数据和无环前置图。最终摘要 `cff29b09faeb50cfc3f63007d757b1eac95e011b66e68155de53151153fd2e37`。标签粒度与依赖由本项目编制，是非合成的工程草案，尚未经目标老师教材进度对齐，不称为官方湖南目录。

最终提示 `source-semantic-prompt-v2` 要求完整大题评级、具体复合考点及逐标签依据，已有解析仅作参考，数学矛盾在 warnings 留痕。评分版本 `junior-math-demand-v1` 采用既有 0～39／40～69／70～100 工程分段，没有将其写成学生实际得分概率。源、输入、目录、提示或显式模型变化须建立匹配的新 run。

## 真实尝试与阻塞证据

初次试跑保留于 `evals/private/semantic-v1-codex/`。实际命令：

```sh
.venv/bin/python scripts/annotate_question_bank.py \
  --bank evals/private/question-bank-v3 \
  --inputs evals/private/semantic-v1-inputs/inputs.json \
  --curriculum data/curricula/junior-math-2022-draft-v1.json \
  --provider codex --output evals/private/semantic-v1-codex \
  --question-ids-file work/semantic-v1/pilot-ids.json \
  --batch-size 4 --concurrency 2 --timeout 480
# 当时版本允许未指定模型；exit 3，12 failed / 188 not_attempted
```

该历史 run 使用目录前一摘要 `ebf35745df30045c171efc4bf2bd99babbf765902e89f09cc66f70a78ecd9ed6` 与提示 v1。后续目录及提示改善没有覆盖旧 run 或冒称曾成功验证新版本。最新批处理已经要求显式 `--codex-model` 或 `CODEX_MODEL`，以免在整批里隐式使用不可用的编译默认值；上面的历史命令因此不作为最新重跑示例。

登录状态检查退出 0。独立的单句合成提示连通诊断分别检查 CLI 默认、显式 `gpt-6.1-sol` 和 `gpt-6-luna`，进程均退出 1，推理服务均返回 `400 / model_not_found`；默认实际报告模型为 `gpt-5.6-sol`。这些不是实际试题评级成功，也不使用开发 Agent 自身能否运行来推断 CLI 账号权限。官方[模型说明](https://learn.chatgpt.com/docs/models)和本机列表用于确定检查候选，实际服务拒绝仍以当次响应为准。

没有修改全局配置、读取或输出凭据、切换账号、开通付费服务或自动更换业务供应商。私有诊断归纳在 `work/semantic-v1/cli-connectivity-summary.json`；完整私有原始模型文本仅在可获得时保存。CLI 非零退出没有成功模型 JSON，故原始评级响应为 null，而非伪造空成功结果。用量不可获得，未估算费用或服务器实际执行数。

用户已收到补充配置问题：提供本机实际可调用的 Codex 模型 ID，或显式改选 DeepSeek 并在本地 `.env` 配置。配置到位后使用[最新运行说明](../semantic-bank.md)新建 run，先复验同一 pilot，再扩展到全部题目；没有擅自继续 188 题的确定失败调用。

## 实现、回归与保护

新增独立侧附合同、模型适配、完整渲染、白名单与闭包校验、批处理和本地结果页。旧 `Question` 合同、组卷规则与原来源内容均未变更。DeepSeek 保留业务通路，Codex 结果记录开发验证模式，均须显式选择。

每批保存提示、输入摘要、模型配置、响应和错误；未知标签、非法分数、漏/重复/额外 ID 均可见。派生前置闭包不伪装成模型证据。续跑要求相同版本及来源，中断结果未知则标失败，显式重试保留旧响应；进程锁阻止同一输出并发恢复。模型/提示变化后的旧缓存不会被静默复用。

最终主会话实际执行 `make check`，退出 0：**Ruff 通过，300 passed / 1 skipped**，日志 `work/semantic-v1/final-check.log`。跳过项为已有真实转换器集成未配置 `TEASSISTANT_SOFFICE`；本批未改导出，不据此声称新 PDF 验收。独立检查者较早全量执行为 298 passed / 1 skipped，后续主会话增加两项运行恢复与错误证据测试，时间点分开记录。

独立反例覆盖严格分数、白名单、复合证据、源/图片漂移、目录环、全 200 集合、未确认输入隔离及长图末尾；模型适配测试使用 mock，不作为真实推理效果。新增 Playwright 开发依赖已同时更新 `pyproject.toml`、`uv.lock`，本机使用 1.65.0；只用于当前研发输入渲染，不称为已部署的生产浏览器 Worker。

独立重新计算 16 个原件与 V3 全部 4338 文件摘要，均与批前完全相同。真实题目、输入图片、响应、模型诊断和本地报告均保持 Git 忽略，没有提交到源码，也没有覆盖先前题库或交付 ZIP。
