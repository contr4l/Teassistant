# Codex CLI 开发通路恢复记录

日期：2026-11-11。继续用户已经授权的考点统一与难度初评，不更换业务供应商，不修改全局账号、配置或企业策略。

## 实际差异与验证

用户回传的交互会话 `/status` 显示企业工作区、OpenAI provider、GPT-6.1 Sol 与本地后台服务器。这证明了会话配置，不能单独证明一次推理已成功。随后主会话检查本机可执行文件：

| 调用位置 | 实际版本 |
|---|---|
| `/opt/homebrew/bin/codex` | `0.151.0` |
| ChatGPT App 内置 CLI | `0.167.0-alpha.3.2` |

主会话使用 App 内置可执行文件、显式 `gpt-6.1-sol`、`--ignore-user-config`、`--ephemeral`、`--sandbox read-only` 和一句合成提示进行验证，进程退出 0、stdout 为 `OK`。记录在私有 `work/semantic-v1/bundled-cli-connectivity.json`。没有修改原先 shell PATH 或旧版 CLI，也没有关闭企业策略来运行。

这次观察确认了可行调用路径，不能继续把旧进程的 `model_not_found` 泛化为用户账号没有 Sol 权限；也不将一次换路径成功描述成对所有客户端差异的完整根因证明。最早失败 run 未记录二进制摘要，不能事后为其伪造运行身份。

## 新的运行身份边界

`annotate_question_bank.py` 继续要求显式模型，新增将解析后的 CLI 绝对路径与整个可执行文件 SHA-256 写入 `run.json`。续跑时连同源、输入、目录、提示、评分及模型配置精确比较；可执行文件被替换也会拒绝复用旧 run。客户端版本另保存在本轮私有 `client-runtime.json`，既有失败目录不改写。

指定已验证的本机 App 内置 CLI：

```sh
--provider codex \
--codex-model gpt-6.1-sol \
--codex-executable '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
```

该路径为本机已测配置，不是平台生产依赖或账号权限保证。其他机器须先验证自己的可执行文件与模型；CLI 仍为显式开发通路，业务 provider 仍只有 DeepSeek。

## 真实小样验证

在同一来源、输入、目录和评分版本下新建 `evals/private/semantic-v1-codex-bundled/`。预先固定的 12 题全部产生有效结构结果，3 批真实响应成功；其中基础 6、提高 1、挑战 5。全部来源分母仍为 200，其余 188 在该时点标未尝试。小样快照保存在 `work/semantic-v1/bundled-pilot-snapshot.json`，没有覆盖原失败 run。

独立检查者对事先观察过的 6 题核对图形、公式、具体考点与粗难度带，另重算了未确认关联的复杂几何题；未发现应停止扩批的明显内容错误。模型还指出一题原解析二周期条件中的严格不等式边界问题，与独立事前推导吻合。这些是研发层面内容抽查，不是教师金标准、标签准确率或难度校准。

据此恢复其余 188 题，批大小 4、并发 3，不重复成功的小样。该首轮同时暴露了旧 90 点目录对整数整除及真假约束题的覆盖缺口；后续目录兼容迁移按[独立合同](semantic-catalog-v2-contract.md)处理，不能靠强塞相近标签达到成功数。最终全批和迁移结果另见本批 live 执行及独立验收记录。
