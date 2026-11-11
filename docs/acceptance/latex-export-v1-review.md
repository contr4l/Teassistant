# LaTeX 重排独立验收

依据[本批合同](latex-export-v1-contract.md)，验证固定 10 题由原生 MathType/OMML 转换为真正 LaTeX，再以统一数学字体排版；不将原公式 PNG 嵌入后称为公式转换。独立设计验收 `gpt-6.1-sol/high`，实施实际 `gpt-6-luna/high`。无模型调用、重新选题、重新评级或题库人工审核；合成学生进度与教师实际价值分开。

## 固定输入及分母

私有 `work/latex-export-v1/review-cohort.json` 在读取转换输出之前，独立从原 DOCX ZIP 固定 187 次 OLE 出现：学生 stem 75、教师 answer+analysis 112，100 个不同的完整 OLE bytes SHA。分母使用出现次数，不能拿 100 个缓存键冒充 187 次覆盖。普通几何图 6/3，学生 1 张 3×8 表，教师无 stem。快照沿用 advanced-v3，snapshot_id `0034987d7de606fdc9b2b94457beee1fe46d890ed51b0b1b8228a02ba8814300`；源快照字节 SHA `bba1fd624e73920ec978c387186d48955c2836a8c4993bc1391f38fdd6b1c3a4`。

保护清单继承全部原件、来源库、语义 raw/最终结果、输入、目录及旧 Word/PDF 交付，另冻结上一批 exam-layout comparison-v4，不覆盖旧文件。私有先验清单中的历史字段 `bank_sha256` 指原始 bank.json SHA；正式 FormulaCatalog 的同名字段按共享合同指完整 PracticeBank canonical hash，二者分别校验，不混用。

## 独立可靠性与安全检查

已读第三方 `mathtypejx 0.1.0` 的实际解析/生成代码，并运行纯合成损失探针：在 LINE 中加入未知 record 50，库输出与无未知记录时相同；追加第二表达式后仍返回首式，剩余 6 字节未消费。这是第三方库的可复现实测，不等于本批尚未完成的转换实现已发生损失。严格未知 record/variant 及消费完整性检查必须发生在被库吞掉信息之前，不能仅对白名单 AST 或“未抛异常”作验收。

