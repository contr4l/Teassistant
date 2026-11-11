# 统一考点与难度评级试跑

此入口对应 FR-03/04、DEV-003/202，复用 V3 真实题目并保存独立语义标记。完整业务题库桥接和数学导出适配尚未由此入口交付，不将来源题直接改为 ready，也不增加题库人工审核。

## 当前状态

2026-11-11 已完成固定200题的AI初评：基础123题、提高55题、挑战22题，最终失败0、未尝试0。全部结果校验到93点工程目录V2；其中177题保留原目录模型初评并显式兼容迁移，23题使用新目录真实重评，页面、CSV和JSON均区分来源。

本轮采用用户选定的Codex CLI开发验证。App内置 `0.167.0-alpha.3.2` 与显式 `gpt-6.1-sol` 已实际调用成功；可执行文件路径和二进制SHA纳入run身份。最初旧进程的 `model_not_found` 及失败记录保留，不据此推断账号没有模型权限，见[通路恢复记录](acceptance/semantic-cli-recovery.md)。

所有200题共221张完整输入图。193题可用关联答案/解析；7题仅用原卷题干，未借用不确定答案。36道没有来源词条的题也全部得到初评，来源词条不替代统一映射。

项目内最终离线报告位于 `evals/private/semantic-v2-unified-final/index.html`；CSV、完整JSON和图片同目录。评分尚未教师或学生数据校准，未自动发布为业务组卷题。完整两轮口径与局限见[真实执行记录](acceptance/semantic-v1-live-execution.md)及[独立验收](acceptance/semantic-v1-live-review.md)。

## 目录与评分规则

`data/curricula/junior-math-2022-draft-v2.json` 含 93 个非合成、稳定 ID，V1 的原 90 点保持不变，兼容既有 `Curriculum` 与前置关系无环校验。来源依据和状态单独存于 `junior-math-2022-draft-v2-provenance.json`。

目录按教育部 2022 年版数学课标的初中范围编制。具体标签粒度、ID 与前置边为本项目工程草案，尚未与目标老师使用教材及教学进度核对，不称为湖南官方目录。后续题库、学生试题和进度须共用同一版本，不能各自创建未知 ID。综合标签不能代替题目实际涉及的具体知识。

`junior-math-demand-v1` 采用既有工程分段：0～39 基础、40～69 提高、70～100 挑战。模型依据整道题的知识综合、推理链、运算和关键构造解释评分；题号、来源、篇幅和学生排名均不能直接替代难度判断。模型分数尚需教师或学生数据校准，不解释为得分率。当前可报告上述模型初评分布；标签准确率和难度校准准确率仍未测。

## 渲染输入

```sh
make setup
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/render_semantic_inputs.py \
  evals/private/question-bank-v3 evals/private/semantic-v1-inputs
```

已有 Chromium 可通过 `--chromium-executable /absolute/path/to/chromium` 指定。输出必须是新目录；失败不发布半成品。渲染器只接受库内本地媒体，生成可校验的图片，不执行题目中的指令或远程内容。manifest 保留每题源摘要、图片 SHA、原截图尺寸及分片坐标，分片有重叠而不缩小丢字。

真实 200 题输入已保留，无需重新渲染。图片或源题改变会导致下游校验失败，需要新版本输入。

## 显式运行

Codex CLI 开发验证须显式指定模型和本机已验证的可执行文件。以下为本机成功配置，其他机器请改为其实际可用配置：

```sh
.venv/bin/python scripts/annotate_question_bank.py \
  --bank evals/private/question-bank-v3 \
  --inputs evals/private/semantic-v1-inputs/inputs.json \
  --curriculum data/curricula/junior-math-2022-draft-v2.json \
  --provider codex --codex-model gpt-6.1-sol \
  --codex-executable "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" \
  --output evals/private/semantic-trial-new \
  --batch-size 4 --concurrency 2
```

可用 `--question-ids-file /path/to/ids.json` 先选小样，文件是固定题目 ID 的 JSON 数组；报告仍保留全部来源题目分母，其余明确为未尝试。`--timeout` 限制每批 CLI 子进程，超时终止整个进程组。CLI 使用只读、临时会话和 `--ignore-user-config`；复用认证、不会加载全局 hooks/MCP 配置或自动更换服务。图片会提交给所选模型服务，不是本地推理。[OpenAI 非交互用法](https://learn.chatgpt.com/docs/non-interactive-mode)

DeepSeek 为唯一业务模型通路，需显式选择且准备本机凭据：

```sh
uv run --no-sync --env-file .env python scripts/annotate_question_bank.py \
  --bank evals/private/question-bank-v3 \
  --inputs evals/private/semantic-v1-inputs/inputs.json \
  --curriculum data/curricula/junior-math-2022-draft-v2.json \
  --provider deepseek --output evals/private/deepseek-semantic-new
```

配置沿用 `.env.example`。真实 DeepSeek 图像能力、模型权限和评分质量仍须实际验证；Mock 测试不代表服务可用。

## 保存、续跑与结果解释

| 文件 | 内容 |
|---|---|
| `run.json`、`curriculum.json` | 固定源库、输入、目录、提示及评分版本、显式模型；目录原始字节快照 |
| `calls/NNNN/` | 每批题目清单、实际提示、响应、尝试/用量可得性、耗时或安全错误分类 |
| `annotations.json` | 全部题目状态、源摘要、模型标签/分数/依据，程序派生的前置闭包单独保存 |
| `summary.json`、`reports/` | 最新状态及保留的历史统计；全部分母、成功/失败/未尝试分开 |
| `annotations.csv`、`index.html` | 查看标签、分数、解释及完整输入图片；HTML 使用 preview-images/ 下相对地址，可连同图片完整目录分发 |

模型输出经结构、严格整数评分、逐标签证据和白名单校验；重复、缺失、额外或非法 ID 不能增加成功数。模型明确输出证据不足时保留失败原因及空分数。程序补足前置闭包，不伪装成模型逐项判断。即使已有答案，数学矛盾也只作为可追踪疑点，不将来源关联视作答案正确性证明。

同一来源、输入、目录及模型配置的输出可加 `--resume` 继续未尝试题。失败需额外 `--retry-failed`，原始响应保留，新尝试使用新的批次编号。中断留下的 running 在续跑时标为结果未知的失败，不静默当作成功或自动重复计费；同一输出目录有排他进程锁。源/目录/模型或提示版本变化须使用新输出目录。

本地进程调用尝试可计数；CLI 内部网络重试、服务器实际执行数及不可获得用量保持未知。结构校验通过比例不能改名为考点准确率或难度准确率。退出 0 表示全部结构标记成功，仍有失败、未尝试或运行中则退出 3；配置和输入错误退出 1。

## 已有结果的目录升级

旧结果不会随目录文件改变自动变成新模型输出。本批按[目录兼容合同](acceptance/semantic-catalog-v2-contract.md)冻结23题受影响清单，真实重评后用 `scripts/merge_semantic_bank.py` 合并，其余177题逐项回溯原响应并做新白名单/闭包校验。每行保留原模型目录、当前索引目录、迁移方式和来源摘要；命令及固定摘要见真实执行记录。
