# 产品功能与交互检查（2026-11-14）

## 范围与证据边界

依据 PRD、`docs/mobile-optimization.md` 第 0 节已确认方向，以及 `docs/ui-guidelines.md` 第 2、6、8 节。检查教师闭环、任务恢复、跨学生状态隔离、管理员编辑目标及 HTTP 部署兼容。

审查时 HEAD 为 `74b16e3`，另一个会话持续修改移动端前端和产品文档。本批未修改这些文件：使用忽略目录的代码快照复现，并对最新源码复核相关逻辑。移动改版中的临时布局、未完成底部导航样式和建议条目不作为缺陷。本报告的源码行号可能随并行修改移动，以函数名和触发路径为准。

本机 Playwright 使用官方 Google Chrome（`channel="chrome"`）、全新隔离上下文、合成 API 响应；不复用账号、浏览器资料或真实学生材料，不调用模型，不检查远程部署。下列是状态和接口行为验证，不是手机真机、识别质量、导出质量或教师价值验收。

后续修复与复验见 [产品交互缺陷修复](product-review-fixes-v1.md)；下列保留审查时的原始发现，不代表修复后仍存在。

## 发现

### R-01／P1：HTTP 局域网部署无法提交识别修正，新增上传队列也使用同一不兼容 API

- 位置：`frontend/src/App.tsx`，`submitCorrection` 生成 correction ID；最新 `uploadFiles` 生成上传队列 key。
- 条件：通过普通 `http://<局域网IP>:18081` 打开，非 localhost 且未配置浏览器特殊可信来源。
- 源码直接调用 `crypto.randomUUID()`。Chrome 非安全上下文实测 `window.isSecureContext=false`、`typeof crypto.randomUUID="undefined"`。修正调用在 try 内会报“提交修正失败”；最新上传队列生成在上传 catch 外，会在发请求前拒绝，不能上传。
- 影响：当前推荐的局域网 HTTP 使用路径不能完成上传/修正。localhost 开发或 HTTPS 测试会掩盖该问题。
- 修复方向：统一兼容 ID 生成器（项目管理员计费页已有兼容处理），或者把 HTTPS 作为部署前提完整实现；不能只给按钮换提示。复验必须覆盖真实 HTTP 非 localhost 来源。
- 证据：`work/product-review-20261114/browser-results.json` 的 `insecure_http_crypto`；上传调用来自复现后的最新源码复核，并非已在板子试传。

### R-02／P1：组卷期间换学生，旧卷会显示在新学生页面

- 位置：`frontend/src/App.tsx`，`PracticeView.createPractice` 中 `pollJob` 完成后无条件 `onPracticeChange(result)` 和写入 history。
- 步骤：学生 A 选参考考试及考点，开始组卷；未完成时切到 B；A 的任务完成。
- Chrome 复现：选择器为 B，但显示“合成学生A专属试卷”下载链接。后台记录仍属于 A，是前端展示/上下文污染，未证明后台改写归属或跨账号越权。
- 影响：可能把 A 的练习下载发给 B；此时反馈也会提交给界面实际持有的 A 练习 ID，老师容易评价错卷。
- 修复方向：请求绑定 student/job ID，切换时中止前端轮询或丢弃旧响应；任务可在后台继续，不因切页取消后端业务任务。history、feedback 与 result 一起隔离。
- 证据：`browser-results.json` 的 `practice_cross_student`，`practice-student-mismatch.png`。

### R-03／P1：离开分析页后旧任务可覆盖另一学生考试列表

