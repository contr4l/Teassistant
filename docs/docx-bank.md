# DOCX 试题库导入与本地检索

本批实现对应 PRD 的资料上传、试题结构化与考点索引。输入为原卷及对应解析版 DOCX；输出是保留来源的本地资料题库，供后续 AI 标记与业务题库适配使用。它不直接产生 `Question.ready`，不引入题库人工审核。

完整录入流程已按本批反馈调整为[试题录入工作流 V0.2](question-ingestion-workflow.md)：本页命令负责来源导入，不会自动启动后续模型标记、评分或发布。

## 使用

在项目根目录执行：

```sh
make setup
.venv/bin/python scripts/build_question_bank.py \
  --source assets \
  --output evals/private/question-bank-v3
```

输出目录必须尚不存在；更新时使用新的版本目录。构建在临时目录完成后发布，抛出异常时不发布目录，也不覆盖已有题库；单题结构异常会保留失败记录与提示。打开输出目录的 `index.html`，可以按试卷、来源考点及文字搜索，展开答案和解析，并下载原文件。无需启动服务器。

V3 不覆盖旧的 `evals/private/question-bank/` 和 `question-bank-v2/`。批次状态分为 `succeeded`、`partial`、`failed`，命令退出码分别为 0、3、1。部分完成或全失败时仍可产生诊断目录；应读取 `summary.json` 的状态、失败文件及关联计数，不能以目录存在就认为完成。坏文件单独记录，其余可处理文件继续；这不等于已实现生产后台任务和持久续跑。

| 文件 | 用途 |
|---|---|
| `bank.json` | `source-bank-v3` 完整来源结构，含批次状态、独立题号清单、关联证据、公式源结构、别名、失败文件与处理提示 |
| `question-bank.sqlite` | 本地程序检索；保留题目 JSON 与标签证据 |
| `catalog.csv` | 可用 Excel 打开的题目目录，含试卷、题号、来源考点与状态 |
| `index.html` | 离线检索预览，依赖同目录下的 `media/` 和 `sources/` |
| `sources/` | 原始 DOCX 副本，不修改源文档 |
| `media/` | 原始预览图及题目配图 |
| `xml/` | 来源正文 OOXML 与关系，便于追踪；OLE 原件仍在 `sources/` 的 DOCX 包内 |
| `summary.json` | 本次结构统计；不是识别质量或教学价值评分 |

通过程序检索：

```python
from pathlib import Path
from app.bank_index import query_bank

questions = query_bank(
    Path("evals/private/question-bank-v3/question-bank.sqlite"),
    keyword="一元二次方程",
    limit=20,
)
```

从项目外运行时将 `backend` 加入 Python 搜索路径。关键词搜索题干、答案、解析文字及来源考点；公式为图像时不能按公式字符串检索。

## 内容与边界

- 原卷和解析分别清点题号，再关联；解析额外题单独保留，不吞入上一题。题数声明按章节核对，无法确定的预期保持未知。文件前缀/后缀用于用途推定，注意事项不入题库。
- 题答需按顺序比较安全规范化文字、表格、数学结构和每个媒体。唯一绑定的 MathType 公式可由完整原生 MTEF payload 精确相同确认；其他图片仍须原尺寸 RGBA 完全一致。有效原生公式不同，即使预览相同也不能通过。只检查封装头与完整字节身份，不声称解析完整 MTEF 语法；无法确定的关联标 `unverified` 并使批次部分完成，不据此宣称题目或答案有错，也不新增人工批准。
- 保留段落、表格、MathType 预览和配图的顺序。MathType 的 OLE 原件保留，浏览器显示其源文件中的预览；并未将它们转换为可计算的 LaTeX 或原生 OMML。
- 支持的原生 Word OMML 公式保留源 XML 并生成安全 MathML，可显示分式、根式和上下标等。未知结构、尚未验证的 n-ary 布局及未验证样式明确失败并显示占位，不能静默消失后报成功。当前字体支持刻意保守，不能宣称完整支持全部 Word 公式。
- 浏览卡片使用原卷题干与解析版的答案、解析；解析版重复题干另行保存在 JSON 中。大题作为题目单位，不将小问虚增为独立题目。
- 答案按实际内容统计，单独答案标记不算可用答案；解析可选。同段多个答案/解析标记按位置分割，片段保留来源段落和文本范围。
- 来源考点从解析中的明确陈述提取，并附来源文档、段落与原句。规则词表有限，不等同于湖南统一考点白名单，也不代表已验证前置知识。
- `difficulty_score` 保持空值，`practice_ready` 为假。没有调用模型，没有用占位分数冒充 AI 难度评分。
- `source-structure-v2` 指纹保留表格与数学结构，再比较规范化文字和预览图字节；空题干不生成通用重复指纹，不声称完成语义或扫描近似去重。同用途同上下文的相同文件复用文档记录并保存所有副本，跨资料上下文保留独立关联。
- 输入文本只作为资料处理，不执行文档中的指令、宏、嵌入对象或远程内容。网页只接受本地预览图片和受限静态标记。

下一批复用已保存内容，先固定真实考点目录与来源到业务的合同桥接，再以 DeepSeek 执行业务标记、评分及未确认关联的自动核验；Codex CLI 仅用于显式选择的开发验证。全部结构成功题均执行统一映射：已有词条仅提供证据，无词条也从完整题目分析。发布还需程序校验、去重及数学导出适配，不能直接给来源记录改 `practice_ready=true`。现有业务合同对表格和 DOCX 定位的缺口及后续顺序见录入工作流；这些业务衔接能力尚未实现。真实模型质量和老师使用价值需要单独验证。

原卷、答案及导入产物只存于被 Git 忽略的 `assets/`、`evals/private/` 或交付目录；本批不将真实材料写入合成测试。

本批实际计数与验证结果见[执行记录](acceptance/docx-bank-execution.md)和[独立验收](acceptance/docx-bank-review.md)。

## 固定样本核验

固定清单必须先于输出冻结，包含预期题目、原件及内容快照摘要。对本批真实 200 题使用：

```sh
.venv/bin/python scripts/evaluate_associations.py \
  --bank evals/private/question-bank-v3 \
  --manifest work/association-v3/review-manifest.json \
  --manifest-sha256 652f17654d0da9748bfdc8562af2e73f0252d8c78673c7790c6d316b2b7eb33c \
  --report evals/private/question-bank-v3/association-evaluation.json
```

该命令检查固定分母、原件摘要、内容快照、原生证据和源图到输出图的精确像素，并重新比较关联；达到至少 190/200 且身份完整时退出 0。未确认题仍保留，构建批次仍可为 partial（退出 3）；核验达标不等于整批无异常。固定清单与实际报告均为本地资料，不提交真实内容。独立验收还从原始 DOCX 重算，不只接受导入器的自报状态。详见[合同](acceptance/association-v3-contract.md)和[独立验收](acceptance/association-v3-review.md)。

本批 V3 实测 193/200（96.5%），剩余 7 题因普通配图差异保留未确认；完整结果与限制见[V3 执行记录](acceptance/association-v3-execution.md)。
