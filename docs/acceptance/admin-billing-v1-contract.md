# 管理员模型配置、估算费用与累计额度合同

2026-11-12，用户新增：管理员设置 DeepSeek API、查看昨日按用户排序花费、设置用户额度。用户已确认额度为**累计增加总额度、记录使用额度、超过禁止继续使用**，没有日/月重置；接受“本平台 token 用量 × 配置单价”的费用估算并明确标注。关联新增 FR-18、DEV-106（本批同步PRD与计划）；不新增供应商、题库审核或教师付费功能。

## 计费与安全边界

工程默认币种为 CNY，金额由十进制字符串输入/输出，数据库以整数微元保存（1元=1,000,000微元），不使用 float。仅本平台实际发出的 DeepSeek 请求入账，不能从共享账户余额变化推导用户费用。Codex 仍仅显式开发验证，不计入 DeepSeek 钱包，也不伪造其费用。

本批按**固定配置费率**估算，不自动处理峰谷与中国节假日，不称官方扣款或精确账单。初始参考2026-11-12中文官方高峰价：每百万输入缓存命中0.04元、未命中2元、输出8元。费率可由管理员修改，在每个模型处理单元创建适配器时冻结密钥、费率及配置版本：主分析子进程的多阶段与重试沿用其快照，独立教师报告在启动时另取快照。保存影响后续启动的处理，已开始的处理保持原配置；每次实际HTTP尝试都记录对应快照，旧账不按新价重算。来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ 。公开GET/user/balance仅用于显式检查API与账户余额，不作用户分摊来源。

累计钱包：`remaining_credit = total_credit - used_credit - reserved_credit`。新账号默认一次性发放10元，管理员可设置默认值，修改只影响以后创建账号，已有用户不补发（覆盖此前默认0设计，见 [补充合同](default-user-credit-v1-contract.md)）；仍可追加正额度而非覆盖或重置。已知HTTP200但业务JSON/Schema失败仍按usage结算；超时/进程中断/无有效usage保留预占并单列未知，不能记为0或随job租约过期自动退款。每次网络重试独立预占。

每次 `client.post` 前，必须共享数据库条件原子更新余额并同事务写attempt，不能只在提交任务或收到最终结果时检查。固定输入保守上界1,048,576 tokens，加该请求输出max_tokens（现有上限8192），按max(命中价,未命中价)预占。默认每次最大预占2.162688元；余额低于该请求预占即拒绝，页面须明确说明“预占不是实际扣费，返回用量后释放差额”。本批仅支持已核准的业务视觉模型deepseek-flash；未知模型拒绝，不能沿用不实上界。

如果返回usage超出已冻结预占上界，按异常保持预占、冻结该账户后续调用并标记待核实，不静默截断费用或透支。管理员可对未知attempt在核实后手工确认估算额（0至预占额），必须原因、幂等request_id及审计；不删除原attempt，不冒称补出了供应商usage。

昨日按北京时间自然日[start,end)，数据库存UTC，按请求开始日归属；只聚合当次冻结费率的已结算估算金额降序。未知/未结算次数、预占金额并列展示；已知小计不等于含未知的总花费。上线前历史不可回填假0，响应含tracking_started_at与coverage(not_tracked/partial/tracked)。

密钥只写入私有数据目录的0600文件，目录0700、原子替换与写锁；GET只回key_configured/source/revision，不回显key或key片段。密钥不进入job、argv、日志、提示词或费用表，环境变量仅为兼容后备，管理员配置优先。只允许官方HTTPS DeepSeek endpoint且不跟随重定向，错误不回传原始响应正文。所有配置/加额/核实写接口限admin+CSRF，教师仅能查自己钱包。任务owner/job均由服务端固定，CLI子进程继承内部计费上下文，缺失/伪造拒绝，不回退成无账本裸请求。

## 文件归属与内部接口

- Root：合同、PRD/架构/计划/文档；`model_settings.py`（私有API配置）、`model_runtime.py`（每HTTP尝试transport）、web_api.py、web_jobs.py、analyze_student_exam.py桥接，App.tsx挂接，集成与证据。
- A（实际gpt-6-luna/high）：新`model_billing.py`和对应测试，独立SQLAlchemy metadata，通过现有Store.engine共用数据库；不改Web Store主体，Root在Store.init调用其init。
- B（实际gpt-6-luna/high）：新frontend/src/AdminModelBilling.tsx、admin-model-billing.css、modelBillingApi.ts；不改App.tsx/api.ts，Root挂第三个billing tab。
- 独立Sol/high：新test_admin_billing_acceptance.py与acceptance/admin-billing-v1-review.md，实际执行并发、未知费用、权限和昨日边界反例，不接受实现者自述代替集成。

`BillingStore(engine)`：

