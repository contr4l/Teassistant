# 本地部署试跑独立验收

依据 `local-deployment-v1-contract.md`，独立检查实际 `gpt-6.1-sol/high`，打包实施实际 `gpt-6-luna/high`。**本机应用＋独立 PostgreSQL 容器的本地检查实例通过验收，未发现该范围剩余阻塞；完整 Docker 应用镜像仍未构建成功。**本轮独立检查不修改 Docker 资源、不改业务实现、不调用模型、不重复全量旧业务单测；只新增独立打包测试、本报告及私有只读核验结果。

## 部署范围与静态兼容性

完整生产镜像在公开基础镜像拉取阶段再次超时，不能把此轮交付说成完整 Docker 部署。Root 采用本机应用与已有官方 PostgreSQL 17.2 arm64 镜像的独立容器，提供新的 18080 实例；生产 Compose 的 postgres:17-bookworm 配置不因此修改。旧 18072 SQLite 预览与数据须保留。新入口使用 localhost:18080，旧入口保持 127.0.0.1:18072，避免相同 Cookie 名在同主机不同端口互相覆盖。

只读检查 Dockerfile/Compose/entrypoint/deploy.py 及新账本：生产依赖含 psycopg，镜像含 tzdata 供上海时区统计；init 调用 Store.init 创建账本与默认设置表，API/Worker 共享数据库和私有卷。创建管理员与教师均经同一 Store.create_user 事务初始化一次赠额，缺失旧钱包的读取仍为零；模型密钥文件不烘焙，生产路由 DeepSeek、Codex 关闭。此处为静态兼容性结论，不等同于实际镜像运行。

先前原始 semantic 副本的 `provenance.source_run_path` 为宿主绝对路径，直接复制 source/semantic 到新根会产生 semantic_source_run_unavailable；不能用“200 行可读取”冒充 176 候选可用。本批新增部署副本及完整引用证据打包解决该问题，原正式库不能就地改写。

## 独立打包反例

新 `backend/tests/test_bank_package_acceptance.py` 复用显式纯合成的一题来源夹具，使用真正 package CLI 与 production loader，不 mock 验真门禁。正例检查原 source/curriculum 字节、annotations 仅改 run 路径、三份 call 及 run.json 原哈希、manifest 覆盖；随后隐藏原始夹具并再次移动包，loader 仍须得到同一 eligible 题，证明包不依赖旧绝对位置。未引用的合成 .env 不能随证据目录整树复制。

负例覆盖相对 ..、空路径、反斜杠、越界 symlink、call 缺失/篡改、原媒体漂移、源目录附加越界 symlink、目标已有或在输入内；即使攻击者更新包 manifest 自报哈希，loader 仍须按 annotations 中原 call SHA 拒绝被改的结果。

初版 11 项通过；源码复核后增加两个明确边界，实际运行 `.venv/bin/pytest backend/tests/test_bank_package_acceptance.py -q` 得 **10 passed、2 failed**：assessed 行删除 provenance 后被发布为同样全 blocked 的包；输出位于被引用 source-run 目录内仍被接受并写入原证据树。两项已通知实现者修复，不把原/新可用数同为零当作证据完整通过。

修复后独立执行 `.venv/bin/pytest backend/tests/test_bank_package_acceptance.py -q -k 'missing-provenance or output_cannot'`：**2 passed、10 deselected、1.63s**；独立文件 Ruff/format 均通过。未降低断言、删除负例或修改实现。原十个通过结果与两项修复后的通过分别记录，不虚构独立单次全部重跑。

## 真实实例与包的独立只读核对

在 Root 宣布 ready 后，使用私有登录文件在内存中取凭据，实际登录 http://localhost:18080，不输出密码、密钥、会话 Cookie 或 DSN。读取首页与 API：新站及旧 http://127.0.0.1:18072 首页均 200；新站管理员登录 200；新用户默认设置 CNY 10.000000/revision1；管理员钱包总额和可用均 10.000000，已用/预占为 0。私有凭据文件 mode0600；生效本机配置后端 PostgreSQL、回环 18543、DeepSeek Flash、无密钥、Codex 关闭。只进行明确授权的登录和 GET，没有创建账号或改设置。

独立执行 Root 指定的只读 `docker compose ... ps --format json`，确认 teassistant-local-native 的 postgres:17.2 容器 running/healthy，端口仅 127.0.0.1:18543→5432；没有启动、停止、exec 或重建容器。本机配置与隔离实例的实际登录成功共同佐证当前 PostgreSQL 通路；这不等于生产应用镜像通过。

