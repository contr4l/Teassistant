# LaTeX 试卷导出

来源 Word 保留原生对象供追溯；打印 PDF 新增独立路径：MathType 原生 MTEF / OMML → MathML → LaTeX → XeLaTeX。公式以数学字体排版，普通几何图保留原图及比例。原 Word 导出保持可用，不依赖 PDF 反向转换得到可编辑 Word。

## 运行

先用根目录 uv 环境安装锁定的 export 依赖（make setup），另外安装 XeLaTeX 和 ctex、Fandol、unicode-math、Latin Modern Math、geometry、graphicx、tabularx、array、booktabs、enumitem、fancyhdr。TeX 引擎属于系统依赖，不由 Python 锁文件安装。部署环境也须执行实际编译与逐页验收。

模板按 TeX 字体文件名定位 lmroman10 的常规/粗体/斜体及 latinmodern-math.otf，不依赖操作系统登记同名字体。来源正文中的已支持几何符号也进入数学字体排版，其他缺字由实际编译门禁拒绝。

    .venv/bin/python scripts/export_latex_practice.py \
      --snapshot work/bank-practice-v1/advanced-v3/snapshot.json \
      --bank evals/private/question-bank-v3 \
      --semantic evals/private/semantic-v2-unified-final \
      --progress work/bank-practice-v1/synthetic-progress.json \
      --output work/my-latex-comparison \
      --xelatex /Library/TeX/texbin/xelatex

输出目录必须不存在，且与全部输入目录分离。两种版式沿用相同冻结题单，包含学生 student.tex/pdf、教师 teacher.tex/pdf、普通图形 assets/ 和来源清单；两种版式都成功后才发布比较目录。作答版加入解答题作答区，紧凑版便于老师浏览；教师版均只含答案和已有解析。

## 转换边界

转换器依赖 mathtypejx 的 MIT 实现，精确 Git 提交锁在 pyproject.toml 和 uv.lock。项目自己的严格读取层先检查支持的原生记录、完整消费及符号；不会将第三方库“成功返回”直接视为完整转换。LaTeX 数学片段检查命令与字符，文本单独转义，不启用 shell escape。源文件、公式目录、图形、进度与选题快照在导出前重验；缺公式、未知符号、来源变化、缺字体、溢出版心或编译失败均明确拒绝，不能转为原公式图片后继续声称本路径成功。

字体、字号和页边距为工程排版默认；几何图中的文字仍是原图片，不等于图形重新绘制。原始公式图片可用于本地研发对照，但不得嵌入本路径的最终试卷。固定十题样张的检查不代表整个题库全部公式或老师价值已经验收。

题目类别本身不决定能否转换。常规计算题中的旧 MathType 字形和模板编码也可能触发拒绝；应定位到原生对象、题干或解析字段后再判断。几何题可使用原图配合原生公式排版，无须先将图形重画为 TikZ。

strict-v2 补充了明确 MTCode `U+03C0` 的 π 映射，并识别双边绝对值 `tmBAR` 的已确认尾部围栏槽位。可包含合法 COLOR 和 FULL 字号控制，但必须保留一个完整非空正文 LINE；错位、缺失、额外正文槽、未知变体或带数学修饰的围栏字符仍拒绝。不按字体位置猜字，也不删除任意私用字符。该规则的依据包括原生模板、原件预览和独立反例，并不表示任意 MathType 私用码表已支持。新目录带 strict-v2 身份，历史目录不自动重写。

`latex-exam-v2` 将解答题最后一行与随后的作答区连接，避免题干留在页尾、整块作答线移至下一页。长题仍可按正文自然分页；紧凑版和教师版正文不受此约束影响。

## 全库诊断

选题前可对同一题库运行只读扫描，输出目录须不存在且与输入目录分开：

    .venv/bin/python scripts/diagnose_latex_bank.py \
      --bank evals/private/question-bank-v3 \
      --semantic evals/private/semantic-v2-unified-final \
      --output work/my-latex-bank-diagnostic

输出 summary.json、questions.json、formulas.json、occurrences.json，将原始题数、可抽题数、不同公式对象、出现次数及来源绑定失败分别统计。除实际导出的题干、答案、解析外，`analysis_stem_blocks` 只用于诊断并单列；不能把两套出现次数混为一个导出分母。定位信息含文档/字段/原生部分/预览绑定，不输出题目正文、公式文本、图片或原生字节；诊断仍应放在忽略的本地目录。

可用 `--converter-source /path/to/frozen_math_latex.py` 比较可信的本机旧转换器；此选项会加载执行该 Python 文件，仅供开发验证。转换通过率仅衡量工程覆盖，不能代替逐式语义检查、实际 PDF 验收或教师评价。

证据见 [合同](acceptance/latex-export-v1-contract.md)、[执行记录](acceptance/latex-export-v1-execution.md) 和 [独立检查](acceptance/latex-export-v1-review.md)。

本轮增强见 [增强合同](acceptance/latex-enhancement-v1-contract.md)、[增强执行](acceptance/latex-enhancement-v1-execution.md) 和 [增强独立检查](acceptance/latex-enhancement-v1-review.md)。
