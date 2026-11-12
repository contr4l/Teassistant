# 新用户默认额度独立验收

依据 `default-user-credit-v1-contract.md`。总设计/独立验收实际 `gpt-6.1-sol/high`，并行实施实际 `gpt-6-luna/high`。**本批本地功能验收通过，未发现剩余阻塞。**本批只使用合成账号、本地 SQLite 与 ASGI 测试，不读取真实密钥、不调用模型、不检查生产容器，也不改旧学生资料或题库。

## 已运行的关键反例

新增 `backend/tests/test_default_user_credit_acceptance.py` 共 13 项实际通过：

- 初始设置为 CNY 10.000000、revision=1；账号创建时得到一次默认额度。修改为 20.123456、再改为 0，只影响随后新建的账号，先前钱包保持原额。
- 用临时禁用新初始化 helper 的方式构造两种显式合成升级前用户：既有钱包授权 7.500000、旧账号没有钱包。改默认值后，前者保持 7.500000，后者读取钱包仍为 0；登录不会补发。
- 重复读取默认值/钱包、重复调用初始化 helper、重复 init 及重新创建 Store 模拟重启，所有表的行数和初始赠额保持不变；管理员保存的新默认值持久化，重启不恢复为 10。
- 在 `Store.create_user` 调用的新钱包 helper 完成并 `flush()` 后人为抛出异常，用户表、钱包及全部审计表行数均回到事务前；不存在只回滚账号、赠额却单独提交的情况。重复用户名也不留下孤立钱包或赠额事件。
- 负数、超过六位小数、NaN、Infinity、bool、float 拒绝且设置/审计不变；旧 revision 保存冲突，不覆盖新值或新增虚假配置事件。
- 真实 ASGI 路径：教师 GET/POST 默认设置均 403；管理员缺 CSRF 为 403；错误类型 422；合法保存成功；旧 revision 重放 409。管理员经用户创建 API 新建账号后，读取同库钱包为 20.123456，原账号仍为 10.000000。不要求先配置 DeepSeek 密钥。

独立读取实现确认 `Store.create_user` 的 `session.add/flush` 与 `initialize_user_wallet(session,user.id)` 位于同一个 `_sessions.begin()` 内；helper 不自行提交，使用同一 engine/session，默认值配置和赠额来源 revision 有审计。旧 `get_wallet` 未变成赠额入口。

## 原额度门禁回归

原 owned `test_admin_billing_acceptance.py` 只在 `metered_environment` 夹具创建账号前增加显式 `save_defaults('0',1,...)`，保证它原本的零余额、精确一份预占及不足重试反例不被新赠额 10 掩盖。未降低金额、放松断言或删除旧用例。

实际命令：`.venv/bin/pytest backend/tests/test_default_user_credit_acceptance.py backend/tests/test_admin_billing_acceptance.py -q`，结果 **40 passed、1 已有 warning、3.50s**（本批新增 13、原账本 27）。独立文件 Ruff 检查与格式检查通过；warning 为已有 Starlette/httpx 弃用提示。实现并行落盘，首次执行时已可用，本报告不虚构先红后绿记录。

## 浏览器、全量结果与限制

独立直接核读 Root 的 `work/default-user-credit-v1/browser-v2/result.json`，并亲看 `desktop.png`、`mobile.png`。该流程明确 is_synthetic=true、model_calls=0：页面初始默认 10，管理员保存 **25.123456** 后经 GUI 创建新账号，钱包为 25.123456；再保存 **0** 并经 GUI 创建账号，钱包为 0；先前账号保持 10。页面没有 JS 错误或横向溢出，桌面/手机的默认配置、版本、保存按钮及钱包卡片均可读；默认额度与模型密钥配置分开，截图中密钥未配置仍能完成操作。

首轮浏览器 v1 失败是脚本用 `get_by_label(...,exact=True)` 查找初始密码框时，真实 label 含说明文字而超时；Root 改用 admin-form-panel 内密码框定位后重跑 v2 成功。失败日志保留，不将它写成业务修复或虚构首次浏览器即成功。浏览器操作由 Root 执行，本验收者复核结果与页图，没有重复创建账号。

最终直接核读 `work/default-user-credit-v1/make-check-final.log`：Root 全量 **709 passed、1 已有 warning、42.12s**；`frontend-check.log` 显示类型检查/构建通过，最终 JS 为 `index-i4TZgsPf.js`。这些为 Root 执行证据复核，独立实际执行数为上述 40 项，不冒充独立重跑全量，也不重复昂贵旧业务链。

跨数据库一致性结论目前来自实际 SQLite 和源码事务核对，未运行 PostgreSQL，也不将新用户赠额称为实际充值付款或供应商余额。既有用户不补发是本批明确范围，不是遗漏；管理员另行追加额度仍沿用原有接口和审计。本报告的浏览器来自隔离合成实例，既有本机预览更新及旧钱包保护由 Root 另行验证，不能据此假定所有已有运行服务已热更新。
