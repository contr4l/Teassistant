# GitHub 原生 ARM64 镜像发布合同

2026-11-14，用户希望GitHub构建、ROCK 5B下载运行，避免每次板上编译。范围为DEV-101/105/502部署发布，不改业务模型或数据，不自动更新板子、不改变仓库/镜像可见性。

## 分工

- 集成负责人：manifest校验程序、文档/部署操作、验收及最终集成。
- 集成负责人同时补齐容器冒烟的Chromium无网络合成渲染检查，验证管理员Word流程的生产渲染依赖；不把合成截图当作真实试卷识别或老师价值。
- GPT-6 Luna/high：`.github/workflows/ci.yml`。保持既有后端/前端门禁，新增原生AMD64/ARM64矩阵、BuildKit缓存、分架构合成冒烟与受信发布/索引合并。
- 独立检查者：发布条件、权限、架构及不拉私有资料/真实模型的反例。实际模型按会话记录，不将Luna写作6.1。

## 流程与冻结边界

1. `image`矩阵使用`ubuntu-24.04`构建linux/amd64、`ubuntu-24.04-arm`构建linux/arm64，在原生runner加载镜像、检查真实image.Architecture并执行既有`container_smoke.py`，不发真实模型请求。该阶段仅contents:read，PR无包发布权限。
2. 受信`publish-platform`矩阵依赖backend/frontend/image全部成功，保持原默认分支push、受保护semver标签或默认分支手动运行条件；再次构建及实际冒烟后才登录GHCR并推送该次运行唯一的暂存标签。唯一标识含run_id、run_attempt及arch，避免不同运行的暂存镜像互相覆盖。
3. 最终`publish`依赖两个原生发布任务成功，组合其暂存镜像为多架构索引。推送正式SHA、latest或受保护semver标签前，使用`scripts/verify_image_manifest.py --input 文件`验证本地manifest恰好包含linux/amd64和linux/arm64各一次，完整sha256描述符，无嵌套索引/attestation混入。发布完成后再次检查远端索引，输出可复制的`TEASSISTANT_IMAGE`及部署命令。
4. 镜像保持`ghcr.io/contr4l/teassistant`命名，默认GHCR权限不作更改；私有镜像由用户以read:packages凭据登录，密钥不写仓库。发布权限仅在受信发布任务设置packages:write，不使用pull_request_target。
5. 采用分架构GHA BuildKit cache，设置明确scope，避免ARM/AMD缓存混用；构建与发布关闭额外provenance/SBOM索引附件以简化旧Docker客户端兼容。新增Action固定已查询验证的commit SHA。

## 已核实的外部参数

GitHub官方文档列出公开及私有仓库可用`ubuntu-24.04-arm`原生ARM64 runner。2026-11-14通过`git ls-remote`核实：`docker/setup-buildx-action@v4`→`f87e5991a6d7451dcb8d9637bfbc97413f497069`，`docker/build-push-action@v7`→`c3c9e263c25d99ce0380d002d59b67737d91b0dc`。使用前确认其action定义支持配置字段。

## 部署与验收

板子保留当前Compose项目名、端口、私有配置、数据库/私有卷/题库挂载，设置已发布的明确SHA或digest镜像引用后，使用现有`deploy.py pull`及`deploy.py up`；up带--no-build，镜像缺失/拉取失败不自动在板上编译。首次切换仍需对应Compose/部署脚本，业务更新主要由镜像承载。更新前按既有指南备份，API与Worker一起升级。

离线检查包含workflow语法/受信条件/最小权限、所有run脚本bash -n、manifest正常及缺ARM/重复平台/不合法digest等反例、相关部署回归。实际GitHub runner构建、GHCR发布、板上拉取与服务健康需真实执行结果，不把配置完成写作已发布可拉取镜像。
