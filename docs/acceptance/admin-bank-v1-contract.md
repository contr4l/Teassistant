# 管理员题库工作流 V1 合同

2026-11-14，用户新增：管理员预览、检查、更新、调整共享题库；明确首批通过上传原始Word试卷及解析自动加工入库。对应FR-01～04、DEV-205、UC-01，不增加题库人工审核。

## 交付与归属

- 集成负责人：题库状态/版本服务`admin_bank.py`、受保护API、当前题库解析与组卷接入、数据库原子发布、PRD/部署说明、集成验收。
- GPT-6 Luna/high实现者：`admin_bank_ingest.py`及对应测试；DOCX到可验证source/semantic/evidence包流水线，按现有DeepSeek计费通路调用。
- GPT-6 Luna/high实现者：`AdminBank.tsx`、`admin-bank.css`、`adminBankApi.ts`、App管理页接入。
- 独立检查者：最终代码、权限/并发/失败/真实已有库和合成导入反例，不只接受实现者报告。实际模型按会话记录，不把GPT-6 Luna写成6.1。

## 状态和更新语义

保留配置的只读种子库。共享数据库中的`bank_state`记录保存当前包相对路径（空表示使用种子库）、版本、管理员调整及历史；生成包在共享私有数据卷内不可变保存。API与Worker按同一状态读取，新任务冻结题库状态版本和有效题目版本；旧产物及不可变来源包保留。并发更新用expected_revision拒绝覆盖；导入完成与题库指针切换须与持有租约的job完成在同一事务中发布。

DOCX导入为增量资料输入、完整题库版本输出：既有来源文件与新文件按规范化试卷身份/角色合并，同卷同角色替换，其他试卷保留；歧义/重复角色拒绝。现有语义证据绑定完整来源库SHA，因此本版会对合并后的整个题库重跑AI，页面须明确显示此成本范围。每次HTTP调用沿用管理员账户额度、预占和实际用量估算；缺密钥/额度/渲染器/非法模型结果显式失败，不发布残缺新库，不回退模拟结果。

管理员可调整已校验题目的启停、目标考点、前置考点、难度（0～100）及备注，必须填写原因。调整是显式`admin_override`，与原AIassessment和调用证据分开；程序校验白名单、前置闭包与原有来源门禁，不把来源损坏题调整为可用。题干/答案/公式通过重传Word形成新来源版本和AI结果；不提供会丢原生公式的纯文本覆盖。调整历史保留操作者/时间/前后值，需版本冲突控制。

## HTTP 合同

所有接口仅管理员可访问，写接口同时要求CSRF。

- `GET /api/admin/bank`：`revision, configured, question_count, eligible_count, blocked_count, disabled_count, source_count, points[{id,name}], active_version, history[], imports[]`；未配置返回明确空态而非500。
- `GET /api/admin/bank/questions?q=&status=all|eligible|blocked|disabled&point_id=&offset=0&limit=25`：`revision,total,items[]`，摘要含`id,number,title,status,block_reasons,point_ids,prerequisite_ids,difficulty_score,adjusted,disabled`。
- `GET /api/admin/bank/questions/{question_id}`：摘要加`revision,question_html,answer_html,analysis_html,ai_assessment,adjustment,history[]`；HTML已按白名单清洗，图片只使用带权限检查的同源资源接口。预览不包含原始模型提示、响应及服务器路径。
- `GET /api/admin/bank/media?version=...&path=...`：只可访问所选题库包的媒体目录，拒绝越界/符号链接/其他文件，限定图片类型；不开放整个文件目录。
- `POST /api/admin/bank/check`，JSON `{expected_revision}`：重新执行来源/语义/媒体等校验，返回同库存摘要及逐类原因；不调用LLM，无人工批准状态。
- `PATCH /api/admin/bank/questions/{question_id}`：`{expected_revision,enabled,point_ids,prerequisite_ids,difficulty_score,reason,note}`；保存新调整历史，返回detail。`note`最长2000，原因1～500，disabled题仍显示原始阻断原因。
- `POST /api/admin/bank/imports`：multipart `files`（2～40份DOCX）、`expected_revision`、`reason`；每份上限30MiB，总200MiB；返回标准Job（202）。不接受服务器路径或其他业务供应商。
- `POST /api/admin/bank/restore`：`{expected_revision,version,reason}`，恢复保留版本及对应调整，返回摘要；继续执行门禁，不覆盖历史来源文件。

导入Job kind=`bank_import`，资源kind=`bank_import`，沿用GET/jobs权限与Worker租约。通过检查的新版本自动生效，无人工审批按钮；调用失败保留旧版，候选错误摘要可追踪。返回422非法内容/目录点，409并发版本冲突，403老师/缺CSRF，404未知题/文件；所有错误不回显凭据或私有路径。

## 流水线函数合同

`process_import(store, settings, job, directory, *, base_bank_dir: Path|None) -> dict`，job.input_data含`files[{filename,path,sha256}], reason, bank_revision, curriculum_sha256`；文件path相对settings.data_dir且在提交时摘要固定。directory是按job租约新建的私有任务目录。成功返回`package_path`（相对data_dir、含source/semantic/evidence）、`question_count,eligible_count,blocked_count,source_count`，以及必要计数；调用方复验生成包和当前课程版本后执行数据库发布。失败抛固定语义异常，不切换当前题库。

## 验收

真实种子库计数、管理员预览、来源/证据未改、老师与未登录拒绝、CSRF、任意路径/外链拒绝；合成DOCX与可控HTTP模型响应贯穿导入→Worker→新库→教师库存，计费归属；缺密钥/额度、非法响应、版本变更、租约失效、重复/歧义文件、媒体异常保持旧版；metadata调整影响真实组卷并保留AI原证据；前端实际构建及桌面/窄屏检查。根目录`make check`和`make check-web`，远端板上构建/真实DeepSeek/老师价值单列未验证。
