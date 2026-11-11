# 专用试卷排版独立验收

依据[排版合同](exam-layout-v1-contract.md)，本批只验证同一固定 10 题快照的两种学生布局与仅答案解析的教师版。独立设计及验收 `gpt-6.1-sol/high`，实施实际 `gpt-6-luna/high`。不重新选题或调用模型，不改原始题目与旧交付，不引入人工题库审核；合成学生进度与真实教师使用分开。

## 固定输入与证据范围

固定快照沿用 `work/bank-practice-v1/advanced-v3/snapshot.json`，原字节 SHA 为 `bba1fd624e73920ec978c387186d48955c2836a8c4993bc1391f38fdd6b1c3a4`，规范 JSON snapshot_id 为 `0034987d7de606fdc9b2b94457beee1fe46d890ed51b0b1b8228a02ba8814300`。10 个 ID 在私有 `work/exam-layout-v1/review-cohort.json` 先固定；学生两模式必须同题、同顺序、同数学内容。教师按 `answer_blocks + analysis_blocks` 重新计算来源对象分母，不沿用旧教师含题干的计数。

私有 `review-protection.json` 冻结 16 原件、来源 V1/V2/V3、语义最终及两轮 raw、输入和目录，并新增冻结旧 default-v3/advanced-v3 两套完整交付目录。最终复核应逐文件 SHA 对照，而非仅看文件数。

## 独立验收门禁

持久合成反例检查 A4、字体与标题、页码字段、非法布局、计算/证明留白与 compact 行为；另检查教师内容范围、段首标签清理、小数与数学正文不可误删、选项顺序、公式选项、长选项和无选项、纯图首段。真实对象从源 DOCX ZIP 重读 OLE、全部预览和普通图，核对数量、完整字节、顺序与关系；表格保留原始 3×8 结构和文本。模板不含原题或答案，manifest 必须保留同一 snapshot_id 并记录 layout/template_version/teacher_content。

实际 PDF 必须逐页渲染与亲眼检查：题号同行、图与首段关联、留白可写、选项完整、长解析自然分页、页码和打印边界。XML 结构不能代替 PDF 可读性，工程版式通过不当作教师满意度或教学价值。

## 独立反例与纠正记录

独立持久用例已落 `test_exam_layout_acceptance.py`。本批确实先运行红例：段中文本后的标签、嵌套 span 的 tail、图后标签均被当成段首而误删；函数参数 `f(1)/f(2)/f(3)` 被当成三个小问；短题末段 `keep_with_next=True` 会与下一题构成跨题链。另补独立展示的正例及叙述、多公式顺序替换、已有 drawing/OMML 不拆的负例，逐项检查对象 XML、尺寸和关系 ID 不变。最后补短段 `keepLines`、3×8 表内各段跟随、末段不跟下一题的结构检查。最终独立运行 **23 passed（0.98s）**，自身新测试与获准补充的旧合成 fixture 均通过 Ruff。

公共导出用例明确 mock PDF 转换，只证明 DOCX 内容与 manifest，不能代替真实转换或版式证据。三份模板 ZIP 内无原卷媒体、嵌入对象或布局表格，正文仅空白标题/姓名日期/版式；生成时才加合成进度标记。当前合同明确学生正文 11pt，选项及教师答案 10.5pt，教师答案段后 2pt；这些是工程默认值。

在真实输出生成前已从固定来源冻结新 scope：学生 stem 共 28 块、75 OLE、81 次图像（普通图 6）、1 表；教师 answer+analysis 共 123 块、112 OLE、115 次图像（普通图 3）、0 表。只含来源标签的空段可以去除，其余数学正文与源对象必须全部保留。中间 `comparison-v1/compact` 的源 ZIP 字节/顺序/绑定、公式原 VML 比例、表格及逐段数学文字核验已通过，但不据此宣布最终排版通过。

审阅时，我曾把 `comparison-v3` 多页图的边界误读为表后提示句跨页，并报告为实际阻塞。复核已证伪该判断：独立用保存的实际 PNG 做差分，v3/v4 第 1 页逐像素相同；用 PDF 文本分页再核对，两版完整提示句都在第 1 页，第 2 页没有孤立末字。证据为私有 `review-caption-correction.json`。新增 `keepLines` 与表内跟随约束是预防性结构增强，不能声称修复了未发生的 v3 像素回归。

## 最终实际验收

