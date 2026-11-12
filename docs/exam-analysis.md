# 学生考卷与学情分析

本地开发入口覆盖材料规范化、原题识别、答题卡布局与手写识别、证据关联、错题信号、局部修正和多次考试汇总。原始材料、模型响应及报告属于私有数据，放在忽略目录；该入口不是已部署的老师账号网页。

## 材料准备

prepare 读取私有 JSON 配置，字段为 paper（原题文件路径列表）、response（学生已批改卷/答题卡列表）、reference（可选标准答案列表）、expected_question_numbers（独立确认的大题编号列表）。角色由上传用途指定，不根据文件名猜测。PDF 完整渲染以保留叠加分数和批改，图片处理 EXIF；全白页仍计入物理页。重复文件、拼页卡片和派生裁剪不会增加考试次数。

    .venv/bin/python scripts/analyze_student_exam.py prepare \
      --inputs work/exam-inputs.json --exam-id exam-001 \
      --student-id private-student-001 --dpi 400 \
      --output work/exam-intake

真实学生使用匿名本地 ID；只有合成材料才加 --synthetic-student。日期不明确可以留空，不从文件名推断。

## 显式模型运行

业务只适配 DeepSeek；Codex CLI 仅用于明确选择的开发验证。两种通路不自动回退。先使用布局阶段验证拼页，再复用成功结果继续：

    .venv/bin/python scripts/analyze_student_exam.py run --stage layout \
      --intake work/exam-intake/intake.json \
      --curriculum data/curricula/junior-math-2022-draft-v2.json \
      --provider codex --codex-model gpt-6.1-sol \
      --codex-executable /absolute/path/to/validated-codex \
      --output work/exam-run

    .venv/bin/python scripts/analyze_student_exam.py run --resume \
      --intake work/exam-intake/intake.json \
      --curriculum data/curricula/junior-math-2022-draft-v2.json \
      --provider codex --codex-model gpt-6.1-sol \
      --codex-executable /absolute/path/to/validated-codex \
      --output work/exam-run

模型与可执行路径以本机实际验证为准。DeepSeek 使用 --provider deepseek，并按 model-validation.md 用 uv 加载本机环境配置；不在命令行/聊天中传密钥。

运行分 layout、paper、reference、response、diagnosis；每次调用绑定真实图片、目录、提示、Schema、模型与 CLI 摘要，记录私有原始响应及失败。resume 仅复用身份相同的成功阶段，输入变化须新建 run。物理 intake 不变，裁剪单独保存父区域与实际像素变换。批改总分、整题扣分、学生转录和模型解释分别展示；缺标准答案、不可辨或冲突项目保留未确定。

run.json 给出实际覆盖、未解决数量、失败及最终 HTML/JSON 路径。报告按分析版本与渲染源码摘要分别保存，更新版式后重放不会覆盖旧报告。离线报告在独立目录复制所需图片，展示原题、转录、批改和来源区域；不需要共享题库写权限。

## 老师局部修正

修正 JSON 为 ExamCorrection 列表，每项包含 id、base_revision、number 和原图 evidence，以及要改的字段。number 表示修正后的目的大题号；part_path 为空表示父题，数字斜线路径表示小问。修改手写可指定 response_id/corrected_transcript，改号指定 corrected_response_number；跨题改号须提供目的题原题和该作答的双侧证据。旧分析与原始模型响应保持不变，新结果记录父版本；作答变化使旧自动诊断失效。

    .venv/bin/python scripts/analyze_student_exam.py correct \
      --analysis work/exam-run/reports/REVISION/RENDERER/report.json \
      --intake work/exam-intake/intake-with-regions.json \
      --asset-root work/exam-intake \
      --curriculum data/curricula/junior-math-2022-draft-v2.json \
      --corrections work/exam-corrections.json \
      --output work/exam-corrected-report

## 多次考试

    .venv/bin/python scripts/analyze_student_exam.py summary \
      --analyses work/exam-a/report.json work/exam-b/report.json \
      --student-id private-student-001 \
      --curriculum data/curricula/junior-math-2022-draft-v2.json \
      --output work/learning-summary.json

默认最近三次，允许 --exam-ids 显式选择。只有一两次也可汇总；日期未知提示顺序限制。按同一学生与原始材料身份去重，改考试 ID、重复导入或重跑模型不虚增次数。考点汇总为有证据的练习信号，不输出由单错/总分/排名推断的掌握率。原题排除仍需可靠同题身份桥接，不能把本地题号当题库 ID；生成练习还需要老师设置教学进度和选择考点。

本批合同见 acceptance/exam-analysis-v1-contract.md，执行和独立检查分别见 acceptance/exam-analysis-v1-execution.md、acceptance/exam-analysis-v1-review.md。固定一名学生一次考试的验证不能代替真实三次考试、完整 MVP 或教师价值验收。
