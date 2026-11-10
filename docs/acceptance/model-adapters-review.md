# 模型适配器迁移独立验收

检查日期：2026-11-10。检查者：`gpt-6.1-sol` / `high`。本次将业务模型唯一供应商改为 DeepSeek，并增加显式选择的 Codex CLI 单题快速验证入口。不修改用户的 Codex CLI 全局供应商配置，也不将历史 P0 测试改记为新适配器测试。

**最终结论：本批离线迁移验收通过。独立全量运行 84 passed、0 skipped；静态检查通过。DeepSeek 真实请求为 0，Codex 真实推理任务为 0；实际服务、登录可用性及识别质量未验证。**

## 官方接口依据

| 项目 | 本批核对的官方约定 |
|---|---|
| 地址与模型 | `POST https://api.deepseek.com/chat/completions`；`deepseek-flash` |
| 非思考模式 | `thinking: {"type": "disabled"}` |
| JSON 输出 | `response_format: {"type": "json_object"}`，提示词也要求 JSON；全空格及截断仍须本地拒绝 |
| 完成状态 | `finish_reason=length` 表示可能截断；全空格可能仍为 `stop` |

以上约定在本次直接查阅 [DeepSeek 首次调用](https://api-docs.deepseek.com/) 与 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)；不是根据兼容接口名称推定。独立 JSON guide 抓取两次超时，上述输出边界由 Chat Completions API 页面核对。

`deepseek-flash` 的图片输入在 `user.content` 数组中使用 `image_url`，可传 base64 data URL。本批保留本地图片类型与大小限制，不代表覆盖官方支持的全部格式或尺寸。[官方 Vision 指南](https://api-docs.deepseek.com/guides/vision/)

## 检查范围

检查 DeepSeek 配置、端点限制、请求参数、模型及用量元数据、密钥不输出、固定来源身份与考点白名单、空白及截断响应拒绝、有界重试，以及现有合同/组卷/导出调用方兼容性。

业务模型请求只允许 `httpx.MockTransport` 合成响应；不读取真实凭据，不发付费模型请求。真实服务连通、卷面识别质量、实际耗时与费用均不在本次通过声明中。

Codex CLI 检查参数数组、stdin 提示、隔离临时目录、图片路径、schema 及最终输出文件、超时与错误处理；返回结果仍须经共享提取合同及白名单验证，不信任命令退出 0。本机仅执行 `codex --version` 与 `codex exec --help`，实际版本为 `codex-cli 0.151.0`，未发起真实模型任务。

官方文档确认 `--output-schema` / `--output-last-message` 可输出结构化结果，`--ephemeral` 避免保存 rollout，非交互模式支持只读 sandbox。本机 help 也列出相同选项；安全验收不依赖提示词自行约束。[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[CLI 参数参考](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

## 执行与结论

独立测试先完成旧 `BailianAdapter` / `BailianConfig` 引用到 `DeepSeekAdapter` / `DeepSeekConfig` 的名称迁移，并在原指纹合成响应中补齐官方必需的正常完成标志 `finish_reason="stop"`。

| 实际命令 | 结果 | 覆盖 |
|---|---|---|
| `.venv/bin/python -m pytest backend/tests/test_ai.py backend/tests/test_evaluation.py backend/tests/test_lead_acceptance.py -q` | 38 passed，退出 0 | DeepSeek 实现稳定后首次独立复跑 |
| `.venv/bin/python -m pytest backend/tests/test_lead_acceptance.py backend/tests/test_ai.py -q` | 41 passed，退出 0 | 新增独立迁移反例后复验 |
| `.venv/bin/python -m ruff check backend/tests/test_lead_acceptance.py` | All checks passed，退出 0 | 独立测试文件检查 |
| `.venv/bin/python -m pytest backend/tests/test_lead_acceptance.py -q` | 30 passed in 2.73s，退出 0 | 既有规则、7 项 DeepSeek 迁移反例及 CLI 进程组超时反例 |
| 带实际转换器的全量测试，完整命令见下 | **84 passed in 6.16s，退出 0，0 skipped** | 新适配器、统一单题入口、原组卷规则、实际数学转换集成 |
| `.venv/bin/python -m ruff check backend scripts` | **All checks passed，退出 0** | 最终源码与测试 |
| `.venv/bin/python scripts/live_extract.py --help` | 退出 0 | 可见 provider 为必选，图片/目录参数清晰；不触发调用 |

已独立运行的新增反例包括：空白与 null 在 `stop` 下不能产生题目；即使 JSON 内容合法，`length`、`aborted`、`insufficient_system_resource`、`content_filter` 均不能产生题目；注入默认跟随重定向的 HTTP 客户端时，302 仍只产生一次固定端点请求，不向第三方发送密钥，异常不含 provider 正文。

独立检查请求体实际包含 `deepseek-flash`、非思考模式和 JSON Object，未保留旧供应商的 `enable_thinking` 参数。题目来源身份由调用方绑定，返回 provider 为 DeepSeek，模型元数据明确标为配置值；没有将配置别名冒充服务端实测版本。

最终全量执行目录为 `/Users/ftan/personal/Teassistant`，命令为：

```sh
TEASSISTANT_SOFFICE=/Users/ftan/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice \
FONTCONFIG_FILE=/Users/ftan/personal/Teassistant/work/fonts.conf \
.venv/bin/python -m pytest -q
```

## CLI 与统一入口独立检查

已阅读最终 `backend/app/codex_cli.py`、`scripts/live_extract.py` 及相应测试，独立验证如下：

- CLI 使用参数数组与 `shell=False`，提示通过 stdin 传递；图片、schema 与结果位于独立临时目录。参数包含 `--ephemeral`、`--ignore-user-config` 和 `--sandbox read-only`，没有变更全局配置或绕过 sandbox。
- 代码审查发现最初 `subprocess.run(timeout=...)` 只能终止直接启动器，可能留下 CLI 子进程。实现者改为 POSIX 独立 session 与进程组终止。独立测试实际启动本地假 CLI 及其子进程，确认已走到子进程启动路径，并验证超时后子进程不能继续写标记文件。本项没有旧实现失败日志，结论来自代码审查与修复后的独立实际测试。
- 独立生成并递归检查严格 schema：共 7 个对象，均声明全部 properties 必填且禁止额外字段；没有残留 `$ref`、`$defs`、`prefixItems` 或 `default`；bbox 为恰好 4 个数字的数组。底层真实 Codex 服务接受该 schema 仍未实调；本地返回结果继续执行完整 Pydantic 校验。
- CLI 非零退出、缺失/非法结果、未知考点及不可用答案不会得到可用题目；不从 stdout 事件流推测题目。未显式指定模型时保留 `cli-default` / `cli-default-unresolved`，usage 为 null，不推断实际模型版本或费用。
- 统一入口要求显式 `--provider deepseek|codex`，不回退供应商、不自动读取 `.env`，先检查本地图片和目录再创建适配器；输出以排他创建方式拒绝覆盖。全量测试包含真正本地假 CLI 进程写入 JSON 的入口路径，未启动真实 Codex 推理。

最终对运行时代码、脚本、README、环境示例、当前技术选型与模型验证说明检索旧供应商名称，无匹配。旧环境变量拒绝测试及历史 P0 报告中的旧名称属于迁移保护/历史证据，未改写。

## 保留边界

本入口仅处理一张图中的一个完整大题、小问和已有答案，不是整卷拆题、答题卡关联或完整备课 dev-e2e。两个适配器共享来源身份绑定与考点白名单校验；JSON 合法、测试通过都不证明题干、公式、答案或标签正确。

Codex CLI 复用本机认证，但不加载用户 `config.toml`，自定义 provider/代理配置不会自动继承；CLI 使用模型服务，不代表本地推理。只读 sandbox、临时目录与不加载配置也不构成完整操作系统文件/网络隔离。进程组超时在本机 POSIX 环境独立验证，没有宣称 Windows 进程树行为经过验证。

下一步真实验证仍需用户准备获准使用的单题图片、已有答案、合适考点目录和相应账号条件，再显式选择通路执行。真实试卷、真实模型调用和老师价值验证门槛保持未通过，完整 P0 前提见既有开发计划；本报告不替代这些证据。

## 2026-11-11 提交前测试时序修复

提交前检查发现，独立进程组超时测试的 0.8 秒计时可能先于冷启动 Python 完成，导致 `started.exists()` 为 false。此前测试将启动速度混入了进程组终止判断；本次仅修复测试，不修改业务适配器。

测试通过 `monkeypatch` 的 `Popen` 子类，在 `communicate` 启动提取超时计时前，独立等待假 CLI 的真实子进程写出 ready 握手；启动等待单独设置 15 秒上限，失败时终止并回收受控进程组。握手后才释放子进程的提取步骤并启动原有 0.8 秒超时。假 CLI 还故意延迟启动 1.0 秒，确保启动耗时超过提取超时仍能正确验证。

原有启动、参数及子进程不得继续写标记文件的断言均保留，并新增子进程已就绪断言，没有通过删除测试、放宽提取超时或跳过真实进程组终止来消除失败。运行对象仍为本地假 CLI，没有真实模型调用。

本次实际执行（项目根目录）：

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest backend/tests/test_lead_acceptance.py::test_codex_timeout_stops_spawned_child_process -q` | **1 passed in 4.51s，退出 0** |
| `make check` | **Ruff 通过；83 passed、1 skipped in 6.08s，退出 0** |

该次普通 `make check` 未显式开启真实转换器集成，故保留 1 项预期跳过；不将本次结果重写为 84 项实际转换器全量通过。
