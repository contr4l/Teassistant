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

本批尚未提交或推送，未运行真实 GitHub Actions、Docker 原生双架构构建、GHCR 发布/下载或 ROCK 5B 健康检查。因此当前仅为离线配置及回归验证，不表示已有新的可拉取镜像。

首次发布需仓库允许相应 Actions 和 ARM64 runner，且包发布权限有效。GHCR 包可见性不自动更改；私有包下载需用户自行登录具备 `read:packages` 的凭据。正式使用以成功 workflow 的镜像引用为准，推荐固定 digest；跨标签推送不具备原子性。

容器冒烟使用合成材料及隔离网络，不调用真实 DeepSeek，也不证明学生卷面识别质量或教师价值。
