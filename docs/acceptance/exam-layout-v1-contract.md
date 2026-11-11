# 试卷专用排版合同

日期：2026-11-11。依据FR-15、DEV-004/404及用户对作答版、紧凑版和教师答案排版的继续实施授权。本批沿用同一固定10题快照，不重新选题、评分或改写数学内容，不覆盖既有原件和交付件。

## 输出与接口

新增 `LayoutMode = Literal["answer_space", "compact"]`，默认answer_space。`export_bank_snapshot(..., layout="answer_space")`保留既有返回结构及四文件事务，manifest版本为 `source-practice-export-v2`，另记layout、template_version、teacher_content="answers_and_solutions"。学生版两模式题号/顺序/内容和同一snapshot_id相同；教师版只保留题号、答案及已有解析，不重复题干。

Root拥有本合同、CLI参数与集成脚本及文档。A拥有 `backend/app/print_layout.py`、`scripts/build_print_templates.py`、`templates/exam-print-v1/*.docx`及对应测试。B拥有 `source_exporting.py`与对应测试。独立Sol拥有新验收测试、私有源对象核验脚本及独立报告，不修改实现。

沿用项目已披露的实际分派：设计/独立验收gpt-6.1-sol/high，实现A/B为gpt-6-luna/high（工具未提供6.1 Luna时的替代，不更换名称）。

冻结A提供API：

```python
LayoutMode = Literal["answer_space", "compact"]
TEMPLATE_VERSION = "exam-print-v1"
make_exam_document(*, teacher: bool, layout: LayoutMode,
                   is_synthetic_student: bool) -> docx.document.Document
style_source_table(table) -> None
append_answer_space(document, *, question_type: str, layout: LayoutMode,
                    subquestion_count: int = 1) -> None
```

模板为程序生成的空白样式文档，不含原卷/学生/答案。样式名固定 `Title`、`ExamMeta`、`ExamSection`、`ExamQuestion`、`ExamBody`、`ExamOption`、`ExamAnswer`、`ExamWorkSpace`。A4，学生正文与题号首行11pt、选项和教师解析正文10.5pt宋体/Times New Roman、黑体小标题，16pt紧凑标题，清晰页码及姓名/日期空栏；合成学生标记可辨认且不占大块页头。教师ExamAnswer段后2pt，其他间距按模板。字体为工程默认，不冒称用户确认的品牌规范。

## 版式行为

- 题号与第一段题干同行，悬挂缩进；只有题首确切原编号可去除，不删小数和小问。按考点分组，保留全卷连续题号。
- 选择题按内容宽度用制表位排1行4项、2行2项或4行，保留选项文字、公式和顺序；无法可靠辨识时保留原顺序，不猜测或删除。
- 表格保留结构及每格内容，首列文字合理分配列宽，数字居中，避免把原生数据表做成布局容器。
- 作答版给计算、证明等free_response按内容/小问量留出可写空间；紧凑版不加该空间。短题、题号/首段、图与相关题干保持关联，大题可以在合理段落分页，不把所有内容强绑导致大片空白。
- 公式保留原OLE/OMML字节。可以校准显示几何及基线，但不得改数学字节、拉伸破坏宽高比例或用整题截图。普通几何图原比例保留。
- 实际页图复查补充：当前LibreOffice实测忽略OLE run的w:position及VML vertical-align，最终默认不增加这些无效且Word GUI未测的偏移。对可靠识别的短“计算：/原式”等引导+单个大型OLE采用独立展示公式段，题号提示仍同行；普通变量或叙述混排不作强制拆分。所有原生字节与尺寸比例不变。短题跟随链限于本题，最后段显式断开，不能跨题形成大块空白或拆开表格和后半题干。
- 教师答案清理重复的来源“答案/解析”标签，只在明确段首标记层处理，不修改源payload或误删证明、步骤、数学文字；答案和解析各自完整保留，长解析自然分页。

## 验收

真实样张沿用上一批advanced-v3固定10题（基础6/提高2/挑战2，含1张3×8表、圆几何与长解析），明确合成学生进度。两套学生布局及教师答案Word/PDF都要渲染检查实际全页；同题单保真、344旧次数不能沿用为新教师计数，新预期按实际导出scope从源ZIP独立重算。检查源OLE/预览/配图完整字节、相对顺序与绑定、题号、选项顺序、表格、重复标签、分页、作答区、字体和页码。覆盖无选项/长选项/公式选项、小数、纯图首段、缺转换器/资产漂移等关键反例。

模板与排版代码更新后运行make check（配置实际转换器），记录成功/失败版本与最终PDF逐页检查。样张布局质量属于本批工程验收，不代替老师满意度或真实教学价值验证。不引入其他业务模型或题库人工审核。
