# 真实题库组卷与导出独立验收

依据[组卷合同](bank-practice-v1-contract.md)，本批验证来源库到本地选题、当前进度门禁及四文件导出；独立设计/验收使用 `gpt-6.1-sol/high`，实施实际为 `gpt-6-luna/high`。不调用模型、不引入题库人工审核、不修改旧 `Question.ready`。真实题目配明确合成学生进度演示，不冒称真实学生备课、教师价值或完整 MVP 已完成。

## 固定输入

独立冻结来源 V3 全部 200 个 ID、语义及目录摘要，私有 `work/bank-practice-v1/review-cohort.json`。source bank SHA 为 `6422f96a33ae72d373070c6dc7b1bdee912e45a4bf82afea5bba05ee2f40a58f`，语义 annotations SHA 为 `b596673e735572cf2a6c013dd182f337b4554ec768158f64f8ab94650335ccd9`，93 点目录 SHA 为 `b158dcdf9c458754eb271b15a945f3e0d7281928e43ae5e7b53b23684c3967e5`。

独立预清点 176 候选、24 blocked：7 题关联未确认、20 题有模型 warnings，交集 3。该预清点不代替实际桥接的源 DOCX、媒体与版本核验，不允许强改状态凑足数量。保护清单已固定 16 原件、V1/V2/V3 来源库、语义最终结果与两轮 raw runs、输入图片和目录。

## 独立规则与输出要求

反例覆盖默认10及难度缺口、全局匹配、多标签单计数、跨 blocked 原题的身份连通排除、未知指纹、全部目标与显式/传递前置已学条件、当前进度撤回、不同学生、同版本目录内容改变、源/语义/资产变化和输出路径保护。导出会对原生 OLE 整字节及 preview 关系、OMML、表格和图片比例逐项核对，不能用整题截图代替。

PDF 使用只读逐页渲染检查，不以 XML 或实现者自述代替视觉。原生对象保留不等于已在 Word GUI 双击编辑测试；两种证据分开记录。

## 实际结果

已独立执行 `.venv/bin/pytest -q backend/tests/test_bank_practice_acceptance.py backend/tests/test_bank_practice.py backend/tests/test_bank_practice_cli.py`，30 passed，其中本文件归属的独立反例 19 项。自身文件 Ruff check 与 format 通过。发现并复验修复了嵌套布尔难度分数、重新签名的来源 payload 未绑定 raw bank、语义难度被改写但原响应不变、同一模型 call 缓存跳过后续行摘要验证等边界；保留持久反例，不以实际库未遭篡改代替门禁。

独立实际调用 `load_practice_bank` 与 `build_bank_practice`，全部 200 ID 与预先固定的集合相同，176 eligible / 24 blocked。合成已学全部 93 点时，默认 POWER 10 题与三目标进阶配额 10 题均无缺口；仅请求 FREQUENCY 的 advanced 10 题，实际 0 题、缺 10，没有降级替代。各选题 ID、指纹单计，目标均在题目标签内；`review-real-rule-results.json` 保留逐 ID 结果。这些结果证明程序遵守现有 AI 初评标签及目录，不能证明标签、难度或目录的教学准确率。

独立保护复核 `review_protection_check.py` 通过：16 原件、来源 V1/V2 各 4337 文件、V3 4338、语义最终 227、两轮 raw runs 261/259、输入 222、目录 4 文件，全部与本批前冻结 SHA 清单逐字节一致。

原始响应来源校验补丁收口后，另独立重跑真实 loader，仍为 176 eligible / 24 blocked，阻断原因仅 20 `semantic_warnings` 与 7 `source_association_unconfirmed`，没有新增资产或 provenance 失败。

## 最终四文件与实际 PDF

最终对象为私有 `work/bank-practice-v1/default-v3/exports` 与 `advanced-v3/exports`，每套包含同一 snapshot 的学生 DOCX/PDF、教师 DOCX/PDF。文件不写入源码。独立 `review_native_exports.py` 先将快照逐 ID payload 与实际 V3 `bank.json` 比对，再逐 occurrence 从原 DOCX ZIP 重读 OLE、预览与普通图片，核对导出包完整字节；并检查原生对象顺序、`w:r/w:object` 层次、唯一 ShapeID/ObjectID、预览关系闭包、学生答案标记排除、无占位 marker 及表格行列文本。四份全部通过，不只依赖 exporter manifest 自述。

| 最终样张 | 原生 OLE | 普通配图 | 原生 OMML | 表格 | 实际 PDF 页数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 默认学生卷 | 24 | 0 | 0 | 0 | 2 |
| 默认教师卷 | 58 | 0 | 0 | 0 | 5 |
| 进阶学生卷 | 75 | 6 | 0 | 1 | 3 |
| 进阶教师卷 | 187 | 9 | 0 | 1 | 10 |

表中普通配图不含每个 OLE 自带预览；逐次图像总数分别为 24、58、81、196，全部与原 DOCX 字节相符。进阶两份表格均为 3 行、每行 8 格，单元格文本顺序与源块一致。原生公式 fallback 均为 0；本次真实材料无 OMML，不能把该列 0 当成真实 OMML 成功证据。

使用 bundled Python 及 documents 技能 `26.1110.11258/skills/documents/render_docx.py` 对最终四份 DOCX 辅助重渲染成功；再直接用 bundled `pdf2image.convert_from_path(..., dpi=130)` 渲染实际交付 PDF，逐页亲眼查看全部 20 页。实际页面均为 A4（约 595.30 × 841.89 pt），中文、指数/根式/分式、统计表及圆几何图可读，无缺图、越界、遮挡或裁剪。进阶学生卷短题 5 的题干、图和选项完整位于第 2 页，长解析正常跨页。最终依据是实际交付 PDF，辅助重渲染的分页可能因渲染环境不同而变化。

可复现的独立命令：

```sh
.venv/bin/pytest -q backend/tests/test_bank_practice_acceptance.py backend/tests/test_bank_practice.py backend/tests/test_bank_practice_cli.py
.venv/bin/ruff check backend/tests/test_bank_practice_acceptance.py
.venv/bin/python work/bank-practice-v1/review_native_exports.py work/bank-practice-v1/default-v3/exports work/bank-practice-v1/default-v3/snapshot.json
.venv/bin/python work/bank-practice-v1/review_native_exports.py work/bank-practice-v1/advanced-v3/exports work/bank-practice-v1/advanced-v3/snapshot.json
.venv/bin/python work/bank-practice-v1/review_protection_check.py
```

实际 PDF PNG 与页数证据位于私有 `review-final-{default,advanced}-{student,teacher}` 及 `review-final-pdf-pages.json`。辅助 canonical renderer 使用 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，PATH 优先 bundled override/fallback；实际转换使用 bundled soffice，未调用桌面 LibreOffice。全量 `make check` 由集成负责人记录，本报告的 30 passed 是独立执行结果，不冒称独立重跑全部测试。

## 验收结论与边界

本批真实题库到本地合成进度选题、硬约束及四文件导出的开发验证通过，未见剩余阻塞。无真实学生信息，无新增模型调用，原始来源库、语义评测历史和原件保持不变；旧 `Question.ready` 不变。93 点目录与模型难度仍是工程目录及 AI 初评，教师校准、真实备课价值、近义改写题的语义去重、生产级服务与完整 MVP 未由本批证明。MathType 原生嵌入字节和关系已核验，但未在 Word GUI 双击编辑验证；跨 Word/字体环境分页一致性也未测。不新增人工题库审核流程。
