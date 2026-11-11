# LaTeX 打印导出 V1 执行记录

本批针对 FR-15、DEV-004/404：解决混合 Word 来源中数字、公式字体和基线不统一，增加实际数学 LaTeX → XeLaTeX 打印路径。沿用先前固定十题，不重新选题、评级或改写答案。Sol/high 负责总设计与验收，实际实施为 gpt-6-luna/high，独立验收 gpt-6.1-sol/high；没有调用业务 AI。

## 冻结输入

snapshot_id 为 0034987d7de606fdc9b2b94457beee1fe46d890ed51b0b1b8228a02ba8814300。十道真实资料题，学生及全已学进度为明确标记的合成演示。独立读取原 DOCX 固定：学生 75 次、教师答案解析 112 次 OLE，总 187 次出现；100 个不同 OLE bytes SHA；对应 107 个原预览像素/尺寸变体。普通几何图学生 6 次、教师 3 次；学生一张 3×8 表。覆盖分母不能改用去重缓存数。固定样本没有实际 OMML，OMML 结构用例属于合成验证。

原始卷、来源库、语义 raw、原冻结快照及上一版 Word/PDF 均保留。源公式图片只用于私有研发语义对照，不嵌入本批最终 LaTeX 试卷。

## 实现及环境

- latex_contracts：原对象 SHA、快照与题库绑定；数学命令/环境白名单及控制字符检查。
- math_latex：原生 MTEF 记录、完整消费、符号与结构检查；重建目录拒绝篡改转换表达式。
- latex_exporting：有序文本、公式、表格、普通图形；二遍实际 XeLaTeX、独立临时目录、全部成功后原子发布。
- export_latex_practice.py：同题单两种版式，输入漂移或第二套失败均清理整个发布暂存目录。
- Word 原生导出保持独立，不以 PDF 回转 Word 冒充编辑能力。

本机 XeTeX TeX Live 2026basic，使用现成 ctex/Fandol、Latin Modern Roman、Latin Modern Math 及文档列明的包。第三方 mathtypejx 0.1.0 为 MIT，Git 提交 7f1b46358a3a7b0b5c992faa0dca91740a54efbb 锁入 pyproject.toml 和 uv.lock 的 export 组；项目不直接信任其成功结果，新增严格包装和独立反例。

LibreOffice 转 ODT 的探针保留了 75 个 draw:object-ole，没有产生 MathML，不能据此宣称完成公式转换。第三方库原解析的未知 record / 后续表达式跳过问题已由独立探针复现；本批严格层须在这些位置拒绝。

## 结果

固定样张转换 100/100 个不同 OLE、187/187 次出现，unknown/unsupported 为零；源预览有 107 个精确像素/尺寸变体，独立逐行对比实际 LaTeX 渲染。最终目录与原 DOCX 的对象及 payload SHA、逐媒体和源块顺序绑定，不以格式合法代替公式语义核对；并非检查原答案本身是否数学正确。

版式检查曾实际拦下字体名称找不到、正文 ∠/∴ 等缺字、短题题干与图/选项分离，以及零宽作答线。最终使用 TeX 字体文件名、已知正文符号数学化、受大小限制的短题 minipage、明确宽度及高度的作答行；不以 samepage 标签存在代替实际分页正确。长题允许合理跨页，未将任意超长题强塞进一页。

最终私有比较目录为 work/latex-export-v1/comparison-v6，沿用固定 snapshot_id。实际输出学生作答版 3 页、紧凑版 3 页、教师答案解析各 5 页。学生 75 次原生公式、6 次几何图、完整 3×8 表；教师 112 次原生公式、3 次几何图，无题干。最终 PDF 中普通图像只来自原几何图，公式预览图为零。

使用文档中的 export_latex_practice.py 命令，将输出参数设为上述新目录；两个 mode 各学生/教师实际二遍 XeLaTeX。源文件与缺字、溢出门禁、失败清理均保留。全部实际 PDF 页面经渲染检查，学生表格题的引言/表/提示同页，短选择题题干/配图/选项完整，作答横线可见。

PDF 检查使用捆绑 runtime 的 pypdfium2 5.14.0、pdfplumber 0.11.9、pypdf 6.21.4。已先尝试 Poppler 26.05.0，但本机捆绑工具的 Adobe-GB1 映射资源定位异常导致中文渲染缺失；Fandol 字体实际嵌入 PDF，改用 PDFium 对同一文件完整渲染中文后检查，未将失败的 Poppler 图当作通过证据。

最终 make check 退出 0：447 passed，0 skipped，23.12 秒。独立原件、版心、27 条可见作答线及原件保护清单结论见独立验收报告；命令和退出状态记录在私有 make-check-v6.txt。产品代码及全部测试使用根目录 uv 环境，真实 LibreOffice 集成由 TEASSISTANT_SOFFICE 显式指定，另包含真实 XeLaTeX 合成编译检查。

本批完成固定十题的 LaTeX 打印工程验证。未完成生产环境部署、整库全部公式覆盖和教师价值验收；首版样张不代表这些出口通过。

最终三份 PDF 单独复制交付，字节与正式导出一致；源文件 ZIP 仅含自足的 TeX、原几何资产及转换/来源清单，不含原 DOCX、OLE、字体二进制或 QA 页图。另将 ZIP 独立解压，在无题库依赖的目录实际二遍重编学生两版和教师一版，三份共 11 页按 PDFium 1.5 倍渲染逐像素相等。重编与副本校验记录保留在私有 bundle-recompile-verification.json 及交付清单。
