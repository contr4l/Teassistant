# 使用方式入口修复执行记录

日期：2026-11-12。用户反馈“了解使用方式”无法交互；关联既有UC-02～09。[合同](usage-guide-v1-contract.md)先行，未新增业务API或模型调用。

根因是App.tsx侧栏帮助卡使用静态span，没有点击事件。Root将其改为可键盘激活的button，接入弹窗开关、三个现有页面导航以及桌面/手机关闭后焦点返回；侧栏增加内部滚动，短屏也能到达帮助入口。实际gpt-6-luna/high负责新UsageGuide.tsx与usage-guide.css，提供四步流程与三处快捷入口；Root集成并补原生close事件清理、Tab/ShiftTab循环和背景滚动锁。实际gpt-6.1-sol/high独立验收，见 [review](usage-guide-v1-review.md)。

说明覆盖学生档案和已学范围、原题与已批改作答/可选参考答案上传、原图对照与局部修正、按考点组卷/题答分开导出/反馈。模型配置及额度问题引导联系管理员，没有向教师开放管理操作或宣称尚未实现的题目替换。三个跳转只切换已有页面，不提交资料、调用模型或改动已选学生。

## 实际检查

- `make check-web`：TypeScript检查、Vite生产构建通过，最终bundle `index-BfErvHMW.js`；日志work/usage-guide-v1/frontend-check.log。`git diff --check`通过。
- 实际localhost:18080测试教师浏览器验证：点击和Enter打开、正反Tab各12次保持焦点在弹窗、关闭按钮/Escape/遮罩关闭、三处导航对应实际页面H1、桌面焦点回帮助按钮，手机焦点回打开导航按钮。
- 桌面1440×1000，手机390×844和短屏320×568；验证菜单可到达帮助入口、弹窗关闭侧栏、弹窗正文可内部滚动、关闭按钮和快捷按钮可见，没有整体横向溢出或JS错误。
- 记录登录后所有API写请求：**0业务写请求**，不修改用户学生、额度、任务或调用模型。复用私有教师会话，不将密码写入源码或验收报告。

首次browser-v1实际暴露原生dialog的Tab末尾可离开弹窗，故补显式首尾循环后复验。browser-v2全部通过；再补短屏正文真实scrollTop变化断言；browser-v3在手机关闭后的即时焦点断言早于requestAnimationFrame而失败，检查脚本改为等待焦点回到菜单按钮（不改业务代码），最终browser-v4通过，结果位于work/usage-guide-v1/browser-v4/result.json。Root已亲看桌面及手机截图，保留首轮失败日志，不冒称只做静态检查即证明交互通过。

前端静态文件已在现有实例更新，用户刷新页面即可加载；没有重启正在使用的API、Worker或PostgreSQL。只改前端与说明，本批不重跑后端全量、真实LLM、导出或Docker构建；此前735项不作为本次新测试数字。修复不依赖新增库，也不改变题库审核或业务权限。
