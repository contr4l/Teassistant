# 真实来源题库组卷与导出：执行记录

日期：2026-11-11。范围：FR-06、FR-11～15、DEV-204/401/402/404/405 的本地开发闭环；[冻结合同](bank-practice-v1-contract.md)、[运行说明](../bank-practice.md)。本批本地集成与独立验收通过，阶段边界见文末。

## 数据与协作

沿用16份DOCX、8套、200题来源V3，以及177题沿用原始初评、23题实际重评后合并的200题语义结果。未重做AI评分或生成题目；业务Provider仍只有DeepSeek，先前Codex CLI结果仅用于本地开发验证。

分派记录：设计/独立检查使用gpt-6.1-sol/high，实现A/B使用实际可用的gpt-6-luna/high，后者是相对于用户指定6.1 Luna的已披露替代。Root负责共享合同、通用分配内核、CLI、集成及文档；A负责来源桥接与候选/进度校验，B负责原生DOCX/PDF，Lead独立执行反例及真实产物检查。文件归属见合同。

本批开始冻结来源与最终语义目录4565个文件，私有清单位于 `work/bank-practice-v1/input-freeze.json`。来源bank.json字节SHA256为 `6422f96a33ae72d373070c6dc7b1bdee912e45a4bf82afea5bba05ee2f40a58f`；目录V2字节SHA256为 `b158dcdf9c458754eb271b15a945f3e0d7281928e43ae5e7b53b23684c3967e5`。

## 已执行的真实组卷

桥接保留全部200题，176题eligible、24题blocked。20题有语义warnings，7题关联未确认，交集3题，因此阻断数不能直接相加；源DOCX、已引用媒体和OLE检查未添加其他阻断项。eligible是本地开发候选资格，不是正式Question.ready，也没有新增人工题库审核。

真实试题配合显式合成学生及进度，生成以下结果：

| 用例 | 实际结果 |
|---|---|
| 默认“乘方与科学记数法”，省略count | 选10题，无缺口 |
| 进阶三个目标考点 | 选10题：基础6、提高2、挑战2；无缺口 |
| 频率考点只要挑战10题 | 选0题，明确缺10题；不以其他难度填补 |
| 仅已学幂及其前置闭包，共3点 | 选6题，明确缺4题；全部所选知识均已学 |
| 排除默认题单的第一道原题 | 改选其他题，共10题；同身份原题未回流 |
| 相同种子重复生成 | 完整题单一致 |
| 撤回已选题所需考点 | 当前进度检查拒绝 |
| 新进度revision但已学范围仍完整 | 允许，保留当前revision供导出记录 |
| 同版本目录修改内容 | 精确目录快照比较拒绝 |
| 修改快照题干但未正确绑定源库 | 拒绝 |

证据为私有 `inventory/`、`default-selection/`、`advanced-selection/`、`shortfall-selection/` 与 `rule-checks.json`。目录库存多标签重叠，不等于全局可同时选入数量。

## 本批发现与修复

- 配额内核复用旧P0与新来源合同，保留全局容量匹配及跨版本同题连通组排除；未知指纹不能把不同题连成同一组。
- 快照绑定完整源/语义内容、目录及资产，eligible嵌套语义使用严格SemanticAssessment校验，拒绝Python中True与1相等造成的分数绕过。
- CLI拒绝向来源/语义目录内或其祖先输出，导出前重读学生进度、来源、语义与目录，失败清理临时目录。
- 导出复制原MathType OLE及预览、OMML、可编辑文字和原生表格，重新分配关系/shape/ObjectID；禁止将w:object放在错误的段落层级。题号只移除与原题号明确匹配的前缀，保留小数。
- 中间真实试跑暴露原件以o:spt=75使用内建图片形状、未单独声明VML shapetype的情况；两个后续样张曾明确失败并清理输出，保留失败记录，不以缺公式的产物宣告成功。
- 独立反例发现重算hash不能替代来源真实性：导出逐题对照原bank.json，语义逐题绑定原始run、request、prompt、result和raw item；缓存仍须核对同call每个row的各自摘要声明。相关伪造、同call缓存及严格分数反例已修并复验。
- 实际页面检查发现短题图题拆页，修复后重导v3。题干首段、配图、选项保持短题关联；长解答和解析允许自然分页。最终交付目录只有四文件和manifest，不含LibreOffice临时profile。

## 最终导出与检查

`work/bank-practice-v1/default-v3/` 与 `advanced-v3/` 为最终候选，每套10题、四份实际文件。最终命令是：

```bash
FONTCONFIG_FILE="$PWD/work/fonts.conf" SAL_FONTPATH="$PWD/work/fonts" TMPDIR=/private/tmp \
  .venv/bin/python scripts/build_bank_practice.py \
  --bank evals/private/question-bank-v3 \
  --semantic evals/private/semantic-v2-unified-final \
  --progress work/bank-practice-v1/synthetic-progress.json \
  --request work/bank-practice-v1/advanced-request.json \
  --synthetic-student --output work/bank-practice-v1/advanced-v3 \
  --export --soffice /absolute/path/to/soffice
```

复现时换新的输出目录；本机字体及转换器路径不作为服务器已部署的证明。默认请求同理替换request路径。本机实际运行时长默认20.1秒、进阶23.8秒（含两次完整来源重验；并行运行），不作为生产性能指标。

| 题单 / 角色 | 实际PDF页数 | 原生OLE出现次数 | 普通配图 | 原生表格 |
|---|---:|---:|---:|---:|
| 默认 / 学生 | 2 | 24 | 0 | 0 |
| 默认 / 教师 | 5 | 58 | 0 | 0 |
| 进阶 / 学生 | 3 | 75 | 6 | 1 |
| 进阶 / 教师 | 10 | 187 | 9 | 1 |

均为A4，源MathType二进制与预览关系保留，图像回退0。本批真实所选题无OMML，原生OMML分支由明确合成测试覆盖；不能报为真实OMML题目效果。表格为3行×8格，单元格文字及顺序与源块一致。

独立检查从冻结V3题目和原DOCX ZIP重新读取OLE字节，核对全部344次导出OLE出现的顺序与完整二进制、w:r/w:object层级、预览关系、shape/ObjectID唯一性和题号；不以manifest自证。直接渲染最终交付PDF并逐页检查全部20页，中文、公式、几何和表格可读，未见缺图或裁剪，学生无答案内容。canonical render_docx另做辅助检查，分页可能随字体环境不同，最终以交付PDF为视觉依据。见 [独立报告](bank-practice-v1-review.md)。

最终回归命令为配置实际 `TEASSISTANT_SOFFICE`、上述字体环境后运行 `make check`：Ruff通过，**358 passed，0 skipped**，含旧P0的实际Pandoc/LibreOffice合成转换集成。模型HTTP测试仍为合成响应，不作为新真实AI调用。本批复核冻结4565文件全部未变，独立检查还覆盖旧来源版本、原始模型试跑及目录；未提交原卷、私有结果、凭据或学生信息。

## 结论边界

合成学生没有真实考试诊断；本批按显式请求输入目标考点。176候选不等于逐题验证了全部导出内容；本批导出覆盖以最终题单与manifest为准。MathType保留源对象，不等于已验证每台老师电脑均可编辑；真实样本没有OMML时，OMML分支仅能由明确合成机械测试验证。

FR-14本批只保存固定题单快照，尚无预览、替换、删除和排序界面。未完成账号与权限、手机上传页面、平台学生进度界面、考试识别/错题分析、生产任务恢复及部署，也未完成三个年级覆盖、目录教学进度核对、难度校准和老师价值验证。不能据此宣布整个P0、P2/P4或MVP通过。
