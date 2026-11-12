# 教师工作台前端实现记录

日期：2026-11-12。实现范围为 `frontend/`，按 [web-loop-v1-contract](web-loop-v1-contract.md) 调用 `/api`；不改后端合同或根依赖。

## 已实现页面

- 登录与退出使用服务端会话；CSRF token 仅保存在当前页面内存，写请求带 `X-CSRF-Token`。
- 工作台展示学生、考试和考点目录的真实 API 数据。
- 学生档案支持创建、资料编辑、考点进度更新和整数进度版本并发校验。
- 考试流程支持创建（题数由老师填写）、试题/作答/参考材料多文件上传、顺序调整、任务轮询、原图证据与 bbox 对照、结构化教师报告及基于原有来源证据的局部转录修正。
- 组卷支持选择已分析考试、查询库存、默认每考点十题或按难度配额、作答/紧凑版、显式 LaTeX/Word PDF 引擎选择、预览、下载和按练习版本提交反馈。
- 管理员页面支持创建教师账号、编辑六类提示词、查看历史及把历史内容复制为新激活版本。

所有 AI/题目文字通过 React 文本节点渲染。来源题 HTML 经过前端标签/属性白名单裁剪后放入空 sandbox 的 iframe；预览图片只接受内嵌栅格图片 data URL。登录密钥不写入 localStorage；上传和原图通过授权 API 访问。

## 构建检查

环境：Node `v26.13.0`、pnpm `10.4.1`、Vite `8.3.8`；`frontend/package.json` 固定 pnpm 版本及精确依赖，`frontend/pnpm-lock.yaml` 锁定解析结果。Vite 开发代理指向 `http://localhost:8000/api`，构建产物位于 `frontend/dist`。

实际运行：

```text
cd frontend
pnpm build
pnpm typecheck
```

两项命令在 Node `v26.13.0`、pnpm `10.4.1` 上退出码均为 0。`build` 包含 TypeScript project build 与 Vite production build；产物由 Vite 输出到 `frontend/dist`。

## 尚未由本实现记录证明

这份记录不代表浏览器完整点击验收。HTTP 权限隔离、worker 持久性、真实浏览器上传/轮询/下载、手机 390px 操作、部署使用 Node 24，以及真实教学价值须依据集成负责人和独立验收者的运行记录确认。数学公式目前以安全纯文本呈现，尚未接入 KaTeX。
