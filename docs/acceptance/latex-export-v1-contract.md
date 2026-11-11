# LaTeX试卷导出合同

日期：2026-11-11。用户明确要求以LaTeX重排，截图指出表格数字与公式/正文不一致。范围FR-15、DEV-004/404；沿用固定10题快照，保留Word路径和原件，不重新选题或改写答案。

## 约束与归属

公式必须从原MathType/OMML转换为真正LaTeX，以统一数学字体、大小和基线，不能将原公式截图嵌进LaTeX后宣称完成转换。几何图保留原图及比例；不支持结构明确计数报错，禁止丢字、猜符号或静默图片回退。截图表格全部数字统一排版，不保留MathType数字预览。

先对同一10题的stem及answer+analysis确定性转换，先验187次OLE（学生75、教师112），普通图6/3、学生1张3×8表，教师无题干。缓存可按原生SHA去重，覆盖分母按出现次数，不以去重数冒称总量。不重新调用识别/评分模型；需要模型时仍遵循显式开发验证及业务Provider约定。

Root拥有latex_contracts.py、CLI、依赖锁与合同/集成/文档；A拥有math_latex.py及测试，负责源对象转换；B拥有latex_exporting.py及测试，负责有序内容、表格、图形、版面和真实XeLaTeX编译；Sol独立反例、原件和实际PDF。实际Sol/high设计验收，gpt-6-luna/high实施（已披露对6.1 Luna不可用的替代）。

## 冻结API

Root类型FormulaConversion记录source_kind、source_sha256、native_payload_sha256、status、latex、mathml、converter、errors；FormulaCatalog记录snapshot_id、bank_sha256及按源SHA索引的entries。converted必须有非空安全数学LaTeX且无错误；unsupported保留错误且不声称有LaTeX。

bank_sha256 为 snapshot.bank_sha256，即完整 PracticeBank 模型的 canonical_hash；与原始 bank.json 字节哈希 source_bank_sha256 区分。snapshot_id 为完整快照模型的 canonical_hash。全部调用方须按此口径比较。

    # A: backend/app/math_latex.py
    build_formula_catalog(snapshot: BankPracticeSnapshot, bank_dir: Path) -> FormulaCatalog
    validate_formula_catalog(snapshot, catalog, bank_dir: Path) -> None
    # B: backend/app/latex_exporting.py
    export_latex_snapshot(snapshot, output_dir, *, bank_dir, practice_bank, curriculum,
                          current_progress, formula_catalog, xelatex_executable,
                          layout="answer_space") -> dict

OLE key为完整原OLE二进制SHA；OMML key为source_xml UTF-8 SHA。先绑定source payload与实际bank/DOCX/资产和当前进度，再消费原生定位。A可选经过验证的确定性库/本地原生导入；新增依赖由Root同步pyproject/uv.lock，版本、来源、许可证可追踪。不能将不抛异常等同没有未知记录或丢字。

## 排版与发布

answer_space/compact不改变题目内容、顺序和snapshot。教师仅答案解析；短题、表格和完整提示保持关联。生成student.tex/pdf、teacher.tex/pdf、assets/原几何图和manifest，全部成功才原子发布。返回dict含snapshot_id、layout、files、manifest_path、counts。

本机已有/Library/TeX/texbin/xelatex及ctexart、unicode-math、fontspec、enumitem、fancyhdr、geometry、booktabs、tabularx、FandolSong和Latin Modern Math。可使用这些现成依赖，避免先安装整套发行版。needspace.sty未发现，暂不依赖。统一数学和数字字体，包含源表格中普通数字与转换的小数。

XeLaTeX实际编译两遍更新页码，有界超时，不启用shell-escape。正文TeX转义，数学命令/环境白名单由Root校验，拒绝危险输入/文件引用/宏定义、未知PUA、缺字和失败公式，不能发布伪成功。几何路径不越界，TeX只引用输出内受控文件名；缺字体/编译器、公式不支持或编译失败不留成功目录。

## 验收

独立先固定数学出现及源SHA，比较原预览与新公式的字符、分式/根号范围、指数、角度符号等含义。重点截图题目、表格小数、计算式、圆周角及长解析。字体重排不要求像素相同，但不可改数学。分别统计转换、失败、未知及原几何图，不以公式PNG作为LaTeX成功。

运行合成和恶意TeX、源/版本/资产漂移、缺公式、编译器缺失/失败及原子清理反例。独立检查tex源码、PDF图像只含原几何、字体一致、全部页可读、题号答案对应及原件旧交付保护。固定10题通过不推导全部200题、教师满意度或MVP通过。
