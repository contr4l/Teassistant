# Docker 单服务器部署

本方案把前端、Python 运行环境、中文字体、LibreOffice 和 XeLaTeX 放入同一镜像。数据库、上传文件、生成文件和题库放在镜像外，更新镜像不删除它们。默认有 API、独立数据库任务 Worker、PostgreSQL 三个常驻服务；init 一次性初始化表与默认提示词。首次建表完成后才启动 API 和 Worker，管理员账号需显式创建。

## 首次启动

ROCK 5B的ARM64原生构建、账号预设及克隆边界见 [板上部署指南](rock5b-deployment.md)。已有私有账号JSON时，可在环境和配置准备后执行 `python3 scripts/deploy.py bootstrap --build --accounts-file .accounts.deploy.json`，完成启动及幂等账号初始化；它不会覆盖已有密码、角色或额度。

服务器需要 Docker Engine、Docker Compose v2（建议 2.32 及以上）和 Python 3.10 或更新版本。辅助 CLI 只使用标准库；业务 Python 3.13 环境已封装在镜像内，宿主机不需要 uv、Node、pnpm 或数学导出工具。克隆项目后，在项目根目录执行：

```bash
python3 scripts/deploy.py init
```

生成 `.env.deploy`，权限为 0600，自动设置随机数据库密码并创建只读题库挂载的宿主目录。已有文件不会被覆盖；`.env` 开发配置不受影响。配置文件、runtime/ 和原始资料均不提交。

编辑 `.env.deploy`：

| 配置 | 用途 |
| --- | --- |
| `TEASSISTANT_IMAGE` | 首次本地构建可用 `teassistant:local`；发布后改为 GHCR 版本标签或不可变 digest |
| `TEASSISTANT_DB_PASSWORD` | init 生成的 64 位十六进制随机值；修改为自定义值时限 20–128 位 URL 安全字符，避免 DSN 编码问题 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | 密钥可暂空，启动后在管理员网页保存；环境变量作为后备。本批网页模型限定 `deepseek-flash` 与官方地址，镜像构建不需要密钥 |
| `TEASSISTANT_BIND` / `TEASSISTANT_PORT` | 默认 `127.0.0.1:8000`；需要受控内网访问时可显式设置 `0.0.0.0` |
| `TEASSISTANT_SECURE_COOKIES` | 本机 HTTP 为 0，对外 HTTPS 反向代理配置后设为 1 |
| `TEASSISTANT_HOST_BANK_ROOT` | 宿主机题库根目录，挂载到容器 `/bank`，只读 |
| `TEASSISTANT_BANK_DIR` / `TEASSISTANT_SEMANTIC_DIR` | 都为空时先部署账号页面；题库就绪后分别设为 `/bank/source`、`/bank/semantic` |
| `TEASSISTANT_CURRICULUM` | 容器内目录文件，默认镜像的 93 点工程目录；定制时须与语义题库版本一致 |
| `COMPOSE_PROJECT_NAME` | 区分同机实例、网络与数据卷；部署后不要随意改名，否则会连接另一组新数据卷 |

密钥可以暂空以先验证登录和学生管理；真实分析会明确失败。无题库时不能组卷。容器固定 DeepSeek 并禁用 Codex CLI，不挂入开发机的 Codex 登录目录。

检查后构建并启动：

```bash
python3 scripts/deploy.py check
python3 scripts/deploy.py up --build
python3 scripts/deploy.py status
python3 scripts/deploy.py admin --username your-admin --display-name 管理员
```

最后一步在终端提示输入并确认管理员密码，无默认密码。成功后打开 `http://127.0.0.1:8000`，登录并创建老师账号。若通过 SSH 操作，可使用 SSH 隧道访问首次配置页，或先配置反向代理。

进入“管理设置 → 模型与额度”保存 DeepSeek 密钥，按需检查余额，并可为用户追加累计额度。新账号默认一次性获得10元，管理员可调整该默认值，只影响后续创建账号，不给已有账号补发。每次请求先按最大上界预占，返回有效用量后按固定单价估算结算；未知用量保留待核实。密钥存于共享私有数据卷的 `admin-model/deepseek.json`，更新镜像时保留，备份需保护其读权限。昨日费用并非官方账单，完整口径见 [管理员模型与额度](admin-model-billing.md)。升级本特性须同时更新并重启 API/Worker；仅更新前端不能启用计量。

镜像内应用运行身份为 UID/GID 10001。`/app/work` 为共享持久卷，init、API 和 Worker 的配置及文件路径一致。不要只运行 API 而省略 Worker：网页关闭后任务由独立进程继续执行；没有 Worker 的任务会保持排队。worker 健康检查仅检查进程身份及数据库连接，不能替代任务完成检查。

## 接入题库

管理员网页也可从“管理设置 → 题库”上传原始Word原卷与解析，自动生成持久版本并更新后续组卷；见[管理员题库指南](admin-question-bank.md)。生产镜像需要本批新增的Playwright和Chromium，旧镜像应重建并同时更新API/Worker。网页生成的题库保存在共享私有卷，由数据库版本指针选择，不能只备份或迁移种子挂载目录。

