# 200 题考点统一与难度初评：真实执行结果

日期：2026-11-11。对应 FR-03/04、DEV-003/202；使用用户明确选择的 Codex CLI 开发验证通路。依据[语义合同](semantic-v1-contract.md)与[目录兼容迁移合同](semantic-catalog-v2-contract.md)，独立检查见[真实验收报告](semantic-v1-live-review.md)。保留[最初失败记录](semantic-v1-execution.md)，不将历史失败覆盖成成功。

## 最终交付

固定的 **200 道真实来源题均已有 AI 考点与难度初评**，最终失败 0、未尝试 0。全部结果统一校验到 93 点工程目录 `junior-math-2022-draft-v2`，保留模型逐标签依据、前置知识、理由和警告。

| 指标 | 实际结果 |
|---|---:|
| 固定预期题目 / 唯一输出 | 200 / 200 |
| 最终有效初评 / 失败 / 未尝试 | 200 / 0 / 0 |
| 基础 0～39 / 提高 40～69 / 挑战 70～100 | 123 / 55 / 22 |
| 实际分数范围 | 8～94 |
| 原目录初评兼容迁移 / 新目录真实重评 | 177 / 23 |
| 无现成来源词条题的成功初评 | 36 / 36 |
| 题答来源未确认、仅按原题干评级 | 7 |
| 有模型 warnings 的题目 | 20 |
| 最终目录 / 实际目标 ID 种类 / 目标及显式前置 ID 种类 | 93 / 82 / 87 |
| 正式业务 ready | 0 |

这里的完整产出覆盖不代表“标签准确率或难度准确率 100%”。评分是 AI 初评，未以老师锚题、学生实测得分率或完整独立金标准校准。资料实际为九年级试卷，不声称已覆盖初一、初二的代表题。本批没有交付来源层到旧业务 `Question` 的完整内容/定位/数学导出桥接，也没有自动发布为可组卷题。

## 模型通路恢复与实际身份

本机终端 CLI 与 App 内置 CLI 存在版本/调用路径差异。主会话在 App 内置 `0.167.0-alpha.3.2` 上使用显式 `gpt-6.1-sol` 成功返回合成 `OK` 后，才重新试跑真实小样。详见[恢复记录](semantic-cli-recovery.md)。没有修改登录、全局配置或企业策略，未增加业务供应商或自动回退；DeepSeek 仍为唯一业务 provider。

每个新 run 固定可执行文件绝对路径和完整二进制 SHA-256，并与源、输入、目录、提示、评分及模型配置一起限制续跑。最终两轮使用相同模型、相同二进制和评分规则；版本保存在私有 `client-runtime.json`。模型身份来自调用配置和真实 CLI 结果记录，不使用模型自报名称。CLI 内部传输重试及不可获得用量保持未知，不估算费用。

## 两轮执行与缺口修复

### 首轮完整 90 点目录

来源仍是 16 份 DOCX、8 套各 25 题。固定 12 题 pilot 全部成功，独立内容抽查通过后，续跑其余 188 题。最终首轮为 192 assessed、8 failed、0 未尝试；50 个 CLI 批次中 48 个有效、2 个因响应结构失败被拒绝。

两批失败都保留了原响应。实际原因是模型发现目录缺少整除/最小公倍数及真假陈述推理的合适标签，拒绝套入无关点，却输出了 assessed 与空目标的矛盾结构。严格合同因此拒绝整批四项，没有把已有分数或不相干标签强行写成成功。

独立全量阅读还发现正多边形边长/外接圆半径/边心距、奇偶基础的明确命名缺口。于是新建 V2，仅追加三个工程点：

| 新 ID | 名称 | 来源与范围 |
|---|---|---|
| `JM.NA.INTEGER_DIVISIBILITY` | 整数整除、奇偶性与最小公倍数 | 跨学段基础补充，来自整除条件题及奇偶概率题 |
| `JM.CP.TRUTH_REASONING` | 真假陈述与分类、排除推理 | 有真假陈述/唯一说谎者约束的工程方法粒度，不泛化到全部几何分类 |
| `JM.GE.REGULAR_POLYGON_CIRCLE` | 正多边形的边长、外接圆半径与边心距 | 真实正六边形题暴露的专题；勾股等知识按具体题另列 |

原 90 个点的原始 JSON 字段、名字、前置及所有旧闭包完全不变。新点不冒称官方湖南 ID 或新增官方课标条目，尚未与老师教材进度逐章校准。

### 冻结 23 题后真实重评

在 V2 模型响应前冻结 `work/semantic-v1/affected-v2.json`，SHA-256 为 `175871ff9aa8da9781e31f63625e03b5ce40970a60598a78bd58fd630bf00eb1`。全部源题、旧响应 reasoning/warnings/证据经过扫描，纳入全部 8 个失败题及 15 个保守候选。弱候选也保留并真实重评，不强制添加新标签；不按输出结果删选样本。

