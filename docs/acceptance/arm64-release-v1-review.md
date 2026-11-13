# ARM64 多架构发布独立复查

日期：2026-11-14

检查者：GPT-6 Luna / high（本会话实际模型）
依据：[发布合同](arm64-release-v1-contract.md)

## 结论

最终工作区中的 workflow 满足离线发布合同所列关键门禁：amd64 与 arm64 在对应原生 runner 构建并冒烟；PR 构建权限只读；只有默认分支 push、受保护的 `vX.Y.Z` tag 和默认分支 `workflow_dispatch` 可以进入发布；最终发布依赖全部 CI 与两项架构发布任务成功。两套最终索引在推送前分别验证，并比较平台摘要；远端推送后再次验证并比较。

本结论仅限代码与本地离线验证。真实 GitHub runner 构建、GHCR 上传/读取、ROCK 5B 拉取和健康检查均未执行。

## 独立检查

- `image` 使用 `ubuntu-24.04` / `ubuntu-24.04-arm` 构建 `linux/amd64` / `linux/arm64`，BuildKit `load: true`，按架构区分 GHA cache scope；用 Docker image inspect 检查已加载镜像架构，再运行合成容器 smoke。
- `publish-platform` 必须等待 backend、frontend 和两项 `image` matrix 成功；受信发布条件生效后才以同样的原生架构构建和 smoke，并登录 GHCR。暂存 tag 包含 run ID、run attempt 和架构。
- `publish` 依赖 backend、frontend、image 和 publish-platform。发布前针对完整 SHA 与 release tag 分别从同一对暂存架构镜像创建本地索引，运行 manifest 校验器，并比较规范化的架构/摘要结果；之后只推送这两份已验证索引。推送后分别再次检查远端索引并比较结果。
- workflow 权限默认和构建阶段为 `contents: read`；`packages: write` 只出现在受信发布任务。未发现 `pull_request_target`。checkout 关闭持久凭据。GHCR token 由 `GITHUB_TOKEN` 通过 `--password-stdin` 输入登录命令，没有输出 token。
- `scripts/container_smoke_fixture.py` 的 Chromium 样例只使用内嵌合成 HTML、数学标记和合成图片，并拦截外部请求；compose 冒烟配置使用空模型凭据、内部网络，不挂载真实题库或主机个人目录。没有真实模型调用。
- manifest 校验器要求 schema 2 的 Docker manifest list 或 OCI index，且恰好包含 amd64 和 arm64 各一项、合法且互异的 sha256 描述符；拒绝额外描述符、嵌套索引和不支持的平台变体。

## 实际执行

- `.venv/bin/pytest -q backend/tests/test_image_manifest.py backend/tests/test_container_smoke.py`：21 passed。
- Ruby Psych 解析 `.github/workflows/ci.yml`，并对提取的 18 段 `run:` 脚本执行 `bash -n`：全部通过。
- 另用 `git diff --check`：通过。
- 首次尝试 `uv run --locked pytest ...` 因本机 `/Users/ftan/.cache/uv/sdists-v8/.git` 权限限制退出；随后改用仓库 `.venv` 独立重跑并通过。系统 Python 的 pytest 运行过相同 16 个 manifest 测试，也通过，但出现项目 pytest 配置项未知警告；以根目录 `.venv` 结果为主要证据。

## 未验证与运行限制

- 未执行 Docker build、原生 runner、GHCR 发布/下载，因此不证明两个 runner 可用、镜像可推送/拉取、或 ARM 容器实际启动成功。
- 两个 GHCR tag 不能作为跨 tag 原子事务提交；网络中断可能造成一个 tag 已更新、另一个仍旧。部署时采用成功 workflow run 的输出，并固定镜像 digest 可避免继续引用易变 tag；需要回滚时使用之前记录的 digest。
- 无浏览器截图视觉验收、无实体 ROCK 5B 服务健康验证。
