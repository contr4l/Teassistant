# 本地部署试跑执行记录

日期：2026-11-12。用户要求本地尝试部署供检查。对应DEV-101/105/106/502，[合同](local-deployment-v1-contract.md)先行；Root部署与集成，实际gpt-6-luna/high负责可迁移题库打包，实际gpt-6.1-sol/high独立检查。没有远程发布或改动其他项目服务。

## 当前可访问实例

访问 **http://localhost:18080**，管理员账号`admin-local`，随机密码保存在私有`work/local-deployment-v1/admin-login.txt`（0600），不写入日志或源码。使用localhost区分原预览127.0.0.1:18072的会话Cookie。原预览首页仍200，原用户、卷面及数据保持。

实际部署为**本机API/持久Worker + 独立PostgreSQL容器**。Docker运行于已有Colima arm64，Compose 2.32.4；复用本机已缓存的官方`postgres:17.2` arm64镜像，独立项目`teassistant-local-native`、专属卷，仅绑定127.0.0.1:18543。应用使用根目录uv环境和生产配置入口检查，DeepSeek唯一业务通路，Codex关闭；私有目录为runtime/local-native/private，题库为runtime/bank-portable，只读来源由程序验真。

**完整Docker应用镜像尚未构建成功**：首次命令因Buildx写用户缓存的沙箱权限失败，按请求授权后再次实际构建，在Python基础镜像registry-1.docker.io:443 TCP超时；单独拉取固定Python、uv GHCR及Compose指定postgres:17-bookworm均超时。没有换代理、改主机网络、固定registry IP或假称旧上下文镜像是生产镜像。未改生产Compose指定镜像；本地17.2仅作为明确替代提供检查实例。私有日志`build-attempt-1/2.log`、`pull-python/uv/postgres.log`保存失败。注册表可用后仍需实建应用镜像及容器内导出；本次原生应用导出不能代替容器验收。

`.env.deploy`已生成独立项目teassistant-local、端口18080和私有数据库密码，通过配置及Compose语法检查；完整Docker与当前本机应用的数据库卷不同，不能把后续切换当成自动迁移。当前实例启停方法见私有`work/local-deployment-v1/LOCAL-ACCESS.md`。

## 实际发现与修复

1. 语义annotations引用开发机绝对run路径。仅复制source/semantic虽读取200行，但不能证明可组卷；补充严格相对证据路径解析与`package_question_bank.py`。新包只改副本run路径，保留来源/目录/run/call字节与原SHA门禁，输出manifest，原/新逐题状态及原因一致才发布。
2. 独立反例发现初版允许assessed缺provenance后以原新同blocked发布、输出写入被引用原run目录；已修复并独立复验。合法24道blocked题仍保留，不为部署放宽题目门槛。
3. 实际下载使用opaque route（student_pdf等），API忽略既有filename字段、用无扩展名路由作为下载名且默认octet-stream。已改用受控filename/实际文件后缀，返回正确PDF/DOCX MIME；新增4项测试，实际四文件下载header及正文独立确认。正文原本完整，不能称ZIP或PDF损坏。

新增打包与下载相关**26项**，既有709项。本轮最终`make check`：**735 passed，45.48s**，ruff通过，1条既有Starlette/httpx弃用warning；日志`work/local-deployment-v1/make-check-download-fix.log`。修复前731项也通过，不混作修复后的最终数字。`make check-web`类型检查/构建通过，最终浏览器桌面/手机无JS错误及页面横向溢出。

## 实际题库与后台任务

- 真实200题打包：原/新 **200题、176eligible、24blocked**，2个run证据目录，逐ID状态及原因一致；source/curriculum原字节保留，原件不修改，不运行模型。manifest位于runtime/bank-portable/manifest.json，Root日志package-attempt-1.log。
- 独立检查者核对包4504个文件的全部SHA、4338个来源文件原字节、目录和逐call证据，重新加载原/包验证200/176/24；未用清单自报代替读取。只读证据review-real-bank.json。
- 实际PostgreSQL初始化、管理员创建、默认额度10、登录、目录及进度库存API均通过。题库进度内库存176；新站禁用Codex，无真实DeepSeek key。
- 用明确命名“部署验收合成学生（非真实学情）”及synthetic标记，设置合成已学范围，分别提交LaTeX和Word两道频率概率题任务。**独立Worker实际消费任务**，两任务均completed，各下载学生/教师DOCX/PDF四文件，共8文件。学生/教师DOCX保留7/4个MathType嵌入对象，不冒称OMML；四PDF各1页，已渲染核对中文、表格、原题及答案完整。合成反馈保存成功，不代表教师评价。
- 提交合成原题/作答图片且缺DeepSeek key的分析任务，Worker明确failed，未制造识别结果；总/已用/预占额度维持10/0/0，无模型调用。结果native-e2e-result.json。私有checker首轮将既有completed误断言为succeeded，次轮按URL缺后缀错误判断文件类型；保留native-e2e及v2日志，修正检查和实际下载缺陷后v3通过，复用同一学生和原练习而非删除失败证据。
- 空闲停止API/Worker、重启独立PG容器、再启动应用，原用户/业务记录/任务数、钱包总/已用/预占及已发布4文件SHA一致；没有重发默认赠额。结果persistence-result.json；此后继续Word任务与缺key检查通过，应用最终保持运行。

独立验收见 [review](local-deployment-v1-review.md)。私有日志、凭据、真实题库副本与合成产物均在ignored work/runtime，不入源码。用户可检查登录、学生/进度、题库、组卷、下载、反馈与管理员设置；手写识别需在管理员页面自行配置DeepSeek，真实LLM识别/对账、完整Docker、远程Actions、服务器HTTPS及教师价值仍未验收。
