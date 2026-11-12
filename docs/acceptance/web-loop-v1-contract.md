# 教师网页闭环与管理员提示词 V1 合同

日期 2026-11-12。依据 PRD FR-05～16、UC-02～09、权限约定，以及用户本轮新增管理员编辑关键提示词的要求。交付 React/TypeScript 网页、FastAPI API、数据库会话与业务记录、独立持久任务 Worker；复用真实题库、学生分析与题答分离导出。手机侧上传，桌面侧备课。没有默认账号密码、公开学生静态目录或题库人工审核。

## 分工

Root Sol 负责共享合同、依赖锁、提示词注册及实际业务 Worker、部署和集成。实现者 A 实际 gpt-6-luna/high 负责 web_store.py、web_api.py、manage_web.py 及对应测试。实现者 B 实际 gpt-6-luna/high 负责 frontend 全部文件。Sol 独立检查者负责独立 API/权限/任务反例及验收文档。旧批未提交的学生分析文件保留，不覆盖历史真实材料和调用结果。本轮不自动提交。

## 运行方式

生产基线为 PostgreSQL，SQLAlchemy 保存业务事实；本地开发可显式使用 SQLite 文件。首版独立数据库 Worker 可在不启动 Redis 时本地运行，不能使用 HTTP 进程内 BackgroundTasks 执行识别或导出。Celery/Redis 调度保留为生产部署路径，实际验收据实记录，不把本地 Worker 宣称为 Redis 验收。

## API V1

API 路径 /api，JSON 字段 snake_case。列表直接返回数组。错误 JSON 为 {detail: string}。当前身份 {user:{id,username,display_name,role},csrf_token}；角色 admin/teacher，admin 可进入教师流程但没有跨老师学生访问的默认权限。业务对象均按 owner_id 隔离，越权统一404。HttpOnly SameSite=Lax 会话 cookie，所有已登录写请求须 X-CSRF-Token；密码哈希、登录节流和注销均为服务端能力。无公开注册；管理员创建教师。

| 请求 | 输入与输出 |
| --- | --- |
| POST /auth/login | {username,password} → 当前身份，设置cookie |
| GET /auth/me | 当前身份；无会话401 |
| POST /auth/logout | 注销；204 |
| GET /capabilities | {allow_codex:boolean,bank_configured:boolean}，需登录 |
| GET /curriculum | {version,points:[{id,name,prerequisite_ids}]} |
| GET/POST /students | 列表／创建 {name,school,grade:7/8/9,learned_point_ids:[]} |
| GET/PATCH /students/{id} | {id,name,school,grade,learned_point_ids,progress_revision,created_at,updated_at}；PATCH 包含 expected_revision 防止进度覆盖 |
| GET /students/{id}/exams | 考试列表 |
| POST /exams | {student_id,title,exam_date:null/ISO,question_count:1..200,class_rank:null,grade_rank:null,total_score:null,max_score:null} |
| GET /exams/{id} | 考试含 assets、analysis_job、analysis_summary；路径不返回服务器绝对文件名 |
| POST /exams/{id}/assets | multipart role=paper/response/reference, file；JPEG/PNG/PDF；返回 {id,role,filename,ordinal,size} |
| POST /exams/{id}/assets/order | {asset_ids:[完整有序ID]}，处理中的考试不可改材料 |
| GET /assets/{id}/content | 授权内容，禁止目录直出 |
| POST /exams/{id}/analyze | {provider:deepseek/codex} → job；Codex需显式开发开关 |
| GET /exams/{id}/analysis | {analysis:ExamAnalysis,teacher_report:object|null,pages:[{page_id,role,parent_page_id,parent_bbox,url}]}，尚无结果409 |
| GET /exams/{id}/analysis/pages/{page_id}/content | 按当前已发布intake白名单、owner与PNG摘要授权渲染原图/裁剪图 |
| POST /exams/{id}/corrections | {corrections:[ExamCorrection]} → job，保留基版本 |
| GET /jobs/{id} | {id,kind,resource_id,status:queued/running/completed/partial/failed,error:null/string,result:object|null,created_at,updated_at,attempts} |
| POST /jobs/{id}/retry | 新任务，固定本次输入和明确提示词版本；原失败保留 |
| GET /inventory?student_id=ID | {points:[{point_id,name,candidate_count,by_difficulty}],...} |
| GET /students/{id}/practices | 练习列表 |
| POST /practices | {student_id,exam_ids:[],quotas:[Quota],seed:0,layout:answer_space/compact} → {id,job_id,status} |
| GET /practices/{id} | {id,student_id,status,snapshot:object|null,files:[{id,label,url}],warnings:[],feedback:object|null,preview:object|null} |
| GET /practices/{id}/files/{name} | 授权下载，服务端文件名白名单 |
| POST /practices/{id}/feedback | {expectation:yes/partial/no,quality:1..5,saved_minutes:可负数,reasons:[],comment}；绑定本练习版本，可更新自己的反馈 |
| GET/POST /admin/users | 管理员列出／创建账号 {username,display_name,password,role:teacher} |
| GET /admin/prompts | [{key,label,description,active_revision,body}] |
| GET /admin/prompts/{key}/history | [{revision,body,created_at,created_by}] |
| POST /admin/prompts/{key} | {body,expected_revision} → 新激活版本；冲突409 |
| POST /admin/prompts/{key}/activate | {revision,expected_revision} → 将旧内容追加为新版本，不重写历史 |

