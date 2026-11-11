# 200 题关联核验 V3 独立验收

独立设计与验收：`gpt-6.1-sol` / `high`；实施模型按实际 `gpt-6-luna` / `high` 记录。本批针对用户明确要求的固定 200 题关联通过率 ≥95%，不调用模型、不新增人工题库审核、不将来源题变为正式可组卷题。

## 固定分母及证据边界

在读取 V3 输出前，已从完整 V2 固定全部 200 个唯一题目 ID、8 对各 25 题及 16 个原件 SHA-256，并记录源 XML/rels、原/解析题干与答案快照摘要。私有 manifest 为 `work/association-v3/review-manifest.json`，SHA-256 为 `652f17654d0da9748bfdc8562af2e73f0252d8c78673c7790c6d316b2b7eb33c`。生成器拒绝静默覆盖。旧 V1/V2 和输入保护摘要保存在 `review-protection-hashes.json`。

通过分子只能由该固定集合中的唯一题目贡献，分母始终 200，至少 190 才达标；缺失、未确认、失败、不支持均不移出分母。重复或额外 ID、来源变更、冲突输出不能用于挑选通过结果。真实比例与合成负例分开；合成负例要求零误确认，不能被真实比例抵消。

本批允许的 `native_mtef_exact` 是完整原生对象 payload 身份证据：检查 CFB、Equation Native 流、wrapper 声明长度及可识别头部后，完整 payload 每个字节参与比较，不删除未知记录，不做数学归一化。它不是数学 AST 解析，也不把末字节 END 当成任意 MTEF 语法完整性的证明。未绑定 OLE 的图片仍须单独严格比较。

