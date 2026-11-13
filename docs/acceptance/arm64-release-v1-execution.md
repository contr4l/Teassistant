# GitHub ARM64 发布配置集成证据

日期：2026-11-14。范围以 [发布合同](arm64-release-v1-contract.md) 为准。

## 改动与验收口径

GitHub Actions 新增原生 AMD64、ARM64 构建及分架构缓存。在每种架构实际容器冒烟通过后，受信发布任务推送运行唯一的暂存镜像，最终组合完整提交 SHA 与 release 标签。两份索引推送前后均检查两种 Linux 架构及平台摘要一致性。新增 Chromium 合成页面渲染检查，作为已有 API、worker、Word 与 LaTeX 冒烟之外的镜像依赖检查。

部署侧沿用现有 `scripts/deploy.py pull` 和 `scripts/deploy.py up`。`up` 默认包含 `--no-build`，只在用户显式传入 `--build` 时构建。使用同一目录、配置、项目名和挂载升级；本批没有操作板子、修改账号或迁移生产数据。

协作中实际实施及独立检查模型为 GPT-6 Luna / high，未将其标作 GPT-6.1 Luna。独立检查范围及结果见 [复查记录](arm64-release-v1-review.md)。

## 本地已执行

- `.venv/bin/ruff check backend scripts`、`git diff --check`：通过。
- 两个原生 runner 矩阵、全 SHA 固定 Actions 引用、只读构建权限、受信发布条件、最终发布依赖及 18 段 `run` 脚本 `bash -n`：通过。
- `.venv/bin/pytest -q backend/tests/test_image_manifest.py backend/tests/test_deploy.py backend/tests/test_container_smoke.py backend/tests/test_container_runtime.py backend/tests/test_deployment_acceptance.py`：新增 Chromium 反例前为 60 passed。
- 新增 Chromium 失败清理、缺少浏览器时拒绝生成输出两个反例后，`.venv/bin/pytest -q backend/tests/test_container_smoke.py backend/tests/test_image_manifest.py`：23 passed。
- 全量 `make check`：Ruff 通过，813 passed、1 warning，159.59 秒。warning 来自 Starlette TestClient 的上游弃用提示。运行时复用本机已有 LibreOffice/字体环境，并设置 `TMPDIR=/private/tmp`；详细日志在被忽略的 `work/arm64-release-v1/make-check.log`，不入源码。

manifest 反例包含缺 ARM、重复平台、额外 attestation、错误 OS、非法摘要、两个平台共享摘要、嵌套索引、非法大小/变体、重复 JSON 键、超长输入及错误信息泄露检查。

## 尚未执行

初次离线验收时尚未提交推送；下文记录随后用户授权的真实云端结果。GHCR 发布/下载及 ROCK 5B 健康检查仍未完成，不表示已有新的可拉取镜像。

首次发布需仓库允许相应 Actions 和 ARM64 runner，且包发布权限有效。GHCR 包可见性不自动更改；私有包下载需用户自行登录具备 `read:packages` 的凭据。正式使用以成功 workflow 的镜像引用为准，推荐固定 digest；跨标签推送不具备原子性。

容器冒烟使用合成材料及隔离网络，不调用真实 DeepSeek，也不证明学生卷面识别质量或教师价值。

## 首次真实云端构建与诊断补充

用户随后明确授权推送并等待构建。`b220ad2e0ac4f8d168616082cbbaaff17c8ecf96` 已推送 main，[Actions 43486640073](https://github.com/contr4l/Teassistant/actions/runs/43486640073) 结果为 failure：前后端检查通过，两种原生架构的 Docker 镜像均构建成功；AMD64 的容器冒烟用时 30 秒、ARM64 用时 31 秒，均失败。受信发布任务被阻止，未发布正式多架构镜像。

通过公共 GitHub API 已实际读取运行、任务及 check-run 状态。日志下载接口返回 403 `Must have admin rights to Repository`；check-run 只暴露步骤失败退出码，没有异常详情。已请求用户提供失败步骤日志，不以时长推断根因。

后续诊断改动只在脚本内部记录受控阶段、异常类型、命令退出码及白名单静态提示，并在 Actions 输出错误 annotation，便于公共 check-run 定位。不会输出命令参数、子进程原始输出、异常任意 payload 或配置内容。清理仍然执行；清理异常不能覆盖最初失败，也不会写成功报告。没有跳过冒烟或降低发布条件。

诊断改动的 Ruff 及 `test_container_smoke.py`、`test_container_runtime.py`、`test_deployment_acceptance.py` 共 43 项通过，包含不泄露异常 payload、已知提示可见及清理失败保留主错误的反例。此结果不证明真实容器失败已修复；后续仍需以重跑的实际结果为准。

GPT-6 Luna / high 独立只读复查并执行 `test_container_smoke.py`：10 passed，未发现异常 payload、凭据或命令日志泄露及绕过门禁。非阻塞限制：不符合预期的成功响应可能触发未包装的 KeyError / IndexError / AttributeError，此时仍失败关闭，但没有受控阶段 annotation。

诊断改动全量 `make check` 再次通过（816 passed、1 条同源弃用 warning）；日志保存在被忽略的 `work/arm64-release-v1/diagnostic-make-check.log`。用户提供的原始失败步骤文本也只有 `container smoke failed: RuntimeError`，不能据此判断具体根因。

## 命令级诊断

`a764d311337a21d005dfdbafe7834b50f541fcd2` 已推送，[Actions 43489029154](https://github.com/contr4l/Teassistant/actions/runs/43489029154) 的前后端再次通过，两种原生镜像再次构建成功，但容器测试均返回公开 annotation：`stage=web workflow; error=SmokeCommandError; exit=1`。用户提供的截图与公共 API 一致。失败发生在 `_api_smoke` 内某条容器命令，尚未执行 Chromium/Word/LaTeX 渲染；不能从此推断具体子命令或 ARM 兼容性根因。GPT-6 Luna / high 只读核对接口、Docker 入口、健康检查与 Store 合同，没有证据足以静态确定具体根因。

因此进一步拆分固定命令阶段：发现端口、镜像私有内容检查、API 健康检查、Worker 健康检查、创建合成管理员及写入合成练习。嵌套阶段保留最具体的错误；任意常规 Exception 也使用受控摘要，解决前述响应形状边角。命令输出尾部最多 64 KiB 仅用于识别固定异常名称白名单，公开摘要只包含这些预设类名，不含原始行、命令参数、路径或凭据。

上述细分改动的 Ruff 及相关部署检查共 48 项通过，包含未知异常 payload 脱敏、嵌套阶段保留、固定错误名称提取及任意 marker 拒绝。不把诊断增强当作容器故障已经修复。

GPT-6 Luna / high 独立执行容器冒烟测试：15 passed，复查 marker 白名单、嵌套阶段和清理门禁通过。全量 `make check` 为 821 passed（同源 warning 1 条），日志在被忽略的 `work/arm64-release-v1/command-diagnostic-make-check.log`。另用本机 SQLite 与合成账号实际执行管理员 CLI 的两行密码管道，退出 0；该验证不覆盖 Docker Compose 的 stdin 行为，也不能据此排除云端创建管理员命令。
