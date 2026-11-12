# Web 教师闭环 V1 独立验收

本批授权的本地教师 Web 闭环工程验收通过，保留下述生产部署、模型质量和教师价值边界。依据 `web-loop-v1-contract.md`，独立验收者为实际 `gpt-6.1-sol / high`，实施者为实际 `gpt-6-luna / high`。本轮独立检查未调用真实模型、未修改业务实现、未提交原始材料或学生信息；Root 单独执行的真实报告调用另列。

## 独立反例与已执行证据

先新增 `backend/tests/test_web_acceptance.py`，再对实际 Store、Worker 和 FastAPI HTTP 路由执行。SQLite 为临时真实数据库；教师、管理员、学生、PNG 和失败产物均为显式合成数据，不证明真实识别、教师价值或生产部署能力。

- `.venv/bin/pytest backend/tests/test_web_acceptance.py -q`：首轮 Store 等 8 项通过；扩展 HTTP 后 18 项通过；补充排队旧资源版本后 19 项通过。
- 提示词长度一致性反例先实际复现 **19 passed / 1 failed**：管理员保存 20,001 字符成功，任务构建器却拒绝。统一上限后该项已通过。
- 增加真实 built-index 的 SPA 路由反例先为 **20 passed / 1 failed**：`/private/not-a-public-file` 返回前端 shell 200，应按合同返回 404。未读取到私有文件内容；是私有路径被 SPA 吞掉的路由错误。补充私有路由前缀拒绝后已通过。
- 新增下载进度门禁后，手造已完成练习夹具补齐真实 `progress_revision` 绑定，保持原有断言；最新完整独立反例运行：**21 passed，4.56 秒**；`.venv/bin/ruff check backend/tests/test_web_acceptance.py` 通过，之前 `ruff format --check` 亦通过。当前依赖产生一条 Starlette TestClient/httpx 弃用提示，无失败。
- `.venv/bin/python work/web-loop-v1/review_protection.py`：既有原件、题库、语义侧附、旧交付及上一批考试分析产物共 **14,165 个受保护文件，0 改变、0 缺失**。既有基线来自上一批私有清单；本批另冻结上一批最终考试运行文件。此计数为保护清单分母，不是题目或用户数量。

反例实际覆盖：服务端登出撤销复制的会话；owner 与 revision 双重限制；幂等键跨老师隔离及异输入拒绝；真实数据库强制租约到期后旧 worker 不能续租或发布；有限恢复次数；资源 CAS 拒绝时无半发布；进度撤回发生在执行返回后仍拒绝发布；转换异常留有暂存文件也不发布成功；历史提示词回滚追加版本且任务内容冻结；私有路径及软链接越界拒绝。

HTTP 反例覆盖：登录状态与 CSRF；管理员也无其他老师的学生权限；进度前置闭包、白名单和乐观锁；显式 null 不破坏进度；真实 multipart PNG 上传与伪图拒绝、完整重排、处理中锁定材料；Codex 未显式启用拒绝且不入队；任务输出隐藏 input_data/lease_token；失败重试创建新任务并保留旧冻结内容；教师不可编辑提示词或提升角色；下载不以反馈为前提；负节省时间可保存且刷新后可读、布尔评分拒绝；练习/反馈/下载跨 owner 返回 404；原图 page_id 只能从已持久化 intake 白名单读取，跨 owner、未知 page 和字节漂移拒绝。

原图授权反例调用实际 `prepare_exam_intake` 构建两页合成材料；分析 JSON 仅为空对象访问控制夹具，不是识别结果。转换异常、进度竞态用例替换执行器来确定性触发边界；不声称这些用例实际编译了试卷。

## 真实来源与模型边界

Root 在用户已选择的 Codex 开发通路新增真实 report 调用 1 次，未重跑原卷识别。独立读取 `real-report-call-v1`，用当前上一批最终分析文件原始字节重算 SHA，并核验模型原始 `raw_json_result` 经 `ExamModelResponse` 校验后与 `teacher-report.json` 相等；4 个优势、2 个优先项，全部题号及考点属于该分析与 93 点目录，两个优先项均标为 hypothesis。证据在私有 `review-real-report-audit.json`。这证明该报告阶段确有一次真实响应及来源/结构绑定，不等于报告观点经过教师校准。

Root 的真实题库 HTTP 演示第 3 轮使用合成学生及已学进度，选取真实题库 10 题，模型调用为 0；前两轮 LaTeX 不支持该选题时明确失败且没有公开文件，第 3 轮显式选择 Word 引擎才成功，不是自动回退。独立从原始 ZIP 重算检查学生 24、教师 34 个原生 OLE，以及 58 个绑定预览：字节、顺序、relationship、ShapeID/ObjectID 均通过；学生无答案标签，教师仅答案解析，当前选题没有普通图或表格。

独立用 PDFium 渲染并查看第 3 轮实际下载 PDF 的全部 4 页（学生 1、教师 3），10 题与答案可见，无明显丢字、丢公式或页面越界。该轮输出的 `is_synthetic_student=false` 是应用默认值错误，并非真实学生证据；Root 已修传播标记并计划新目录重导，旧第 3 轮保留为中间证据。Word 原生公式混排的既有基线和字体差异不冒称已达到 LaTeX 统一排版。