* `init()`：新建独立表与首次tracking_started_at；可重复，不改旧业务表。
* `get_wallet(user_id)`：返回Wallet；不存在原子创建零钱包。
* `top_up(user_id, amount:str, request_id:str, actor_id:str)`：幂等追加，重复同内容返回同效果，不同内容同request_id冲突。
* `reserve_attempt(user_id, job_id, stage, operation_id, attempt_no, model, config_revision, prices:dict, max_output_tokens:int)`：自行计算保守预占，原子扣可用余额，生成id；返回`{id,reserved_credit,...}`。prices字段见下，全部落冻结快照。不信任前端传预占数字。
* `settle_attempt(attempt_id, usage:dict|None, http_status:int|None, error_code:str|None=None)`：幂等处理已知用量或unknown保留；返回安全attempt摘要。
* `resolve_attempt(attempt_id, amount:str, reason:str, request_id:str, actor_id:str)`：仅未知/异常可核实，0至预占额，append审计且幂等。
* `daily_summary(day:date|None=None, now:datetime|None=None)`：日期默认昨日；返回下面日报格式，不含用户姓名，Root按现有user表补名称。
* `list_unresolved(limit=100)`：安全id/user/job/stage/started_at/reserved_credit/error_code/status，不含prompt/key。
* `get_attempt(id)`：安全摘要，用于核实前检查状态；API补充`can_resolve`。仍在有效运行租约内的reserved不允许核实；任务结束、丢失或租约过期的reserved可显式标记为process_interrupted后人工核实，不自动释放。管理员仍需先确认实际用量。

异常：`BillingError`通用父类；`InsufficientCredit`（余额不足/冻结）、`BillingConflict`（重复冲突）、`BillingNotFound`。所有可展示消息为固定安全文本，不能包含秘密或SQL。

Wallet：`{user_id,total_credit,used_credit,reserved_credit,remaining_credit,frozen:boolean,revision:int}`（所有credit为6位小数字符串）。

Prices：`{input_cache_hit:string,input_cache_miss:string,output:string}`，单位CNY/百万tokens，必须正数且有限，最多6位小数；金额和单价输入均限定合理数值上限以保护BigInteger。

日报：`{day,timezone:'Asia/Shanghai',currency:'CNY',tracking_started_at,coverage,known_cost:string,unknown_count:int,call_count:int,users:[{user_id,known_cost:string,estimated_cost:string|null,call_count:int,unknown_count:int,reserved_cost:string}]}`。有未知则estimated_cost=null，known_cost为已结算小计；按known_cost降序和user_id稳定排序，所有原始未知行保留。

## HTTP与前端冻结合同

以下均使用现有登录态；写接口沿用CSRF：

* GET `/api/admin/deepseek` → `{revision,key_configured,key_source:'admin'|'environment'|'none',model:'deepseek-flash',base_url,prices,currency:'CNY',reservation_credit,estimation_note}`。
* POST `/api/admin/deepseek`，body `{expected_revision:int,api_key:string|null,model,prices}`；api_key空/缺保持现密钥；返回同GET安全结构。只在用户点击时持久化，不触发模型请求。
* POST `/api/admin/deepseek/check`，body `{}` → `{ok,is_available:boolean,balance_infos:[{currency,total_balance,granted_balance,topped_up_balance}]}`，只用当前保存/环境密钥查官方余额，不调用chat；失败返回安全错误。
* GET `/api/admin/billing/yesterday` → 日报，user行附`username,display_name`；元信息和未知口径保留。
* GET `/api/admin/billing/wallets` → `{currency:'CNY',users:[Wallet+username+display_name+role]}`。
* POST `/api/admin/billing/users/{id}/credits`，body `{amount:string,request_id:string}` → Wallet。
* GET `/api/admin/billing/unresolved` → `{calls:[safe attempt rows + can_resolve:boolean]}`。
* POST `/api/admin/billing/calls/{id}/resolve`，body `{amount:string,reason:string,request_id:string}` → safe attempt summary。
* GET `/api/billing/me` → Wallet+currency；用户不能指定他人id。

后端额度不足HTTP402、冲突409、找不到404、输入错误422、非admin403；不让Key进入任何返回。前端组件只接`onNotice(kind,text)`，内部使用现有api<T>()与统一错误处理；密钥输入密码框、保存后清空，网络失败不把原key写toast；额度只做增额，不覆盖历史。默认新增‘模型与额度’tab，包括配置/余额检查、昨日排行、累计钱包/加额和待核实预占处理。

## 验收

真实独立进程争抢最后一份额度只能一次成功；重试/子CLI与父报告共享账本；格式错误但有usage计费；超时/缺usage/进程死亡不退未知；缓存复用没有新HTTP不记账；重复加额/结算/核实不二次扣减；历史价格不漂移；昨日跨UTC/月底与上线前未采集正确。管理员权限/CSRF/私密key与日志反例、前端实际浏览器保存/增额/排序验收；全部使用显式合成凭据和MockTransport，不假称真实DeepSeek账单核验或生产Docker通过。