V2 的 23/23 题在 6 个 CLI 批次中成功，0 失败。其余 177 题保留原始 V1 assessment，不改标签、分数、理由或 warnings；从原始响应重新校验后，显式迁移至 V2 索引。每题分别保存模型实际输入目录版本、当前校验目录、来源 run/call/request/result/item 摘要和迁移方式。

因此最终是 **177 题兼容迁移 + 23 题真实重评**，不能描述成“200 题都让模型看过 V2 重新推理”。受影响题若重评失败，程序禁止回退旧成功结果；本批最终没有此类失败。

## 证据与校验

| 固定材料 | SHA-256 |
|---|---|
| 来源 V3 bank | `6422f96a33ae72d373070c6dc7b1bdee912e45a4bf82afea5bba05ee2f40a58f` |
| 200 题、221 PNG 的输入 manifest | `434a309a530fa2dba6dd07a428622d43364ff304610c36a2f41f339b9812afcf` |
| 模型首轮目录 V1 | `cff29b09faeb50cfc3f63007d757b1eac95e011b66e68155de53151153fd2e37` |
| 最终目录 V2 | `b158dcdf9c458754eb271b15a945f3e0d7281928e43ae5e7b53b23684c3967e5` |

独立检查者逐 200 题核对原 run、call、request、prompt、result 和响应项，与原 sidecar 及最终 assessment 完全一致；还解析全部 56 个批次实际 prompt 中的目录 JSON、题目文字和图数，匹配各自版本及固定输入。所有目标/前置在 V2 白名单内，严格整数分数与前置闭包合法。旧 pilot、首轮、16 个原件及来源 V3 全部 4338 个文件保持不变。

合成反例覆盖缺题、重复、未知 ID、非法分数、旧点变化、原始 JSON 空白被 Pydantic trim 掩盖、篡改响应/sidecar、受影响失败禁止回退、旧闭包及二进制变化拒绝续跑。独立检查发现的字面目录比较漏洞已修复，随后在新目录 `semantic-v2-unified-final` 重跑合并；annotations、summary、curriculum 与先前真实数据逐字相同，没有重新编造模型结果。

最终实际 `make check` 退出 0：**Ruff 通过，320 passed / 1 skipped**，日志 `work/semantic-v1/live-final-check.log`。唯一跳过项为未配置 `TEASSISTANT_SOFFICE` 的既有真实转换器集成；本批未改导出，不将该跳过项当作新 PDF 通过。

无头 Chromium 实测最终离线报告：200 题卡、221 图片全部加载；基础/提高/挑战筛选分别为 123/55/22，新增真假考点筛选为 2 题，空搜索提示正确，无页面异常，无手机横向溢出。另实际查看桌面与手机截图。页面及 CSV 明示迁移与重评来源，复制图片使用安全相对路径，可独立分发。

## 可复现入口与交付

完整首轮：

```sh
.venv/bin/python scripts/annotate_question_bank.py \
  --bank evals/private/question-bank-v3 \
  --inputs evals/private/semantic-v1-inputs/inputs.json \
  --curriculum data/curricula/junior-math-2022-draft-v1.json \
  --provider codex --codex-model gpt-6.1-sol \
  --codex-executable '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex' \
  --output evals/private/semantic-base-next --batch-size 4 --concurrency 3
```

V2 针对性重评使用 V2 目录、冻结的 `--question-ids-file work/semantic-v1/affected-v2-ids.json` 与新输出目录。不可重用历史版本的 affected 摘要到另一个新 run；新首轮的文件摘要变化后，应重新冻结对应清单。

本批实际合并命令（输出必须是新目录）：

```sh
.venv/bin/python scripts/merge_semantic_bank.py \
  --base-run evals/private/semantic-v1-codex-bundled \
  --reassessed-run evals/private/semantic-v2-codex-bundled \
  --bank evals/private/question-bank-v3 \
  --inputs evals/private/semantic-v1-inputs/inputs.json \
  --affected work/semantic-v1/affected-v2.json \
  --output evals/private/semantic-v2-unified-final
# exit 0：200有效初评，177兼容迁移、23重评
```

项目内最终结果为 `evals/private/semantic-v2-unified-final/`，含离线 HTML、CSV、完整 JSON、目录及迁移证据和 221 张预览图。另交付 `长沙九年级数学题库_考点与难度初评_V1/` 及同名 ZIP，保留原有 V3 与旧 ZIP。打包逐文件 SHA 相同、ZIP 逐条 CRC 通过，说明文件标明开发初评和版本来源。

所有真实题目、图片、原响应及私有报告保持 Git 忽略，没有提交到源码。开发设计与独立验收使用 `gpt-6.1-sol/high`，并行实现实际使用 `gpt-6-luna/high`；它们与本轮实际业务数据推理的 Codex CLI 配置身份分开记录。
