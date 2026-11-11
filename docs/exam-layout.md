# 试卷版式与导出

本地来源题库导出支持两种学生版式，默认使用作答版。两种版式来自相同冻结题单，题目内容、题号、顺序和考点分组不变；教师答案统一采用紧凑排版，只输出对应题号、答案及已有解析。

| 版式 | 用途 | 行为 |
|---|---|---|
| `answer_space` 作答版 | 打印给学生直接作答 | 计算、证明等解答题按小问数量加入作答区 |
| `compact` 紧凑版 | 老师选题、讲解、学生另纸作答 | 不新增作答留白 |

固定A4、宋体正文配Times New Roman数字、黑体小标题、连续页码。题号与题干同行；可靠识别的选择题选项按宽度分行，原有表格按内容分配列宽。MathType/OMML仍保留原生数学及预览，几何图保持原比例。版式设置是工程默认，不代表已完成老师偏好或品牌规范确认。

## 生成练习并选择版式

沿用 [本地组卷入口](bank-practice.md)，增加参数：

```bash
.venv/bin/python scripts/build_bank_practice.py \
  --bank evals/private/question-bank-v3 \
  --semantic evals/private/semantic-v2-unified-final \
  --progress work/my-progress.json --request work/my-request.json \
  --output work/my-compact-practice --export --layout compact \
  --soffice /absolute/path/to/soffice
```

每次输出目录必须是新目录；合成学生演示需加 `--synthetic-student`。教师文件不会因为学生选择作答版而加入作答区。导出manifest标明 `source-practice-export-v2`、layout、template_version和teacher_content；快照身份不包含版式，便于验证同题单的不同输出。

## 用同一题单比较两种版式

已有 `source-practice-snapshot-v1` 快照时，无须再次抽题：

```bash
.venv/bin/python scripts/export_practice_layouts.py \
  --snapshot work/bank-practice-v1/advanced-v3/snapshot.json \
  --bank evals/private/question-bank-v3 \
  --semantic evals/private/semantic-v2-unified-final \
  --progress work/bank-practice-v1/synthetic-progress.json \
  --output work/layout-comparison \
  --soffice /absolute/path/to/soffice
```

该示例题目为真实资料，学生及进度为合成演示。输出 `answer_space/` 和 `compact/`，各包含学生DOCX/PDF与教师DOCX/PDF，另含逐字节保留的snapshot及comparison.json。每种布局生成前重新验证来源、语义、目录及当前进度，两种全部成功才发布整个比较目录。中途失败或进度变化时不留下成功目录。

## 模板维护与验证

空白模板在 `templates/exam-print-v1/`，生成程序为 `scripts/build_print_templates.py`，样式与作答区规则在 `backend/app/print_layout.py`。生成模板不需要真实题目；模板和样式可以入源码，字体文件、真实题目、学生信息及本地输出不可入源码。

模板更新需运行生成程序并重新检查实际导出文件。模板具有 `Title`、`ExamMeta`、`ExamSection`、`ExamQuestion`、`ExamBody`、`ExamOption`、`ExamAnswer`、`ExamWorkSpace` 样式。调整显示尺寸时必须保持图像比例、公式原生字节及内容顺序，不以文本提取成功代替真实PDF视觉检查。

本机验证使用 `work/print-fonts.conf` 显式映射宋体到系统Songti SC、黑体到Heiti SC；字体仅在私有work目录。服务器或老师电脑的字体环境可能不同，部署应固定合法可用的字体及转换器后重验。Word原生对象保留不等于已在所有Word/MathType版本测试编辑体验；最终PDF作为本批打印版面检查依据。

本批合同和执行证据见 [版式合同](acceptance/exam-layout-v1-contract.md)、[执行记录](acceptance/exam-layout-v1-execution.md) 与 [独立验收](acceptance/exam-layout-v1-review.md)。
