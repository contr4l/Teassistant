# 管理员题库工作流 V1 执行与验收

2026-11-14，依据[合同](admin-bank-v1-contract.md)，对应FR-19、FR-01～04、DEV-205。用户确认首批更新入口为原始Word原卷与解析上传。本批在本地`main`工作区实施，不代表已提交/推送或ROCK 5B服务已升级。

## 实施与分工

- 集成负责人（当前会话）：`admin_bank.py`、`admin_bank_api.py`、共享BankQuestion调整合同及Schema、Web API/Worker接入、Store多资源CAS原子发布、部署依赖与说明、集成测试。
- 实现者A（实际GPT-6 Luna/high）：`admin_bank_ingest.py`、对应8项测试，原件合并、来源重建、全量语义评分与计量、证据保存、完整包校验。
- 实现者B（实际GPT-6 Luna/high）：`AdminBank.tsx`、API封装、样式、共享安全预览与App页签；前端构建及浏览器尝试。
- 独立检查（实际GPT-6 Luna/high）：管理员/CSRF、覆盖与原AI分离、损坏来源不可解锁、CAS回滚、旧租约、真实来源库与调整后公式门禁。

来源包及AI证据不可变。共享DB中的题库状态指向种子挂载或共享私有卷的新包，保存管理员元数据覆盖与版本历史；API/Worker解析同一状态，防止网页修改不影响组卷。新Word来源与原件按试卷身份/角色合并，同卷替换、其他保留，歧义拒绝。现有证据合同绑定整库SHA，本版全量重评，上传页明确告知管理员额度消耗及新库重置当前覆盖。无人工审批队列。

## 实际结果

1. 检查此前私有部署归档，确认包含完整source/semantic/evidence/manifest。该包为200题、176可用/24受阻；这不是板子已挂载通过的证据。
2. 独立只读真实包测试通过：200/176/24；管理员HTTP库存与预览；人工元数据覆盖不改原source/annotations/manifest；新旧快照版本隔离、源文件门禁、公式catalog校验。此项没有新模型调用，不证明教师价值。
3. 合成HTTP集成通过：实际管理员认证上传DOCX→持久任务→独立`run_once`→原生DOCX解析→显式渲染替身及MockTransport→真实MeteredTransport/数据库结算→生成可验证包→同事务发布状态→教师inventory与capabilities读取。非法JSON模型响应反例保持旧指针，管理员用量正常结算、预占释放；教师不可读取管理员导入任务。没有把渲染替身或MockTransport写成真实识别通过。
4. 管理员标签/难度调整改变教师可用库存及选择结果；停用排除、恢复历史、版本冲突与提交后变更拒绝发布，原AI证据未改。损坏媒体保持受阻。
5. `make check`第二轮：**794 passed，1条已有Starlette/httpx弃用警告，117.40秒**，含静态检查；`work/admin-bank-v1/make-check-final.log`。首轮1 failed/791 passed（192.99秒）源于新增测试使用`from scripts`依赖仓库根路径；已改为同生产流水线的显式脚本加载器，失败日志保留在`make-check.log`。
6. 全量执行后补充旧任务兼容保护：缺bank_revision的历史排队任务只允许未修改的初始种子版本，最终发布同样校验题库状态。受影响的题库/任务/网页独立验收组合**35项通过**，包含有/无bank_revision两种反例；最终静态检查通过。该补充检查与前述794项完整运行分开记录，不虚报一次795项全量运行。
7. 最终`make check-web`：TypeScript与生产构建通过；仍有Vite大chunk提示。日志`work/admin-bank-v1/check-web-final.log`。题库页嵌入原有安全预览，阻断原因显示中文解释，避免将CSRF、乐观锁等实现细节放进操作流程。
8. `UV_CACHE_DIR=.cache/uv uv lock --offline`成功同步export组Playwright依赖及uv.lock。Docker运行阶段补Chromium及明确可执行路径；保持上批Git HTTP/1.1构建兼容修改，没有回退到未锁定依赖或取消TLS校验。

## 独立发现及修复

损坏图片题曾在调整成功写入后因detail预览抛异常，造成HTTP失败但状态已保存。修复为分区明确的预览失效占位和preview_errors，保留来源阻断原因；独立损坏媒体反例复验通过。多对象发布若第二项CAS失败会回滚前一项变更；旧租约不可切换题库指针。首次空库导入与教师API配置判断也已接通，不能仅以原始环境路径是否为空判断新库不可用。

## 未验证和清理

- 浏览器流程未完成：Playwright期望的内置Chromium二进制缺失，系统Chrome及已有headless_shell启动SIGABRT/TargetClosedError。未安装系统软件、未使用已有浏览器profile，没有截图。日志`work/admin-bank-v1/frontend-agent/logs/browser-step1.log`。不能用前端构建代替真实视觉/窄屏验收。
- 浏览器尝试使用隔离合成管理员/临时SQLite，没有真实LLM调用。临时18742 API进程由集成负责人核实PID与启动参数后通过受控权限SIGTERM清理，并确认已退出；没有停止用户原有预览或部署实例。
- 真实DeepSeek新Word识别/评级质量与实际费用、真实Chromium整库渲染、完整ARM64镜像构建、ROCK 5B升级/网络/手机操作、备份恢复和教师教学价值本批未验收。
- 前序SSH执行被托管策略拒绝仍是远程执行限制，没有改用替代工具绕过，也没有宣称服务器已更新。

题干、答案、公式或图形的内容调整通过重新上传Word形成新来源和AI版本，尚未提供网页原生数学富文本编辑。上传时全库重评的时间与费用属于首版边界，已明确展示。

## 提交前复核（2026-11-14）

发布检查发现真实包验收测试原先无条件依赖被忽略的本地`runtime/bank-portable`，全新源码检出与CI会因缺少私有资料失败。已补充显式skip条件：仅当私有manifest不存在时跳过这一个真实包用例，有资料时仍完整执行。把该测试文件复制到无私有资料的隔离目录，实际pytest结果为1 skipped；日志`work/admin-bank-v1/fresh-checkout-private-bank.log`。这一跳过不代表真实来源校验通过。

待提交32个文件已检查：没有私有题库/运行目录、预设密码、私钥或疑似真实API key；`uv lock --offline --check`通过，前端typecheck及生产构建通过。远端main与本地提交基线一致。发布前`make check`最终结果为**795 passed，1条已有弃用警告，166.41秒**，包含完整真实200题包用例，日志`work/admin-bank-v1/pre-push-check.log`。此结果覆盖此前补充的旧任务兼容反例；Git推送和远端CI结果分开报告。
