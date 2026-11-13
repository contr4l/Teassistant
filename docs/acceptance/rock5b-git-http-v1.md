# ROCK 5B 构建中 Git HTTP/2 错误处理（2026-11-12）

## 输入与范围

用户提供板上日志：`uv sync --locked --no-dev --group export --no-install-project`下载`mathtypejx`固定提交时，Git退出128，报`curl 16 Error in the HTTP2 framing layer`和`expected flush after ref listing`。本批针对DEV-101/105的镜像构建步骤，不调整依赖提交、锁文件或服务器系统网络。

## 修改与验证

- `Dockerfile`仅在Python依赖安装的RUN中设置`GIT_CONFIG_COUNT=1`、`GIT_CONFIG_KEY_0=http.version`、`GIT_CONFIG_VALUE_0=HTTP/1.1`，供uv启动的Git子进程使用；最终运行阶段不继承这组环境变量。此为协议兼容处理，不能由错误日志直接断言代理软件是唯一原因。
- 更新Docker部署说明，区分服务代理、客户端认证代理与RUN步骤构建代理；宿主机全局Git配置不能代替容器配置。
- 真实执行运行时Git配置读取，得到`HTTP/1.1`，未发远程Git请求。
- 根目录uv创建的`.venv`中执行`.venv/bin/ruff check .`通过；部署CLI、容器运行入口及部署验收三个测试文件共39项通过（2.23秒）；`git diff --check`通过。
- 初次`uv run`被本机共享缓存的文件系统权限阻止，未运行检查。改用同一根目录环境已安装的ruff/pytest完成上述检查，未新增或安装依赖。

## 未验证

尚未在本机重新构建完整镜像，也不能通过SSH执行板上命令。板上GitHub固定提交拉取、完整ARM64镜像构建、容器启动和业务端到端仍待用户执行后反馈。39项本地检查不能代替这些结果。
