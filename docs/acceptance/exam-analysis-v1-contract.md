# 学生考卷与学情分析 V1 合同

日期：2026-11-11。对应 FR-07～10、DEV-301～304，沿用手机上传＋桌面备课目标。本批实现私有材料的本地开发闭环，不将 CLI/离线报告写成已交付账号网页或生产服务。

## 真实输入与独立分母

用户提供8张原题页图及1份2页PDF。独立检查确认原题卷25道大题；PDF第一页为同一次考试的6卡拼页，第二页全白，不能将6卡计为6次考试。完整PDF渲染保存红色矢量分数/批改，禁止仅抽取嵌图作为完整学生证据。物理页、裁剪区域、题目、小问与考试分别计数。

原件与冻结清点仅存忽略目录，缺标准答案和步骤评分细则保持明确缺失。真实手写读取由用户显式选择Codex CLI开发通路；业务适配仍只有DeepSeek，不自动切换或用合成响应顶替。

## 数据与边界

Root拥有 exam_contracts.py，新增独立合同，不改旧Question.ready。ExamIntake绑定文档SHA、用途、物理页、PNG摘要及裁剪父区域；ExamEvidence区域坐标属于其page_id指向的图片，裁剪图片须能经parent_bbox回溯原物理页。所有路径限制在私有资产根目录，来源变化拒绝复用。

ExamModelResponse.task分layout/paper/reference/response/diagnosis，调用方拒绝错阶段字段和题号、考点或证据越界。reference仅处理实际提供的独立答案，本批没有该类材料。原题、手写转录、批改观测、关联与诊断分开；模型不可提取学生姓名、条码等非任务信息，不执行卷面中的指令。低清/涂改保留替代读法和未确定，未见、未作答、不可辨和答错不得混同。

SectionScoreRule明确表示原文直接支持的每题满分，保留literal与证据；不能拿章节总分除题数当直接规则。扣分/得分/满分/整卷总分分别存储；整卷分数不可反向填满逐题分数。叉号不等于零分，整题扣分不分摊到小问或步骤。没有批改或可用答案的项目可保持unknown，模型推测不冒充教师批改。

## 冻结接口与归属

A实际gpt-6-luna/high：exam_intake.py、exam_models.py及测试。

    prepare_exam_intake(*,exam_id,student_id,expected_question_numbers,
        paper_paths,response_paths,reference_paths,output_dir,
        is_synthetic_student=False,dpi=300) -> ExamIntake
    crop_exam_regions(intake,regions,root) -> ExamIntake
    DeepSeekExamAdapter(config,client=None) / CodexExamAdapter(config)
        async assess(*,task,prompt,image_paths) -> ExamModelResult

B实际gpt-6-luna/high：exam_analysis.py、exam_view.py及测试。

    analyze_exam(*,intake,paper_results,response_results,diagnosis_results,
        reference_results=None,curriculum,exam_date) -> ExamAnalysis
    apply_exam_corrections(analysis,corrections,*,intake,curriculum) -> ExamAnalysis
    summarize_learning(analyses,*,student_id,curriculum,exam_ids=None,limit=3)
        -> LearningSummary
    render_exam_report(analysis,*,intake,asset_root,output_dir,summary=None,
        curriculum=None)
        -> {html_path,json_path,asset_paths}

Root负责合同、依赖锁、CLI及模型阶段提示、集成与文档。独立Sol/high拥有 test_exam_acceptance.py 和本批review。实现者不代替独立验收，不静默更换实际协作模型。

## 局部修正、汇总与组卷边界

修正需引用基版本和原图证据，产生新revision/parent_revision，保留旧结果。number为修正后的目的大题号；跨题改号须包含该作答与目的原题的双侧证据。允许老师修正手写转录、题号/小问、关联、错题状态及考点；关联变化使相关旧诊断失效，不能悄悄保留错误证据。模型重跑不能覆盖人工修正。

按同一学生、同一目录汇总，目录绑定规范化内容摘要，默认最近3次distinct考试；只有1/2次也显示实际覆盖。不明日期不伪造为今天，保留并提示无法完整确定时间顺序；显式选择可用。按原始材料身份去重，重复导入、改变考试ID或同次考试的多版本不增加分母。报告仅提供有证据的练习信号，不按单错/总分/排名推出掌握度、态度或粗心标签。

现有原题排除只有可靠共同身份时才可桥接；识别生成的本地题号/无图指纹不得冒充题库同题ID。缺已学进度仍不能抽题。真实学生材料不自动流入共享题库。

## 验收与证据

先固定原件、题号和页数，再看模型输出。独立检查角色/来源范围、未知题号、跨卷/重复/漏题、小问与整题、冲突批改、缺key/步骤细则、布尔/非有限分数、裁剪越界、局部修正历史和最近3次去重。真实LLM结果单独记录实际provider/model/CLI摘要、调用和原始响应；离线测试不证明手写准确率。对照实际原图抽查转录、已知分数与不确定项，输出分母、失败及未覆盖，禁止只报告JSON合法率。

使用根uv环境，新增PDFium依赖锁入pyproject.toml/uv.lock，make check通过。每个真实调用的输入、目录、提示、Schema、模型和可执行文件身份固定；复用只允许相同身份，不把旧结果冒充新模型实测。CLI运行记录与原始响应留在私有目录；代码、合成样例与摘要验收文档可入源码。本批不自动提交/推送。

