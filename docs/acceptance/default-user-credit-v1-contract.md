# 新用户默认额度补充合同

2026-11-12，用户要求新用户默认额度为10，管理员可设置。对应FR-18/DEV-106，币种沿用CNY；本批按**账号创建时一次性赠予**实现，既有用户（包括尚未建立钱包者）不补发，修改只影响随后创建的账号。默认额度可设为0，最多六位小数，金额上界沿用账本。

默认值保存在共享数据库独立新表，不修改旧表结构；初始10.000000，升级不覆盖管理员已保存设置。配置及初始赠额都有审计。创建账号、读取默认设置、初始钱包及赠额事件在同一业务数据库事务中提交；失败不留下半成品。get_wallet、登录、列表、重启均不发赠额。修改设置采用expected_revision防止旧页面覆盖。

新增GET `/api/admin/billing/defaults` → `{currency:'CNY',new_user_credit:string,revision:int}`；POST同路径，body `{new_user_credit:string,expected_revision:int}`，返回同GET。仅admin；POST须CSRF，非法金额422、版本冲突409。独立于DeepSeek密钥配置保存，不要求先填密钥。

内部BillingStore：`get_defaults()`、`save_defaults(new_user_credit,expected_revision,actor_id)`；`initialize_user_wallet(session,user_id)` 在Store.create_user既有事务中调用（不自行提交）。旧get_wallet仍为缺失钱包创建零余额，避免向旧用户补发。

文件归属：Root负责合同、web_store/web_api集成、现有API/桥接fixture、文档和浏览器；实际gpt-6-luna/high A负责model_billing与对应测试；实际gpt-6-luna/high B负责AdminModelBilling/modelBillingApi前端；Sol/high独立验收负责新test_default_user_credit_acceptance.py与review证据。共享接口按上述冻结通知。

验收：默认10；设20.123456/0影响新账号；旧钱包、旧无钱包用户不变；重复读取/登录/初始化不重发；重复用户名/事务失败无孤立赠额；配置冲突及精度/负数/越权/CSRF拒绝；重启持久化；真实浏览器修改默认值后创建用户、钱包验证。均为合成用户，不调用真实模型。
