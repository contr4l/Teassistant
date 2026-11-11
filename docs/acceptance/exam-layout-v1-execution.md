# 试卷专用排版执行记录

日期：2026-11-11。依据用户对作答版、紧凑版和教师答案优化的继续实施授权，范围为FR-15与DEV-004/404；[冻结合同](exam-layout-v1-contract.md)、[运行说明](../exam-layout.md)。本批本地版式集成通过，真实题目与明确合成进度分开记录，不将模板单测作为真实试题版面通过。

## 固定输入与实施

沿用上一批advanced-v3同一个source-practice-snapshot-v1的10题，snapshot_id为 `0034987d7de606fdc9b2b94457beee1fe46d890ed51b0b1b8228a02ba8814300`。题目来自真实长沙九年级材料，学生与已学进度为明确合成演示。本批不重新选题、不调用题目识别/考点评分模型、不修改源库和语义初评，也不覆盖上一版导出。

Root冻结来源V3、最终语义、旧固定快照及旧交付，共4581文件。独立检查另固定原件、旧来源和模型原始评测等历史。Root负责合同、命令及真实集成；A负责3个空白Word模板与样式/作答区helper，B负责来源渲染接线、选项和教师作用域，Sol独立检查反例、原件与实际PDF。实际模型分派沿用已披露的Sol/high设计验收、gpt-6-luna/high实现替代。

版式采用程序生成的Word模板，字体和作答空间规则为本批工程默认，不写成用户已确认的产品参数。空白模板没有真实题目或学生信息，字体文件与生成样张留在Git忽略目录。

## 规则与先验分母

两种学生版式都输出同一题号/题单/内容，作答版为free_response增加横线区，紧凑版不添加。教师版去掉重复题干，只包含答案与已有解析；标签清理只作用于明确段首包装，保留数学行内文字。所有原生公式、预览、图形和表格仍从源块及源DOCX读取。

独立从冻结快照预先清点：学生stem 28块、75次OLE、81次图像（含6普通图）、1表格；教师answer+analysis 123块、112次OLE、115次图像（含3普通图）、0表格。教师作用域变化后不能沿用旧187次OLE的计数。纯包装标签块可归并显示，不能因此丢失任何公式或实际解析文字。

模板helper已完成9项机械测试，另以明确合成内容实际转换A4 PDF，验证页码、正文、表格与作答线；单小问约40mm、三小问约74mm、紧凑版无作答区。该合成烟测不作为本批真实题目和数学保真证据。

CLI增加 `--layout answer_space|compact`；新 `export_practice_layouts.py` 读取原冻结快照，在两个布局都成功后原子发布比较目录，期间快照或学生进度发生变化则拒绝。合成转换缝的第二版失败和进度变化反例均不留下半成品目录；此为事务机械验证，未伪装真实PDF。

## 运行与后续检查

真实比较使用根uv环境、显式字体配置及LibreOffice路径，命令示例：

```bash
FONTCONFIG_FILE="$PWD/work/print-fonts.conf" SAL_FONTPATH="$PWD/work/fonts" TMPDIR=/private/tmp \
  .venv/bin/python scripts/export_practice_layouts.py \
  --snapshot work/bank-practice-v1/advanced-v3/snapshot.json \
  --bank evals/private/question-bank-v3 \
  --semantic evals/private/semantic-v2-unified-final \
  --progress work/bank-practice-v1/synthetic-progress.json \
  --output work/exam-layout-v1/comparison-v4 \
  --soffice /absolute/path/to/soffice
```

复现时使用新输出目录；原snapshot不因模板改变而重写。最终比较批次为 `comparison-v4`，两模式共8个DOCX/PDF工件及比较清单；教师两套为同一内容，面向用户交付一份教师文件即可。实际运行48.94秒，包含来源重验和4次实际PDF转换，并行回归时长不作为生产性能指标。

## 实际迭代与失败证据

