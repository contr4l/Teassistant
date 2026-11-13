# 合成 Chromium 检查的受限诊断

2026-11-14，关联 DEV-101/105/502，续接 [产物清理修复](arm64-smoke-output-cleanup.md)。

## 实际状态

`92a2be02130db4d92ebf1ecf80fffaa6926ebddf` 的 [Actions 43497291858](https://github.com/contr4l/Teassistant/actions/runs/43497291858) 前后端与两种原生镜像构建通过，但容器冒烟失败，正式发布被阻止。ARM64 公共 annotation 明确为 `stage=Chromium render; error=SmokeCommandError; exit=1`。该步骤执行前的实际 HTTP/PostgreSQL/Worker 闭环已经通过；尚不能据此判断 Chromium 的具体异常，也不证明整个容器或导出通过。

源码核对到锁定 Playwright 1.65.0、运行系统 `/usr/bin/chromium`、容器 UID 10001；APT Chromium 版本未记录。官方说明非 bundled executable 不保证兼容，另有 Docker IPC 建议，但当前没有浏览器异常证据，不能将失败归因为版本、sandbox 或内存。[Playwright API](https://playwright.dev/python/docs/api/class-browsertype)、[Docker 说明](https://playwright.dev/python/docs/docker)。

本机尝试拉取 `python:3.13-slim-bookworm` 以做最小浏览器复现，实际失败于 Docker daemon 的 Docker Hub DNS 查询；未修改本机 DNS、代理或板子设置。因此改用下一轮真实云端合成 fixture 获取可操作错误。

## 诊断边界

此前错误摘要不包含 Chromium 的底层异常。本批只对精确的 `/app/scripts/container_smoke_fixture.py chromium` 非零退出增加处理后的诊断，并且仅在 `GITHUB_ACTIONS=true` 生效。这是纯合成浏览器 fixture 的窄例外；不应用于普通 API、模型、真实资料或其他子进程输出，也不更改发布门禁。

先在完整捕获文本中替换已知环境敏感值、URL、敏感赋值、Authorization 与绝对路径，然后取尾部并去控制字符。编码 `%`、CR、LF 后再限制整个 annotation 为至多 5000 字符，防止换行注入和编码膨胀；底层命令仍按原退出码失败。该处理不宣称能辨识任意未知秘密，fixture 必须保持无真实凭据、真实资料及用户浏览器配置。

root 实施最小脚本片段；GPT-6 Luna / high 独立新增 `test_chromium_smoke_diagnostics.py`，覆盖环境敏感值跨原截断边界、DSN/database_url/token、路径/URL、编码后长度、换行注入、精确命令和 CI 门控以及失败退出保留。先前 DSN 未处理和编码前截断两项反例实际先失败，修复后 10 passed。与网络、清理和真实接口处理逻辑测试合跑共 37 passed，1 条既有 Starlette/httpx 弃用 warning，Ruff 通过。

候选源码从 `92a2be0` 隔离导出，排除另一会话正在进行的 lean API/镜像精简改动；仅将同一小补丁合入共享工作区以保留其修改。完整检查及真实云端诊断完成后补充，当前仍没有本轮可拉取的正式镜像。

隔离候选最终 `make check`：Ruff 通过，842 passed、1 skipped、1 条既有弃用 warning，55.84 秒；跳过项为不随源码提供的本地私有题库验收。日志保留在被忽略的 `work/arm64-release-v1/chromium-candidate-make-check.log`。这只证明日志边界和离线回归通过，浏览器错误仍待下一次真实运行确认。