最终目录为私有 `work/exam-layout-v1/comparison-v4/{answer_space,compact}`。两个 manifest 的 snapshot_id 均为上述固定值，输入 snapshot 文件与旧原文件逐字节相同，题号 1～10 顺序完整；layout、`template_version=exam-print-v1`、`teacher_content=answers_and_solutions` 与实际模式一致，四文件摘要正确且路径为本目录相对文件名。

独立 `review_native_exports.py` 将快照 payload 与 V3 原始 bank 逐 ID 对照，再从原 DOCX ZIP 重读每次 OLE、预览和普通图；不是仅相信 exporter manifest。两模式均通过完整字节、出现顺序、ShapeID/ObjectID 唯一性、预览绑定、原生 OLE 原宽高、原 VML/冻结普通图显示比例、全部学生/教师数学正文顺序及精确内容范围检查。正文核对仅忽略排版空白及明确编号/来源标签层，不做数值、符号或公式语义归一化。

| 模式与范围 | 原生 OLE | 普通图 | 含预览的图像出现次数 | 表格 | 实际 PDF 页数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| answer_space 学生 stem | 75 | 6 | 81 | 1 | 4 |
| compact 学生 stem | 75 | 6 | 81 | 1 | 3 |
| answer_space 教师 answer+analysis | 112 | 3 | 115 | 0 | 5 |
| compact 教师 answer+analysis | 112 | 3 | 115 | 0 | 5 |

学生的 3×8 表格行列、每格文本与公式预览均保留；教师没有导入 stem 块，保留现有答案与解析，不限制解析中合理重述题设。27 个作答区段只出现在作答版学生卷，compact 学生与教师均为 0。真实材料 OMML 为 0，不把合成 OMML 检查冒充真实 OMML 渲染效果。

对最终四份 DOCX 使用 bundled Python 与 documents 技能 `26.1110.11258/skills/documents/render_docx.py` 做 canonical 渲染，全部成功；再直接渲染实际交付 PDF。共 17 个物理页面全部生成 PNG，目视检查 12 张不同页图；两套教师的 5 页逐页像素完全相同，另用像素摘要核验一致性。所有实际页为 A4，页码及总页数与物理页一致。表格及完整后续提示句在同页，短题题号/题干/图/选项关联，无缺图、裁剪、遮挡或错误插入的大片空白；长证明自然分页。大型计算式独立展示清楚，题号与“计算”首段同行；教师短答案同行，最终结论已随完整解析留在第 5 页，无单行结论额外尾页。

最终保护复核通过：16 原件、来源 V1/V2/V3、语义最终及 raw、输入、目录、上一批两套交付，合计 14,021 个受保护文件全部与先前 SHA 清单一致。

实际执行命令：

```sh
.venv/bin/pytest -q backend/tests/test_exam_layout_acceptance.py
.venv/bin/ruff check backend/tests/test_exam_layout_acceptance.py backend/tests/test_bank_practice_acceptance.py
.venv/bin/python work/exam-layout-v1/review_native_exports.py work/exam-layout-v1/comparison-v4/answer_space work/bank-practice-v1/advanced-v3/snapshot.json
.venv/bin/python work/exam-layout-v1/review_native_exports.py work/exam-layout-v1/comparison-v4/compact work/bank-practice-v1/advanced-v3/snapshot.json
.venv/bin/python work/exam-layout-v1/review_protection_check.py
```

PDF 直接渲染与页码/文件摘要/题号核验由 bundled Python 执行私有 `review_render_pdf.py`、`review_final_checks.py`，证据为 `review-pdf-v4-*`、`review-native-v4-*`、`review-final-checks.json`。运行时为 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`；canonical renderer 的 PATH 优先 bundled override/fallback，使用 bundled soffice，不调用桌面 LibreOffice。集成负责人另记录带真实转换器的 `make check` 399 passed 与 Ruff 通过，本人独立运行的是上述 23 项，不冒称重跑了全量。

## 结论与限制

最终 v4 通过本批固定 10 题的版式工程验收，无剩余阻塞。未进行新的模型调用、重新评分或人工题库审核，不改变正式 ready。结论不代表 200 题全部排版复验、教师满意度、难度校准或完整 MVP 通过。原生对象和可打印 PDF 已核验，但 Word GUI 双击编辑、跨 Word/字体环境分页及物理打印未测。普通变量混排不强制拆分；本批只对可靠短引导的大型 MathType 做独立展示，不声称已解决通用公式基线问题。