对 `runtime/bank-portable/manifest.json`，独立重新计算 **4504 文件全部 SHA**，零差异；其中 **4338 个 source 文件逐字等于原 question-bank-v3**，curriculum 原字节相同、原 annotations 摘要相同。进一步独立逐 200 行检查 annotations 除 `provenance.source_run_path` 外完全不变；每行 request/prompt/result 仍匹配原摘要、两份 run.json 与原文件逐字相同。最后真正运行 loader 重读原库与包，均为 **200/176 eligible/24 blocked**，逐 ID status/block_reasons 完全一致，不依赖 manifest 自报。无模型调用，原件未写入。

私有核验记录为 `work/local-deployment-v1/review-readonly.json`、`review-real-bank.json`，不含凭据。原生数学/媒体完整性仍经原有 loader 执行，未移除来源或逐 call 校验来获得 176 候选。

## Worker、实际下载与页面核验

直接复核 Root 的 `native-e2e-result.json`：实际 PostgreSQL 与独立 Worker 分别完成 LaTeX、Word 两题频率概率组卷，两任务状态 completed，各有学生/教师 DOCX/PDF 四文件；合成学生与反馈已明确标记，不能充当教师价值。Root 的 DOCX 原生结构记录为学生 7、教师 4 个 MathType 嵌入，不是 OMML；四份实际 PDF 均为一页。

独立亲看 `downloaded-latex-v3`、`downloaded-word-v3` 中全部四份实际 PDF 页图：中文、两题、3×8 频率表及对应答案/解析完整可读，没有发现截断；LaTeX 与 Word 是分别选择的引擎，不是转换失败后自动回退。本次只证明这两道真实题在本机工具链能导出，未重新判定全部 176 候选都支持 LaTeX。

本轮实际发现下载缺陷：API 使用 student_pdf 等不带扩展名的 route key 作下载名，且 MIME 为 octet-stream；正文原本完整。Root 修复后，独立运行 `.venv/bin/pytest backend/tests/test_download_metadata.py -q`，**4 passed、1 已有 warning、1.04s**。随后实际 GET 两套既有 practice 的共八个下载接口，均得到受控 .pdf/.docx 文件名及正确 MIME，PDF 为 %PDF-、DOCX 是包含 word/document.xml 的 ZIP；Word 套的服务器响应与 Root 保存副本逐字相同。私有证据 `review-download-headers.json`、`review-word-download-headers.json`，没有新建组卷/反馈或改学生设置。

Root 首轮 checker 把既有 completed 误断言为 succeeded，属脚本问题；第二轮按无后缀 URL 推断文件类型又暴露上述真实下载缺陷，不能把它统称脚本错误，也不能误称 PDF/ZIP 内容损坏。失败日志保留，最终 v3 复用已有练习通过。

直接复核 `browser-result.json`，并亲看实际部署管理员桌面/手机截图：登录、默认10、DeepSeek 页面配置可读，无 JS 错误或页面横向溢出。浏览器流程由 Root 执行，本验收者复核已有结果与截图，不重复创建数据。

## 重启、最终检查与限制

直接复核 `persistence-result.json`：Root 在空闲时停止本机 API/Worker，重启独立 PostgreSQL 容器后再启动服务，业务记录与钱包 total/used/reserved 不变、已发布四文件 SHA 不变；此后 Word 任务与缺密钥失败检查继续完成，没有重复赠额。独立未执行服务生命周期操作。

缺 DeepSeek 密钥的合成卷面任务在实际 Worker 明确 failed，没有制造识别结果；Root 最终记录模型调用 0，钱包仍总10、已用0、预占0。直接核读最终 `make-check-download-fix.log`：Root 全量 **735 passed、1 已有 warning、45.48s**；此前731是下载修复前记录，不能混作最终数。最终前端 typecheck/build 通过，JS 为 index-i4TZgsPf.js。以上全量由 Root 执行，本验收者没有冒充独立重复运行。

当前入口为 http://localhost:18080，密码仅在私有 admin-login.txt；旧 http://127.0.0.1:18072 首页仍200。完整镜像、生产Compose指定的 postgres:17-bookworm、容器内应用与导出、远程发布、外网 HTTPS、真实 DeepSeek 识别/账单及教师价值仍未验收。当前使用缓存官方 postgres:17.2 arm64 的明确本地替代，不把它写成完整 Docker 或生产上线通过。

上述默认值、余额、行数与文件摘要均为验收取证时快照。用户已开始使用检查实例，后续合法变更不构成验收回归；独立检查已停止，不再假定当前数据库固定、不恢复设置或改动服务。