挂载的是已加工的来源库、对应语义目录及验真证据，不是未经处理的原题文件夹。不能只复制两个JSON目录：旧语义结果可能引用开发机绝对路径，缺失调用证据会使候选被阻断。先用打包命令生成新的自包含目录（输出必须不存在，不覆盖原始资料）：

```bash
uv run python scripts/package_question_bank.py \
  --bank-dir /path/to/source-bank \
  --semantic-dir /path/to/semantic-final \
  --output-dir /path/to/bank-package
```

打包仅重写部署副本的语义证据路径为包根相对路径，逐字节复制来源资料、目录及所引用的run/call证据，保留每次请求/提示词/结果摘要校验，生成manifest并检查可用题数与阻断原因保持一致；不会重跑模型或修改原件。将**整个包**复制到 `.env.deploy` 的 `TEASSISTANT_HOST_BANK_ROOT`（也可直接指向生成的包）：

```text
runtime/bank/
  source/
    bank.json
    sources/...
    media/...
  semantic/
    annotations.json
    curriculum.json
  evidence/
    .../run.json
    .../calls/.../request.json
    .../calls/.../prompt.txt
    .../calls/.../result.json
  manifest.json
```

保留来源库引用的完整相对文件结构。设置两条容器路径 `/bank/source` 和 `/bank/semantic`；不能填写宿主机 `/Users/...` 路径。文件及上层目录须允许容器 UID 10001 读取/进入，挂载仍保持只读。自定义考点目录可置于挂载内，并把 `TEASSISTANT_CURRICULUM` 设为 `/bank/semantic/curriculum.json`；版本必须匹配学生与题库。

```bash
python3 scripts/deploy.py check
python3 scripts/deploy.py up
```

check 验证配置、必需文件和 Compose 语法，不代替题库全部内容、可抽题数量或数学转换支持验证。业务使用时仍执行既有来源、考点、进度和导出门禁；不要用空文件绕过这些门禁。

## 使用 GitHub Actions 镜像

工作流在PR运行测试、前端检查，以及原生AMD64和ARM64镜像构建及容器合成冒烟。受信默认分支push、受保护版本标签及默认分支手动发布通过全部门禁后，才发布到`ghcr.io/contr4l/teassistant`。只有实际成功的Actions发布运行才证明镜像可拉取；本地配置与离线检查不作此声明。

`ubuntu-24.04`与`ubuntu-24.04-arm`分别构建各自架构，按架构复用BuildKit GHA缓存。API/Worker、数据库持久队列、无密钥失败、Word/LaTeX及Chromium合成页面渲染均在对应架构容器内检查；不靠x86构建成功推导ARM可用。发布任务再次验证实际镜像，临时标签含run_id/run_attempt/arch，正式SHA和latest/semver索引都在推送前校验两个Linux平台及完全相同的描述符digest映射，并在推送后复验。索引不包含额外attestation附件，减少旧Docker客户端的兼容障碍。

推送代码后先在仓库 Actions 查看结果，再从该次发布记录选择镜像标签或 digest。私有 GHCR 包需要服务器以具有 `read:packages` 的账户凭据登录；使用交互 `docker login ghcr.io`，不要把令牌写进本项目配置、shell 命令参数或仓库。

仓库需允许工作流请求`packages: write`，组织策略可能另外限制发布。版本标签须为`v主版本.次版本.修订版本`，并在Rulesets保护`v*`；未保护标签只检查，不发布。默认分支push/手动运行生成latest与完整提交SHA标签，每次成功发布摘要给出可复制的`TEASSISTANT_IMAGE=...:提交SHA`。PR及非默认分支手动运行不发布，PR构建任务无包写权限。镜像默认可见性不由本流程更改，首次GHCR包通常为private；公开包可匿名拉取，私有包由用户使用具有read:packages权限的PAT登录，不能用GitHub账户密码代替令牌。

已发布镜像的部署：

```bash
# 在 .env.deploy 中设置该次发布的 TEASSISTANT_IMAGE 标签或 digest
python3 scripts/deploy.py pull
python3 scripts/deploy.py up
```

更新采用同样两条命令，`up`明确带`--no-build`，镜像拉取失败不会悄悄在板上重新编译。保留原Compose项目名、端口、数据库密码、种子挂载及两个数据卷；API和Worker一起使用同一镜像。首次下载包含TeX/字体/Chromium的大镜像层，后续复用已有层，只拉取变化内容，耗时仍取决于网络和缓存。

ROCK 5B无需重新上传整套源码或重新初始化账号；原压缩包部署目录可以继续使用。当前Compose和deploy.py已支持此方式；未来若部署协议/Compose有变化，仍需同步小体积部署文件。升级前按下节备份，成功后刷新网页确认版本和业务。

