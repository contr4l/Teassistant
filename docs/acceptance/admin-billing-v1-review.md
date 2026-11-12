# 管理员模型配置与累计估算钱包独立验收

依据 `admin-billing-v1-contract.md`，独立设计/验收为 `gpt-6.1-sol/high`，实施者实际为 `gpt-6-luna/high`。**本批本地开发验证通过，未发现剩余阻塞。**本轮仅本地合成凭据、SQLite 与 MockTransport；不使用真实密钥、不向 DeepSeek 发请求、不改旧原件、报告或既有业务产物。独立后端关键反例、真实独立 Python 子进程桥接与最终浏览器证据均已检查，不将本地估算账本称为官方账单核验。

## 固定口径

用户确认累计授权、累计使用和剩余额度，没有日/月重置；接受按本平台配置费率估算。金额以整数微元存储，预占也占用可用余额；未知 usage 不能写成零。固定输入保守上界 1,048,576、当前输出上限 8192，默认最大预占 2.162688 元/HTTP 尝试，所有重试、Worker 与 CLI 子进程共享数据库门禁。它是平台估算钱包，不是供应商正式账单或该账户在平台外的全部花费。

官方依据已只读核验：[价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) 存在峰谷及中国法定节假日规则，本批采用明确固定配置价格，不假称自动精确复刻计费；[usage](https://api-docs.deepseek.com/api/create-chat-completion/) 区分输入缓存命中、未命中和输出；[余额](https://api-docs.deepseek.com/api/get-user-balance/) 只返回共享账户当前余额，不能据余额变化分摊教师。未发任何认证请求。

## 独立反例与执行状态

`backend/tests/test_admin_billing_acceptance.py` 已先于账本模块落盘：

- 两个真实 spawn 子进程通过同一个文件数据库争抢最后一份预占，只允许一个成功；进程各自创建 engine，不使用线程或内存锁冒充跨进程。
- 默认零授权、累计追加、重复加额冲突、跨 400 天不重置；同一逻辑 attempt 返回 created=False 且不再预占。
- 超时及重复 unknown 结算保留预占；余额不足时下一轮重试拒绝。HTTP 200 后业务 schema 失败仍按有效 usage 结算，重复结算不重复扣款，冻结价格不因调用者修改字典而漂移。
- 手工核实不超过预占、零金额核实可幂等、同 request_id 不同内容冲突；缺失/bool/负 usage 不免费退款；超上界保持预占并冻结后续调用。
- 北京时间昨日半开区间，覆盖 UTC 偏移、月底、起点包含/终点排除；按已知估算小计排序，未知费用保留 null 和预占，上线前 coverage=not_tracked。

首次运行 `.venv/bin/pytest backend/tests/test_admin_billing_acceptance.py -q` 在 collection 阶段因 `model_billing` 尚未落盘而失败，属于实施中间态。模块及补丁落盘后，独立先后实际运行 24、26、27 项通过；最终独立命令同上，结果 **27 passed、1 已有 warning、2.19s**。另实际执行 `.venv/bin/ruff check backend/tests/test_admin_billing_acceptance.py` 与 `ruff format --check`，均通过。已有 warning 为 Starlette/httpx 弃用提示，不是模型或账本失败。

## 实际 HTTP、权限与密钥证据

独立测试使用真实 `Store`、`MeteredExamAdapter`、DeepSeek adapter 解析和 `httpx.MockTransport`，没有绕开费用 transport：

- HTTP 200 返回有效 usage、无效业务 JSON，adapter 明确报业务解析失败，但钱包结算 **0.002408 元**、释放其余预占。管理员密钥与仅环境后备密钥两种路径均实跑通过；环境配置 revision=0 在最终实现可正常计费。该零版本问题在阅读时已反馈 Root，独立运行时修复已存在，不声称独立观察到其红态。
- 第一次实际进入 MockTransport 后抛 ReadTimeout，钱包保留 2.162688 元；adapter 发起重试时余额不足，**MockTransport 总调用数仍为 1**，证明拒绝发生于第二次 HTTP 发送前。
- 伪造其他 owner 的 BillingContext 在 adapter 创建时即拒绝，未发 HTTP、未占用真实 owner 钱包。内部 CLI 即使有环境密钥，缺必需计费上下文仍拒绝，没有降级为裸请求。真正跨进程部分为两个独立 spawn 解释器共用一个 SQLite 文件争抢同一钱包；没有把线程竞争或两个内存账本称为跨进程验证。
- 配置文件实际权限 0600、目录 0700；管理员配置优先环境后备，空密钥保存保持原值，stale revision 不修改原文件。公开配置和运行快照不含密钥或片段，配置对象 repr 隐藏密钥。
- 本地 ASGI HTTP 实跑管理员/教师登录：教师读取配置及全体钱包为 403、教师加额为 403、管理员缺 CSRF 加额为 403；正确加额两次同 request_id 仅累计一次。教师在 me 查询中传他人 user_id 仍只能得到自己的钱包。管理员保存密钥后的响应与 GET 均无密钥；含密钥的错误结构返回 422 也不回显输入。

设计审查中另推动修复：预占/结算的整数微元使用 ROUND_CEILING，低于一微元的正费用不能截为免费；SQLite 写事务在 SAVEPOINT 前显式 BEGIN IMMEDIATE，避免 legacy driver 提前提交与写竞争；若缓存命中/未命中/总量字段冲突，usage 保持 unknown 而不静默改价。这些边界均有持久独立反例；修复在首次可运行测试前已落盘，不伪造每项均有独立红态。

配置生效粒度另经源码核对并与 Root 明确：在**模型处理单元启动**时读取密钥/费率版本，已启动的主分析 CLI 包括其多阶段与重试继续使用启动快照；独立报告在其处理单元启动时单独读取。每次 HTTP 保存它实际使用的版本，而不是宣称配置保存后所有在途请求即时改价/换 key。本批采用说明与实现一致的这一口径，没有改运行中任务或追溯重算旧账。

## 集成链路、最终检查与限制

每个 client.post 前须成功提交预占；created=False 不允许再发 HTTP；没有服务器绑定的 owner/job 或子进程计费上下文时拒绝。transport 应先记账后进行业务 JSON 校验；进程中断、日志异常和租约恢复不能丢失未知占用。所有配置及额度写接口须 admin+CSRF，教师不能通过前端 owner 参数越权；密钥仅私有原子文件，不进入 GET、job、日志、argv、费用表或上游原始错误。

独立另读并执行 Root 新增桥接测试：`.venv/bin/pytest backend/tests/test_model_runtime_bridge.py -q`，**1 passed in 0.87s**。它实际 `subprocess.run` 启动新 Python 解释器，继承服务端 job/owner/lease 与共享数据库配置，经 `adapter_from_environment`、`MeteredExamAdapter` 与 MockTransport；子进程收到无效业务 schema 后失败，父进程读取同一 SQLite 钱包已结算 **0.002204 元**、预占 0、可用 **19.997796 元**，合成密钥未出现在 stdout/stderr。此处有真实子进程与共享持久账本，供应商响应仍是显式合成；不是重跑真实卷面或真实模型请求。

前端源码只读检查确认排序使用 BigInt、费用未知为 null、coverage 与追踪开始时间单列、保存密钥后清空输入、保存失败 toast 不转述密钥响应。最终独立读取 `work/admin-billing-v1/browser-v2/result.json`，确认它明确标记 is_synthetic、MockTransport only，并记录登录、密钥写入/清空、模拟余额检查、昨日排序/未知、累计加额、未知预占核实全部通过，page_errors=[]、无页面横向溢出。另亲看最终 `admin-after-desktop.png` 与 `admin-mobile.png`：配置生效说明已与处理单元快照一致，桌面与手机的金额、额度卡片、表格及核实结果可读。手机表格保留内部横向滚动，不等于页面横向溢出。浏览器流程由 Root 执行，本验收者复核其结果与截图，没有重复发起操作。

最终直接核读 `work/admin-billing-v1/make-check-final.log`：Root 全量 **691 passed、1 已有 warning、41.63s**；`make check-web` 类型检查/构建通过，前端最终 bundle 为 `index-BKECKoBR.js`。此处为 Root 执行日志复核，没有冒充本验收者重复运行全量。Root 最终还增加进行中 reserved 禁止人工核实、已失败 job 的遗留 reserved 可在核实后恢复等 API 边界，并纳入全量记录；本验收者保留自己的 27 项与独立桥接 1 项实际运行证据，不将 Root 总数写成独立执行数。

尚未真实调用 DeepSeek、检查真实账户余额、对账官方费用或运行 PostgreSQL 并发测试；跨进程结论目前来自本地 SQLite。预占提交与网络送达间若进程中断，无法证明供应商是否收到请求，因此保留未知尝试及预占，不将每条尝试都称为供应商已收费。没有重跑旧卷面识别、真实题库评估或此前未验证的生产容器部署。本报告不把软件反例、费用结构合法性或合成浏览器检查称为真实供应商账单准确性或教学价值证明。既有本机预览是否已加载本次 API/Worker 代码由 Root 另行检查；本报告的浏览器结论来自隔离合成实例，不能据此声称已有运行服务已热更新。
