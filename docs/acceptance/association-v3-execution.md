# 来源关联 V3 执行记录

日期：2026-11-11。对应 FR-02、DEV-201/202 的来源关联增强；本批按用户要求验收固定 200 题至少 95%。合同、所有权与证据边界见 [V3 合同](association-v3-contract.md)，独立重算见 [独立验收](association-v3-review.md)。

## 最终结果

| 指标 | V2 | V3 |
|---|---:|---:|
| 固定预期大题 | 200 | 200 |
| 来源关联 confirmed | 47 | 193 |
| 来源关联 unverified | 153 | 7 |
| confirmed / 全部预期题 | 23.5% | **96.5%** |
| 结构失败 / 缺失 / 重复 / 额外题 | 0 | 0 |
| 有可用答案 | 200 | 200 |
| 真实难度评分 / 正式 ready | 0 / 0 | 0 / 0 |
| 真实模型调用 | 0 | 0 |

16 个原件、8 套各 25 题。固定 manifest 的 SHA-256 为 `652f17654d0da9748bfdc8562af2e73f0252d8c78673c7790c6d316b2b7eb33c`；没有减少分母、替换样本或将待核验状态直接改为通过。

1249 对题干 MathType 对象完整原生 payload 完全相同，新增原生身份通路确认了 146 道此前仅因预览像素差异未通过的题。121 对普通配图仍按原尺寸 RGBA 精确比较，其中 111 对相同、10 对不同，涉及 7 道题；这些题保留未确认，不代表已证明题目内容有错。全部 6386 个原生公式对象均能读取；没有执行嵌入对象。

批次状态仍为 `partial`：构建成功保存资料但仍有 7 题未确认，构建退出码 3。固定样本验收独立计算 193/200，完整性通过且超过 190 题，评测退出码 0。不能把达到本批来源关联目标写成“整个题库无异常”或“可自动组卷”。

## 实现与检查

- `native_equation.py`：只读校验 CFB、唯一 Equation Native 流、封装长度与版本头；对完整 MTEF payload 摘要，不删未知记录，不宣称完整语法或 AST 语义解析。新增 `olefile`，已同步 `pyproject.toml` 与 `uv.lock`，实际版本 0.47。
- `source_association.py`：有序比较文字、表格、数学及媒体。原生公式必须唯一绑定且预览存在可读；两侧原生不同不能回退为相同预览通过。预览像素与媒体身份使用独立字段，普通图片不放宽阈值。
- 导入器升级 `source-bank-v3`，保留原件、全部内容和逐媒体证据。旧 V1/V2 不覆盖，业务 `Question` 合同不变。
- `evaluate_associations.py`：检查固定预期 ID、文档与试卷集合唯一性、原件 SHA/XML/rels、内容快照，重新读取 OLE 原件并检查源图到输出图的 RGBA，一律重算关联状态；缺失、失败、重复或来源漂移不能靠高比例抵消。

独立检查期间修复了 MTEF 版本字段类型不一致、OLE 预览双向绑定不足、缺预览仍可走原生通路，以及像素字段混用媒体身份的问题。固定评测同时补充了重复试卷记录、非布尔答案标记与输出 PNG 被修改的反例。最终测试全部通过；这些合成反例用于验证程序边界，未混入真实 200 题的分子。

## 实际命令与产物

从项目根目录运行，日志在 Git 忽略的 `work/association-v3/`：

```sh
.venv/bin/python scripts/build_question_bank.py \
  --source assets --output evals/private/question-bank-v3
# exit 3：partial，193 confirmed + 7 unverified

.venv/bin/python scripts/evaluate_associations.py \
  --bank evals/private/question-bank-v3 \
  --manifest work/association-v3/review-manifest.json \
  --manifest-sha256 652f17654d0da9748bfdc8562af2e73f0252d8c78673c7790c6d316b2b7eb33c \
  --report evals/private/question-bank-v3/association-evaluation.json
# exit 0：193/200，missing/extra/duplicate/source_errors 均空

make check
# exit 0：Ruff 通过，235 passed / 1 skipped
```

唯一跳过项为未配置 `TEASSISTANT_SOFFICE` 的真实转换器集成；本批没有修改导出器，也没有用该跳过项声称新增 PDF 验收。其他测试覆盖数学文字、原生对象差异、预览差异、普通配图、表格、顺序、无效绑定与固定分母失真。

本机无头 Chromium 实测 `index.html`：200 张题卡，8 套各 25 题，5342 张图片全部加载且有尺寸，7 个表格，无页面错误、无手机横向溢出；筛选及空搜索提示通过。证据为 `work/association-v3/browser/browser-report.json` 与本地截图。

源码产物为 `evals/private/question-bank-v3/`，含 HTML、JSON、SQLite、目录 CSV、原件和完整核验报告。交付副本为本地输出目录中的 `长沙九年级数学试题库_V3/` 与同名 ZIP；打包逐条 CRC 校验。真实资料、核验清单和本地报告保持 Git 忽略，不提交源代码。

实施实际使用 `gpt-6-luna/high`，总设计与独立验收使用 `gpt-6.1-sol/high`；未把不可用的 `gpt-6.1-luna` 写成实际模型。业务模型调用为 0，来源关联达标不代表考点、难度、打印导出或老师价值已通过。
