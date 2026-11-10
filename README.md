# Teassistant

面向湖南省初中数学教培老师的智能备课平台。通过考试材料识别、错题考点分析及结构化题库，生成符合学生教学进度的个性化练习。

当前处于 P0 离线基础阶段，包含数据合同、组卷规则、单题模型适配与评测工具、数学导出原型。DeepSeek API 是唯一生产模型 Provider；Codex CLI 仅供开发者快速验证。真实卷面和模型质量尚未验证，不代表 MVP 已完成。

## 项目文档

- [产品需求 V0.1](PRD.md)：产品范围、业务规则和 MVP use case。
- [模块与架构设计](docs/architecture.md)：模块边界、数据结构和处理流程。
- [技术选型](docs/technology.md)：首期技术方案、替代方案与验证事项。
- [开发计划](docs/development-plan.md)：阶段任务、依赖和验收标准。
- [开发与验收范式](docs/engineering-workflow.md)：合同、分工、独立检查及交付证据。
- [单题模型快速验证](docs/model-validation.md)：DeepSeek API 和 Codex CLI 的显式验证方法与边界。
- [当前模型适配器验收](docs/acceptance/model-adapters-review.md)：DeepSeek 迁移、CLI 进程检查与本轮测试证据。
- [本批独立检查](docs/acceptance/p0-review.md)：Sol 独立运行发现的问题与复验记录。
- [首批执行报告](docs/acceptance/p0-execution.md)：实际环境、集成测试、评测分母与导出证据。

## 离线开发

需要 Python 3.12 或 3.13，以及 uv。当前环境实际使用 Python 3.13.7，依赖固定于 `uv.lock`。

```bash
make setup
make check
make schemas
make selection
make evaluate
```

`make selection` 使用合成题目生成 `work/selection-snapshot.json`；`make evaluate` 读取独立 manifest 和预置预测，不调用模型。评测中的真实样本数为零时，真实质量保持未测。

数学导出还需要 LibreOffice、中文字体和 Pandoc；`make setup` 的 export 依赖组提供 Pandoc 二进制。使用显式可执行路径运行：

```bash
.venv/bin/python scripts/export_demo.py --output work/export-demo \
  --pandoc /absolute/path/to/pandoc --soffice /absolute/path/to/soffice
```

输出目录必须不存在。该脚本只导出合成题样张；生产字体、转换器镜像和真实卷面导出仍须后续验证。

### 单题模型快速验证

生产业务模型只有 DeepSeek API；Codex CLI 仅供开发者快速验证，复用登录但不加载用户 `config.toml`，使用所选或默认模型服务，不是本地推理或生产 Provider。两种路径都要显式选择，不会自动互相回退。真实卷面和识别质量尚未验证。

配置项、命令和输入限制见[单题模型快速验证](docs/model-validation.md)。

## 实施顺序

先验证真实试卷识别、答题卡关联和公式导出，再搭建基础工程、打通题库与学生分析，最后完成组卷、导出及老师试用。具体任务见开发计划。