最终实际产物改以 `work/web-loop-v1/http-real-practice-5` 为准。独立读取持久化 practice 和 snapshot，确认 10 题、`is_synthetic_student=true`，学生 PDF 页首可见“合成学生进度演示 · 非真实学生”；第 3 轮错误标志不修改历史。第 5 轮的四个下载文件逐字节 SHA 与 HTTP 记录相等，原生对象仍为学生 24 / 教师 34，58 个 OLE 及预览按出现顺序与原始 DOCX ZIP 相符。51 个非空来源文字块（学生 14、教师 37）在目标相应正文中逐块保留；核对只剥原题号、标签和空白，不把字符/数值/数学符号归一化。

实际运行独立私有 `review_native_exports.py`，输入第 5 轮 word 目录与从 SQLite 冻结的 `review-practice-snapshot-v5.json`，结果为 `review-native-result.json`；另有 `review-text-v5.json`。两份 DOCX 分别使用已解析到的 bundled Python 与 documents 技能 `render_docx.py --emit_pdf` 渲染，输出 `review-canonical-v5-{student,teacher}`。转换只使用 bundled LibreOffice，未使用桌面 Word/LibreOffice。

独立 PDFium 1.5 倍重渲染下载 PDF 与 canonical PDF：学生各 1 页、教师各 3 页，全部 **4 页 RGB 逐像素相等**；随后分别打开两套全部 **8 张页图**，确认题号 1–10、答案与中文/公式可读，无页面裁切或重叠。私有证据 `review-pdf-v5/audit.json`。当前题单无普通图/表格，不将本次零计数当成新表格或几何图验收覆盖。

## 审查发现及收口条件

已反馈并在独立反例中复验的修改包括：进度发布事务 guard、任务冻结 resource_revision、student 显式 null 拒绝、反馈刷新读取、原图受权限保护的 page 映射。曾发现前端创建考试缺少必需 question_count，现已修复，Root 完整浏览器流程实际填写并创建成功。

收尾另独立阅读 `web_jobs.restore_manual_corrections`：按老师历史顺序逐条调用原有修正校验，每步只更新当前基版本，保留每条证据和修正 ID；不再把同题的多次编辑当成同一批冲突。`_analysis` 对重新识别结果调用该 helper，随后写新的派生分析及重新生成报告。独立执行 `.venv/bin/pytest backend/tests/test_web_jobs.py -q`：**6 passed，0.88 秒**；新增反例实际验证同题两次转录编辑最终保留第二次、两个修正 ID 均保留、原分析对象未被修改。这是纯合成历史重放验证，没有新模型调用；既有前端候选不变。

最终代码独立 21 项及旧产物保护已再次执行，14,165 文件仍 0 改变、0 缺失。独立复核 Root 执行日志：完整套件首次为 567 passed / 31.90 秒；历史重放收尾修复后，`work/web-loop-v1/make-check-final.log` 最终 Ruff 通过、**568 passed，33.18 秒，0 skipped**。`frontend-check-final.log` 中 `pnpm --dir frontend typecheck` 与 `build` 均成功。完整套件由 Root 执行，未冒充独立者再次运行全部测试。

独立检查 Root 首轮截图时发现预览为空白，进一步亲自启动本机 Chromium，以真实已构建 React 页面、合成测试账号和现成已完成的浏览器练习复现；它与 HTTP 第 5 轮选题不同。首轮仅拦截创建请求返回现成 ID，未生成新任务或模型调用。等待绘制后，20 个题干/答案 iframe 均有文字，66 次图片出现均加载成功、0 pageerror，因此不能把首轮空白截图直接归因为内容被删。实际截图确认公式 PNG 丢失源显示尺寸、按原像素放大且被固定高 iframe 截断；另发现缺少刷新后恢复历史练习入口。均已修复，保留 `review-browser-preview.json` 与 `review-preview-probe-crop.png` 为修改前证据。

最终独立浏览器检查直接读取已有练习历史，不拦截创建、不发新任务。真实登录后选择当前合成学生，确认四个下载 URL；刷新、再次打开该学生，四个 URL 逐项一致，反馈 `saved_minutes=-1` 也恢复。逐 iframe 核对 **20 个非空 frame、66 次图片出现、0 损坏、0 超出有效声明宽度、0 pageerror**。亲自打开首题及展开答案截图，公式数字恢复为正文附近的大小；长答案为可滚动窗口，实际滚动至底部 220/220px，未把窗口初始高度当成内容丢失。证据 `review-browser-preview-final.json`、`review-preview-first-final.png`、`review-preview-answer-final.png`；记录实际 JS/CSS 文件摘要，JS 为 `index-CUoCXfoA.js`。

Root `browser/e2e-result.json` 完整模拟流程包括：真实 API+数据库+独立 worker，经显式合成 CLI fixture 返回识别/报告，页面关联、授权原图及 bbox、局部转录修正、10 题真实题库组卷、四文件下载、负节省时间反馈和刷新恢复均成功；390px 上传无横向溢出，0 pageerror。唯一 HTTP 409 是新考试尚无分析的预期空态，非任务成功误报。独立查看最终手机上传和原图覆盖截图，布局及 bbox 可见。该流程模型调用数为 0，不是新的真实卷面识别测试。Root 的 `admin-result.json` 另记录新增教师、report v1→v2→回滚 v3，0 pageerror；Store 的历史不可变/CAS 由独立反例实际验证。

## 结论边界

FR14 本批只读预览，未交付替换、删除、重排编辑。当前独立证据包含本地持久化与权限/发布正确性、已有真实题库的 HTTP 导出，以及单次真实报告结构绑定。真实学情报告质量、教师节省时间、生产 PostgreSQL/Redis、多进程部署和外网手机 HTTPS 不由这些合成用例证明。既有题库的来源/关联/语义状态不因 Web 接入改为生产 ready，不增加题库人工审核。