建议固定不可变digest；完整提交SHA标签比latest易追踪，但同一提交重新构建仍可能覆盖其SHA标签（上游基础镜像可更新）。可在板子执行`docker image inspect 镜像引用 --format '{{json .RepoDigests}}'`取得digest，再把准确引用写入`.env.deploy`。GHCR不提供多个标签的跨标签事务，网络中断可能留下SHA已发布但latest未更新；只按最终成功运行的摘要部署，不把失败运行的中间推送当完整发布。

GitHub原生ARM64 runner是否可用、组织权限及私有仓库计算分钟以实际账户策略为准。ARM64容器冒烟成功也不代表具体ROCK内核/Docker版本、真实DeepSeek质量、手机访问或备份恢复已经验收。

## 构建期间的代理与 GitHub 下载

Docker服务、Compose/BuildKit客户端认证、Dockerfile中RUN步骤的联网是不同路径。仅在systemd设置Docker服务代理，不代表当前终端的认证请求或容器内Git、apt、pnpm均使用代理；当前终端的代理也不会自动成为所有RUN步骤的构建参数。应按实际失败阶段检查，使用标准代理构建参数配置RUN步骤，避免把代理地址或凭据通过ENV写入最终镜像。不要覆盖现有Docker客户端配置中的认证信息。

若安装`mathtypejx`时出现`curl 16 Error in the HTTP2 framing layer`，实际失败发生于容器内Git拉取锁定提交。Dockerfile在`uv sync`这一RUN步骤中通过`GIT_CONFIG_COUNT`设置`http.version=HTTP/1.1`；该配置传递给uv启动的Git子进程，不修改依赖提交、锁文件或宿主机全局Git配置。此项规避HTTP/2传输问题，不保证网络可达；重试构建仍需确认代理及GitHub连接。修改宿主机`git config --global`不能自动改变构建容器内的Git配置。

参考：[Git配置](https://git-scm.com/docs/git-config)、[Docker构建代理](https://docs.docker.com/engine/cli/proxy/)。ARM64完整构建与运行结果须以板上实际执行为准。

## 对外访问与 HTTPS

默认只监听服务器回环地址，PostgreSQL 不发布宿主端口。将 HTTPS 反向代理指向 `127.0.0.1:8000`，网页与 `/api` 共用域名；上传体积及超时时间按实际资料量配置。HTTPS 启用后将 `TEASSISTANT_SECURE_COOKIES=1` 并运行 up 重新创建服务。

反向代理应把 HTTP 请求重定向到 HTTPS，保留 Host，设置 `X-Forwarded-Proto`；不要把 `/app/work` 或 `/bank` 配成公开静态目录。手机上传依赖手机能访问相同域名，仍需在实际手机网络验证；本机浏览器可用不等于公网部署通过。

## 备份、恢复与回退

数据库记录和私有文件必须取同一静止时间点。先停止 API/Worker 写入，数据库继续运行。下列操作不会删除数据卷；备份目录也属于私有资料：

```bash
mkdir -p work/backups
chmod 700 work/backups
docker compose --env-file .env.deploy stop api worker
docker compose --env-file .env.deploy exec -T postgres pg_dump -U teassistant -d teassistant -Fc > work/backups/database.dump
docker compose --env-file .env.deploy run --rm --no-deps -T api /app/.venv/bin/python -c 'import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz"); t.add("/app/work",arcname="work"); t.close()' > work/backups/private-files.tar.gz
docker compose --env-file .env.deploy up -d --no-build api worker
```

另备份题库挂载根目录、`.env.deploy`、镜像 digest 和备份时间；这些与上传文件一样不能公开。确认备份命令退出成功并定期在隔离实例试恢复。当前使用表初始化 `create_all`，没有承诺自动数据库迁移或向旧代码降级兼容。

回退前先停写并备份，把镜像改回先前验证的 digest，执行 pull、up；若新版本更改了数据库结构，不能只换镜像，应在隔离环境先确认兼容，或恢复同一时间点的数据库和文件。恢复到现有库会覆盖数据，不提供默认自动执行的破坏性一键命令。

停止服务可用 `python3 scripts/deploy.py stop`。不要使用 `docker compose down -v`：该命令会删除数据库及私有数据卷。不同实例的配置、项目名、数据卷和端口应分开管理。

## 检查证据

每次交付分别记录离线配置/代码测试、本机容器集成、远程 Actions、注册表发布及实际服务器部署。合成容器冒烟不调用真实模型，不证明手写识别或老师价值。详见 [合同](acceptance/deployment-v1-contract.md)、[执行](acceptance/deployment-v1-execution.md)、[独立检查](acceptance/deployment-v1-review.md)。

本批当前环境的生产镜像构建停在公开基础镜像拉取：本机 Docker daemon 访问 Docker Hub/GHCR 超时，尚未运行最终生产容器。已有源码配置、构建上下文和宿主机合成测试证据不能代替容器实测；应在 Docker 出口可用的环境运行 CI 冒烟，再开放给老师。