上传限制默认每文件30MiB、每考试60文件；文件格式用实际内容核对，不信任后缀。持久记录中的私有路径不得从通用GET直接透出。

## Store 与业务 Worker 边界

web_store.Store(database_url)，建表 init()；engine 可供独立检查。全部时间 UTC ISO 字符串，ID 使用随机 UUID hex。以 SQLAlchemy 的持久 Record 保存业务 JSON，按 kind/owner_id/parent_id 索引；用户、会话、任务、提示词版本有独立表。

方法返回 JSON 可序列化 dict。get_record 找不到返回 None，create_record 返回完整记录包装 {id,kind,owner_id,parent_id,data,revision,created_at,updated_at}；update_record 更新整个 data，对 expected_revision 做乐观锁。

    create_user(username,password,display_name,role="teacher") -> user
    authenticate(username,password) -> user|None
    create_session(user_id) -> {token,csrf_token,user}
    lookup_session(token) -> {user,csrf_token}|None
    delete_session(token)
    list_users() -> list[user]
    list_records(kind,owner_id,parent_id=None) -> list[record]
    get_record(kind,record_id,owner_id=None) -> record|None
    create_record(kind,owner_id,data,parent_id=None,record_id=None) -> record
    update_record(kind,record_id,owner_id,data,expected_revision=None) -> record
    enqueue_job(owner_id,kind,resource_id,input_data,idempotency_key=None) -> job
    get_job(job_id,owner_id=None) -> job|None
    list_jobs(owner_id,resource_id=None) -> list[job]
    claim_job(worker_id,lease_seconds=900) -> job|None
    heartbeat_job(job_id,lease_token,lease_seconds=900) -> bool
    finish_job(job_id,lease_token,result,status="completed",error=None) -> bool
    ensure_prompts(defaults) # defaults dict[key] {label,description,body}
    list_prompts() / prompt_history(key)
    save_prompt(key,body,created_by,expected_revision) -> prompt
    snapshot_prompts() -> dict[key] {revision,body}

job 包含 input_data、lease_token 供 Worker，API须脱敏掉 input_data/lease_token。原子 claim、超时恢复和 lease_token compare-and-swap 防止旧 Worker 覆盖新执行。claim 将过期 running 回收后重试；有限最大尝试次数，失败保留。任务产物按 job ID 与执行 lease 独立目录；DB完成状态后才授权下载。重复幂等键只返回同一 owner 的同输入任务，异输入409。运行输入绑定进度版本、考试分析版本、材料快照和提示词快照。

web_api.create_app(settings=None,store=None)，settings 由 Root web_settings.py 提供。实例启动不自动运行 Worker。web_jobs.execute_job(store,settings,job) 使用冻结 job 输入运行实际识别、报告、组卷、导出和局部修正；web_jobs.run_once(store,settings,worker_id) -> bool 可供测试与独立进程。API根路径静态服务前端 dist（存在时），SPA回退不能吞掉未知API或私有路径。

## 提示词与确定性规则

管理员可编辑 layout/paper/reference/response/diagnosis/report 六段任务说明。公共来源约束、JSON Schema、权限、考点白名单、进度、数量、去重不随可编辑提示词关闭。每次提交任务保存全部有效版本与内容摘要，编辑只影响后续任务。报告要区分可读批改事实、数学解释、待确认，不把父题扣分分给步骤，不生成掌握率。报告为结构化模型输出后再渲染，不把写死的样例报告作为自动生成能力。

## 验收场景

1. 管理员创建两名老师；老师登录与注销，A不可访问B学生、考试、文件、练习、任务或反馈；老师不能改提示词。
2. 真实HTTP上传及页面重排、重新登录后数据仍在；手机390px宽完成材料上传，桌面查看同一考试。
3. 管理员编辑和回滚提示词；两个任务固定各自版本；AI输出仍受Schema及白名单限制。
4. 学生进度未设、题量不足、原题身份未确认、模型失败和转换器缺失均准确显示，不能伪装成功。未确认原题身份只能明确告知并允许老师补充排除，不能冒称已全部排除。
5. 登录→学生/进度→考试上传→持久任务→分析及报告→默认/进阶组卷→预览/题答下载→反馈。真实链路与显式合成HTTP/浏览器用例分别报告；页面刷新与进程重启不丢状态。
6. 独立检查运行关键反例，完整 make check 与前端 build/typecheck；真实 Word/PDF 检查复用既有原生与LaTeX验证，生产数据库/Redis和外网手机HTTPS未实测则明确列出。