- 位置：`frontend/src/App.tsx`，`ExamView` 选择考试 effect 中连续两个 return；`updateJob` 完成时 `onRefresh(selectedStudentId)`；App 的 `refreshExams` 无条件 `setExams(normalized)`。
- 第一个 return 仅执行 `active=false`，第二个 abort cleanup 不可达，组件卸载未停止旧轮询。
- 步骤：A 开始分析 → 切工作台 → 切 B → A 任务完成。
- Chrome 复现：当前学生选择器仍为 B，考试表格却显示“合成考试A”。这是账号内跨学生视图污染，不是跨账号越权。
- 影响：老师可能在 B 的上下文里查看、上传或处理 A 的考试；界面显示学生与操作实际考试不一致。
- 修复方向：合并 cleanup、卸载时取消前端轮询，并在全局列表发布前检查响应 studentId 是否仍为当前学生；仅修 cleanup 仍不能防住其它慢请求。
- 证据：`browser-results.json` 的 `exam_cross_student`，`exam-student-mismatch.png`。

### R-04／P2：刷新或重新进入已有处理中考试，不恢复进度查询

- 位置：`frontend/src/App.tsx`，`ExamView` 选择考试 effect，仅 `setActiveJob` 并 GET analysis；只有主动分析/重试/修正才启动 `updateJob`。
- 步骤：已有 queued/running 任务时刷新，或从另一设备进入该考试；等待任务后台完成。
- Chrome 复现：页面显示“处理中”，等待超过两轮预期轮询间隔，发往 `/jobs/` 的请求为 0，“开始分析”仍可点击。没有自动刷新终态或结果。
- 影响：与“稍后回来查看”的后台任务交互不符，用户会以为卡住；本次未证明一定重复计费，不能把可点击等同于一定重复调用模型。
- 修复方向：载入已有 queued/running job 时恢复带取消机制的轮询，终态后重新读取分析；处理期间主按钮显示任务状态。
- 证据：`browser-results.json` 的 `existing_running_exam`，`running-exam.png`。

### R-05／P2：题库详情响应乱序，会把选中题和编辑表单跳回旧题

- 位置：`frontend/src/AdminBank.tsx`，`loadDetail` await 后 `setSelectedId(id); setDetail(next); setDraft(...)`；无请求序号或取消检查。
- 步骤：点题 A，A 请求很慢；再点 B，B 先返回；A 随后返回。
- 真实 AdminBank 组件 + 合成延迟响应复现：最后用户点击后详情为“B · 合成题B”，旧响应到达后自动变为“A · 合成题A”。没有保存任何调整。
- 影响：编辑目标跳回，已输入内容可能被重置；管理员若未注意可能按修改 B 的意图调整 A。没有证明后端把 B ID 的请求写到 A。
- 修复方向：选择仅由用户事件改变，详情返回时检查当前 questionId/request token；旧响应不得更新 selectedId/detail/draft。
- 证据：`work/product-review-20261114/admin-results.json` 和 `admin-detail-race.png`。

## 检查结果及未测项

- 两个本机浏览器脚本最终运行退出 0，复现上述跨学生状态、已有任务不恢复和详情乱序；HTTP 不安全上下文 API 可用性实测。上传队列的最新调用另做源码复核。
- 独立只读 Agent（实际 `gpt-6-luna / high`）复核了任务、权限、详情和业务闭环；没有确认额外跨账号越权。该结论仅限已检查路径，不是安全审计通过。
- 反馈持久化与私有文件路径既有专项：`backend/tests/test_web.py::test_practice_feedback_is_reloaded_and_private_file_paths_are_never_listed`，1 passed，1 项依赖弃用警告。历史练习排序/选择与其反馈恢复未发现已证实缺陷，不列猜测问题。
- UI 仍被另一会话持续改动，本次没有把全量测试通过、移动 CSS 完成、iPhone/Android/微信内置浏览器通过写为结论，也没有提交或部署。
- 复现脚本、前端快照、JSON 和截图均位于忽略的 `work/product-review-20261114/`，全部为合成数据；未将其当作真实教学结果。

建议先处理 R-01～R-03，再处理 R-04～R-05，最后等移动改版稳定后做 390px、iPhone/Android 和返回键/上传/PDF 下载专项验收。
