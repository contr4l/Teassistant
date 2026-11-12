# ARM64部署准备与账号初始化执行记录

日期：2026-11-12。用户询问ROCK 5B克隆后一键部署及预设管理员/测试账号，确认ARM64/8GB，具体Radxa/Armbian系统版本未知。对应DEV-101/102/502，[合同](board-bootstrap-v1-contract.md)先行。Root负责部署编排和文档，实际gpt-6-luna/high负责纯标准库parser/CLI、Store原子导入，实际gpt-6.1-sol/high独立反例与验收。

## 实现

- 新`scripts/bootstrap_accounts.py`：顶层只依赖标准库，严格version1/users JSON、128KiB/1～100账号、字段类型及重复JSON键/规范化用户名重复校验。CLI仅从`--stdin`接收密码，动态导入业务环境后初始化数据库；输出只含用户名及created/skipped计数，错误不回显密码、原始JSON或DSN。
- 新`Store.bootstrap_users`：完整验证/同名角色冲突预检，整批新增用户、初始钱包和赠额同一事务；SQLite写锁及PostgreSQL锁/唯一约束。已有同角色用户跳过，不改密码、显示名、额度，不给旧无钱包用户补发。并发冲突回滚，不留下半批。
- 新`deploy.py bootstrap --build --accounts-file FILE`：最先校验私有regular文件和至少一个管理员，拒绝不私密权限、symlink、非regular、过大及非法JSON；缺配置时init，已有配置不覆盖；Compose检查、可选build、up/等待健康后以`exec -T api ... --stdin`导入账号。密码只走stdin，不放argv/env/镜像，子命令输出捕获而不泄漏。构建/启动失败不继续导入。
- 本机`.accounts.deploy.json`按用户既有授权准备2个管理员和1个测试教师，权限0600，Git/Docker context明确排除；没有将真实密码写进源码、文档示例或报告，也没有向正在运行的用户数据库重新导入/重置。
- 新 [ROCK 5B指南](../rock5b-deployment.md)，同步README/部署说明。板上采用原生arm64 build，未改现有amd64发布流水线；题库包、账号及密钥与Git分离，LAN绑定显式配置。

## 检查与证据

`make check`（ruff+全量pytest）：**773 passed，48.15s**，1条既有Starlette/httpx弃用warning。此前735项，本批新增38项；日志`work/board-bootstrap-v1/make-check-final.log`。Root定向现有部署29项通过，`ruff format --check scripts/deploy.py`及`git diff --check`通过。前端未改，不重跑浏览器/导出。

独立新增 **21项**实际运行通过（2.35s），含纯标准库隔离Python parser、严格非法输入/重复/密码空格、权限/symlink/无管理员在任何Docker及配置写入前拒绝、真实stdin编排不泄漏、startup失败不seed、旧密码/显示名/额度及无wallet保留、角色冲突整批拒、第二wallet已flush后抛异常全表回滚、真实双spawn进程冲突不留下半批。Docker编排测试显式mock，不冒称容器已启动。独立报告见 [review](board-bootstrap-v1-review.md)。

Root另真实执行：在已有本项目PG17.2容器中创建**一次性独立数据库**，新Python进程运行`bootstrap_accounts.py --stdin`。首次创建2admin/1teacher，再用改过密码的seed执行全部skip；逐个原密码仍能authenticate，每用户总额度10且不重复发放，角色正确，stdout/stderr无合成密码。测试数据库最后删除，不修改现有产品数据库、用户或服务。证据`work/board-bootstrap-v1/pg-bootstrap-result.json`；全部输入为显式合成账号，未调用模型。

Root使用`python -S`真实验证宿主标准库即可导入deploy/parser及解析私有账号文件，不需要在宿主安装uv/SQLAlchemy；不打印内容。用户指定的3账号预设文件被Git忽略且不进入Docker context，源码示例仅占位密码。

## 部署边界

只读`git ls-files`确认Dockerfile、compose.yaml、deploy及bootstrap等当前仍在本地未跟踪工作区；本批没有隐式提交/推送全部既有更改，不能声称现在git clone就能取得最新部署功能。当前CI/GHCR策略只面向amd64，ROCK 5B需要本机原生arm64构建；锁文件有aarch64相关依赖制品，仅是可行性证据，不是运行通过。

没有连接板子、安装Docker、改其网络或服务，没有重试此前失败的公开注册表下载，没有实建ARM64应用镜像或在ROCK 5B实机验收。具体发行版应现场用/etc/os-release确认；Docker安装按实际系统官方说明。当前交付为**经过本地软件/独立PG检查的部署入口与操作流程**，仍需源码同步和板上实际镜像、健康、导出及网络验证；不把它标为板上一键部署已经通过。
