# 单题模型快速验证

业务模型使用 DeepSeek，开发快速验证可显式选择本机 Codex CLI。两者都经过相同的识别合同、考点白名单及来源元数据校验；不自动回落到另一通路。

## 范围与准备

本入口验证一张图片中的一个完整大题及小问，输出结构化题目 JSON。它尚不是完整试卷拆题、独立答题卡关联、错题分析到练习导出的 dev-e2e。

准备：

- PNG、JPEG 或 WebP 图片，大小不超过 10 MiB，包含完整题干及配图。
- 考点目录 JSON，符合 `Curriculum` 合同。程序自动读取可用 ID，无需手工给每道题打标签。
- 标准答案可包含在图中，或通过 `--prompt-file` 提供已有答案原文。此入口要求提取已有答案，不要求模型生成新答案；缺少可用答案将校验失败，不当成可用题入库。

`evals/fixtures/smoke-curriculum.json` 只有“分数运算”一个合成考点，仅可配合相应的简单试题做通路测试，不能当作完整湖南省考点目录。真实试题请使用真实目录，并将 `is_synthetic` 标为 `false`。

## 初始化

从项目根目录执行：

```bash
make setup
```

真实材料建议放在 `evals/private/`，输出默认位于 `work/`；两者均已排除出源码。

## Codex CLI 通路

需要本机安装 Codex CLI 并具备可用登录。适配器启动只读、临时会话，并通过 `--ignore-user-config` 不加载用户 `config.toml`，避免为识别卷面启用其中的 MCP 或 hooks；认证仍复用现有登录，不修改全局配置文件。自定义 provider 或代理配置不会自动继承。CLI 使用所选或默认模型服务，不代表本地推理，也不构成完整文件或网络隔离。

```bash
.venv/bin/python scripts/live_extract.py \
  --provider codex \
  --image /absolute/path/question.png \
  --curriculum evals/fixtures/smoke-curriculum.json \
  --output work/codex-first.json
```

可用 `--codex-executable /absolute/path/codex` 指定程序，用 `--codex-model MODEL_ID` 固定模型；也可设置 `CODEX_EXECUTABLE` 和 `CODEX_MODEL`。没有显式固定模型时，结果记录为 `cli-default` 与 `cli-default-unresolved`，不推断实际使用了哪一个模型。

适配器将提示通过 stdin 交给 CLI，以图片输入和 `--output-schema` 获取最终 JSON。CLI 退出成功后仍要校验结果文件、字段、考点及答案；命令退出 0 不等于题目识别正确。调用方法依据 [Codex 官方非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)。

## DeepSeek 通路

按 `.env.example` 在本机准备 `.env`，填写 `DEEPSEEK_API_KEY`。默认模型为 `deepseek-flash`，地址为 `https://api.deepseek.com`。代码不自动加载 `.env`；下面使用 uv 在进程启动时加载，且不输出密钥：

```bash
uv run --no-sync --env-file .env python scripts/live_extract.py \
  --provider deepseek \
  --image /absolute/path/question.png \
  --curriculum evals/fixtures/smoke-curriculum.json \
  --output work/deepseek-first.json
```

DeepSeek 使用图片输入、JSON Object 和非思考参数。返回空白 JSON、截断或不符合合同的结果时明确失败，不伪装为成功。图片和 JSON 能力分别参考 [Vision](https://api-docs.deepseek.com/guides/vision/) 与 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。

## 结果与核对

两个示例都可追加 `--prompt-file /absolute/path/answer.txt`、`--question-id Q001`、`--asset-id exam-page-1`、`--page 1`。输出路径已存在时拒绝覆盖，请为下一次试验选择新路径。

结果保存 provider、配置模型标识、尝试次数、可获得的用量、耗时、结构化题目与原始 JSON。未知用量保留为 null，原始材料或响应不打印到终端。模型返回 JSON 合法不等于内容正确，需对照原图检查题干、小问、公式、答案和考点。

项目自动化测试使用 MockTransport、模拟子进程或临时伪 CLI 程序。测试通过不证明本机登录、真实服务连通或识别效果；真实单题调用需在准备好材料后显式运行上述命令。

## 整批考点与难度试跑

来源 V3 已提供独立的批量语义入口，输入包含完整渲染公式、图形及可用解析，不伪造识别页码或直接写 ready。目录草案、显式模型配置、续跑与当前真实调用状态见[统一考点与难度评级](semantic-bank.md)。该批次的 Codex 通路要求显式模型 ID；已登录及模型缓存存在都不等于该账号能完成推理。
