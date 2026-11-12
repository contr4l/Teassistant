# ARM64 账号预设与部署入口独立验收

依据 `board-bootstrap-v1-contract.md`，独立设计/验收实际 `gpt-6.1-sol/high`，账号解析与整批初始化实施实际 `gpt-6-luna/high`，Root 负责部署编排。**本批离线账号初始化与编排验收通过，未发现剩余阻塞。**本轮未连接 ROCK 5B，不把离线软件结果称为板端实际部署通过。独立验收者没有操作 Docker/现有服务、导入账号到运行实例或调用模型。

## 独立实际执行

新增 `backend/tests/test_board_bootstrap_acceptance.py`，全部账号与密码是明确合成数据。实际运行 `.venv/bin/pytest backend/tests/test_board_bootstrap_acceptance.py -q`：**21 passed in 2.35s**，Ruff 检查与格式整理通过。实施并行落盘，首次可执行时即通过，不虚构先红后绿。

- 使用真正 Python 子进程 `-I -S` 导入 parser，证明宿主不依赖 uv、SQLAlchemy 或应用依赖；JSON 通过 stdin，用户名 strip/casefold、显示名 trim，密码首尾空格原样保持。
- bool version、额外字段、规范化后重复用户名、bool 密码、空显示名、非法角色、101 账号、超128KiB与坏JSON均拒绝，错误不回显合成密码。
- 同名同角色重复初始化跳过；原密码仍有效、原显示名与角色不改、原总额17.500000及完整钱包不变，不补发默认赠额。旧用户原来没有钱包时也不在 bootstrap 中补建或赠额，后续正常读取仍为0。
- 角色冲突拒绝整个批次，排在前面的新管理员也不创建；第二个用户的钱包已初始化并 flush 后人为抛异常，所有用户、钱包与审计表行数回到基线。
- 两个真实 spawn 解释器共享一个 SQLite 文件，对同名不同角色竞争；只有一个完整批次成功，另一个冲突，无落败批次的半个用户或钱包，每个成功新账号仅获一次10额度。
- 部署编排的所有 Docker subprocess 被替换，不实际构建/启动。确认原JSON字节只出现在 `exec -T api ... bootstrap_accounts.py --stdin` 的 input，未进入任何 argv/env/日志、不使用 shell；已有.env再次执行不覆盖。
- 0644、symlink、没有管理员及无效JSON在创建.env或调用任何Docker命令前拒绝；启动阶段失败不会继续初始化账号，也不转述带秘密的 subprocess 输出。

## 只读实现与发布边界

parser 顶层仅标准库，CLI 在完成有界解析后才导入 Store；Store.bootstrap_users 完整预检、单一业务事务创建用户与初始钱包，冲突回滚且不调用逐个独立提交的 create_user。输出只含 created/skipped 用户名与计数。Root 文件预检使用 O_NOFOLLOW/O_NONBLOCK 与 fstat regular/private 权限，避免链接或非普通文件阻塞；私有模式可以比600更严格，不向组/其他用户开放。

Git/Docker ignore 已有账号文件排除。独立 `git ls-files Dockerfile compose.yaml scripts/deploy.py scripts/bootstrap_accounts.py` 无输出，四个部署文件当前均未 tracked；所以本地实现不能写成用户现在从远端 clone 就已获得。没有擅自提交/推送，也没有读取用户实际私有三账号文件或把其值写入源码、测试或报告。

本批不变更既有amd64发布策略。板子发行版、Docker/Compose前置与ARM64真实构建仍须现场确认；原生 build 方案不等于已实建镜像。21项证明离线软件与事务边界，不能证明 ROCK 5B/8GB 的完整部署、资源使用或性能。

## 最终集成证据

独立读取 `work/board-bootstrap-v1/pg-bootstrap-result.json`，仅输出安全统计而不展示账号或密码：Root 在隔离的 PostgreSQL17.2 数据库实际启动子 Python CLI，stdin 首次创建3个账号，修改 seed 密码后第二次3个全部 skipped，原密码保留、每个初始10、不重复赠额，运行用户数据库未触动、供应商调用0。该数据库由 Root 测试后删除；本验收者只读结果，没有执行其生命周期。

Root 还使用宿主 Python -S 对真实私有预设配置作纯标准库解析，只输出验证通过；本验收者没有读取该私有文件。源码文档示例仅可占位，具体私有账号不应随样例进入源码。

最后文档审查发现指南初版正文/示例使用了用户指定用户名，与本合同“模板只占位”不一致；Root 已将用户名、密码和显示名全部改为占位值，正文仅描述两个管理员与一教师。独立解析最终指南 JSON 示例确认三类字段均为占位，不记录实际值。此项是文档口径修正，不冒称又发生了代码或板端测试。

直接核读 `work/board-bootstrap-v1/make-check-final.log`：Root 全量 **773 passed、1 条既有 warning、48.15s**；其中包含独立21项。已有 warning 为 Starlette/httpx 弃用提示。Root 的 PostgreSQL/全量执行与本验收者实际运行的21项分别归属，不声称独立重复跑过全部773项。
