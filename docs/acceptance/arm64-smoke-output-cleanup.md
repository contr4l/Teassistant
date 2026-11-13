# 容器冒烟产物的跨 UID 清理

2026-11-14，关联 DEV-101/105/502，续接 [网络修复](arm64-smoke-network-fix.md)。本批只处理临时测试产物的存放与诊断，不改变正式导出文件权限、业务接口、容器生产用户或主机代理。

## 观察与复现

`ee784793e65304c1b43325822781f7749b11b834` 的 [Actions 43494332402](https://github.com/contr4l/Teassistant/actions/runs/43494332402) ARM64 镜像构建成功后，容器冒烟失败；当时公共 check-run 只提供 exit 2，没有受控阶段 annotation。已请求用户提供末行异常类型；尚不能仅由此断言当前 run 的确切异常。

独立 GPT-6 Luna / high 核对了一条明确的源码风险链：`export_snapshot` 以 `mkdtemp` 创建默认 `0700` staging 目录，再将其原样 rename 为最终 Word 输出目录；CI 容器以 UID 10001 写入 host bind 的 `/smoke`。`compose down --volumes` 不删除 bind mount 的宿主机文件，外层 host `TemporaryDirectory` 仍会尝试递归删除这些产物。runner 与容器 UID 不同时，宿主机不能遍历私有输出目录；异常在阶段包装之外，会只打印普通异常类型。

root 在真实 Docker Linux 容器中创建纯合成目录，模拟 writer UID 10001、cleanup UID 1001、目录 `0700`：递归删除实际返回 `EACCES`。另用临时 Docker named volume，以 UID 10001 写入同样 `0700` 产物，不挂载 host temp：宿主机临时目录清理成功，Docker volume 删除退出 0。两个探针均使用缓存 `node:20-alpine`、`--network none`，没有真实材料、凭据或模型调用，容器及临时卷已清理。这证明跨 UID 风险与 named-volume 方案，不冒充业务镜像真实通过。

## 合同与文件归属

- 三项渲染样例改写入现有 `smoke_data` named volume 内的 `/app/work/container-smoke/`，不再 bind 宿主机产物目录。主机只接收已有封闭 JSON 结果，`down --volumes` 回收临时数据。保留 `_compose_text` 的 share 参数以兼容并行调用方，但不使用它建立挂载。
- 仍执行真实 Word、LaTeX、Chromium、API 与 Worker 检查；不放宽容器 UID、不修改生产目录权限、不跳过检查。
- 未包装的主机生命周期异常也输出仅包含固定阶段和异常类名的 Actions annotation，不回显任意异常内容。
- root 仅修复 `container_smoke.py` 相关最小片段；独立 GPT-6 Luna / high 归属新增 `test_smoke_output_cleanup.py`。另一会话正在修改同一脚本的 lean API/PNG/PDF 功能，本批保留它的工作区变动，候选与提交从已发布 HEAD 导出的隔离源码中取自有改动，不纳入这些并行内容。

## 验证

隔离候选的既有网络与 HTTP/Store/Worker 专项检查为 25 passed；独立新增产物/权限反例后共 27 passed，1 条既有 Starlette/httpx 弃用 warning，Ruff 通过。独立检查确认无 host bind、三种产物均留在 named volume、`down --volumes` 执行，以及主机清理异常的 annotation 只显示固定阶段和异常类名。

root 另增加“host 清理失败时不留下成功报告”的断言，实际先失败：报告在临时目录退出前已写入。随后将报告写入块移动到完整 `TemporaryDirectory` 生命周期结束之后；该反例转绿，独立检查者再次执行上述 27 项通过并确认代码顺序。没有修改生产导出的私密目录权限。

完整检查及后续真实云端结果完成后补充。尚未确认新镜像已发布或板子已升级。

最终隔离候选实际执行 `make check`：Ruff 通过，832 passed、1 skipped、1 条既有弃用 warning，49.95 秒。跳过项仍为未随源码复制的本地私有题库可选验收。日志位于被忽略的 `work/arm64-release-v1/cleanup-candidate-make-check-final.log`；本批的业务源文件补丁从候选生成，保留工作区正在进行的 lean API 修改。
