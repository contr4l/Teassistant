# 真实来源题库的本地组卷与导出

本模块衔接既有 DOCX 来源库与已完成的考点、难度初评，供开发验证使用。它保留原始文档的公式、图片、表格及题答分段，不把来源库强行改写成有虚构页码和坐标的旧 P0 数据。

## 输入

- `--bank`：来源目录，含 `bank.json`、`sources/` 和 `assets/`。
- `--semantic`：语义结果目录，含 `annotations.json` 与该批 `curriculum.json`；兼容显式迁移产物 `annotations.run` 的摘要及版本绑定。
- `--progress`：学生当前进度，使用 `contracts/p0-v1/StudentProgress.schema.json`。更新时写入新 revision 和真实已学考点，不能把“选择了目标考点”当作已学习全部前置知识。
- `--request`：`PracticeRequest`。真实初评的 `difficulty_version` 为 `junior-math-demand-v1`，不能沿用合成 P0 的默认版本。

语义结果引用的原始试跑证据目录也需保留在同一语义工作区内；验证器会核对原始 run 的题目范围、来源摘要和难度规则版本。开发证据可能包含本机绝对路径，迁移时使用 `scripts/package_question_bank.py --bank-dir 来源目录 --semantic-dir 语义目录 --output-dir 新目录` 生成自包含包。打包重写副本的证据路径为相对包根的路径，原始run/call字节与摘要仍逐项校验；不能只复制最终annotations。完整结构与挂载见 [部署指南](deployment.md)。

示例仅展示字段，`example-student` 及其进度为合成数据：

```json
{
  "student_id": "example-student",
  "revision": "progress-1",
  "curriculum_version": "junior-math-2022-draft-v2",
  "learned_point_ids": ["JM.NA.RATIONAL_CONCEPT", "JM.NA.RATIONAL_CALC"]
}
```

默认每考点10题；`reference_score` 可输入相关错题的参考难度。以下 JSON 没有对应的真实学生诊断，不代表已自动从考试提取目标：

```json
{
  "difficulty_version": "junior-math-demand-v1",
  "quotas": [{"point_id": "JM.NA.RATIONAL_CALC", "reference_score": 30}],
  "excluded_question_ids": [],
  "excluded_canonical_ids": [],
  "seed": 1
}
```

进阶模式显式填写 `count` 与 `by_difficulty`，例如 `"count": 10, "by_difficulty": {"basic": 5, "intermediate": 3, "advanced": 2}`；三档分别为0～39、40～69、70～100分。数量必须相加一致，不能用其他难度补缺口。

## 使用

先查看库存，可选加 `--progress` 限定当前已学范围：

```bash
.venv/bin/python scripts/build_bank_practice.py \
  --bank evals/private/question-bank-v3 \
  --semantic evals/private/semantic-v2-unified-final \
  --inventory-only --output work/my-inventory
```

生成快照及四份题答文件：

```bash
.venv/bin/python scripts/build_bank_practice.py \
  --bank evals/private/question-bank-v3 \
  --semantic evals/private/semantic-v2-unified-final \
  --progress work/my-progress.json --request work/my-request.json \
  --output work/my-practice --export --soffice /absolute/path/to/soffice
```

仅对合成学生演示增加 `--synthetic-student`；不传 `--export` 可只生成题单和缺口报告。每次使用新的输出目录，旧版本不会覆盖。环境需有 LibreOffice 和中文字体；本来源导出路径直接复制已有原生数学结构，不需要 Pandoc 重新生成公式。

输出含 `inventory.json`、`summary.json`、`snapshot.json`、`progress.json`；导出后含 `exports/student.docx`、`student.pdf`、`teacher.docx`、`teacher.pdf`、导出 manifest 与 `export-result.json`。真实资料及这些本地结果均不提交源码仓库。库存多标签数量存在重叠，不能相加当作全卷可同时抽题数；实际数量及缺口以快照为准。

试卷排版另支持 `--layout answer_space`（默认作答版）和 `--layout compact`（紧凑版）。可直接从已有题单生成两种版式比较，无须重抽题；教师版统一仅答案与解析。模板、命令和版面边界见 [试卷版式说明](exam-layout.md)。

## 自动检查与限制

来源结构、答案、题答关联、语义合同及考点闭包、版本和媒体字节均通过后才作为本地候选；带模型 warning 或未确认关联的题保留阻断原因。本批没有新增题库人工审核，也没有改变原始题库或旧业务 `ready` 状态。

全局容量分配确保同题只选一次，原题排除覆盖同身份的其他版本；全部目标及前置考点都要已学。导出前重读当前进度，进度撤回导致超纲即拒绝；资产损坏、原件变化、缺少转换器、转换失败均不发布成功目录。学生卷只含题目；教师卷输出相同连续编号的已有答案和解析，不调用模型补答案。

MathType 公式保留源 OLE 二进制对象及预览，OMML 保留原生数学节点；两者分开计数。保留 MathType 对象不等于已经验证所有电脑上的公式编辑能力，实际编辑仍依赖兼容的软件。新来源导出使用 A4 默认版面，实际 PDF 需独立逐页检查。

这仍是本地开发闭环，未交付老师账号、手机上传页面、学生进度编辑界面、考试诊断和完整生产任务服务。93点目录是工程草案，难度未以老师或真实学生数据校准；合成学生进度演示不能证明真实教学价值。实际覆盖及反例见 [本批验收](acceptance/bank-practice-v1-execution.md)。
