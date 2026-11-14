# Docker 镜像精简：合同与验证

本记录对应先完成的全功能镜像清理阶段。后续 API/Worker 分拆与 API 200 MB 门禁以 [轻量 API 合同及验证](lean-api-v1.md) 为准，两阶段改动随本次发布一起纳入。

## 范围与约束

对应 FR-02 题库加工、FR-15 导出和部署交付。保持单一全功能镜像、非 root 用户、AMD64/ARM64、原生 Word 数学结构、中文 XeLaTeX PDF 和 Chromium 渲染；不移除转换器、字体或业务依赖，不引入运行时下载。

本批仅清理可再生成的 Python 字节码、系统帮助资料和构建残留，排除后端测试代码，并增加 CI 镜像体积记录。清理与安装必须发生在同一层或复制之前；在后续层删除大文件不能缩小已有层。保留已有 VCS_REF 标签移动改动，避免每个提交重新构建大依赖层。

不根据构建日志的两个层推算最终镜像大小，也不把 GitHub Actions 构建缓存等同于部署镜像。正式收益需用相同架构、相同基础镜像版本的前后结果比较。

## 文件归属与验收

集成负责人修改 Dockerfile、CI、部署说明和本证据；独立检查使用实际可用的 `gpt-6-luna/high`，只读检查与执行反例。验收包括静态检查、现有容器测试和可用条件下的镜像实建与容器内 Word/LaTeX/Chromium 冒烟。无法实建时明确保留未验证状态，不声称镜像已可部署或节省了确定数量的 MB。

## 执行结果

- Dockerfile：依赖阶段关闭字节码预编译，并在复制前移除 `__pycache__`；运行阶段仅复制 `backend/app`，不携带测试树。APT 安装同层删除缓存、man/info，以及匹配 PDF/HTML/changelog/NEWS/README 的离线文档；保留 copyright/license/copying 文件。未修改依赖锁、字体、TeX 宏包或转换器。关闭预编译可能增加首次导入的 CPU 时间，尚未测量冷启动差异。
- 新增 `scripts/image_size_report.py`：只查询本地镜像平台和累计未压缩大小，不读取环境配置，也不启动镜像。CI 在对应架构容器冒烟成功后记录该指标和主要运行目录占用，后者用禁网、只读、默认非 root 容器采集。目录占用不是镜像层总量。
- 已执行 Ruff；报告器与容器/部署相关测试 **56 passed**。报告器实际查询本地既有上下文验证用小镜像，得到 `linux/arm64`、`2,879,957 bytes`；这仅证明指标采集路径可运行，绝不是生产镜像体积或瘦身收益。
- 独立检查：实际 `gpt-6-luna/high` 读取最终清理表达式，并在临时合成目录执行反例：普通 PDF/README 被删除，copyright/LICENSE/COPYING、Python 源码保留，`__pycache__` 被删除；核对生产路径不依赖 `backend/tests`。根据反馈将 Dockerfile 说明注释放到 RUN 上方，避免续行歧义。
- 本机尝试 `docker pull python:3.13-slim-bookworm`，Docker Hub 域名解析失败（`lookup registry-1.docker.io ... no such host`）。因此本轮未实建全功能镜像、未测量前后体积、未运行新镜像内 Word/LaTeX/Chromium 或 ARM64 集成，不作确定节省 MB/百分比或可部署声明；保留既有双架构 CI 冒烟门禁待实际运行。
- 原工作区 VCS_REF/LABEL 缓存调整与另一会话的容器冒烟修正均保留；未改宿主网络、代理、镜像仓库配置或删除现有镜像/数据卷，未提交推送。

全量 `make check` 已完成：Ruff 通过，**829 passed**，1 条既有 Starlette/httpx 弃用警告，用时 98.57 秒。运行使用本机已配置的 soffice 和字体环境，日志在忽略目录 `work/image-slim-check.log`。该结果覆盖宿主开发环境，不代替新镜像内的转换器验证。