字形还须按编码解释，font position 不是可直接当 Unicode 的数学字符身份；官方说明将字体位置与 MTCode 分列映射：[Wiris Font knowledge](https://docs.wiris.com/reference/font-knowledge)。Symbol、角度、几何符号和未知 PUA 必须有明确映射或失败，不得猜字或置空。

首批独立安全合同用例 13 项先运行出 5 个拒绝缺口：TeX `^^` 预处理形式、补充平面 PUA、全空白数学。共享校验器已修，独立最终复验全部通过。未实际执行恶意 TeX；这类用例只证明校验拒绝，不能证明真实数学转换正确。

## 真实公式逐项核对

固定分母仍为 187 次出现，100 个不同完整 OLE 对象。独立先按原 PNG 的 RGBA 与尺寸分组，得到 107 个不同预览变体（同一原生对象的不同预览也未省略），制作实际 XeLaTeX 左右对照页，逐行看完 C001–C107 全部 14 页。核对数字、小数点与尾零、正负号、分式分母、根号覆盖范围、幂与负指数、绝对值、角度、垂直/平行、三角形/圆及因为/所以符号；未发现语义差异。这证明本批转换保留来源公式，不是证明原解析答案正确，也不是教师 gold 校准。

目录 v3 到 v4 仅 5 个公式键的 TeX 围栏伸缩变化，全部 100 个 MathML 逐字未变；再次实际编译并对照 6 个受影响预览行，括号、绝对值和根号/指数范围保持。逐行映射及目录/PDF SHA 保存在私有 `review-formula-semantics-v3.json`、`review-formula-semantics-v4-delta.json`；其覆盖索引严格等于 0..186，无漏项或重复。

严格转换器保留第三方 MIT 版本来源，拒绝未知记录、未消费表达式、未知分式变体、未映射 PUA。针对 EC07/EC08 的处理仅限已识别 tmBAR 槽位组合，不把任意私有字符统一丢掉。独立正例同时验证真实可转换结构，避免负例因无关解析错误而假通过；未知变体用例要求相应错误码。

## 独立实跑及当前产物检查

- `.venv/bin/pytest -q backend/tests/test_latex_acceptance.py backend/tests/test_latex_comparison_cli.py`：28 passed（独立 26 项、Root 的两布局 CLI 原子性 2 项）。包含 TeX 预处理与命令拒绝、文本转义、原生未知结构、分数/根式范围、重签目录但篡改数学、原 DOCX/图资产变化、第二份 PDF 失败清理，以及进度变化/第二布局失败不发布。合成产物明确标记，不用于真实公式质量分母。
- `.venv/bin/ruff check backend/tests/test_latex_acceptance.py`：通过；独立文件已格式化。
- `.venv/bin/python work/latex-export-v1/review_delivery_audit.py work/latex-export-v1/comparison-v6`：从原 DOCX ZIP 重读 187 次完整 OLE，并独立用 olefile 取 Equation Native 包装后的完整 payload 重算 SHA；与固定清单、目录及两份 manifest 的逐次来源顺序一致。每种布局学生 75、教师 112 公式，学生 28、教师 123 来源块，普通图 6/3 均与原 ZIP bytes 相同，资产中没有公式 PNG。学生表格 3×8，21 个数值含 0.750/0.780 等尾零逐值一致并使用普通 math 字体，教师不包含 stem。
- `.venv/bin/python work/latex-export-v1/review_protection_check.py`：14,033 个受保护文件无缺失、变化或增加，含 16 原件、V1/V2/V3 来源库、真实语义 raw/最终结果、目录及两批旧交付。

## 最终实际 PDF 与结论

最终目录为私有 `work/latex-export-v1/comparison-v6/{answer_space,compact}`，同一 10 题、snapshot_id 及来源文件哈希保持不变。正式目录 FormulaCatalog SHA 为 `5a10791d879e1c5bb7b07ff08b05ef8729ebfffbc113c0c3f2f94b4b4fb95d28`，与已完成逐公式语义 QA 的 v4 目录逐字相等。187/187 次公式转换成功，unsupported/unknown/失败均 0；100 只是缓存键数量。

独立使用 `/Library/TeX/texbin/xelatex` 实际编译过 QA 对照页，版本 XeTeX 0.999998 / TeX Live 2026；最终交付的真实编译由 Root CLI 执行。独立随后用 bundled Python 的 PDFium 重渲染全部 16 页（作答学生 3、紧凑学生 3、教师各 5）。两份教师页逐像素相同且与独立逐页读过的 v5 教师页相同；作答学生 3 页与独立看完的 v6c 实际编译预览逐像素相同；紧凑学生 3 页再次逐页检查，共覆盖 11 个不同页画面。检查了题号 1..10、完整表格/提示、图与选项顺序、中文与数学可读性、证明和末尾结论；没有缺字、重叠或裁切。没有将未见的多页拼图边界当作跨页事实。

中间态 v4 的表格引入分离、v5 的第 4/8/10 题题干与图或选项分离，以及未绘出的作答线均已在最终 PDF 实际消除。最终第 3 题完整引入+3×8 表+提示同第 1 页，第 4/8 题整组在第 2 页，第 10 题图与题干同页；教师第一题分号随显示公式，长解析完整保留。

独立实际 PDF 指标命令：

```sh
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 work/latex-export-v1/inspect_pdfs.py work/latex-export-v1/comparison-v6 work/latex-export-v1/review-v6-pdfium
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 work/latex-export-v1/review_pdf_metrics.py work/latex-export-v1/comparison-v6 work/latex-export-v1/review-v6-pdfium
```

结果：每种布局 PDF 仅有学生 6、教师 3 次普通图出现；数学为文字/数学字体排版，没有嵌入 187 张公式图。表格 21 个数值在实际 PDF 中均为 `LatinModernMath-Regular` 12pt，无粗细差异；两学生版均 A4、页码连续、字形位于打印区域。作答 PDF 实际可见 27 条长横线（7+7+13），加表格 4 条共 31；紧凑版只有表格 4 条，作答线 0。这里计数的是 PDF 中有非零线宽的真实绘制线条（本引擎编码为 stroked line），不是只数 TeX 指令，也不是把 0 宽度矩形当可见线条。私有 `review-pdf-metrics.json`、`review-pixel-comparisons.json` 及 `review-delivery-audit.json` 保留复核结果。

最终独立 26 项加 CLI 原子性 2 项再次实跑 **28 passed**，Ruff 及格式检查通过。另读取 Root 最终全量 `make-check-v6.txt`：Ruff 通过、**447 passed / 0 skipped**（这项是 Root 执行的全量结果，未冒称独立重新跑过）。最终再次运行保护清单检查，14,033 个旧文件仍完全一致。

**结论：固定 10 题 LaTeX 转换与版式工程验收通过，无剩余阻塞。** 数学转换支持范围按本批原生对象与独立合成正反例界定，不能外推全部 200 题或任意 MathType/OMML；真实材料 OMML 为 0，其根式支持只经过合成反例。未重新评级、未改答案语义、未提升题目生产 ready。原解析本身的数学正确性、教师满意度与实际打印机效果未在本批证明，完整 MVP 和教师价值仍须各自验收。
