# 原生镜像冒烟的网络修复合同与证据

2026-11-14。关联 DEV-101/105/502；续接 [多架构发布证据](arm64-release-v1-execution.md)。业务模型、生产部署网络、题库及学生数据不变。

## 根因取证

`4e828efbf2fc2e811c0208465dbcf560fa9127c3` 的 [Actions 43491168662](https://github.com/contr4l/Teassistant/actions/runs/43491168662) 中，前后端与两种原生镜像构建通过，容器测试失败。公开 AMD64 annotation 给出 `stage=API port discovery; error=SmokeCommandError; exit=1`，指向 `docker compose port api 8000`。尚未到达 HTTP 登录或文档导出。

本机实际使用 Docker Engine 27.4.0、Compose 2.32.4，以已缓存的 `nginx:alpine` 创建纯合成临时项目：

```yaml
services:
  api:
    image: nginx:alpine
    ports:
      - "127.0.0.1::80"
networks:
  default:
    internal: true
```

`up -d --wait --wait-timeout 20` 退出 0；`port api 80` 退出 1，返回 `no port 80/tcp for container ...`；`ps --format json` 显示容器 running，`Publishers: []`。最后 `down --volumes --remove-orphans` 退出 0，无测试容器或网络保留。该复现没有挂载主机资料、修改主机代理、访问板子或调用模型。

另用同一缓存镜像重建 internal 网络项目，完全不声明 host ports，在容器内执行 `wget -q -O /dev/null http://127.0.0.1:80`：启动、HTTP 请求及清理均退出 0。该正向检查实际验证了容器内 loopback 的访问策略，仍不是业务镜像完整冒烟结果。

因此旧冒烟测试的 internal 网络与宿主机映射端口访问方式冲突；这不是由日志证明的 ARM 指令集兼容故障。此前宽泛诊断不足，已通过分阶段 annotation 定位；不将诊断补充当作业务故障修复。

## 修复合同与归属

- GPT-6 Luna / high 实施者归属 `scripts/container_smoke.py`：保留内部网络，删除无效端口映射与宿主机端口发现，HTTP 客户端在 API 容器内连接 `127.0.0.1:8000`；host 仍执行镜像私有内容检查、API/Worker 健康检查及真实管理员 CLI。客户端用 stdin JSON 接收合成凭据，保留真实登录、学生/考试/上传、持久队列失败、合成练习 Store 写入及反馈写读，不发真实模型请求。
- 客户端结果使用封闭 JSON；缺失、非法、错误或不完整成功结果必须失败。错误只能输出静态提示/白名单类名，不回显异常 payload、凭据、子进程原始输出或私有路径。清理不能覆盖主错误；失败不写成功报告。
- 同一实施者已仅调整 `Dockerfile` 的 `ARG VCS_REF` 与 OCI 标签位置，移到所有耗时指令之后，保留镜像 revision 元数据，避免新 SHA 让其后的依赖安装层失去缓存复用。依据 [Docker ARG 缓存规则](https://docs.docker.com/reference/dockerfile/#impact-on-build-caching)；本地未实测构建加速。
- root 归属现有 `test_container_smoke.py`、文档及集成验证；独立 GPT-6 Luna / high 归属新增 `test_smoke_network_acceptance.py`，覆盖失败、网络及凭据反例。仅调整开发冒烟内部合同，不改变业务 HTTP/Store 接口。

## 验证状态

网络冲突已用真实 Docker 最小项目复现；业务镜像的修复后原生容器闭环、GHCR 发布与 ROCK 5B 拉取仍待执行。离线检查及后续云端结果完成后补充，不以合成 HTTP 结果证明模型识别或教师价值。

本地专项检查：`test_container_smoke.py` 与独立 `test_smoke_network_acceptance.py` 合跑 25 passed（既有 Starlette/httpx 弃用 warning 1 条），Ruff 通过。新增客户端路径使用真实 FastAPI 路由、SQLite Store 和 `run_once` Worker，HTTP 传输由 TestClient 适配；合成材料上传、无密钥任务失败、独立 feedback 记录及读取全部覆盖，没有外部模型调用。该检查不冒充真实 Docker 中的 API/Postgres 端到端。

独立 GPT-6 Luna / high 检查了无宿主机端口/socket/外部网络、固定 loopback、凭据只走 stdin、保留真实健康检查与管理员 CLI、子客户端非零/非法 JSON/不完整成功结果/浮点计数/任意 hint 均失败关闭。root 另增强了 API 与 Worker 两项健康检查以及私有镜像内容检查仍被调用的断言。

工作区同时存在另一会话的镜像精简、CI 及体积报告改动，本批不纳入。Dockerfile 仅暂存自有 VCS 标签移动补丁；完整检查使用 `git checkout-index` 导出的精确候选源码并复用根 uv 环境，避免把并行变动纳入测试或提交。候选目录及日志均在被忽略的 `work/arm64-release-v1/`。

候选源码实际执行 `make check`：Ruff 通过，830 passed、1 skipped、1 条既有弃用 warning，53.31 秒。跳过项为依赖本地私有题库快照的可选验收，隔离源码没有复制这些私有文件；不将此结果称为真实题库或真实学生验证。日志为 `work/arm64-release-v1/network-candidate-make-check.log`。根工作区对应 25 项网络专项也已通过。
