# ROCK 5B 部署与预设账号

目标环境：用户提供的ARM64、8GB ROCK 5B，系统为Radxa/Armbian类Linux，具体版本待现场确认。模型调用DeepSeek API，板子承担网页、数据库、文件处理与Word/LaTeX导出，不运行本地LLM或依赖NPU。

## 当前是否能克隆后一键运行

当前版本包含网页、Docker部署和账号初始化入口。克隆仓库最新`main`，准备好Docker和私有配置后，可以用`deploy.py bootstrap`完成配置初始化、构建、启动、等待健康及预设账号导入。已有克隆应先同步最新`main`；私有账号文件、API key和题库仍需单独准备，不随Git分发。

用户于2026-11-14确认板上服务已运行；当前工作区的Actions流程新增原生linux/amd64与linux/arm64构建、冒烟及多架构GHCR发布配置。是否已有可拉取版本，以实际成功的Actions发布结果为准；本地配置和测试不能代替远端构建或板上升级验证。

日常升级优先使用[GitHub预构建镜像](deployment.md#使用-github-actions-镜像)：把同一次成功发布摘要中的`TEASSISTANT_IMAGE`（完整Worker）和`TEASSISTANT_API_IMAGE`（轻量API）一起写入原`.env.deploy`，然后执行`python3 scripts/deploy.py pull`和`python3 scripts/deploy.py up`。板子自动选取ARM64，不重新编译Node/Python或安装TeX/Chromium。需要在本地构建轻量API时还应设置`TEASSISTANT_API_TARGET=api`；未设置新键的旧配置仍使用完整镜像。下面的`--build`流程保留给首次本地构建和显式排障。

已有单镜像部署首次切换时，先同步本次 `compose.yaml` 和 `scripts/deploy.py`，再补齐原配置的新键。保留原 `.env.deploy`、数据库密码、项目名和数据卷；旧 Compose 文件不会使用新增的 API 镜像配置。

## 1 环境确认

板上运行：

```bash
uname -m
cat /etc/os-release
python3 --version
docker version
docker compose version
df -h
```

架构应为aarch64/arm64，使用64位系统；宿主机需Git、Python 3.10或更新、Docker Engine及Compose v2（建议2.32或更新）。Docker服务须运行，当前用户须可访问它。Docker安装按实际发行版选择[官方Debian步骤](https://docs.docker.com/engine/install/debian/)或[官方Ubuntu步骤](https://docs.docker.com/engine/install/ubuntu/)，不要因口头称呼Radxa/Armbian便假设具体发行版；当前官方Debian安装说明列12/13及arm64支持。

Python业务环境、前端、字体、LibreOffice与XeLaTeX在镜像内，宿主机不需要安装Node/pnpm、uv或导出工具。构建需要访问Docker Hub、GHCR、GitHub、Debian软件源及Python/Node包源；中文字体/TeX等使镜像较大，先确认存储空间。8GB是本次部署目标，不是已经测出的最低配置或并发容量。

## 2 源码与初始配置

克隆主分支并初始化私有配置：

```bash
git clone git@github.com:contr4l/Teassistant.git
cd Teassistant
python3 scripts/deploy.py init
```

init只创建缺失的私有`.env.deploy`与题库挂载目录，不覆盖已有配置。数据库密码自动生成，不能复制Mac上含/Users路径的部署配置直接当板上路径。需要SSH仓库访问权限时自行使用已授权Git配置，也可改为仓库HTTPS地址。

从同一局域网的电脑/手机访问，编辑`.env.deploy`：

```dotenv
TEASSISTANT_BIND=0.0.0.0
TEASSISTANT_PORT=8000
```

默认127.0.0.1仅板子本机可访问；若保持默认，可在电脑用`ssh -L 8000:127.0.0.1:8000 用户@板子IP`建立转发，再访问电脑的localhost:8000。外网与HTTPS另按[部署指南](deployment.md)，本批不修改板子的网络、代理或登录配置。

## 3 预设管理员和测试教师

本机已准备私有`.accounts.deploy.json`，包含两个管理员和一个测试教师，具体用户名及密码以私有配置为准。该文件不进入Git和Docker构建上下文，需单独安全复制到板子项目目录。它是初始化输入，不是登录时每次读取的配置。

如果自行创建，格式如下（密码占位须替换）：

```json
{
  "version": 1,
  "users": [
    {"username": "<第一个管理员用户名>", "password": "<管理员密码>", "display_name": "<管理员显示名>", "role": "admin"},
    {"username": "<第二个管理员用户名>", "password": "<第二管理员密码>", "display_name": "<管理员显示名>", "role": "admin"},
    {"username": "<测试教师用户名>", "password": "<测试教师密码>", "display_name": "<教师显示名>", "role": "teacher"}
  ]
}
```

```bash
chmod 600 .accounts.deploy.json
```

初始化仅创建缺失账号。同名同角色跳过，不覆盖密码、不重发初始额度；同名角色冲突拒绝，不自动升级权限。整批新增用户、钱包与初始发放在同一事务中完成，非法文件/重复用户名/角色冲突不留下半批账号。新账号按照当时管理员默认额度创建，初始默认10元；修改文件中的密码后重复部署不会重置已有密码。

## 4 一条命令启动与初始化账号

环境、私有配置及账号文件准备好后：

```bash
python3 scripts/deploy.py bootstrap --build --accounts-file .accounts.deploy.json
python3 scripts/deploy.py status
```

bootstrap先校验账号文件和权限，再创建缺失的`.env.deploy`、验证Compose、构建镜像、启动PostgreSQL/init/API/Worker并等待健康，最后通过容器stdin导入账号。密码不放在启动参数、环境变量或镜像中，日志不回显输入。已有部署配置保留，账号初始化失败会报失败，已启动的服务不会被伪称全部完成。

配置局域网监听后访问`http://板子IP:8000`。先用管理员登录检查模型和默认额度，再以测试教师检查教师流程。DeepSeek key可以先留空，启动后在“管理设置→模型与额度”填写；缺key时不能实际识别手写答卷。

## 5 题库与已有数据

Git不携带本机PostgreSQL/SQLite账号、学生资料、上传材料、题库或密钥。初始化账号不能迁移这些数据。

现有题库应把本机`runtime/bank-portable`的**整个包**复制到板上（source、semantic、evidence、manifest均保留），让板上`.env.deploy`的`TEASSISTANT_HOST_BANK_ROOT`指向包目录，并设置：

```dotenv
TEASSISTANT_BANK_DIR=/bank/source
TEASSISTANT_SEMANTIC_DIR=/bank/semantic
TEASSISTANT_CURRICULUM=/bank/semantic/curriculum.json
```

包目录与文件需允许容器UID10001读取；挂载仍为只读。不要只复制annotations，也不要把真实题库加进Git。目录及证据迁移规则见[部署指南](deployment.md)。缺题库时账号和学生页可用，不能抽题；有题库且缺模型key时仍可手动选考点组卷，但不能获得新手写识别结果。

需要迁移本机已有用户/学生/练习时，另行同步数据库与私有文件的同一时点，不能仅复制账号JSON；当前本机临时应用+PG容器与正式Compose使用不同数据卷。更新、停止、备份与回退沿用[部署指南](deployment.md)。
