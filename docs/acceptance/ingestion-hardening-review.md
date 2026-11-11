# DOCX 录入正确性独立验收

本批依据 [修复合同](ingestion-hardening-contract.md) 检查来源层录入正确性；独立设计与验收模型为 `gpt-6.1-sol` / `high`，实施模型为实际可用的 `gpt-6-luna` / `high`。不执行模型调用、不新增人工题库审核，不把来源结构 imported 当成业务 ready。

## 验收范围与初始证据

新增独立纯合成测试 [test_ingest_acceptance.py](../../backend/tests/test_ingest_acceptance.py)，覆盖正常关联、不同题干、原解析独立题号清点、共同缺号、章节题数、解析可选、空答案、同段标记与媒体位置、重复输入及跨用途身份、后缀识别、零题失败、坏文件局部失败、OMML 成功及未知失败、公式像素比较与解析内编号。

实施中首次实际执行 `.venv/bin/python -m pytest backend/tests/test_ingest_acceptance.py -q`，结果 **17 failed、1 passed**。这是并行实施中间态的红灯记录，不能解释为未修改旧实现的完整基线；后续以冻结实现上的独立复跑为准。

对既有 V1 私有题库独立比较：200/200 题的规范化题干文字相同；按有序图像的原宽高与解码 RGBA 内容精确比较，47/200 一致、153/200 不一致。后者仅说明精确关联证据不足，不证明原卷与解析存在语义错配。正常一致题必须能 confirmed；其余须明确 unverified、给出理由并阻止整批完整成功，不能通过把所有题一律失败或未确认来规避正确性要求。

原件与旧库的修改前摘要保存在私有 `work/ingestion-hardening-review/baseline-hashes.json`，包括 16 个输入 DOCX 和旧 V1 库 4337 个文件；像素比较计数位于 `work/ingestion-hardening-review/rgba-baseline.json`。这些私有证据不包含在源码交付中。

## 最终结论

2026-11-11 冻结实现通过本批来源层修复的独立验收。正式产物为私有 `evals/private/question-bank-v2`，其批次状态仍是 **partial**：200 题结构可用、200 题答案可用，其中 47 题具备精确关联证据、153 题未确认。没有将全部题目判失败来绕过问题，也没有将未确认关联静默当成完整成功。该结论不等于 200 题数学语义全部正确，不代表题库满足完整 PRD 或具备自动组卷资格。

### 独立反例与命令

最终持久合成验收共 **23 个用例**，保留正常题、正常内部编号、同像素不同 PNG 元数据等正例。除初始范围外，审查中实际复现并推动修复了以下边界：无声明时伪造声明分母；小数点被规范化删除后误确认；OMML 拼接文字相同但分式结构不同后误确认；同段数学答案的 occurrence 丢失；中间题缺答案时污染前一题且仍显示结构成功。未将这些合成问题指称为旧真实 200 题已经发生的错误。

实际执行：

```sh
.venv/bin/python -m pytest backend/tests/test_ingest_acceptance.py backend/tests/test_docx_ingest.py backend/tests/test_source_math.py -q
.venv/bin/ruff check backend/tests/test_ingest_acceptance.py
PYTHONPATH=backend .venv/bin/python work/ingestion-hardening-review/math_probes.py
```

结果分别为 **35 passed in 0.80s**（独立 23 + 原导入 3 + 数学 9）、Ruff 通过、独立数学探针 **6 passed**。后者另检查正常分式、重复分子、未验证粗体与 n-ary 布局、恶意定界符及未知属性：不支持的结构明确失败、保留原 XML，输出不注入 HTML。主会话另外执行全量 `make check`，报告 Ruff 通过、166 passed / 1 skipped；该全量结果由主会话取得，未冒写为本报告独立重跑。

### 真实材料审计与分母

独立执行：

```sh
.venv/bin/python work/ingestion-hardening-review/audit_library_v2.py evals/private/question-bank-v2
.venv/bin/python scripts/build_question_bank.py --source assets --output work/ingestion-hardening-review/independent-bank-v2
```

来源审计 **error_count=0**；独立重建退出码 **3**，与 `partial` 合同一致。统计如下：

| 项目 | 实际结果 |
|---|---:|
| 输入 / 唯一文档 / 配对 | 16 / 16 / 8 |
| 重复输入 / 文件失败 | 0 / 0 |
| 题目 | 200，8 对各 1～25，无重复或缺号 |
| 结构 imported / processing_failed | 200 / 0 |
| 答案可用 | 200 / 200 |
| 关联 confirmed / unverified | 47 / 153 |
| 原 XML 图像 occurrence / OLE occurrence | 6748 / 6386 |
| 全部选中题目块图像 / 表格 | 6712 / 12 |
| 浏览 HTML 图像 / 表格 | 5342 / 7 |
| 真实 OMML source / rendered | 0 / 0 |
| SQLite 文档 / 配对 / 题目 | 16 / 8 / 200 |
| 有解析原文考点标签的题目 | 164 / 200 |
| 来源考点证据行 / 不同标签 | 320 / 48 |
| practice_ready / 已评分难度 / 模型调用 | 0 / 0 / 0 |

审计直接读取源 ZIP 的 XML、relationships 与 PNG，核对原 DOCX 副本和 XML/rels 字节；按题目范围及段内片段重新合并后，比较媒体与 OLE 的出现顺序，检查片段范围不重叠、非空段落覆盖、图片字节和原始显示尺寸、12 个表格及浏览 HTML 顺序。6712 张选中块图片均保留源尺寸；浏览器不重复展示解析中的题干，因此浏览计数较低。36 张不在题目范围内的源图仍保存在原 DOCX 中。SQLite 使用只读连接检查完整性、外键及 200 条记录与 JSON 完全一致。

原件保护与稳定性独立计算 SHA-256：16 个输入 DOCX 与修改前摘要相同；旧 V1 的 4337 个文件全部相同；新正式 V2 与独立重建各 4337 个文件，路径集合和每个文件摘要全部相同。此后主会话只调整来源浏览器的未确认提示文案并重渲染 `index.html`，未改题库 JSON、来源或 SQLite；独立重建目录也使用最终渲染器重渲染，已再次核对两份最终 HTML 字节完全一致。计数证据分别在私有 `library-audit.json`、`preservation-and-stability.json`，与前述探针共同位于 `work/ingestion-hardening-review/`；不将原题、原件或私有产物加入源码。

## 仍未验证或未交付

153 题的图像精确证据不一致仍需要后续自动语义关联验证；本批没有真实模型调用，也不增加人工题库审核。现有真实资料全为 MathType OLE 加 PNG 预览，不能用其证明真实 OMML 转换质量；OMML 仅由合成结构与浏览实现验证，支持范围保守，未知或未验证样式明确失败。

来源结构、答案可用和关联确认是不同状态。即使 47 题已精确确认，全部 200 题仍未进行难度评估、统一考点映射及正式业务题目转换，均为 `practice_ready=false`、难度 null；业务候选检索、进度约束和组卷闭环尚未由这批来源记录接通。来源索引保留解析原文标签证据，不把这些标签冒充官方目录。本报告未逐题进行数学语义或教学价值判断；最终浏览器视觉与交互检查由主会话单独记录，不计作此处 200 题逐题视觉验收。
