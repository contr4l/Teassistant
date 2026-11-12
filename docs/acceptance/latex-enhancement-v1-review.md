# MathType 转换增强独立验收

本批固定 7 对象恢复与同题单 LaTeX 导出工程验收通过，无未关闭阻塞；全库仍有明确不支持项，不等于任意 MathType 支持。依据 `latex-enhancement-v1-contract.md`，对应 FR-15、DEV-004/404。独立验收为实际 `gpt-6.1-sol/high`，并行实施为实际 `gpt-6-luna/high`。未调用模型、未用 OCR 猜公式，未修改旧题库或旧交付。

## 固定分母与原件核对

私有 `frozen-inputs.json` 先冻结旧转换器文件 SHA、失败清单、来源库与语义侧附。独立脚本 `work/latex-enhancement-v1/review_inventory.py` 实际验证这些摘要，从原始 DOCX ZIP 提取每个 OLE，重算完整对象 SHA、Equation Native payload SHA，并按同一块的 ole_id 绑定预览，而不把该段其他公式图片混作本对象。

先验固定为 10 题、53 个不同完整 OLE 对象，其中失败 **7 个不同对象、8 次出现、涉及 6 题**。同一对象的两次出现有不同 PNG 编码，均保留并查看；不把 8 次出现说成 8 个对象。独立实际看齐 8 张源预览，三处为科学记数法范围中的绝对值，其他包含分数负指数、根式、π、运算与绝对值作用域。真实原件与图像只存私有目录。

候选对这 7 对象实际转换均成功，独立逐一将 MathML/TeX 与绑定原预览对照：不等号、正负号、小数点、分子分母、指数及根式/绝对值范围一致。结果为 `review-seven-converted.json`，不能以此推出任意 MathType 对象均可转换。

## 规则与独立反例

变更限于 paired tmBAR 的已知末尾围栏槽和显式 MTCode π 补表；转换器身份从 strict-v1 升为 strict-v2。围栏必须绑定正确 template/variation、非空 LINE、已知控制记录顺序及合法 COLOR/FULL；尾部 EC07/EC08 必须是指定 typeface 的无修饰普通字符，不按样本 SHA 匹配，不在正文中统一删除 PUA。

独立新增 `backend/tests/test_latex_enhancement_acceptance.py`。起初旧实现对 5 个合法结构正例失败，8 个负例拒绝正确；初版增强后又实际发现两个数学丢失反例：左右围栏若带 one-dot EMBELL，原清理会删掉修饰并错误成功。实施者补齐 marker 属性检查后复验通过。

实际命令 `.venv/bin/pytest backend/tests/test_latex_enhancement_acceptance.py backend/tests/test_math_latex.py backend/tests/test_latex_acceptance.py -q`：**71 passed，1.29 秒**，其中本批独立 23 项。自身文件 Ruff 与格式检查通过。覆盖 COLOR 前置/中置、FULL、正文负数/小数/关系符号；交换、缺失、相近 PUA、错误 typeface、额外数学字符、未知 record/variation、不合法 SIZE/COLOR；无 MTCode 仅 font position=112 不能猜 π，π 修饰须保留或明确失败，左右围栏修饰不得吞掉。全部为纯合成完整 CFB，不以合成通过率充当真实转换正确率。