- 首轮真实版式暴露跨题keepNext链导致的大空白，修正为按本题关联，末段显式断开。选项覆盖独立A-D段落及同段公式选项，长公式使用最大单图宽度保守降列。
- 段首标签清理曾越过嵌套span、tail或图片，函数参数f(1)等曾被误计为小问；独立红例已修。仅格式化包装标签，数学正文和源payload不改。
- 负w:position及VML vertical-align的尝试在XML层合法，但当前LibreOffice实测完全忽略，首两条公式PDF bbox与原版逐坐标相同。该无效机制及专用API/测试已从默认实现移除，私有受控DOCX/PDF及 `position-experiments/RESULTS.md` 保留。最终对短引导语+单个大型公式使用清晰展示段，不改源对象或尺寸；多对象、图像、普通叙述不强拆。
- 教师版曾有末页仅剩一句结论；保持字号，将ExamAnswer段后距4pt收至2pt后实际收紧到5页，结论留在完整末页。
- 页图审读中曾将v3表后提示句误判为跨页；Root独立按相同分辨率直接渲染actual v3/v4 PDF，首页逐像素相同，pypdf两版完整提示句均在第1页，已纠正该判读。v4新增keepLines和表内连续关联是结构防回归增强，不宣称修复了未发生的像素回归。最终实际打印文件均按原PDF重新检查。

## 最终版式与保真

| 最终工件 | 实际PDF页数 | 原生OLE | 普通图 | 原生表格 |
|---|---:|---:|---:|---:|
| 作答版学生卷 | 4 | 75 | 6 | 1 |
| 紧凑版学生卷 | 3 | 75 | 6 | 1 |
| 教师答案解析（两模式相同内容） | 5 | 112 | 3 | 0 |

均A4。教师答案由上一批含题干的10页收紧至5页，去掉重复题干、标签并优化段距，原答案/解析全部保留。标题16pt，学生正文/编号首行11pt，选项及教师解析正文10.5pt；题号与首段同行，数据表首列加宽，数字对齐，短题图表保持关联，作答版有可写横线区。

Root实际重跑独立 `review_native_exports.py` 对两模式所有Word包的原始OLE、全部预览与普通图逐字节及顺序核对，原生大小比例、关系绑定、唯一ShapeID/ObjectID、题号、数学正文和表格均通过。原生OMML在本批真实10题中的数量为0，其分支只由明确合成机械/转换测试覆盖；不报成真实OMML质量验证。学生1张3×8表与原文本顺序相符，教师无题干与该表。

实际DOCX的canonical渲染与交付PDF的逐页视觉证据由 [独立验收](exam-layout-v1-review.md) 记录：4份Word均canonical渲染成功，实际PDF共17物理页；两份教师PDF的5页逐像素相同，独立亲眼检查12张不同页图，涵盖全部17页。A4、页码与总页数、1～10题号、27段作答区域及manifest摘要均通过。最终以交付PDF为打印依据；不能从XML合规或单测通过推断老师认为排版满意。

## 最终检查与交付边界

配置 `TEASSISTANT_SOFFICE` 为实际LibreOffice路径及上述字体环境后运行 `make check`：Ruff通过，**399 passed，0 skipped**，含实际Pandoc/LibreOffice的既有P0转换集成。独立反例为23项，另实际运行与复核原件及最终PDF。模型HTTP测试为合成响应，本批不新调用题目识别/评分模型。Root冻结的4581文件全部与原SHA一致；独立旧库/16原件及旧交付的14021文件保护也全部通过，两清单范围有重叠，不相加作为新的分母。

新交付包含作答版、紧凑版和教师答案共3份Word及对应PDF、说明与摘要清单；字体、原始试卷、学生信息、QA页图和临时LibreOffice目录不打包。Word编辑体验及跨Word/字体环境的分页一致性没有用GUI实测，原生嵌入保留不等于所有电脑都具备MathType编辑能力。

本批通过的是本地来源导出、版式工程与内容保真验证。题目是既有真实资料，学生进度为合成演示；未新增网页设置界面、生产部署或教师实际满意度证据，不宣称完整MVP或老师价值验证通过。