原生内容与显示预览是不同证据层。[MathType 官方说明](https://docs.wiris.com/interface-and-usability/a-different-equation-opens-in-mathtype-than-the-one-double-clicked-in-word)指出，损坏文档可能出现两者不同步。本批采用原生身份依赖题源质量前提，不能由原生相同宣称已独立证明全部 PNG 的数学语义；实际发现冲突必须保留为未确认。

## 独立验收结论

2026-11-11 冻结的私有 `evals/private/question-bank-v3` 达成本批用户门槛：**193 / 200 = 96.5%，高于 95%**。独立从原件重算的 200 个预期状态与正式产物逐 ID 一致；7 题保留未确认，没有删题、降低像素阈值、使用模型或强改状态。固定评测器退出 0、`passed=true`；来源构建本身仍为 `partial`、退出 3，因仍有 7 题未确认，两者不矛盾。

本结论是有明确证据层级和题源质量前提的来源关联核验，不是 96.5% 的数学答案正确率，也不是全部 PNG 语义独立识别正确率。全部 200 题仍 `practice_ready=false`、难度 null，尚未由本批完成统一目录、AI 难度或正式业务组卷接入。

## 合成反例与代码检查

独立新增 [test_association_acceptance.py](../../backend/tests/test_association_acceptance.py)，使用自建 CFB miniFAT、合成 Equation Native stream、文字、表格与 PNG，不复制真实题目内容。比较器测试的原生签名由真实提取 API 从该合成 CFB 生成，再进入比较 API，未仅手填通过状态。

最终 **35 passed in 0.35s**，Ruff check 与 format check 均通过。正常完整 payload、仅 wrapper 元数据变化、原生相同但预览尺寸不同、相同表格及普通图为正例；以下边界不误确认或明确返回不可用：

- 数字、小数点、加减号、Unicode 上下标及关系符变化；完整 payload 中代表数字、分母、上下标、关系及未知记录的任意字节变化，即使 PNG 相同也不回退放行。
- 非 OLE 图像变化一个像素；公式相同不能豁免同段配图；媒体顺序或公式在文字中的位置改变。
- 同文字但表格行列、MathML 上下标结构不同；缺 OLE 双向绑定、缺预览文件或缺可见预览。
- CFB/原生流缺失、非 CFB、截断、额外尾部、错误 wrapper 长度、无效版本、缺 END 和空 payload。

Opaque 记录只用于验证完整字节参与身份哈希，不声称这些合成记录具有已解析数学 AST。实施前最初 6 项测试失败仅因新模块尚未落盘，不能记作算法缺陷基线。随后独立代码检查促成两个修正：顶层 `image_pixels_match` 保留真实像素含义，另以 `media_identity_match` 表示综合身份；有效 native 不能豁免预览缺失或不可读。相应断言在冻结实现上全部通过。

实际执行：

```sh
.venv/bin/python -m pytest backend/tests/test_association_acceptance.py -q
.venv/bin/ruff check backend/tests/test_association_acceptance.py
.venv/bin/ruff format --check backend/tests/test_association_acceptance.py
```

## 固定真实材料独立重算

`work/association-v3/review_recompute.py` 不导入生产比较器或原生提取器。它重新读取 16 份源 DOCX 的 ZIP、CFB 与 Equation Native stream，独立检查封装和头部、哈希完整 payload，并从原 ZIP 的 PNG 重新计算原宽高与解码 RGBA。随后比较有序文字、图像、表格及数学事件，保留公式与文字的交错位置，最后逐 ID 对齐正式 V3 的状态和固定来源范围。

| 关联材料范围 | 实际结果 |
|---|---:|
| 固定题目 / 文档 / 配对 | 200 / 16 / 8 |
| 原题干与解析题干的 OLE 配对 | 1249 |
| 完整 native payload 身份相同 | 1249 / 1249 |
| 非 OLE 图片配对 | 121 |
| 原宽高与 RGBA 严格相同 / 不同 | 111 / 10 |
| 题目 confirmed / unverified | 193 / 7 |
| 缺失 / 额外 / 重复 / 失败 | 0 / 0 / 0 / 0 |

1249 是关联题干内的对象配对数，不是全文件 OLE 总数。10 个非 OLE 图片差异涉及以下 7 个固定题目 ID，全部保留为未确认；差异不等于已证明数学语义错误：

```text
pair-173056743099-q10
pair-33c8bfb880f0-q15
pair-3aa470532507-q1
pair-3e0329ebee73-q20
pair-61d01048c16d-q21
pair-61d01048c16d-q25
pair-b16f7c3816f0-q2
```

实际执行并退出 0：

```sh
.venv/bin/python work/association-v3/review_recompute.py evals/private/question-bank-v3/bank.json
.venv/bin/python scripts/evaluate_associations.py --bank evals/private/question-bank-v3 --manifest work/association-v3/review-manifest.json --manifest-sha256 652f17654d0da9748bfdc8562af2e73f0252d8c78673c7790c6d316b2b7eb33c --report work/association-v3/review-cli-evaluation.json
```

独立重算 `rate=0.965`、`actual_statuses_match_independent_recomputation=true`。固定评测器独立复跑报告 `confirmed=193`、`unverified=7`、`failed=0`、`integrity_ok=true`、`source_errors=[]`、`passed=true`。评测器同时核对固定快照、源文件、原生签名和导出图片相对原件的像素，不能只靠修改状态通过。

## 来源保真与保护

另执行以下独立审计，退出 0、`error_count=0`：

```sh
.venv/bin/python work/association-v3/review_library_audit.py evals/private/question-bank-v3
```

200 题均结构 imported、答案可用；8 对题号各为 1～25。原件/XML/rels、图片字节、出现顺序、OLE 引用、题目范围、分片不重叠及 SQLite 完整性/外键/JSON 记录一致性均通过。原文件含 6748 张图像 occurrence、6386 个 OLE、12 个表格；全部选中题目块保留 6712 张图与 12 表，6712 张均有源尺寸；浏览 HTML 保留 5342 张图与 7 表，解析题干不重复展示。SQLite 为 16 文档、8 配对、200 题、320 条来源词条证据。

最终重新计算保护摘要：16 个原 DOCX、V1 的全部 4337 个文件、V2 的全部 4337 个文件与批前摘要完全一致，旧库未被覆盖。上述私有结果保存在 `review-recomputation.json`、`review-cli-evaluation.json`、`review-library-audit.json` 与 `review-preservation.json`，不将原件、真实题目或私有报告加入源码。

本报告没有逐题判断答案数学正确性、教学难度或教师价值；最终浏览器交互与视觉检查、打包及全量 `make check` 见主会话的[执行报告](association-v3-execution.md)，不冒写为本报告独立执行结果。