字符身份依据区分如下：Wiris 的 [Font knowledge](https://docs.wiris.com/reference/font-knowledge) 说明 MTCode 是字符身份，而 font position 是字体槽位；[Unicode 官方数据](https://www.unicode.org/Public/UCD/latest/ucd/UnicodeData.txt) 可核对 U+03C0 为 Greek small letter pi。因此 π 的精确补表不需要按字体位置猜测。独立读取 [1999 年厂商 MTEF5 文档历史镜像](https://rtf2latex2e.sourceforge.net/MTEF5.html)，核对 Typeface values 的 22=fnEXPAND、tmBAR 与左右围栏 variation 位，以及 ParBoxClass 的主槽/左右围栏字符顺序。该页明确说明其历史镜像身份；当前厂商 SDK 路径重定向登录页。没有找到可直接核准 EC07/EC08 命名的公开官方码点表，故本批是“已知模板槽位、严格字节/属性约束及绑定源预览确认”的局部归一化，不宣称查证了通用 PUA 映射。

## 实际导出与保护

Root 从同一固定题单实际编译 `replayed-pdf-v1/{answer_space,compact}/{student,teacher}.pdf`，独立以 PDFium 渲染并查看全部 **9 页**：作答学生 2、紧凑学生 1、教师各 3 页。实际 MathML/TeX 目录为 53 项，导出公式出现为学生 24、教师 34；本题单没有普通几何图或表格，不能把零计数冒充新的几何图/表格验收。

另用 bundled Python/pdfplumber 独立核对四 PDF 的嵌入图像出现均为 **0**，字体包含 Latin Modern Roman、Latin Modern Math 与 Fandol，未把 58 次公式出现替换为旧 PNG。中文、公式、答案与解析可读；首轮作答版第 7 题题干在第一页底而全部作答线在第二页顶，该分页反例已交 Root 修复，最终复验见下段。紧凑版与教师版未见新的内容丢失。

最终产物以 `work/latex-enhancement-v1/replayed-pdf-v2` 为准，模板身份为 latex-exam-v2。独立重新渲染并查看作答版全部 2 页：第 7 题及其 7 条作答线已一起位于第 2 页，1–10 题顺序完整。紧凑学生和两份教师 TeX 相对 v1 逐字相同，独立比较其全部 7 页 PDFium 1.5 倍 RGB 像素也完全相同，因此沿用已逐页完成的视觉检查。四个最终 PDF 仍为 2/1/3/3 页，共 9 页，图像出现数均为 0；最终 manifest 保持同一快照、53 目录项、学生 24 / 教师 34 次公式及合成进度标记。证据 `review-pdf-v2/audit.json`、`font-image-audit.json`。

独立运行私有 `review_protection.py`：**14,173 个受保护文件，0 改变、0 缺失**。范围包含原件、V1/V2/V3 来源库、原语义目录及原始模型响应、既有 Word/LaTeX 交付、原考试分析运行与上一批 Web 下载文件。清单及本批诊断报告留私有目录；未提交真实卷面/公式对象。

## 全库分母、回归与最终检查

独立脚本 `review_bank_denominators.py` 不调用扫描器的转换/绑定 helper，直接从 16 原 DOCX ZIP 重算完整 OLE SHA，并对全部出现位置的题目/字段/document/part/SHA 多重集逐项核对：200 题、2,652 个不同完整 OLE、6,386 次诊断出现，全部绑定相等；其中 analysis_stem_blocks 的 1,249 次是额外诊断字段，导出实际使用的三字段共 5,137 次。原 24 个 blocked 题未丢出分母；187 题有公式，13 题无公式。

| 口径 | 冻结旧版 | strict-v2 |
| --- | ---: | ---: |
| 不同完整 OLE 对象分母 | 2,652 | 2,652 |
| 转换成功对象 | 2,233 | 2,288 |
| 剩余不支持对象 | 419 | 364 |
| 诊断范围成功出现 / 6,386 | 5,732 | 5,816 |
| 诊断范围失败出现 / 6,386 | 654 | 570 |
| 导出范围成功出现 / 5,137 | 4,580 | 4,644 |
| 导出范围失败出现 / 5,137 | 557 | 493 |
| 导出范围有失败的题 / 200 | 102 | 88 |
| 176 个 eligible 题中有公式失败的题 | 85 | 71 |

实际独立运行 `.venv/bin/python scripts/diagnose_latex_bank.py --bank evals/private/question-bank-v3 --semantic evals/private/semantic-v2-unified-final --output work/latex-enhancement-v1/review-bank-scan-final`，退出 0，45.88 秒。自身新输出四个 JSON 与 Root 冻结的 `candidate-bank-scan-final` 逐字相同，记录的实际转换器源码 SHA 为 `7ce7f4b0d5823fafe4fd3594d57fb9b1925ae2e84cafb7414732d45e80fab9fa`，与当前文件重算相等。先前候选报告源码摘要为空的问题已修复，并保留旧报告。独立比对新旧对象集合：55 个对象新增成功、84 次诊断出现恢复，旧成功对象没有变成失败；失败类别变化不直接视为支持退步。

Root 的 `catalog-audit.json` 及对应脚本另记录固定题单原 46 个成功对象、上一版成功样卷 100 个对象/187 次出现重建目录后，MathML 和 LaTeX 逐字不变。该项是公式目录回归，不冒称本批重新编译了旧样卷 PDF。旧原件和旧交付已由上述保护清单独立核对。

独立读取 Root `make-check-final.log`：Ruff 通过、**606 passed，37.56 秒**，1 条既有 Starlette/httpx 弃用警告；不冒充独立者重跑全套。扫描器最后修订后，独立另运行 `.venv/bin/pytest backend/tests/test_diagnose_latex_bank.py -q`：**3 passed，0.20 秒**。本批新 23 个关键反例与旧数学测试的 71 项是独立实际执行结果，未改动不相关前端。

## 结论边界

7/7 原失败对象、8/8 原失败出现已恢复，固定 10 题可实际生成两版题答 LaTeX PDF；未知和近似结构保持明确拒绝。全库 364 对象、493 次导出出现仍不支持，尚非全库公式语义正确性通过。本批不证明原解析答案正确、教师满意或任意 Word 文档支持；不改变题库 ready、模型供应商或人工审核流程。
