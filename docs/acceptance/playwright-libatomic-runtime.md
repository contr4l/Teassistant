# Playwright 驱动缺少 libatomic 的修复

2026-11-14，关联 DEV-101/105/502。只补齐已确认缺失的系统运行库及提前执行检查，不更改业务依赖锁、浏览器供应商、sandbox/IPC、容器用户或发布门禁。

## 真实错误与直接修复

`437dc8980e3154719a1cfb0f0915ce6299e46e36` 的 [Actions 43500388432](https://github.com/contr4l/Teassistant/actions/runs/43500388432) 中，AMD64、ARM64 的专用诊断均给出 `error while loading shared libraries: libatomic.so.1: cannot open shared object file`，随后在 `with sync_playwright()` 进入阶段报告 `Connection closed while reading from the driver`。此时尚未调用浏览器 launch；不是由这些日志证明的 sandbox、IPC 或内存问题。

Debian Bookworm 的 `libatomic1` 确实在 [ARM64](https://packages.debian.org/bookworm/arm64/libatomic1/filelist) 和 [AMD64](https://packages.debian.org/bookworm/amd64/libatomic1/filelist) 提供对应的 `libatomic.so.1`。独立 GPT-6 Luna / high 还核对了 [Playwright v1.65.0 驱动路径](https://github.com/microsoft/playwright-python/blob/v1.65.0/playwright/_impl/_driver.py) 与 [官方发布记录](https://github.com/microsoft/playwright-python/releases)，其中 v1.65.1 的 Docker 修订也明确为 driver Node 添加该运行库。

因此仅在 Debian runtime 的 apt 清单加入 `libatomic1`。复制 venv 后立即定位已安装 Playwright 的 `driver/node` 并执行 `--version`，随后执行 `chromium --version`；缺失运行库或二进制无法执行会在镜像构建时直接失败。实际 Chromium 页面、数学/图像、Word/LaTeX/PDF 冒烟仍保留，版本检查不代替渲染检查。

根 uv 环境实际确认 `find_spec('playwright').origin` 对应父目录下的 `driver/node` 存在；这只验证安装布局，本机未执行 Linux 驱动的动态链接检查。后者由两个原生 runner 的 Docker RUN 实际执行。

## 提交与验证边界

工作区仍有另一会话的 Alpine API、镜像精简、配置与依赖调整，本批保留这些内容，只将同一系统库/预检查小补丁合入其 Debian runtime 部分。提交和完整验证使用从 `437dc89` 导出的隔离候选；没有将并行改动带入本批。

独立检查确认包文件、锁定的双架构 Playwright wheel 与驱动 Node 路径均对应此修复，未在本机构建或声称渲染已通过。完整检查与下一次真实云端结果待补充；正式镜像仍待成功发布。

最终隔离候选实际 `make check`：Ruff 通过，842 passed、1 skipped、1 条既有弃用 warning，81.97 秒；跳过项仍是未复制的本地私有题库可选验收。日志保留于被忽略的 `work/arm64-release-v1/libatomic-candidate-make-check.log`。这些离线结果不代替下一轮原生镜像中的 Node/Chromium 执行及完整容器冒烟。
