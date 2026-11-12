# ARM64部署与预设账号初始化合同

2026-11-12，用户询问ROCK 5B克隆后一键部署及如何预设管理员/测试账号，补充硬件ARM64/8GB、Radxa/Armbian类系统，具体发行版未确认。对应DEV-101/102/502，不远程登录板子，不把未实建的ARM64镜像称为已验证，不隐式提交/推送全部既有工作区。

新增私有`.accounts.deploy.json`（0600，Git和Docker context排除），格式`{"version":1,"users":[{"username":"...","password":"...","display_name":"...","role":"admin|teacher"}]}`。用户指定的三账号仅写本机私有配置，不写源码、示例、日志或argv。模板只用占位值。

纯标准库`parse_accounts(raw:bytes)->list[dict[str,str]]`位于scripts/bootstrap_accounts.py，错误为AccountSeedError固定安全文本；限制128KiB、1～100账号，严格字段与类型、用户名规范化后重复拒绝、密码1～1024字符、显示名非空且最多200字符。禁止额外字段，不能在错误里回显输入。

CLI `python scripts/bootstrap_accounts.py --stdin`从标准输入读取JSON，初始化Store并调用新`Store.bootstrap_users(accounts)`。后者完整验证且单事务创建全部新用户/初始钱包及发放记录，预检已存在角色冲突后拒绝；同名同角色跳过，不重置密码/角色或补发额度。并发冲突回滚并返回安全冲突，不制造半批账号。输出仅created/skipped用户名及计数，不输出密码。

Root扩展`python3 scripts/deploy.py bootstrap --build --accounts-file .accounts.deploy.json`：先验证私有账号文件及至少一管理员，再初始化缺失.env.deploy、检查配置、可选构建、启动并等待服务健康，最后`docker compose exec -T api ... bootstrap_accounts.py --stdin`以stdin输入原JSON（不进入argv/env/镜像）。配置已存在不覆盖；重复部署不重置用户。保留现有init/up/admin命令。

部署采用板子原生`docker build`，不使用现有amd64 GHCR作为ARM64保证；不在本批改镜像发布策略。文档说明Docker/Compose/Python/Git前置、确认实际OS、题库自包含包与密钥分开配置、局域网bind、源码同步与未实建状态。不能把私有题库或本机用户数据库塞进Git。

分工：Root负责合同、deploy.py编排、私有预设文件、隔离集成/文档；实际gpt-6-luna/high A负责bootstrap_accounts.py（纯parser+CLI）、Store.bootstrap_users及新对应测试；实际gpt-6.1-sol/high独立验收负责新test_board_bootstrap_acceptance.py及review，Root通知调用合同。既有运行实例不导入/重置账号、不重启。
