# 本地部署试跑合同

2026-11-12，用户要求本地尝试部署供检查。沿用DEV-101/105/106/502，不发布远程镜像，不变更原18072预览、现有项目数据库或主机网络/代理。独立实例监听127.0.0.1:18080，凭据仅私有文件，不在日志或源码输出。

优先执行现有Dockerfile/Compose。若公开注册表仍超时，明确保留完整镜像未构建状态，采用本机应用+独立PostgreSQL容器提供可访问实例。已有官方postgres:17.2 arm64镜像仅用于本地试跑；不改生产Compose的postgres:17-bookworm，不假称一致的生产镜像部署。该替代须明确报告。

实际预检发现既有semantic来源证据有绝对路径，直接copy source/semantic虽能读取200行却会阻断候选。增加可迁移打包：复制source原字节、curriculum原字节；只改部署副本annotations中的provenance.source_run_path为相对包根的evidence目录，逐字节复制run.json和引用calls中的request.json/prompt.txt/result.json，其他评估/标签/题目不变。生成manifest绑定原/新annotations摘要、映射与复制文件摘要；原件不改、不放松逐call或原生资产门禁。打包后按同一loader验证原/新可用数及原因一致才原子发布，目标不覆盖既有目录/不得位于输入内。

loader对相对source_run_path以semantic_root.parent为基准解析，拒绝空值、绝对逃逸、..、反斜杠和越界symlink；原有同根绝对路径兼容。读取请求/结果仍使用原哈希校验，不相信包清单自报。

分工：Root负责部署、私有配置、账号与实际浏览器/Worker/导出集成及记录。实际gpt-6-luna/high A负责bank_practice.py相对证据路径、新scripts/package_question_bank.py、对应打包反例测试。实际gpt-6.1-sol/high独立检查部署与打包关键反例。共享loader调用签名保持不变，CLI参数冻结`--bank-dir --semantic-dir --output-dir`。

验收分开记录：镜像构建、真实PostgreSQL/API/Worker、用户登录/默认10额度/管理员默认设置、题库200与eligible计数、真实独立Worker组卷与Word/LaTeX PDF、重启后数据保留。无密钥不运行真实LLM；不把账号可用或合成响应称为手写识别/教师价值通过。
