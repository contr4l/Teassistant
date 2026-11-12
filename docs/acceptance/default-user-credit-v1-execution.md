# 新用户默认额度执行记录

日期：2026-11-12。用户新增：新用户默认额度10，管理员可设置。对应FR-18、DEV-106；[合同](default-user-credit-v1-contract.md) 先行，实际分工沿用Root集成、gpt-6-luna/high账本与前端并行、gpt-6.1-sol/high独立验收。没有以不可用的6.1 Luna名称代替实际模型。

## 最终行为

- 新账号创建时一次性获得默认 **10.000000 CNY**；管理员在“管理设置→模型与额度→用户额度→新用户默认额度”独立保存，可设0，最多六位小数。
- 新表`billing_defaults`保存默认值与版本，`billing_default_events`记录初始化/修改审计；重启和重复初始化不覆盖管理员值。版本冲突拒绝覆盖。
- `Store.create_user`在同一数据库事务内创建用户、锁读默认设置、初始化钱包及写入赠额事件；失败整体回滚。设置对之后创建的账号生效，既有账号及尚无钱包的旧账号不补发。
- `get_wallet`保留旧的缺钱包补零行为，不在登录、读取、列表或重启时发放。已用、预占和可用额度算法保持，追加额度仍只增加累计总额。
- 新增GET/POST `/api/admin/billing/defaults`，管理员权限与写入CSRF；无需先填写模型密钥。PRD、架构、计划及操作/部署说明同步，历史批次执行记录仍保留当时零额度事实。

## 实际检查

新增 **18 项**默认额度反例测试；此前691项，本批最终`make check`为 **709 passed，42.12s**，ruff通过，1条既有Starlette/httpx弃用warning。日志：`work/default-user-credit-v1/make-check-final.log`。`make check-web`类型检查及构建通过：`work/default-user-credit-v1/frontend-check.log`。

独立检查者新增13项并与其原27项计费验收合跑，**40 passed，3.50s**。覆盖初始10、自定义20.123456/0、旧钱包/旧无钱包、重复初始化/登录/重启、赠额已flush后抛异常仍整体回滚、重复用户名无孤立发放、非法金额/版本冲突、权限和CSRF。实现者账本定向检查39项通过；Root另外实跑原API/子进程桥接7项保持原门槛。旧门槛fixture显式配置0，避免赠10令“余额不足”反例失效，不通过更改预期掩盖边界。详见 [独立记录](default-user-credit-v1-review.md)。

真实Chromium连接隔离合成实例：默认表单为10，保存25.123456后通过教师账号页面创建用户，核对钱包25.123456；再保存0后创建另一账号，核对钱包0，已有账号保持10。刷新后设置保留，无JS错误或页面横向溢出。桌面1440px、手机390px截图已亲看，证据`work/default-user-credit-v1/browser-v2/result.json`、`desktop.png`和`mobile.png`。第一次browser-v1因脚本以完整label精确定位密码框超时（label包含帮助文字）；修正脚本locator后browser-v2通过，保留原失败日志，没有为此修改业务代码。

所有账号与材料为合成，无真实密钥、无模型调用或供应商费用。本地结果不代替生产PostgreSQL、Docker实建或教学价值验证，无新增依赖。

## 本地预览

既有`http://127.0.0.1:18072`已更新API/Worker与前端。按记录PID核对参数和cwd，空闲检查及停止API后再次确认无任务，先备份SQLite再重启；首页200，新增接口可用，原业务记录数及全部既有钱包总/已用/预占金额逐项比较一致。证据`work/default-user-credit-v1/preview-reload-result.json`，私有副本及截图留在ignored work，不提交源码。未给旧用户补发10元，也未在现有预览创建合成账号或改变管理员设置。
